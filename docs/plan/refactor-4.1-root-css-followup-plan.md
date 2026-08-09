# 4.1 后续重构：根目录收口、CSS 模块化与 Web 单入口

> 计划日期：2026-08-07  
> 状态：第 0 轮、1A、1B-1、1B-3 已完成；1B-2 待实施  
> 本文仅记录已确认的实施计划；本次写入不执行其中任何代码或样式变更。

## 总结

- 有必要迁移剩余根目录 Python 文件。最终根目录只保留正式 Web 入口 `app.py`，不保留兼容 shim。
- 可以删除 `main.py`。抓取、分析、推荐和组合流水线全部以 Web 页面及现有 API 为唯一正式入口。
- 有必要拆分 `static/style.css`。采用“共享基础层 + 场景页面层”，同时去重和统一组件类名，但保持现有浅色视觉风格。
- 不修改 `promo.css`，不增加深色模式；除经批准的 1B-3 抓取完整性与 `/api/fetch` 废弃参数契约外，不改变 HTTP API、SQLite schema、`settings.json` 格式或业务行为。

## 分轮实施

### 第 0 轮：固化计划与恢复测试基线 ✅ 已完成（2026-08-07）

- 第一个文件变更必须是将本计划写入 `docs/plan/refactor-4.1-root-css-followup-plan.md`，在此之前不修改代码。
- 将当前 `static/style.css` 和 `docs/changelog.md` 的未提交修改视为用户基线，全程保留。
- 修复 `test_ai_provider_config.py` 缺少 Flask application/request context 导致的 40 个既有错误；暂不拆分该测试文件。
- 阶段验收：当前 210 项测试全部通过，再进入结构迁移。

完成记录：
- `test_ai_provider_config.py` 已按 Q20 拆分为 `tests/ai_test/` 下 17 个文件（143 个方法逐字节平移，测试基座在 `common.py`），原巨石文件删除，commit `0a828df`。
- 基线验收：210 passed + 35 subtests 全绿，三层验收（unittest + pytest 随机序 + 3 次模块乱序）通过；`static/style.css`、`docs/changelog.md` 基线保留，worktree clean。

### 第 1 轮：Python 结构收口与入口调整

按风险拆为两个子轮：1A 先删除低风险根兼容模块，1B 再执行迁入与拆解。

#### 第 1A 轮：删除根兼容模块（main.py / settings.py / database.py）✅ 已完成（2026-08-07）

- 改写 5 个根模块（`analyzer.py` / `fetcher.py` / `pdf_reader.py` / `email_report.py` / `backup.py`）的 shim import 为 `source.*` 正式路径（纯同名转发，零行为变化）。
- 改写 15 个测试文件的导入；删除 2 个依赖 CLI 的测试（`LegacyRemovalTests`、`test_cli_analyze_uses_saved_concurrency`，行为均有等价覆盖）。
- 新增 `tests/test_root_boundaries.py`：根目录 `*.py` 仅 `app.py`；AST 扫描断言源码不再导入旧根模块。
- 删除 `main.py`、`settings.py`、`database.py`，不保留转发 shim。
- 活跃文档最小收口：`AGENTS.md`、`README.md`、`CLAUDE.md`、`docs/*.md` 删除 `python main.py` 用法，改为 Web `/tasks` 与 `/api/fetch|analyze|run` 入口；全面宣传性收口仍属第 3 轮。
- 验收：210 项测试全绿 + 新增边界测试；`python app.py` 启动冒烟，调度器只启动一次。

#### 第 1B 轮：迁入与拆解（config / analyzer / fetcher / pdf_reader / backup / email_report / app）

按风险拆为多个子轮：1B-1 迁移 `config.py` 与收口 `app.py`；1B-2 标签候选/评级标准可配化（待实施）；1B-3 抓取完整性修复（已实施）；后续继续 analyzer/fetcher/pdf_reader/backup/email_report 的迁入拆解。

#### 第 1B-1 轮：config.py 迁入 source/ 与 app.py 入口收口 ✅ 已完成（2026-08-07）

- `config.py` 迁至 `source/config.py`：`DB_DIR/DB_PATH` 改为基于项目根上跳两级计算，`data/` 与 `papers.db` 位置不变；删除无引用的历史常量 `OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL`
- 全部 12 处 `from config import` 改写为 `from source.config import`；清理 `source/settings` 六个模块中未使用的 config 导入（providers/runtime/prompts 整块删除，defaults/store/normalize 收窄到实际用量）；`source/web/application.py` 删除未使用的 `WEB_HOST/WEB_PORT` 导入
- `app.py` 增加明确 `main()`（初始化 + 调度器 + 开发服务器），保留模块级 Flask `app` 供测试/WSGI 使用；根目录不再有 `config.py`
- 验收：210 项测试全绿；冒烟断言 `DB_DIR == 项目根/data`、调度器单任务

#### 第 1B-3 轮：抓取完整性修复（日期窗口抓全） ✅ 已完成（2026-08-07）

- 实测复核 arXiv API：`submittedDate` 日期过滤语法可用（推翻 AGENTS.md 旧记录），单查询上限 30000 条、2000 分片
- 实弹审计确认：08-06 缺 35 篇系 08-07 定时日报抓取失败（arXiv 连接超时、重试 20 次耗尽）所致；滚动窗口重叠 + 入库去重自动补抓，已补回 42 篇缺失论文
- `fetch_latest_papers` 统一按日期窗口抓全（默认 1 天），删除按条数抓取的非分批路径与 `MAX_PAPERS_PER_CATEGORY`（原批准的"2000 安全阀"基于被证伪的 API 2000 上限前提，删除以避免大窗口截断）；`/api/fetch` 不再支持 `max_results`，请求中出现该废弃参数即返回 HTTP 400，默认最近 1 天；`/api/run` 与手动流水线抓取窗口改用 `schedule.fetch_days`
- `_fetch_date_range` 查询启用 `submittedDate` 日期窗口过滤（GMT 分钟精度）+ `max_results=30000`；代码内 `[start, end)` 过滤保留为分钟截断/秒级边界兜底
- 防 429：请求间隔默认 3→5 秒、批次间隔默认 5→10 秒（实测连续翻页在 3 秒间隔下仍可能触发软限流）
- 新增测试：日期过滤查询构造、窗口边界过滤、重复抓取去重（补抓语义）、默认窗口、`/api/run` 使用 `fetch_days`；215 项测试全绿
- 实弹验证：新逻辑补抓 08-06 35 篇 + 08-05 7 篇全部入库，数据库 08-05/08-06 恢复完整

#### 第 1B-2 轮：标签候选与评级标准可配化（待实施）

- `TAG_CANDIDATES` 与 `RATING_CRITERIA` 移入 `settings.json`（默认值=现值），设置页新增「AI 分析与评级」分组（编辑形式待定）；analyzer 改为运行时读取，normalize 补字段校验
- 抓取范围（`ARXIV_CATEGORIES`）本轮不做；后续可考虑 config.py 收缩为路径模块（`REPO_ROOT/DB_DIR/DB_PATH`）

| 现有模块 | 最终位置与职责 |
|---|---|
| `config.py` | `source/config.py`；继续提供硬编码常量，并通过项目根路径计算保证 `data/` 位置不变 |
| `analyzer.py` | 拆为 `source/analysis/`：LLM 客户端与用量、消息构建、基础/深度分析、推荐、学习问答、报告导读、批处理 |
| `fetcher.py` | 拆为 `source/ingestion/`：arXiv 日常抓取、分批抓取和单篇查询 |
| `pdf_reader.py` | 迁入 `source/documents/`：PDF 校验、存储、缓存下载和文本提取 |
| `backup.py` | WebDAV 逻辑迁入 `source/backups/`；数据库文件信息移入 `source/storage/info.py` |
| `email_report.py` | 拆为 `source/reports/email/`：邮件内容、SMTP/代理传输、发送编排；长邮件 CSS 独立为包内资源 |
| `app.py` | 唯一根入口；增加明确的 `main()`，保留可导入的 Flask `app` 供测试/WSGI 使用 |

（`settings.py` / `database.py` / `main.py` 已在 1A 删除，不再列入。）

实施要求（1A 已落实的部分：边界测试、根目录 `*.py` 仅 `app.py`）：

- 每个新包用显式 `__all__` 暴露业务接口；调用方只导入正式 `source.*` 路径。
- 清除 `source/web` 等模块中历史遗留的整块无用 import。
- 删除全部根兼容模块，不保留转发 shim。
- 增加模块边界测试：根目录 `*.py` 只能有 `app.py`，源码不得再导入旧根模块。
- 保持现有 86 条非静态路由、调度器启动语义、数据目录、PDF 缓存和邮件/WebDAV 行为不变。

### 第 2 轮：CSS 拆分、去重与组件统一

最终结构：

```text
static/css/
├── core.css
├── components.css
├── rich-text.css
└── pages/
    ├── library.css
    ├── paper.css
    ├── learning.css
    ├── tasks.css
    ├── settings.css
    ├── reports.css
    └── auth.css
```

加载规则：

- 所有业务页加载 `core.css` 和 `components.css`。
- 首页、浏览、搜索、阅读清单加载 `pages/library.css`。
- 论文详情和学习页分别加载页面 CSS，并共同加载 `rich-text.css`。
- 任务、设置、报告、登录页只加载各自页面 CSS。
- 最终删除 `static/style.css`；`promo.css` 和第三方 KaTeX CSS 保持独立、原样不动。

整理规则：

- 在 `core.css` 建立现有配色、字号、圆角、阴影和间距的语义变量，不改变整体视觉语言。
- 统一按钮为 `.btn` 加 primary/danger/warning/small 等 modifier；统一状态条、表单、Tab、空态和加载状态类。
- 更新模板和内嵌 JS 的类名，删除 `.action-btn`、`.no-data` 等重复接口。
- 解决两套 `.btn-danger` 冲突，并补齐当前模板在用但未定义的批量操作、学习状态和定时任务布局类。
- 将固定的内联样式迁入对应页面 CSS；仅允许进度百分比、图表坐标、动态颜色等运行时值继续内联。
- 修复明确缺陷：移动端布局破损、缺失 focus 状态和明显低于可读标准的次要文字；其余页面保持视觉等价。
- 不引入 Sass、PostCSS、Node 或新的前端构建步骤。

### 第 3 轮：文档收口

- 更新 `AGENTS.md`、`README.md`、架构、开发者、用户和 Agent 指南，删除所有正式 CLI 使用说明（1A 已做最小收口，此处做全面收口）。
- 将 `/tasks`、`/api/fetch`、`/api/analyze`、`/api/run` 说明为原 CLI 能力的 Web 替代入口。
- 更新模板维护文档，记录 CSS 文件加载矩阵和“共享组件/页面专属样式”的归属规则。
- 在 `docs/plan/test-suite-modularization-followup.md` 单独记录 4222 行测试文件的后续领域拆分计划，本轮不实施。
- 历史 changelog/已完成计划可以保留 `main.py` 的历史描述；活跃使用文档不得继续指导用户运行它。

## 测试与验收

- Python：`py_compile` 通过，完整 210+ 项测试全绿，86 条路由契约不变。
- 边界：根目录仅有 `app.py`；不存在旧根模块 import；`main.py` 和所有兼容 shim 已删除。
- 行为：抓取、分析、推荐、报告、邮件、WebDAV、PDF、论文学习和定时任务测试保持通过。
- 静态资源：新增测试验证各模板加载正确 CSS、所有 CSS URL 返回 200、旧 `style.css` 不再被引用。
- CSS：检查模板/动态 HTML 使用的类均有定义；固定内联样式和冲突选择器清理完成。
- 视觉：使用现有 headless Firefox，对首页、浏览、搜索、论文详情、论文学习、任务、设置、报告、阅读清单和登录页生成桌面与移动端前后截图矩阵；除已列缺陷修复外不得出现布局或风格漂移。
- 最终从 `python app.py` 启动，确认数据库初始化和 APScheduler 只启动一次。

## Sol-Luna 执行编排

- 使用三个固定所有者，分轮顺序执行：文档所有者、Python 所有者、CSS 所有者；不并发修改共享文件。
- 文档所有者先写计划，最后更新文档；Python 所有者负责测试基线、所有 Python 迁移及旧文件删除；CSS 所有者负责 `static/`、`templates/` 和 CSS 专项测试。
- 每轮都由 Sol 检查真实 diff、变更路径、测试输出和截图后再决定是否进入下一轮。
- 本计划不使用 OpenCode/DeepSeek。若 Luna Max 的准确模型身份或权限无法证明，则按 `$sol-luna` 规则停止，不静默替换执行模型。

## 明确假设

- 允许破坏旧 Python import 和 CLI 兼容性，但不允许破坏 Web 功能。
- 不进行数据库迁移，不修改运行时数据和配置格式。
- 保留当前浅色视觉风格；只修复已确认的 CSS 缺陷。
- `promo.css`、about/vision 页面和测试巨石拆分均不属于本轮实现范围。
