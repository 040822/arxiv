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
├── settings.py         # 运行时配置（JSON：供应商、prompt、代理）
├── database.py         # SQLite 数据库全部操作（~1000 行）
├── fetcher.py          # arXiv API 论文抓取（支持分批）
├── analyzer.py         # AI 分析（基础/完整两种模式）
├── pdf_reader.py       # PDF 下载与文本提取（令牌桶限速）
├── markdown_gen.py     # Markdown 报告生成
├── app.py              # Flask Web 服务 + APScheduler（~1050 行）
├── main.py             # CLI 入口（fetch/analyze/generate/run）
├── requirements.txt    # Python 依赖
├── templates/          # Jinja2 HTML 模板（9 个文件）
│   ├── index.html      # 首页（每日论文）
│   ├── browse.html     # 分类浏览
│   ├── search.html     # 搜索
│   ├── paper.html      # 论文详情
│   ├── settings.html   # 设置
│   ├── tasks.html      # 任务管理
│   ├── reports.html    # 报告列表
│   ├── report_detail.html  # 报告详情
│   └── reading_list.html   # 阅读清单
├── static/style.css    # 全局样式（~1860 行）
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

### 5 张表

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
    rating INTEGER DEFAULT 0,          -- 0-5 星
    value_comment TEXT,                -- 评价
    qa_analysis TEXT,                  -- Q&A 深度阅读（Markdown）
    analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

#### task_logs — 任务日志表

```sql
CREATE TABLE task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT NOT NULL,           -- daily_pipeline/fetch/analyze/generate/run
    status TEXT NOT NULL DEFAULT 'running',  -- running/success/error
    message TEXT,
    detail TEXT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    duration_sec REAL
);
```

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

---

## 配置系统

### config.py — 硬编码配置

需要改代码才能修改的配置：

| 配置项 | 说明 |
|--------|------|
| `ARXIV_CATEGORIES` | 监控的 arXiv 分类列表 |
| `MAX_PAPERS_PER_CATEGORY` | 每分类每次拉取上限 |
| `TAG_CANDIDATES` | AI 标签候选列表 |
| `RATING_CRITERIA` | 评级标准文本 |
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
| `ai_tasks` | 基础分析、深度阅读、报告导读的任务级供应商/模型/参数路由 |
| `prompt_profiles` | 按 AI 功能拆分的稳定 system/instruction prompt |
| `prompts` | 旧版 system/user prompt 兼容字段，映射到 `deep_reading` |
| `concurrency` | AI 分析并发数 |
| `per_page` | 首页每页论文数 |
| `schedule` | 每日定时任务启用状态和执行时间 |
| `fetch` | 抓取延迟配置 |
| `proxy` | 代理配置 |
| `admin_password` | 管理密码（SHA-256） |

> **⚠️ 重要：** 在 `load_settings()` 中添加新字段时，必须在合并逻辑中显式添加 `if "key" in migrated: merged["key"] = migrated["key"]`，否则新字段在读取时会丢失！

AI 调用参数统一由 `settings.get_ai_task_config(task_key)` 和 `settings.build_chat_completion_kwargs()` 生成。新增模型调用逻辑时不要直接固定传 `temperature`、`max_tokens` 或 `enable_thinking`，也不要绕过任务级模型路由。

设置管理密码后，写接口和敏感设置读取接口需要登录；供应商列表接口只能返回脱敏后的 `api_key_masked`。

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
