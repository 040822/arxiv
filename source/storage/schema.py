"""SQLite schema implementation."""

import html as html_module
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from .connection import get_connection


def init_db():
    """初始化数据库，创建所有表和索引。
    
    功能：
    1. 创建核心业务表、学习记录表和用量日志表（如果不存在）
    2. 创建索引优化查询性能
    3. 执行数据库迁移（添加新字段）
    
    注意：此函数在应用启动时调用，可重复调用不会破坏已有数据。
    """
    with get_connection() as conn:
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
                status TEXT NOT NULL DEFAULT 'running',  -- running/success/warning/error/skipped/interrupted
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
