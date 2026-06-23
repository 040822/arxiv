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

```
arxiv/
├── config.py           # 硬编码配置（分类、标签、路径、延迟参数）
├── settings.py         # 运行时配置（JSON：供应商、prompt、代理、AI任务路由、邮件/备份）
├── database.py         # SQLite 数据库全部操作（论文/分析/报告/学习记录）
├── fetcher.py          # arXiv API 论文抓取（支持分批）
├── analyzer.py         # AI 分析与论文学习对话/问答
├── backup.py           # WebDAV 云同步备份
├── email_report.py     # 每日报告 SMTP 邮件发送
├── pdf_reader.py       # PDF 下载与文本提取（令牌桶限速）
├── markdown_gen.py     # Markdown 报告生成
├── app.py              # Flask Web 服务 + APScheduler
├── main.py             # CLI 入口（fetch/analyze/generate/run）
├── requirements.txt    # Python 依赖
├── templates/          # Jinja2 HTML 模板
│   ├── index.html      # 首页（每日论文）
│   ├── browse.html     # 分类浏览
│   ├── search.html     # 搜索
│   ├── paper.html      # 论文详情
│   ├── settings.html   # 设置
│   ├── tasks.html      # 论文处理（手动抓取/分析/报告/添加论文）
│   ├── reports.html    # 报告列表
│   ├── report_detail.html  # 报告详情
│   ├── reading_list.html   # 阅读清单
│   ├── paper_chat.html     # 单篇论文学习页
│   ├── about.html          # 公开项目宣传页
│   └── vision.html         # 实验室愿景页
├── static/style.css    # 全局样式
├── static/promo.css    # 宣传页独立样式（.promo-* 命名空间）
├── data/               # 运行时数据（不提交 git）
│   ├── papers.db       # SQLite 数据库
│   ├── settings.json   # 运行时配置
│   └── pdf_cache/      # PDF 缓存
├── output/             # 生成的 Markdown 报告
├── docs/               # 文档目录
├── README.md           # 项目 README
└── AGENTS.md           # AI Agent 维护文档
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
    paper_id INTEGER NOT NULL,         -- FK -> papers.id (CASCADE DELETE)
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
    paper_id INTEGER NOT NULL,         -- FK -> papers.id (CASCADE DELETE)
    status TEXT DEFAULT 'unread',      -- unread/read
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);
```

#### 论文学习相关表

```sql
CREATE TABLE paper_chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,         -- FK -> papers.id (CASCADE DELETE)
    role TEXT NOT NULL,                -- user/assistant
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE paper_quiz_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,         -- FK -> papers.id (CASCADE DELETE)
    mode TEXT NOT NULL,                -- quick3/standard6/socratic
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

删除论文时会级联删除对话消息、练习会话、题目和作答记录。

---

## 配置系统

### config.py — 硬编码配置

需要改代码才能修改的配置：

| 配置项 | 说明 |
|--------|------|
| `ARXIV_CATEGORIES` | 监控的 arXiv 分类列表 |
| `MAX_PAPERS_PER_CATEGORY` | 每分类每次拉取上限 |
| `TAG_CANDIDATES` | AI 标签候选列表 |
| `RATING_CRITERIA` | AI 基础分析评级标准（0-5 星校准锚点） |
| `SCHEDULE_HOUR/MINUTE` | 定时任务首次默认时间；运行后以 `settings.json.schedule` 为准 |
| `FETCH_REQUEST_DELAY` | API 请求间隔 |
| `FETCH_BATCH_DAYS` | 分批抓取每批天数 |
| `FETCH_BATCH_DELAY` | 批次间隔 |
| `PDF_DOWNLOAD_RATE/CAPACITY` | PDF 下载限速 |

### settings.py — 运行时配置

通过 Web 设置页可修改，存储在 `data/settings.json`：

| 配置项 | 说明 |
|--------|------|
| `active_provider` | 当前激活的 AI 供应商 |
| `providers` | 供应商配置（API key、model、参数开关、思考模式、模型缓存等） |
| `ai_tasks` | 基础分析、深度阅读、报告导读、个性化推荐、论文对话、论文问答练习的任务级供应商/模型/参数路由 |
| `prompt_profiles` | 按 AI 功能拆分的稳定 system/instruction prompt |
| `personalization` | 个性化推荐配置，当前包含 `research_interests` |
| `webdav_backup` | WebDAV 云同步备份配置，包含地址、账号、远端目录、历史保留天数和最近备份状态 |
| `email_report` | 每日报告邮件配置，包含 SMTP、收件人、主题模板、站点地址和最近发送状态；UI 位于定时任务标签 |
| `schedule` | 唯一内置日报的星期、时间、抓取回看天数和分析上限 |
| `prompts` | 旧版 system/user prompt 兼容字段，映射到 `deep_reading` |
| `concurrency` | AI 分析并发数 |
| `per_page` | 首页每页论文数 |
| `schedule` | 每日定时任务启用状态和执行时间 |
| `fetch` | 抓取延迟配置 |
| `proxy` | 代理配置 |
| `admin_password` | 管理密码（SHA-256） |
| `session_secret` | 内部 Flask session 签名密钥，用于服务重启后保持登录 |

> **⚠️ 重要：** 在 `load_settings()` 中添加新字段时，必须在合并逻辑中显式添加 `if "key" in migrated: merged["key"] = migrated["key"]`，否则新字段在读取时会丢失！

AI 调用参数统一由 `settings.get_ai_task_config(task_key)` 和 `settings.build_chat_completion_kwargs()` 生成。新增模型调用逻辑时不要直接固定传 `temperature`、`max_tokens` 或 `enable_thinking`，也不要绕过任务级模型路由。基础分析会生成 AI 初评 `rating`，用户仍可在详情页手动修正；个性化推荐必须使用独立的 `recommendation` 任务路由，推荐分只在 `recommendation_interest_hash` 匹配当前研究兴趣时参与排序。论文学习功能使用 `paper_chat` 和 `paper_quiz` 任务路由，并通过 `build_paper_learning_messages()` 保持稳定 PDF 上下文前缀。

设置管理密码后，写接口和敏感设置读取接口需要登录；管理登录默认通过签名 cookie 持久保存 180 天，修改管理密码后旧登录状态失效。供应商列表接口只能返回脱敏后的 `api_key_masked`。

WebDAV 云备份由 `backup.py` 负责：先通过 SQLite online backup API 生成一致性快照，再将 `papers.db`、`settings.json` 和 `output/` 打包上传。`GET /api/settings/webdav-backup` 不得返回明文密码；备份包按需求包含原始 `settings.json`，因此会包含 API Key、管理密码哈希和 session secret。

报告邮件发送由 `email_report.py` 负责：按 `report_date` 读取数据库中的论文轻量分析数据，生成邮件专用摘要 HTML；推荐分 `>80` 的论文进入重点精读区，其余论文最多展示 20 篇速览，并可按 `site_url` 生成论文详情和完整报告链接。每日任务会在生成 AI 导读前检查 `last_sent_report_date`，同一日报成功发送后直接跳过；发送失败不会更新该日期，因此仍可重试。手动测试发送允许重复执行，并且不参与自动任务去重。SMTP 发送复用现有 `proxy` 配置；代理启用时通过标准库 socket 发起 HTTP CONNECT 隧道，不引入额外依赖，也不新增邮件专用代理字段。`GET /api/settings/email-report` 不得返回明文 SMTP 密码；POST 密码为空时保留旧密码。自动日报通过 `task_log_steps` 记录邮件/备份结果，附加步骤失败使父日志变为 `warning`；手动测试仍写独立顶级日志。

定时日报固定为六步流程，`task_logs` 保存父任务，`task_log_steps` 保存步骤状态和耗时。核心步骤异常时后续步骤标记 `skipped`；服务启动时遗留 `running` 记录会被收口为 `interrupted`。`daily_pipeline` 与 `/api/run` 共用进程内非阻塞互斥锁。

---

## 添加新功能的步骤

### 1. 添加新数据库字段

在 `database.py` 的 `init_db()` 中添加迁移逻辑：

```python
cursor.execute("PRAGMA table_info(table_name)")
columns = [row["name"] for row in cursor.fetchall()]
if "new_column" not in columns:
    cursor.execute("ALTER TABLE table_name ADD COLUMN new_column TYPE DEFAULT value")
```

### 2. 添加新 API 端点

在 `app.py` 中添加路由函数：

```python
@app.route("/api/new-endpoint", methods=["POST"])
def api_new_endpoint():
    try:
        # 业务逻辑
        return jsonify({"status": "ok", "message": "成功"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
```

### 3. 添加新页面

1. 创建 `templates/new_page.html`
2. 在 `app.py` 添加页面路由
3. 在其他页面的导航栏添加链接

### 4. 添加新样式

在 `static/style.css` 中添加，使用 kebab-case 命名。

宣传类独立页面使用 `static/promo.css`，所有类名以 `.promo-` 为前缀，避免影响现有业务页面。

---

## 数据库迁移模式

```python
# 检查列是否存在
cursor.execute("PRAGMA table_info(table_name)")
columns = [row["name"] for row in cursor.fetchall()]

# 添加新列
if "new_column" not in columns:
    cursor.execute("ALTER TABLE table_name ADD COLUMN new_column TYPE DEFAULT value")
```

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
- 状态样式使用前缀：`.log-success`、`.log-error`
- 响应式断点：`@media (max-width: 768px)`

### 模板

- 使用 Jinja2 语法
- 每个页面独立（无 base template）
- 导航栏在每个页面中重复定义
