"""SQLite papers implementation."""

import html as html_module
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from .connection import get_connection


logger = logging.getLogger(__name__)


MAX_SEARCH_QUERY_LENGTH = 200


MAX_SEARCH_TERMS = 10


def normalize_search_terms(keyword):
    """规范化搜索词；过长查询直接拒绝，避免构造过大的 SQL。"""
    keyword = str(keyword or "").strip()
    if len(keyword) > MAX_SEARCH_QUERY_LENGTH:
        raise ValueError(f"搜索内容不能超过 {MAX_SEARCH_QUERY_LENGTH} 个字符")

    terms = []
    seen_terms = set()
    for term in keyword.split():
        normalized = term.casefold()
        if normalized and normalized not in seen_terms:
            terms.append(term)
            seen_terms.add(normalized)
    if len(terms) > MAX_SEARCH_TERMS:
        raise ValueError(f"搜索关键词不能超过 {MAX_SEARCH_TERMS} 个")
    return terms


def _basic_analysis_missing_condition(alias="a"):
    """Return SQL condition for missing or incomplete basic AI analysis fields."""
    return f"""(
        {alias}.id IS NULL
        OR {alias}.tags IS NULL OR {alias}.tags = '' OR {alias}.tags = '[]'
        OR {alias}.summary_cn IS NULL OR {alias}.summary_cn = ''
        OR {alias}.value_comment IS NULL OR {alias}.value_comment = ''
    )"""


def paper_exists(arxiv_id):
    """检查指定 arXiv ID 的论文是否已存在于数据库中。
    
    参数：
        arxiv_id (str): arXiv 论文编号，如 "2401.12345"
        
    返回：
        bool: 论文存在返回 True，否则返回 False
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM papers WHERE arxiv_id = ?", (arxiv_id,))
        exists = cursor.fetchone() is not None

    return exists


def insert_paper(paper_data):
    """插入新论文到数据库。
    
    参数：
        paper_data (dict): 论文数据字典，包含以下字段：
            - arxiv_id: arXiv 论文编号
            - title: 论文标题
            - authors: 作者列表（Python 列表，会自动转为 JSON）
            - abstract: 摘要
            - categories: 分类列表（Python 列表，会自动转为 JSON）
            - primary_category: 主分类
            - url: arXiv 页面链接
            - pdf_url: PDF 下载链接
            - published_date: 发布日期
            - updated_date: 更新日期
            
    返回：
        int or None: 成功返回论文 ID，失败返回 None
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO papers (arxiv_id, title, authors, abstract, categories,
                                  primary_category, url, pdf_url, published_date, updated_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                paper_data["arxiv_id"],
                paper_data["title"],
                json.dumps(paper_data["authors"], ensure_ascii=False),
                paper_data["abstract"],
                json.dumps(paper_data["categories"], ensure_ascii=False),
                paper_data["primary_category"],
                paper_data["url"],
                paper_data["pdf_url"],
                paper_data["published_date"],
                paper_data["updated_date"],
            ))
            paper_id = cursor.lastrowid

            return paper_id
        except sqlite3.IntegrityError:

            return None


def get_paper_by_arxiv_id(arxiv_id):
    """根据 arXiv ID 获取单篇论文的完整信息。
    
    参数：
        arxiv_id (str): arXiv 论文编号，如 "2401.12345"
        
    返回：
        dict or None: 论文数据字典，不存在则返回 None
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,))
        row = cursor.fetchone()

    return dict(row) if row else None


def get_papers_with_analysis(date=None, tag=None, min_rating=None, limit=100, offset=0, count_total=False):
    """获取论文列表及其分析结果，支持多条件筛选和分页。
    
    功能：
    1. LEFT JOIN 关联 analysis 表，获取分析结果
    2. 自动过滤隐藏论文（hidden=1）
    3. 支持按日期、标签、最低评级筛选
    4. 支持分页和总数统计
    5. 自动解析 JSON 字段（authors, categories, tags）
    
    参数：
        date (str, optional): 按发布日期筛选（YYYY-MM-DD）
        tag (str, optional): 按标签筛选（模糊匹配）
        min_rating (int, optional): 最低评级筛选
        limit (int): 每页数量，默认 100
        offset (int): 偏移量，默认 0
        count_total (bool): 是否返回总数，默认 False
        
    返回：
        list or tuple: 
            - count_total=False 时返回论文列表
            - count_total=True 时返回 (论文列表, 总数) 元组
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        # 构建基础查询：JOIN analysis 表，过滤隐藏论文
        base_query = """
            FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE (p.hidden IS NULL OR p.hidden = 0)
        """
        params = []

        # 按日期筛选
        if date:
            base_query += " AND p.published_date = ?"
            params.append(date)

        # 按最低评级筛选
        if min_rating is not None:
            base_query += " AND a.rating >= ?"
            params.append(min_rating)

        # 按标签筛选（JSON 字段模糊匹配）
        if tag:
            base_query += " AND a.tags LIKE ?"
            params.append(f"%{tag}%")

        # 可选：统计满足条件的总数（用于分页）
        total = 0
        if count_total:
            count_sql = "SELECT COUNT(*) " + base_query
            cursor.execute(count_sql, params)
            total = cursor.fetchone()[0]

        # 构建完整查询：选择字段、排序、分页
        query = """
            SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis,
                   a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                   a.recommendation_analyzed_at, a.analyzed_at
        """ + base_query
        query += " ORDER BY p.published_date DESC, a.rating DESC"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()


    # 解析 JSON 字段：将数据库中的 JSON 字符串转为 Python 对象
    results = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        results.append(r)

    if count_total:
        return results, total
    return results


def browse_papers(date=None, tag=None, min_rating=None, max_rating=None,
                  category=None, has_analysis=None, has_deep_analysis=None, hidden=None, limit=20, offset=0):
    """高级论文浏览接口，支持多维度筛选和分页。
    
    与 get_papers_with_analysis 相比，本函数提供更多筛选维度：
    - 按分类筛选
    - 按评级范围筛选（最低/最高）
    - 按是否有分析结果筛选
    - 按是否有深度分析（Q&A）筛选
    - 显示/隐藏论文切换
    
    参数：
        date (str, optional): 按发布日期筛选
        tag (str, optional): 按标签筛选（模糊匹配）
        min_rating (int, optional): 最低评级
        max_rating (int, optional): 最高评级
        category (str, optional): 按主分类筛选（如 "cs.RO"）
        has_analysis (str, optional): "yes" 仅已分析，"no" 仅未分析
        has_deep_analysis (str, optional): "yes" 仅有 Q&A，"no" 仅无 Q&A
        hidden (str, optional): "yes" 仅显示隐藏论文
        limit (int): 每页数量，默认 20
        offset (int): 偏移量，默认 0
        
    返回：
        tuple: (论文列表, 总数) 元组
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        # 构建基础查询
        base_query = """
            FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE 1=1
        """
        params = []

        # 隐藏论文筛选逻辑
        if hidden == "yes":
            base_query += " AND p.hidden = 1"
        else:
            base_query += " AND (p.hidden IS NULL OR p.hidden = 0)"

        # 按日期筛选
        if date:
            base_query += " AND p.published_date = ?"
            params.append(date)

        # 按分类筛选
        if category:
            base_query += " AND p.primary_category = ?"
            params.append(category)

        # 按评级范围筛选
        if min_rating is not None:
            base_query += " AND a.rating >= ?"
            params.append(min_rating)

        if max_rating is not None:
            base_query += " AND a.rating <= ?"
            params.append(max_rating)

        # 按标签筛选（JSON 字段模糊匹配）
        if tag:
            base_query += " AND a.tags LIKE ?"
            params.append(f"%{tag}%")

        # 按是否有分析结果筛选
        if has_analysis == "yes":
            base_query += f" AND NOT {_basic_analysis_missing_condition('a')}"
        elif has_analysis == "no":
            base_query += f" AND {_basic_analysis_missing_condition('a')}"

        # 按是否有深度分析（Q&A）筛选
        if has_deep_analysis == "yes":
            base_query += " AND a.qa_analysis IS NOT NULL AND a.qa_analysis != ''"
        elif has_deep_analysis == "no":
            base_query += " AND (a.qa_analysis IS NULL OR a.qa_analysis = '')"

        # 统计满足条件的总数
        count_query = "SELECT COUNT(*) as cnt " + base_query
        cursor.execute(count_query, params)
        total = cursor.fetchone()["cnt"]

        # 查询数据：选择字段、排序、分页
        data_query = """
            SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis,
                   a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                   a.recommendation_analyzed_at, a.analyzed_at
        """ + base_query + " ORDER BY p.published_date DESC, a.rating DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(data_query, params)
        rows = cursor.fetchall()


    # 解析 JSON 字段
    results = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        results.append(r)

    return results, total


def get_all_categories():
    """获取所有论文分类及其数量统计。
    
    用于分类浏览页面的侧边栏筛选器。
    
    返回：
        list: 元组列表 [(分类名, 数量), ...]，按数量降序排列
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT primary_category, COUNT(*) as cnt
            FROM papers
            GROUP BY primary_category
            ORDER BY cnt DESC
        """)
        rows = cursor.fetchall()

    return [(row["primary_category"], row["cnt"]) for row in rows]


def get_all_dates():
    """获取所有发布日期及其论文数量。
    
    用于日期筛选器和统计图表。
    
    返回：
        list: 元组列表 [(日期, 数量), ...]，按日期降序排列
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT published_date, COUNT(*) as cnt
            FROM papers
            GROUP BY published_date
            ORDER BY published_date DESC
        """)
        rows = cursor.fetchall()

    return [(row["published_date"], row["cnt"]) for row in rows]


def get_earliest_date(category=None):
    """获取数据库中最早的论文发布日期。
    
    参数：
        category (str, optional): 指定分类，None 表示所有分类
        
    返回：
        str or None: 最早日期（YYYY-MM-DD），无数据返回 None
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        if category:
            cursor.execute("""
                SELECT MIN(published_date) as earliest
                FROM papers
                WHERE primary_category = ?
            """, (category,))
        else:
            cursor.execute("SELECT MIN(published_date) as earliest FROM papers")
        row = cursor.fetchone()

    return row["earliest"] if row else None


def get_date_range_for_category(category):
    """获取指定分类的日期范围和论文数量。
    
    用于分类浏览页面显示时间跨度信息。
    
    参数：
        category (str): arXiv 分类，如 "cs.RO"
        
    返回：
        dict or None: 包含 earliest（最早日期）、latest（最新日期）、count（论文数），
                      无数据返回 None
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT MIN(published_date) as earliest, MAX(published_date) as latest, COUNT(*) as cnt
            FROM papers
            WHERE primary_category = ?
        """, (category,))
        row = cursor.fetchone()

    if row and row["earliest"]:
        return {"earliest": row["earliest"], "latest": row["latest"], "count": row["cnt"]}
    return None


def get_all_tags():
    """获取所有标签及其出现次数。
    
    遍历 analysis 表中的 tags JSON 字段，统计每个标签的出现次数。
    用于标签云和筛选器。
    
    返回：
        list: 元组列表 [(标签名, 次数), ...]，按次数降序排列
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT tags FROM analysis WHERE tags IS NOT NULL")
        rows = cursor.fetchall()


    # 遍历所有标签 JSON，统计每个标签的出现次数
    tag_counts = {}
    for row in rows:
        tags = json.loads(row["tags"])
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return sorted(tag_counts.items(), key=lambda x: -x[1])


def get_paper_count():
    """获取数据库中的论文总数。
    
    返回：
        int: 论文总数
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM papers")
        count = cursor.fetchone()["cnt"]

    return count


def get_analyzed_count():
    """获取已分析的论文数量。
    
    返回：
        int: 已完成基础分析的论文数
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT COUNT(*) as cnt
            FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE NOT {_basic_analysis_missing_condition("a")}
        """)
        count = cursor.fetchone()["cnt"]

    return count


def get_unanalyzed_count():
    """获取未分析的论文数量。
    
    通过 LEFT JOIN 找出没有基础分析结果或基础分析字段不完整的论文。
    
    返回：
        int: 未分析论文数
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT COUNT(*) as cnt FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE {_basic_analysis_missing_condition("a")}
        """)
        count = cursor.fetchone()["cnt"]

    return count


def get_unanalyzed_papers(limit=100):
    """获取未分析的论文列表。
    
    用于批量分析任务，按发布日期降序获取待分析论文。
    
    参数：
        limit (int): 最大返回数量，默认 100
        
    返回：
        list: 论文数据字典列表
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT p.* FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE {_basic_analysis_missing_condition("a")}
            ORDER BY p.published_date DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()


    # 解析 JSON 字段
    results = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        results.append(r)
    return results


def search_papers(keyword, limit=50):
    """搜索论文，支持多关键词和 arXiv ID。
    
    搜索逻辑：
    1. 如果关键词匹配 arXiv ID 格式（如 2401.12345），则精确查找
    2. 否则按空白拆分关键词；每个词都必须在任一搜索字段中命中
    3. 按标题、标签、中文摘要、英文摘要、Q&A 的字段权重计算相关分
    
    参数：
        keyword (str): 搜索关键词或 arXiv ID
        limit (int): 最大返回数量，默认 50
        
    返回：
        list: 匹配的论文列表
    """
    # 检查关键词是否为 arXiv ID 格式
    terms = normalize_search_terms(keyword)
    keyword = str(keyword or "").strip()
    arxiv_match = re.search(r'(\d{4}\.\d{4,5})(v\d+)?', keyword)
    arxiv_id = arxiv_match.group(1) if arxiv_match else None

    if not arxiv_id and not terms:
        return []

    with get_connection() as conn:
        cursor = conn.cursor()

        if arxiv_id:
            # 精确匹配 arXiv ID
            cursor.execute("""
                SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis,
                       a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                       a.recommendation_analyzed_at
                FROM papers p
                LEFT JOIN analysis a ON p.id = a.paper_id
                WHERE p.arxiv_id = ?
                  AND (p.hidden IS NULL OR p.hidden = 0)
                LIMIT ?
            """, (arxiv_id, limit))
        else:
            search_fields = (
                ("p.title", 5),
                ("a.tags", 4),
                ("a.summary_cn", 3),
                ("p.abstract", 2),
                ("a.qa_analysis", 1),
            )
            score_parts = []
            score_params = []
            required_groups = []
            where_params = []

            for term in terms:
                escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                matches = []
                for field, weight in search_fields:
                    condition = f"COALESCE({field}, '') LIKE ? ESCAPE '\\'"
                    matches.append(condition)
                    score_parts.append(f"CASE WHEN {condition} THEN {weight} ELSE 0 END")
                    score_params.append(pattern)
                    where_params.append(pattern)
                required_groups.append("(" + " OR ".join(matches) + ")")

            relevance_sql = " + ".join(score_parts) if score_parts else "0"
            required_sql = " AND ".join(required_groups) if required_groups else "0"
            query = f"""
                SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis,
                       a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                       a.recommendation_analyzed_at,
                       ({relevance_sql}) AS search_score
                FROM papers p
                LEFT JOIN analysis a ON p.id = a.paper_id
                WHERE (p.hidden IS NULL OR p.hidden = 0)
                AND {required_sql}
                ORDER BY search_score DESC, a.rating DESC, p.published_date DESC, p.arxiv_id
                LIMIT ?
            """
            cursor.execute(query, score_params + where_params + [limit])

        rows = cursor.fetchall()


    # 解析 JSON 字段
    results = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        results.append(r)
    return results


def hide_paper(arxiv_id):
    """隐藏论文，使其在默认列表中不显示。
    
    参数：
        arxiv_id (str): arXiv 论文编号
        
    返回：
        bool: 操作成功返回 True，论文不存在返回 False
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE papers SET hidden = 1 WHERE arxiv_id = ?", (arxiv_id,))
        affected = cursor.rowcount

    return affected > 0


def unhide_paper(arxiv_id):
    """取消论文隐藏，恢复其在列表中的显示。
    
    参数：
        arxiv_id (str): arXiv 论文编号
        
    返回：
        bool: 操作成功返回 True，论文不存在返回 False
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE papers SET hidden = 0 WHERE arxiv_id = ?", (arxiv_id,))
        affected = cursor.rowcount

    return affected > 0


def delete_paper(arxiv_id):
    """永久删除论文及其关联的分析结果。
    
    由于外键 CASCADE DELETE，删除 papers 记录会自动删除关联的 analysis 记录。
    
    参数：
        arxiv_id (str): arXiv 论文编号
        
    返回：
        bool: 操作成功返回 True，论文不存在返回 False
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM papers WHERE arxiv_id = ?", (arxiv_id,))
        affected = cursor.rowcount

    return affected > 0


def batch_delete_papers(arxiv_ids):
    """批量删除多篇论文及其关联的分析结果。
    
    参数：
        arxiv_ids (list): arXiv 论文编号列表
        
    返回：
        int: 实际删除的论文数量
    """
    if not arxiv_ids:
        return 0
    with get_connection() as conn:
        cursor = conn.cursor()
        # 使用参数化 IN 子句批量删除
        placeholders = ",".join(["?"] * len(arxiv_ids))
        cursor.execute(f"DELETE FROM papers WHERE arxiv_id IN ({placeholders})", arxiv_ids)
        affected = cursor.rowcount

    return affected


def batch_hide_papers(arxiv_ids):
    """批量隐藏多篇论文。
    
    参数：
        arxiv_ids (list): arXiv 论文编号列表
        
    返回：
        int: 实际隐藏的论文数量
    """
    if not arxiv_ids:
        return 0
    with get_connection() as conn:
        cursor = conn.cursor()
        # 使用参数化 IN 子句批量更新
        placeholders = ",".join(["?"] * len(arxiv_ids))
        cursor.execute(f"UPDATE papers SET hidden = 1 WHERE arxiv_id IN ({placeholders})", arxiv_ids)
        affected = cursor.rowcount

    return affected


def get_unanalyzed_papers_by_ids(arxiv_ids):
    """获取指定 arXiv ID 列表中未分析的论文。
    
    用于批量分析时，筛选出需要分析的论文（排除已有分析结果的）。
    
    参数：
        arxiv_ids (list): arXiv 论文编号列表
        
    返回：
        list: 未分析的论文数据字典列表
    """
    if not arxiv_ids:
        return []
    with get_connection() as conn:
        cursor = conn.cursor()
        # 使用 IN 子句筛选，LEFT JOIN 找出无基础分析或基础分析不完整的论文
        placeholders = ",".join(["?"] * len(arxiv_ids))
        cursor.execute(f"""
            SELECT p.* FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE p.arxiv_id IN ({placeholders}) AND {_basic_analysis_missing_condition("a")}
        """, arxiv_ids)
        rows = cursor.fetchall()

    # 解析 JSON 字段
    results = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        results.append(r)
    return results
