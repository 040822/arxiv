# 项目架构说明

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      用户浏览器                               │
│  (首页/分类浏览/搜索/论文详情/论文学习/任务管理/报告/清单/设置)│
└─────────────────────────┬───────────────────────────────────┘
                          │ HTTP
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    Flask Web 服务 (app.py)                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ 页面路由  │  │ 任务 API │  │ 论文 API │  │ 设置 API │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              APScheduler 定时任务                      │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              SSE 进度推送 (/api/progress)              │   │
│  └──────────────────────────────────────────────────────┘   │
└───────────┬──────────────┬──────────────┬───────────────────┘
            │              │              │
            ▼              ▼              ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│  fetcher.py   │ │  analyzer.py  │ │markdown_gen.py│
│  (arXiv API)  │ │ (OpenAI API)  │ │  (报告生成)   │
└───────┬───────┘ └───────┬───────┘ └───────┬───────┘
        │                 │                 │
        │           ┌─────┴─────┐           │
        │           │pdf_reader │           │
        │           │(PDF下载)  │           │
        │           └─────┬─────┘           │
        │                 │                 │
        ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────┐
│              SQLite 数据库 (data/papers.db)                   │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌─────┐│
│  │ papers  │ │ analysis │ │task_logs │ │ reports │ │ todo ││
│  └─────────┘ └──────────┘ └──────────┘ └─────────┘ └─────┘│
│  ┌──────────────── paper_chat / paper_quiz ───────────────┐│
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────┐
│  arXiv API    │
│ (export.      │
│  arxiv.org)   │
└───────────────┘
```

---

## 数据流

### 1. 论文抓取流

```
用户点击"抓取" → app.py /api/fetch → fetcher.py
  → arxiv.Client 查询 arXiv API
  → 去重（内存 + 数据库）
  → insert_paper() 写入 papers 表
  → 返回新增数量
```

### 2. AI 分析流

#### 基础分析（批量）

```
用户点击"分析" → app.py /api/analyze → analyzer.py
  → get_unanalyzed_papers()
  → ThreadPoolExecutor 并发：
    → analyze_paper_basic(abstract)
    → OpenAI API 调用
    → 解析 JSON 结果
  → insert_analysis() 写入 analysis 表
```

#### 深度阅读（单篇）

```
用户点击"生成报告" → app.py /api/paper/<id>/reanalyze → analyzer.py
  → analyze_paper_full(paper_data)
  → pdf_reader.download_pdf() + extract_text()
  → OpenAI API 调用（只生成 Q&A）
  → update_analysis() 仅更新 qa_analysis
```

#### 个性化推荐

```
设置页保存研究兴趣 → 仅写入 settings.personalization
手动重算 / 每日任务 / 一键执行 / 默认生成报告 → analyzer.recommend_pending_papers()
  → get_papers_for_recommendation() 仅取已有基础分析且缺失/过期推荐分的非隐藏论文
  → recommendation 任务模型返回 recommendation_score/reason
  → update_recommendation_result() 写入当前兴趣 hash
```

#### 论文学习（单篇）

```
详情页点击“讨论论文” → GET /paper/<arxiv_id>/chat
  → 自由讨论 / 主动问答练习 / 苏格拉底追问
  → analyzer.get_learning_paper_text()
    → 优先读取 data/pdf_cache/<arxiv_id>.pdf
    → 缓存不存在时 download_pdf()
    → PDF 下载或提取失败时回退 abstract
  → analyzer.build_paper_learning_messages()
    → system → 稳定任务说明 → 稳定论文上下文 → 动态历史/用户输入
  → paper_chat 或 paper_quiz 任务模型
  → paper_chat_messages / paper_quiz_sessions / paper_quiz_questions / paper_quiz_attempts
```

### 3. 报告生成流

```
用户点击"生成报告" → app.py /api/generate → database.py
  → 若研究兴趣非空且未传 recommend=0，先补齐目标日期缺失/过期推荐分
  → generate_report_content(date)
  → 查询指定日期的论文
  → 生成 HTML（统计、个性化推荐〔研究兴趣/中文摘要/推荐语/评价〕、分类分布、标签、全部论文）
  → save_report() 写入 reports 表
```

### 4. WebDAV 云备份流

```
设置页保存 WebDAV 配置 → settings.webdav_backup
手动备份 / 每日任务结束 → backup.run_webdav_backup()
  → SQLite online backup 生成 papers.db 一致性快照
  → 打包 papers.db + settings.json + output/ + manifest.json
  → WebDAV MKCOL/PUT 上传 latest 和日期历史文件
  → PROPFIND/DELETE 清理超过 history_days 的历史备份
```

每日任务中的云备份失败只写入 `webdav_backup` 任务日志和设置页最近错误，不中断抓取、分析和日报生成。

### 5. 搜索流

```
用户输入关键词 → GET /search?q=xxx → database.py
  → 检查是否为 arXiv ID
  → 如果是 ID：精确匹配 arxiv_id
  → 如果是关键词：LIKE 匹配 title/abstract/summary_cn/tags/qa_analysis
  → 返回结果列表
```

---

## 组件依赖关系

```
app.py
├── database.py     # 所有 DB 操作
├── fetcher.py      # 论文抓取
│   ├── database.py
│   └── settings.py # 代理配置
├── analyzer.py     # AI 分析
│   ├── database.py
│   ├── settings.py # API 配置、prompt
│   └── pdf_reader.py
├── markdown_gen.py
│   └── database.py
└── settings.py     # 配置管理
```

---

## 关键设计决策

### 1. 两种分析模式

**决策：** 将分析拆分为基础模式（不下载 PDF）和完整模式（下载 PDF）。

**原因：**
- 批量分析时下载 PDF 会很慢且容易触发 arXiv 限速
- 基础分析只用摘要，速度快，适合批量处理
- 深度阅读在用户主动请求时执行，单篇不会太慢，且不会覆盖基础分析字段

### 2. SQLite + WAL 模式

**决策：** 使用 SQLite 而非 PostgreSQL/MySQL。

**原因：**
- 单用户本地部署，不需要复杂的并发控制
- SQLite 零配置，文件级数据库，便于备份
- WAL 模式支持读写并发，满足 Web 服务需求

### 3. 无前端框架

**决策：** 使用原生 JS + Jinja2 模板，不使用 React/Vue。

**原因：**
- 项目是本地工具，不需要复杂的前端交互
- 减少依赖，降低部署复杂度
- Jinja2 模板足够满足服务端渲染需求

### 4. SSE 而非 WebSocket

**决策：** 使用 Server-Sent Events 推送进度。

**原因：**
- 进度推送是单向的（服务器→客户端）
- SSE 比 WebSocket 简单，浏览器原生支持
- Flask 单线程模型下 SSE 更容易实现

### 5. 配置分层

**决策：** config.py（硬编码）+ settings.json（运行时）。

**原因：**
- config.py 存放不常改的配置（分类、标签、路径）
- settings.json 存放用户可改的配置（供应商、prompt、任务级模型路由、个性化研究兴趣、WebDAV 云备份、代理、抓取参数、定时任务）
- 两者合并使用，优先级 settings.json > config.py

### 6. 论文学习的缓存友好前缀

**决策：** 论文学习不做向量检索或分块 RAG，先直接把论文全文作为稳定上下文前缀传给长上下文模型。

**原因：**
- 用户通常先触发深度阅读，PDF 已缓存在 `data/pdf_cache/`
- 同一篇论文的多轮讨论、答题评分和苏格拉底追问共享稳定 PDF 上下文，支持 DeepSeek/OpenAI 等供应商的 prompt cache 命中
- 动态历史和当前用户输入只放在 PDF 上下文之后，避免破坏缓存前缀
- PDF 提取失败时回退摘要，保证学习页可用性优先

---

## 性能考量

### 并发

- AI 分析使用 `ThreadPoolExecutor`，默认 5 线程
- 可通过设置页调整并发数（1-20）

### 限速

- arXiv API：每次请求间隔 3 秒
- PDF 下载：令牌桶限速（默认 1 次/秒，突发 2 个）
- 分批抓取：批次间隔 5 秒

### 缓存

- PDF 文件缓存到 `data/pdf_cache/`，避免重复下载
- 论文学习请求优先复用 PDF 缓存，并在 API 响应中返回 PDF 来源、缓存命中 tokens 和未命中 tokens
- 数据库查询无缓存（SQLite 足够快）

### 数据库优化

- WAL 模式支持读写并发
- 关键字段建索引：arxiv_id、published_date、primary_category、rating

---

## 安全考量

- 管理密码使用 SHA-256 哈希存储
- API Key 在接口返回时脱敏（只显示前 4 后 4 位）
- 设置管理密码后，设置页、任务页、写接口和敏感设置读取接口需要登录
- 管理登录默认通过签名 cookie 持久保存 180 天，使用 `session_secret` 保持服务重启后的登录状态，修改管理密码后旧登录状态失效
- 供应商列表接口只返回 `api_key_masked`，不返回完整 `api_key`
- 报告 HTML 由后端生成，数据库/AI 内容进入 HTML 前必须转义
- 代理配置和 WebDAV 密码明文存储在 settings.json
- WebDAV 备份包按设计包含原始 settings.json，因此也包含 API Key、管理密码哈希和 session secret
