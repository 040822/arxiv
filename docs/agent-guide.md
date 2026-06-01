# AI Agent 开发指南

本指南帮助 AI Agent 快速理解项目结构、数据流和开发约定，以便高效地进行代码修改和功能扩展。

---

## 项目概览

这是一个**AI 论文数据库**系统，核心功能：

1. 从 arXiv 抓取论文（cs.RO 等分类）
2. 调用 AI 生成标签、评级、中文摘要、Q&A 深度阅读
3. 存入 SQLite 数据库
4. 提供 Web 界面浏览、搜索、筛选

**技术栈：** Python + Flask + SQLite + arxiv-py + OpenAI SDK

---

## 文件职责映射

| 文件 | 行数 | 职责 | 修改频率 |
|------|------|------|----------|
| `config.py` | ~96 | 硬编码配置（分类、标签、路径、延迟） | 低 |
| `settings.py` | ~283 | 运行时配置（供应商、prompt、代理） | 中 |
| `database.py` | ~1010 | 所有数据库操作（5 张表） | 高 |
| `fetcher.py` | ~343 | arXiv 论文抓取 | 中 |
| `analyzer.py` | ~204 | AI 分析（基础/完整） | 中 |
| `pdf_reader.py` | ~107 | PDF 下载与文本提取 | 低 |
| `app.py` | ~1058 | Flask 路由 + 定时任务 + SSE | 高 |
| `main.py` | ~97 | CLI 入口 | 低 |
| `templates/*.html` | ~9 文件 | 前端页面 | 高 |
| `static/style.css` | ~1860 | 全局样式 | 中 |

---

## 核心数据流

### 1. 论文抓取流程

```
fetcher.fetch_latest_papers() / fetch_batch()
  → arxiv.Client 查询 arXiv API
  → 去重：内存 seen_ids + paper_exists() 数据库去重
  → insert_paper() 写入 papers 表
  → 返回新增论文列表
```

### 2. 基础分析流程（批量）

```
analyzer.analyze_pending_papers(limit, concurrency)
  → get_unanalyzed_papers() 获取未分析论文
  → ThreadPoolExecutor 并发执行 analyze_paper_basic()
    → 使用 abstract（不下载 PDF）
    → 调用 OpenAI API
    → 解析 JSON：{tags, rating, summary_cn, value_comment}
  → insert_analysis() 写入 analysis 表
```

### 3. 完整分析流程（单篇）

```
analyzer.analyze_paper_full(paper_data)
  → get_paper_full_text() 下载 PDF 提取全文
  → 调用 OpenAI API（含 Q&A 问题）
  → 解析 JSON：{qa_analysis, tags, rating, summary_cn, value_comment}
```

### 4. 定时任务流程

```
APScheduler cron(hour=10, minute=0)
  → daily_pipeline()
    → fetch_latest_papers(days=3)  # 近 3 日
    → analyze_pending_papers(limit=1000)
    → generate_report_content(latest_date)
    → save_report()
```

---

## 关键约定

### 1. settings.py 的 load_settings()

`load_settings()` 合并配置时，必须显式添加新字段：

```python
if "new_field" in migrated:
    merged["new_field"] = migrated["new_field"]
```

**否则新字段在读取时会丢失！** 这是已踩过的坑。

### 2. arXiv API 注意事项

- `submittedDate:[... TO ...]` 过滤器**不工作**，不要使用
- 正确做法：`cat:xxx` + `sortBy=submittedDate` + 代码中按 `published` 日期过滤
- `cat:cs.RO` 比 `primary_category:cs.RO` 更可靠
- arXiv 返回的 `published` 是带 UTC 时区的 datetime，比较时必须用 `datetime.now(timezone.utc)`

### 3. 数据库迁移模式

```python
cursor.execute("PRAGMA table_info(table_name)")
columns = [row["name"] for row in cursor.fetchall()]
if "new_column" not in columns:
    cursor.execute("ALTER TABLE table_name ADD COLUMN new_column TYPE DEFAULT value")
```

### 4. API 返回格式

```json
// 成功
{"status": "ok", "message": "...", ...}

// 错误
{"status": "error", "message": "..."}
```

### 5. 两种分析模式

| 模式 | 函数 | PDF | Q&A | 使用场景 |
|------|------|-----|-----|----------|
| 基础 | `analyze_paper_basic()` | ❌ | ❌ | 批量分析、定时任务 |
| 完整 | `analyze_paper_full()` | ✅ | ✅ | 单篇论文详情页 |

---

## 常见修改场景

### 场景 1：添加新的数据库字段

1. 在 `database.py` 的 `init_db()` 中添加迁移逻辑
2. 在相关的 CRUD 函数中添加新字段的处理
3. 在 `app.py` 的 API 中返回新字段
4. 在模板中显示新字段

### 场景 2：添加新的 API 端点

1. 在 `app.py` 中添加路由函数
2. 遵循统一的返回格式
3. 使用 try/except 处理错误
4. 如需数据库操作，在 `database.py` 中添加函数

### 场景 3：添加新的页面

1. 创建 `templates/new_page.html`
2. 在 `app.py` 添加页面路由
3. 在所有模板的 `.nav-bar` 中添加导航链接
4. 在 `static/style.css` 中添加样式

### 场景 4：修改 AI 分析逻辑

1. 基础分析：修改 `analyzer.py` 的 `analyze_paper_basic()`
2. 完整分析：修改 `analyze_paper_full()`
3. Prompt：在 `settings.py` 的 `DEFAULT_SETTINGS` 中修改默认值，或通过 Web 设置页修改

### 场景 5：添加新的筛选条件

1. 在 `database.py` 的查询函数中添加 WHERE 条件
2. 在 `app.py` 的路由中读取参数
3. 在模板中添加筛选 UI
4. 传递参数到模板渲染

---

## 需要注意的边界情况

### 1. 论文可能没有分析结果

papers 表和 analysis 表是 1:N 关系（实际是 1:1），使用 LEFT JOIN 查询。访问分析字段前需检查：

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

`fetcher.py` 的 `_apply_proxy()` 会设置/清除 `http_proxy` 和 `https_proxy` 环境变量，影响所有 HTTP 请求。

### 5. SSE 进度推送

长任务使用 SSE（Server-Sent Events）推送进度。客户端通过 `EventSource` 监听 `/api/progress/<task_id>`。

---

## 快速定位问题

| 问题 | 检查文件 |
|------|----------|
| 论文抓取失败 | `fetcher.py` + 代理配置 + arXiv API 状态 |
| AI 分析失败 | `analyzer.py` + API 配置 + 模型可用性 |
| PDF 下载失败 | `pdf_reader.py` + 代理配置 + 令牌桶限速 |
| 页面显示异常 | `templates/*.html` + `static/style.css` |
| 数据库问题 | `database.py` + `data/papers.db` |
| 定时任务不执行 | `app.py` 的 `daily_pipeline()` + APScheduler 日志 |
| 配置不生效 | `settings.py` 的 `load_settings()` 合并逻辑 |

---

## 依赖关系图

```
app.py
├── database.py (所有 DB 操作)
├── fetcher.py (论文抓取)
│   ├── database.py
│   └── settings.py (代理配置)
├── analyzer.py (AI 分析)
│   ├── database.py
│   ├── settings.py (API 配置、prompt)
│   └── pdf_reader.py (PDF 下载)
├── markdown_gen.py (报告生成)
│   └── database.py
└── settings.py (配置管理)
```
