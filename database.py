"""
database.py - SQLite 数据库操作模块

本模块负责所有数据库相关的操作，包括：
- 数据库初始化和表结构管理
- 论文的增删改查（CRUD）
- 分析结果的存储和查询
- 任务日志的记录和查询
- 报告的生成和存储
- 阅读清单的管理

数据库结构：
- papers: 论文基本信息
- analysis: AI 分析结果（与 papers 1:1 关联）
- task_logs: 定时任务和手动操作的日志
- reports: 每日 Web 报告
- reading_list: 用户阅读清单
- ai_usage_logs: LLM 调用 token 用量账本

依赖：
- config.py: 数据库路径配置
- sqlite3: Python 内置 SQLite 模块
"""

import sqlite3
import json
import os
import logging
import html as html_module
from datetime import datetime, timedelta, timezone
from config import DB_PATH, DB_DIR

logger = logging.getLogger(__name__)


def _basic_analysis_missing_condition(alias="a"):
    """Return SQL condition for missing or incomplete basic AI analysis fields."""
    return f"""(
        {alias}.id IS NULL
        OR {alias}.tags IS NULL OR {alias}.tags = '' OR {alias}.tags = '[]'
        OR {alias}.summary_cn IS NULL OR {alias}.summary_cn = ''
        OR {alias}.value_comment IS NULL OR {alias}.value_comment = ''
    )"""


def get_connection():
    """获取数据库连接。
    
    配置：
    - WAL 模式：支持读写并发，提升 Web 服务性能
    - 外键约束：启用级联删除，保证数据完整性
    - Row 工厂：返回字典风格的行对象
    
    返回：
        sqlite3.Connection: 配置好的数据库连接
    """
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 写前日志模式，提升并发性能
    conn.execute("PRAGMA foreign_keys=ON")    # 启用外键约束
    return conn


def init_db():
    """初始化数据库，创建所有表和索引。
    
    功能：
    1. 创建核心业务表、学习记录表和用量日志表（如果不存在）
    2. 创建索引优化查询性能
    3. 执行数据库迁移（添加新字段）
    
    注意：此函数在应用启动时调用，可重复调用不会破坏已有数据。
    """
    conn = get_connection()
    cursor = conn.cursor()

    # ==================== papers 表：论文基本信息 ====================
    # 存储从 arXiv 抓取的论文元数据
    # authors 和 categories 使用 JSON 数组格式存储
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arxiv_id TEXT UNIQUE NOT NULL,    -- arXiv 论文编号，如 "2401.12345"
            title TEXT NOT NULL,               -- 论文标题
            authors TEXT NOT NULL,             -- 作者列表（JSON 数组）
            abstract TEXT NOT NULL,            -- 论文摘要
            categories TEXT NOT NULL,          -- 分类列表（JSON 数组，如 ["cs.RO", "cs.AI"]）
            primary_category TEXT,             -- 主分类，如 "cs.RO"
            url TEXT,                          -- arXiv 页面链接
            pdf_url TEXT,                      -- PDF 下载链接
            published_date TEXT,               -- 发布日期（YYYY-MM-DD）
            updated_date TEXT,                 -- 更新日期
            created_at TEXT DEFAULT CURRENT_TIMESTAMP  -- 记录创建时间
        )
    """)

    # ==================== analysis 表：AI 分析结果 ====================
    # 存储 AI 对论文的分析结果，与 papers 表 1:1 关联
    # 使用 CASCADE DELETE，删除论文时自动删除分析结果
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id INTEGER NOT NULL,         -- 关联的论文 ID
            tags TEXT,                         -- 标签（JSON 数组，如 ["VLA", "World Model"]）
            summary_cn TEXT,                   -- 摘要中文翻译
            summary_en TEXT,                   -- 英文摘要（当前未使用）
            rating INTEGER DEFAULT 0,          -- AI 初评 + 用户可手动修正（0-5 星）
            legacy_ai_rating INTEGER,          -- 历史 AI 自动评级备份
            rating_restored_from_legacy INTEGER DEFAULT 0, -- 是否已从历史 AI 评级恢复
            value_comment TEXT,                -- 价值评价（2-3 句话）
            qa_analysis TEXT,                  -- Q&A 深度阅读（Markdown 格式）
            recommendation_score INTEGER,      -- 个性化推荐分（0-100）
            recommendation_reason TEXT,        -- 推荐理由
            recommendation_interest_hash TEXT, -- 对应研究兴趣的哈希
            recommendation_analyzed_at TEXT,   -- 推荐评分时间
            analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP,  -- 分析时间
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        )
    """)

    # ==================== 创建索引 ====================
    # 索引优化常见查询场景
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_category ON papers(primary_category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_paper_id ON analysis(paper_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_rating ON analysis(rating)")

    # ==================== 数据库迁移 ====================
    # 使用 PRAGMA table_info 检查列是否存在，实现安全的字段添加
    # 这是项目的迁移模式，新字段都通过这种方式添加

    # 迁移：添加 qa_analysis 字段（Q&A 深度阅读）
    cursor.execute("PRAGMA table_info(analysis)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "qa_analysis" not in columns:
        cursor.execute("ALTER TABLE analysis ADD COLUMN qa_analysis TEXT")
    if "legacy_ai_rating" not in columns:
        cursor.execute("ALTER TABLE analysis ADD COLUMN legacy_ai_rating INTEGER")
        cursor.execute("UPDATE analysis SET legacy_ai_rating = rating")
    if "rating_restored_from_legacy" not in columns:
        cursor.execute("ALTER TABLE analysis ADD COLUMN rating_restored_from_legacy INTEGER DEFAULT 0")
    cursor.execute("""
        UPDATE analysis
        SET rating = legacy_ai_rating,
            rating_restored_from_legacy = 1
        WHERE legacy_ai_rating IS NOT NULL
          AND COALESCE(rating_restored_from_legacy, 0) = 0
    """)
    if "recommendation_score" not in columns:
        cursor.execute("ALTER TABLE analysis ADD COLUMN recommendation_score INTEGER")
    if "recommendation_reason" not in columns:
        cursor.execute("ALTER TABLE analysis ADD COLUMN recommendation_reason TEXT")
    if "recommendation_interest_hash" not in columns:
        cursor.execute("ALTER TABLE analysis ADD COLUMN recommendation_interest_hash TEXT")
    if "recommendation_analyzed_at" not in columns:
        cursor.execute("ALTER TABLE analysis ADD COLUMN recommendation_analyzed_at TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_recommendation_score ON analysis(recommendation_score)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_recommendation_hash ON analysis(recommendation_interest_hash)")

    # 迁移：添加 hidden 字段（论文隐藏标记）
    cursor.execute("PRAGMA table_info(papers)")
    paper_columns = [row["name"] for row in cursor.fetchall()]
    if "hidden" not in paper_columns:
        cursor.execute("ALTER TABLE papers ADD COLUMN hidden INTEGER DEFAULT 0")

    # ==================== task_logs 表：任务执行日志 ====================
    # 记录定时任务和手动操作的执行情况
    # 用于任务统计、问题排查和执行历史查看
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name TEXT NOT NULL,           -- 任务名称：daily_pipeline/fetch/analyze/generate/run
            status TEXT NOT NULL DEFAULT 'running',  -- 状态：running/success/error
            message TEXT,                      -- 人类可读的消息
            detail TEXT,                       -- 技术细节
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,  -- 开始时间
            finished_at TEXT,                  -- 结束时间
            duration_sec REAL                  -- 执行时长（秒）
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_name ON task_logs(task_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_status ON task_logs(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_started ON task_logs(started_at)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_log_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_log_id INTEGER NOT NULL,
            step_key TEXT NOT NULL,
            step_name TEXT NOT NULL,
            position INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            message TEXT,
            detail TEXT,
            started_at TEXT,
            finished_at TEXT,
            duration_sec REAL,
            UNIQUE(task_log_id, step_key),
            FOREIGN KEY (task_log_id) REFERENCES task_logs(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_log_steps_log ON task_log_steps(task_log_id, position)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_log_steps_status ON task_log_steps(status)")

    # ==================== reports 表：每日 Web 报告 ====================
    # 存储生成的 HTML 格式报告，按日期唯一
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT UNIQUE NOT NULL,  -- 报告日期（YYYY-MM-DD），唯一
            content TEXT NOT NULL,             -- HTML 格式的报告内容
            paper_count INTEGER DEFAULT 0,     -- 论文总数
            analyzed_count INTEGER DEFAULT 0,  -- 已分析数
            avg_rating REAL DEFAULT 0,         -- 平均评级
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(report_date)")

    # ==================== reading_list 表：用户阅读清单 ====================
    # 用户收藏的待读论文，支持已读/未读状态管理
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reading_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id INTEGER NOT NULL,         -- 关联的论文 ID
            status TEXT DEFAULT 'unread',      -- 状态：unread/read
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,  -- 添加时间
            completed_at TEXT,                 -- 标记已读的时间
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reading_list_paper ON reading_list(paper_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reading_list_status ON reading_list(status)")

    # ==================== paper_chat_messages 表：论文自由讨论历史 ====================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_chat_paper ON paper_chat_messages(paper_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_chat_created ON paper_chat_messages(created_at)")

    # ==================== paper_quiz_sessions 表：论文主动问答/苏格拉底会话 ====================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_quiz_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_quiz_sessions_paper ON paper_quiz_sessions(paper_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_quiz_sessions_mode ON paper_quiz_sessions(mode)")

    # ==================== paper_quiz_questions 表：练习题与参考要点 ====================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_quiz_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            question TEXT NOT NULL,
            expected_points TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES paper_quiz_sessions(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_quiz_questions_session ON paper_quiz_questions(session_id)")

    # ==================== paper_quiz_attempts 表：用户答案与模型反馈 ====================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            answer_text TEXT NOT NULL,
            score INTEGER DEFAULT 0,
            feedback_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES paper_quiz_questions(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_quiz_attempts_question ON paper_quiz_attempts(question_id)")

    # ==================== ai_usage_logs 表：LLM 调用用量账本 ====================
    # 只记录 token 用量和路由信息，不内置价格表，避免价格变化造成误导
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_key TEXT NOT NULL,
            provider_key TEXT,
            provider_name TEXT,
            model TEXT,
            paper_id INTEGER,
            arxiv_id TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cached_tokens INTEGER DEFAULT 0,
            cache_miss_tokens INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("PRAGMA table_info(ai_usage_logs)")
    usage_columns = [row["name"] for row in cursor.fetchall()]
    if "cache_miss_tokens" not in usage_columns:
        cursor.execute("ALTER TABLE ai_usage_logs ADD COLUMN cache_miss_tokens INTEGER DEFAULT 0")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_task ON ai_usage_logs(task_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_created ON ai_usage_logs(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_model ON ai_usage_logs(model)")

    conn.commit()
    conn.close()


# ==================== 论文基本操作 ====================


def paper_exists(arxiv_id):
    """检查指定 arXiv ID 的论文是否已存在于数据库中。
    
    参数：
        arxiv_id (str): arXiv 论文编号，如 "2401.12345"
        
    返回：
        bool: 论文存在返回 True，否则返回 False
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM papers WHERE arxiv_id = ?", (arxiv_id,))
    exists = cursor.fetchone() is not None
    conn.close()
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
    conn = get_connection()
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
        conn.commit()
        paper_id = cursor.lastrowid
        conn.close()
        return paper_id
    except sqlite3.IntegrityError:
        conn.close()
        return None


def insert_analysis(paper_id, analysis_data):
    """插入 AI 分析结果到数据库。
    
    功能：
    1. 检查该论文是否已有分析结果（防重复）
    2. 将分析数据插入 analysis 表
    3. tags 字段自动从 Python 列表转为 JSON 字符串
    
    参数：
        paper_id (int): 关联的论文 ID
        analysis_data (dict): 分析数据字典，包含：
            - tags: 标签列表（如 ["VLA", "World Model"]）
            - summary_cn: 中文摘要
            - summary_en: 英文摘要
            - rating: AI 初评（0-5），用户可在详情页手动修正
            - value_comment: 价值评价
            - qa_analysis: Q&A 深度阅读（可选，默认为空字符串）
            - recommendation_score/reason/interest_hash: 个性化推荐字段（可选）
            
    返回：
        int or None: 成功返回分析 ID，已存在则返回 None
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 检查是否已存在分析结果，避免重复插入
    cursor.execute("SELECT id FROM analysis WHERE paper_id = ?", (paper_id,))
    if cursor.fetchone():
        conn.close()
        logger.debug(f"Analysis already exists for paper_id={paper_id}, skipping.")
        return None

    cursor.execute("""
        INSERT INTO analysis (
            paper_id, tags, summary_cn, summary_en, rating, value_comment, qa_analysis,
            recommendation_score, recommendation_reason, recommendation_interest_hash, recommendation_analyzed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        paper_id,
        json.dumps(analysis_data["tags"], ensure_ascii=False),
        analysis_data["summary_cn"],
        analysis_data["summary_en"],
        int(analysis_data.get("rating", 0)),
        analysis_data["value_comment"],
        analysis_data.get("qa_analysis", ""),
        analysis_data.get("recommendation_score"),
        analysis_data.get("recommendation_reason", ""),
        analysis_data.get("recommendation_interest_hash", ""),
        analysis_data.get("recommendation_analyzed_at"),
    ))
    conn.commit()
    analysis_id = cursor.lastrowid
    conn.close()
    return analysis_id


def get_paper_by_arxiv_id(arxiv_id):
    """根据 arXiv ID 获取单篇论文的完整信息。
    
    参数：
        arxiv_id (str): arXiv 论文编号，如 "2401.12345"
        
    返回：
        dict or None: 论文数据字典，不存在则返回 None
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,))
    row = cursor.fetchone()
    conn.close()
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
    conn = get_connection()
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
    conn.close()

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
    conn = get_connection()
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
    conn.close()

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
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT primary_category, COUNT(*) as cnt
        FROM papers
        GROUP BY primary_category
        ORDER BY cnt DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [(row["primary_category"], row["cnt"]) for row in rows]


def get_all_dates():
    """获取所有发布日期及其论文数量。
    
    用于日期筛选器和统计图表。
    
    返回：
        list: 元组列表 [(日期, 数量), ...]，按日期降序排列
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT published_date, COUNT(*) as cnt
        FROM papers
        GROUP BY published_date
        ORDER BY published_date DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [(row["published_date"], row["cnt"]) for row in rows]


def get_earliest_date(category=None):
    """获取数据库中最早的论文发布日期。
    
    参数：
        category (str, optional): 指定分类，None 表示所有分类
        
    返回：
        str or None: 最早日期（YYYY-MM-DD），无数据返回 None
    """
    conn = get_connection()
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
    conn.close()
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
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MIN(published_date) as earliest, MAX(published_date) as latest, COUNT(*) as cnt
        FROM papers
        WHERE primary_category = ?
    """, (category,))
    row = cursor.fetchone()
    conn.close()
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
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT tags FROM analysis WHERE tags IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()

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
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM papers")
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_analyzed_count():
    """获取已分析的论文数量。
    
    返回：
        int: 已完成基础分析的论文数
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT COUNT(*) as cnt
        FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE NOT {_basic_analysis_missing_condition("a")}
    """)
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_unanalyzed_count():
    """获取未分析的论文数量。
    
    通过 LEFT JOIN 找出没有基础分析结果或基础分析字段不完整的论文。
    
    返回：
        int: 未分析论文数
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT COUNT(*) as cnt FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE {_basic_analysis_missing_condition("a")}
    """)
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_unanalyzed_papers(limit=100):
    """获取未分析的论文列表。
    
    用于批量分析任务，按发布日期降序获取待分析论文。
    
    参数：
        limit (int): 最大返回数量，默认 100
        
    返回：
        list: 论文数据字典列表
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT p.* FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE {_basic_analysis_missing_condition("a")}
        ORDER BY p.published_date DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()

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
    """搜索论文，支持关键词和 arXiv ID。
    
    搜索逻辑：
    1. 如果关键词匹配 arXiv ID 格式（如 2401.12345），则精确查找
    2. 否则在标题、摘要、中文摘要、标签、Q&A 中进行模糊搜索
    
    参数：
        keyword (str): 搜索关键词或 arXiv ID
        limit (int): 最大返回数量，默认 50
        
    返回：
        list: 匹配的论文列表
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 检查关键词是否为 arXiv ID 格式
    import re
    arxiv_match = re.search(r'(\d{4}\.\d{4,5})(v\d+)?', keyword.strip())
    arxiv_id = arxiv_match.group(1) if arxiv_match else None

    if arxiv_id:
        # 精确匹配 arXiv ID
        cursor.execute("""
            SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis,
                   a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                   a.recommendation_analyzed_at
            FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE p.arxiv_id = ?
            LIMIT ?
        """, (arxiv_id, limit))
    else:
        # 多字段模糊搜索：标题、摘要、中文摘要、标签、Q&A
        cursor.execute("""
            SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis,
                   a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                   a.recommendation_analyzed_at
            FROM papers p
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE (p.hidden IS NULL OR p.hidden = 0)
            AND (p.title LIKE ? OR p.abstract LIKE ? OR a.summary_cn LIKE ? OR a.tags LIKE ? OR a.qa_analysis LIKE ?)
            ORDER BY a.rating DESC, p.published_date DESC
            LIMIT ?
        """, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit))

    rows = cursor.fetchall()
    conn.close()

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


def get_daily_stats(date):
    """获取指定日期的论文统计信息。
    
    参数：
        date (str): 日期（YYYY-MM-DD）
        
    返回：
        dict or None: 包含 total（总数）、analyzed（已分析数）、avg_rating（平均评级），
                      无数据返回 None
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN NOT {_basic_analysis_missing_condition("a")} THEN 1 ELSE 0 END) as analyzed,
               AVG(a.rating) as avg_rating
        FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE p.published_date = ?
    """, (date,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ==================== 分析结果操作 ====================


def get_analysis_by_paper_id(paper_id):
    """根据论文 ID 获取分析结果。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        dict or None: 分析数据字典（tags 已解析为列表），不存在返回 None
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM analysis WHERE paper_id = ?", (paper_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        r = dict(row)
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        return r
    return None


def update_analysis(paper_id, data):
    """更新或插入论文的分析结果。
    
    逻辑：
    1. 如果该论文已有分析记录，则更新指定字段（部分更新）
    2. 如果没有记录，则插入新记录
    
    参数：
        paper_id (int): 论文 ID
        data (dict): 要更新的数据，可包含以下字段：
            - rating: AI 初评或用户手动修正（0-5）
            - tags: 标签列表
            - summary_cn: 中文摘要
            - value_comment: 价值评价
            - qa_analysis: Q&A 深度阅读
            - recommendation_score: 个性化推荐分（0-100）
            - recommendation_reason: 推荐理由
            - recommendation_interest_hash: 对应研究兴趣哈希
            - recommendation_analyzed_at: 推荐评分时间
            
    返回：
        bool: 始终返回 True
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 检查是否已有分析记录
    cursor.execute("SELECT id FROM analysis WHERE paper_id = ?", (paper_id,))
    exists = cursor.fetchone()

    if exists:
        # 更新现有记录：动态构建 SET 子句，仅更新传入的字段
        sets = []
        params = []
        if "rating" in data:
            sets.append("rating = ?")
            params.append(int(data["rating"]))
        if "tags" in data:
            sets.append("tags = ?")
            params.append(json.dumps(data["tags"], ensure_ascii=False))
        if "summary_cn" in data:
            sets.append("summary_cn = ?")
            params.append(data["summary_cn"])
        if "value_comment" in data:
            sets.append("value_comment = ?")
            params.append(data["value_comment"])
        if "qa_analysis" in data:
            sets.append("qa_analysis = ?")
            params.append(data["qa_analysis"])
        if "recommendation_score" in data:
            sets.append("recommendation_score = ?")
            score = data["recommendation_score"]
            params.append(None if score is None else int(score))
        if "recommendation_reason" in data:
            sets.append("recommendation_reason = ?")
            params.append(data["recommendation_reason"])
        if "recommendation_interest_hash" in data:
            sets.append("recommendation_interest_hash = ?")
            params.append(data["recommendation_interest_hash"])
        if "recommendation_analyzed_at" in data:
            sets.append("recommendation_analyzed_at = ?")
            params.append(data["recommendation_analyzed_at"])

        if sets:
            params.append(paper_id)
            cursor.execute(f"UPDATE analysis SET {', '.join(sets)} WHERE paper_id = ?", params)
    else:
        # 插入新记录：使用 get 提供默认值
        cursor.execute("""
            INSERT INTO analysis (
                paper_id, tags, summary_cn, summary_en, rating, value_comment, qa_analysis,
                recommendation_score, recommendation_reason, recommendation_interest_hash, recommendation_analyzed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            paper_id,
            json.dumps(data.get("tags", []), ensure_ascii=False),
            data.get("summary_cn", ""),
            data.get("summary_en", ""),
            int(data.get("rating", 0)),
            data.get("value_comment", ""),
            data.get("qa_analysis", ""),
            data.get("recommendation_score"),
            data.get("recommendation_reason", ""),
            data.get("recommendation_interest_hash", ""),
            data.get("recommendation_analyzed_at"),
        ))

    conn.commit()
    conn.close()
    return True


def _parse_paper_analysis_row(row):
    """解析 papers + analysis 查询结果中的 JSON 字段。"""
    r = dict(row)
    for field in ("authors", "categories", "tags"):
        if r.get(field) and isinstance(r[field], str):
            try:
                r[field] = json.loads(r[field])
            except json.JSONDecodeError:
                r[field] = []
    return r


def get_papers_for_recommendation(limit=200, date=None, interest_hash=""):
    """
    获取需要计算个性化推荐分的论文。

    只返回已有基础分析结果的非隐藏论文，避免推荐任务创建空 analysis 记录后
    影响 get_unanalyzed_papers() 对“待基础分析”论文的判断。
    """
    if not interest_hash:
        return []
    limit = max(1, min(1000, int(limit or 200)))
    conn = get_connection()
    cursor = conn.cursor()
    params = [interest_hash]
    where = """
        WHERE (p.hidden IS NULL OR p.hidden = 0)
        AND a.id IS NOT NULL
        AND (
            a.recommendation_score IS NULL
            OR a.recommendation_interest_hash IS NULL
            OR a.recommendation_interest_hash != ?
        )
    """
    if date:
        where += " AND p.published_date = ?"
        params.append(date)
    params.append(limit)
    cursor.execute("""
        SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis,
               a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
               a.recommendation_analyzed_at
        FROM papers p
        JOIN analysis a ON p.id = a.paper_id
    """ + where + """
        ORDER BY p.published_date DESC, COALESCE(a.rating, 0) DESC, p.arxiv_id
        LIMIT ?
    """, params)
    rows = cursor.fetchall()
    conn.close()
    return [_parse_paper_analysis_row(row) for row in rows]


def update_recommendation_result(paper_id, score, reason, interest_hash):
    """更新已有分析记录的个性化推荐结果。"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        score = max(0, min(100, int(score or 0)))
    except (TypeError, ValueError):
        score = 0
    cursor.execute("""
        UPDATE analysis
        SET recommendation_score = ?,
            recommendation_reason = ?,
            recommendation_interest_hash = ?,
            recommendation_analyzed_at = ?
        WHERE paper_id = ?
    """, (
        score,
        str(reason or ""),
        str(interest_hash or ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        paper_id,
    ))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# ==================== 论文管理操作 ====================


def hide_paper(arxiv_id):
    """隐藏论文，使其在默认列表中不显示。
    
    参数：
        arxiv_id (str): arXiv 论文编号
        
    返回：
        bool: 操作成功返回 True，论文不存在返回 False
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE papers SET hidden = 1 WHERE arxiv_id = ?", (arxiv_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def unhide_paper(arxiv_id):
    """取消论文隐藏，恢复其在列表中的显示。
    
    参数：
        arxiv_id (str): arXiv 论文编号
        
    返回：
        bool: 操作成功返回 True，论文不存在返回 False
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE papers SET hidden = 0 WHERE arxiv_id = ?", (arxiv_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_paper(arxiv_id):
    """永久删除论文及其关联的分析结果。
    
    由于外键 CASCADE DELETE，删除 papers 记录会自动删除关联的 analysis 记录。
    
    参数：
        arxiv_id (str): arXiv 论文编号
        
    返回：
        bool: 操作成功返回 True，论文不存在返回 False
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM papers WHERE arxiv_id = ?", (arxiv_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
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
    conn = get_connection()
    cursor = conn.cursor()
    # 使用参数化 IN 子句批量删除
    placeholders = ",".join(["?"] * len(arxiv_ids))
    cursor.execute(f"DELETE FROM papers WHERE arxiv_id IN ({placeholders})", arxiv_ids)
    affected = cursor.rowcount
    conn.commit()
    conn.close()
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
    conn = get_connection()
    cursor = conn.cursor()
    # 使用参数化 IN 子句批量更新
    placeholders = ",".join(["?"] * len(arxiv_ids))
    cursor.execute(f"UPDATE papers SET hidden = 1 WHERE arxiv_id IN ({placeholders})", arxiv_ids)
    affected = cursor.rowcount
    conn.commit()
    conn.close()
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
    conn = get_connection()
    cursor = conn.cursor()
    # 使用 IN 子句筛选，LEFT JOIN 找出无基础分析或基础分析不完整的论文
    placeholders = ",".join(["?"] * len(arxiv_ids))
    cursor.execute(f"""
        SELECT p.* FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE p.arxiv_id IN ({placeholders}) AND {_basic_analysis_missing_condition("a")}
    """, arxiv_ids)
    rows = cursor.fetchall()
    conn.close()
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


# ==================== 任务日志操作 ====================


def start_task_log(task_name, message=""):
    """记录任务开始执行的日志。
    
    在任务开始时调用，返回日志 ID，后续用于 finish_task_log 更新状态。
    
    参数：
        task_name (str): 任务名称，如 "daily_pipeline", "fetch", "analyze"
        message (str): 任务描述信息
        
    返回：
        int: 日志记录 ID
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO task_logs (task_name, status, message, started_at) VALUES (?, 'running', ?, ?)",
        (task_name, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    log_id = cursor.lastrowid
    conn.close()
    return log_id


def finish_task_log(log_id, status, message="", detail=""):
    """更新任务日志，记录任务完成状态。
    
    在任务结束时调用，自动计算执行时长。
    
    参数：
        log_id (int): 日志记录 ID（由 start_task_log 返回）
        status (str): 最终状态，"success" 或 "error"
        message (str): 结果描述信息
        detail (str): 技术细节（如错误堆栈）
    """
    conn = get_connection()
    cursor = conn.cursor()
    # 计算执行时长：当前时间 - 开始时间
    cursor.execute("SELECT started_at FROM task_logs WHERE id = ?", (log_id,))
    row = cursor.fetchone()
    duration = 0
    if row and row["started_at"]:
        started = datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")
        duration = (datetime.now() - started).total_seconds()

    cursor.execute(
        "UPDATE task_logs SET status = ?, message = ?, detail = ?, finished_at = ?, duration_sec = ? WHERE id = ?",
        (status, message, detail, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), round(duration, 1), log_id)
    )
    conn.commit()
    conn.close()


def initialize_task_log_steps(log_id, steps):
    """为一次流水线执行初始化有序步骤。

    ``steps`` 是 ``(step_key, step_name)`` 二元组列表。重复初始化不会覆盖
    已经开始或完成的步骤，便于调用方安全重试日志初始化。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT OR IGNORE INTO task_log_steps
            (task_log_id, step_key, step_name, position, status)
        VALUES (?, ?, ?, ?, 'pending')
        """,
        [
            (log_id, step_key, step_name, position)
            for position, (step_key, step_name) in enumerate(steps, start=1)
        ],
    )
    conn.commit()
    conn.close()


def set_task_log_step_status(log_id, step_key, status, message="", detail=""):
    """更新流水线步骤状态，并维护步骤开始/结束时间和耗时。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT started_at FROM task_log_steps WHERE task_log_id = ? AND step_key = ?",
        (log_id, step_key),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False

    if status == "running":
        cursor.execute(
            """
            UPDATE task_log_steps
            SET status = ?, message = ?, detail = ?,
                started_at = COALESCE(started_at, ?), finished_at = NULL, duration_sec = NULL
            WHERE task_log_id = ? AND step_key = ?
            """,
            (status, message, detail, now, log_id, step_key),
        )
    elif status == "pending":
        cursor.execute(
            """
            UPDATE task_log_steps SET status = ?, message = ?, detail = ?
            WHERE task_log_id = ? AND step_key = ?
            """,
            (status, message, detail, log_id, step_key),
        )
    else:
        started_at = row["started_at"] or now
        started = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
        duration = round((datetime.now() - started).total_seconds(), 1)
        cursor.execute(
            """
            UPDATE task_log_steps
            SET status = ?, message = ?, detail = ?, started_at = ?,
                finished_at = ?, duration_sec = ?
            WHERE task_log_id = ? AND step_key = ?
            """,
            (status, message, detail, started_at, now, max(0, duration), log_id, step_key),
        )
    conn.commit()
    conn.close()
    return True


def get_task_logs(task_name=None, limit=50, offset=0):
    """获取任务日志列表，支持按任务名筛选和分页。
    
    参数：
        task_name (str, optional): 按任务名筛选
        limit (int): 每页数量，默认 50
        offset (int): 偏移量，默认 0
        
    返回：
        tuple: (日志列表, 总数) 元组
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 构建查询：可选按任务名筛选
    query = "SELECT * FROM task_logs WHERE 1=1"
    params = []

    if task_name:
        query += " AND task_name = ?"
        params.append(task_name)

    # 统计总数（用于分页）
    count_query = query.replace("SELECT *", "SELECT COUNT(*) as cnt")
    cursor.execute(count_query, params)
    total = cursor.fetchone()["cnt"]

    # 分页查询：按开始时间降序
    query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()
    results = [dict(row) for row in rows]
    if results:
        log_ids = [row["id"] for row in results]
        placeholders = ",".join("?" for _ in log_ids)
        cursor.execute(
            f"""
            SELECT * FROM task_log_steps
            WHERE task_log_id IN ({placeholders})
            ORDER BY task_log_id, position
            """,
            log_ids,
        )
        steps_by_log = {log_id: [] for log_id in log_ids}
        for step in cursor.fetchall():
            step_data = dict(step)
            steps_by_log.setdefault(step_data["task_log_id"], []).append(step_data)
        for row in results:
            row["steps"] = steps_by_log.get(row["id"], [])
    conn.close()

    return results, total


def get_task_stats():
    """获取各任务的执行统计信息。
    
    统计每个任务的：总运行次数、成功次数、错误次数、运行中次数、
    最后运行时间、平均执行时长。
    
    返回：
        list: 统计数据字典列表
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT task_name,
               COUNT(*) as total_runs,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_runs,
               SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_runs,
               SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
               MAX(started_at) as last_run,
               AVG(duration_sec) as avg_duration
        FROM task_logs
        GROUP BY task_name
        ORDER BY last_run DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_running_tasks():
    """获取当前正在运行的任务列表。
    
    用于检测是否有任务卡住或正在执行。
    
    返回：
        list: 状态为 "running" 的日志记录列表
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM task_logs WHERE status = 'running' ORDER BY started_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def interrupt_running_task_logs(reason="服务重启，任务已中断"):
    """在应用启动时收口上一次进程遗留的运行中任务。"""
    now_dt = datetime.now()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, started_at FROM task_logs WHERE status = 'running'")
    running_logs = cursor.fetchall()
    for row in running_logs:
        duration = 0
        if row["started_at"]:
            started = datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")
            duration = max(0, round((now_dt - started).total_seconds(), 1))
        cursor.execute(
            """
            UPDATE task_logs
            SET status = 'interrupted', message = ?, finished_at = ?, duration_sec = ?
            WHERE id = ?
            """,
            (reason, now, duration, row["id"]),
        )
        cursor.execute(
            """
            UPDATE task_log_steps
            SET status = 'interrupted', message = ?, finished_at = ?,
                duration_sec = CASE
                    WHEN started_at IS NULL THEN 0
                    ELSE MAX(0, ROUND((julianday(?) - julianday(started_at)) * 86400, 1))
                END
            WHERE task_log_id = ? AND status = 'running'
            """,
            (reason, now, now, row["id"]),
        )
        cursor.execute(
            """
            UPDATE task_log_steps
            SET status = 'skipped', message = '父任务中断前未执行',
                started_at = COALESCE(started_at, ?), finished_at = ?, duration_sec = 0
            WHERE task_log_id = ? AND status = 'pending'
            """,
            (now, now, row["id"]),
        )
    conn.commit()
    conn.close()
    return len(running_logs)


def clear_task_logs(keep_days=30):
    """清理指定天数之前的旧任务日志。
    
    用于定期清理，防止日志表无限增长。
    
    参数：
        keep_days (int): 保留最近多少天的日志，默认 30 天
        
    返回：
        int: 实际删除的日志条数
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM task_logs WHERE started_at < datetime('now', ?)",
        (f"-{keep_days} days",)
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


# ==================== 报告操作 ====================


def save_report(report_date, content, paper_count, analyzed_count, avg_rating):
    """保存或更新每日报告。
    
    使用 UPSERT 逻辑：如果该日期已有报告则更新，否则插入新记录。
    
    参数：
        report_date (str): 报告日期（YYYY-MM-DD）
        content (str): HTML 格式的报告内容
        paper_count (int): 论文总数
        analyzed_count (int): 已分析数
        avg_rating (float): 平均评级
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO reports (report_date, content, paper_count, analyzed_count, avg_rating)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(report_date) DO UPDATE SET
            content=excluded.content,
            paper_count=excluded.paper_count,
            analyzed_count=excluded.analyzed_count,
            avg_rating=excluded.avg_rating,
            created_at=CURRENT_TIMESTAMP
    """, (report_date, content, paper_count, analyzed_count, avg_rating))
    conn.commit()
    conn.close()


def get_reports(limit=50):
    """获取报告列表，按日期降序。
    
    参数：
        limit (int): 最大返回数量，默认 50
        
    返回：
        list: 报告数据字典列表
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reports ORDER BY report_date DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_report_by_date(report_date):
    """根据日期获取单份报告。
    
    参数：
        report_date (str): 报告日期（YYYY-MM-DD）
        
    返回：
        dict or None: 报告数据字典，不存在返回 None
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reports WHERE report_date = ?", (report_date,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_report_dates():
    """获取所有报告日期及统计信息。
    
    用于报告列表页面显示日期和概览数据。
    
    返回：
        list: 包含 report_date, paper_count, analyzed_count, avg_rating 的字典列表
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT report_date, paper_count, analyzed_count, avg_rating FROM reports ORDER BY report_date DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ==================== AI 用量记录 ====================

def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def record_ai_usage(usage):
    """记录一次 LLM API 调用的 token 用量。"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ai_usage_logs (
            task_key, provider_key, provider_name, model, paper_id, arxiv_id,
            prompt_tokens, completion_tokens, total_tokens, cached_tokens, cache_miss_tokens
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        usage.get("task_key", ""),
        usage.get("provider_key", ""),
        usage.get("provider_name", ""),
        usage.get("model", ""),
        usage.get("paper_id"),
        usage.get("arxiv_id", ""),
        _safe_int(usage.get("prompt_tokens")),
        _safe_int(usage.get("completion_tokens")),
        _safe_int(usage.get("total_tokens")),
        _safe_int(usage.get("cached_tokens")),
        _safe_int(usage.get("cache_miss_tokens")),
    ))
    conn.commit()
    conn.close()


def _usage_dates(days):
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days - 1)
    return [(start_date + timedelta(days=i)).isoformat() for i in range(days)]


def _empty_usage_point(day):
    return {
        "date": day,
        "call_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "cache_miss_tokens": 0,
    }


def get_ai_usage_summary(days=7, group_by="task"):
    """按任务/模型汇总最近 N 天 token 用量，并返回按天分桶的时间序列。"""
    days = max(1, min(365, _safe_int(days) or 7))
    group_by = group_by if group_by in {"task", "model"} else "task"
    dates = _usage_dates(days)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            task_key,
            provider_key,
            provider_name,
            model,
            COUNT(*) AS call_count,
            SUM(prompt_tokens) AS prompt_tokens,
            SUM(completion_tokens) AS completion_tokens,
            SUM(total_tokens) AS total_tokens,
            SUM(cached_tokens) AS cached_tokens,
            SUM(cache_miss_tokens) AS cache_miss_tokens
        FROM ai_usage_logs
        WHERE created_at >= datetime('now', ?)
        GROUP BY task_key, provider_key, provider_name, model
        ORDER BY total_tokens DESC, call_count DESC
    """, (f"-{days} days",))
    rows = cursor.fetchall()

    if group_by == "model":
        cursor.execute("""
            SELECT
                date(created_at) AS usage_date,
                COALESCE(NULLIF(model, ''), 'unknown') AS group_key,
                COALESCE(NULLIF(model, ''), 'unknown') AS label,
                COUNT(*) AS call_count,
                SUM(prompt_tokens) AS prompt_tokens,
                SUM(completion_tokens) AS completion_tokens,
                SUM(total_tokens) AS total_tokens,
                SUM(cached_tokens) AS cached_tokens,
                SUM(cache_miss_tokens) AS cache_miss_tokens
            FROM ai_usage_logs
            WHERE created_at >= datetime('now', ?)
            GROUP BY usage_date, group_key
            ORDER BY usage_date, group_key
        """, (f"-{days} days",))
    else:
        cursor.execute("""
            SELECT
                date(created_at) AS usage_date,
                COALESCE(NULLIF(task_key, ''), 'unknown') AS group_key,
                COALESCE(NULLIF(task_key, ''), 'unknown') AS label,
                COUNT(*) AS call_count,
                SUM(prompt_tokens) AS prompt_tokens,
                SUM(completion_tokens) AS completion_tokens,
                SUM(total_tokens) AS total_tokens,
                SUM(cached_tokens) AS cached_tokens,
                SUM(cache_miss_tokens) AS cache_miss_tokens
            FROM ai_usage_logs
            WHERE created_at >= datetime('now', ?)
            GROUP BY usage_date, group_key
            ORDER BY usage_date, group_key
        """, (f"-{days} days",))
    series_rows = cursor.fetchall()
    conn.close()

    groups = {}
    for row in series_rows:
        item = dict(row)
        key = item["group_key"]
        if key not in groups:
            groups[key] = {
                "key": key,
                "label": item["label"],
                "call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "cache_miss_tokens": 0,
                "points": {day: _empty_usage_point(day) for day in dates},
            }
        point = groups[key]["points"].setdefault(item["usage_date"], _empty_usage_point(item["usage_date"]))
        for field in ("call_count", "prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "cache_miss_tokens"):
            value = _safe_int(item.get(field))
            point[field] = value
            groups[key][field] += value

    group_items = []
    for group in groups.values():
        group["points"] = [group["points"].get(day, _empty_usage_point(day)) for day in dates]
        group_items.append(group)
    group_items.sort(key=lambda item: (item["total_tokens"], item["call_count"]), reverse=True)

    totals = {
        "call_count": sum(_safe_int(row["call_count"]) for row in rows),
        "prompt_tokens": sum(_safe_int(row["prompt_tokens"]) for row in rows),
        "completion_tokens": sum(_safe_int(row["completion_tokens"]) for row in rows),
        "total_tokens": sum(_safe_int(row["total_tokens"]) for row in rows),
        "cached_tokens": sum(_safe_int(row["cached_tokens"]) for row in rows),
        "cache_miss_tokens": sum(_safe_int(row["cache_miss_tokens"]) for row in rows),
    }
    return {
        "days": days,
        "group_by": group_by,
        "dates": dates,
        "totals": totals,
        "groups": group_items,
        "items": [dict(row) for row in rows],
    }


def generate_report_content(date, ai_summary=None):
    """生成指定日期的 HTML 报告内容。
    
    报告包含：
    1. 统计摘要（论文总数、已分析数、平均评级）
    2. 分类分布
    3. 热门标签（Top 15）
    4. 个性化推荐（如已启用）
    5. 全部论文列表
    
    参数：
        date (str): 报告日期（YYYY-MM-DD）
        ai_summary (str | None): 可选 AI 导读，默认不生成也不展示
        
    返回：
        tuple: (html_content, total, analyzed, avg_rating)
            - html_content: HTML 字符串，无数据返回 None
            - total: 论文总数
            - analyzed: 已分析数
            - avg_rating: 平均评级
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 查询指定日期的所有论文及其分析结果
    cursor.execute("""
        SELECT p.*, a.id AS analysis_id, a.tags, a.summary_cn, a.rating, a.value_comment,
               a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
               a.recommendation_analyzed_at
        FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE p.published_date = ?
        ORDER BY a.rating DESC, p.arxiv_id
    """, (date,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return None, 0, 0, 0

    from settings import get_personalization_config, get_research_interest_hash
    current_research_interests = get_personalization_config().get("research_interests", "")
    current_interest_hash = get_research_interest_hash(current_research_interests)

    def esc(value):
        return html_module.escape(str(value or ""), quote=True)

    def format_stars(value):
        rating = max(0, min(5, int(value or 0)))
        return " ".join("★" if i < rating else "☆" for i in range(5))

    def current_recommendation_score(paper):
        if not current_interest_hash or paper.get("recommendation_interest_hash") != current_interest_hash:
            return None
        try:
            score = int(paper.get("recommendation_score"))
            return max(0, min(100, score))
        except (TypeError, ValueError):
            return None

    # 解析 JSON 字段
    papers = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        r["current_recommendation_score"] = current_recommendation_score(r)
        papers.append(r)

    if current_interest_hash:
        papers.sort(key=lambda p: (
            -(p.get("current_recommendation_score") if p.get("current_recommendation_score") is not None else -1),
            -int(p.get("rating") or 0),
            str(p.get("arxiv_id") or ""),
        ))

    # 计算统计数据
    total = len(papers)
    analyzed = sum(1 for p in papers if p.get("analysis_id") is not None)
    ratings = [int(p["rating"]) for p in papers if p.get("rating") is not None]
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0
    recommended_count = sum(1 for p in papers if (p.get("current_recommendation_score") or 0) >= 80)

    # 统计标签和分类分布
    tag_counts = {}
    category_counts = {}
    for p in papers:
        if p.get("tags") and isinstance(p["tags"], list):
            for t in p["tags"]:
                tag_counts[t] = tag_counts.get(t, 0) + 1
        if p.get("categories") and isinstance(p["categories"], list):
            for c in p["categories"]:
                category_counts[c] = category_counts.get(c, 0) + 1

    # 获取 Top 15 标签和 Top 10 分类
    top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:15]
    top_categories = sorted(category_counts.items(), key=lambda x: -x[1])[:10]
    recommended = [p for p in papers if (p.get("current_recommendation_score") or 0) >= 60]
    recommended.sort(key=lambda x: (-(x.get("current_recommendation_score") or 0), -int(x.get("rating") or 0)))

    # ==================== 生成 HTML 报告 ====================
    
    # 统计摘要卡片
    html = f'<div class="report-summary">'
    html += f'<div class="report-stats">'
    html += f'<div class="report-stat"><span class="report-stat-val">{total}</span><span class="report-stat-label">论文总数</span></div>'
    html += f'<div class="report-stat"><span class="report-stat-val">{analyzed}</span><span class="report-stat-label">已分析</span></div>'
    html += f'<div class="report-stat"><span class="report-stat-val">{avg_rating}</span><span class="report-stat-label">平均评级</span></div>'
    if current_interest_hash:
        html += f'<div class="report-stat"><span class="report-stat-val">{recommended_count}</span><span class="report-stat-label">强推荐</span></div>'
    html += f'</div></div>'

    if ai_summary:
        summary_html = esc(ai_summary).replace("\n", "<br>")
        html += f'<div class="report-section"><h3>🤖 AI 导读</h3><div class="report-paper-summary">{summary_html}</div></div>'

    if recommended:
        interest_html = ""
        if current_research_interests:
            interest_text = esc(current_research_interests).replace("\n", "<br>")
            interest_html = f'<div class="report-paper-summary"><strong>研究兴趣:</strong><br>{interest_text}</div>'
        html += f'<div class="report-section"><h3>🎯 个性化推荐</h3>{interest_html}<div class="report-papers">'
        for p in recommended[:20]:
            stars = format_stars(p.get('rating'))
            score = p.get("current_recommendation_score") or 0
            authors = ', '.join(esc(a) for a in p['authors'][:3]) if isinstance(p.get('authors'), list) else esc(p.get('authors', ''))
            cats = ' '.join(f'<span class="category-tag">{esc(c)}</span>' for c in (p.get('categories') or [])[:3])
            arxiv_id = esc(p.get('arxiv_id', ''))
            summary = f'<div class="report-paper-summary"><strong>中文摘要:</strong> {esc(p.get("summary_cn", ""))}</div>' if p.get("summary_cn") else ''
            reason = f'<div class="report-paper-comment"><strong>推荐语:</strong> {esc(p.get("recommendation_reason", ""))}</div>' if p.get("recommendation_reason") else ''
            comment = f'<div class="report-paper-comment"><strong>评价:</strong> {esc(p.get("value_comment", ""))}</div>' if p.get("value_comment") else ''
            html += f'''<div class="report-paper">
                <div class="report-paper-title"><a href="/paper/{arxiv_id}">{esc(p.get('title', ''))}</a></div>
                <div class="report-paper-meta"><span class="rating">推荐 {score}/100</span> <span class="rating">{stars}</span> {cats}</div>
                <div class="report-paper-authors">{authors}</div>
                {summary}
                {reason}
                {comment}
            </div>'''
        html += '</div></div>'

    # 分类分布区块
    if top_categories:
        html += '<div class="report-section"><h3>📂 分类分布</h3><div class="report-tags">'
        for cat, cnt in top_categories:
            html += f'<span class="tag-badge">{esc(cat)} <span class="tag-count">{cnt}</span></span>'
        html += '</div></div>'

    # 热门标签区块
    if top_tags:
        html += '<div class="report-section"><h3>🏷️ 热门标签</h3><div class="report-tags">'
        for tag, cnt in top_tags:
            html += f'<span class="tag-badge">{esc(tag)} <span class="tag-count">{cnt}</span></span>'
        html += '</div></div>'

    # 全部论文列表区块
    html += '<div class="report-section"><h3>📋 全部论文</h3><div class="report-papers">'
    for p in papers:
        stars = ''
        if p.get('rating') is not None:
            stars = f'<span class="rating">{format_stars(p.get("rating"))}</span>'
        rec = ''
        if p.get("current_recommendation_score") is not None:
            rec = f'<span class="rating">推荐 {int(p["current_recommendation_score"])}/100</span>'
        cats = ' '.join(f'<span class="category-tag">{esc(c)}</span>' for c in (p.get('categories') or [])[:3])
        authors = ', '.join(esc(a) for a in p['authors'][:3]) if isinstance(p.get('authors'), list) else esc(p.get('authors', ''))
        tags_html = ''
        if p.get('tags') and isinstance(p['tags'], list):
            tags_html = ' '.join(f'<span class="tag-small">{esc(t)}</span>' for t in p['tags'][:5])
        summary = f'<div class="report-paper-summary">{esc(p["summary_cn"])}</div>' if p.get('summary_cn') else ''
        arxiv_id = esc(p.get('arxiv_id', ''))
        html += f'''<div class="report-paper">
            <div class="report-paper-title"><a href="/paper/{arxiv_id}">{esc(p.get('title', ''))}</a> {rec} {stars}</div>
            <div class="report-paper-meta">{cats}</div>
            <div class="report-paper-authors">{authors}</div>
            {'<div class="report-paper-tags">' + tags_html + '</div>' if tags_html else ''}
            {summary}
        </div>'''
    html += '</div></div>'

    return html, total, analyzed, avg_rating


# ==================== 阅读清单操作 ====================


def add_to_reading_list(paper_id):
    """将论文添加到阅读清单。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        bool: 添加成功返回 True，已存在或其他错误返回 False
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO reading_list (paper_id) VALUES (?)", (paper_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def remove_from_reading_list(paper_id):
    """从阅读清单中移除论文。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        bool: 移除成功返回 True，论文不在清单中返回 False
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM reading_list WHERE paper_id = ?", (paper_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def is_in_reading_list(paper_id):
    """检查论文是否在阅读清单中。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        bool: 在清单中返回 True，否则返回 False
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM reading_list WHERE paper_id = ?", (paper_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def mark_as_read(paper_id):
    """将阅读清单中的论文标记为已读。
    
    同时记录完成时间。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        bool: 操作成功返回 True，论文不在清单中返回 False
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE reading_list SET status = 'read', completed_at = ? WHERE paper_id = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), paper_id)
    )
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def mark_as_unread(paper_id):
    """将阅读清单中的论文标记为未读。
    
    清除完成时间，重置为未读状态。
    
    参数：
        paper_id (int): 论文 ID
        
    返回：
        bool: 操作成功返回 True，论文不在清单中返回 False
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE reading_list SET status = 'unread', completed_at = NULL WHERE paper_id = ?",
        (paper_id,)
    )
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_reading_list(status=None):
    """获取阅读清单，支持按状态筛选。
    
    关联 papers 和 analysis 表，返回完整的论文信息。
    默认按状态排序（未读优先），同状态按添加时间降序。
    
    参数：
        status (str, optional): 按状态筛选，"unread" 或 "read"，None 返回全部
        
    返回：
        list: 论文数据字典列表，额外包含 todo_status, added_at, completed_at 字段
    """
    conn = get_connection()
    cursor = conn.cursor()
    if status:
        # 按指定状态筛选
        cursor.execute("""
            SELECT p.*, a.tags, a.summary_cn, a.rating, a.value_comment,
                   a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                   rl.status as todo_status, rl.added_at, rl.completed_at
            FROM reading_list rl
            JOIN papers p ON rl.paper_id = p.id
            LEFT JOIN analysis a ON p.id = a.paper_id
            WHERE rl.status = ?
            ORDER BY rl.added_at DESC
        """, (status,))
    else:
        # 返回全部：未读优先，同状态按添加时间降序
        cursor.execute("""
            SELECT p.*, a.tags, a.summary_cn, a.rating, a.value_comment,
                   a.recommendation_score, a.recommendation_reason, a.recommendation_interest_hash,
                   rl.status as todo_status, rl.added_at, rl.completed_at
            FROM reading_list rl
            JOIN papers p ON rl.paper_id = p.id
            LEFT JOIN analysis a ON p.id = a.paper_id
            ORDER BY
                CASE WHEN rl.status = 'unread' THEN 0 ELSE 1 END,
                rl.added_at DESC
        """)
    rows = cursor.fetchall()
    conn.close()

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


def get_reading_list_count():
    """获取阅读清单的统计数据。
    
    返回：
        dict: 包含 total（总数）和 unread（未读数）
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'unread' THEN 1 ELSE 0 END) as unread
        FROM reading_list
    """)
    row = cursor.fetchone()
    conn.close()
    return {"total": row["total"] or 0, "unread": row["unread"] or 0}


# ==================== 论文学习：对话与主动问答 ====================

def add_paper_chat_message(paper_id, role, content):
    """保存一条论文自由讨论消息。"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO paper_chat_messages (paper_id, role, content)
        VALUES (?, ?, ?)
    """, (paper_id, str(role or ""), str(content or "")))
    conn.commit()
    message_id = cursor.lastrowid
    conn.close()
    return message_id


def get_paper_chat_messages(paper_id, limit=200):
    """按时间顺序读取论文自由讨论历史。"""
    limit = max(1, min(1000, _safe_int(limit) or 200))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM (
            SELECT id, paper_id, role, content, created_at
            FROM paper_chat_messages
            WHERE paper_id = ?
            ORDER BY id DESC
            LIMIT ?
        )
        ORDER BY id ASC
    """, (paper_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def create_paper_quiz_session(paper_id, mode):
    """创建一轮论文问答或苏格拉底练习会话。"""
    mode = str(mode or "quick3")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO paper_quiz_sessions (paper_id, mode, status, updated_at)
        VALUES (?, ?, 'active', CURRENT_TIMESTAMP)
    """, (paper_id, mode))
    conn.commit()
    session_id = cursor.lastrowid
    conn.close()
    return session_id


def get_paper_quiz_session(session_id, paper_id=None):
    """读取单个练习会话元数据。"""
    conn = get_connection()
    cursor = conn.cursor()
    if paper_id is None:
        cursor.execute("SELECT * FROM paper_quiz_sessions WHERE id = ?", (session_id,))
    else:
        cursor.execute("SELECT * FROM paper_quiz_sessions WHERE id = ? AND paper_id = ?", (session_id, paper_id))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def add_paper_quiz_questions(session_id, questions):
    """批量保存练习题。questions 元素包含 question/expected_points。"""
    conn = get_connection()
    cursor = conn.cursor()
    saved = []
    for idx, question in enumerate(questions or [], start=1):
        if not isinstance(question, dict):
            continue
        text = str(question.get("question") or "").strip()
        if not text:
            continue
        expected = question.get("expected_points", [])
        if not isinstance(expected, str):
            expected = json.dumps(expected or [], ensure_ascii=False)
        cursor.execute("""
            INSERT INTO paper_quiz_questions (session_id, position, question, expected_points)
            VALUES (?, ?, ?, ?)
        """, (session_id, idx, text, expected))
        saved.append(cursor.lastrowid)
    cursor.execute("UPDATE paper_quiz_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return saved


def add_paper_quiz_question(session_id, position, question, expected_points=""):
    """保存单个练习题或苏格拉底追问。"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO paper_quiz_questions (session_id, position, question, expected_points)
        VALUES (?, ?, ?, ?)
    """, (session_id, int(position or 1), str(question or ""), str(expected_points or "")))
    cursor.execute("UPDATE paper_quiz_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
    conn.commit()
    question_id = cursor.lastrowid
    conn.close()
    return question_id


def add_paper_quiz_attempt(question_id, answer_text, score, feedback):
    """保存用户答案和模型反馈。"""
    conn = get_connection()
    cursor = conn.cursor()
    feedback_json = feedback if isinstance(feedback, str) else json.dumps(feedback or {}, ensure_ascii=False)
    cursor.execute("""
        INSERT INTO paper_quiz_attempts (question_id, answer_text, score, feedback_json)
        VALUES (?, ?, ?, ?)
    """, (question_id, str(answer_text or ""), max(0, min(5, _safe_int(score))), feedback_json))
    cursor.execute("""
        UPDATE paper_quiz_sessions
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = (
            SELECT session_id FROM paper_quiz_questions WHERE id = ?
        )
    """, (question_id,))
    conn.commit()
    attempt_id = cursor.lastrowid
    conn.close()
    return attempt_id


def get_paper_quiz_question(question_id, paper_id=None):
    """读取单个题目，可选校验所属论文。"""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT q.*, s.paper_id, s.mode, s.status
        FROM paper_quiz_questions q
        JOIN paper_quiz_sessions s ON q.session_id = s.id
        WHERE q.id = ?
    """
    params = [question_id]
    if paper_id is not None:
        query += " AND s.paper_id = ?"
        params.append(paper_id)
    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_paper_quiz_session_detail(session_id, paper_id=None):
    """读取练习会话、题目和每题最新一次作答反馈。"""
    session = get_paper_quiz_session(session_id, paper_id=paper_id)
    if not session:
        return None

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT q.*, a.id AS attempt_id, a.answer_text, a.score, a.feedback_json, a.created_at AS answered_at
        FROM paper_quiz_questions q
        LEFT JOIN paper_quiz_attempts a ON a.id = (
            SELECT id FROM paper_quiz_attempts
            WHERE question_id = q.id
            ORDER BY id DESC
            LIMIT 1
        )
        WHERE q.session_id = ?
        ORDER BY q.position ASC, q.id ASC
    """, (session_id,))
    rows = cursor.fetchall()
    conn.close()

    questions = []
    for row in rows:
        item = dict(row)
        expected = item.get("expected_points")
        if expected:
            try:
                item["expected_points"] = json.loads(expected)
            except json.JSONDecodeError:
                item["expected_points"] = expected
        feedback = item.get("feedback_json")
        if feedback:
            try:
                item["feedback"] = json.loads(feedback)
            except json.JSONDecodeError:
                item["feedback"] = {"feedback": feedback}
        else:
            item["feedback"] = None
        questions.append(item)
    session["questions"] = questions
    return session


def get_latest_paper_quiz_sessions(paper_id, limit=20):
    """读取某篇论文最近的学习会话列表。"""
    limit = max(1, min(100, _safe_int(limit) or 20))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.*,
               COUNT(DISTINCT q.id) AS question_count,
               COUNT(DISTINCT a.id) AS attempt_count
        FROM paper_quiz_sessions s
        LEFT JOIN paper_quiz_questions q ON s.id = q.session_id
        LEFT JOIN paper_quiz_attempts a ON q.id = a.question_id
        WHERE s.paper_id = ?
        GROUP BY s.id
        ORDER BY s.updated_at DESC, s.id DESC
        LIMIT ?
    """, (paper_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
