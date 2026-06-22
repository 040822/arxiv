"""
每日报告邮件发送模块。

使用 Python 标准库 SMTP 客户端发送邮件专用摘要版日报，不额外引入依赖。
"""

import base64
import html
import json
import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from urllib.parse import unquote, urlparse

from database import get_connection
from settings import (
    get_email_report_config,
    get_personalization_config,
    get_proxy_config,
    get_research_interest_hash,
    update_email_report_status,
)


EMAIL_TIMEOUT_SECONDS = 30
IMPORTANT_SCORE_THRESHOLD = 80
OVERVIEW_LIMIT = 20

EMAIL_CSS = """
* { box-sizing: border-box; }
body {
    margin: 0;
    padding: 0;
    background: #f5f7fa;
    color: #1e293b;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
    line-height: 1.7;
    font-size: 14px;
}
.email-shell {
    max-width: 680px;
    margin: 0 auto;
    padding: 24px 16px;
}
.email-header {
    padding: 34px 36px;
    margin-bottom: 26px;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    color: #ffffff;
    border-radius: 18px;
}
.badge-row {
    display: flex;
    gap: 10px;
    margin-bottom: 16px;
    flex-wrap: wrap;
}
.badge {
    display: inline-block;
    padding: 5px 13px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: .5px;
}
.badge-date {
    background: rgba(255,255,255,.20);
    color: #ffffff;
}
.badge-count {
    background: rgba(76,175,80,.32);
    color: #a5d6a7;
}
.email-header h1 {
    margin: 0 0 10px;
    font-size: 25px;
    line-height: 1.3;
    font-weight: 800;
    letter-spacing: -.5px;
}
.email-meta {
    margin: 0;
    color: rgba(255,255,255,.70);
    font-size: 14px;
}
.stats-row {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-top: 22px;
}
.stat-card {
    flex: 1 1 120px;
    min-width: 120px;
    padding: 13px 16px;
    background: rgba(255,255,255,.10);
    border: 1px solid rgba(255,255,255,.15);
    border-radius: 10px;
    text-align: center;
}
.stat-num {
    display: block;
    color: #4fc3f7;
    font-size: 23px;
    font-weight: 800;
    line-height: 1.1;
}
.stat-label {
    display: block;
    color: rgba(255,255,255,.62);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .5px;
    margin-top: 4px;
}
.section {
    margin-bottom: 28px;
}
.section-title {
    margin: 0 0 16px;
    color: #1a1a2e;
    font-size: 17px;
    font-weight: 800;
    padding-left: 14px;
    border-left: 4px solid #0f3460;
}
.summary-card {
    background: #ffffff;
    border: 1px solid #e8ecf1;
    border-radius: 12px;
    padding: 18px 22px;
    box-shadow: 0 1px 6px rgba(0,0,0,.05);
}
.summary-chips {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 14px;
}
.summary-chips-label {
    color: #0f3460;
    font-size: 13px;
    font-weight: 800;
    margin-right: 2px;
}
.kp-chip {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 14px;
    background: #0f3460;
    color: #ffffff;
    font-size: 12.5px;
    font-weight: 700;
}
.summary-prose {
    margin: 0;
    padding-top: 13px;
    border-top: 1px solid #edf2f7;
    color: #64748b;
    font-size: 13.5px;
    line-height: 1.75;
}
.deep-card {
    background: #ffffff;
    border: 1px solid #e8ecf1;
    border-radius: 14px;
    margin-bottom: 16px;
    overflow: hidden;
    box-shadow: 0 2px 10px rgba(0,0,0,.06);
}
.deep-head {
    background: linear-gradient(135deg, #0f3460, #1a1a2e);
    color: #ffffff;
    padding: 18px 22px 16px;
}
.deep-head-tag {
    display: inline-block;
    background: #e74c3c;
    color: #ffffff;
    font-size: 11px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 20px;
    margin-bottom: 9px;
}
.deep-head-tag .deep-rank {
    color: inherit;
    font-weight: 800;
}
.deep-title {
    margin: 0 0 7px;
    font-size: 17px;
    font-weight: 800;
    line-height: 1.4;
}
.deep-title a {
    color: #ffffff;
    text-decoration: none;
}
.deep-authors {
    font-size: 12.5px;
    color: rgba(255,255,255,.78);
    margin: 0 0 11px;
}
.deep-meta-row {
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    font-size: 12px;
    color: rgba(255,255,255,.72);
    align-items: center;
}
.deep-meta-row .score-pill {
    background: rgba(76,175,80,.30);
    color: #a5d6a7;
}
.deep-stars {
    color: #fbbf24;
    font-size: 14px;
    white-space: nowrap;
}
.deep-cat {
    font-family: "SFMono-Regular", Consolas, Menlo, monospace;
    font-size: 11.5px;
}
.paper-body {
    padding: 18px 22px 20px;
}
.tag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 0 0 14px;
}
.tag-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 10px;
    background: #e8f0f8;
    color: #2c5282;
    font-size: 12px;
    font-weight: 600;
}
.text-block {
    margin: 0 0 14px;
    color: #3e4c5e;
    font-size: 13.5px;
    line-height: 1.75;
}
.reason-block {
    background: linear-gradient(135deg, #fff8e1, #fff3e0);
    border-radius: 0 10px 10px 0;
    padding: 13px 16px;
    color: #5b4500;
}
.field-label {
    display: inline-block;
    margin-right: 6px;
    padding: 1px 8px;
    border-radius: 8px;
    background: #eef2f7;
    color: #475569;
    font-size: 11.5px;
    font-weight: 700;
}
.reason-block .field-label {
    background: rgba(229,81,0,.15);
    color: #e65100;
}
.links {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 16px;
}
.link-pill {
    display: inline-block;
    padding: 6px 13px;
    border-radius: 8px;
    background: #f0f4f8;
    color: #0f3460;
    font-size: 12.5px;
    font-weight: 700;
    text-decoration: none;
}
.link-pill.primary {
    background: #0f3460;
    color: #ffffff;
}
.overview-card {
    background: #ffffff;
    border: 1px solid #e8ecf1;
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 1px 6px rgba(0,0,0,.05);
}
.overview-item {
    display: flex;
    border-bottom: 1px solid #edf2f7;
}
.overview-item:last-child {
    border-bottom: 0;
}
.overview-rank {
    min-width: 42px;
    background: #f0f4ff;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding-top: 15px;
    font-size: 16px;
    font-weight: 800;
    color: #0f3460;
}
.overview-content {
    flex: 1;
    min-width: 0;
    padding: 14px 18px;
}
.overview-title {
    color: #1a1a2e;
    font-weight: 700;
    font-size: 14.5px;
    line-height: 1.45;
    margin-bottom: 5px;
}
.overview-title a {
    color: #1a1a2e;
    text-decoration: none;
}
.overview-meta {
    color: #888;
    font-size: 11.5px;
    margin-bottom: 8px;
}
.overview-meta .ov-stars {
    color: #f59e0b;
    font-weight: 700;
}
.overview-meta .ov-score {
    color: #0f3460;
    font-weight: 700;
}
.overview-meta .ov-arxiv {
    font-family: "SFMono-Regular", Consolas, Menlo, monospace;
    color: #94a3b8;
}
.overview-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin-bottom: 7px;
}
.overview-note {
    color: #555;
    font-size: 12.5px;
    line-height: 1.7;
    margin: 0;
}
.overview-links {
    margin-top: 8px;
}
.overview-links .link-pill {
    padding: 4px 10px;
    font-size: 11.5px;
}
.empty-state {
    background: #ffffff;
    border: 1px dashed #cbd5e1;
    border-radius: 12px;
    color: #64748b;
    padding: 24px 20px;
    text-align: center;
}
.full-report {
    text-align: center;
    margin: 30px 0 10px;
}
.full-link {
    display: inline-block;
    padding: 13px 28px;
    border-radius: 24px;
    background: linear-gradient(135deg, #0f3460, #1a1a2e);
    color: #ffffff;
    font-size: 15px;
    font-weight: 700;
    text-decoration: none;
}
.email-footer {
    margin-top: 28px;
    padding-top: 18px;
    border-top: 1px solid #e8ecf1;
    text-align: center;
    color: #999;
    font-size: 11.5px;
    line-height: 1.7;
}
@media (max-width: 600px) {
    .email-shell { padding: 16px 10px; }
    .email-header { padding: 26px 22px; }
    .email-header h1 { font-size: 21px; }
    .stat-card { flex: 1 1 calc(50% - 6px); min-width: calc(50% - 6px); }
    .deep-head { padding: 16px 18px 14px; }
    .paper-body { padding: 16px 18px; }
    .overview-rank { min-width: 34px; padding-top: 14px; font-size: 14px; }
    .overview-content { padding: 13px 14px; }
    .summary-chips { gap: 6px; }
}
"""


class _SafeFormatDict(dict):
    """缺失字段时保留原占位符，避免主题模板配置错误导致发送失败。"""

    def __missing__(self, key):
        return "{" + key + "}"


def _sender_from_config(config):
    return (config.get("sender") or config.get("username") or "").strip()


def _normalize_recipients(value):
    if isinstance(value, str):
        parts = re.split(r"[,;\n\r]+", value)
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = []
    return [str(part or "").strip() for part in parts if str(part or "").strip()]


def _normalize_runtime_config(config):
    config = dict(config or {})
    security = str(config.get("security") or "starttls").strip().lower()
    if security not in {"starttls", "ssl", "none"}:
        security = "starttls"
    config["security"] = security
    config["recipients"] = _normalize_recipients(config.get("recipients"))
    config["site_url"] = str(config.get("site_url") or "").strip().rstrip("/")
    return config


def _resolve_smtp_proxy_url(proxy_config=None):
    """返回 SMTP 发送要使用的代理 URL；HTTPS 代理配置优先。"""
    proxy = proxy_config if proxy_config is not None else get_proxy_config()
    if not proxy.get("enabled"):
        return ""
    return str(proxy.get("https") or proxy.get("http") or "").strip()


def _parse_proxy_url(proxy_url):
    parsed = urlparse(proxy_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("报告邮件 SMTP 代理仅支持 HTTP CONNECT 代理")
    if not parsed.hostname:
        raise ValueError("报告邮件 SMTP 代理地址缺少主机名")
    return parsed


def _create_proxy_tunnel(host, port, timeout, proxy_url):
    """通过 HTTP CONNECT 创建到 SMTP 服务器的隧道 socket。"""
    parsed = _parse_proxy_url(proxy_url)
    proxy_port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    raw_sock = socket.create_connection((parsed.hostname, proxy_port), timeout=timeout)
    sock = raw_sock
    try:
        if parsed.scheme.lower() == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(raw_sock, server_hostname=parsed.hostname)

        target = f"{host}:{int(port)}"
        headers = [
            f"CONNECT {target} HTTP/1.1",
            f"Host: {target}",
            "Proxy-Connection: Keep-Alive",
        ]
        if parsed.username:
            username = unquote(parsed.username)
            password = unquote(parsed.password or "")
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            headers.append(f"Proxy-Authorization: Basic {token}")
        request = "\r\n".join(headers) + "\r\n\r\n"
        sock.sendall(request.encode("ascii"))

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if len(response) > 65536:
                raise ConnectionError("SMTP 代理 CONNECT 响应过大")

        response_text = response.decode("iso-8859-1", errors="replace")
        status_line = response_text.splitlines()[0] if response_text.splitlines() else "无响应"
        parts = status_line.split()
        status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
        if status_code != 200:
            raise ConnectionError(f"SMTP 代理 CONNECT 失败：{status_line}")
        return sock
    except Exception:
        try:
            sock.close()
        finally:
            if sock is not raw_sock:
                raw_sock.close()
        raise


class _ProxySMTP(smtplib.SMTP):
    """使用 HTTP CONNECT 代理隧道的 SMTP 客户端。"""

    def __init__(self, *args, proxy_url="", **kwargs):
        self._smtp_proxy_url = proxy_url
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        if self._smtp_proxy_url:
            return _create_proxy_tunnel(host, port, timeout, self._smtp_proxy_url)
        return super()._get_socket(host, port, timeout)


class _ProxySMTP_SSL(smtplib.SMTP_SSL):
    """使用 HTTP CONNECT 代理隧道的 SMTP over SSL 客户端。"""

    def __init__(self, *args, proxy_url="", **kwargs):
        self._smtp_proxy_url = proxy_url
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        if self._smtp_proxy_url:
            sock = _create_proxy_tunnel(host, port, timeout, self._smtp_proxy_url)
            return self.context.wrap_socket(sock, server_hostname=host)
        return super()._get_socket(host, port, timeout)


def _rewrite_relative_links(content, site_url):
    """将 href="/..." 形式的站内链接改为绝对链接。"""
    site_url = str(site_url or "").strip().rstrip("/")
    if not site_url:
        return content

    def replace(match):
        quote = match.group(1)
        path = match.group(2)
        return f"href={quote}{site_url}/{path}{quote}"

    return re.sub(r"href=(['\"])/(?!/)([^'\"]*)\1", replace, content)


def _esc(value):
    return html.escape(str(value or ""), quote=True)


def _json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _truncate(value, limit=150):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _format_authors(authors, limit=3):
    authors = authors or []
    if len(authors) > limit:
        return ", ".join(authors[:limit]) + f" et al. ({len(authors)} authors)"
    return ", ".join(authors)


def _format_stars(value):
    rating = max(0, min(5, _safe_int(value)))
    return " ".join("★" if i < rating else "☆" for i in range(5))


def _report_detail_url(report_date, config):
    site_url = str(config.get("site_url") or "").strip().rstrip("/")
    return f"{site_url}/reports/{report_date}" if site_url and report_date else ""


def _paper_detail_url(paper, config):
    site_url = str(config.get("site_url") or "").strip().rstrip("/")
    if site_url and paper.get("arxiv_id"):
        return f"{site_url}/paper/{paper['arxiv_id']}"
    return paper.get("url") or ""


def _load_report_papers(report_date):
    """读取邮件日报需要的轻量论文数据。"""
    if not report_date:
        return []
    current_interests = get_personalization_config().get("research_interests", "")
    current_interest_hash = get_research_interest_hash(current_interests)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.*, a.id AS analysis_id, a.tags, a.summary_cn, a.rating, a.value_comment,
               a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
               a.recommendation_analyzed_at
        FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE p.published_date = ?
        ORDER BY p.arxiv_id
    """, (report_date,))
    rows = cursor.fetchall()
    conn.close()

    papers = []
    for row in rows:
        item = dict(row)
        item["authors"] = _json_list(item.get("authors"))
        item["categories"] = _json_list(item.get("categories"))
        item["tags"] = _json_list(item.get("tags"))
        item["rating"] = _safe_int(item.get("rating"))
        score = None
        if current_interest_hash and item.get("recommendation_interest_hash") == current_interest_hash:
            raw_score = item.get("recommendation_score")
            if raw_score is not None:
                score = max(0, min(100, _safe_int(raw_score)))
        item["current_recommendation_score"] = score
        papers.append(item)

    papers.sort(key=lambda paper: (
        -(paper.get("current_recommendation_score") if paper.get("current_recommendation_score") is not None else -1),
        -_safe_int(paper.get("rating")),
        str(paper.get("arxiv_id") or ""),
    ))
    return papers


def build_report_email_data(report, config=None, ai_summary=None, ai_summary_error=""):
    """构建邮件专用日报数据，不复用网页报告 HTML。"""
    config = _normalize_runtime_config(config or get_email_report_config(mask_password=False))
    report_date = str(report.get("report_date") or "")
    papers = _load_report_papers(report_date)
    important = [
        paper for paper in papers
        if paper.get("current_recommendation_score") is not None
        and paper["current_recommendation_score"] > IMPORTANT_SCORE_THRESHOLD
    ]
    important_object_ids = {id(paper) for paper in important}
    overview = [paper for paper in papers if id(paper) not in important_object_ids][:OVERVIEW_LIMIT]
    ratings = [_safe_int(paper.get("rating")) for paper in papers if paper.get("analysis_id") is not None]
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else _safe_float(report.get("avg_rating"))
    total = len(papers) if papers else _safe_int(report.get("paper_count"))
    analyzed = sum(1 for paper in papers if paper.get("analysis_id") is not None) if papers else _safe_int(report.get("analyzed_count"))
    summary = str(ai_summary or "").strip()
    if not summary:
        summary = "AI 导读暂不可用。本邮件仍按推荐分、评级和已有分析结果整理重点论文与快速速览。"
    return {
        "report_date": report_date,
        "papers": papers,
        "important": important,
        "overview": overview,
        "total": total,
        "analyzed": analyzed,
        "avg_rating": avg_rating,
        "ai_summary": summary,
        "ai_summary_error": str(ai_summary_error or "").strip(),
        "full_report_url": _report_detail_url(report_date, config),
        "config": config,
    }


def _render_tag_spans(tags):
    return "".join(f'<span class="tag-pill">{_esc(tag)}</span>' for tag in (tags or [])[:5])


def _render_paper_links(paper, config):
    links = []
    detail_url = _paper_detail_url(paper, config)
    if detail_url:
        links.append(f'<a class="link-pill primary" style="text-decoration:none;color:#ffffff" href="{_esc(detail_url)}">查看详情</a>')
    if paper.get("url"):
        links.append(f'<a class="link-pill" style="text-decoration:none;color:#0f3460" href="{_esc(paper["url"])}">arXiv</a>')
    if paper.get("pdf_url"):
        links.append(f'<a class="link-pill" style="text-decoration:none;color:#0f3460" href="{_esc(paper["pdf_url"])}">PDF</a>')
    return "".join(links)


def _render_keyword_chips(papers, limit=5):
    """统计论文 tags 频次，渲染热门方向胶囊条；无 tags 回退 categories。"""
    from collections import Counter
    counter = Counter()
    for paper in papers or []:
        for tag in _json_list(paper.get("tags")):
            counter[str(tag).strip()] += 1
    items = [name for name, _ in counter.most_common(limit)]
    if not items:
        counter = Counter()
        for paper in papers or []:
            for cat in _json_list(paper.get("categories")):
                counter[str(cat).strip()] += 1
        items = [name for name, _ in counter.most_common(limit)]
    if not items:
        return ""
    return "".join(f'<span class="kp-chip">{_esc(name)}</span>' for name in items)


def build_report_email_html(report, config=None, ai_summary=None, ai_summary_error="", email_data=None):
    """构建完整邮件 HTML。"""
    data = email_data or build_report_email_data(report, config, ai_summary, ai_summary_error)
    config = data["config"]
    report_date = data["report_date"]
    important = data["important"]
    overview = data["overview"]
    full_report_url = data["full_report_url"]
    keyword_chips = _render_keyword_chips(data["papers"])
    chips_html = f'<div class="summary-chips"><span class="summary-chips-label">热门方向</span>{keyword_chips}</div>' if keyword_chips else ""

    important_html = ""
    if important:
        cards = []
        for index, paper in enumerate(important, 1):
            score = paper.get("current_recommendation_score")
            head_tags = _render_tag_spans((paper.get("tags") or paper.get("categories") or [])[:4])
            authors = _format_authors(paper.get("authors"))
            summary = paper.get("summary_cn") or paper.get("abstract") or ""
            reason = paper.get("recommendation_reason") or paper.get("value_comment") or ""
            links = _render_paper_links(paper, config)
            title_url = _paper_detail_url(paper, config) or paper.get("url") or "#"
            cat_html = f'<span class="deep-cat">{_esc(paper["primary_category"])}</span>' if paper.get("primary_category") else ''
            arxiv_html = f'<span class="deep-cat">{_esc(paper["arxiv_id"])}</span>' if paper.get("arxiv_id") else ''
            score_html = f'<span class="score-pill">推荐 {score}/100</span>' if score is not None else ''
            summary_html = f'<div class="text-block"><span class="field-label">摘要</span>{_esc(_truncate(summary, 280))}</div>' if summary else ''
            reason_html = f'<div class="text-block reason-block"><span class="field-label">推荐语</span>{_esc(_truncate(reason, 200))}</div>' if reason else ''
            links_html = f'<div class="links">{links}</div>' if links else ''
            head_meta_items = [x for x in [score_html, f'<span class="deep-stars">{_format_stars(paper.get("rating"))}</span>', cat_html, arxiv_html] if x]
            head_meta_html = "".join(head_meta_items)
            cards.append(f"""
                <div class="deep-card">
                    <div class="deep-head">
                        <div class="deep-head-tag"><span class="deep-rank">#{index}</span> 重点精读</div>
                        <div class="deep-title"><a style="text-decoration:none;color:#ffffff" href="{_esc(title_url)}">{_esc(paper.get("title"))}</a></div>
                        {f'<div class="deep-authors">{_esc(authors)}</div>' if authors else ''}
                        <div class="deep-meta-row">{head_meta_html}</div>
                    </div>
                    <div class="paper-body">
                        {f'<div class="tag-row">{head_tags}</div>' if head_tags else ''}
                        {summary_html}
                        {reason_html}
                        {links_html}
                    </div>
                </div>
            """)
        important_html = "".join(cards)
    else:
        important_html = '<div class="empty-state">今天没有推荐分高于 80 的重点精读论文。</div>'

    overview_html = ""
    if overview:
        items = []
        for index, paper in enumerate(overview, 1):
            score = paper.get("current_recommendation_score")
            tags = _render_tag_spans((paper.get("tags") or paper.get("categories") or [])[:4])
            note = paper.get("summary_cn") or paper.get("value_comment") or paper.get("abstract") or ""
            title_url = _paper_detail_url(paper, config) or paper.get("url") or "#"
            score_html = f'<span class="ov-score">推荐 {score}/100 · </span>' if score is not None else ''
            arxiv_html = f'<span class="ov-arxiv"> · {_esc(paper.get("arxiv_id"))}</span>' if paper.get("arxiv_id") else ''
            note_html = f'<p class="overview-note">{_esc(_truncate(note, 130))}</p>' if note else ''
            links_html = f'<div class="overview-links">{_render_paper_links(paper, config)}</div>'
            items.append(f"""
                <div class="overview-item">
                    <div class="overview-rank">{index}</div>
                    <div class="overview-content">
                        <div class="overview-title"><a style="text-decoration:none;color:#1a1a2e" href="{_esc(title_url)}">{_esc(paper.get("title"))}</a></div>
                        <div class="overview-meta">{score_html}<span class="ov-stars">{_format_stars(paper.get("rating"))}</span>{arxiv_html}</div>
                        {f'<div class="overview-tags">{tags}</div>' if tags else ''}
                        {note_html}
                        {links_html}
                    </div>
                </div>
            """)
        overview_html = "".join(items)
    else:
        overview_html = '<div class="empty-state">暂无可供速览的其他论文。</div>'

    full_link_html = ""
    if full_report_url:
        full_link_html = f'<div class="full-report"><a class="full-link" style="text-decoration:none;color:#ffffff" href="{_esc(full_report_url)}">查看完整 Web 报告</a></div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>{EMAIL_CSS}</style>
</head>
<body>
    <div class="email-shell">
        <div class="email-header">
            <div class="badge-row">
                <span class="badge badge-date">📅 {_esc(report_date)}</span>
                <span class="badge badge-count">📊 共扫描 {data["total"]} 篇论文</span>
            </div>
            <h1>AI 论文数据库每日速读</h1>
            <p class="email-meta">🤖 重点精读优先 · 其他论文压缩为快速速览</p>
            <div class="stats-row">
                <div class="stat-card"><span class="stat-num">{data["total"]}</span><span class="stat-label">论文总数</span></div>
                <div class="stat-card"><span class="stat-num">{len(important)}</span><span class="stat-label">重点精读</span></div>
                <div class="stat-card"><span class="stat-num">{len(overview)}</span><span class="stat-label">速览展示</span></div>
                <div class="stat-card"><span class="stat-num">{_esc(data["avg_rating"])}</span><span class="stat-label">平均评级</span></div>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">🔬 本日研究速览</h2>
            <div class="summary-card">{chips_html}<p class="summary-prose">{_esc(data["ai_summary"]).replace(chr(10), "<br>")}</p></div>
        </div>

        <div class="section">
            <h2 class="section-title">⭐ 重点精读（推荐分 &gt; {IMPORTANT_SCORE_THRESHOLD}）</h2>
            {important_html}
        </div>

        <div class="section">
            <h2 class="section-title">📋 快速速览（最多 {OVERVIEW_LIMIT} 篇）</h2>
            <div class="overview-card">{overview_html}</div>
        </div>

        {full_link_html}
        <div class="email-footer">AI 论文数据库 · 邮件版为精简摘要，完整内容请查看 Web 报告</div>
    </div>
</body>
</html>"""


def build_report_email_text(report, config=None, ai_summary=None, ai_summary_error="", email_data=None):
    """构建纯文本备用邮件。"""
    data = email_data or build_report_email_data(report, config, ai_summary, ai_summary_error)
    lines = [
        f"{data['report_date']} AI 论文日报",
        f"论文总数：{data['total']}｜重点精读：{len(data['important'])}｜速览展示：{len(data['overview'])}｜平均评级：{data['avg_rating']}",
        "",
        "本日研究速览",
        data["ai_summary"],
        "",
        "重点精读",
    ]
    if data["important"]:
        for index, paper in enumerate(data["important"], 1):
            score = paper.get("current_recommendation_score")
            lines.append(f"{index}. {paper.get('title', '')}（推荐 {score}/100，{_format_stars(paper.get('rating'))}）")
            note = paper.get("recommendation_reason") or paper.get("value_comment") or paper.get("summary_cn") or ""
            if note:
                lines.append(f"   {_truncate(note, 160)}")
    else:
        lines.append("今天没有推荐分高于 80 的重点精读论文。")
    lines.extend(["", "快速速览"])
    if data["overview"]:
        for paper in data["overview"]:
            score = paper.get("current_recommendation_score")
            score_text = f"推荐 {score}/100，" if score is not None else ""
            lines.append(f"- {paper.get('title', '')}（{score_text}{_format_stars(paper.get('rating'))}）")
    else:
        lines.append("暂无可供速览的其他论文。")
    if data["full_report_url"]:
        lines.extend(["", f"完整报告：{data['full_report_url']}"])
    return "\n".join(lines)


def build_report_email_subject(report, config=None):
    """根据主题模板构建邮件主题。"""
    config = _normalize_runtime_config(config or get_email_report_config(mask_password=False))
    values = _SafeFormatDict({
        "date": report.get("report_date") or "",
        "report_date": report.get("report_date") or "",
        "paper_count": report.get("paper_count") or 0,
        "analyzed_count": report.get("analyzed_count") or 0,
        "avg_rating": report.get("avg_rating") or 0,
    })
    template = config.get("subject_template") or "AI 论文日报 {date} - {paper_count} 篇论文"
    try:
        return template.format_map(values)
    except (KeyError, ValueError):
        return f"AI 论文日报 {values['date']} - {values['paper_count']} 篇论文"


def _validate_send_config(config):
    if not config.get("smtp_host"):
        raise ValueError("请先填写 SMTP 服务器地址")
    if not config.get("recipients"):
        raise ValueError("请至少填写一个收件人邮箱")
    if not _sender_from_config(config):
        raise ValueError("请填写发件人邮箱或 SMTP 用户名")


def _send_message(config, message):
    context = ssl.create_default_context()
    security = config.get("security", "starttls")
    host = config["smtp_host"]
    port = int(config.get("smtp_port") or (465 if security == "ssl" else 587))
    proxy_url = _resolve_smtp_proxy_url()
    if security == "ssl":
        smtp_cls = _ProxySMTP_SSL if proxy_url else smtplib.SMTP_SSL
        kwargs = {"timeout": EMAIL_TIMEOUT_SECONDS, "context": context}
        if proxy_url:
            kwargs["proxy_url"] = proxy_url
        with smtp_cls(host, port, **kwargs) as smtp:
            if config.get("username"):
                smtp.login(config["username"], config.get("password", ""))
            smtp.send_message(message)
        return

    smtp_cls = _ProxySMTP if proxy_url else smtplib.SMTP
    kwargs = {"timeout": EMAIL_TIMEOUT_SECONDS}
    if proxy_url:
        kwargs["proxy_url"] = proxy_url
    with smtp_cls(host, port, **kwargs) as smtp:
        if security == "starttls":
            smtp.starttls(context=context)
        if config.get("username"):
            smtp.login(config["username"], config.get("password", ""))
        smtp.send_message(message)


def send_report_email(report, config=None, force=False, record_status=True, ai_summary=None, ai_summary_error=""):
    """
    发送单份报告邮件。

    force=True 用于手动测试：即使 enabled=false，只要 SMTP 配置完整也会执行。
    """
    runtime_config = _normalize_runtime_config(config or get_email_report_config(mask_password=False))
    if not runtime_config.get("enabled") and not force:
        return {"status": "skipped", "message": "报告邮件发送未启用"}

    report_date = str(report.get("report_date") or "")
    try:
        _validate_send_config(runtime_config)
        subject = build_report_email_subject(report, runtime_config)
        email_data = build_report_email_data(
            report,
            runtime_config,
            ai_summary=ai_summary,
            ai_summary_error=ai_summary_error,
        )
        text_content = build_report_email_text(report, runtime_config, email_data=email_data)
        html_content = build_report_email_html(report, runtime_config, email_data=email_data)
        sender = _sender_from_config(runtime_config)
        recipients = runtime_config.get("recipients") or []

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message.set_content(text_content)
        message.add_alternative(html_content, subtype="html")

        _send_message(runtime_config, message)
        if record_status:
            update_email_report_status(
                "success",
                report_date="" if force else report_date,
            )
        return {
            "status": "ok",
            "message": f"报告邮件已发送：{report_date}",
            "report_date": report_date,
            "recipients": recipients,
            "subject": subject,
            "important_count": len(email_data["important"]),
            "overview_count": len(email_data["overview"]),
            "ai_summary_error": email_data.get("ai_summary_error", ""),
        }
    except Exception as exc:
        if record_status:
            update_email_report_status("error", error=str(exc))
        raise
