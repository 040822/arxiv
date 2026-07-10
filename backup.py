"""
WebDAV 云同步备份。

备份流程：
1. 使用 SQLite online backup API 生成一致性 papers.db 快照
2. 将数据库快照、settings.json 和 manifest.json 打包成 zip
3. 上传 latest 和日期历史文件到 WebDAV
4. 根据 history_days 清理过期历史备份
"""

import json
import os
import posixpath
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET

import requests

from config import DB_DIR, DB_PATH
from settings import SETTINGS_PATH, get_webdav_backup_config, update_webdav_backup_status


LATEST_BACKUP_NAME = "arxiv-backup-latest.zip"
BACKUP_NAME_RE = re.compile(r"^arxiv-backup-(\d{8})-(\d{6})\.zip$")


def format_bytes(size_bytes):
    """格式化字节数。"""
    size = int(size_bytes or 0)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024 / 1024 / 1024:.1f} GB"


def get_database_file_sizes(db_path=DB_PATH):
    """统计 SQLite 主文件、WAL 和 SHM 文件占用。"""
    files = []
    total = 0
    for path in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
        if not os.path.exists(path):
            continue
        size = os.path.getsize(path)
        total += size
        files.append({
            "path": path,
            "name": os.path.basename(path),
            "size_bytes": size,
            "size": format_bytes(size),
        })
    return {
        "total_bytes": total,
        "total": format_bytes(total),
        "files": files,
    }


def _timestamp(now=None):
    now = now or datetime.now()
    return now.strftime("%Y%m%d-%H%M%S")


def _copy_sqlite_snapshot(db_path, snapshot_path):
    """使用 SQLite backup API 复制一致性数据库快照。"""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dest = sqlite3.connect(snapshot_path)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def create_backup_archive(
    archive_path,
    db_path=DB_PATH,
    settings_path=SETTINGS_PATH,
    now=None,
):
    """创建包含数据库快照、设置和清单的 zip 备份包。"""
    now = now or datetime.now()
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    db_sizes = get_database_file_sizes(db_path)
    manifest = {
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "database": {
            "source_path": db_path,
            "size_bytes": db_sizes["total_bytes"],
            "size": db_sizes["total"],
            "files": db_sizes["files"],
        },
        "included": {
            "database_snapshot": True,
            "settings_json": os.path.exists(settings_path),
        },
    }

    with tempfile.TemporaryDirectory(prefix="sqlite-snapshot-") as tmp:
        snapshot_path = os.path.join(tmp, "papers.db")
        _copy_sqlite_snapshot(db_path, snapshot_path)

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot_path, "papers.db")
            if os.path.exists(settings_path):
                zf.write(settings_path, "settings.json")
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    manifest["archive"] = {
        "path": archive_path,
        "size_bytes": os.path.getsize(archive_path),
        "size": format_bytes(os.path.getsize(archive_path)),
    }
    return manifest


def _normalize_runtime_config(config):
    config = dict(config or {})
    try:
        history_days = int(config.get("history_days") or 3)
    except (TypeError, ValueError):
        history_days = 3
    return {
        "enabled": bool(config.get("enabled", False)),
        "url": str(config.get("url") or "").strip().rstrip("/"),
        "username": str(config.get("username") or "").strip(),
        "password": str(config.get("password") or ""),
        "remote_dir": str(config.get("remote_dir") or "arxiv-backups").strip().strip("/") or "arxiv-backups",
        "history_days": max(1, min(3650, history_days)),
    }


def _auth(config):
    if config.get("username") or config.get("password"):
        return (config.get("username", ""), config.get("password", ""))
    return None


def _remote_url(config, *parts):
    base = config["url"].rstrip("/")
    segments = []
    if config.get("remote_dir"):
        segments.extend(seg for seg in config["remote_dir"].split("/") if seg)
    segments.extend(str(part) for part in parts if part)
    if not segments:
        return base + "/"
    encoded = "/".join(quote(seg.strip("/")) for seg in segments)
    return f"{base}/{encoded}"


def _request(method, url, config, **kwargs):
    kwargs.setdefault("timeout", 60)
    auth = _auth(config)
    if auth:
        kwargs.setdefault("auth", auth)
    return requests.request(method, url, **kwargs)


def ensure_webdav_directory(config):
    """逐级创建远端目录，已存在时忽略。"""
    if not config.get("remote_dir"):
        return
    partial = []
    for segment in config["remote_dir"].split("/"):
        if not segment:
            continue
        partial.append(segment)
        url = _remote_url({**config, "remote_dir": "/".join(partial)})
        resp = _request("MKCOL", url, config)
        if resp.status_code in (200, 201, 204, 301, 302, 405):
            continue
        resp.raise_for_status()


def upload_file(config, local_path, remote_name):
    """上传一个本地文件到 WebDAV。"""
    url = _remote_url(config, remote_name)
    with open(local_path, "rb") as f:
        resp = _request("PUT", url, config, data=f)
    if resp.status_code not in (200, 201, 204):
        resp.raise_for_status()
    return remote_name


def list_webdav_files(config):
    """列出远端目录下的文件名；服务器不支持 PROPFIND 时返回空列表。"""
    url = _remote_url(config)
    resp = _request("PROPFIND", url, config, headers={"Depth": "1"})
    if resp.status_code == 404:
        return []
    if resp.status_code not in (200, 207):
        resp.raise_for_status()
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []
    names = []
    for item in root.findall(".//{DAV:}response"):
        href = item.findtext("{DAV:}href")
        if not href:
            continue
        path = unquote(urlsplit(href).path.rstrip("/"))
        name = posixpath.basename(path)
        if name and name != posixpath.basename(config.get("remote_dir", "").rstrip("/")):
            names.append(name)
    return names


def cleanup_old_backups(config, now=None):
    """删除超过 history_days 的日期历史备份，保留 latest。"""
    now = now or datetime.now()
    cutoff = now - timedelta(days=config["history_days"])
    deleted = []
    for name in list_webdav_files(config):
        match = BACKUP_NAME_RE.match(name)
        if not match:
            continue
        backup_time = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
        if backup_time >= cutoff:
            continue
        url = _remote_url(config, name)
        resp = _request("DELETE", url, config)
        if resp.status_code not in (200, 202, 204, 404):
            resp.raise_for_status()
        deleted.append(name)
    return deleted


def run_webdav_backup(config=None, force=False, record_status=True, now=None):
    """
    执行一次 WebDAV 备份。

    force=True 用于手动备份：即使 enabled=false，只要配置完整也会执行。
    """
    now = now or datetime.now()
    runtime_config = _normalize_runtime_config(config or get_webdav_backup_config(mask_password=False))
    if not runtime_config["enabled"] and not force:
        return {"status": "skipped", "message": "WebDAV 云备份未启用"}
    if not runtime_config["url"]:
        raise ValueError("请先填写 WebDAV 地址")

    historical_name = f"arxiv-backup-{_timestamp(now)}.zip"
    os.makedirs(DB_DIR, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="webdav-backup-", dir=DB_DIR) as tmp:
            archive_path = os.path.join(tmp, historical_name)
            manifest = create_backup_archive(archive_path, now=now)
            ensure_webdav_directory(runtime_config)
            uploaded = [
                upload_file(runtime_config, archive_path, historical_name),
                upload_file(runtime_config, archive_path, LATEST_BACKUP_NAME),
            ]
            deleted = cleanup_old_backups(runtime_config, now=now)

        result = {
            "status": "ok",
            "message": f"WebDAV 备份完成：{historical_name}",
            "uploaded_files": uploaded,
            "deleted_files": deleted,
            "last_uploaded_file": historical_name,
            "archive_size_bytes": manifest["archive"]["size_bytes"],
            "archive_size": manifest["archive"]["size"],
            "db_size_bytes": manifest["database"]["size_bytes"],
            "db_size": manifest["database"]["size"],
        }
        if record_status:
            update_webdav_backup_status("success", uploaded_file=historical_name)
        return result
    except Exception as exc:
        if record_status:
            update_webdav_backup_status("error", error=str(exc))
        raise
