# 项目架构说明

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      用户浏览器                               │
│  (首页/分类浏览/搜索/论文详情/任务管理/报告/阅读清单/设置)      │
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

#### 完整分析（单篇）

```
用户点击"生成报告" → app.py /api/paper/<id>/reanalyze → analyzer.py
  → analyze_paper_full(paper_data)
  → pdf_reader.download_pdf() + extract_text()
  → OpenAI API 调用（含 Q&A）
  → update_analysis() 更新 analysis 表
```

### 3. 报告生成流

```
用户点击"生成报告" → app.py /api/generate → database.py
  → generate_report_content(date)
  → 查询指定日期的论文
  → 生成 HTML（统计、分类分布、标签、高分论文、全部论文）
  → save_report() 写入 reports 表
```

### 4. 搜索流

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
- 完整分析在用户主动请求时执行，单篇不会太慢

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
- settings.json 存放用户可改的配置（供应商、prompt）
- 两者合并使用，优先级 settings.json > config.py

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
- 数据库查询无缓存（SQLite 足够快）

### 数据库优化

- WAL 模式支持读写并发
- 关键字段建索引：arxiv_id、published_date、primary_category、rating

---

## 安全考量

- 管理密码使用 SHA-256 哈希存储
- API Key 在接口返回时脱敏（只显示前 4 后 4 位）
- 无用户登录系统，管理密码仅保护设置页
- 代理配置明文存储在 settings.json
