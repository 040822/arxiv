"""Manual paper import preview and confirmation helpers."""

import html
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests


class PaperImportError(ValueError):
    """A user-correctable import error."""

def validate_public_http_url(value):
    """Reject non-HTTP and local/private destinations before server-side fetches."""
    url = str(value or "").strip()
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip().lower()
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise PaperImportError("链接必须是有效的 HTTP/HTTPS URL")
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise PaperImportError("不允许访问本机或私有网络地址")
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(
                    hostname,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except OSError as exc:
            raise PaperImportError(f"无法解析论文链接域名: {hostname}") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise PaperImportError("不允许访问本机或私有网络地址")
    return url


def _safe_http_get(url, **kwargs):
    """GET metadata while validating every redirect destination."""
    current = str(url)
    kwargs = dict(kwargs)
    kwargs["allow_redirects"] = False
    kwargs["stream"] = True
    for _ in range(6):
        validate_public_http_url(current)
        response = requests.get(current, **kwargs)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            if not location:
                return response
            response.close()
            current = urljoin(current, location)
            continue
        limit = 5 * 1024 * 1024
        length = int(response.headers.get("Content-Length") or 0)
        if length > limit:
            raise PaperImportError("论文元数据页面不能超过 5 MB")
        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if chunk:
                content.extend(chunk)
            if len(content) > limit:
                response.close()
                raise PaperImportError("论文元数据页面不能超过 5 MB")
        response._content = bytes(content)
        response._content_consumed = True
        return response
    raise PaperImportError("论文链接重定向次数过多")



def _content_value(content, key, default=""):
    value = (content or {}).get(key, default)
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    return value if value is not None else default


def _base_draft(source_type, source_id, source_url):
    return {
        "source_type": source_type,
        "source_id": source_id or "",
        "source_url": source_url or "",
        "title": "",
        "authors": [],
        "abstract": "",
        "venue": "",
        "published_date": "",
        "updated_date": "",
        "pdf_url": "",
        "arxiv_id": None,
        "warnings": [],
    }


def _openreview_id(source_url):
    parsed = urlparse(source_url)
    if parsed.hostname not in {"openreview.net", "www.openreview.net"}:
        return ""
    return str((parse_qs(parsed.query).get("id") or [""])[0]).strip()

class _ScholarlyMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        attrs = {str(key).lower(): value for key, value in attrs}
        name = str(attrs.get("name") or attrs.get("property") or "").lower()
        content = str(attrs.get("content") or "").strip()
        if name and content:
            self.values.setdefault(name, []).append(content)


def _doi_value(source_url):
    value = str(source_url or "").strip()
    parsed = urlparse(value)
    if parsed.hostname in {"doi.org", "dx.doi.org", "www.doi.org"}:
        value = parsed.path.lstrip("/")
    elif value.lower().startswith("doi:"):
        value = value[4:]
    return value.strip().lower() if re.match(r"^10\.\d{4,9}/\S+$", value.strip(), re.I) else ""


def _manual_arxiv_id(source_url):
    """Recognize only a bare modern ID or an actual arxiv.org paper URL."""
    value = str(source_url or "").strip()
    direct = re.fullmatch(
        r"(?:arxiv\s*:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?",
        value,
        re.I,
    )
    if direct:
        return direct.group(1)
    parsed = urlparse(value)
    if (parsed.hostname or "").lower() not in {
        "arxiv.org", "www.arxiv.org", "export.arxiv.org",
    }:
        return ""
    match = re.fullmatch(
        r"/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?",
        parsed.path,
        re.I,
    )
    return match.group(1) if match else ""

def _plain_text(value):
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(text).split())


def _date_from_parts(value):
    parts = ((value or {}).get("date-parts") or [[]])[0]
    if not parts:
        return ""
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 else 1
    day = int(parts[2]) if len(parts) > 2 else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def _preview_doi(source_url, http_get):
    doi = _doi_value(source_url)
    response = http_get(
        f"https://api.crossref.org/works/{quote(doi, safe='')}",
        timeout=30,
        headers={"User-Agent": "ArxivPaperDB/1.0"},
    )
    response.raise_for_status()
    item = (response.json() or {}).get("message") or {}
    draft = _base_draft("doi", doi, str(item.get("URL") or source_url))
    authors = []
    for author in item.get("author") or []:
        name = author.get("name") or " ".join(
            part for part in (author.get("given", ""), author.get("family", "")) if part
        )
        if str(name).strip():
            authors.append(str(name).strip())
    pdf_link = next((
        link.get("URL", "") for link in item.get("link") or []
        if link.get("content-type") == "application/pdf"
    ), "")
    draft.update({
        "title": str((item.get("title") or [""])[0]).strip(),
        "authors": authors,
        "abstract": _plain_text(item.get("abstract")),
        "venue": str((item.get("container-title") or [""])[0]).strip(),
        "published_date": _date_from_parts(item.get("published") or item.get("issued")),
        "pdf_url": str(pdf_link).strip(),
    })
    return draft


def _first_meta(values, *names):
    for name in names:
        items = values.get(name.lower()) or []
        if items:
            return items[0]
    return ""


def _normalise_date(value):
    match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", str(value or "").strip())
    if not match:
        return ""
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _preview_web(source_url, http_get):
    parsed_url = urlparse(source_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise PaperImportError("论文页面必须是有效的 HTTP/HTTPS URL")
    response = http_get(
        source_url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 ArxivPaperDB/1.0"},
    )
    response.raise_for_status()
    parser = _ScholarlyMetaParser()
    parser.feed(response.text or "")
    values = parser.values
    title = _first_meta(values, "citation_title", "dc.title", "og:title")
    draft = _base_draft("web", "", source_url)
    if not title:
        draft["warnings"].append("网页没有可识别的学术元数据，请手工填写或上传 PDF")
        return draft
    authors = values.get("citation_author") or values.get("dc.creator") or []
    pdf_url = _first_meta(values, "citation_pdf_url")
    draft.update({
        "title": title.strip(), "authors": [item.strip() for item in authors if item.strip()],
        "abstract": _first_meta(values, "citation_abstract", "dc.description", "description").strip(),
        "venue": _first_meta(values, "citation_conference_title", "citation_journal_title").strip(),
        "published_date": _normalise_date(_first_meta(values, "citation_publication_date", "citation_date")),
        "pdf_url": urljoin(source_url, pdf_url) if pdf_url else "",
    })
    return draft


def _preview_openreview(source_url, http_get):
    forum_id = _openreview_id(source_url)
    if not forum_id:
        raise PaperImportError("OpenReview 链接缺少 forum id")
    note = None
    try:
        response = http_get(
            f"https://api2.openreview.net/notes?id={quote(forum_id)}",
            timeout=30,
        )
        response.raise_for_status()
        notes = (response.json() or {}).get("notes") or []
        note = notes[0] if notes else None
    except Exception:
        note = None
    if not note:
        response = http_get(
            f"https://api.openreview.net/notes?forum={quote(forum_id)}",
            timeout=30,
        )
        response.raise_for_status()
        notes = (response.json() or {}).get("notes") or []
        note = next((item for item in notes if item.get("id") == item.get("forum")), None)
        note = note or (notes[0] if notes else None)
    if not note:
        raise PaperImportError("未能从 OpenReview 读取论文元数据")

    content = note.get("content") or {}
    draft = _base_draft("openreview", forum_id, source_url)
    authors = _content_value(content, "authors", [])
    if isinstance(authors, str):
        authors = [item.strip() for item in authors.split(",") if item.strip()]
    draft.update({
        "title": str(_content_value(content, "title", "")).strip(),
        "authors": authors if isinstance(authors, list) else [],
        "abstract": str(_content_value(content, "abstract", "")).strip(),
        "venue": str(
            _content_value(content, "venue", "")
            or _content_value(content, "venueid", "")
        ).strip(),
        "pdf_url": f"https://openreview.net/pdf?id={quote(forum_id)}",
    })
    return draft


def _preview_pdf(pdf_path, metadata_extractor=None):
    from source.documents import extract_text_from_pdf

    draft = _base_draft("upload", "", "")
    text = extract_text_from_pdf(pdf_path) or ""
    if not text:
        draft["warnings"].append("PDF 没有可提取文本，请手工填写元数据；扫描件暂不支持全文分析")
        return draft
    if metadata_extractor is None:
        from source.analysis import extract_paper_import_metadata
        metadata_extractor = extract_paper_import_metadata
    extracted = metadata_extractor(text)
    error = None
    if isinstance(extracted, tuple):
        extracted, error = extracted
    if isinstance(extracted, dict):
        for key in ("title", "authors", "abstract", "venue", "published_date"):
            if extracted.get(key):
                draft[key] = extracted[key]
    if error:
        draft["warnings"].append(f"AI 元数据预填失败：{error}")
    draft["has_upload"] = True
    return draft


def preview_import(source_url="", pdf_path=None, http_get=None, metadata_extractor=None):
    """Resolve user inputs into editable metadata without writing a paper."""
    source_url = str(source_url or "").strip()
    http_get = http_get or _safe_http_get
    if pdf_path:
        pdf_draft = _preview_pdf(pdf_path, metadata_extractor=metadata_extractor)
        pdf_draft["source_url"] = source_url
        if not source_url:
            return pdf_draft
        try:
            link_draft = preview_import(source_url=source_url, http_get=http_get)
        except Exception as exc:
            pdf_draft["warnings"].append(f"来源链接解析失败，已保留 PDF 结果：{exc}")
            return pdf_draft
        for key in ("title", "authors", "abstract", "venue", "published_date"):
            if not link_draft.get(key) and pdf_draft.get(key):
                link_draft[key] = pdf_draft[key]
        link_draft["has_upload"] = True
        link_draft["warnings"] = list(link_draft.get("warnings") or []) + list(
            pdf_draft.get("warnings") or []
        )
        return link_draft
    if _openreview_id(source_url):
        return _preview_openreview(source_url, http_get)
    if _doi_value(source_url):
        return _preview_doi(source_url, http_get)
    arxiv_id = _manual_arxiv_id(source_url)
    if arxiv_id:
        from source.ingestion import lookup_paper_by_id
        draft = lookup_paper_by_id(arxiv_id)
        if not draft:
            raise PaperImportError("未能从 arXiv 读取论文元数据")
        return draft
    if source_url.lower().split("?", 1)[0].endswith(".pdf"):
        draft = _base_draft("web", source_url, source_url)
        draft["pdf_url"] = source_url
        draft["warnings"].append("PDF 直链已识别；请确认元数据后导入")
        return draft
    if urlparse(source_url).scheme in {"http", "https"}:
        return _preview_web(source_url, http_get)
    raise PaperImportError("暂不支持该论文来源")


__all__ = ["PaperImportError", "preview_import"]
