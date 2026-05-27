# AI 论文数据库

自动从 arXiv 抓取具身智能与人工智能领域最新论文，调用 AI 快速阅读分析（标签、翻译、摘要、评级），生成 Markdown 报告，并提供 Web 浏览界面。

## 功能特性

- **每日自动抓取** — 从 arXiv 拉取 cs.AI、cs.RO、cs.CV、cs.LG、cs.CL、cs.MA 分类的最新论文
- **AI 快速阅读** — 自动调用 OpenAI 兼容 API，为每篇论文生成标签（VLA、World Model 等）、中文摘要翻译、精炼总结、0~5 星评级
- **Markdown 报告** — 自动生成 README 总览和每日论文报告
- **Web 浏览** — Flask 本地 Web 服务，支持按标签/评级筛选、关键词搜索
- **定时任务** — 内置 APScheduler，每天定时自动执行抓取和分析流程

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

编辑 `config.py`，填入你的 OpenAI 兼容 API 信息：

```python
OPENAI_API_KEY = "your-api-key-here"
OPENAI_BASE_URL = "https://your-api-url/v1"   # 自定义 URL
OPENAI_MODEL = "gpt-4o-mini"                   # 模型名称
```

也可通过环境变量设置：

```bash
set OPENAI_API_KEY=your-key
set OPENAI_BASE_URL=https://your-url/v1
set OPENAI_MODEL=gpt-4o-mini
```

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
├── config.py           # 配置文件（API、分类、标签候选等）
├── main.py             # 主入口，支持 fetch/analyze/generate/run
├── app.py              # Flask Web 服务 + APScheduler 定时任务
├── database.py         # SQLite 数据库操作
├── fetcher.py          # arXiv API 论文抓取
├── analyzer.py         # OpenAI API 论文分析（标签/翻译/摘要/评级）
├── markdown_gen.py     # Markdown 报告生成
├── templates/          # Flask HTML 模板
├── static/style.css    # Web 样式
├── data/papers.db      # SQLite 数据库（自动创建）
└── output/             # 生成的 Markdown 文件
    ├── README.md       # 总览报告
    └── daily/          # 每日报告
        └── 2026-05-27.md
```

## Web API

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

在 `config.py` 中修改每日执行时间（24 小时制）：

```python
SCHEDULE_HOUR = 10      # 默认早上 10 点
SCHEDULE_MINUTE = 0
```

## 监控的 arXiv 分类

| 分类 | 说明 |
|------|------|
| cs.AI | 人工智能 |
| cs.RO | 机器人学 |
| cs.CV | 计算机视觉与模式识别 |
| cs.LG | 机器学习 |
| cs.CL | 计算与语言（NLP） |
| cs.MA | 多智能体系统 |

在 `config.py` 的 `ARXIV_CATEGORIES` 中可自行增减。

## 评级标准

| 评级 | 含义 |
|------|------|
| ☆☆☆☆☆ | 与 AI/机器人领域无关或质量极低 |
| ★☆☆☆☆ | 常规工作，增量改进 |
| ★★☆☆☆ | 有一定价值，创新性有限 |
| ★★★☆☆ | 有价值的工作，有明确创新点 |
| ★★★★☆ | 高质量工作，可能产生较大影响 |
| ★★★★★ | 里程碑式工作，将深刻影响领域 |
