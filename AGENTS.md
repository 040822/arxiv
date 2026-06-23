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

```
arxiv/
├── config.py           # 硬编码配置（分类、标签候选、路径）
├── settings.py         # 运行时配置（JSON文件：供应商、prompt、AI任务路由、并发数、定时任务、邮件、密码）
├── database.py         # SQLite 数据库全部操作（CRUD、迁移、任务日志、学习记录）
├── fetcher.py          # arXiv API 论文抓取（去重、按分类拉取）
├── analyzer.py         # AI 分析与论文学习逻辑（PDF全文、Q&A、对话、问答反馈）
├── backup.py           # WebDAV 云同步备份（SQLite 快照、zip 打包、上传/清理）
├── email_report.py     # 每日报告邮件发送（SMTP、邮件HTML包装、站内链接重写）
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
│   ├── paper_chat.html # 论文学习页（对话、问答、苏格拉底追问）
│   ├── about.html      # 公开项目宣传页（研究闭环、优势、竞品定位）
│   ├── vision.html     # 实验室愿景页（科研价值、竞品格局、发展路线）
│   ├── settings.html   # 设置页（含独立的定时任务、邮件和执行日志管理）
│   └── tasks.html      # 论文处理页（抓取、分析、报告、添加指定论文）
├── static/style.css    # 全局样式
├── static/promo.css    # 宣传页独立样式（公开版 + 深色愿景版）
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

删除论文时，以上学习记录会随 papers 外键级联删除。

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
    → get_ai_task_config("basic_analysis") 获取基础分析模型与参数
    → get_prompt_profile("basic_analysis") 获取稳定 Prompt 前缀
    → 论文标题/作者/摘要作为独立 JSON message 放在最后
    → OpenAI chat.completions.create()
    → 解析 JSON 响应：{tags, summary_cn, value_comment}
  → insert_analysis() 写入 analysis 表（含重复检查）

analyzer.analyze_paper_full(paper_data)
  → get_ai_task_config("deep_reading") 获取深度阅读模型与参数
  → get_paper_full_text(max_chars=None) 下载 PDF 并提取全文（不截断）
  → 稳定 Prompt 前缀 + 动态论文全文 JSON message
  → OpenAI chat.completions.create()
  → 解析 JSON 响应：{qa_analysis}
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

### 4.4 定时任务流程
```
APScheduler cron(day_of_week, hour, minute)
  → daily_pipeline()
    → 初始化 task_logs + 六条 task_log_steps
    → fetch_latest_papers(days=schedule.fetch_days)
    → analyze_pending_papers(limit=schedule.analyze_limit)
    → 按研究兴趣补齐推荐评分（未设置时 skipped）
    → generate_report_content(latest_date) + save_report()
    → send_report_email()  # 未启用/已发送时 skipped；失败记 warning
    → run_webdav_backup()  # 未启用时 skipped；失败记 warning
    → finish_task_log(success/warning/error)
```

应用启动时会将上一次进程遗留的 `running` 日志改为 `interrupted`。定时日报和 `/api/run` 共享非阻塞互斥锁；自动冲突记为 `skipped`，手动冲突返回 HTTP 409。

---

## 5. 配置系统

### 5.1 config.py（硬编码，需改代码）
- `ARXIV_CATEGORIES` — 监控的 arXiv 分类
- `TAG_CANDIDATES` — AI 标签候选列表
- `RATING_CRITERIA` — AI 基础分析评级标准（0-5 星校准锚点）
- `ANALYSIS_CONCURRENCY` — 默认并发数
- `SCHEDULE_HOUR/MINUTE` — 定时任务首次默认时间；运行后以 `settings.json` 的 `schedule` 为准
- `WEB_HOST/PORT` — Web 服务地址

### 5.2 data/settings.json（运行时，Web界面可改）
```json
{
  "active_provider": "deepseek",
  "concurrency": 5,
  "admin_password": "sha256...",
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
    "site_url": "https://your-domain.example"
  },
  "schedule": {
    "enabled": true,
    "days_of_week": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "hour": 10,
    "minute": 0,
    "fetch_days": 3,
    "analyze_limit": 1000
  },
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
    "user_prompt": "...{title}...{authors}...{abstract}...{tag_candidates}..."
  },
  "prompt_profiles": {
    "basic_analysis": {"system": "...", "instruction": "...{tag_candidates}..."},
    "deep_reading": {"system": "...", "instruction": "..."},
    "report_summary": {"system": "...", "instruction": "..."},
    "recommendation": {"system": "...", "instruction": "...返回 recommendation_score/recommendation_reason..."},
    "paper_chat": {"system": "...", "instruction": "...多轮讨论..."},
    "paper_quiz": {"system": "...", "instruction": "...主动回忆/评分/追问..."}
  },
  "ai_tasks": {
    "basic_analysis": {"provider_key": "deepseek", "model": "deepseek-chat", "is_thinking": false, "max_tokens_enabled": true, "max_tokens": 1200},
    "deep_reading": {"provider_key": "deepseek", "model": "deepseek-reasoner", "is_thinking": true, "thinking_effort": "high", "max_tokens_enabled": true, "max_tokens": 6000},
    "report_summary": {"provider_key": "deepseek", "model": "deepseek-chat", "is_thinking": false, "max_tokens_enabled": true, "max_tokens": 1000},
    "recommendation": {"provider_key": "deepseek", "model": "deepseek-chat", "is_thinking": false, "max_tokens_enabled": true, "max_tokens": 500},
    "paper_chat": {"provider_key": "deepseek", "model": "deepseek-reasoner", "is_thinking": true, "thinking_effort": "high", "max_tokens_enabled": true, "max_tokens": 4000},
    "paper_quiz": {"provider_key": "deepseek", "model": "deepseek-reasoner", "is_thinking": true, "thinking_effort": "high", "max_tokens_enabled": true, "max_tokens": 3000}
  }
}
```

**settings.py 函数:**
- `load_settings()` / `save_settings()` — 读写JSON（含自动迁移）
- `get_ai_config()` — 获取当前激活供应商的 API 配置
- `get_ai_task_config(task_key)` — 获取某个 AI 功能的实际供应商、模型和参数配置
- `get_ai_tasks()` / `save_ai_tasks()` — 获取/保存基础分析、深度阅读、报告导读、个性化推荐、论文对话、论文问答练习的模型路由
- `build_chat_completion_kwargs()` — 统一构建 Chat Completions 参数（思考模型会省略采样参数）
- `normalize_provider_config()` — 补齐供应商配置字段，兼容旧版 settings.json
- `get_prompt_profile()` / `get_prompt_profiles()` — 获取任务级 Prompt Profile；`get_prompts()` 保留旧接口兼容
- `get_concurrency()` — 获取并发数
- `get_per_page()` — 获取每页论文数
- `get_schedule_config()` / `save_schedule_config()` — 获取/保存内置日报的星期、时间、回看天数和分析上限
- `get_fetch_config()` / `save_fetch_config()` — 抓取配置（请求间隔、批次天数、批次间隔）
- `get_proxy_config()` / `save_proxy_config()` — 代理配置
- `get_personalization_config()` / `save_personalization_config()` — 个性化推荐研究兴趣
- `get_webdav_backup_config()` / `save_webdav_backup_config()` — WebDAV 云备份配置；GET 给前端时必须脱敏密码
- `get_email_report_config()` / `save_email_report_config()` / `update_email_report_status()` — 每日报告邮件配置；GET 给前端时必须脱敏 SMTP 密码；`last_sent_report_date` 只记录自动任务成功发送的日报日期
- `add/remove/switch/update_provider()` — 供应商 CRUD
- `get/set/verify/has_admin_password()` — 管理密码
- `get_session_secret()` — 获取/生成持久 Flask session 签名密钥

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
| `GET /paper/<arxiv_id>/chat` | 论文学习页（对话/问答/苏格拉底追问） |
| `GET /about` | 公开项目宣传页（首页提供入口） |
| `GET /vision` | 实验室科研情报基础设施愿景页（仅直接访问） |
| `GET /settings` | 设置页（含独立定时任务标签） |
| `GET /tasks` | 论文处理页 |

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
| `/login` | GET | 管理登录页 |
| `/api/auth/status` | GET | 当前认证状态 |
| `/api/auth/login` | POST | 管理密码登录 |
| `/api/auth/logout` | POST | 退出登录 |
| `/api/providers` | GET/POST | 供应商列表/添加 |
| `/api/providers/<key>` | PUT/DELETE | 更新/删除供应商 |
| `/api/providers/<key>/activate` | POST | 切换供应商 |
| `/api/providers/presets` | GET | 预设供应商列表 |
| `/api/providers/models` | POST | 从供应商 API 自动获取模型列表 |
| `/api/test_connection` | POST | 测试API连接 |
| `/api/detect_thinking` | POST | 检测是否为思考模型 |
| `/api/prompts` | GET/POST | 读取/保存Prompt |
| `/api/settings/ai-tasks` | GET/POST | 读取/保存 AI 功能模型路由 |
| `/api/settings/ai-usage` | GET | 查看近期 LLM token 用量 |
| `/api/settings/personalization` | GET/POST | 读取/保存研究兴趣 |
| `/api/recommendations/recalculate` | POST | 手动重算个性化推荐评分 |
| `/api/settings/webdav-backup` | GET/POST | 读取/保存 WebDAV 云备份配置（GET 不返回明文密码） |
| `/api/backup/webdav/run` | POST | 手动立即执行 WebDAV 备份 |
| `/api/settings/email-report` | GET/POST | 读取/保存每日报告邮件配置（GET 不返回明文密码） |
| `/api/email-report/test` | POST | 使用最近一份日报告测试发送邮件 |
| `/api/settings/concurrency` | POST | 保存并发数 |
| `/api/settings/schedule` | GET/POST | 读取/保存内置日报的星期、时间、抓取天数和分析上限 |
| `/api/db/info` | GET | 数据库信息 |
| `/api/admin/password` | POST/DELETE | 设置/清除密码 |

### 任务日志 API
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tasks/stats` | GET | 任务统计 |
| `/api/tasks/logs` | GET | 日志列表（?task=&page=），日报日志附带 `steps` |
| `/api/tasks/scheduled` | GET | 内置日报配置、时区、下次执行与最近运行 |
| `/api/tasks/clear` | POST | 清理旧日志（?keep_days=30） |

---

## 7. 开发规范

### 7.1 添加新功能的步骤
1. 如果涉及新数据库表/字段 → 修改 `database.py` 的 `init_db()` 并添加迁移逻辑
2. 如果涉及新 API → 在 `app.py` 添加路由函数
3. 如果涉及新页面 → 创建 `templates/xxx.html`，在 `app.py` 添加页面路由
4. 如果涉及新样式 → 通用业务页面在 `static/style.css` 添加；独立宣传页使用 `static/promo.css` 和 `.promo-*` 命名空间
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
}
```

供应商运行时配置还支持：
- `available_models` — 自动拉取或预设的模型列表
- `max_tokens_enabled` — 是否发送输出长度限制；默认 `false`
- `temperature_enabled/top_p_enabled/presence_penalty_enabled/frequency_penalty_enabled` — 采样参数开关
- `is_thinking` / `thinking_effort` — 思考模型开关与强度档位（auto/low/medium/high/max）

调用模型时必须通过 `build_chat_completion_kwargs()` 构建参数，不要在业务代码中直接固定传 `temperature` 或 `max_tokens`。

管理密码设置后，`/settings`、`/tasks`、写接口和敏感设置读取接口都需要登录；阅读清单加入/移除接口例外，公开可用。登录状态通过签名 cookie 持久保存 180 天，默认使用 `settings.json` 中的 `session_secret` 保证服务重启后仍有效；如果设置了 `FLASK_SECRET_KEY` 则优先使用环境变量。修改管理密码会使旧登录状态失效。`GET /api/providers` 只能返回 `api_key_masked`，不能返回完整 `api_key`。

### 7.4 修改 Prompt
- 新版 prompt 主要存储在 `data/settings.json` 的 `prompt_profiles` 字段，按 `basic_analysis`、`deep_reading`、`report_summary`、`recommendation`、`paper_chat`、`paper_quiz` 拆分
- 旧版 `prompts.system_prompt/user_prompt` 保留为兼容字段，并映射到 `deep_reading`
- 基础分析 Prompt Profile 可使用 `{tag_candidates}`、`{rating_criteria}`，并返回 `tags`、`rating`、`summary_cn`、`value_comment`；深度阅读只描述 Q&A 输出；论文标题、作者、摘要、PDF 全文由后端作为独立 JSON message 传入
- 修改 AI 调用逻辑时不要重新把动态论文内容拼回稳定 instruction，否则会降低 prompt cache 命中率
- 深度阅读按质量优先调用 `get_paper_full_text(max_chars=None)`，不截断 PDF 全文；基础分析只使用摘要以降低成本
- 论文学习功能必须通过 `build_paper_learning_messages()` 构造消息，保持 `system → 稳定任务说明 → 稳定论文上下文 → 动态历史/用户输入` 的顺序；不要把时间戳、session id、当前问题等易变内容放进稳定论文上下文
- 论文学习 PDF 文本必须通过 `get_learning_paper_text()` 获取，优先复用 `data/pdf_cache/<arxiv_id>.pdf`，未命中才下载，失败时回退摘要

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

# WebDAV 云备份
# 设置页「数据库 → WebDAV 云同步备份」可启用每日自动同步。
# 备份包包含 papers.db 一致性快照、data/settings.json 和 output/，
# 会包含 API Key、管理密码哈希和 session secret 等敏感配置。
# 远端文件：arxiv-backup-latest.zip + arxiv-backup-YYYYMMDD-HHMMSS.zip，
# 历史备份默认保留 3 天，可在设置页修改。

# 报告邮件发送
# 设置页「定时任务 → 报告邮件」可配置 SMTP、收件人、主题模板和站点地址。
# 启用后每日定时任务会在报告生成并保存后发送邮件专用摘要版 HTML：
# report_summary 导读、推荐分 >80 重点精读、最多 20 篇快速速览。
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
- SQLite 在高并发写入时可能有锁竞争（已用 WAL 模式缓解）
- 无多用户隔离系统，管理密码只提供本地单用户访问保护

### 可扩展方向
- 添加更多 arXiv 分类到 `config.py` 的 `ARXIV_CATEGORIES`
- 实现论文版本更新检测（v2/v3）
- 添加 Webhook 推送每日报告
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
