# AGENTS.md — AI 论文数据库项目维护文档

本文件供 AI Agent 阅读，用于理解项目结构、代码逻辑和开发规范，以便后续维护和开发新功能。

> **📚 完整文档：** 详细的用户手册、开发者指南、API 文档等请参阅 [`docs/`](docs/) 目录。
> - [docs/agent-guide.md](docs/agent-guide.md) — AI Agent 快速入门
> - [docs/developer-guide.md](docs/developer-guide.md) — 开发者指南
> - [docs/api-reference.md](docs/api-reference.md) — API 接口文档
> - [docs/architecture.md](docs/architecture.md) — 项目架构说明

---

## 1. 项目概述

自动从 arXiv 抓取 AI/机器人领域论文，调用 OpenAI 兼容 API 进行深度阅读分析（Q&A 格式、标签、评级、中文翻译），存入 SQLite 数据库，通过 Flask Web 界面浏览。

**技术栈:** Python 3.10+ / Flask / SQLite / APScheduler / arxiv-py / OpenAI SDK / PyMuPDF

**分支策略:**
- `master` — 稳定版本
- `dev` — 开发分支

---

## 2. 文件结构与职责

```
arxiv/
├── config.py           # 硬编码配置（分类、标签候选、评级标准、路径）
├── settings.py         # 运行时配置（JSON文件：供应商、prompt、并发数、密码）
├── database.py         # SQLite 数据库全部操作（CRUD、迁移、任务日志）
├── fetcher.py          # arXiv API 论文抓取（去重、按分类拉取）
├── analyzer.py         # AI 分析逻辑（并发调用、PDF全文提取、Q&A生成）
├── pdf_reader.py       # PDF 下载与文本提取（PyMuPDF，缓存到 data/pdf_cache/）
├── markdown_gen.py     # Markdown 报告生成（README + 每日报告）
├── app.py              # Flask Web 服务（路由、API、APScheduler定时任务）
├── main.py             # CLI 入口（fetch/analyze/generate/run）
├── requirements.txt    # Python 依赖
├── README.md           # 用户文档
├── AGENTS.md           # 本文件（AI维护文档）
├── templates/          # Jinja2 HTML 模板
│   ├── index.html      # 首页（论文列表、操作面板）
│   ├── browse.html     # 分类浏览（多条件筛选）
│   ├── search.html     # 搜索页
│   ├── paper.html      # 论文详情（含编辑功能）
│   ├── settings.html   # 设置页（AI/数据库/管理三个Tab）
│   └── tasks.html      # 任务管理页（定时任务、统计、日志）
├── static/style.css    # 全局样式
├── data/               # 运行时数据（不提交到git）
│   ├── papers.db       # SQLite 数据库
│   ├── settings.json   # 运行时配置
│   └── pdf_cache/      # PDF 缓存
└── output/             # 生成的 Markdown 报告
    ├── README.md       # 总览报告
    └── daily/          # 每日报告
```

---

## 3. 数据库结构

### papers 表
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

### analysis 表
```sql
CREATE TABLE analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,         -- FK -> papers.id (CASCADE DELETE)
    tags TEXT,                         -- JSON 数组，如 ["VLA","World Model"]
    summary_cn TEXT,                   -- Abstract 中文翻译
    summary_en TEXT,                   -- 英文摘要（当前未使用）
    rating INTEGER DEFAULT 0,          -- 0-5 星
    value_comment TEXT,                -- 评价
    qa_analysis TEXT,                  -- Q&A 深度阅读（Markdown 格式）
    analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### task_logs 表
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

---

## 4. 核心数据流

### 4.1 论文抓取流程
```
fetcher.fetch_latest_papers()
  → 遍历 ARXIV_CATEGORIES
  → arxiv.Client 按 submittedDate 降序拉取
  → 去重：seen_ids 内存去重 + paper_exists() 数据库去重
  → insert_paper() 写入 papers 表
  → 返回新增论文列表
```

### 4.2 AI 分析流程
```
analyzer.analyze_pending_papers(limit, concurrency)
  → get_unanalyzed_papers() 获取未分析论文
  → ThreadPoolExecutor 并发执行 analyze_paper()
    → get_ai_config() 获取 API 配置
    → get_prompts() 获取 prompt 模板
    → get_paper_full_text() 下载PDF提取全文（失败则用摘要）
    → user_prompt.format(title, authors, abstract, tag_candidates, rating_criteria)
    → OpenAI chat.completions.create()
    → 解析 JSON 响应：{qa_analysis, tags, rating, summary_cn, value_comment}
  → insert_analysis() 写入 analysis 表（含重复检查）
```

### 4.3 定时任务流程
```
APScheduler cron(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE)
  → daily_pipeline()
    → start_task_log()
    → fetch_latest_papers()
    → analyze_pending_papers()
    → generate_all_markdown()
    → finish_task_log(status="success")
```

---

## 5. 配置系统

### 5.1 config.py（硬编码，需改代码）
- `ARXIV_CATEGORIES` — 监控的 arXiv 分类
- `TAG_CANDIDATES` — AI 标签候选列表
- `RATING_CRITERIA` — 评级标准文本
- `ANALYSIS_CONCURRENCY` — 默认并发数
- `SCHEDULE_HOUR/MINUTE` — 定时任务时间
- `WEB_HOST/PORT` — Web 服务地址

### 5.2 data/settings.json（运行时，Web界面可改）
```json
{
  "active_provider": "deepseek",
  "concurrency": 5,
  "admin_password": "sha256...",
  "providers": {
    "deepseek": {
      "name": "DeepSeek",
      "api_key": "sk-xxx",
      "base_url": "https://api.deepseek.com",
      "model": "deepseek-chat",
      "temperature": 0.3,
      "temperature_enabled": true,
      "max_tokens": 8192,
      "max_tokens_enabled": false,
      "is_thinking": false,
      "thinking_effort": "medium",
      "available_models": ["deepseek-chat", "deepseek-reasoner"]
    }
  },
  "prompts": {
    "system_prompt": "...",
    "user_prompt": "...{title}...{authors}...{abstract}...{tag_candidates}...{rating_criteria}..."
  }
}
```

**settings.py 函数:**
- `load_settings()` / `save_settings()` — 读写JSON（含自动迁移）
- `get_ai_config()` — 获取当前激活供应商的 API 配置
- `build_chat_completion_kwargs()` — 统一构建 Chat Completions 参数（思考模型会省略采样参数）
- `normalize_provider_config()` — 补齐供应商配置字段，兼容旧版 settings.json
- `get_prompts()` — 获取 system/user prompt
- `get_concurrency()` — 获取并发数
- `get_per_page()` — 获取每页论文数
- `get_fetch_config()` / `save_fetch_config()` — 抓取配置（请求间隔、批次天数、批次间隔）
- `get_proxy_config()` / `save_proxy_config()` — 代理配置
- `add/remove/switch/update_provider()` — 供应商 CRUD
- `get/set/verify/has_admin_password()` — 管理密码

> **⚠️ 重要：** 在 `load_settings()` 中添加新字段时，必须在合并逻辑中显式添加对应的 `if "key" in migrated: merged["key"] = migrated["key"]`，否则新字段在读取时会丢失！这是已踩过的坑。

---

## 6. API 端点清单

### 页面路由
| 路由 | 说明 |
|------|------|
| `GET /` | 首页（论文列表） |
| `GET /browse` | 分类浏览（多条件筛选） |
| `GET /search?q=` | 搜索 |
| `GET /paper/<arxiv_id>` | 论文详情（含编辑） |
| `GET /settings` | 设置页（AI/数据库/管理） |
| `GET /tasks` | 任务管理页 |

### 任务 API
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/fetch` | POST | 抓取论文 |
| `/api/analyze` | POST | AI分析（?limit=50） |
| `/api/generate` | POST | 生成报告 |
| `/api/run` | POST | 一键执行全部 |

### 论文 API
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/papers` | GET | 论文列表JSON |
| `/api/tags` | GET | 标签列表 |
| `/api/stats` | GET | 统计信息 |
| `/api/paper/<id>/analysis` | PUT | 更新论文分析 |
| `/api/paper/<id>/hide` | POST | 隐藏论文 |
| `/api/paper/<id>/unhide` | POST | 取消隐藏 |
| `/api/paper/<id>` | DELETE | 删除论文 |
| `/api/paper/<id>/reanalyze` | POST | 重新AI分析 |

### 设置 API
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/providers` | GET/POST | 供应商列表/添加 |
| `/api/providers/<key>` | PUT/DELETE | 更新/删除供应商 |
| `/api/providers/<key>/activate` | POST | 切换供应商 |
| `/api/providers/presets` | GET | 预设供应商列表 |
| `/api/providers/models` | POST | 从供应商 API 自动获取模型列表 |
| `/api/test_connection` | POST | 测试API连接 |
| `/api/prompts` | GET/POST | 读取/保存Prompt |
| `/api/settings/concurrency` | POST | 保存并发数 |
| `/api/db/info` | GET | 数据库信息 |
| `/api/admin/password` | POST/DELETE | 设置/清除密码 |

### 任务日志 API
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tasks/stats` | GET | 任务统计 |
| `/api/tasks/logs` | GET | 日志列表（?task=&page=） |
| `/api/tasks/scheduled` | GET | 定时任务列表 |
| `/api/tasks/clear` | POST | 清理旧日志（?keep_days=30） |

---

## 7. 开发规范

### 7.1 添加新功能的步骤
1. 如果涉及新数据库表/字段 → 修改 `database.py` 的 `init_db()` 并添加迁移逻辑
2. 如果涉及新 API → 在 `app.py` 添加路由函数
3. 如果涉及新页面 → 创建 `templates/xxx.html`，在 `app.py` 添加页面路由
4. 如果涉及新样式 → 在 `static/style.css` 添加
5. 更新 `AGENTS.md` 记录变更

### 7.2 数据库迁移模式
```python
# 在 init_db() 中使用 PRAGMA table_info 检查列是否存在
cursor.execute("PRAGMA table_info(table_name)")
columns = [row["name"] for row in cursor.fetchall()]
if "new_column" not in columns:
    cursor.execute("ALTER TABLE table_name ADD COLUMN new_column TYPE DEFAULT value")
```

### 7.3 添加新供应商
在 `settings.py` 的 `PROVIDER_PRESETS` 字典中添加：
```python
"new_provider": {
    "name": "显示名称",
    "base_url": "https://api.example.com/v1",
    "models": ["model-1", "model-2"],
},
```

供应商运行时配置还支持：
- `available_models` — 自动拉取或预设的模型列表
- `max_tokens_enabled` — 是否发送输出长度限制；默认 `false`
- `temperature_enabled/top_p_enabled/presence_penalty_enabled/frequency_penalty_enabled` — 采样参数开关
- `is_thinking` / `thinking_effort` — 思考模型开关与强度档位（auto/low/medium/high/max）

调用模型时必须通过 `build_chat_completion_kwargs()` 构建参数，不要在业务代码中直接固定传 `temperature` 或 `max_tokens`。

### 7.4 修改 Prompt
- prompt 存储在 `data/settings.json` 的 `prompts` 字段
- Web 设置页可修改，也可直接编辑 JSON 文件
- 可用变量：`{title}` `{authors}` `{abstract}` `{tag_candidates}` `{rating_criteria}`
- 注意：`{abstract}` 实际可能是论文全文（如果 PDF 提取成功）

### 7.5 添加新标签
在 `config.py` 的 `TAG_CANDIDATES` 列表中添加。注意：
- 避免过于宽泛的标签（如 "Transformer"、"LLM"）
- 优先使用具体的技术方法名称

### 7.6 arXiv API 注意事项
- **submittedDate 过滤器不工作**：arXiv API 的 `submittedDate:[... TO ...]` 查询语法实际不返回结果，已踩坑
- **正确做法**：使用 `cat:xxx` 查询 + `sortBy=submittedDate&sortOrder=descending` 排序，然后在代码中按 `published` 日期过滤
- **分类查询**：`cat:cs.RO` 匹配主分类为 cs.RO 的论文，比 `primary_category:cs.RO` 更可靠
- **分批抓取**：大批量抓取时使用 `fetch_batch()` 自动分批，避免单次请求过大
- **时区问题**：arXiv 返回的 `published` 是带 UTC 时区的 datetime，比较时必须使用 `datetime.now(timezone.utc)`，否则报 `can't compare offset-naive and offset-aware datetimes`

### 7.7 CSS 样式约定
- 组件样式使用 kebab-case：`.paper-card`、`.qa-item`
- 状态样式使用前缀：`.log-success`、`.log-error`、`.log-running`
- 响应式断点：`@media (max-width: 768px)`

---

## 8. 常见运维操作

```bash
# 启动服务
python app.py

# 手动执行完整流程
python main.py

# 仅抓取
python main.py fetch

# 仅分析
python main.py analyze

# 仅生成报告
python main.py generate

# 查看数据库状态
python -c "from database import *; init_db(); print(get_paper_count(), 'papers,', get_analyzed_count(), 'analyzed')"

# 备份数据库
cp data/papers.db data/papers.db.bak
```

---

## 9. 已知限制与改进方向

### 当前限制
- PDF 提取依赖 PyMuPDF，扫描版 PDF 无法提取文本
- arXiv API 有速率限制，大量抓取时需增加 delay_seconds
- SQLite 在高并发写入时可能有锁竞争（已用 WAL 模式缓解）
- 无用户登录系统，管理密码仅保护设置页

### 可扩展方向
- 添加更多 arXiv 分类到 `config.py` 的 `ARXIV_CATEGORIES`
- 实现论文版本更新检测（v2/v3）
- 添加邮件/Webhook 推送每日报告
- 实现向量语义搜索（embedding + cosine similarity）
- 添加论文收藏/标注功能
- 添加多用户系统
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
```
