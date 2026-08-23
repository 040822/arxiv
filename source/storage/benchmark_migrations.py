"""Schema migrations for private paper reading benchmark (v6)."""

import logging

logger = logging.getLogger(__name__)


def migrate_benchmark_tables(conn):
    """Create benchmark tables (routes, suites, papers, cases, runs, responses, judgments)."""
    conn.execute(
        """
        CREATE TABLE benchmark_routes (
            task_key TEXT PRIMARY KEY,          -- benchmark_author/benchmark_judge/benchmark_judge_review
            provider_key TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            is_thinking INTEGER DEFAULT 0,
            thinking_effort TEXT DEFAULT 'medium',
            temperature_enabled INTEGER DEFAULT 0,
            temperature REAL DEFAULT 0.2,
            max_tokens_enabled INTEGER DEFAULT 1,
            max_tokens INTEGER DEFAULT 2000,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE benchmark_suites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subset_name TEXT NOT NULL,
            version_label TEXT NOT NULL,        -- pprb-<subset>-YYYY.MM.DD-rN
            status TEXT NOT NULL DEFAULT 'draft',  -- draft/review/frozen/retired
            deep_reading_prompt TEXT NOT NULL DEFAULT '{}',  -- JSON 快照 {system, instruction}
            paper_chat_prompt TEXT NOT NULL DEFAULT '{}',    -- JSON 快照 {system, instruction}
            suite_checksum TEXT NOT NULL DEFAULT '',
            scoring_revision TEXT NOT NULL DEFAULT '{}',     -- JSON {label, judge_prompt_hash, routes}
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(subset_name, version_label)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE benchmark_suite_papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suite_id INTEGER NOT NULL,
            paper_id INTEGER,                   -- 原业务 papers.id，论文删除后可为 NULL
            paper_key TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            authors TEXT NOT NULL DEFAULT '[]',
            source_type TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            abstract TEXT NOT NULL DEFAULT '',
            full_text TEXT NOT NULL DEFAULT '',
            text_chars INTEGER DEFAULT 0,
            text_sha256 TEXT NOT NULL DEFAULT '',
            pdf_sha256 TEXT NOT NULL DEFAULT '',
            extractor_version TEXT NOT NULL DEFAULT '',
            position INTEGER DEFAULT 0,
            FOREIGN KEY (suite_id) REFERENCES benchmark_suites(id) ON DELETE CASCADE,
            UNIQUE(suite_id, paper_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE benchmark_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suite_id INTEGER NOT NULL,
            paper_ref_id INTEGER NOT NULL,
            track TEXT NOT NULL,                -- deep_reading/chat
            position INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT '',      -- q1..q6 / chat_round1..3
            question TEXT NOT NULL DEFAULT '',
            reference_answer TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '[]',   -- JSON 字符串数组
            rubric TEXT NOT NULL DEFAULT '{}',     -- JSON {conditions:[{text,weight,critical}]}
            requires_reject INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending/accepted/rejected
            review_note TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT,
            author_raw TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (suite_id) REFERENCES benchmark_suites(id) ON DELETE CASCADE,
            FOREIGN KEY (paper_ref_id) REFERENCES benchmark_suite_papers(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE benchmark_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suite_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',  -- running/interrupted/completed/error
            repeats INTEGER DEFAULT 1,
            max_calls INTEGER,                  -- 硬预算，NULL 不限
            runner_version TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            FOREIGN KEY (suite_id) REFERENCES benchmark_suites(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE benchmark_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            provider_key TEXT NOT NULL DEFAULT '',
            provider_name TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            is_thinking INTEGER DEFAULT 0,
            thinking_effort TEXT DEFAULT 'medium',
            temperature_enabled INTEGER DEFAULT 0,
            temperature REAL DEFAULT 0.2,
            max_tokens_enabled INTEGER DEFAULT 1,
            max_tokens INTEGER DEFAULT 4000,
            config_hash TEXT NOT NULL DEFAULT '',
            actual_params TEXT NOT NULL DEFAULT '{}',  -- 实际发送的请求参数快照
            FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE benchmark_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL,
            paper_ref_id INTEGER NOT NULL,
            track TEXT NOT NULL,                -- deep_reading/chat
            repeat_index INTEGER DEFAULT 0,
            round_index INTEGER,                -- chat 1..3；deep_reading 为 NULL
            prompt_snapshot TEXT NOT NULL DEFAULT '[]',  -- JSON 消息列表
            raw_output TEXT NOT NULL DEFAULT '',
            parsed TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'ok',  -- ok/retry_failed/format_error/truncated/empty
            finish_reason TEXT NOT NULL DEFAULT '',
            usage_json TEXT NOT NULL DEFAULT '{}',
            latency_ms REAL DEFAULT 0,
            continuation_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (candidate_id) REFERENCES benchmark_candidates(id) ON DELETE CASCADE,
            FOREIGN KEY (paper_ref_id) REFERENCES benchmark_suite_papers(id) ON DELETE CASCADE,
            UNIQUE(candidate_id, paper_ref_id, track, repeat_index, round_index)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE benchmark_judgments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            response_id INTEGER NOT NULL,
            judge_role TEXT NOT NULL,           -- primary/review/human
            judge_route_key TEXT NOT NULL DEFAULT '',
            scoring_revision TEXT NOT NULL DEFAULT '',
            case_id INTEGER NOT NULL,
            condition_scores TEXT NOT NULL DEFAULT '[]',  -- JSON [{condition,score}]
            score REAL NOT NULL DEFAULT 0,      -- 0-100
            hallucination_critical INTEGER DEFAULT 0,
            confidence REAL,
            raw_json TEXT NOT NULL DEFAULT '{}',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE,
            FOREIGN KEY (response_id) REFERENCES benchmark_responses(id) ON DELETE CASCADE,
            FOREIGN KEY (case_id) REFERENCES benchmark_cases(id) ON DELETE CASCADE,
            UNIQUE(response_id, judge_role, case_id, scoring_revision)
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_benchmark_cases_suite ON benchmark_cases(suite_id, track, position)"
    )
    conn.execute(
        "CREATE INDEX idx_benchmark_cases_paper ON benchmark_cases(paper_ref_id)"
    )
    conn.execute(
        "CREATE INDEX idx_benchmark_runs_suite ON benchmark_runs(suite_id)"
    )
    conn.execute(
        "CREATE INDEX idx_benchmark_responses_run ON benchmark_responses(run_id, track)"
    )
    conn.execute(
        "CREATE INDEX idx_benchmark_judgments_response ON benchmark_judgments(response_id)"
    )


def migrate_benchmark_candidate_call_budget(conn):
    """Persist candidate API-attempt usage and backfill existing runs.

    ``benchmark_responses`` stores one row per logical response, while a
    deep-reading response can contain one continuation attempt.  The v6
    counter therefore sums ``1 + continuation_count`` for every historical
    response.  New failed attempts are charged by the runner before the API
    call and remain counted even when no response row can be saved.
    """
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(benchmark_runs)").fetchall()
    }
    if "candidate_calls_made" not in columns:
        conn.execute(
            "ALTER TABLE benchmark_runs ADD COLUMN candidate_calls_made INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        """
        UPDATE benchmark_runs
        SET candidate_calls_made = COALESCE(
            (
                SELECT SUM(1 + COALESCE(continuation_count, 0))
                FROM benchmark_responses
                WHERE benchmark_responses.run_id = benchmark_runs.id
            ),
            0
        )
        """
    )


def _table_columns(conn, table_name):
    return {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _add_column_if_missing(conn, table_name, column_name, definition):
    if column_name not in _table_columns(conn, table_name):
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )


def migrate_benchmark_v7(conn):
    """Add durable run/reuse/rejudge/audit state to the v6 benchmark schema.

    Existing rows deliberately receive only schema defaults.  In particular,
    no historical actor, response provenance, scoring revision, or retry
    information is fabricated during the migration.
    """
    _add_column_if_missing(
        conn, "benchmark_runs", "active_scoring_revision",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        conn, "benchmark_responses", "reused_from_response_id",
        "INTEGER REFERENCES benchmark_responses(id) ON DELETE SET NULL",
    )
    _add_column_if_missing(
        conn, "benchmark_responses", "retry_count",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        conn, "benchmark_responses", "error_json",
        "TEXT NOT NULL DEFAULT '{}'",
    )
    _add_column_if_missing(
        conn, "benchmark_judgments", "actor_user_id",
        "INTEGER",
    )
    _add_column_if_missing(
        conn, "benchmark_judgments", "actor_username",
        "TEXT NOT NULL DEFAULT ''",
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS benchmark_scoring_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            revision_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            primary_route_key TEXT NOT NULL DEFAULT '',
            review_route_key TEXT NOT NULL DEFAULT '',
            primary_route_snapshot TEXT NOT NULL DEFAULT '{}',
            review_route_snapshot TEXT NOT NULL DEFAULT '{}',
            primary_prompt_snapshot TEXT NOT NULL DEFAULT '{}',
            review_prompt_snapshot TEXT NOT NULL DEFAULT '{}',
            error_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            finished_at TEXT,
            FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE,
            UNIQUE(run_id, revision_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS benchmark_case_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            suite_id INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            action TEXT NOT NULL,
            actor_user_id INTEGER,
            actor_username TEXT NOT NULL DEFAULT '',
            before_json TEXT NOT NULL DEFAULT '{}',
            after_json TEXT NOT NULL DEFAULT '{}',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES benchmark_cases(id) ON DELETE CASCADE,
            FOREIGN KEY (suite_id) REFERENCES benchmark_suites(id) ON DELETE CASCADE,
            UNIQUE(case_id, revision)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_benchmark_scoring_revisions_run "
        "ON benchmark_scoring_revisions(run_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_benchmark_case_revisions_case "
        "ON benchmark_case_revisions(case_id, revision)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_benchmark_case_revisions_suite "
        "ON benchmark_case_revisions(suite_id, id)"
    )
