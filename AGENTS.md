# AGENTS.md — AI 论文数据库项目维护文档

本文件供 AI Agent 阅读，用于理解项目结构、代码逻辑和开发规范，以便后续维护和开发新功能。

> **📚 完整文档：** 详细的用户手册、开发者指南、API 文档等请参阅 [`docs/`](docs/) 目录。
> - [docs/agent-guide.md](docs/agent-guide.md) — AI Agent 快速入门
> - [docs/developer-guide.md](docs/developer-guide.md) — 开发者指南
> - [docs/api-reference.md](docs/api-reference.md) — API 接口文档
> - [docs/architecture.md](docs/architecture.md) — 项目架构说明

---

## 1. 项目概述

自动从 arXiv 抓取 AI/机器人领域论文，调用 OpenAI 兼容 API 做基础分析（标签、AI 评级、中文摘要、简评）和按需深度阅读（Q&A），存入 SQLite 数据库，通过 Flask Web 界面浏览。论文评级由 AI 初评，用户可手动修正。用户还可以在单篇论文学习页中基于 PDF 全文进行自由讨论、主动问答练习和苏格拉底追问。

**技术栈:** Python 3.10+ / Flask / SQLite / APScheduler / arxiv-py / OpenAI SDK / PyMuPDF

**分支策略:**
- `master` — 稳定版本
- `dev` — 开发分支

---

## 2. 文件结构与职责

```text
arxiv/
├── app.py                  # 唯一 Web 入口（main + 可导入 Flask app）
├── source/
│   ├── analysis/         # LLM 客户端、消息、分析、学习、导读与批处理
│   ├── benchmark/        # 私有论文阅读 Benchmark 深模块（出题/冻结/运行/裁判/报告）
│   ├── backups/          # WebDAV 快照打包、上传、清理与编排
│   ├── documents/        # PDF 校验、持久上传、缓存下载、删除与提取
│   ├── ingestion/        # arXiv 日期窗口抓取、分批与单篇查询
│   ├── reports/email/   # 邮件 config/content/transport/service 与 CSS 资源
│   ├── config.py          # 硬编码分类、标签候选与路径
│   ├── settings/         # 运行时配置
│   ├── imports/          # 手动论文预览与安全 URL 校验
│   ├── storage/          # 连接、迁移、快照、文件信息与业务存储
│   ├── reports/          # Web 日报渲染
│   ├── pipeline/         # 手动/定时流水线与 APScheduler
│   └── web/              # Flask 应用装配、Blueprint、鉴权与进度
├── templates/             # Jinja2 页面
├── static/css/           # core/components/rich-text 与 pages/*
├── static/promo.css       # about/vision 独立宣传样式
└── data/                  # 运行时数据库、配置、PDF 缓存与持久上传
```

---

## 3. 数据库结构

### papers 表
```sql
CREATE TABLE papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key TEXT UNIQUE NOT NULL,   -- 所有来源通用的内部路由标识
    arxiv_id TEXT UNIQUE,             -- 仅 arXiv 论文有值
    source_type TEXT NOT NULL,        -- arxiv/openreview/doi/web/upload
    source_id TEXT,                   -- 来源稳定标识；与 source_type 联合唯一
    ingest_mode TEXT NOT NULL,        -- feed/manual；定时任务只处理 feed
    title TEXT NOT NULL,
    authors TEXT NOT NULL,            -- JSON 数组
    abstract TEXT NOT NULL,
    categories TEXT NOT NULL,         -- JSON 数组
    primary_category TEXT,
    url TEXT,                         -- 来源页面链接
    pdf_url TEXT,                     -- 远程 PDF 链接
    venue TEXT,
    published_date TEXT,
    updated_date TEXT,
    pdf_local_path TEXT,              -- data/ 下的持久 PDF 相对路径
    pdf_sha256 TEXT,                  -- 非空时唯一
    pdf_size_bytes INTEGER,
    hidden INTEGER DEFAULT 0,          -- 1=隐藏
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### analysis 表
```sql
CREATE TABLE analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,         -- FK -> papers.id；uq_analysis_paper_id 保证每篇论文唯一
    tags TEXT,                         -- JSON 数组，如 ["VLA","World Model"]
    summary_cn TEXT,                   -- Abstract 中文翻译
    summary_en TEXT,                   -- 英文摘要（当前未使用）
    rating INTEGER DEFAULT 0,          -- AI 初评 + 用户可手动修正（0-5 星）
    legacy_ai_rating INTEGER,          -- 历史 AI 自动评级备份
    rating_restored_from_legacy INTEGER DEFAULT 0, -- 是否已从历史 AI 评级恢复
    value_comment TEXT,                -- 评价
    qa_analysis TEXT,                  -- Q&A 深度阅读（Markdown 格式）
    recommendation_score INTEGER,      -- 个性化推荐分（0-100）
    recommendation_reason TEXT,        -- 推荐理由
    recommendation_interest_hash TEXT, -- 对应研究兴趣哈希
    recommendation_analyzed_at TEXT,   -- 推荐评分时间
    analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### schema_migrations 表
```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,       -- 连续迁移版本
    name TEXT NOT NULL UNIQUE,         -- 稳定迁移名称
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### task_logs 表
```sql
CREATE TABLE task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT NOT NULL,           -- daily_pipeline/fetch/analyze/generate/run/webdav_backup/email_report
    status TEXT NOT NULL DEFAULT 'running',  -- running/success/warning/error/skipped/interrupted
    message TEXT,
    detail TEXT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    duration_sec REAL
);
```

### task_log_steps 表
```sql
CREATE TABLE task_log_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_log_id INTEGER NOT NULL,       -- FK -> task_logs.id (CASCADE DELETE)
    step_key TEXT NOT NULL,             -- fetch/analyze/recommend/report/email/backup
    step_name TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',      -- pending/running/success/warning/error/skipped/interrupted
    message TEXT,
    detail TEXT,
    started_at TEXT,
    finished_at TEXT,
    duration_sec REAL,
    UNIQUE(task_log_id, step_key)
);
```

### 论文学习表
```sql
CREATE TABLE paper_chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,           -- FK -> users.id (CASCADE DELETE)，私有归属
    paper_id INTEGER NOT NULL,          -- FK -> papers.id (CASCADE DELETE)
    role TEXT NOT NULL,                 -- user/assistant
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE paper_quiz_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,           -- FK -> users.id (CASCADE DELETE)，私有归属
    paper_id INTEGER NOT NULL,          -- FK -> papers.id (CASCADE DELETE)
    mode TEXT NOT NULL,                 -- quick3/standard6/socratic
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE paper_quiz_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,       -- FK -> paper_quiz_sessions.id (CASCADE DELETE)
    position INTEGER NOT NULL,
    question TEXT NOT NULL,
    expected_points TEXT,              -- JSON 数组或 socratic 标记
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE paper_quiz_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,      -- FK -> paper_quiz_questions.id (CASCADE DELETE)
    answer_text TEXT NOT NULL,
    score INTEGER DEFAULT 0,           -- 0-5
    feedback_json TEXT,                -- correct/missing/misconception/improved_answer
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

删除论文时，以上学习记录会随 papers 外键级联删除；删除用户时，其私有学习记录随 users 外键级联删除。

### 论文阅读 Benchmark 表（v7）

```sql
-- 自管理任务路由（不进入共享 ai_tasks 设置）
CREATE TABLE benchmark_routes (
    task_key TEXT PRIMARY KEY,          -- benchmark_author/benchmark_judge/benchmark_judge_review
    provider_key TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
    is_thinking INTEGER DEFAULT 0, thinking_effort TEXT DEFAULT 'medium',
    temperature_enabled INTEGER DEFAULT 0, temperature REAL DEFAULT 0.2,
    max_tokens_enabled INTEGER DEFAULT 1, max_tokens INTEGER DEFAULT 2000,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 题库：draft → review → frozen → retired；冻结后不可修改，变更须克隆新版本
CREATE TABLE benchmark_suites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subset_name TEXT NOT NULL, version_label TEXT NOT NULL,  -- pprb-<subset>-YYYY.MM.DD-rN
    status TEXT NOT NULL DEFAULT 'draft',
    deep_reading_prompt TEXT NOT NULL DEFAULT '{}',          -- 冻结 Prompt 快照
    paper_chat_prompt TEXT NOT NULL DEFAULT '{}',
    suite_checksum TEXT NOT NULL DEFAULT '',
    scoring_revision TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(subset_name, version_label)
);

-- 论文快照：全文与哈希在冻结时固化，原论文删除不影响已冻结题库
CREATE TABLE benchmark_suite_papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suite_id INTEGER NOT NULL, paper_id INTEGER,             -- 原业务 papers.id，可空
    paper_key TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', authors TEXT NOT NULL DEFAULT '[]',
    source_type TEXT NOT NULL DEFAULT '', source_id TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '', full_text TEXT NOT NULL DEFAULT '',
    text_chars INTEGER DEFAULT 0, text_sha256 TEXT NOT NULL DEFAULT '',
    pdf_sha256 TEXT NOT NULL DEFAULT '', extractor_version TEXT NOT NULL DEFAULT '',
    position INTEGER DEFAULT 0,
    FOREIGN KEY (suite_id) REFERENCES benchmark_suites(id) ON DELETE CASCADE,
    UNIQUE(suite_id, paper_key)
);

-- 题目：轨道（deep_reading/chat）、参考答案、证据片段与逐项 rubric
CREATE TABLE benchmark_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suite_id INTEGER NOT NULL, paper_ref_id INTEGER NOT NULL,
    track TEXT NOT NULL, position INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '', reference_answer TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '[]',                     -- JSON 字符串数组，冻结前须能精确匹配全文
    rubric TEXT NOT NULL DEFAULT '{}',                       -- JSON {conditions:[{text,weight,critical}]}
    requires_reject INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',                  -- pending/accepted/rejected
    review_note TEXT NOT NULL DEFAULT '', reviewed_at TEXT,
    author_raw TEXT NOT NULL DEFAULT '{}',                   -- 出题原始结果与证据追溯
    FOREIGN KEY (suite_id) REFERENCES benchmark_suites(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_ref_id) REFERENCES benchmark_suite_papers(id) ON DELETE CASCADE
);

-- 运行：queued → running，持久化、可恢复；启动时遗留 queued/running 标记为 interrupted
CREATE TABLE benchmark_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suite_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'running',
    repeats INTEGER DEFAULT 1, max_calls INTEGER,            -- 候选调用硬预算，NULL 不限
    candidate_calls_made INTEGER NOT NULL DEFAULT 0,         -- 已预留的候选 API 尝试（含续写，不含裁判）
    runner_version TEXT NOT NULL DEFAULT '',
    active_scoring_revision TEXT NOT NULL DEFAULT '',         -- 当前报告采用的评分版本
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, finished_at TEXT,
    FOREIGN KEY (suite_id) REFERENCES benchmark_suites(id) ON DELETE CASCADE
);

-- 候选配置快照：不保存 API Key，config_hash 用于输出复用
CREATE TABLE benchmark_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, position INTEGER NOT NULL, label TEXT NOT NULL DEFAULT '',
    provider_key TEXT NOT NULL DEFAULT '', provider_name TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '', is_thinking INTEGER DEFAULT 0,
    thinking_effort TEXT DEFAULT 'medium', temperature_enabled INTEGER DEFAULT 0,
    temperature REAL DEFAULT 0.2, max_tokens_enabled INTEGER DEFAULT 1,
    max_tokens INTEGER DEFAULT 4000, config_hash TEXT NOT NULL DEFAULT '',
    actual_params TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE
);

-- 候选原始输出：UNIQUE 键保证恢复/重跑时输出复用（只重判不重跑）
CREATE TABLE benchmark_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL, paper_ref_id INTEGER NOT NULL,
    track TEXT NOT NULL, repeat_index INTEGER DEFAULT 0, round_index INTEGER,
    prompt_snapshot TEXT NOT NULL DEFAULT '[]', raw_output TEXT NOT NULL DEFAULT '',
    parsed TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'ok',
    finish_reason TEXT NOT NULL DEFAULT '', usage_json TEXT NOT NULL DEFAULT '{}',
    latency_ms REAL DEFAULT 0, continuation_count INTEGER DEFAULT 0,
    reused_from_response_id INTEGER, retry_count INTEGER NOT NULL DEFAULT 0,
    error_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES benchmark_candidates(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_ref_id) REFERENCES benchmark_suite_papers(id) ON DELETE CASCADE,
    FOREIGN KEY (reused_from_response_id) REFERENCES benchmark_responses(id) ON DELETE SET NULL,
    UNIQUE(candidate_id, paper_ref_id, track, repeat_index, round_index)
);

-- 判定：primary/review/system/human；人工覆盖优先级最高且不覆盖原始裁判记录
CREATE TABLE benchmark_judgments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, response_id INTEGER NOT NULL,
    judge_role TEXT NOT NULL,            -- primary/review/system/human
    judge_route_key TEXT NOT NULL DEFAULT '', scoring_revision TEXT NOT NULL DEFAULT '',
    case_id INTEGER NOT NULL, condition_scores TEXT NOT NULL DEFAULT '[]',
    score REAL NOT NULL DEFAULT 0,       -- 0-100；严重幻觉时该题封顶 60
    hallucination_critical INTEGER DEFAULT 0, confidence REAL, raw_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '', actor_user_id INTEGER, actor_username TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (response_id) REFERENCES benchmark_responses(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id) REFERENCES benchmark_cases(id) ON DELETE CASCADE,
    UNIQUE(response_id, judge_role, case_id, scoring_revision)
);

-- 裁判配置/Prompt 的独立版本；只重判时创建新版本，不覆盖旧判定
CREATE TABLE benchmark_scoring_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, revision_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    primary_route_key TEXT NOT NULL DEFAULT '', review_route_key TEXT NOT NULL DEFAULT '',
    primary_route_snapshot TEXT NOT NULL DEFAULT '{}', review_route_snapshot TEXT NOT NULL DEFAULT '{}',
    primary_prompt_snapshot TEXT NOT NULL DEFAULT '{}', review_prompt_snapshot TEXT NOT NULL DEFAULT '{}',
    error_json TEXT NOT NULL DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT, finished_at TEXT,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE,
    UNIQUE(run_id, revision_key)
);

-- 题目编辑/审核的追加式审计历史
CREATE TABLE benchmark_case_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL, suite_id INTEGER NOT NULL, revision INTEGER NOT NULL,
    action TEXT NOT NULL, actor_user_id INTEGER, actor_username TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (case_id) REFERENCES benchmark_cases(id) ON DELETE CASCADE,
    FOREIGN KEY (suite_id) REFERENCES benchmark_suites(id) ON DELETE CASCADE,
    UNIQUE(case_id, revision)
);
```

---

### users、audit_events 与私有归属（v4）

- `users` 保存小写唯一用户名、显示名、scrypt 哈希、member/admin 角色、启用状态、首次改密标记、session_version 和登录/创建/更新时间；唯一 admin 由索引、触发器与服务层共同保护，不可删除、停用、改名或降级。
- `audit_events` 保存 actor id、用户名快照、动作、目标、脱敏 JSON 元数据和时间；删除用户后保留事件及用户名快照。
- `reading_list` 唯一键为 `(user_id, paper_id)`；`paper_chat_messages` 和 `paper_quiz_sessions` 直接带 user_id，题目和作答通过 session 归属。
- `papers.imported_by_user_id` 可空，用户删除时置空；feed 论文及历史系统任务为空。
- `ai_usage_logs.user_id` 可空，定时任务/历史记录视为系统调用。
- 删除成员级联清理其私有学习数据；停用只递增会话版本，不删除数据。
- 删除含私有学习记录的论文默认返回 409 及影响统计，只有 admin 显式 `force=true` 才级联；批量删除跳过并报告。

公有论文数据（论文、分析、评分、标签、深度阅读、报告、全局推荐）同成员私有学习数据共用 SQLite，通过表归属和授权逻辑隔离，以保留外键、事务与原子备份。完整术语见 `CONTEXT.md`，决策见 `docs/adr/`。

## 4. 核心数据流

### 4.1 论文抓取流程
```
source.ingestion.fetch_latest_papers(days)   # 默认 1 天；定时日报传 schedule.fetch_days
  → fetch_batch() 按 batch_days 分批（请求节奏/进度粒度）
  → _fetch_date_range() 按分类查询
    → 查询带 submittedDate 日期窗口过滤（GMT 分钟精度）+ cat:xxx
    → 按 submittedDate 降序翻页取完窗口内论文（单查询上限 30000 条）
    → 代码内 [start, end) 日期过滤兜底（分钟截断/秒级边界）
  → 去重：seen_ids 内存去重 + paper_exists() 数据库去重
  → insert_paper() 写入 papers 表
  → 返回新增论文列表
```
滚动窗口重叠（每日窗口再次覆盖前几天的论文）+ 入库去重，天然支持抓取失败后由下一次运行自动补抓。

### 4.2 AI 分析流程
```
source.analysis.analyze_pending_papers(limit, concurrency)
  → get_unanalyzed_papers() 获取未分析论文
  → ThreadPoolExecutor 并发执行 analyze_paper()
    → get_ai_task_config("basic_analysis") 获取基础分析模型与参数
    → get_prompt_profile("basic_analysis") 获取稳定 Prompt 前缀
    → 论文标题/作者/摘要作为独立 JSON message 放在最后
    → OpenAI chat.completions.create()
    → 解析 JSON 响应：{tags, summary_cn, value_comment}
  → insert_analysis() 写入 analysis 表（含重复检查）

source.analysis.analyze_paper_full(paper_data)
  → get_ai_task_config("deep_reading") 获取深度阅读模型与参数
  → get_paper_full_text(max_chars=None) 下载 PDF 并提取全文（不截断）
  → system + 动态论文全文 JSON message + 稳定 instruction（长输入任务指令后置，见 7.4）
  → OpenAI chat.completions.create()
  → 解析 JSON 响应：{qa_analysis}
  → 按当前 Prompt 中的 Q 编号校验完整性；截断或缺题时最多自动续写一次
  → 补全后仍不完整则返回 warning，保留数据库中已有 qa_analysis
  → update_analysis() 仅写入 qa_analysis，不覆盖基础分析字段
```

### 4.3 论文学习流程
```
GET /paper/<arxiv_id>/chat
  → 展示自由讨论、3/6题练习、苏格拉底追问

POST /api/paper/<arxiv_id>/chat/messages
  → get_paper_chat_messages(limit=12) 读取最近历史
  → chat_about_paper()
    → get_learning_paper_text()
      → 优先检查 data/pdf_cache/<arxiv_id>.pdf
      → 未命中才调用 download_pdf()
      → PDF 下载/提取失败回退 abstract
    → build_paper_learning_messages()
      → system → 稳定任务说明 → 稳定论文上下文 → 历史/当前问题
    → paper_chat 任务模型
  → 保存 user/assistant 消息

POST /api/paper/<arxiv_id>/quiz/sessions
  → paper_quiz 任务模型生成 quick3/standard6 题目
  → 保存 session/questions

POST /api/paper/<arxiv_id>/quiz/questions/<question_id>/answer
  → paper_quiz 任务模型评分并返回 correct/missing/misconceptions/improved_answer
  → 保存 answer/feedback

POST /api/paper/<arxiv_id>/socratic/sessions
POST /api/paper/<arxiv_id>/socratic/sessions/<session_id>/reply
  → paper_quiz 任务模型根据历史连续追问
```

### 4.5 论文阅读 Benchmark 流程（v7）
```
GET /benchmark（admin）→ 选论文 → POST /api/benchmark/drafts 创建草稿
  → create_draft()：get_paper_by_key + _extract_full_text（失败阻止冻结，不回退摘要）
      + get_prompt_profiles() 复制 deep_reading/paper_chat Prompt 为不可变快照
  → POST .../generate：返回 HTTP 202/task_id，单 worker 异步执行每篇论文 1 次出题调用
      （benchmark_author 路由）
      → 生成 6 个深度阅读参考答案/rubric/证据 + 3 轮交流脚本
      → 证据须能精确匹配冻结全文（normalize_text：NFKC/断行连字/括号空白归一）
      → 全部失败自动驳回；部分匹配标记警告（pending 人工复核）；通过后 pending 待审
  → 逐题 review_case / 批量 review_all_cases（accept/reject）
      → 冻结前可编辑题目，编辑重新校验证据/rubric，并追加 case revision 审计记录
  → freeze_suite()：校验全文、证据、rubric、题量覆盖后置为 frozen 并写入校验和
  → POST .../runs：返回 HTTP 202/task_id/run_id，先持久化 queued，再由单 worker 原子 claim 为 running
      → 创建运行前拒绝放不下未复用候选工作的预算；每次候选 API 尝试（含深读续写与临时重试）
        原子递增 `candidate_calls_made`，裁判调用不占用该预算；耗尽后 resume 只能严格上调 `max_calls`
      → 网络/timeout/429/5xx 最多额外重试 2 次；仍失败保存 retry_failed，作为基础设施失败等待恢复，不计为候选 0 分
      → 按 suite checksum、runner version、candidate config hash 和响应槽精确复用已保存响应，失败响应不复用
      → 交流轨每轮用候选自身历史；深度阅读输出按文本级 `### Qn:` 解析
  → judge_run()：主裁判按（候选, 论文, 轨道）批量评分，缺题计零（system 判定）
      → 复核裁判抽样（每候选每轨道 ≥10% 下限 1 组，并完整覆盖异常组与同供应商候选）
      → 裁判 kind 重命名时按位置对齐兜底
  → POST .../rejudge：只对已保存候选输出建立新的 scoring revision，不增加候选调用数；成功后切换 active 版本
      → 人工覆盖必须提交完整 `condition_scores`，按冻结 rubric 权重计算并保留 actor 快照
  → get_report()：双轨分榜、逐题明细（精确 `response_id`/`case_id`、主裁判/复核分）、
      幻觉/缺题计数、token/延迟/续写、pilot 未校准警告、同家族偏置提示、人工校准统计
      → 主/复核条件级冲突标记 `needs_human_review=true`，该题分数为 null 且不进入轨道/榜单聚合
        （若该轨道所有题均冲突，轨道分数为 null），直到人工覆盖；新运行固定保存
        `RUNNER_VERSION=pprb-runner-v7`，历史 `runner_version=''` 原样保留并在报告 warning 中提示
```

### 4.6 配置说明（benchmark 自管理路由）
- `benchmark_author` / `benchmark_judge` / `benchmark_judge_review` 三个任务路由存在
  `benchmark_routes` 表，不进入共享 `ai_tasks`；供应商凭据仍从 settings.json 的
  `providers` 解析（`resolve_model_config()` 复用归一化与 `build_chat_completion_kwargs()`）
- 路由默认值在 `source/benchmark/config.py` 的 `DEFAULT_BENCHMARK_ROUTES`；出题/裁判
  Prompt 为代码常量（`source/benchmark/prompts.py`），v1 修改 Prompt 需改代码
- 候选模型配置与路由同构（provider_key/model/思考开关/强度/输出上限），运行前
  `build_chat_completion_kwargs` 校验可构建并快照 actual_params；`config_hash` 与 suite checksum、
  runner version 一起用于跨运行响应复用去重，快照不保存 API Key 或 messages

### 4.4 定时任务流程
```
APScheduler cron(day_of_week, hour, minute)
  → daily_pipeline()
    → 初始化 task_logs + 六条 task_log_steps
    → fetch_latest_papers(days=schedule.fetch_days)
      # 抓取阶段异常时按 schedule.fetch_retry_interval_minutes 等待重试，
      # 最多 schedule.fetch_max_retries 次；仅作用于定时日报
    → analyze_pending_papers(limit=schedule.analyze_limit)
    → 按研究兴趣补齐推荐评分（未设置时 skipped）
    → generate_report_content(latest_date) + save_report()
      # Web 日报包含截至报告日最近 7 个有论文日期的标签走势、新标签和推荐分分布
    → send_report_email()  # 未启用/已发送时 skipped；失败记 warning
    → run_webdav_backup()  # 未启用时 skipped；失败记 warning
    → finish_task_log(success/warning/error)
```

应用启动时会将上一次进程遗留的 `running` 日志改为 `interrupted`。定时日报和 `/api/run` 共享非阻塞互斥锁；自动冲突记为 `skipped`，手动冲突返回 HTTP 409。

---

## 5. 配置系统

### 5.1 source/config.py（硬编码，需改代码）
- `ARXIV_CATEGORIES` — 监控的 arXiv 分类
- `TAG_CANDIDATES` — AI 标签候选列表
- `RATING_CRITERIA` — AI 基础分析评级标准（0-5 星校准锚点）
- `ANALYSIS_CONCURRENCY` — 默认并发数
- `SCHEDULE_HOUR/MINUTE` — 定时任务首次默认时间；运行后以 `settings.json` 的 `schedule` 为准
- `WEB_HOST/PORT` — Web 服务地址

### 5.2 data/settings.json（运行时，Web界面可改）
```json
{
  "settings_schema_version": 4,
  "concurrency": 5,
  "session_secret": "随机生成的 Flask session 签名密钥",
  "personalization": {"research_interests": "用户研究兴趣"},
  "webdav_backup": {
    "enabled": false,
    "url": "https://example.com/remote.php/dav/files/user",
    "username": "webdav用户名",
    "password": "webdav密码或应用密码",
    "remote_dir": "arxiv-backups",
    "history_days": 3
  },
  "email_report": {
    "enabled": false,
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "security": "starttls",
    "username": "user@example.com",
    "password": "SMTP密码或授权码",
    "sender": "user@example.com",
    "recipients": ["reader@example.com"],
    "subject_template": "AI 论文日报 {date} - {paper_count} 篇论文",
    "site_url": "https://your-domain.example",
    "important_score_threshold": 80,
    "overview_limit": 20
  },
  "schedule": {
    "enabled": true,
    "days_of_week": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "hour": 10,
    "minute": 0,
    "fetch_days": 3,
    "analyze_limit": 1000,
    "fetch_retry_interval_minutes": 10,
    "fetch_max_retries": 20
  },
  "providers": {
    "deepseek": {
      "name": "DeepSeek",
      "api_key": "sk-xxx",
      "base_url": "https://api.deepseek.com",
      "available_models": ["deepseek-chat", "deepseek-reasoner"]
    }
  },
  "prompts": {
    "system_prompt": "...",
    "user_prompt": "...{title}...{authors}...{abstract}...{tag_candidates}..."
  },
  "prompt_profiles": {
    "paper_import": {"system": "...", "instruction": "...从 PDF 提取可编辑元数据..."},
    "basic_analysis": {"system": "...", "instruction": "...{tag_candidates}..."},
    "deep_reading": {"system": "...", "instruction": "..."},
    "report_summary": {"system": "...", "instruction": "..."},
    "recommendation": {"system": "...", "instruction": "...返回 recommendation_score/recommendation_reason..."},
    "paper_chat": {"system": "...", "instruction": "...多轮讨论..."},
    "paper_quiz": {"system": "...", "instruction": "...主动回忆/评分/追问..."}
  },
  "ai_tasks": {
    "basic_analysis": {"provider_key": "deepseek", "model": "deepseek-chat", "temperature_enabled": true, "temperature": 0.2, "is_thinking": false, "max_tokens_enabled": true, "max_tokens": 1200},
    "paper_import": {"provider_key": "deepseek", "model": "deepseek-chat", "temperature_enabled": true, "temperature": 0.1, "is_thinking": false, "max_tokens_enabled": true, "max_tokens": 1000},
    "deep_reading": {"provider_key": "deepseek", "model": "deepseek-reasoner", "temperature_enabled": false, "temperature": 0.2, "is_thinking": true, "thinking_effort": "high", "max_tokens_enabled": true, "max_tokens": 6000},
    "report_summary": {"provider_key": "deepseek", "model": "deepseek-chat", "temperature_enabled": true, "temperature": 0.3, "is_thinking": false, "max_tokens_enabled": true, "max_tokens": 1000},
    "recommendation": {"provider_key": "deepseek", "model": "deepseek-chat", "temperature_enabled": true, "temperature": 0.2, "is_thinking": false, "max_tokens_enabled": true, "max_tokens": 500},
    "paper_chat": {"provider_key": "deepseek", "model": "deepseek-reasoner", "temperature_enabled": false, "temperature": 0.4, "is_thinking": true, "thinking_effort": "high", "max_tokens_enabled": true, "max_tokens": 4000},
    "paper_quiz": {"provider_key": "deepseek", "model": "deepseek-reasoner", "temperature_enabled": false, "temperature": 0.3, "is_thinking": true, "thinking_effort": "high", "max_tokens_enabled": true, "max_tokens": 3000}
  }
}
```

**source/settings 公共函数:**
- `load_settings()` / `save_settings()` — 读写JSON（含自动迁移）
- `get_ai_config()` — 兼容接口，返回 `basic_analysis` 功能路由的实际调用配置
- `get_ai_task_config(task_key)` — 获取某个 AI 功能的实际供应商、模型和参数配置
- `resolve_ai_task_config(task_key, task_config)` — 将已保存或未保存的功能路由草稿与供应商连接凭据合并并校验
- `get_ai_tasks()` / `save_ai_tasks()` — 获取/保存 PDF 元数据提取、基础分析、深度阅读、报告导读、个性化推荐、论文对话、论文问答练习的模型路由
- `build_chat_completion_kwargs()` — 统一构建 Chat Completions 参数；功能路由只支持可选 Temperature 和输出长度，未启用 Temperature 或使用思考模型时不发送 Temperature，其他采样参数不发送并交给模型采用默认行为
- LLM 客户端必须通过 `source.analysis.get_openai_client()` 创建，以复用全局代理配置并禁用环境变量代理
- `normalize_provider_connection()` — 归一化供应商连接字段；`normalize_provider_config()` 仅用于合并后的实际调用配置
- `get_prompt_profile()` / `get_prompt_profiles()` — 获取任务级 Prompt Profile；`get_prompts()` 保留旧接口兼容
- `get_concurrency()` — 获取并发数
- `get_per_page()` — 获取每页论文数
- `get_schedule_config()` / `save_schedule_config()` — 获取/保存内置日报的星期、时间、回看天数、分析上限和抓取失败重试策略
- `get_fetch_config()` / `save_fetch_config()` — 抓取配置（请求间隔、批次天数、批次间隔）
- `get_proxy_config()` / `save_proxy_config()` — 代理配置
- `get_personalization_config()` / `save_personalization_config()` — 个性化推荐研究兴趣
- `get_webdav_backup_config()` / `save_webdav_backup_config()` — WebDAV 云备份配置；GET 给前端时必须脱敏密码
- `get_email_report_config()` / `save_email_report_config()` / `update_email_report_status()` — 每日报告邮件配置，含 `important_score_threshold`（重点精读推荐分阈值，默认 80，0-100）与 `overview_limit`（速览上限，默认 20，0-50）；GET 给前端时必须脱敏 SMTP 密码；`last_sent_report_date` 只记录自动任务成功发送的日报日期
- `add/remove/update_provider()` — 供应商连接 CRUD；被功能路由引用时禁止删除
- 账号凭据由 `source.storage.users` 管理；`authenticate_user/create_member/change_password/reset_member_password/set_member_enabled/delete_member` 不再通过 settings 保存密码
- `get_session_secret()` — 获取/生成持久 Flask session 签名密钥

> **配置合并：** `source/settings/store.py` 使用递归 deep merge；新增普通顶层字段无需维护白名单。需要归一化、迁移或秘密保留语义的字段，仍应在 normalize/store 中显式处理并补回归测试。

### 5.3 静态文件缓存（source/web/application.py）

- `build_app()` 中 `SEND_FILE_MAX_AGE_DEFAULT=86400` 控制 `/static/*` 的 `Cache-Control: max-age=86400`（Flask 默认 `no-cache`），配合 Cloudflare 隧道/CDN 的边缘缓存
- 全项目无 `send_file/send_from_directory` 调用，该配置只影响静态路由；动态页面响应带 `Set-Cookie`，Cloudflare 不会缓存
- 静态文件文件名不带 hash，改动 CSS/JS 后需在 Cloudflare 后台 Purge 对应 URL 或等待缓存过期

---

## 6. API 端点清单

### 页面路由
| 路由 | 说明 |
|------|------|
| `GET /` | 首页（论文列表） |
| `GET /browse` | 分类浏览（多条件筛选） |
| `GET /search?q=` | 搜索 |
| `GET /paper/<arxiv_id>` | 论文详情（含编辑） |
| `GET /paper/<arxiv_id>/chat` | 论文学习页（对话/问答/苏格拉底追问） |
| `GET /about` | 公开项目宣传页（首页提供入口） |
| `GET /vision` | 实验室科研情报基础设施愿景页（仅直接访问） |
| `GET /settings` | 设置页（含独立定时任务标签） |
| `GET /tasks` | 成员手动导入页；admin 另见抓取、批处理与日报 |

### 任务 API
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/fetch` | POST | 抓取论文 |
| `/api/analyze` | POST | AI分析（?limit=50） |
| `/api/generate` | POST | 生成报告 |
| `/api/run` | POST | 抓取、分析、推荐并生成报告；流水线冲突返回 409 |

### 论文 API
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/papers` | GET | 论文列表JSON |
| `/api/tags` | GET | 标签列表 |
| `/api/stats` | GET | 统计信息 |
| `/api/paper/<id>/analysis` | PUT | 更新论文分析 |
| `/api/paper/<id>/hide` | POST | 隐藏论文 |
| `/api/paper/import/preview` | POST | 解析论文链接或上传 PDF，返回可编辑元数据，不入库 |
| `/api/paper/import` | POST | 确认手动导入，可选基础分析和深度阅读 |
| `/api/paper/<id>/pdf` | POST | 为已有论文上传或替换本地 PDF |
| `/api/paper/<id>/unhide` | POST | 取消隐藏 |
| `/api/paper/<id>` | DELETE | 删除论文 |
| `/api/paper/<id>/reanalyze` | POST | 重新AI分析 |
| `/api/paper/<id>/todo/status` | GET | 检查阅读清单状态 |

### 论文学习 API
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/paper/<id>/chat/messages` | GET | 读取自由讨论历史 |
| `/api/paper/<id>/chat/messages` | POST | 发送讨论消息并保存模型回复 |
| `/api/paper/<id>/quiz/sessions` | POST | 创建 quick3/standard6 练习并生成题目 |
| `/api/paper/<id>/quiz/sessions/<session_id>` | GET | 读取练习题、答案和反馈 |
| `/api/paper/<id>/quiz/questions/<question_id>/answer` | POST | 提交单题答案并返回评分反馈 |
| `/api/paper/<id>/socratic/sessions` | POST | 创建独立苏格拉底追问会话 |
| `/api/paper/<id>/socratic/sessions/<session_id>/reply` | POST | 提交回答并返回反馈和下一问 |

### 设置 API
| 端点 | 方法 | 说明 |
|------|------|------|
| `/login` | GET | 用户名/密码登录页 |
| `/api/auth/status` | GET | 当前 principal、角色、首次改密状态和 CSRF token |
| `/api/auth/login` | POST | 用户名、密码登录（含 CSRF） |
| `/api/auth/logout` | POST | 退出登录（含 CSRF） |
| `/api/providers` | GET/POST | 供应商列表/添加 |
| `/api/providers/<key>` | PUT/DELETE | 更新/删除供应商 |
| `/api/providers/presets` | GET | 预设供应商列表 |
| `/api/providers/models` | POST | 从供应商 API 自动获取模型列表 |
| `/api/prompts` | GET/POST | 读取/保存Prompt |
| `/api/settings/ai-tasks` | GET/POST | 读取/保存 AI 功能模型路由 |
| `/api/settings/ai-tasks/<task_key>/test` | POST | 使用未保存草稿测试指定功能路由，并返回思考能力提示 |
| `/api/settings/ai-usage` | GET | 查看近期 LLM token 用量 |
| `/api/settings/personalization` | GET/POST | 读取/保存研究兴趣 |
| `/api/recommendations/recalculate` | POST | 手动重算个性化推荐评分 |
| `/api/settings/webdav-backup` | GET/POST | 读取/保存 WebDAV 云备份配置（GET 不返回明文密码） |
| `/api/backup/webdav/run` | POST | 手动立即执行 WebDAV 备份 |
| `/api/settings/email-report` | GET/POST | 读取/保存每日报告邮件配置（GET 不返回明文密码） |
| `/api/email-report/test` | POST | 使用最近一份日报告测试发送邮件 |
| `/api/settings/concurrency` | POST | 保存并发数 |
| `/api/settings/schedule` | GET/POST | 读取/保存内置日报的星期、时间、抓取天数、分析上限和抓取失败重试策略 |
| `/api/db/info` | GET | 数据库信息 |
| `/api/account/password` | POST | 当前用户修改密码 |
| `/api/admin/password` | POST | admin 改密兼容别名（保留一个版本） |
| `/api/users` | GET/POST | admin 查看数量汇总/邀请创建成员 |
| `/api/users/<id>/enabled` | POST | admin 启停成员 |
| `/api/users/<id>/reset-password` | POST | admin 重置成员临时密码 |
| `/api/users/<id>` | DELETE | admin 永久删除成员及其私有数据 |
| `/api/audit-events` | GET | admin 分页读取脱敏审计 |
| `/account/password` | GET | 首次改密阻断页 |

### 任务日志 API
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tasks/stats` | GET | 任务统计 |
| `/api/tasks/logs` | GET | 日志列表（?task=&page=），日报日志附带 `steps` |
| `/api/tasks/scheduled` | GET | 内置日报配置、时区、下次执行与最近运行 |
| `/api/tasks/clear` | POST | 清理旧日志（?keep_days=30） |

### 论文阅读 Benchmark API（admin 专属）
| 端点 | 方法 | 说明 |
|------|------|------|
| `/benchmark` | GET | Benchmark 管理页 |
| `/api/benchmark/suites` | GET | 题库列表 |
| `/api/benchmark/papers?q=` | GET | 可选论文列表 |
| `/api/benchmark/drafts` | POST | 创建题库草稿 {subset_name, paper_keys} |
| `/api/benchmark/suites/<id>` | GET | 题库详情（论文/题目/运行记录） |
| `/api/benchmark/suites/<id>/generate` | POST | 排队自动出题，返回 202/task_id（SSE 进度） |
| `/api/benchmark/cases/<id>/review` | POST | 人工审核/编辑 `{edits, decision, note}`，追加审计历史 |
| `/api/benchmark/suites/<id>/review-all` | POST | 批量审核 |
| `/api/benchmark/suites/<id>/freeze` | POST | 冻结题库 |
| `/api/benchmark/suites/<id>/estimate` | GET | 调用量估算 ?candidates=&repeats= |
| `/api/benchmark/suites/<id>/runs` | POST | 排队评测运行，返回 202/task_id/run_id（SSE 进度） |
| `/api/benchmark/runs/<id>` | GET | 运行详情 |
| `/api/benchmark/runs/<id>/resume` | POST | 排队恢复中断/失败运行，可选 `{max_calls}` 且只能严格上调 |
| `/api/benchmark/runs/<id>/report` | GET | 双轨分榜报告 |
| `/api/benchmark/runs/<id>/rejudge` | POST | 排队只重判，返回 202；不增加候选调用量 |
| `/api/benchmark/judgments` | POST | 人工覆盖，必须提交完整 `condition_scores` |
| `/api/benchmark/routes` | GET | 自管理任务路由（无凭据） |
| `/api/benchmark/routes/<task_key>` | POST | 保存任务路由 |
| `/api/benchmark/progress/<task_id>` | GET | SSE 进度 |

---

## 7. 开发规范

### 7.1 添加新功能的步骤
1. 如果涉及新数据库表/字段 → 在 `source/storage/migrations.py` 注册下一个连续版本的迁移函数
2. 如果涉及新 API → 在对应的 `source/web/*_api.py` Blueprint 添加路由函数
3. 如果涉及新页面 → 创建 `templates/xxx.html`，在 `source/web/pages.py` 添加页面路由
4. 如果涉及新样式 → 按 `templates/README.md` 的加载矩阵选择 `static/css/core.css`、`components.css`、`rich-text.css` 或 `pages/*.css`；独立宣传页继续使用 `static/promo.css` 和 `.promo-*` 命名空间
5. 更新 `AGENTS.md` 记录变更

### 7.2 数据库迁移模式
`init_db()` 只负责启动顺序迁移器。新增 schema 变更时，在
`source/storage/migrations.py` 的 `MIGRATIONS` 末尾注册连续版本，并把实际
DDL/数据整理放入独立迁移函数。每个版本由迁移器在单独事务中执行并写入
`schema_migrations`；变更前会创建 SQLite 一致性快照，失败时回滚并中止启动。

所有业务数据库访问都使用 `with get_connection() as conn:`。该兼容式托管连接
退出时会提交或回滚并关闭，且统一启用 WAL、外键和 5000 ms `busy_timeout`。

### 7.3 添加新供应商
在 `source/settings/defaults.py` 的 `PROVIDER_PRESETS` 字典中添加：
```python
"new_provider": {
    "name": "显示名称",
    "base_url": "https://api.example.com/v1",
    "models": ["model-1", "model-2"],
}
```

供应商运行时配置只保存 `name`、`api_key`、`base_url` 和 `available_models`。模型、输出长度、Temperature 采样控制以及思考模式全部保存在 `ai_tasks` 的具体功能路由中；未启用 Temperature 时不发送该参数，其他采样参数不发送并采用模型默认行为。旧版供应商推理字段会在读取时迁移到显式任务路由。

调用模型时必须通过 `build_chat_completion_kwargs()` 构建参数，不要在业务代码中直接固定传 `temperature` 或 `max_tokens`；保持可选参数的省略语义，让模型在未配置采样控制时使用自身默认值。

`users` 是凭据唯一真源，系统只有固定用户名 `admin` 的唯一管理员。v4 迁移复制旧 settings 管理哈希，提交后备份配置并逐字段移除旧凭据；没有旧凭据时临时密码仅由实际创建 admin 的进程输出一次。session 保存 `user_id/session_version`、30 天滑动有效，逐请求校验用户存在/启用/版本；改密、重置、停用或删除立即撤销旧会话。路由必须明确归为 public/member/admin，所有 cookie 非安全方法校验 CSRF。私有查询只接受当前 principal 注入的 user_id。`GET /api/providers` 只能返回 `api_key_masked`。

主页保持公开浏览；访客显示登录入口，已登录用户显示显示名和明确的 `@username`，并提供普通账号改密和退出入口。新密码策略由 `source.storage.users.PASSWORD_MIN_LENGTH` 统一提供，当前最低 8 个字符，模板通过认证上下文使用 `password_min_length`；现有密码和一次性临时密码不迁移、不缩短。`static/auth.js` 的 `window.AuthUI` 负责密码显隐和退出辅助，必须保留全局 fetch 的 CSRF 包装。

登录限流按来源 IP 和规范化用户名分别计数：滚动 15 分钟内 5 次失败后第 6 次返回 429，并提供 JSON `retry_after` 与 `Retry-After`；成功登录清理当前 IP/用户名失败桶。计数是单 Flask 进程内存状态，重启清空，多 worker 不共享，默认不信任客户端 `X-Forwarded-For`；登录页用 `retry_after` 显示等待倒计时。

### 7.4 修改 Prompt
- 新版 prompt 主要存储在 `data/settings.json` 的 `prompt_profiles` 字段，按 `paper_import`、`basic_analysis`、`deep_reading`、`report_summary`、`recommendation`、`paper_chat`、`paper_quiz` 拆分
- 旧版 `prompts.system_prompt/user_prompt` 保留为兼容字段，并映射到 `deep_reading`
- 基础分析 Prompt Profile 可使用 `{tag_candidates}`、`{rating_criteria}`，并返回 `tags`、`rating`、`summary_cn`、`value_comment`；深度阅读只描述 Q&A 输出；论文标题、作者、摘要、PDF 全文由后端作为独立 JSON message 传入
- 修改 AI 调用逻辑时不要重新把动态论文内容拼回稳定 instruction，否则会降低 prompt cache 命中率
- 长输入任务（`deep_reading`、`paper_import`，输入含数万 token PDF 全文）的消息顺序固定为 `system → 动态论文全文 JSON message → 稳定 instruction`：指令放在长文本之后（输入末尾）可避免被上下文“淹没”导致 flash 类模型漏答；短输入任务（基础分析、推荐、报告导读）保持 `system → instruction → 动态数据` 以保留更长的稳定前缀缓存。两种顺序都由 `_build_task_messages()` 统一构建，不要手工拼消息
- 深度阅读按质量优先读取完整 PDF 全文；基础分析通常只使用摘要，无摘要的手动论文可回退到 PDF 前 50000 字符
- 深度阅读必须校验当前 Prompt 声明的所有 `### Qn:`；自动补全最多调用一次，补全后仍不完整时禁止覆盖已有 `qa_analysis`
- 论文详情页和学习页的模型富文本统一通过 `static/rich_text.js` 的 `RichText.render()` / `RichText.renderMath()` 渲染，不要在模板中复制 Markdown 或清洗逻辑
- 论文学习功能必须通过 `build_paper_learning_messages()` 构造消息，保持 `system → 稳定任务说明 → 稳定论文上下文 → 动态历史/用户输入` 的顺序；不要把时间戳、session id、当前问题等易变内容放进稳定论文上下文
- 论文学习 PDF 文本必须通过 `get_learning_paper_text()` 获取，优先复用 `data/pdf_cache/<arxiv_id>.pdf`，未命中才下载，失败时回退摘要

### 7.5 添加新标签
在 `source/config.py` 的 `TAG_CANDIDATES` 列表中添加。注意：
- 避免过于宽泛的标签（如 "Transformer"、"LLM"）
- 优先使用具体的技术方法名称

### 7.6 arXiv API 注意事项
- **`submittedDate` 是官方日期过滤字段**：格式 `submittedDate:[YYYYMMDDTTTT TO YYYYMMDDTTTT]`（24 小时制、GMT、分钟精度），如 `cat:cs.RO AND submittedDate:[202608060000 TO 202608070000]`。抓取按日期窗口查询 + 翻页取完窗口内论文；代码内 `[start, end)` 日期过滤保留作为分钟截断与秒级边界的兜底
- **查询上限**：单查询 `max_results` 上限 30000 条，分片返回；窗口内论文数由日期过滤天然限定
- **请求节奏**：官方建议连续调用间隔 ≥ 3 秒（`request_delay` 默认 5）；过快请求会触发 429 软限流，需保持批次间 `batch_delay` 节奏
- **分类查询**：`cat:cs.RO` 匹配分类列表含 cs.RO 的论文（含二级分类），比 `primary_category:cs.RO` 更可靠；抓取不按主分类过滤
- **分批抓取**：大批量抓取时使用 `fetch_batch()` 自动分批，避免单次请求过大
- **时区问题**：arXiv 返回的 `published` 是带 UTC 时区的 datetime，比较时必须使用 `datetime.now(timezone.utc)`，否则报 `can't compare offset-naive and offset-aware datetimes`

### 7.7 CSS 样式约定
- 组件样式使用 kebab-case：`.paper-card`、`.qa-item`
- 状态样式使用前缀：`.log-success`、`.log-warning`、`.log-error`、`.log-skipped`、`.log-interrupted`、`.log-running`
- 响应式断点：`@media (max-width: 768px)`

### 7.8 运行时数据操作规范（data/ 目录）
- `data/` 下文件（`settings.json`、`*.db`、`pdf_cache/`、`paper_files/`）是 git 不追踪的运行时状态，不可再生；账号哈希和私有学习数据在数据库，`settings.json` 含 API key 和 session secret
- **禁止**以"同步默认值/现值"为由重建或整体改写 `data/settings.json`，禁止用默认模板覆盖文件
- 修改运行时配置的唯一正道：设置页对应 API（`/api/providers/*`、`/api/settings/*`、`/api/account/password`）；AI 代理必须通过 API 修改，或逐字段编辑（保留其余字段）——逐字段编辑前必须先备份到 `data/settings-backup/`，编辑后向用户声明 diff 并运行守卫测试
- 涉及 `data/` 的执行计划，完成清单必须包含"确认 settings.json 未被重建"：运行 `python scripts/check_settings_guardrail.py`（退出码 0=OK、1=疑似重建），或检查启动日志中的重建告警
- 守卫机制三层：① `tests/test_settings_guardrail.py` 只测判定逻辑本身（构造数据，不读真实文件，CI/本地一致）；② `create_app()` 启动时对真实文件告警记 `logger.warning`（只写日志、不阻断服务启动，systemctl 下见 `journalctl -u <服务名>`）；③ `scripts/check_settings_guardrail.py` 显式检查真实文件（仅只读，可挂 cron 或进计划清单）

### 7.9 SSE 进度注册表
- `source/web/progress.py` 的内部任务键必须包含当前 `user_id` 与客户端 `task_id`；不同用户可同时使用同一 task_id，调用方不得自行拼接用户标识实现隔离
- `completed` / `error` 终态保留 600 秒供 SSE 消费和短暂重连，后续读写时懒清理；`running` 等非终态不使用该 TTL，避免误删长任务
- SSE 读取必须从当前 principal 注入 `user_id`，不得接受客户端 user id；`update_progress()` / `get_progress()` 的接口和事件 JSON 不暴露内部复合键

### 7.10 Benchmark 深模块约定（v7）
- 外部只允许通过 `source/benchmark` 公共接口操作（create_draft/generate_cases/review_case/
  freeze_suite/start_run/resume_run/rejudge_run/edit_case/get_report/set_human_judgment/路由配置）；读取题库论文与运行状态
  使用 `list_suite_papers(suite_id)`、`list_runs(suite_id=None)`，启动时遗留运行由
  `mark_interrupted_runs()` 统一处理（queued/running 均会标记为 interrupted）；Web 层不得
  直接拼接裁判 Prompt 或操作 benchmark SQL
- 题库冻结后不可原地修改（review/generate 抛 BenchmarkError）；变更必须克隆新版本
  （create_draft 自动递增 `pprb-<subset>-YYYY.MM.DD-rN`）
- 证据匹配必须走 `evidence.normalize_text()`（NFKC + 断行连字符 + 括号/标点空白归一，
  匹配两侧同一变换）；冻结要求证据非空且逐题已人工确认，题目覆盖冻结 Prompt 全部
  `### Qn:` 与 3 轮交流
- 深度阅读输出解析是文本级的（`runner._qa_sections_from_content`：转义换行展开 +
  `### Qn:` 标题），不依赖 JSON 完整性；模型输出可用 `json_support._clean_json_content`
  与 `_extract_first_json_object` 兜底修复
- 运行按（候选, 论文, 轨道, 重复槽, 轮次）唯一键保存响应，`get_existing_response` 跳过
  已保存项；跨运行复用必须同时匹配 suite checksum、runner version、candidate config hash 和槽位，
  retry_failed/未完成响应不复用，克隆复用不增加候选调用计数
- v7 `max_calls` 仅限制候选 API 尝试（含续写与额外重试），启动前校验未复用的完整候选工作量，
  每次尝试通过持久化原子预留计数；耗尽后恢复只允许严格提高上限；裁判重试不计入该预算
- 出题、运行、恢复和重判的 Web 入口均为后台单 worker 任务，API 返回 202；进程重启时 queued/running
  运行标记为 interrupted，必须人工恢复，已保存响应和调用计数不丢失
- 裁判判定优先序：human > review > system（缺题计零）> primary；严重幻觉该题封顶 60 分；
  主/复核条件级分歧等待人工且不计入榜单；报告逐题保留 `response_id`、`case_id`、
  `primary_score`、`review_score`、`needs_human_review`；同家族偏置与非等预算比较必须出现在报告 warnings
- 裁判使用独立 scoring revision；更换裁判只重判已有响应，旧 judgment 保留，完成后才切换 active 版本。
  人工 `condition_scores` 必须覆盖冻结 rubric 的每个条件，服务端按权重计算分数并保存 actor 快照。
- 新建运行固定保存非空 `source.benchmark.RUNNER_VERSION=pprb-runner-v7`；历史空版本不回填，只在报告中提示 provenance warning

---

## 8. 常见运维操作

```bash
# 启动服务
python app.py

# 忘记 admin 密码时在服务器本机生成临时密码（更新 users 表并撤销旧会话）
python scripts/reset_admin_password.py

# 抓取 / 分析 / 推荐评分：Web 页面 /tasks 或 API POST /api/fetch、/api/analyze、/api/run（原 CLI 能力入口）

# 查看数据库状态
python -c "from source.storage import *; init_db(); print(get_paper_count(), 'papers,', get_analyzed_count(), 'analyzed')"

# 运行测试（统一入口，需在仓库根目录；测试用相对路径读 templates/）
python scripts/run_all_tests.py         # 标准流程：unittest + pytest(随机顺序) + 3 次模块乱序，失败即停
python scripts/run_all_tests.py --quick # 只跑 pytest 一次（日常快速验证）
# 等价裸命令（脚本内部使用）：python -m unittest discover -s tests / python -m pytest tests/
# 测试布局：tests/ 顶层 17 个文件 + tests/ai_test/ 22 个文件（其中 17 个按模块拆分自原 test_ai_provider_config.py，
# 另有后续回归模块与 v0.7.0 权限矩阵/IDOR/删除保护测试；共享桩 DummyOpenAI/FakeRequest/install_import_stubs 与 Web 测试基座在 tests/ai_test/common.py）
# 覆盖率：python -m pytest --cov=. --cov-report=term-missing tests/（.coveragerc 排除 tests/scripts/.venv，tests/* 通配覆盖 ai_test 子目录）
# CI：push 到 dev/master 自动跑 tests.yml（完整套件 + 覆盖率），见 .github/workflows/tests.yml

# 备份数据库
cp data/papers.db data/papers.db.bak

# 手工编辑 settings.json 前先备份（settings.json 随 WebDAV 备份包备份）
cp data/settings.json data/settings.json.bak

# WebDAV 云备份
# 设置页「数据库 → WebDAV 云同步备份」可启用每日自动同步。
# 备份包包含 papers.db 一致性快照、data/settings.json 和 manifest；
# Web 日报位于 reports 表中，会随数据库快照备份。
# 会包含账号、全部私有学习数据、API Key 和 session secret；仅适用于可信存储，当前无客户端加密。
# 远端文件：arxiv-backup-latest.zip + arxiv-backup-YYYYMMDD-HHMMSS.zip，
# 历史备份默认保留 3 天，可在设置页修改。

# 报告邮件发送
# 设置页「定时任务 → 报告邮件」可配置 SMTP、收件人、主题模板和站点地址。
# 启用后每日定时任务会在报告生成并保存后发送邮件专用摘要版 HTML：
# report_summary 导读、推荐分 > important_score_threshold（默认 80）重点精读、最多 overview_limit（默认 20）篇快速速览。
# 自动发送前会检查 last_sent_report_date，同一日报成功发送后不再重复发送；
# 手动测试发送可重复执行，但不会更新自动任务的去重日期。
# 自动流程中的发送失败写入日报步骤并使父任务标记 warning，不中断后续备份；
# 手动测试发送仍记录独立 email_report 日志。
# SMTP 连接复用现有网络代理配置，代理启用时通过 HTTP CONNECT 连接 SMTP 服务器，
# 不新增邮件专用代理配置。
```

---

## 9. 已知限制与改进方向

### 当前限制
- PDF 提取依赖 PyMuPDF，扫描版 PDF 无法提取文本
- arXiv API 有速率限制，大量抓取时需增加 delay_seconds
- SQLite 仍适合单机中低并发；WAL 与 5000 ms `busy_timeout` 可缓解短时写锁竞争，但不替代分布式数据库
- 邀请制系统不提供自助注册、找回密码、MFA/OAuth、多管理员、自定义角色或管理员查看成员私有内容

### 可扩展方向
- 添加更多 arXiv 分类到 `source/config.py` 的 `ARXIV_CATEGORIES`
- 实现论文版本更新检测（v2/v3）
- 添加 Webhook 推送每日报告
- 实现向量语义搜索（embedding + cosine similarity）
- 添加论文收藏/标注功能
- 用 Celery 替代 APScheduler 实现分布式任务

---

## 10. 依赖列表

```
arxiv>=2.1.0        # arXiv API 客户端
openai>=1.0.0       # OpenAI 兼容 API SDK
flask>=3.0.0        # Web 框架
apscheduler>=3.10.0 # 定时任务调度
requests>=2.31.0    # HTTP 客户端（PDF下载）
PyMuPDF>=1.24.0     # PDF 文本提取
pytest>=9.0.0       # 测试运行器（与 unittest 双运行器并存）
pytest-randomly>=4.1.0 # pytest 随机顺序插件（防顺序耦合回归，--randomly-seed 可复现）
pytest-cov>=7.1.0   # 覆盖率统计（配合 .coveragerc）
```
