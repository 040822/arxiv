# Q20 测试巨石拆分计划（含验收流程）

> 计划日期：2026-08-07
> 来源：`docs/plan/project-review-2026-07-10.md` Q20（唯一测试文件 4224 行 / 143 个方法全在一个 TestCase 中，文件名严重名不副实）
> 实施方式：按功能拆分到 `tests/ai_test/` 子目录，只平移不改逻辑，三层验收证明功能不变

---

## 1. 目标与原则

- 新建 `tests/ai_test/`，把 4224 行 / 143 个方法的 `tests/test_ai_provider_config.py` 按功能拆成 19 个文件（17 个测试文件 + `common.py` + `__init__.py`）
- **只平移不改逻辑**：方法体、类名、断言原样保留；仅调整模块引用方式
- 验收以"三层等价性"证明功能不变；**全部验收通过后才删除原文件**
- 其余 11 个既有测试文件原地不动

## 2. 前置准备与基线（实施第一步）

1. `cp tests/test_ai_provider_config.py /tmp/opencode/test_ai_provider_config.py`（AST 对比需要旧版，保留至验收完成）
2. 跑基线：`python scripts/run_all_tests.py`，记录三阶段全绿 + 总测试数 N
3. 生成可对比基线：`python -m pytest tests/ -q --randomly-seed=42 --junitxml=/tmp/opencode/before.xml`
4. 记录双收集器基线数量：`pytest tests/ --collect-only -q` 与 `unittest discover -s tests` 的计数
5. 记录覆盖率基线：`python -m pytest --cov=. --cov-report=term-missing tests/`

## 3. 目录结构与方法映射

### 共享基座 `tests/ai_test/common.py`

从巨石原样迁入约 120 行：`FakeArgs`、`FakeRequest`、`DummyOpenAI`、`DummyHttpxClient`、`install_import_stubs()`、`_plain_jsonify`、`setup_web_test_base()`、`teardown_web_test_base()`，以及模块级全局 `pipeline_orchestrator`、`pipeline_scheduler`、`web_application`、`web_auth`、`web_learning_api`、`web_pages`、`web_papers_api`、`web_providers_api`、`web_settings_api`、`web_tasks_api`、`_WEB_APP`、`_WEB_APP_CONTEXT`。

> **关键约束**：`web_*` 全局由 `setup_web_test_base()` 在 `setUpClass` 时绑定，测试文件**不得**在模块顶层 `from common import web_papers_api`（拿到 None），必须通过 `common.X` 属性动态访问或在 `setUpClass` 赋值给 `cls.X`。

### 测试文件（方法数合计 = 143）

| 新文件 | 来源 | 方法数 |
|---|---|---|
| `test_request_builder.py` | `ProviderRequestBuilderTests` | 6 |
| `test_ai_task_settings.py` | `AiTaskSettingsTests` | 19 |
| `test_task_logs_db.py` | `TaskLogDatabaseTests` | 3 |
| `test_web_providers_api.py` | `ProviderEndpointTests` provider 部分 | 10 |
| `test_web_auth.py` | `ProviderEndpointTests` auth 部分 | 11 |
| `test_web_papers_api.py` | `ProviderEndpointTests` papers 部分 | 6 |
| `test_web_settings_api.py` | `ProviderEndpointTests` settings/tasks 部分 | 10 |
| `test_pipeline.py` | `ProviderEndpointTests` pipeline 部分 | 11 |
| `test_analyzer_routing.py` | `AiCallRoutingTests` | 10 |
| `test_prompt_validation.py` | `PromptAndReportSafetyTests` prompt 校验 3 个方法 | 3 |
| `test_report_and_recommendation.py` | `PromptAndReportSafetyTests` 报告转义/推荐 3 个方法 | 3 |
| `test_rating_migration.py` | `PromptAndReportSafetyTests` 评级迁移 4 个方法 | 4 |
| `test_ai_usage_and_learning.py` | `PromptAndReportSafetyTests` 用量/学习表 2 个方法 | 2 |
| `test_fetcher_batch.py` | `FetchBatchTests` | 4 |
| `test_runtime_propagation.py` | `RuntimeSettingPropagationTests` | 7 |
| `test_backup_service.py` | `BackupServiceTests` | 3 |
| `test_email_report.py` | `EmailReportTests` | 15 |
| `test_template_safety.py` | `TemplateSafetyTests` | 12 |
| `test_schedule_retry.py` | `ScheduleRetryTests` | 4 |

另加空 `tests/ai_test/__init__.py`。各文件按需保留原导入（`build_chat_completion_kwargs`/`settings_store`/`db_connection`/`report_renderer`/`import app as app_module` 等），保留 `if __name__ == "__main__": unittest.main()`。

### 引用适配点

- `RuntimeSettingPropagationTests` 2 处裸全局 `web_papers_api.request = ...` → `common.web_papers_api`
- `with _WEB_APP.test_request_context()` → `with common._WEB_APP...`
- `ScheduleRetryTests.import_app_with_temp_settings` 的 `global` 语句 → 写 `common.*` 属性
- `DummyOpenAI` 静态字段重置 `tearDown` 保留在使用它的文件

## 4. 运行脚本与配置适配

- `tests/ai_test/__init__.py`（空）：unittest discover 只递归进包，缺它子目录不被发现
- `scripts/run_all_tests.py` `test_modules()`：追加 `tests/ai_test/test_*.py`，返回 `ai_test.test_x`，乱序验证命令 `python -m unittest tests.<module>` 才能加载
- **无需改动**：`.github/workflows/tests.yml`、`.coveragerc`（`tests/*` 的 fnmatch `*` 跨 `/` 匹配子目录；验收第 5 步实测确认）

## 5. 文档同步

- `CLAUDE.md`：测试命令示例改新路径（如 `python -m unittest tests.ai_test.test_request_builder`），测试文件描述更新
- `AGENTS.md`：第 8 节补充 `tests/ai_test/` 布局；Q20/Q21 状态行更新
- `docs/plan/project-review-2026-07-10.md`：Q20 标记 ✅ 已完成
- `docs/changelog.md`：追加条目

## 6. 删除原文件（验收后执行）

- 删除 `tests/test_ai_provider_config.py`。**不做 shim**（pytest 会收集导入的类导致双跑，第二层验收会暴露）

## 7. 验收流程（三层等价性，在删除旧文件前完成）

### 第一层：静态等价 —— AST 逐方法字节对比（一票否决）

一次性脚本 `/tmp/opencode/verify_split.py`（不入库），用 `ast` 解析旧文件与全部新文件，提取 `{方法名: 方法源码}`：

- 判据 A：**143 个 `test_*` 方法名集合完全一致**（无增删改名）
- 判据 B：**每个方法源码逐字节相同**；`common.py` 迁移的共享代码同样逐字节比对
- 为什么逐方法而非整文件：杂烩类拆分后类名会变，但方法体必须一字不差
- 输出 `143/143 一致`，任何差异 = 验收失败（阻止顺手改 bug 混入）

### 第二层：收集等价 —— 无漏跑、无双跑

- `pytest tests/ --collect-only -q` 数量 = 基线 N，方法名集合 = 基线
- `unittest discover -s tests` Ran N tests，与 pytest 一致
- 双跑/消失均会使数量偏离 N，立即暴露

### 第三层：运行等价 —— 行为没变

- 拆分后跑 `pytest tests/ -q --randomly-seed=42 --junitxml=/tmp/opencode/after.xml`，脚本以**方法名为 key** 对比 before/after 的 outcome（passed/failed/skipped）逐一对上；允许的唯一差异是 `classname` 模块前缀（`tests.test_ai_provider_config` → `ai_test.test_x`）；先断言 143 个方法名唯一
- `python scripts/run_all_tests.py` 三阶段全绿（unittest / pytest seed=42 / 3 次模块乱序）——乱序阶段专门验证 `common.py` 跨文件共享状态无顺序依赖
- 覆盖率前后对比（±0.5% 容差），防隐性打折
- 独立性抽查：抽 3-5 个新文件单独跑（`python -m unittest tests.ai_test.test_email_report`、`pytest tests/ai_test/test_pipeline.py`），确认不依赖批内导入副作用

### 验收判据汇总

| 层级 | 判据 | 证明 |
|---|---|---|
| AST 对比 | 143/143 方法名一致且源码逐字节相同 | 代码确实没改 |
| 收集 | 双收集器数量一致 = N | 没漏跑、没双跑 |
| 结果 | 按方法名 outcome 集合与基线一致 | 行为没变 |
| 全量 | run_all_tests.py 三阶段全绿 | 顺序无关性 |
| 覆盖率 | source 覆盖率前后一致 | 无隐性打折 |

### 执行顺序

前置基线 → 拆分 → 文档同步 → **验收三层** → 通过后删除旧文件 → `git diff --stat` 核对改动集（新增 19 文件 + 删 1 + 改 run_all_tests.py + 文档）→ 最终全量复跑确认

## 8. 风险与注意点

- 全局状态共享语义不变：`install_import_stubs()` 幂等，`DummyOpenAI` 静态字段跨文件共享与单文件时代一致
- `tests/` 无 `__init__.py`（命名空间包），顶层文件照旧；`ai_test` 为常规包，相对导入 `from . import common` 在两种运行器下均可用
- 实施中发现的任何真实 bug（若有）单独记录，不在本次拆分中顺手修复，否则第一层验收直接判失败

## 9. 实施结果与验收记录（2026-08-07）

### 实施完成

- 拆分产物：`tests/ai_test/` 17 个测试文件（143 个方法）+ `common.py` 共享基座 + `__init__.py`，方法数按计划逐一核对（每文件 OK，总计 143）
- 原 `tests/test_ai_provider_config.py`（4224 行）已删除，不做 shim；旧文件副本保留在 `/tmp/opencode/test_ai_provider_config.py` 供对比
- `scripts/run_all_tests.py` `test_modules()` 已支持 `ai_test.test_x` 子目录模块点路径
- 文档同步：CLAUDE.md / AGENTS.md / project-review Q20、Q21 / changelog
- 改动集：新增 20 文件（`tests/ai_test/` 19 + `docs/backup/` 计划文档）+ 删除 1 + 修改 5

### 生成器实施中发现并修复的 3 个问题（均被第一层验收兜底暴露）

1. `ast` 的 `FunctionDef.lineno` 在此环境不含装饰器行，切片丢 `@classmethod` → 切片起点改为 `decorator_list` 最小行
2. 类体渲染双重缩进（方法体整体 8 空格）→ 改为先剥 4 空格再加 4 空格
3. `reindent_part` 对 0 缩进的字符串行做 `line[4:]` 截断，损坏 `weak_prompt` 三引号字符串内容 → 仅对 `startswith("    ")` 的行处理

### 三层验收结果（基线：210 tests / 66% 覆盖率；2026-08-07 两次独立执行，含 opencode server 崩溃后全量重跑，结果一致）

| 层级 | 验收项 | 结果 |
|---|---|---|
| 一、静态等价 | AST 逐方法字节对比（`/tmp/opencode/verify_split.py`） | ✅ 143/143 方法名一致、源码逐字节相同（含共享基座共 155 项），无新增 `test_*` 方法 |
| 二、收集等价 | pytest `--collect-only` | ✅ 210 = 基线 210 |
| | unittest discover | ✅ Ran 210 tests, OK |
| | 方法名集合（before vs after） | ✅ 完全一致：缺失 0、新增 0（无漏跑、无双跑） |
| 三、运行等价 | pytest seed=42 逐测试 outcome（before.xml vs after.xml） | ✅ 210/210 用例名称与结果逐一对应，无差异（210 passed + 35 subtests） |
| | `scripts/run_all_tests.py` 全量（unittest + pytest + 3 次乱序） | ✅ ALL PASS（乱序验证跨文件共享状态无顺序依赖） |
| | 覆盖率 | ✅ 66%（5704 statements / 1945 missing）前后完全一致；`tests/ai_test/` 被 `.coveragerc` 的 `tests/*` 正确排除 |
| | 独立性抽查（test_email_report / test_request_builder+task_logs_db / test_pipeline 单独运行） | ✅ 全部 OK |

**结论：拆分后测试组功能与拆分前完全等价，Q20 验收通过。**

