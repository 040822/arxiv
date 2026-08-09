"""Manual imports for papers outside the scheduled arXiv feed."""

import json
import io
import logging
import os
import sqlite3
import tempfile
import uuid

from flask import Blueprint, jsonify, request

from source.analysis import analyze_paper_basic, analyze_paper_full
from source.documents import get_paper_pdf_path, remove_paper_pdf_files, store_uploaded_pdf, validate_pdf_file
from source.imports import PaperImportError, preview_import
from source.storage import (
    get_analysis_by_paper_id, get_paper_by_key,
    insert_analysis, insert_paper, update_analysis, update_paper_document,
)
from source.storage.row_mapping import parse_paper_row
from .progress import update_progress


bp = Blueprint("paper_import_api", __name__)
logger = logging.getLogger(__name__)
SOURCE_TYPES = {"arxiv", "openreview", "doi", "web", "upload"}


def _bool_value(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _metadata_from_request():
    if request.is_json:
        data = request.get_json(silent=True) or {}
        return data.get("metadata") if isinstance(data.get("metadata"), dict) else data
    raw = request.form.get("metadata", "")
    if raw:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PaperImportError("metadata 必须是合法 JSON") from exc
        if not isinstance(value, dict):
            raise PaperImportError("metadata 必须是 JSON 对象")
        return value
    return dict(request.form)


def _authors(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return _authors(parsed)
        except json.JSONDecodeError:
            pass
        return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
    return []


def _temporary_upload(upload):
    suffix = os.path.splitext(upload.filename or "paper.pdf")[1] or ".pdf"
    handle = tempfile.NamedTemporaryFile(prefix="paper-import-", suffix=suffix, delete=False)
    path = handle.name
    try:
        upload.save(handle)
    finally:
        handle.close()
    return path


@bp.route("/api/paper/import/preview", methods=["POST"])
def api_preview_paper_import():
    source_url = str(request.form.get("source_url") or "").strip()
    if request.is_json:
        source_url = str((request.get_json(silent=True) or {}).get("source_url") or "").strip()
    upload = request.files.get("pdf_file")
    if not source_url and not upload:
        return jsonify({"status": "error", "message": "请输入论文链接或选择 PDF"}), 400
    temp_path = None
    try:
        if upload:
            temp_path = _temporary_upload(upload)
            validate_pdf_file(temp_path)
        draft = preview_import(source_url=source_url, pdf_path=temp_path)
        return jsonify({"status": "ok", "draft": draft})
    except (PaperImportError, ValueError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": f"解析论文失败: {exc}"}), 502
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def _save_basic_analysis(paper, result):
    if insert_analysis(paper["id"], result):
        return
    update_analysis(paper["id"], {
        "rating": result.get("rating", 0), "tags": result.get("tags", []),
        "summary_cn": result.get("summary_cn", ""),
        "summary_en": result.get("summary_en", ""),
        "value_comment": result.get("value_comment", ""),
    })


def _cleanup_uncommitted_pdf(paper_key, document_info):
    if not document_info:
        return
    try:
        remove_paper_pdf_files({
            "paper_key": paper_key,
            "pdf_local_path": document_info.get("local_path"),
        })
    except Exception:
        logger.exception("Failed to clean uncommitted PDF for %s", paper_key)


def _restore_attached_pdf(paper, old_payload):
    try:
        if old_payload is not None:
            store_uploaded_pdf(io.BytesIO(old_payload), paper["paper_key"], "paper.pdf")
        else:
            remove_paper_pdf_files({
                "paper_key": paper["paper_key"],
                "pdf_local_path": f"paper_files/{paper['paper_key']}.pdf",
            })
    except Exception:
        logger.exception("Failed to restore PDF for %s", paper["paper_key"])


@bp.route("/api/paper/import", methods=["POST"])
def api_confirm_paper_import():
    document_info = None
    paper_id = None
    paper_key = ""
    try:
        metadata = _metadata_from_request()
        title = str(metadata.get("title") or "").strip()
        if not title:
            raise PaperImportError("论文标题不能为空")
        source_type = str(metadata.get("source_type") or "upload").strip().lower()
        if source_type not in SOURCE_TYPES:
            raise PaperImportError("不支持的论文来源类型")
        arxiv_id = str(metadata.get("arxiv_id") or "").strip() or None
        paper_key = f"p_{uuid.uuid4().hex}"
        if get_paper_by_key(arxiv_id or paper_key):
            return jsonify({
                "status": "error", "message": "论文已存在，请在现有论文中更新 PDF",
            }), 409
        upload = request.files.get("pdf_file")
        source_url = str(metadata.get("source_url") or metadata.get("url") or "").strip()
        source_id = str(metadata.get("source_id") or arxiv_id or "").strip()
        if not source_id and source_type == "web":
            source_id = source_url
        document_info = store_uploaded_pdf(upload, paper_key, upload.filename) if upload else None
        paper_data = {
            "paper_key": paper_key, "arxiv_id": arxiv_id,
            "source_type": source_type,
            "source_id": source_id or None,
            "ingest_mode": "manual", "title": title,
            "authors": _authors(metadata.get("authors")),
            "abstract": str(metadata.get("abstract") or "").strip(),
            "categories": metadata.get("categories") if isinstance(metadata.get("categories"), list) else [],
            "primary_category": metadata.get("primary_category") or None,
            "url": source_url,
            "pdf_url": str(metadata.get("pdf_url") or "").strip(),
            "venue": str(metadata.get("venue") or "").strip(),
            "published_date": str(metadata.get("published_date") or "").strip(),
            "updated_date": str(metadata.get("updated_date") or "").strip(),
            "pdf_local_path": document_info.get("local_path") if document_info else None,
            "pdf_sha256": document_info.get("sha256") if document_info else None,
            "pdf_size_bytes": document_info.get("size_bytes") if document_info else None,
        }
        paper_id = insert_paper(paper_data)
        if not paper_id:
            if document_info:
                remove_paper_pdf_files(paper_data)
            return jsonify({"status": "error", "message": "论文已存在（来源标识或 PDF 重复）"}), 409
        paper_data["id"] = paper_id
        paper = parse_paper_row(paper_data)
        task_id = request.form.get("task_id", f"import-{paper_key}")
        run_basic = _bool_value(request.form.get("run_basic"), True)
        run_deep = _bool_value(request.form.get("run_deep"), True)
        warnings = []

        if run_basic:
            update_progress(task_id, {"current": 1, "total": 3, "status": "running", "message": "正在进行基础分析..."})
            _, basic, error = analyze_paper_basic(paper)
            if basic:
                _save_basic_analysis(paper, basic)
            else:
                warnings.append(f"基础分析失败：{error}")
        if run_deep:
            update_progress(task_id, {"current": 2, "total": 3, "status": "running", "message": "正在进行深度阅读..."})
            _, deep, error = analyze_paper_full(paper)
            if deep and deep.get("complete", True):
                qa_data = {"qa_analysis": deep.get("qa_analysis", "")}
                if get_analysis_by_paper_id(paper_id):
                    update_analysis(paper_id, qa_data)
                else:
                    insert_analysis(paper_id, {
                        "tags": [],
                        "summary_cn": "",
                        "summary_en": "",
                        "value_comment": "",
                        **qa_data,
                    })
            elif deep:
                warnings.append("深度阅读输出不完整，未保存")
            else:
                warnings.append(f"深度阅读失败：{error}")
        update_progress(task_id, {"current": 3, "total": 3, "status": "completed", "message": "论文导入完成"})
        return jsonify({
            "status": "ok", "message": "论文已导入", "paper_key": paper_key,
            "detail_url": f"/paper/{paper_key}", "warnings": warnings,
        })
    except (PaperImportError, ValueError) as exc:
        if document_info and not paper_id:
            _cleanup_uncommitted_pdf(paper_key, document_info)
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        if document_info and not paper_id:
            _cleanup_uncommitted_pdf(paper_key, document_info)
        return jsonify({"status": "error", "message": f"导入论文失败: {exc}"}), 500


@bp.route("/api/paper/<paper_key>/pdf", methods=["POST"])
def api_attach_paper_pdf(paper_key):
    paper = get_paper_by_key(paper_key)
    if not paper:
        return jsonify({"status": "error", "message": "论文不存在"}), 404
    upload = request.files.get("pdf_file")
    if not upload:
        return jsonify({"status": "error", "message": "请选择 PDF 文件"}), 400
    old_payload = None
    if paper.get("pdf_local_path"):
        old_path = get_paper_pdf_path(paper, download=False)
        if old_path:
            with open(old_path, "rb") as handle:
                old_payload = handle.read()
    try:
        info = store_uploaded_pdf(upload, paper["paper_key"], upload.filename)
        if not update_paper_document(paper["paper_key"], info):
            raise ValueError("论文不存在，PDF 未保存")
        return jsonify({"status": "ok", "message": "PDF 已保存", "has_local_pdf": True})
    except (ValueError, sqlite3.Error) as exc:
        _restore_attached_pdf(paper, old_payload)
        return jsonify({"status": "error", "message": str(exc)}), 400
