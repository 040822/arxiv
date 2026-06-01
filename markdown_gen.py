"""
Markdown 报告生成模块

本模块负责将数据库中的论文数据生成格式化的 Markdown 报告。
主要功能：
- 生成 README.md 总览报告（包含所有论文，按评级排序）
- 生成每日论文日报（按日期筛选，按评级分类）
- 生成单篇论文卡片（包含标题、作者、摘要、评级、标签等）
- 支持多种格式化函数（评级、作者、标签等）

报告结构：
- README.md: 项目总览，包含标签统计、最新论文列表
- daily/YYYY-MM-DD.md: 每日报告，包含当日论文，按评级分类

依赖：
- database.py: 提供论文数据查询接口
- config.py: 提供输出目录配置
"""

import os
import json
from datetime import datetime
from config import OUTPUT_DIR, DAILY_DIR, ARXIV_CATEGORIES
from database import get_papers_with_analysis, get_all_tags, get_paper_count, get_analyzed_count, get_daily_stats


def ensure_dirs():
    """
    确保输出目录存在
    
    创建 README 输出目录和每日报告目录。如果目录已存在则不做任何操作。
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(DAILY_DIR, exist_ok=True)


def format_rating(rating):
    """
    将数字评级转换为星号显示
    
    将 0-5 的数字评级转换为可视化的星号表示。
    使用实心星（★）表示已获得的评级，空心星（☆）表示未获得的评级。
    
    Args:
        rating (int 或 None): 数字评级，范围 0-5，None 表示未评级
        
    Returns:
        str: 格式化的评级字符串，如 "★★★☆☆" 表示 3 星评级
             "N/A" 表示未评级
    """
    if rating is None:
        return "N/A"
    return "★" * rating + "☆" * (5 - rating)


def format_authors(authors):
    """
    格式化作者列表
    
    将作者列表格式化为可读的字符串。
    如果作者超过 3 个，只显示前 3 个并添加 "et al." 和总人数。
    
    Args:
        authors (str 或 list): 作者列表，可以是 JSON 字符串或列表
        
    Returns:
        str: 格式化的作者字符串，如 "Author1, Author2, Author3 et al. (10 authors)"
    """
    # 如果是 JSON 字符串则解析为列表
    if isinstance(authors, str):
        authors = json.loads(authors)
    # 超过 3 个作者时截断显示
    if len(authors) > 3:
        return ", ".join(authors[:3]) + f" et al. ({len(authors)} authors)"
    return ", ".join(authors)


def format_tags(tags):
    """
    格式化标签列表
    
    将标签列表转换为 Markdown 内联代码格式。
    每个标签用反引号包裹，标签之间用空格分隔。
    
    Args:
        tags (str 或 list): 标签列表，可以是 JSON 字符串或列表
        
    Returns:
        str: 格式化的标签字符串，如 "`Tag1` `Tag2` `Tag3`"
             空列表返回空字符串
    """
    # 如果是 JSON 字符串则解析为列表
    if isinstance(tags, str):
        tags = json.loads(tags)
    if not tags:
        return ""
    return " ".join([f"`{t}`" for t in tags])


def generate_paper_card(paper, show_date=True):
    """
    生成单篇论文的 Markdown 卡片
    
    生成包含论文完整信息的 Markdown 格式卡片，包括：
    - 标题（带链接）
    - 元数据（日期、评级、分类）
    - 标签
    - 作者
    - 中文摘要
    - 评价
    - Q&A 分析（如果有）
    - 相关链接
    
    Args:
        paper (dict): 论文数据字典，包含以下字段：
            - title (str): 论文标题
            - url (str): arXiv 页面链接
            - published_date (str): 发布日期
            - rating (int): 评级 0-5
            - primary_category (str): 主分类
            - tags (list): 标签列表
            - authors (list): 作者列表
            - summary_cn (str): 中文摘要
            - value_comment (str): 价值评价
            - qa_analysis (str): Q&A 分析内容
            - pdf_url (str): PDF 下载链接
        show_date (bool): 是否显示发布日期，默认为 True
        
    Returns:
        str: 完整的 Markdown 格式论文卡片
    """
    lines = []
    
    # 格式化评级、标签和作者
    rating = format_rating(paper.get("rating"))
    tags = format_tags(paper.get("tags", []))
    authors = format_authors(paper.get("authors", []))

    # 标题行：带 arXiv 链接
    title_line = f"### [{paper['title']}]({paper['url']})"
    lines.append(title_line)
    lines.append("")

    # 元数据行：日期、评级、分类，使用管道符分隔
    meta_parts = []
    if show_date and paper.get("published_date"):
        meta_parts.append(f"📅 {paper['published_date']}")
    meta_parts.append(f"⭐ {rating}")
    if paper.get("primary_category"):
        meta_parts.append(f"📂 {paper['primary_category']}")
    lines.append(" | ".join(meta_parts))
    lines.append("")

    # 标签部分
    if tags:
        lines.append(f"**标签:** {tags}")
        lines.append("")

    # 作者部分
    lines.append(f"**作者:** {authors}")
    lines.append("")

    # 中文摘要部分（如果有）
    if paper.get("summary_cn"):
        lines.append(f"**中文摘要:** {paper['summary_cn']}")
        lines.append("")

    # 价值评价部分（如果有）
    if paper.get("value_comment"):
        lines.append(f"**评价:** {paper['value_comment']}")
        lines.append("")

    # Q&A 分析部分（如果有）
    if paper.get("qa_analysis"):
        lines.append(paper["qa_analysis"])
        lines.append("")

    # 相关链接部分：arXiv 链接和 PDF 链接
    links = [f"[arXiv]({paper['url']})"]
    if paper.get("pdf_url"):
        links.append(f"[PDF]({paper['pdf_url']})")
    lines.append(" | ".join(links))
    lines.append("")
    
    # 分隔线
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def generate_readme():
    """
    生成项目 README.md 总览报告
    
    生成包含以下内容的 README 文件：
    1. 项目标题和简介
    2. 统计信息（论文总数、已分析数）
    3. 目录导航
    4. 热门标签统计（前 30 个）
    5. 最新论文列表（按评级排序，最多 100 篇）
    6. 监控的 arXiv 分类列表
    
    Returns:
        str: 生成的 README.md 文件路径
    """
    ensure_dirs()

    # 获取统计数据
    total_papers = get_paper_count()
    analyzed_papers = get_analyzed_count()
    tags_with_counts = get_all_tags()
    papers = get_papers_with_analysis(limit=500)

    lines = []
    
    # 标题和简介
    lines.append("# 🤖 AI 论文数据库 - 具身智能 & 人工智能")
    lines.append("")
    lines.append("> 自动从 arXiv 抓取最新论文，AI 快速阅读分析，每日更新")
    lines.append("")
    
    # 统计信息
    lines.append(f"📊 **论文总数:** {total_papers} | **已分析:** {analyzed_papers}")
    lines.append("")
    lines.append(f"🕐 **最后更新:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # 目录导航
    lines.append("## 📋 目录")
    lines.append("")
    lines.append("- [热门标签](#-热门标签)")
    lines.append("- [最新论文（按评级排序）](#-最新论文按评级排序)")
    lines.append("- [监控分类](#-监控分类)")
    lines.append("")

    # 热门标签统计
    if tags_with_counts:
        lines.append("## 🏷️ 热门标签")
        lines.append("")
        tag_links = []
        # 只显示前 30 个标签
        for tag, count in tags_with_counts[:30]:
            tag_links.append(f"`{tag}` ({count})")
        lines.append(" | ".join(tag_links))
        lines.append("")

    # 最新论文列表（按评级排序）
    lines.append("## 📄 最新论文（按评级排序）")
    lines.append("")

    # 筛选有评级的论文并按评级降序排序
    rated_papers = [p for p in papers if p.get("rating") is not None]
    rated_papers.sort(key=lambda x: (-x.get("rating", 0), x.get("published_date", "")))

    # 最多显示 100 篇论文
    for paper in rated_papers[:100]:
        lines.append(generate_paper_card(paper))

    # 监控分类列表
    lines.append("## 📂 监控分类")
    lines.append("")
    for cat in ARXIV_CATEGORIES:
        lines.append(f"- `{cat}`")
    lines.append("")

    # 页脚说明
    lines.append("---")
    lines.append("")
    lines.append("*本项目由 AI 自动维护，每日定时从 arXiv 抓取并分析论文*")

    # 写入文件
    readme_path = os.path.join(OUTPUT_DIR, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return readme_path


def generate_daily_report(date_str=None):
    """
    生成指定日期的论文日报
    
    生成包含以下内容的每日报告：
    1. 日期标题
    2. 当日统计（论文数、已分析数、平均评级）
    3. 高价值论文（4-5 星）
    4. 值得关注论文（2-3 星）
    5. 其他论文（0-1 星）
    
    Args:
        date_str (str 或 None): 日期字符串，格式为 "YYYY-MM-DD"
                               如果为 None，则使用当前日期
                               
    Returns:
        str: 生成的日报文件路径
    """
    ensure_dirs()

    # 默认使用当前日期
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    # 获取当日论文和统计信息
    papers = get_papers_with_analysis(date=date_str, limit=200)
    stats = get_daily_stats(date_str)

    lines = []
    # 日报标题
    lines.append(f"# 📅 {date_str} 论文日报")
    lines.append("")

    # 统计信息
    if stats:
        total = stats.get("total", 0)
        analyzed = stats.get("analyzed", 0)
        avg_rating = stats.get("avg_rating", 0)
        lines.append(f"📊 今日论文: {total} 篇 | 已分析: {analyzed} 篇 | 平均评级: {avg_rating:.1f if avg_rating else 'N/A'}")
        lines.append("")

    # 论文列表（按评级分类）
    if not papers:
        lines.append("今日暂无论文。")
    else:
        # 筛选有评级的论文并按评级降序排序
        rated_papers = [p for p in papers if p.get("rating") is not None]
        rated_papers.sort(key=lambda x: -x.get("rating", 0))

        # 按评级分类
        high_rated = [p for p in rated_papers if p.get("rating", 0) >= 4]  # 高价值：4-5 星
        mid_rated = [p for p in rated_papers if 2 <= p.get("rating", 0) < 4]  # 关注：2-3 星
        low_rated = [p for p in rated_papers if p.get("rating", 0) < 2]  # 其他：0-1 星

        # 高价值论文部分
        if high_rated:
            lines.append("## ⭐⭐⭐⭐+ 高价值论文")
            lines.append("")
            for paper in high_rated:
                lines.append(generate_paper_card(paper, show_date=False))

        # 值得关注论文部分
        if mid_rated:
            lines.append("## ⭐⭐⭐ 值得关注")
            lines.append("")
            for paper in mid_rated:
                lines.append(generate_paper_card(paper, show_date=False))

        # 其他论文部分
        if low_rated:
            lines.append("## ⭐⭐ 其他论文")
            lines.append("")
            for paper in low_rated:
                lines.append(generate_paper_card(paper, show_date=False))

    # 写入文件
    report_path = os.path.join(DAILY_DIR, f"{date_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path


def generate_all_markdown():
    """
    生成所有 Markdown 报告
    
    同时生成 README.md 总览报告和当日的论文日报。
    这是报告生成的主入口函数，通常在每日定时任务中调用。
    
    Returns:
        tuple: (readme_path, daily_path) 两个报告文件的路径
    """
    readme_path = generate_readme()
    daily_path = generate_daily_report()
    return readme_path, daily_path