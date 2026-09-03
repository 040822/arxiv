# 开发者指南

## 目录

- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [开发环境搭建](#开发环境搭建)
- [数据库设计](#数据库设计)
- [配置系统](#配置系统)
- [添加新功能的步骤](#添加新功能的步骤)
- [数据库迁移模式](#数据库迁移模式)
- [代码规范](#代码规范)

---

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.10+, Flask |
| 数据库 | SQLite (WAL mode) |
| 定时任务 | APScheduler |
| arXiv 抓取 | arxiv-py |
| AI 调用 | OpenAI SDK (兼容接口) |
| PDF 处理 | PyMuPDF (fitz) |
| 前端 | Jinja2 模板 + 原生 JS + CSS |

---

## 项目结构

```text
arxiv/
├── app.py                  # 唯一 Web 入口
├── source/
│   ├── analysis/         # LLM 调用、分析、学习与批处理
│   ├── benchmark/        # 私有论文阅读 Benchmark（出题/冻结/运行/裁判/报告）
│   ├── backups/          # WebDAV 备份
│   ├── documents/        # PDF 文档操作
│   ├── ingestion/        # arXiv 摄取
│   ├── reports/email/   # 报告邮件
│   ├── settings/         # 运行时配置
│   ├── storage/          # SQLite 存储
│   ├── pipeline/         # 流水线与 scheduler
│   └── web/              # Flask Blueprints
├── templates/             # Jinja2 HTML
├── static/css/           # 模块化业务样式
├── static/promo.css       # 宣传页样式
├── tests/                 # unittest/pytest 回归测试
└── data/                  # 运行时数据（不提交）
```

---

## 开发环境搭建

```bash
# 克隆项目
git clone <repo-url>
cd arxiv

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
python app.py
```

---

## 数据库设计

### 核心表

#### papers — 论文表

```sql
CREATE TABLE papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id TEXT UNIQUE NOT NULL,    -- 如 "2401.12345"
    title TEXT NOT NULL,
    authors TEXT NOT NULL,             -- JSON 数组
    abstract TEXT NOT NULL,
    categories TEXT NOT NULL,          -- JSON 数组
    primary_category TEXT,             -- 如 "cs.RO"
    url TEXT,                          -- arXiv 页面链接
    pdf_url TEXT,                      -- PDF 下载链接
    published_date TEXT,               -- "YYYY-MM-DD"
    updated_date TEXT,
    hidden INTEGER DEFAULT 0,          -- 1=隐藏
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

#### analysis — 分析结果表

```sql
CREATE TABLE analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,         -- FK -> papers.id；uq_analysis_paper_id 保证每篇论文唯一
    tags TEXT,                         -- JSON 数组
    summary_cn TEXT,                   -- 中文摘要翻译
    summary_en TEXT,                   -- 英文摘要（未使用）
    rating INTEGER DEFAULT 0,          -- AI 初评 + 用户可手动修正（0-5 星）
    legacy_ai_rating INTEGER,          -- 历史 AI 自动评级备份
    rating_restored_from_legacy INTEGER DEFAULT 0, -- 是否已从历史 AI 评级恢复
    value_comment TEXT,                -- 评价
    qa_analysis TEXT,                  -- Q&A 深度阅读（Markdown）
    recommendation_score INTEGER,      -- 个性化推荐分（0-100）
    recommendation_reason TEXT,        -- 推荐理由
    recommendation_interest_hash TEXT, -- 对应研究兴趣哈希
    recommendation_analyzed_at TEXT,   -- 推荐评分时间
    analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

#### task_logs — 任务日志表

```sql
CREATE TABLE task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT NOT NULL,           -- daily_pipeline/fetch/analyze/generate/run/...
    status TEXT NOT NULL DEFAULT 'running',  -- running/success/warning/error/skipped/interrupted
    message TEXT,
    detail TEXT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    duration_sec REAL
);
```

`task_log_steps` 通过 `task_log_id` 级联关联父日志，保存六步的 `step_key`、顺序、状态、消息、起止时间和耗时；`GET /api/tasks/logs` 会把步骤数组附在对应父日志上。

#### reports — 报告表

```sql
CREATE TABLE reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT UNIQUE NOT NULL,  -- "YYYY-MM-DD"
    content TEXT NOT NULL,             -- HTML 内容
    paper_count INTEGER DEFAULT 0,
    analyzed_count INTEGER DEFAULT 0,
    avg_rating REAL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

#### reading_list — 阅读清单表

```sql
CREATE TABLE reading_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,           -- FK -> users.id (CASCADE DELETE)，私有归属
    paper_id INTEGER NOT NULL,          -- FK -> papers.id (CASCADE DELETE)
    status TEXT DEFAULT 'unread',       -- unread/read
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(user_id, paper_id)           -- 每用户每论文唯一
);
```

#### 论文学习相关表

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
    expected_points TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE paper_quiz_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,      -- FK -> paper_quiz_questions.id (CASCADE DELETE)
    answer_text TEXT NOT NULL,
    score INTEGER DEFAULT 0,
    feedback_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

删除论文时会级联删除对话消息、练习会话、题目和作答记录；删除用户时，其私有学习记录随 `users` 外键级联删除。

---

## 配置系统

### source/config.py — 硬编码配置

需要改代码才能修改的配置：

| 配置项 | 说明 |
|--------|------|
| `ARXIV_CATEGORIES` | 监控的 arXiv 分类列表 |
| `TAG_CANDIDATES` | AI 标签候选列表 |
| `RATING_CRITERIA` | AI 基础分析评级标准（0-5 星校准锚点） |
| `SCHEDULE_HOUR/MINUTE` | 定时任务首次默认时间；运行后以 `settings.json.schedule` 为准 |
| `FETCH_REQUEST_DELAY` | API 请求间隔 |
| `FETCH_BATCH_DAYS` | 分批抓取每批天数 |
| `FETCH_BATCH_DELAY` | 批次间隔 |
| `PDF_DOWNLOAD_RATE/CAPACITY` | PDF 下载限速 |

### source/settings — 运行时配置

通过 Web 设置页可修改，存储在 `data/settings.json`：

| 配置项 | 说明 |
|--------|------|
| `settings_schema_version` | 设置结构版本；当前为 4 |
| `providers` | 仅保存供应商连接（名称、API Key、Base URL、模型列表缓存） |
| `ai_tasks` | PDF 元数据提取、基础分析、深度阅读、报告导读、个性化推荐、论文对话、论文问答练习的供应商/模型/Temperature/输出长度路由 |
| `prompt_profiles` | 按 AI 功能拆分的稳定 system/instruction prompt |
| `personalization` | 个性化推荐配置，当前包含 `research_interests` |
| `webdav_backup` | WebDAV 云同步备份配置，包含地址、账号、远端目录、历史保留天数和最近备份状态 |
| `email_report` | 每日报告邮件配置，包含 SMTP、收件人、主题模板、站点地址和最近发送状态；UI 位于定时任务标签 |
| `schedule` | 唯一内置日报的星期、时间、抓取回看天数、分析上限和抓取失败重试策略 |
| `prompts` | 旧版 system/user prompt 兼容字段，映射到 `deep_reading` |
| `concurrency` | AI 分析并发数 |
| `per_page` | 首页每页论文数 |
| `schedule` | 每日定时任务启用状态和执行时间 |
| `fetch` | 抓取延迟配置 |
| `proxy` | 全局代理配置，用于 arXiv、PDF 下载、LLM API 和 SMTP 邮件 |
| `session_secret` | 内部 Flask session 签名密钥，用于服务重启后保持登录 |

> `source/settings/store.py` 通过递归 deep merge 保留新增顶层字段，不再需要顶层白名单；需要归一化、迁移或密码保留语义的字段仍须显式处理并补测试。

AI 调用参数统一由 `settings.get_ai_task_config(task_key)` 和 `settings.build_chat_completion_kwargs()` 生成；测试未保存的路由草稿使用 `resolve_ai_task_config()`。OpenAI 兼容客户端统一通过 `source.analysis.get_openai_client()` 创建以复用全局代理并禁用环境变量代理；对 opencode 官方网关（base_url 含 `opencode.ai`）会自动携带 `x-opencode-session` 会话头，值由 `conversation_session_id(task_key, paper_data)` 派生（`arxiv-{task_key}-{user_id or system}-{paper_key}`，同一对话跨轮复用），非网关端点不加该头。新增模型调用逻辑时不要直接固定传 `temperature`、`max_tokens` 或 `enable_thinking`，也不要把模型或推理参数写回供应商连接。路由只保留可选 Temperature 控制和输出长度；未启用 Temperature 或使用思考模型时省略该参数，其他采样参数不发送并采用模型默认行为。基础分析会生成 AI 初评 `rating`，用户仍可在详情页手动修正；个性化推荐必须使用独立的 `recommendation` 任务路由，推荐分只在 `recommendation_interest_hash` 匹配当前研究兴趣时参与排序。论文学习功能使用 `paper_chat` 和 `paper_quiz` 任务路由，并通过 `build_paper_learning_messages()` 保持稳定 PDF 上下文前缀。

账号凭据只保存在 `users` 表。`source.web.auth` 提供 principal、30 天 session 版本校验、三级策略和全站 CSRF；私有存储接口必须显式接收当前 principal 的 `user_id`。供应商列表接口只能返回脱敏后的 `api_key_masked`。

WebDAV 云备份由 `source/backups/` 负责：先通过 SQLite online backup API 生成一致性快照，再将 `papers.db`、`settings.json` 和 manifest 打包上传。归档包含账号及全部私有学习数据、API Key 和 session secret，只能放在可信存储；不提供客户端加密。

报告邮件发送由 `source/reports/email/` 负责：按 `report_date` 读取数据库中的论文轻量分析数据，生成邮件专用摘要 HTML；推荐分高于 `important_score_threshold`（默认 80，0-100）的论文进入重点精读区，其余论文最多展示 `overview_limit`（默认 20，0-50）篇速览，并可按 `site_url` 生成论文详情和完整报告链接。两个阈值均可在设置页「定时任务 → 报告邮件」调整。每日任务会在生成 AI 导读前检查 `last_sent_report_date`，同一日报成功发送后直接跳过；发送失败不会更新该日期，因此仍可重试。手动测试发送允许重复执行，并且不参与自动任务去重。SMTP 发送复用现有 `proxy` 配置；代理启用时通过标准库 socket 发起 HTTP CONNECT 隧道，不引入额外依赖，也不新增邮件专用代理字段。`GET /api/settings/email-report` 不得返回明文 SMTP 密码；POST 密码为空时保留旧密码。自动日报通过 `task_log_steps` 记录邮件/备份结果，附加步骤失败使父日志变为 `warning`；手动测试仍写独立顶级日志。

定时日报固定为六步流程，`task_logs` 保存父任务，`task_log_steps` 保存步骤状态和耗时。抓取阶段异常时按 `settings.schedule.fetch_retry_interval_minutes` 等待重试，最多 `settings.schedule.fetch_max_retries` 次，默认 10 分钟/20 次且仅影响定时日报。核心步骤异常时后续步骤标记 `skipped`；服务启动时遗留 `running` 记录会被收口为 `interrupted`。`daily_pipeline` 与 `/api/run` 共用进程内非阻塞互斥锁。

---

## 添加新功能的步骤

### 1. 添加新数据库字段

在 `source/storage/migrations.py` 注册下一个连续版本的迁移：

```python
def migrate_new_field(conn):
    conn.execute("ALTER TABLE table_name ADD COLUMN new_column TYPE")

MIGRATIONS = (
    # ...保留已有版本...
    (3, "new_field", migrate_new_field),
)
```

### 2. 添加新 API 端点

在对应的 `source/web/*_api.py` Blueprint 中添加路由函数：

```python
@bp.route("/api/new-endpoint", methods=["POST"])
def api_new_endpoint():
    try:
        # 业务逻辑
        return jsonify({"status": "ok", "message": "成功"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
```

### 3. 添加新页面

1. 创建 `templates/new_page.html`
2. 在 `source/web/pages.py` 添加页面路由
3. 在其他页面的导航栏添加链接

### 4. 添加新样式

共享规则放入 `static/css/core.css` 或 `components.css`，富文本规则放入 `rich-text.css`，页面专属规则放入 `static/css/pages/`；使用 kebab-case 命名。

宣传类独立页面使用 `static/promo.css`，所有类名以 `.promo-` 为前缀，避免影响现有业务页面。

---

### Benchmark 表（v7）

论文阅读 Benchmark 使用八张核心表和两张 v7 审计/评分表：`benchmark_routes`（自管理任务路由）、
`benchmark_suites`（题库/冻结 Prompt 快照/校验和）、`benchmark_suite_papers`（论文全文快照）、
`benchmark_cases`（题目/证据/rubric）、`benchmark_runs`、`benchmark_candidates`、
`benchmark_responses`（原始输出、重试/错误和跨运行复用来源）、`benchmark_judgments`
（primary/review/system/human 判定）、`benchmark_scoring_revisions`（裁判配置/Prompt 版本）和
`benchmark_case_revisions`（题目编辑/审核审计）。运行额外保存 `active_scoring_revision`；状态支持
`queued → running → completed/error/interrupted`。

业务规则集中在 `source/benchmark/`（深模块），存储层不感知冻结/证据语义；题库冻结后不可修改，
冻结前编辑会重新校验证据/rubric 并追加审计记录。出题、运行、恢复和重判由单 worker 后台任务执行，
Web API 立即返回 202；进程重启时 queued/running 运行标记为 interrupted，人工恢复不会丢失已保存响应。
完整约定见 `AGENTS.md §7.10` 与规划文档 `docs/plan/paper-reading-benchmark-2026-08-04.md`。

## 数据库迁移模式

`schema.apply_baseline_schema()` 只负责基线 schema；后续变更必须作为
`source/storage/migrations.py` 中的连续版本追加。迁移器会：

1. 校验数据库版本连续且不高于当前代码；
2. 在首次待迁移版本前生成 SQLite online-backup 快照，最近保留 3 份；
3. 对每个版本执行 `BEGIN IMMEDIATE`，将 schema/data 变更与版本记录原子提交；
4. 任一快照或迁移失败时回滚并中止应用启动。

业务访问统一使用 `with get_connection() as conn:`，不要再手动
`commit()`/`close()`；连接默认启用 WAL、外键与 5000 ms `busy_timeout`。

---

## 代码规范

### Python

- 使用 `logging` 模块记录日志
- 函数/变量使用 snake_case
- 类名使用 PascalCase
- API 返回统一格式：`{"status": "ok/error", "message": "...", ...}`

### JavaScript

- 使用原生 JS（无框架）
- 异步操作使用 `async/await`
- API 调用使用 `fetch`

### CSS

- 组件样式使用 kebab-case：`.paper-card`、`.qa-item`
- 状态样式使用前缀：`.log-success`、`.log-warning`、`.log-error`、`.log-skipped`、`.log-interrupted`
- 响应式断点：`@media (max-width: 768px)`

### 模板

- 使用 Jinja2 语法
- 每个页面独立（无 base template）
- 导航栏在每个页面中重复定义
