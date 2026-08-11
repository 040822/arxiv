# 更新日志

## 未发布
### 认证默认关闭与只读访客界面
- 首次启动/无密码升级自动生成随机管理密码，仅在首次日志输出；损坏 `settings.json` 时拒绝覆盖并中止初始化
- 管理密码改用 scrypt 慢哈希，旧 SHA-256 在成功登录后安全比较并原位升级；新增本机重置脚本与配置备份
- 移除清除密码能力，登录增加每 IP 15 分钟 5 次失败限制，阅读清单和任务进度不再公开
- paper、首页、浏览页和日报详情对匿名访客隐藏写控件；paper 页内容仍公开只读

### 4.1 验收审查修复（3/4a/5）
- 补齐 `.learning-question.q-pending` 样式（learning.css，与默认左边框一致 `#1a73e8`），消除 paper_chat 未答题 JS 状态类"使用但未定义"
- 消除 rich-text.css 与 learning.css 之间 `.learning-message-body` 系列 5 处重复：基础排版（font-size/line-height/color）合并进 rich-text.css（跨论文详情与学习页共用），learning.css 删除重复的 p/p:last-child/strong/code 规则，并补回缺失的 `.learning-message-body code` 行内代码规则；两页视觉零变化（paper.html 的 `.qa-answer` 仍后加载覆盖排版值）
- 邮件运行时配置归一化恢复阈值钳制：`DEFAULT_IMPORTANT_SCORE_THRESHOLD`/`DEFAULT_OVERVIEW_LIMIT` 移入 `source/reports/email/config.py`（消除 content→config 潜在循环导入，常量单一来源），`_normalize_runtime_config` 对 `important_score_threshold`（0-100）与 `overview_limit`（0-50）钳制，非法值回退默认（80/20）；顺带修复 `import re` 函数内导入
- 新增测试：`_normalize_runtime_config` 运行时钳制（200→100、9999→50、-1/「abc」→0/默认）
- 验收：236 项测试全绿（原 235 + 新 1），CSS 类覆盖契约通过，重复选择器扫描 learning-message-body 相关归零，py_compile 与 `git diff --check` 无告警

### settings.json 重建守卫
- 事故复盘：2026-08-08 执行 4.1 第 1B-3 轮时误以默认模板整体重建 `data/settings.json`，导致 xiaomi 供应商、deepseek api_key 与自定义模型路由丢失（已从每日快照恢复）
- 新增 `source/settings/guardrail.py`（只读、无副作用）：比较 providers/ai_tasks/email_report 三个用户配置分区与默认模板是否全等，检测"疑似被默认模板重建"；email_report 比较时剔除 `last_*` 运行时状态字段；不比较 prompts 等低频修改项
- `create_app()` 启动时对重建告警记 `logger.warning`（只写日志、不阻断服务启动，systemctl 下见 journalctl）
- 新增 `scripts/check_settings_guardrail.py`：显式检查真实文件，退出码 0=OK / 1=疑似重建（可挂 cron 或写进计划完成清单）
- 新增 `tests/test_settings_guardrail.py`（4 项）：只验证判定逻辑本身（模板副本→3 条告警、健康配置→无告警、email_report 重置但残留 last_*→仍告警、仅 providers 重建→告警），不读取真实文件，CI/本地行为一致
- AGENTS.md 新增 7.8「运行时数据操作规范」：禁止以"同步默认值/现值"为由重建或模板覆盖 `data/settings.json`，配置修改只能走设置页 API 或逐字段编辑（先备份、后声明 diff），涉及 `data/` 的计划完成清单必须包含守卫检查；运维章节补充手工编辑前备份说明
- 验收：235 项测试全绿（原 231 + 新 4），unittest/pytest/3 次乱序通过；`scripts/check_settings_guardrail.py` 对恢复后的真实配置输出 OK 退出 0

### 4.1 根模块迁入、CSS 模块化与文档收口
- 删除根目录 `analyzer.py`、`fetcher.py`、`pdf_reader.py`、`backup.py`、`email_report.py`；正式接口迁入 `source.analysis`、`source.ingestion`、`source.documents`、`source.backups` 与 `source.reports.email`，根目录仅保留 `app.py`
- `source.analysis` 按 core/papers/learning/report/batch 拆分业务实现，并独立提取客户端/代理、稳定消息构建、JSON 修复与 token 用量；邮件拆为 config/content/transport/service，邮件 CSS 改由 `importlib.resources` 读取；SQLite 文件占用查询迁入 `source.storage.info`
- 清理 Web Blueprint 从旧巨石遗留的无用整块 import，调用方与测试统一使用 `source.*` 路径；边界测试禁止旧根模块 import，86 条非静态路由和认证契约保持不变
- 删除 `static/style.css`，新增 core/components/rich-text 与七个页面 CSS；11 个业务模板按资源矩阵加载，统一 `.btn` 与 `.empty-state`，解决 danger 按钮冲突并补齐批量操作、学习和调度样式
- 清理固定内联样式，增加 focus-visible、移动端堆叠和次要文字对比度修复；新增 CSS URL/加载矩阵/类覆盖契约和无 Selenium 的 Firefox/geckodriver 截图脚本
- 同步 README、AGENTS、架构、开发者、用户、Agent 与模板维护文档；4.1 计划标记完成
- 验收：执行前 227 项、最终 231 项测试；`--quick` 为 231 passed，完整入口的 unittest、随机顺序 pytest 与 3 次模块乱序均通过；全部 Python 文件通过 `py_compile`，`git diff --check` 无告警
- Web/静态验收：86 条非静态路由契约不变，CSS 资源矩阵全部返回 200，逐模板所用类均由该页面实际加载的 CSS 定义；before/after 各 22 张截图覆盖 11 页桌面与移动端
- 启动验收：因本机 5000 端口已有另一实例占用，使用不绑定端口的等价 `app.main()` 冒烟；连续初始化两次时 APScheduler 仅启动一次，且仅保留一个 `daily_pipeline` job

### Standards 修复与 `/api/fetch` 契约收口
- `/api/fetch` 对废弃的 `max_results` 参数执行严格拒绝：参数只要出现（含空值或与 `days`/`date` 混传）即返回 HTTP 400，且不创建任务日志、不调用 arXiv；正常优先级保持 `date > days > 默认最近 1 天`
- 修复论文处理页抓取分类 `.form-row` 缺失闭合标签，删除最大数量与“已抓取日期跳过”旧说明，改为重叠日期窗口 + 数据库去重的失败补抓语义
- 抓取设置部分 POST 会保留未提交的当前值；设置页加载与空输入统一回退到 `request_delay=5`、`batch_days=30`、`batch_delay=10`，不改写已有 `data/settings.json`
- 测试基座新增 context 泄漏检测与 `jsonify` 恢复，统一测试脚本同时解析 unittest/pytest 失败摘要，并在名称无法解析时输出明确诊断



### 抓取完整性修复：日期窗口抓全（4.1 第 1B-3 轮）
- 修复手动抓取固定 50 条上限：`fetch_latest_papers` 统一按日期窗口抓全（默认最近 1 天），删除按条数抓取的非分批路径与 `MAX_PAPERS_PER_CATEGORY` 常量；`/api/fetch` 默认抓最近 1 天并删除 `max_results` 参数，tasks 页同步移除「最大数量」输入
- `/api/run` 与手动组合流水线的抓取窗口改为 `schedule.fetch_days`（与定时日报一致）
- `_fetch_date_range` 查询启用 arXiv API 官方 `submittedDate` 日期过滤（GMT 分钟精度），消除无过滤查询时"跳过区"消耗翻页额度导致的深回填截断；代码内 `[start, end)` 日期过滤保留为分钟截断/秒级边界的兜底；单查询上限提升至 API 上限 30000
- 复核并推翻 AGENTS.md「submittedDate 过滤器不工作」的历史结论：实测语法可用（如 `cat:cs.RO AND submittedDate:[202608060000 TO 202608070000]` 精确返回当日 35 篇）
- 防 429：抓取请求间隔默认 3→5 秒、批次间隔默认 5→10 秒（实测连续翻页在 3 秒间隔下仍可能触发 arXiv 软限流）；同步更新 `data/settings.json` 现值与设置页 UI 默认
- 滚动窗口重叠 + 入库去重自动补抓语义补测试锁定（抓取失败后次日运行自动补回，不重复入库）
- 新增测试：日期过滤查询构造、窗口边界过滤、重复抓取去重、默认窗口 1 天、/api/run 使用 fetch_days

### config.py 迁入 source/ 与 app.py 入口收口（4.1 第 1B-1 轮）
- `config.py` 迁至 `source/config.py`：`DB_DIR/DB_PATH` 改为基于项目根计算（上跳两级），`data/` 与 `papers.db` 位置不变；删除无引用的历史常量 `OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL`
- 全部 12 处 `from config import` 改写为 `from source.config import`；清理 `source/settings` 六个模块中未使用的 config 导入（providers/runtime/prompts 整块删除，defaults/store/normalize 收窄到实际用量）
- `source/web/application.py` 删除未使用的 `WEB_HOST/WEB_PORT` 导入
- `app.py` 增加明确 `main()`（初始化 + 调度器 + 开发服务器），保留模块级 Flask `app` 供测试/WSGI 使用；根目录不再有 `config.py`

### 删除根兼容模块（4.1 第 1A 轮）
- 删除 `main.py`、`settings.py`、`database.py` 三个根兼容模块，不保留转发 shim；CLI（fetch/analyze/run）不再提供，抓取、分析、推荐评分以 Web `/tasks` 与 `/api/fetch`、`/api/analyze`、`/api/run` 为唯一入口
- 5 个根模块（`analyzer.py`/`fetcher.py`/`pdf_reader.py`/`email_report.py`/`backup.py`）的 shim 导入全部改写为 `source.settings` / `source.storage` 正式路径；`source.web`/`source.pipeline` 等业务包不受影响
- 15 个测试文件改导入 `source.*`；删除 2 个依赖 CLI 的测试（`LegacyRemovalTests`、`test_cli_analyze_uses_saved_concurrency`，行为均有等价覆盖）；移除测试中 `settings.DB_DIR` 死赋值
- 新增 `tests/test_root_boundaries.py` 边界测试：根目录 `*.py` 仅允许正式入口与 1B 待迁模块，AST 断言源码不再导入旧根模块
- 活跃文档与 `templates/index.html` 空态提示同步收口，不再指导运行 `python main.py`

### Q20 测试巨石拆分
- `tests/test_ai_provider_config.py`（4224 行 / 143 个方法）按功能拆至 `tests/ai_test/`：17 个测试文件 + 共享基座 `tests/ai_test/common.py`（FakeArgs/FakeRequest/DummyOpenAI/DummyHttpxClient/install_import_stubs/setup_web_test_base 与 web/pipeline 模块引用）
- 拆分类命名：`ProviderApiTests`/`AuthApiTests`/`PapersApiTests`/`SettingsTasksApiTests`/`PipelineTests`、`PromptValidationTests`/`ReportAndRecommendationTests`/`RatingMigrationTests`/`AiUsageAndLearningTests`；其余 10 个类整体平移保留原名
- 验收：AST 逐方法对比 143/143 源码逐字节一致；pytest/unittest 双收集器 210 前后一致无漏跑双跑；seed=42 逐测试 outcome 一致；`run_all_tests.py` 三阶段全绿；覆盖率 66% 前后不变
- `scripts/run_all_tests.py` 乱序验证支持子目录（返回 `ai_test.test_x` 模块点路径）

### 长输入任务指令后置，修复 flash 模型深度阅读漏题
- 深度阅读（`deep_reading`）与手动导入（`paper_import`）的消息顺序从 `system → instruction → 动态数据` 调整为 `system → 动态数据 → instruction`：PDF 全文可达数万 token，指令若放在全文之前会被长上下文“淹没”，flash 类模型会漏答部分问题（实测约 30% 概率只输出 Q1 或 Q6 一个条目，且 `finish_reason=stop` 无法用截断检测兜底）
- 同输入实测：指令后置后 flash 深度阅读 4/4 完整输出 Q1-Q6（原结构 10 次中 3 次失败）；pro 模型两种顺序均稳定
- 短输入任务（基础分析、个性化推荐、报告导读）保持 `system → instruction → 动态数据` 顺序，不存在淹没问题且可保留更长的稳定前缀缓存
- 缓存代价：跨论文时丢失 instruction 约 654 字符的缓存命中（对 48k tokens 输入约 1%）；同论文重试/自动补全调用仍完整命中前缀缓存
- 更新 `tests/test_ai_provider_config.py` 深度阅读消息顺序断言（动态数据在 `messages[1]`、instruction 在 `messages[2]`）

### 模型供应商与功能路由解耦
- 模型供应商只管理 API Key、Base URL 与模型列表缓存；模型、输出长度、Temperature 采样控制和思考模式统一迁入各功能模型路由
- 功能路由模型字段支持下拉建议与手动输入，并可按供应商刷新模型列表
- 每个功能路由新增独立连接测试，明确返回测试的功能、供应商、模型、耗时和思考能力提示；测试使用当前未保存草稿
- 设置页明确七类功能的非思考模型 Temperature 推荐值；未启用 Temperature 或使用思考模型时省略该参数，其他采样参数不发送
- 设置结构版本升级到 v3：功能路由删除旧的六个额外采样字段及其开关；迁移会丢弃旧自定义值，外部提交的旧字段也会被忽略，中性默认交由模型自身行为处理
- 旧版供应商推理字段自动迁移为显式任务路由；被路由引用的供应商禁止删除

### 4.2 SQLite 稳定性与版本迁移
- 全部生产数据库调用改用兼容式托管连接，统一提交/回滚/关闭，并显式启用 WAL、外键和 5000 ms `busy_timeout`
- 新增 `schema_migrations` 顺序迁移器；迁移前创建 SQLite 一致性快照，最近保留 3 份，失败时回滚并中止启动
- v2 迁移合并历史重复分析记录并建立 `uq_analysis_paper_id` 唯一索引；分析插入改为原子 `INSERT OR IGNORE`
- 数据库快照能力由 WebDAV 备份和 schema 迁移共同复用；新增并发、锁竞争、迁移回滚、快照失败与版本异常测试

### 4.1 超大文件拆分（目标 v0.6.0）
- `app.py`、`database.py`、`settings.py` 收缩为 14/8/9 行兼容 shim，实际实现迁入 `source/`
- Flask 路由按 auth/pages/papers/learning/tasks/settings/providers 拆成 7 个 Blueprint，保留全部 86 条 URL + method 契约
- SQLite 按 connection/schema/papers/analysis/operations/reports/learning 拆分，日报 HTML 渲染移入独立 reports 模块
- settings 按 coercion/defaults/normalize/thinking/store/providers/prompts/runtime 拆分；Q9 改为递归 deep merge，新增顶层字段无需维护两处白名单
- 定时与手动组合流水线下沉到 pipeline 模块，共享互斥锁；`import app` 不启动 scheduler
- 拆分前已打 v0.5.0 标签（2026-07-12，提交 `1f93232`）作为回退点，出错时可回退到拆分前稳定版本
- 根模块公开导入、CLI、数据库 schema、设置格式和 HTTP 行为保持兼容；新增真实 Flask 路由与鉴权契约测试

---

## v0.5.0 (2026-07-12)

> **版本性质**：项目全局规划调整后的**拆分前稳定快照**。本次规划（`docs/plan/` 项目评审与规划文档）决定拆分多个超大文件并构建 `source/` 分层结构；因拆分重构对整个项目影响极大，在拆分开始前打本标签（提交 `1f93232`）作为回退点——若拆分出错可随时回退到本版本。

### 论文学习功能
- 新增论文对话与主动问答学习功能：单篇论文学习页支持基于 PDF 全文的自由讨论、主动问答练习（quick3/standard6）和苏格拉底追问，配套 `paper_chat_messages` / `paper_quiz_*` 学习记录表与学习 API

### 论文学习页
- 自由讨论 / 主动问答练习 / 苏格拉底追问三模式从纵向堆叠改为 Tab 切换，聊天默认激活
- 复用设置页 Tab 样式，Tab 栏 sticky 顶部，移动端横向滚动
- hero 区简化为面包屑 + 标题 + 星级评级单行，移除右侧引导语
- Tab 内容区限制 max-width 760px 保证阅读舒适
- 聊天气泡加圆角阴影和指向感；AI 回复支持轻量 markdown 渲染（粗体/行内代码/列表/段落），用户消息保持纯文本
- 问答练习反馈新增彩色评分徽章（绿/琥珀/红）、分区标签胶囊和浅蓝底改进版答案块；题目卡片加状态条（待答蓝/已答绿）
- 苏格拉底追问改为对话气泡风格与 quiz 区分
- 最近练习记录改为图标胶囊，无历史时隐藏
- 三个输入框（自由讨论/问答/苏格拉底）统一 padding、圆角、focus 蓝边光晕和 placeholder 颜色；自由讨论发送按钮改为顶部对齐
- 修复 `.empty-state.compact` 引用无 CSS 规则的 bug
- 自由讨论、主动问答练习、苏格拉底追问三处的 AI 回复、题目、评分反馈与改进版答案，从自写轻量 markdown（`renderMarkdownLite`，仅支持段落/行内代码/粗体/列表）升级为 marked + KaTeX 渲染，支持完整 Markdown 语法与 LaTeX 数学公式（`$...$`/`$$...$$`/`\(...\)`/`\[...\]` 四种定界符）
- 数学片段先替换为占位符再交 marked 解析，避免 `$a*b*$` 中的 `*` 被 marked 当强调符号误解析；还原后由 `renderMathInElement` 在 DOM 上渲染公式
- 安全：marked 默认透传原始 HTML，新增 `sanitizeDom` 在 DOM 层兜底——移除 `script/style/iframe/object/embed/link/meta` 标签、所有 `on*` 事件属性、`javascript:` 协议的 href/src；用户输入始终走 `escapeHtml` 不经 marked
- 引入 vendor 静态资源：marked.min.js、katex.min.js + katex.min.css、auto-render.min.js 及 KaTeX 字体（已在 git 追踪）
- 已知边界：数学占位符 `@@KX{n}@@` 若与 AI 原文里恰好出现的该字面字符串碰撞会误替换，极端罕见且 sanitizeDom 兜底，实际风险可忽略

### 报告邮件
- 新增每日报告邮件发送与摘要版日报：SMTP 配置、邮件专用 HTML 摘要版（报告导读、重点精读、快速速览）
- 邮件头部改为三色渐变（`#1a1a2e → #16213e → #0f3460`），日期/计数 badge 分色，统计标签大写，副标题带 emoji
- 章节标题改为左侧 4px 蓝色短条样式
- 重点精读卡片头部改为深色渐变带，标题/作者/分类/推荐分/星级全部上移到深色头部；正文区只保留摘要和推荐语，消除原三重左强调边嵌套
- 快速速览改为左侧序号列 + 右侧内容的双栏布局，每条论文独立编号
- 本日研究速览新增「热门方向」胶囊条，统计当天论文 tags 频次取前 5，AI 散文降级为下方 muted 段落
- 所有链接改为胶囊按钮样式，首个链接升级为主按钮；链接内联 `text-decoration:none` 避免邮件客户端强制下划线
- 统一配色为 `#0f3460` 主色 + 琥珀色星级，邮件宽度收窄至 680px
- 每日任务发送前检查 `last_sent_report_date`，同一日报已成功发送时跳过 AI 导读和 SMTP，避免重复投递；邮件失败不更新去重日期，后续每日任务仍可重试；手动测试发送保持可重复执行，但不计入每日任务去重
- 邮件重点精读阈值（`important_score_threshold`，默认 80）与速览上限（`overview_limit`，默认 20）可配置

### 设置页与 UI
- AI 设置 Tab 从 11 张平铺卡片重组为左侧 sticky 侧栏 + 右侧 5 个可折叠分区（模型供应商 / 路由与参数 / 个性化与内容 / 抓取与网络 / 报告邮件）
- 侧栏目录支持滚动高亮（IntersectionObserver）和点击平滑跳转，分区折叠状态记忆到 localStorage
- Tab 栏改为 sticky 顶部常驻；测试连接卡片合并到「路由与参数」分区
- 报告邮件发送从数据库 Tab 移到 AI 设置 Tab 新增第 5 分区
- 数据库 Tab 3 张卡片归并为 2 个可折叠分区（数据库概览 / 云备份）
- 统计网格加左侧色条、数字放大；供应商激活态加蓝色光环、当前使用 badge 改蓝底白字胶囊
- 设置页卡片标题加左侧浅灰短条形成二级层次
- `.action-status` 三色状态条统一加左 3px 强调边（info 蓝 / success 绿 / error 红），圆角 8px
- 设置页和论文学习页响应式布局优化，窄屏自动堆叠或横向滚动
- 修复前端交互体验问题（项目评审 F7/F8/F10/F12）：学习页交互与列表页细节修正

### 宣传页
- 新增双版本项目宣传页：`/about` 公开项目宣传页 + `/vision` 实验室科研情报基础设施愿景页（独立 `promo.css` 深色样式）
- vision 愿景页从深色主题整体转为亮色高对比度主题：正文 `#10203b`/`#f6f8fb` ≈ 15:1、次要文字 `#5f6e84` ≈ 6.5:1，全部满足 WCAG AA；原深底上多处 2–3.5:1 的低对比度灰蓝（→箭头、各节眉标、能力列标题、roadmap 编号等约 10 处）随之消除
- hero「研究记忆」示意图保留深色控制台卡片作为视觉锚点，青色辉光在深底上保持效果；控制台内浅字同步提亮保证可读
- 修复按钮文字色被全局 `.promo-body a { color: inherit }` 强行继承的低对比度 bug：该规则特异性 (0,1,1) 高于按钮自身 (0,1,0)，导致 about 页「进入系统/浏览每日论文」深字压在 navy 底、final CTA「进入每日论文」白字压在白底；改用 `:where(:not(.promo-button):not(.promo-vision-button):not(.promo-text-link))` 零特异性排除按钮，一次性修复 about 6 个 + vision 3 个按钮链接，非按钮链接行为不变
- vision 主按钮由亮青 `#08a89f`（白字对比度仅 2.95:1）改为深青 `#0c7269`（5.78:1），与 about 主按钮白字 on 深底风格一致
- 全页面字号上调：vision 暗色版 34 处 8–10px → 12–14px（正文 ≥14、标签 ≥12）、about 亮色版 8 处 9–11px → 12–13px，消除中文小字不可读问题
- 字重规范化：全文件 850→800、750→700、650→600，避免回退系统字体（PingFang/雅黑/Segoe UI 仅 400/500/600/700）字重吸附导致跨平台显示不一致
- 布局修复：vision 竞品栅格「本项目」卡由 `grid-column: 2/4`（漂浮留白）改为 `1/-1` 整行置底强调；about 优势卡 h3 加 `padding-right` 防与右上装饰元素贴边；vision 主视觉图加 18px 圆角与 about 控制台对齐
- 配色清理：vision 状态丸改用 teal/blue/warm 实色（原 lime 在白底不可见）；竞品「本项目」卡改 navy featured 渐变；结尾 CTA 改 navy 渐变 + 亮青眉标
- about 页删除未使用的 `promo-public` body 类
- vision 愿景页内容扩充：新增「部署与落地成本」4 格说明（本地私有部署 / 数据可控 / 模型自选 / 低维护），回应部署与维护顾虑
- current 区显式标注「当前为单用户私有部署，成员账户与团队协作在路线图第二阶段引入」；roadmap 第二阶段补「从单用户走向实验室共用的关键一步」
- impact 副标题与卡 02/04 描述补入综述、开题、组会、实验设计参考等具体科研产出场景
- use-case 四个裸标签升级为带标题 + 一句实验室具体场景的卡片（方向动态跟踪 / 组会选题准备 / 新生阅读路径 / 方法与证据复用）

### 论文处理与定时日报管理
- 原“任务管理”页重命名为“论文处理”，只保留抓取、AI 分析、生成报告和添加指定论文；组合按钮改名为“抓取、分析并生成报告”
- 设置页新增顶级“定时任务”标签，集中管理唯一的内置 AI 论文日报、报告邮件与全部执行日志；WebDAV 配置仍保留在数据库标签
- 调度配置扩展为星期组合、时分、抓取回看天数和分析上限；旧配置自动补齐为全周、3 天、1000 篇
- 自动日报固定记录抓取、分析、推荐、报告、邮件、备份六步；日志新增 warning/skipped/interrupted 状态和可展开步骤耗时
- 应用启动时自动收口上次进程遗留的 running 日志；定时日报与手动组合任务增加互斥，手动冲突返回 409
- 邮件或备份失败不再伪装为成功：父任务标记“部分成功”，已生成报告保留，后续独立步骤继续执行

### 抓取与网络
- 定时 arXiv 抓取新增失败重试控制：`fetch_retry_interval_minutes` / `fetch_max_retries` 可配置（仅作用于定时日报）
- LLM API 调用统一走全局代理：LLM 客户端通过 `analyzer.get_openai_client()` 创建，复用全局代理配置并禁用环境变量代理

### 搜索与日报趋势
- 实现加权搜索与日报趋势：搜索按字段加权匹配与相关性排序；Web 日报新增截至报告日最近 7 个有论文日期的标签走势、新标签和推荐分分布

### 深度阅读与富文本
- 深度阅读记录模型 `finish_reason`，按当前 Prompt 中的 `### Qn:` 校验 Q&A 完整性；JSON 截断时从原始 assistant 输出末尾续写，缺题时只请求缺失问题，最多额外调用一次
- 自动补全后仍不完整时不覆盖已有 `qa_analysis`；重新生成接口返回 warning，添加指定论文则保留已完成的基础分析并报告缺失问题
- 保持深度阅读默认输出上限 6000 tokens 可配置，不改为固定 100k，也不强制关闭上限
- 新增共享 `static/rich_text.js`，论文详情页与学习页统一使用 marked + KaTeX，并通过标签/属性/URL 白名单清理模型输出；详情页继续保留分题卡片布局

### 文档与规划
- 新增 `docs/plan/` 项目评审与规划文档（项目审查、分阶段重构规划与优先级）；本次规划决定拆分超大文件并构建 `source/` 分层结构（目标 v0.6.0），拆分前打本版本标签作为回退点
- 新增 systemd 服务部署说明（`docs/systemd-service.md`）

---

## v0.4.1 (2026-06-17)

### 评级与 Prompt
- 恢复基础分析生成 0-5 星 AI 初评，用户仍可在论文详情页手动修正
- 新增 `rating_restored_from_legacy` 迁移标记，首次升级时从 `legacy_ai_rating` 一次性恢复历史 AI 评级
- 基础分析 Prompt 重新要求返回 `rating`，并通过 `{rating_criteria}` 使用校准后的评分标准
- 评分标准强调充分使用 0-5 星，降低普通增量工作默认集中到 3 星的问题

### 稳定性修复
- 改进 AI 返回 JSON 的反斜杠转义清理，修复 LaTeX/Markdown 内容导致 `Invalid \escape` 的解析失败
- 新增回归测试，覆盖合法 LaTeX 转义和无效 Markdown 转义混合出现的情况

### 展示与文档
- 列表、筛选项、HTML 报告和 Markdown 报告统一使用纯星级展示，不再追加数字星级后缀
- 优化设置页存储路径和 WebDAV 最近文件状态的长文本布局
- 同步更新 README、API、用户指南、开发者指南和 Agent 维护文档中的评级说明

---

## v0.4.0 (2026-06-15)

### LLM 成本控制
- 新增 AI 功能模型路由：基础分析、深度阅读、报告导读可分别选择供应商、模型、采样参数、输出上限和思考强度
- 基础分析改用短 Prompt，只生成标签、中文摘要和简评，不再生成 Q&A 后丢弃；评级改为用户手动维护
- 深度阅读使用独立任务配置，默认支持 high 思考强度，并按质量优先不截断 PDF 全文输入
- 深度阅读 Prompt 改为只生成 `qa_analysis`，不再重复生成或覆盖标签、手动评级、中文摘要和简评
- Prompt 改为 profile 结构，稳定任务说明与动态论文 JSON 分离，以提高兼容供应商的缓存命中率
- 报告生成默认不调用 LLM；任务页勾选“生成 AI 导读”时才使用报告导读模型生成摘要
- 新增 LLM token 用量账本，按任务、供应商和模型汇总 prompt/completion/total/cached tokens
- 设置页新增“账单”子项，LLM 用量账本移入账单页；功能模型路由和添加供应商区域默认折叠
- 账单页新增模型选择式 Tokens 堆叠柱状图，并用饼图展示不同模型的总 tokens 占比
- Tokens 柱状图支持悬停查看单日精确明细，区分缓存命中输入、未命中输入和输出 tokens
- 强化深度阅读 Q&A Prompt，要求按顺序完整输出所有问题，避免只回答最后一个总结问题

### 抓取与运行时设置
- 修复最近 N 天抓取误用数据库最早日期判断，导致明明缺少最近论文却提示“无需重复抓取”的问题
- 修复日期范围抓取没有使用设置页 `request_delay` 的问题，429 等 arXiv 限流错误会明确返回给前端
- 抓取日期窗口统一使用 UTC 边界，并在日志中显示具体 UTC 时间范围
- 修复 CLI 分析、批量分析默认并发、PDF 下载代理、论文列表每页数量等运行时设置没有传到实际执行函数的问题
- 放宽抓取配置上限并归一化保存，便于遇到 arXiv 429 时设置更长请求间隔和批次间隔

### 阅读清单
- 按本地个人使用场景，将阅读清单加入/移除接口设为公开操作；标记已读/未读仍需要登录
- 修复论文详情页内联 JSON 参数引号错误，导致“加入阅读清单”和标签删除按钮点击后不发请求的问题
- 阅读清单相关前端操作遇到鉴权错误时会提示登录，而不是静默失败

### 安全与鉴权
- 新增管理登录页和认证 API，设置管理密码后保护设置页、任务页、写接口和敏感设置读取接口
- 修复 `/api/providers` 返回完整 API Key 的问题，仅返回 `api_key_masked`
- 清除管理密码时必须验证当前密码
- 报告、任务日志、论文详情中的数据库/AI 内容进入 HTML 前统一转义，降低存储型 XSS 风险

### 任务与分析修复
- 定时任务配置迁入 `settings.json`，任务管理页可启用/停用并保存执行时间，保存后自动重建 APScheduler job
- 修复分类浏览「批量分析」可能分析全局未分析论文而不是选中论文的问题
- 批量分析新增单篇异常兜底，坏 Prompt 或单篇失败不会中断整批
- Prompt 保存时校验必需占位符和大括号格式

### 清理与文档
- 清理 `database.init_db()` 中重复的建表逻辑
- 删除废弃的 `fetch_papers_by_date()` 兼容函数
- 文档同步默认分类、报告体系、鉴权接口和定时任务配置方式

### AI 供应商配置
- 新增 `/api/providers/models`，可从 OpenAI 兼容供应商自动获取模型列表
- Max Tokens 改为默认不发送，只有启用「限制输出长度」后才传递
- 新增 Top P、Presence Penalty、Frequency Penalty 等采样参数开关
- 思考模式新增强度档位，并按 OpenAI、DeepSeek、Qwen/MiMo 等供应商协议映射参数
- 思考模式下自动省略 temperature/top_p/presence_penalty/frequency_penalty
- 重做思考模型检测，返回置信度和协议类型，并保存到当前激活供应商

### 个性化推荐
- 新增「个性化推荐」功能，根据用户研究兴趣为每篇论文计算推荐分（0-100）
- 推荐评分使用独立「个性化推荐」模型路由，只依赖论文摘要和已有基础分析字段
- analysis 表新增 `recommendation_score`、`recommendation_reason`、`recommendation_interest_hash`、`recommendation_analyzed_at` 字段
- 设置页新增研究兴趣输入和手动重算按钮；兴趣哈希用于自动判断推荐分是否过期
- 报告排序自动优先按推荐分排列，推荐分相同时再按评级排序
- 论文卡片和报告显示推荐分和推荐理由

### WebDAV 云备份
- 新增 `backup.py` 模块，使用 SQLite online backup API 生成一致性数据库快照
- 将数据库快照、`settings.json`、`output/` 报告目录和 manifest 打包为 zip 上传到 WebDAV
- 每次备份同时写入 `arxiv-backup-latest.zip` 和带时间戳的历史文件
- 根据 `history_days` 自动清理过期历史备份
- 设置页「数据库 → WebDAV 云同步备份」可启用/配置、手动立即备份
- 每日定时任务结束后自动执行云备份；备份失败单独记录，不中断日报流程

### 评级体系重构
- AI 基础分析不再生成评级，`rating` 字段改为用户手动维护
- 新增 `legacy_ai_rating` 字段迁移历史 AI 评级，迁移后 `rating` 清零
- 旧默认基础分析 Prompt 抵达用户后自动迁移，去除 AI 评级输出和 `{rating_criteria}` 占位符
- Prompt 设置不再包含 `rating_criteria`；基础分析只要求 `tags`、`summary_cn`、`value_comment`

### 认证增强
- Session secret 持久化到 `settings.json`，保证服务重启后登录状态仍有效
- Session 有效期设为 180 天，启用 HttpOnly + SameSite=Lax
- 管理密码版本 token（HMAC-SHA256）：修改管理密码后旧登录状态自动失效
- 退出登录正确清除持久 session

### 基础分析检测精度
- 新增 `_basic_analysis_missing_condition()` 统一判断 tags、summary_cn、value_comment 是否缺失
- `get_analyzed_count`、`get_unanalyzed_count`、`get_unanalyzed_papers` 改用字段级判断
- 分类浏览「是否已分析」筛选同样使用字段级判断，修复仅有空分析记录被误判为已分析的问题
- 批量分析时 `insert_analysis` 检测到已有完整基础分析则跳过，避免重复

### 报告增强
- 报告排序按推荐分优先，再按评级和日期排列
- 每日报告改为按推荐分分类列表，未设置研究兴趣时按评级排列
- 每日报告页新增「重新生成该日报告」按钮
- 生成报告 API 支持 `?date=` 参数指定日期
- 报告卡片新增推荐分显示（🎯 推荐 N/100）

## v0.3.0 (2026-06-01)

### 阅读清单
- 新增「📌 阅读清单」功能，可将感兴趣的论文加入待读清单
- 支持标记已读/未读，已读论文自动移到下方并加删除线
- 论文卡片和详情页新增「📌 加入清单」按钮
- 新增 `/reading-list` 页面和相关 API（添加/移除/已读/未读）

### 抓取系统重构
- 支持按天数抓取（自动分批，已抓取日期自动跳过，不会重复抓取）
- 支持精确抓取某一天的论文
- 可配置：请求间隔、每批天数、批次间隔（设置页 → 抓取配置）
- arXiv API 查询改为 `cat:cs.RO`（`submittedDate` 过滤器不工作）
- 修复时区比较错误（`datetime.now(timezone.utc)`）
- 每日任务改为抓取近 3 日全部 cs.RO 论文

### 代理支持
- 新增代理配置（HTTP/HTTPS）
- 设置页新增代理测试功能（测试 arXiv 连接）
- arXiv 抓取和 PDF 下载均支持代理

### 思考模型支持
- 供应商配置新增 `is_thinking` 字段
- API 调用时按供应商协议自动传递思考参数
- 设置页新增「检测是否为思考模型」功能

### AI 分析优化
- 新增 `_clean_json_content()` 清理 AI 返回的无效 JSON 转义
- 修复 `Invalid \escape` 导致的 JSON 解析失败

### 报告系统
- 生成报告改为支持指定日期
- 每日任务自动使用数据库最新日期生成报告

### UI 改进
- 首页默认显示最新一天论文（原「今日论文」改为「每日论文」）
- 日期选择器始终显示，可随时切换日期
- 分页栏新增页码跳转和每页数量选择
- 分类浏览新增「深度分析」筛选
- 分类浏览新增「可见性」筛选（已隐藏/未隐藏）
- 分类浏览新增批量隐藏和批量分析
- 任务管理页新增抓取天数、指定日期选项
- 任务管理页新增定时任务配置展示
- 论文详情页 arXiv/PDF 链接改为内联显示
- 搜索支持 arXiv ID 精确查找

### 文档
- 新增 `docs/` 目录，包含完整文档体系
  - `user-guide.md` — 用户使用手册
  - `developer-guide.md` — 开发者指南
  - `api-reference.md` — API 接口文档
  - `agent-guide.md` — AI Agent 开发指南
  - `architecture.md` — 项目架构说明
  - `changelog.md` — 更新日志
- 全项目添加中文注释（+1968 行，覆盖率 1.1% → 37%）

---

## v0.2.0 (2026-05-28)

### 论文浏览体验
- 首页论文卡片新增原始摘要显示，支持展开/收起
- 论文列表新增多分类标签展示
- 分类浏览页新增批量选择和批量删除功能
- 论文链接行新增「不感兴趣」按钮
- paper 详情页布局优化
- AI 深度阅读的 Markdown 渲染支持加粗、斜体、行内代码

### 分析流程重构
- 批量分析改为轻量模式（不下载 PDF）
- paper 详情页「生成报告」触发完整分析
- PDF 下载新增令牌桶限速
- 抓取改为仅拉取主分类论文

### Web 报告系统
- 新增 `/reports` 报告汇总页和 `/reports/<date>` 单日报告详情页
- 「生成报告」改为生成 Web 版结构化报告

### 任务管理
- 操作面板移至任务管理页
- 抓取支持选择主分类和最大数量
- 新增定时任务配置展示
- 首页新增「今日论文」快捷筛选

### 分页与设置
- 首页分页支持页码跳转
- 设置页新增「每页论文数」配置

### 其他
- 新增 `AGENTS.md` 维护文档
- arXiv API 查询改为按 arXiv 分类抓取，并在代码中保留主分类匹配的论文

---

## v0.1.0 (2026-05-27)

### 初始版本
- arXiv 论文自动抓取（cs.RO 分类）
- AI 分析（标签、评级、中文摘要、Q&A 深度阅读）
- SQLite 数据库存储
- Flask Web 界面浏览
- APScheduler 定时任务
- Markdown 报告生成
- CLI 入口（fetch/analyze/generate/run）
- 支持多种 AI 供应商预设
