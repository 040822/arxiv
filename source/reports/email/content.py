"""Build report-email data, HTML, text and subjects."""

import html
import json
import re
from importlib.resources import files

from source.storage import get_connection
from source.value_coercion import as_float, as_int
from source.settings import (
    get_email_report_config,
    get_personalization_config,
    get_research_interest_hash,
)

from .config import (
    DEFAULT_IMPORTANT_SCORE_THRESHOLD,
    DEFAULT_OVERVIEW_LIMIT,
    _SafeFormatDict,
    _normalize_runtime_config,
)

EMAIL_CSS = files(__package__).joinpath("email.css").read_text(encoding="utf-8")

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
    rating = max(0, min(5, as_int(value)))
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
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.*, a.id AS analysis_id, a.tags, a.summary_cn, a.rating, a.value_comment,
                   a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                   a.recommendation_analyzed_at
            FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE p.published_date = ? AND p.ingest_mode = 'feed'
            ORDER BY p.arxiv_id
        """, (report_date,))
        rows = cursor.fetchall()


    papers = []
    for row in rows:
        item = dict(row)
        item["authors"] = _json_list(item.get("authors"))
        item["categories"] = _json_list(item.get("categories"))
        item["tags"] = _json_list(item.get("tags"))
        item["rating"] = as_int(item.get("rating"))
        score = None
        if current_interest_hash and item.get("recommendation_interest_hash") == current_interest_hash:
            raw_score = item.get("recommendation_score")
            if raw_score is not None:
                score = max(0, min(100, as_int(raw_score)))
        item["current_recommendation_score"] = score
        papers.append(item)

    papers.sort(key=lambda paper: (
        -(paper.get("current_recommendation_score") if paper.get("current_recommendation_score") is not None else -1),
        -as_int(paper.get("rating")),
        str(paper.get("arxiv_id") or ""),
    ))
    return papers


def build_report_email_data(report, config=None, ai_summary=None, ai_summary_error=""):
    """构建邮件专用日报数据，不复用网页报告 HTML。"""
    config = _normalize_runtime_config(config or get_email_report_config(mask_password=False))
    report_date = str(report.get("report_date") or "")
    papers = _load_report_papers(report_date)
    important_threshold = int(config.get("important_score_threshold", DEFAULT_IMPORTANT_SCORE_THRESHOLD))
    overview_limit = int(config.get("overview_limit", DEFAULT_OVERVIEW_LIMIT))
    important = [
        paper for paper in papers
        if paper.get("current_recommendation_score") is not None
        and paper["current_recommendation_score"] > important_threshold
    ]
    important_object_ids = {id(paper) for paper in important}
    overview = [paper for paper in papers if id(paper) not in important_object_ids][:overview_limit]
    ratings = [as_int(paper.get("rating")) for paper in papers if paper.get("analysis_id") is not None]
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else as_float(report.get("avg_rating"))
    total = len(papers) if papers else as_int(report.get("paper_count"))
    analyzed = sum(1 for paper in papers if paper.get("analysis_id") is not None) if papers else as_int(report.get("analyzed_count"))
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
        "important_score_threshold": important_threshold,
        "overview_limit": overview_limit,
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
    important_threshold = data.get("important_score_threshold", DEFAULT_IMPORTANT_SCORE_THRESHOLD)
    overview_limit = data.get("overview_limit", DEFAULT_OVERVIEW_LIMIT)
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
        important_html = f'<div class="empty-state">今天没有推荐分高于 {important_threshold} 的重点精读论文。</div>'

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
            <h2 class="section-title">⭐ 重点精读（推荐分 &gt; {important_threshold}）</h2>
            {important_html}
        </div>

        <div class="section">
            <h2 class="section-title">📋 快速速览（最多 {overview_limit} 篇）</h2>
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
    important_threshold = data.get("important_score_threshold", DEFAULT_IMPORTANT_SCORE_THRESHOLD)
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
        lines.append(f"今天没有推荐分高于 {important_threshold} 的重点精读论文。")
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
