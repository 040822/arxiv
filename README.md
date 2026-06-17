# AI 论文数据库

自动从 arXiv 抓取具身智能与人工智能领域最新论文，调用 AI 快速阅读分析（标签、翻译、摘要、AI 评级、简评），生成 Web 报告，并提供 Flask 浏览界面。论文评级由 AI 初评，用户可在详情页手动修正，CLI 仍可按需生成 Markdown 报告。

## 功能特性

- **每日自动抓取** — 默认从 arXiv 拉取 cs.RO 分类论文，可在 `config.py` 中增减分类
- **AI 快速阅读** — 自动调用 OpenAI 兼容 API，为每篇论文生成标签（VLA、World Model 等）、中文摘要翻译和精炼总结
- **Web 报告** — 自动生成数据库内的每日结构化报告；CLI 可生成 Markdown 总览和日报
- **Web 浏览** — Flask 本地 Web 服务，支持按标签/评级筛选、关键词搜索
- **定时任务** — 内置 APScheduler，每天定时自动执行抓取、分析和报告生成
- **云同步备份** — 可通过 WebDAV 每日备份数据库快照、运行设置和报告文件
- **管理保护** — 可设置管理密码保护设置页、任务执行和写接口

## 快速开始

### 1. 创建虚拟环境并安装依赖

以下三种方式任选其一：

**方式 A — conda**

```bash
conda create -n arxiv python=3.11 -y
conda activate arxiv
pip install -r requirements.txt
```

**方式 B — uv（推荐，速度更快）**

```bash
# 安装 uv（如尚未安装）
pip install uv

# 创建虚拟环境并安装依赖
uv venv
uv pip install -r requirements.txt

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
```

**方式 C — 原生 venv + pip**

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 API

启动 Web 服务后进入 `http://localhost:5000/settings`，在「AI 设置」中添加 OpenAI 兼容供应商，填写 API Key、Base URL 和模型名称。

运行时配置保存在 `data/settings.json`，该文件包含 API Key，已被 `.gitignore` 排除，请不要提交到 GitHub。`config.py` 中的 API 相关变量只作为首次默认值或环境变量 fallback。

### 3. 运行

**完整流程**（抓取 → 分析 → 生成报告）：

```bash
python main.py
```

**分步执行**：

```bash
python main.py fetch       # 仅抓取新论文
python main.py analyze     # 仅分析未处理的论文
python main.py generate    # 仅生成 Markdown 报告
```

**启动 Web 服务**（含每日定时任务）：

```bash
python app.py
```

浏览器访问 `http://localhost:5000`

## 项目结构

```
├── config.py           # 硬编码配置（分类、标签候选、路径、默认值）
├── main.py             # 主入口，支持 fetch/analyze/generate/run
├── app.py              # Flask Web 服务 + APScheduler 定时任务
├── database.py         # SQLite 数据库操作
├── fetcher.py          # arXiv API 论文抓取
├── analyzer.py         # OpenAI API 论文分析（标签/翻译/摘要/简评）
├── markdown_gen.py     # CLI Markdown 报告生成
├── templates/          # Flask HTML 模板
├── static/style.css    # Web 样式
├── data/papers.db      # SQLite 数据库（自动创建）
└── output/             # 生成的 Markdown 文件
    ├── README.md       # 总览报告
    └── daily/          # 每日报告
        └── 2026-05-27.md
```

## Web API

设置管理密码后，所有 `POST/PUT/DELETE` 写接口、设置接口、任务管理接口都需要先登录；首页、浏览、搜索、报告和只读论文数据保持可读。管理登录默认保持 180 天，服务重启后仍有效，修改管理密码后旧登录状态会失效。

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 首页，论文列表（支持 `?tag=`、`?min_rating=`、`?date=`、`?page=`） |
| `/paper/<arxiv_id>` | GET | 论文详情页 |
| `/search?q=关键词` | GET | 搜索论文 |
| `/api/papers` | GET | JSON 格式论文列表 |
| `/api/tags` | GET | 所有标签及计数 |
| `/api/stats` | GET | 统计信息 |
| `/api/fetch` | POST | 触发论文抓取 |
| `/api/analyze` | POST | 触发 AI 分析 |
| `/api/generate` | POST | 触发报告生成 |

## 定时任务配置

首次默认时间来自 `config.py`，之后可在「任务管理 → 定时任务设置」中启用/停用并保存执行时间，配置会写入 `data/settings.json`。如在「设置 → 数据库 → WebDAV 云同步备份」启用云备份，每日任务会在报告生成后自动同步一次；备份失败会单独记录日志，不中断日报流程。

## WebDAV 云备份

在「设置 → 数据库」中可填写 WebDAV 地址、用户名、密码/应用密码、远端目录和历史保留天数。系统会上传：

- `arxiv-backup-latest.zip`
- `arxiv-backup-YYYYMMDD-HHMMSS.zip`

历史备份默认保留 3 天，可在设置页调整。备份包包含 `papers.db` 一致性快照、`data/settings.json` 和 `output/` 报告目录；其中 `settings.json` 含 API Key、管理密码哈希和 session secret，请确保 WebDAV 位置可信。

## 监控的 arXiv 分类

| 分类 | 说明 |
|------|------|
| cs.AI | 人工智能 |
| cs.RO | 机器人学 |
| cs.CV | 计算机视觉与模式识别 |
| cs.LG | 机器学习 |
| cs.CL | 计算与语言（NLP） |
| cs.MA | 多智能体系统 |

当前默认只启用 `cs.RO`。在 `config.py` 的 `ARXIV_CATEGORIES` 中可自行增减。

## AI 评级与手动修正

基础分析会生成 0~5 星 AI 初评，评分标准已校准以充分使用各星级，避免默认集中在 3 星。用户仍可在论文详情页手动修正星级；`legacy_ai_rating` 仅作为历史 AI 评级备份/恢复字段。

## 更新日志

### v0.2.0 (2026-05-28)

**论文浏览体验**
- 首页论文卡片新增原始摘要显示，支持展开/收起，无中文摘要时默认展开
- 论文列表新增多分类标签展示（cross-list 分类）
- 分类浏览页新增批量选择和批量删除功能
- 论文链接行新增「不感兴趣」按钮，点击可快速隐藏论文
- paper 详情页 arXiv ID 和 PDF 链接改为内联超链接，去掉独立按钮
- paper 详情页布局调整：原始摘要 → 中文摘要 → AI 深度阅读 → 评价
- AI 深度阅读的 Markdown 渲染支持加粗、斜体、行内代码

**分析流程重构**
- 批量分析（每日定时/手动批量）改为轻量模式：不下载 PDF，仅基于摘要生成标签/翻译/AI 评级/简评
- paper 详情页「生成报告」触发深度阅读：下载 PDF 全文，只生成/刷新 Q&A，不覆盖基础分析字段
- PDF 下载新增令牌桶限速（默认 1 次/秒），避免触发 arXiv 风控
- 抓取改为使用 `cat:` 查询并在代码中按发布日期过滤

**Web 报告系统**
- 新增 `/reports` 报告汇总页和 `/reports/<date>` 单日报告详情页
- 「生成报告」改为生成 Web 版结构化报告（个性化推荐先展示研究兴趣，再展示中文摘要、推荐语和评价，另含分类分布、热门标签、全部论文列表）
- 单日报告详情页支持一键重新生成并覆盖当前日期报告
- 定时任务和一键执行自动生成报告

**任务管理**
- 操作面板从首页移至任务管理页
- 抓取支持选择主分类和最大数量
- 分析支持设置数量上限，带实时进度条
- 新增定时任务配置展示（执行时间、状态、下次执行时间）
- 首页新增「今日论文」快捷筛选，去掉「高分」和「推荐」

**分页与设置**
- 首页分页支持页码跳转，显示总页数
- 首页/分类浏览支持每页数量配置（默认 20，范围 5~100）

**其他**
- 新增 `AGENTS.md` 维护文档
- arXiv API 查询改为 `cat:` 分类查询并在代码中过滤日期
