# AI Agent 开发指南

本指南帮助 AI Agent 快速理解项目结构、数据流和开发约定，以便高效地进行代码修改和功能扩展。

---

## 项目概览

这是一个**AI 论文数据库**系统，核心功能：

1. 从 arXiv 抓取论文（cs.RO 等分类）
2. 调用 AI 生成标签、评级、中文摘要和简评，并按需补充 Q&A 深度阅读；评级可由用户手动修正
3. 存入 SQLite 数据库
4. 提供 Web 界面浏览、搜索、筛选、论文对话和主动问答学习

**技术栈：** Python + Flask + SQLite + arxiv-py + OpenAI SDK

---

## 文件职责映射

| 文件 | 行数 | 职责 | 修改频率 |
|------|------|------|----------|
| `source/config.py` | ~96 | 硬编码配置（分类、标签、路径、延迟） | 低 |
| `source/settings/*` | 8 个模块 | 值转换、默认值、归一化、思考协议、store、供应商、Prompt、运行时配置 | 中 |
| `source/storage/*` | 10 个模块 | SQLite 托管连接、顺序迁移/快照、论文、分析、日志、报告与学习记录 | 中 |
| `source/ingestion/` | 包 | arXiv 论文抓取 | 中 |
| `source/analysis/` | 包 | AI 分析（基础/完整） | 中 |
| `source/documents/` | 包 | PDF 下载与文本提取 | 低 |
| `app.py` | 单文件 | 唯一 Web 入口 | 低 |
| `source/web/*` | 9 个模块 | Blueprint、鉴权、页面/API 与进度 | 高 |
| `source/pipeline/*` | 3 个模块 | 定时/手动组合流水线与 scheduler | 中 |
| `templates/*.html` | ~10+ 文件 | 前端页面 | 高 |
| `static/css/` | 10 个文件 | 共享、富文本与页面样式 | 中 |

---

## 核心数据流

### 1. 论文抓取流程

```
source.ingestion.fetch_latest_papers() / fetch_batch()
  → arxiv.Client 查询 arXiv API
  → 去重：内存 seen_ids + paper_exists() 数据库去重
  → insert_paper() 写入 papers 表
  → 返回新增论文列表
```

### 2. 基础分析流程（批量）

```
source.analysis.analyze_pending_papers(limit, concurrency)
  → get_unanalyzed_papers() 获取未分析论文
  → ThreadPoolExecutor 并发执行 analyze_paper_basic()
    → 使用 abstract（不下载 PDF）
    → 调用 OpenAI API
    → 解析 JSON：{tags, rating, summary_cn, value_comment}
  → insert_analysis() 写入 analysis 表
```

### 3. 深度阅读流程（单篇）

```
source.analysis.analyze_paper_full(paper_data)
  → get_paper_full_text() 下载 PDF 提取全文
  → 调用 OpenAI API（只生成 Q&A）
  → 解析 JSON：{qa_analysis}
  → 校验 Prompt 声明的 Q 编号，截断或缺题时最多补全一次
  → 仅在完整时 update_analysis()；否则保留旧 qa_analysis 并返回 warning
```

### 4. 论文学习流程（单篇）

```
详情页点击“讨论论文” → GET /paper/<arxiv_id>/chat
  → 自由讨论 / 问答练习 / 苏格拉底追问
  → get_learning_paper_text()
    → 优先检查 data/pdf_cache/<arxiv_id>.pdf
    → 缓存不存在时才 download_pdf()
    → PDF 提取失败则回退摘要
  → build_paper_learning_messages()
    → system → 稳定任务说明 → 稳定论文上下文 → 最近历史/用户输入
  → paper_chat 或 paper_quiz 任务模型
  → 保存 chat messages / quiz sessions / questions / attempts
```

### 5. 定时任务流程

```
APScheduler cron(day_of_week, hour, minute)
  → daily_pipeline()
    → 初始化父日志与六条 task_log_steps
    → fetch_latest_papers(days=schedule.fetch_days)  # 失败按 schedule 重试，仅限定时日报
    → analyze_pending_papers(limit=schedule.analyze_limit)
    → recommend_pending_papers(latest_date)  # 未设置研究兴趣时 skipped
    → generate_report_content(latest_date)
    → save_report()
    → send_report_email() / run_webdav_backup()  # 未启用时 skipped，失败时 warning
```

执行星期、时间、抓取回看天数、分析上限和抓取失败重试策略从 `settings.json.schedule` 读取，`source/config.py` 仅提供首次默认时间。抓取阶段异常时默认每 10 分钟重试，最多 20 次；该策略只作用于定时日报。应用启动时遗留 `running` 日志会变为 `interrupted`；定时日报和 `/api/run` 共用互斥锁。

---

## 关键约定

### 1. source/settings 的 load_settings()

`load_settings()` 会递归合并默认配置和 `settings.json`，普通新增顶层字段无需维护
白名单。需要迁移、归一化或密码保留语义的字段，仍应在
`source/settings/store.py` 或 `normalize.py` 中显式处理并添加回归测试。

### 2. 认证与敏感字段

- 设置管理密码后，`/settings`、`/tasks`、论文学习页、写接口和敏感设置读取接口都需要登录；阅读清单加入/移除接口例外，公开可用
- 管理登录默认持久 180 天，使用签名 cookie；`session_secret` 存在 `settings.json` 中以保证服务重启后仍有效，修改管理密码会使旧登录状态失效
- `GET /api/providers` 只能返回 `api_key_masked`，不要返回完整 `api_key`
- 个性化推荐的研究兴趣保存在 `settings.personalization.research_interests`；推荐评分必须使用独立 `recommendation` 任务路由，并且只有 `recommendation_interest_hash` 匹配当前兴趣时才能用于报告排序
- WebDAV 云备份配置保存在 `settings.webdav_backup`；GET 接口只返回 `password_masked`，自动备份失败写入 backup 步骤并使父任务变为 `warning`
- 用户/AI/数据库内容进入 HTML 前必须转义，报告页的 `|safe` 只用于后端生成且已转义的 HTML

### 3. arXiv API 注意事项

- `submittedDate:[YYYYMMDDTTTT TO YYYYMMDDTTTT]` 是可用的官方日期过滤字段；与 `cat:xxx` 组合后按日期窗口查询
- 查询仍需按 `submittedDate` 降序翻页，并在代码中保留 `[start, end)` 的 `published` 日期边界兜底
- `cat:cs.RO` 比 `primary_category:cs.RO` 更可靠
- arXiv 返回的 `published` 是带 UTC 时区的 datetime，比较时必须用 `datetime.now(timezone.utc)`

### 4. 数据库迁移模式

在 `source/storage/migrations.py` 的 `MIGRATIONS` 末尾增加连续版本和独立迁移
函数，不要把新迁移继续堆入 baseline schema。迁移器会在变更前创建一致性
快照，每个版本在独立事务中执行，并把结果写入 `schema_migrations`；快照或
迁移失败会中止启动。业务代码统一使用 `with get_connection() as conn:`。

### 4. API 返回格式

```json
// 成功
{"status": "ok", "message": "...", ...}

// 错误
{"status": "error", "message": "..."}
```

### 5. AI 任务模式

| 模式 | 函数 | PDF | 输出 | 使用场景 |
|------|------|-----|------|----------|
| 基础 | `analyze_paper_basic()` | 否 | tags/rating/summary/value_comment | 批量分析、定时任务；使用 `basic_analysis` 任务模型 |
| 深度阅读 | `analyze_paper_full()` | 是 | qa_analysis | 单篇论文详情页；使用 `deep_reading` 任务模型且不截断 PDF 全文，只补充 Q&A |
| 论文对话 | `chat_about_paper()` | 缓存优先 | 自然语言回复 | 学习页自由讨论；使用 `paper_chat` 任务模型 |
| 主动问答 | `generate_paper_quiz()` / `grade_quiz_answer()` / `socratic_reply()` | 缓存优先 | 题目、评分反馈、追问 | 学习页练习和苏格拉底模式；使用 `paper_quiz` 任务模型 |

Prompt 已拆为 `prompt_profiles`。学习功能必须通过 `build_paper_learning_messages()` 构造消息，稳定 instruction 和稳定论文上下文放在前缀，最近 12 条历史、题目状态和当前用户输入只追加在 PDF 上下文之后，以提高 prompt cache 命中率。

---

## 常见修改场景

### 场景 1：添加新的数据库字段

1. 在 `source/storage/migrations.py` 注册下一个连续版本的迁移
2. 在相关的 CRUD 函数中添加新字段的处理
3. 在对应的 `source/web/*_api.py` 中返回新字段
4. 在模板中显示新字段

### 场景 2：添加新的 API 端点

1. 在对应的 `source/web/*_api.py` Blueprint 中添加路由函数
2. 遵循统一的返回格式
3. 使用 try/except 处理错误
4. 如需数据库操作，在对应的 `source/storage/*.py` 中添加函数

### 场景 3：添加新的页面

1. 创建 `templates/new_page.html`
2. 在 `source/web/pages.py` 添加页面路由
3. 在所有模板的 `.nav-bar` 中添加导航链接
4. 按 `templates/README.md` 的加载矩阵，把共享样式放入 `core.css`/`components.css`，页面规则放入 `pages/`

### 场景 4：修改 AI 分析逻辑

1. 基础分析：修改 `source/analysis/` 的 `analyze_paper_basic()`
2. 深度阅读：修改 `source/analysis/` 的 `analyze_paper_full()`
3. Prompt：在 `source/settings/defaults.py` 的 `DEFAULT_SETTINGS` 中修改默认值，或通过 Web 设置页修改

### 场景 5：添加新的筛选条件

1. 在对应的 `source/storage/*.py` 查询函数中添加 WHERE 条件
2. 在对应的 `source/web/*_api.py` 路由中读取参数
3. 在模板中添加筛选 UI
4. 传递参数到模板渲染

---

## 需要注意的边界情况

### 1. 论文可能没有分析结果

papers 表和 analysis 表是数据库唯一索引保证的 1:1 关系，查询通常使用 LEFT JOIN。访问分析字段前需检查：

```python
if paper.get("rating") and paper["rating"] > 0:
    # 有评级
```

### 2. authors/categories 是 JSON 字符串

从数据库取出时需要解析：

```python
if r.get("authors") and isinstance(r["authors"], str):
    r["authors"] = json.loads(r["authors"])
```

### 3. 论文可能已存在

添加论文前检查 `paper_exists(arxiv_id)`，分析前检查 `get_analysis_by_paper_id()`。

### 4. 代理环境变量

代理配置保存在 `settings.proxy`，设置页「网络与代理」可保存全局 HTTP/HTTPS 代理并测试 arXiv 与基础分析 LLM 连接。`source/ingestion/` 的 `_apply_proxy()` 会设置/清除 `http_proxy` 和 `https_proxy` 环境变量，供 arXiv 客户端使用；LLM 调用不依赖环境变量，而是由 `source.analysis.get_openai_client()` 显式创建 `DefaultHttpxClient(trust_env=False, proxy=...)`。PDF 下载和 SMTP 邮件也会读取全局代理；WebDAV 当前按内网服务处理，不接入代理。

### 5. SSE 进度推送

长任务使用 SSE（Server-Sent Events）推送进度。客户端通过 `EventSource` 监听 `/api/progress/<task_id>`。

---

## 快速定位问题

| 问题 | 检查文件 |
|------|----------|
| 论文抓取失败 | `source/ingestion/` + 代理配置 + arXiv API 状态 |
| AI 分析失败 | `source/analysis/` + API 配置 + 模型可用性 + 网络与代理页的 LLM 测试 |
| PDF 下载失败 | `source/documents/` + 代理配置 + 令牌桶限速 |
| 页面显示异常 | `templates/*.html` + `static/css/` |
| 数据库问题 | `source/storage/*.py` + `data/papers.db` |
| WebDAV 备份失败 | `source/backups/` + `settings.webdav_backup` + 日报 backup 步骤/手动日志 |
| 定时任务不执行 | `source/pipeline/orchestrator.py` + `scheduler.py` + `task_logs/task_log_steps` |
| 配置不生效 | `source/settings/store.py` 的 `load_settings()` 与 normalize 逻辑 |

---

## 依赖关系图

```text
app.py -> source/web/application.py + Blueprints
|-- source/pipeline/       # 流水线与 scheduler
|-- source/storage/        # SQLite 存储
|-- source/settings/       # 运行时配置
|-- source/ingestion/      # arXiv 摄取
|-- source/analysis/       # 分析与学习
|   `-- source/documents/  # PDF 文档
|-- source/backups/        # WebDAV
`-- source/reports/email/ # 报告邮件
```
