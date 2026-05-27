import os
import json
from datetime import datetime
from config import OUTPUT_DIR, DAILY_DIR, ARXIV_CATEGORIES
from database import get_papers_with_analysis, get_all_tags, get_paper_count, get_analyzed_count, get_daily_stats


def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(DAILY_DIR, exist_ok=True)


def format_rating(rating):
    if rating is None:
        return "N/A"
    return "★" * rating + "☆" * (5 - rating)


def format_authors(authors):
    if isinstance(authors, str):
        authors = json.loads(authors)
    if len(authors) > 3:
        return ", ".join(authors[:3]) + f" et al. ({len(authors)} authors)"
    return ", ".join(authors)


def format_tags(tags):
    if isinstance(tags, str):
        tags = json.loads(tags)
    if not tags:
        return ""
    return " ".join([f"`{t}`" for t in tags])


def generate_paper_card(paper, show_date=True):
    lines = []
    rating = format_rating(paper.get("rating"))
    tags = format_tags(paper.get("tags", []))
    authors = format_authors(paper.get("authors", []))

    title_line = f"### [{paper['title']}]({paper['url']})"
    lines.append(title_line)
    lines.append("")

    meta_parts = []
    if show_date and paper.get("published_date"):
        meta_parts.append(f"📅 {paper['published_date']}")
    meta_parts.append(f"⭐ {rating}")
    if paper.get("primary_category"):
        meta_parts.append(f"📂 {paper['primary_category']}")
    lines.append(" | ".join(meta_parts))
    lines.append("")

    if tags:
        lines.append(f"**标签:** {tags}")
        lines.append("")

    lines.append(f"**作者:** {authors}")
    lines.append("")

    if paper.get("summary_cn"):
        lines.append(f"**中文摘要:** {paper['summary_cn']}")
        lines.append("")

    if paper.get("value_comment"):
        lines.append(f"**评价:** {paper['value_comment']}")
        lines.append("")

    if paper.get("qa_analysis"):
        lines.append(paper["qa_analysis"])
        lines.append("")

    links = [f"[arXiv]({paper['url']})"]
    if paper.get("pdf_url"):
        links.append(f"[PDF]({paper['pdf_url']})")
    lines.append(" | ".join(links))
    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def generate_readme():
    ensure_dirs()

    total_papers = get_paper_count()
    analyzed_papers = get_analyzed_count()
    tags_with_counts = get_all_tags()
    papers = get_papers_with_analysis(limit=500)

    lines = []
    lines.append("# 🤖 AI 论文数据库 - 具身智能 & 人工智能")
    lines.append("")
    lines.append("> 自动从 arXiv 抓取最新论文，AI 快速阅读分析，每日更新")
    lines.append("")
    lines.append(f"📊 **论文总数:** {total_papers} | **已分析:** {analyzed_papers}")
    lines.append("")
    lines.append(f"🕐 **最后更新:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    lines.append("## 📋 目录")
    lines.append("")
    lines.append("- [热门标签](#-热门标签)")
    lines.append("- [最新论文（按评级排序）](#-最新论文按评级排序)")
    lines.append("- [监控分类](#-监控分类)")
    lines.append("")

    if tags_with_counts:
        lines.append("## 🏷️ 热门标签")
        lines.append("")
        tag_links = []
        for tag, count in tags_with_counts[:30]:
            tag_links.append(f"`{tag}` ({count})")
        lines.append(" | ".join(tag_links))
        lines.append("")

    lines.append("## 📄 最新论文（按评级排序）")
    lines.append("")

    rated_papers = [p for p in papers if p.get("rating") is not None]
    rated_papers.sort(key=lambda x: (-x.get("rating", 0), x.get("published_date", "")))

    for paper in rated_papers[:100]:
        lines.append(generate_paper_card(paper))

    lines.append("## 📂 监控分类")
    lines.append("")
    for cat in ARXIV_CATEGORIES:
        lines.append(f"- `{cat}`")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*本项目由 AI 自动维护，每日定时从 arXiv 抓取并分析论文*")

    readme_path = os.path.join(OUTPUT_DIR, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return readme_path


def generate_daily_report(date_str=None):
    ensure_dirs()

    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    papers = get_papers_with_analysis(date=date_str, limit=200)
    stats = get_daily_stats(date_str)

    lines = []
    lines.append(f"# 📅 {date_str} 论文日报")
    lines.append("")

    if stats:
        total = stats.get("total", 0)
        analyzed = stats.get("analyzed", 0)
        avg_rating = stats.get("avg_rating", 0)
        lines.append(f"📊 今日论文: {total} 篇 | 已分析: {analyzed} 篇 | 平均评级: {avg_rating:.1f if avg_rating else 'N/A'}")
        lines.append("")

    if not papers:
        lines.append("今日暂无论文。")
    else:
        rated_papers = [p for p in papers if p.get("rating") is not None]
        rated_papers.sort(key=lambda x: -x.get("rating", 0))

        high_rated = [p for p in rated_papers if p.get("rating", 0) >= 4]
        mid_rated = [p for p in rated_papers if 2 <= p.get("rating", 0) < 4]
        low_rated = [p for p in rated_papers if p.get("rating", 0) < 2]

        if high_rated:
            lines.append("## ⭐⭐⭐⭐+ 高价值论文")
            lines.append("")
            for paper in high_rated:
                lines.append(generate_paper_card(paper, show_date=False))

        if mid_rated:
            lines.append("## ⭐⭐⭐ 值得关注")
            lines.append("")
            for paper in mid_rated:
                lines.append(generate_paper_card(paper, show_date=False))

        if low_rated:
            lines.append("## ⭐⭐ 其他论文")
            lines.append("")
            for paper in low_rated:
                lines.append(generate_paper_card(paper, show_date=False))

    report_path = os.path.join(DAILY_DIR, f"{date_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path


def generate_all_markdown():
    readme_path = generate_readme()
    daily_path = generate_daily_report()
    return readme_path, daily_path
