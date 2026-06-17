# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

自动从 arXiv 抓取 AI/机器人领域论文，调用 OpenAI 兼容 API 做基础分析（标签/评级/中文摘要）和按需深度阅读（Q&A），存入 SQLite，通过 Flask Web 界面浏览、生成报告，并支持单篇论文对话/主动问答学习。Python 3.11 / Flask / SQLite / APScheduler / arxiv-py / OpenAI SDK / PyMuPDF。

**详尽文档已存在，优先查阅：** [`AGENTS.md`](AGENTS.md)（数据库 schema、完整 API 端点清单、`settings.py` 函数表、开发规范）和 [`docs/`](docs/)（架构、开发者指南、API 参考）。本文件只记录命令和需要跨文件阅读才能掌握的「大局」。**改动后请同步更新 `AGENTS.md`。**

分支：`master` 稳定 / `dev` 开发（当前在 `dev`）。提交信息用中文。

## 常用命令

```bash
source .venv/bin/activate          # 必须先激活；.envrc 也会做这件事

# CLI 流水线（main.py）
python main.py                     # 完整流程：fetch → analyze → generate
python main.py fetch               # 仅抓取   analyze 仅分析   generate 仅生成报告

python app.py                      # 启动 Flask（0.0.0.0:5000）+ APScheduler 定时任务

# 测试（unittest，全部 mock、无网络、<1s）—— 必须在仓库根目录运行（测试用相对路径读 templates/）
python -m unittest tests.test_ai_provider_config
# 单个测试：
python -m unittest tests.test_ai_provider_config.ProviderRequestBuilderTests.test_regular_model_omits_disabled_max_tokens

# 查看数据库状态
python -c "from database import *; init_db(); print(get_paper_count(), 'papers,', get_analyzed_count(), 'analyzed')"
```

无 lint/format/CI 配置；主要测试文件是 `tests/test_ai_provider_config.py`（覆盖配置/认证/抓取/分析路由/模板安全/论文学习）。

## 架构要点（需跨文件阅读）

**模块分层与懒加载。** `app.py`（Web+定时）和 `main.py`（CLI）是入口；它们调用 `fetcher` / `analyzer` / `markdown_gen`；后者依赖 `database`（SQLite 全部 CRUD+迁移）、`settings`（运行时配置）、`pdf_reader`（PDF 下载+PyMuPDF 提取）；最底层是 `config`。`main.py` 在函数内部 import 以避免循环依赖——新增入口时保持这个模式。

**两层配置系统是核心。** `config.py` 是硬编码默认值/环境变量 fallback，**仅在首次运行或缺省时生效**；真正的运行时配置在 `data/settings.json`（git 忽略，含 API Key），由 Web 设置页读写。所有运行时配置都经 `settings.py` 读取，不要直接读 `config.py` 的 API 变量。

**AI 任务路由。** 独立 AI 任务包括 `basic_analysis`、`deep_reading`、`report_summary`、`recommendation`、`paper_chat`、`paper_quiz`，各自路由到自己的供应商/模型/参数。调用前用 `get_ai_task_config(task_key)` 取配置、`get_prompt_profile(task_key)` 取 Prompt 前缀，**参数必须用 `build_chat_completion_kwargs()` 构建**——绝不在业务代码里硬编码 `temperature`/`max_tokens`（思考模型会自动省略采样参数并改用 reasoning/thinking 字段）。

**两层分析流程。** 批量/每日分析走 `analyze_paper()`：**只用摘要**生成标签/评级/翻译（省钱，不下载 PDF）。论文详情页「生成报告」走 `analyze_paper_full()`：下载 PDF 全文（`get_paper_full_text(max_chars=None)` 不截断）**只写 `qa_analysis` 字段，不覆盖基础分析**。`reanalyze` 同理只更新 `qa_analysis`。

**Prompt 缓存设计。** 稳定的 Prompt 前缀放在 `prompt_profiles`。论文学习功能必须用 `build_paper_learning_messages()`，消息顺序固定为 system → 稳定任务说明 → 稳定论文上下文 → 动态历史/当前输入。不要把动态用户输入、题号状态或 session id 拼回稳定 instruction/论文上下文，否则会破坏 prompt cache 命中率。

## 关键陷阱（已踩过的坑）

- **`settings.json` 字段合并**：在 `load_settings()` 新增字段时，必须在合并逻辑里显式加 `if "key" in migrated: merged["key"] = migrated["key"]`，否则读取时新字段会被丢弃。
- **arXiv API**：`submittedDate:[... TO ...]` 查询语法实际不返回结果——改用 `cat:cs.RO` 查询 + `sortBy=submittedDate&sortOrder=descending`，再在代码里按 `published` 日期过滤。`published` 带 UTC 时区，比较必须用 `datetime.now(timezone.utc)`，否则报 offset-naive/aware 错误。
- **认证模型**：设置管理密码后，`/settings`、`/tasks`、所有写接口和敏感设置读接口都需登录；阅读清单加入/移除接口是公开例外；`GET /api/providers` 只能返回 `api_key_masked`，绝不返回明文 `api_key`。
- **数据库迁移**：在 `init_db()` 里用 `PRAGMA table_info(table)` 检查列是否存在再 `ALTER TABLE ADD COLUMN`。SQLite 已开 WAL 模式。
- **新增标签**：加到 `config.py` 的 `TAG_CANDIDATES`，避免过宽泛的标签（如 "Transformer"/"LLM"），优先具体技术方法名。
