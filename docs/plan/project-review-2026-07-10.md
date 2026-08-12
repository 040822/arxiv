# 项目全面检查报告

> 检查日期：2026-07-10
> 检查范围：安全性、前端美观性、用户友好性、核心功能（AI 辅助论文阅读）改进点、代码质量与架构
> 检查方式：全代码审阅（app.py / database.py / settings.py / analyzer.py / email_report.py / fetcher.py / backup.py / pdf_reader.py / markdown_gen.py / config.py / 11 个模板 / 2 个 CSS 文件 / 测试文件 / 文档）
> **复核（2026-08-12）：** 逐条对照当前代码核对完成状态；已完成项以 `✅ 已完成` 标注，部分完成项以 `✅ 部分完成` 标注，未标注项仍未完成。各条目位置已按重构后的 `source/` 目录更新。

---

## 目录

- [一、安全性问题](#一安全性问题)
- [二、前端美观性与用户友好性](#二前端美观性与用户友好性)
- [三、核心功能（AI 辅助论文阅读）改进点](#三核心功能ai-辅助论文阅读改进点)
- [四、代码质量与架构](#四代码质量与架构)
- [五、优先级总览与实施建议](#五优先级总览与实施建议)

---

## 一、安全性问题

### 1.1 认证与授权

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| S1 ✅ | ~~中~~ **已完成** | 启动时若无凭据会生成高强度随机管理密码并仅在首次日志输出；缺少凭据时鉴权失败关闭 | `source/settings/admin.py`, `source/web/application.py` | 损坏配置会中止启动，禁止默认模板覆盖 |
| S2 ✅ | ~~低~~ **已完成** | Web 端不再支持无认证首次设密或清除密码；改密始终验证当前密码 | `source/web/auth.py` | 忘记密码使用服务器本机重置脚本 |
| S3 ✅ | ~~低~~ **已完成** | 阅读清单页面、读取与全部写接口均要求登录，不再存在公开写例外 | `source/web/auth.py` | 全站 CSRF 仍由 S14 单独跟踪 |
| S4 ✅ | ~~低~~ **已完成** | `/api/progress/<task_id>` 已移出公开 GET 集合，未登录返回 401 | `source/web/auth.py` | 当前单管理员模型下所有登录会话均为可信管理员 |
| S5 ✅ | ~~低~~ **已完成** | 登录按来源 IP 限制 15 分钟内 5 次失败，超限返回 429/`Retry-After` | `source/web/auth.py` | 不默认信任可伪造的 `X-Forwarded-For` |
| S6 ✅ | ~~低~~ **已完成** | 新密码使用 scrypt 慢哈希；旧 SHA-256 使用安全比较并在成功登录后原位升级 | `source/settings/admin.py` | 旧摘要升级会使旧 session 自动失效 |
| S6A ✅ | ~~低~~ **已完成** | 未登录访客仍能看到 paper 页危险操作和其他管理控件 | `templates/paper.html` | 匿名页保留内容展示，隐藏全部管理入口；列表与报告页同步收口 |

### 1.2 SQL 注入

总体评估：**database.py 绝大多数 SQL 使用参数化查询**，做得相当规范。

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| S7 ✅ | ~~低~~ **已完成** | `update_analysis` 用 f-string 拼 SET 子句（字段为硬编码常量，当前安全，但模式易被后续开发者误用） | `source/storage/analysis.py:135-158` | 已改为从硬编码 `field_values` 白名单 dict 取键拼 SET，字段名不可能来自外部输入 |
| S8 | 低 | `get_task_logs` 用 `query.replace("SELECT *", "SELECT COUNT(*) ...")` 构造 count 查询，方式脆弱 | `source/storage/operations.py:162` | 显式构造 count SQL |
| S9 ✅ | ~~低~~ **部分完成** | LIKE 模糊匹配未转义用户输入中的 `%`/`_` 通配符，可能导致意外宽匹配 | `source/storage/papers.py:230,334,616` | 搜索路径已转义（`replace("%","\\%")` + `ESCAPE '\'`，见 :616-620）；`get_papers_with_analysis`/`browse_papers` 的标签 LIKE（:230/:334）仍未转义 |

### 1.3 XSS 风险

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| S10 | **中** | `report_detail.html` 用 `{{ report.content \| safe }}` 直接输出拼接 HTML。当前 Python 端已 `html.escape`，但一旦新增字段忘记转义即形成存储型 XSS；该页在 PUBLIC_GET 无需登录 | `templates/report_detail.html:47`, `source/reports/renderer.py:63` | 改用 Jinja 模板渲染报告，或加 DOMPurify / bleach 白名单清洗（仍为 Python 端统一 escape + `\| safe` 旧模式，未改） |
| S11 ✅ | ~~中~~ **已完成** | `paper_chat.html` 的 `sanitizeDom` 黑名单不充分：未处理 `<svg>`/`<math>`/`<form>`/`data:` URI，可被模型输出间接诱导 | `static/rich_text.js:29-61` | 自制 sanitizer 已改为白名单（ALLOWED_TAGS/ALLOWED_ATTRIBUTES + `on*` 移除 + scheme 校验拦截 `javascript:`/`data:`），随 `RichText.render()` 对详情页与学习页统一生效 |
| S12 ✅ | ~~低~~ **已完成** | `paper.html` 的 `renderMd` 手写 Markdown 转换器，先 escape 再正则替换，逻辑安全但易被改动引入 XSS | `templates/paper.html:238-262` | 已删除 renderMd，改用 `/static/rich_text.js` 的 `RichText.render`/`RichText.renderMath`（同 C12，2026-07-12） |
| S13 ✅ | ~~低~~ **部分完成** | 第三方前端库 `marked.min.js`/`katex.min.js` vendored 未声明版本号，无法跟踪 CVE | `static/vendor/` | `marked.min.js` 已带版本头（v4.3.0）；`katex.min.js`/`auto-render.min.js` 仍无版本声明 |

### 1.4 CSRF 防护

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| S14 ✅ | ~~中~~ **已完成** | 全项目无 CSRF token 机制，仅依赖 `SameSite=Lax` cookie。对公开写接口（todo）攻击者可不用 cookie 直接打；子域 XSS 可绕过 | `source/web/auth.py:94-118,230`, `static/auth.js` | 已实现 session CSRF token + `X-CSRF-Token` 校验：非 SAFE 方法全量校验（失效返回 403），`static/auth.js` 自动为同源非安全 fetch 注入 token，各模板输出 `csrf_token` meta |

### 1.5 密钥 / 敏感信息

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| S15 ✅ | ~~中~~ **已完成** | 管理密码已改用 scrypt 慢哈希；旧 SHA-256 仅用于兼容验证并在成功登录后升级 | `source/settings/admin.py` | 随机初始密码仍只在首次启动日志中短暂有效，建议登录后修改 |
| S16 ✅ | ~~中~~ **部分完成** | `settings.json` 同时明文存放 session_secret、API key、WebDAV/SMTP 密码、密码哈希 | `source/settings/strict_io.py:33-57`, `source/settings/store.py:319` | `strict_io`（用户迁移路径）对 settings 备份/临时文件 chmod 0o600；常规 `save_settings` 仍普通写入，未做 OS keyring、未对 `data/` 目录设 0700 |
| S17 | 低 | `/api/db/info` 返回服务器文件系统绝对路径 | `source/web/settings_api.py:426` | 仅返回相对路径（仍返回绝对 `DB_PATH`，未改） |
| S18 ✅ | ~~低~~ **已完成** | 备份任务日志 `detail` 记录上传文件名、DB 大小等 | `source/backups/__init__.py:231` | 任务日志接口已收归 admin（`api_task_logs` 在 `ADMIN_ENDPOINTS`），不存在公开查看路径，脱敏需求自然消失 |
| S19 ✅ | ~~低~~ **已完成** | 部分 GET 设置接口在默认无密码场景下全公开（proxy/prompts/ai-tasks/personalization 等） | `source/web/auth.py:45-61` | `api_get_proxy`/`api_get_prompts`/`api_get_ai_tasks`/`api_get_personalization` 等全部移入 `ADMIN_ENDPOINTS`，未登录返回 401 |

### 1.6 网络请求安全

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| S20 | **中** | `api_test_connection`/`api_network_test_llm`/`api_detect_thinking` 可被用来 SSRF：管理员可设任意 base_url 后触发请求内网 | `source/web/providers_api.py:183-206,105-174` | 对 base_url 做 scheme 白名单 + 私网地址拦截（未实现） |
| S21 ✅ | ~~低~~ **已完成** | PDF 下载未校验 Content-Type 和大小上限，恶意链接可塞入超大文件耗尽磁盘 | `source/documents/__init__.py:36,332-345` | 已设 `MAX_PDF_BYTES = 100 MB` 流式上限 + `%PDF-` 魔数校验（替代 Content-Type 校验），上传与远程下载双路径覆盖 |
| S22 | 低 | WebDAV `remote_dir` 含 `..` segment 时可在远端目录上跳一级 | `source/backups/__init__.py:99,110-119` | 过滤 `..` segment（仅过滤空 segment，`..` 仍可透传，未改） |
| S23 | 低 | `security == "none"` 时 SMTP 明文发送认证密码 | `source/reports/email/transport.py:109`, `templates/settings.html:336` | UI 强制警告或禁止 none（未实现） |

### 1.7 输入验证

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| S24 | 低 | `api_batch_delete_papers` 未校验 arxiv_ids 是 list、元素格式、长度上限 | `source/web/papers_api.py:195-217` | isinstance + 正则 + 长度上限（仍仅判空，未实现） |
| S25 | 低 | 邮件 `recipients` 未校验邮箱格式 | `source/settings/normalize.py:123-139` | 用 email.utils.parseaddr 校验（现仅去重，未实现） |
| S26 ✅ | ~~低~~ **部分完成** | 分页参数无上界，page 可传 10^9 | `source/web/papers_api.py:70-81` | HTML 页已 clamp（`auth.py:389-390`）；`/api/papers` JSON 接口 page 仍无上界 |

### 1.8 错误处理与信息泄露

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| S27 | **中** | 几乎所有异常 `str(e)` 直接回给客户端，可能泄露内部路径、SQL 错误、base_url 等 | `source/web/` 40+ 处 | 生产模式返回通用错误，详细记入服务端日志（未实现，`str(e)` 仍大量回传） |
| S28 ✅ | ~~低~~ **已完成** | `api_db_info` 用裸 `except:` 捕获并存默吞异常 | `source/web/settings_api.py:408-413` | 已改为 `except Exception as exc` + `logger.warning`；`source/` 下已无裸 `except:` |

### 1.9 其他

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| S29 | **中** | 深度阅读 `get_paper_full_text(max_chars=None)` 不截断，超大 PDF 可撑爆 LLM 请求与账单（成本 DoS） | `source/analysis/papers.py:109-113` | 设合理上限（如 200 万字符）作为安全护栏（仍显式传 `max_chars=None`，未改；仅下载阶段有 100 MB 文件上限） |
| S30 | 低 | `progress_store` 全局 dict 无 TTL 清理，用户可控 task_id 可撑大内存（轻微 DoS） | `source/web/progress.py:8-29` | 限制大小或设 TTL（仅记录 timestamp 与 user 归属，无清理，未改） |
| S31 | 低 | 依赖用 `>=` 范围未固定版本 | `requirements.txt` | 固定版本 + pip-audit 扫描（仍全部 `>=`，未改） |

---

## 二、前端美观性与用户友好性

### 2.1 整体视觉设计

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| F1 ✅ | ~~高~~ **已完成** | 多个 CSS class 已在模板中使用但 `style.css` 中**无对应定义**：`.batch-actions`、`.select-all-label`、`.learning-quiz`、`.field-label`、`.q-pending`、`.schedule-runtime-grid` | `static/css/pages/library.css:305-318, learning.css:181,339-340, settings.css:857` | 已按页补齐全部缺失样式，并建立"每页 CSS 契约"测试 `tests/test_static_css_contract.py` 防止回退 |
| F2 | **中** | 无 CSS 变量 / 设计令牌，同一颜色硬编码数十次；`.btn-danger` 被定义两次且配色冲突 | `static/css/*.css` 全文件 | 引入 `:root` 变量系统（未实现） |
| F3 | **中** | 配色对比度偏低，多处 `#888`/`#aaa`/`#bbb` 在白色背景上低于 WCAG AA 4.5:1 | `static/css/core.css:97,109, components.css:224-262` | 次要文字至少 `#64748b`（未改） |
| F4 | 低 | `font-family` 无中文字体回退，Windows/Linux 上中文渲染不一致 | `static/css/core.css:8` | 加 `"PingFang SC","Microsoft YaHei"`（未改） |
| F5 ✅ | ~~低~~ **已完成** | 大量内联 `style=""`，settings.html 有数十处 | `templates/settings.html` | 内联样式已收口：settings.html 仅剩 5 处、其余业务页合计约 4 处 |
| F6 | 低 | 宣传页(`about.html`/`vision.html`)与核心 App 页设计语言完全割裂，"门面精致、内部朴素" | `static/promo.css` vs `static/css/` | 统一设计系统（未改；`templates/README.md` 已将 promo.css 定为独立命名空间作为已接受取舍） |

### 2.2 交互体验

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| F7 ✅ | ~~高~~ **已完成** | 全站无 spinner / 骨架屏 / "正在输入"气泡。聊天发送后数秒无反馈，生成题目/苏格拉底追问期间空白 | `templates/paper_chat.html:167-213` | 已实现 AI 打字气泡（`.typing-dots`）+ `inline-spinner` 状态条，聊天/生成题目/追问全路径覆盖 |
| F8 ✅ | ~~高~~ **已完成** | `paper_chat` 生成/提交按钮在异步期间未禁用，用户可连点重复创建 session 重复消耗 LLM token | `templates/paper_chat.html:242-272,332-355` | 已实现 `setBusy()`：发送/生成/提交期间禁用按钮与 textarea，finally 恢复 |
| F9 | **中** | 全站用原生 `alert()` 做错误反馈，`confirm()` 做确认弹窗（批量删除双重 confirm） | `templates/index.html:273-275`, `paper.html:490-493` 等 | 统一 toast + 确认弹窗组件（未实现） |
| F10 ✅ | ~~中~~ **已完成** | 隐藏论文（index/browse）**无任何确认**，误点即丢失卡片 | `templates/index.html:278-279` | 已加 confirm（"确定隐藏这篇论文？隐藏后可在「分类浏览」筛选已隐藏项恢复"），browse 同步 |
| F11 | **中** | `browse.html` 筛选 select `onchange="this.form.submit()"` 全页刷新，丢失滚动位置 | `templates/browse.html:85` 等 | 改 AJAX 或至少重置 page=1（未改） |
| F12 ✅ | ~~中~~ **已完成** | `paper_chat` 无 Ctrl/Cmd+Enter 发送，textarea 发送期间未禁用 | `templates/paper_chat.html:446-451` | 已实现 Ctrl/Cmd+Enter 发送，textarea 发送期间禁用 |
| F13 ✅ | ~~低~~ **部分完成** | 表单验证几乎全缺，重复标签静默忽略、数字输入无即时校验 | `templates/tasks.html:57,81`, `templates/paper.html:491` | tasks 数字输入已加 min/max，删除保护 409 分支已处理；重复标签仍静默忽略 |

### 2.3 页面一致性

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| F14 ✅ | ~~高~~ **部分完成** | 导航条各页条目、文案、数量全不一致：paper_chat 仅 3 项，paper/search 缺"每日报告"，reports 缺"阅读清单"。导航复制粘贴而非 `{% include %}` | 各模板 nav-bar | 各页导航条文案/数量已对齐（c6b25ab）；但仍为逐页复制，未抽 `partials/nav.html` |
| F15 ✅ | ~~中~~ **已完成** | header 结构不一致：仅首页有完整 header;其余页用 `<div></div>` 空占位，subtitle/stats 消失 | `templates/browse.html:18-25` 等 | 各页已统一 `header > h1` 结构（"🤖 AI 论文数据库"），c6b25ab 对齐 |
| F16 ✅ | ~~低~~ **部分完成** | 按钮样式三套并存（`.btn`/`.action-btn`/`.btn-sm`），视觉近似但细节各异 | `static/css/components.css:678` | `.action-btn` 已清除，`.btn-sm` 收口至 components.css；settings 页仍有独立 `.btn-small` |
| F17 ✅ | ~~低~~ **已完成** | 空态两套 class（`.empty-state` / `.no-data`）风格不统一 | `static/css/components.css:221` | `.no-data` 已全部清除，统一使用 `.empty-state` |

### 2.4 可访问性（核心 App 页几乎为零）

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| F18 | **高** | 核心 App 页零 aria / role 标签（仅 about/vision 有） | 全部核心模板 | 加 `<nav aria-label>` / `role="main"` / skip-link |
| F19 | **高** | 大量 `<a href="javascript:void(0)" onclick>` 充当按钮，键盘可达但语义错误，屏幕阅读器读作"链接" | `index.html:120,125`, `browse.html:142-146` 等 | 改 `<button type="button">` |
| F20 | 中 | 星级评分为 `<span onclick>`，无 tabindex、无键盘操作支持 | `paper.html:73-79` | 改 `role="slider"` + 方向键 |
| F21 | 中 | Tab 组件（settings/paper_chat）无 `role="tablist"/"tab"/"tabpanel"`/`aria-selected` | `settings.html:19-26`, `paper_chat.html:41-45` | 加 ARIA tab 语义 |
| F22 | 低 | 密码显隐按钮内容仅 emoji `👁`，无 aria-label | `settings.html:75,500` 等 | 加 aria-label |

### 2.5 核心功能页面可用性

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| F23 | **高** | `index.html` 将完整摘要注入 `onclick` 属性传给 JS：`\| e` 不转义反引号/`${}`，摘要含反引号即破坏 JS；DOM 翻倍膨胀 | `templates/index.html:130,135` | 改 `data-abstract` 或 JSON island（未改，仍为模板字面量注入） |
| F24 ✅ | ~~高~~ **已完成** | `paper.html` 深度阅读 Q&A 用手写残缺 `renderMd`（仅粗体/斜体/代码），而学习页用 marked+KaTeX 完整渲染。**核心 AI 输出被降级** | `templates/paper.html:238-262` | 已统一复用 `RichText.render`/`RichText.renderMath`（同 C12，2026-07-12） |
| F25 | **高** | `paper.html` `saveField` 系列不检查 `resp.ok` / 401 / 500，失败时静默假装成功 | `templates/paper.html:369-376` | 加完整 HTTP 校验（对齐 paper_chat fetchJson）（未改，仍仅判 `result.status`） |
| F26 | **中** | `search.html` 无分页，大量结果一次性渲染 | `templates/search.html` | 加分页控件（未实现） |
| F27 | 中 | `index.html` "加入清单"永远渲染"加入清单"，不查询是否已加入（contrast paper.html 有 checkTodoStatus） | `templates/index.html:160` | 列表页同步状态（未实现） |
| F28 | 中 | `index.html` 有 `min_rating` 查询参数拼进分页但页面无评级筛选 UI（半成品） | `templates/index.html:180` | 补 UI 或移除（未处理） |
| F29 ✅ | ~~中~~ **已完成** | `browse.html` 批量操作栏无样式，5 个按钮挤在一起换行 | `templates/browse.html:161-171`, `static/css/pages/library.css:305-318` | 已补齐 `.batch-actions` 样式（随 F1） |
| F30 | 中 | `paper_chat.html` 每发一条消息整体 `innerHTML` 重建 + 重渲染 KaTeX，长会话渐慢 | `templates/paper_chat.html:217-231` | 增量追加 DOM（未改，仍整体重建） |
| F31 | 中 | `paper_chat.html` 生成入口永远可见，无"当前会话进行中"提示，无删除/重置入口 | `templates/paper_chat.html:75-91` | 有 session 后转为"继续/重开"（未改；仅展示历史会话 chips） |
| F32 | 低 | `settings-sidebar` 移动端 `display:none` 且无替代跳转 | `static/css/pages/settings.css:852-856` | 改为可折叠或水平 chip（未改） |

### 2.6 跨页维护性

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| F33 ✅ | ~~高~~ **部分完成** | `escapeHtml`/`handleAuthRequired`/`goPage` 等大量 JS 跨页重复复制（6+ 份） | `static/auth.js`, 各模板 `<script>` | CSRF 注入已抽到共享 `static/auth.js`（全站加载）；`escapeHtml`/`handleAuthRequired` 等仍各页重复 |
| F34 | 中 | `handleAuthRequired` 在操作中用 `confirm()` 问"去登录？"，打断用户 | `templates/index.html:246-254` 等 5 处 | 改 inline 提示 + 跳转链接（未改） |

---

## 三、核心功能（AI 辅助论文阅读）改进点

> 本节聚焦项目核心目标：**如何让 AI 更好地辅助用户阅读论文，提升阅读效率和质量**。

### 3.1 阅读效率

| # | 优先级 | 改进方向 | 现状 | 建议 | 位置 |
|---|--------|----------|------|------|------|
| C1 | **高** | **列表接入推荐分排序与展示** | 首页/浏览页排序固定为 `published_date DESC, rating DESC`，不显示推荐分 | 增加"按推荐分排序"选项，卡片展示 `🎯 推荐 X/100` 徽章 | `database.py:527,656-657`, `index.html:60-78` |
| C3 | **高** | **阅读笔记 / 进度 / 学习状态展示** | `reading_list` 仅 `status(unread/read)`，无笔记表；详情/学习页不显示"已讨论 N 条 / 已做 M 轮"（数据已存在但未展示） | 新增 notes 表；详情/学习页头部加"学习进度卡" | `database.py:224-232`, `paper.html`, `paper_chat.html` |
| C5 | 低 | 邮件阈值固定 | 推荐>80 重点，最多 20 篇速览 | 阈值放入 email_report 配置 | `analyzer.py:795` | ✅ 已完成 2026-07-12：`important_score_threshold`/`overview_limit` 移入 `settings.email_report`，UI 可调，默认 80/20 |

### 3.2 阅读质量

| # | 优先级 | 改进方向 | 现状 | 建议 | 位置 |
|---|--------|----------|------|------|------|
| C6 | **高** | **基础分析结构化** | `value_comment` 仅"2-3 句话"，`rating` 仅基于摘要 | 增加字段：`strengths`/`limitations`/`method_type`/`novelty`，列表卡片展示方法类型图标 + 一句亮点 | `settings.py:238-239`, `analyzer.py:441-466` |
| C7 ✅ | ~~高~~ **部分完成** | **对话历史窗口 + PDF 文本缓存 + RAG** | `chat_about_paper` 取最近 12 条且每次重新 `extract_text_from_pdf` 整篇 PDF 全文重发 LLM | 缓存提取后纯文本写 `.txt` 旁缓存；长对话滚动摘要；超长 PDF 引入段落检索 | `source/analysis/core.py:48-80`, `source/analysis/learning.py` | PDF 全文缓存与复用已完成（`get_learning_paper_text` 优先复用 `data/pdf_cache/`，下载/提取失败回退摘要）；长对话滚动摘要与段落检索仍未做 |
| C8 | 中 | **Q&A 按论文类型适配** | 6 个通用问题对所有论文一致 | 按标签/方法类型路由不同问题模板；支持用户 per-paper 追问 | `settings.py:258-265` |
| C9 | 中 | **深度阅读截断检测** | `max_tokens: 6000` 可能截断 6 个详尽回答，截断后不校验完整性 | 检测 Q1..Q6 缺失时自动重试或分段续写；截断时前端提示 | `settings.py:154-155`, `analyzer.py:350-357` | ✅ 已修复 2026-07-12：`analyzer.analyze_paper_full` 检 `finish_reason in {length,max_tokens}` + 缺 Q 自动续写/补题一次，返回 `complete/continuation_used/missing_questions/finish_reason`；前端 `deep_reading_incomplete` 提示；测试 `tests/test_deep_reading_completion.py` |
| C10 | 中 | **主动回忆练习难度自适应 + 薄弱点闭环** | quiz 固定 3/6 题，不参考历史答题表现，评分后无"针对薄弱点再练" | 生成题目时传入历史错题；score<3 自动建议再练；会话级给薄弱点概览 | `analyzer.py:558-559,685-723` |
| C11 | 中 | **苏格拉底追问终止条件 + 会话评价** | 每轮生 next_question 无明确终止判定，无整体掌握度输出 | 模型返回 `is_complete` + `session_summary`；前端给结业卡 | `analyzer.py:726-769` |
| C12 | 中 | **paper.html Q&A 渲染统一** | 详情页用残缺 renderMd，学习页用 marked+KaTeX | 统一复用 renderRich | `paper.html:150-157` | ✅ 已修复 2026-07-12：`paper.html` 改用 `/static/rich_text.js` 的 `RichText.render`/`RichText.renderMath`，与 `paper_chat.html` 同源 |

### 3.3 AI 辅助个性化

| # | 优先级 | 改进方向 | 现状 | 建议 | 位置 |
|---|--------|----------|------|------|------|
| C13 | **高** | **推荐评分引入用户反馈闭环** | `analyze_paper_recommendation` 仅用 research_interests 文本，手动评级、隐藏、阅读清单、quiz 分数——这些最强信号完全未回流 | 显式反馈（收藏/隐藏/手动评级）作为正负样本；隐式反馈（quiz 分/聊天轮数）聚合为"已掌握"信号；兴趣变化只补算未评分论文 | `analyzer.py:511-542` |
| C14 | 中 | 推荐分维度 + 解释 + "找更多类似" | 推荐理由仅"1-2 句"，分数单一整数 | 返回分维度评分（相关性/前沿性/补充度）+ 匹配点列表 | `settings.py:314-333` |
| C15 | 中 | **跨论文记忆 / 用户画像** | 对话按 paper_id 隔离，用户在 A 论文的认知不带入 B 论文 | 维护用户研究背景 profile（用户填 + 自动归纳），作为跨论文对话 system 上下文 | `database.py:236-248`, `analyzer.py:621` |

### 3.4 缺失的辅助功能

| # | 优先级 | 改进方向 | 建议 | 位置 |
|---|--------|----------|------|------|
| C16 | **高** | **论文关联 / 对比 / 主题追踪 / 知识图谱** | 相关论文侧栏（共享标签 + embedding）；2-3 篇并排对比；标签/主题聚合时间线；从 qa_analysis 抽概念建图 | 全局新增 |
| C17 | 中 | **笔记与学习成果导出** | 一键导出单篇"学习档案" Markdown/PDF（基础分析 + Q&A + 笔记 + 对话摘要 + quiz 成绩）；导出全部清单为 Obsidian 兼容 md | 全局新增 |
| C18 | 中 | **术语解释 / 背景知识补充** | Q&A 与摘要中的术语提供悬浮解释（轻量 LLM 任务或预生成 glossary）；"我不熟悉这个子领域"入口 | `settings.py:340-347` |
| C19 | 中 | **主题趋势追踪页** | 标签 × 时间的热度/平均评级折线，点击进入主题论文列表 | 全局新增 |

### 3.5 交互流程

| # | 优先级 | 改进方向 | 现状 | 建议 | 位置 |
|---|--------|----------|------|------|------|
| C20 | 中 | **详情/学习页学习状态卡** | 无"已讨论 N 条 / 已做 M 轮 / 平均分"提示，续学无标识 | 数据现成（get_latest_paper_quiz_sessions、get_paper_chat_messages），加头部卡片 | `paper.html`, `paper_chat.html` |
| C21 | 中 | **学习成果量化仪表盘** | quiz chips 仅 mode/题数，无分数/薄弱点；无跨论文学习总览页 | 会话 chips 展示分数；新增学习仪表盘页 | `paper_chat.html:68-78` |
| C22 | 中 | **深度阅读转后台 + 进度** | `/api/paper/<id>/reanalyze` 同步阻塞极易超时，前端仅"正在载入…" | 复用 progress_store 按 pos 报进度 | `app.py:1299-1323`, `paper.html:300-304` |
| C23 ✅ | ~~中~~ **部分完成** | **PDF 文本 / Q&A 复用联动** | 深度阅读与学习页独立下载/提取 PDF；Q&A 不被对话复用 | 缓存纯文本；允许对话在 qa_analysis 基础上"追问 Q3 细节" | `source/analysis/core.py:48-80`, `source/analysis/papers.py:96-133` | 深度阅读与学习页已共用 `download_pdf` → `data/pdf_cache/` 同一缓存链路（命中即复用）；对话仍不复用 `qa_analysis` |
| C24 | 低 | 学习页登录引导前置 | 未登录时只在发送后才弹登录 | 进入页面即检查认证状态，显示占位 | `paper_chat.html:175-183` |

---

## 四、代码质量与架构
> **修复状态更新（2026-07-24）：** Q1–Q9 已完成并通过
> 173 项测试；对应实施记录见
> [`refactor-4.1-splitplan.md`](refactor-4.1-splitplan.md) 与
> [`refactor-4.2-plan.md`](refactor-4.2-plan.md)。

### 4.1 超大文件

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| Q1 ✅ | ~~高~~ **已完成** | `app.py` 已收缩为 14 行兼容入口，路由、认证、流水线和调度均已拆分 | `source/web/`, `source/pipeline/` | 7 个 Blueprint；保留 86 条非静态路由契约 |
| Q2 ✅ | ~~中~~ **已完成** | `database.py` 已收缩为 8 行兼容入口，SQLite 操作和报告渲染已分离 | `source/storage/`, `source/reports/` | 按表族拆分 storage；renderer 独立 |
| Q3 ✅ | ~~中~~ **已完成** | `settings.py` 已收缩为 9 行兼容入口，配置职责已拆分 | `source/settings/` | 拆分 defaults/normalize/thinking/providers/prompts/store/runtime 等模块 |

### 4.2 数据库层

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| Q4 ✅ | ~~高~~ **已完成** | 所有生产调用点均使用兼容式托管连接；退出上下文时统一提交/回滚并关闭，旧的直接调用接口仍可用 | `source/storage/connection.py`, `source/storage/*.py` | 已移除业务层手动 `commit/close` |
| Q5 ✅ | ~~中~~ **已完成** | v2 迁移合并历史重复分析并建立 `uq_analysis_paper_id`；插入改为原子 `INSERT OR IGNORE` | `source/storage/analysis_migrations.py`, `source/storage/analysis.py` | 保留最早记录，空字段从后续记录补齐，冲突保留主记录并告警 |
| Q6 ✅ | ~~中~~ **已完成** | 每条托管连接显式设置 `busy_timeout=5000`，短时写锁竞争会等待 | `source/storage/connection.py` | 已覆盖锁竞争成功测试 |
| Q7 ✅ | ~~中~~ **已完成** | `init_db()` 改为调用顺序迁移器；每个版本在独立事务中原子提交或回滚 | `source/storage/schema.py`, `source/storage/migrations.py` | 迁移或快照失败会中止启动 |
| Q8 ✅ | ~~中~~ **已完成** | 新增 `schema_migrations` 版本表与 v1/v2 有序迁移；变更前创建 SQLite 一致性快照并保留最近 3 份 | `source/storage/migrations.py`, `source/storage/snapshot.py` | 拒绝未知未来版本与版本断层 |

### 4.3 配置管理

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| Q9 ✅ | ~~高~~ **已完成** | `load_settings` 已使用递归 deep merge，未知顶层字段不再因双重白名单遗漏而丢失 | `source/settings/store.py` | 已增加新旧格式与未知顶层字段回归测试 |
| Q10 | **中** | `load_settings` 整体 `except Exception` 仍直接回退到 DEFAULT_SETTINGS；后续写入可能覆盖用户 API key/密码/研究兴趣 | `source/settings/store.py:288-290` | 区分读取、解析和归一化错误，保留可恢复数据且禁止隐式覆盖原文件（未改；已另加 guardrail 只读告警 `source/settings/guardrail.py` + `scripts/check_settings_guardrail.py`，不阻断回退行为） |
| Q11 ✅ | ~~中~~ **部分完成** | `get_session_secret()` 仍会在读路径触发 `save_settings` 写盘 | `source/settings/store.py:327-362` | 延迟到显式初始化或写入阶段（未完全实现；读路径已 fail-closed：损坏文件直接抛错中止启动，缺失密钥时用临时文件原子写回，不会用默认模板覆盖原文件） |
| Q12 | 低 | 供应商思考模型探测逻辑仍硬编码多个供应商分支 | `source/settings/thinking.py:18-47` | 改用供应商 metadata 自描述（仍硬编码 deepseek/qwen/openai 分支，未改） |

### 4.4 重复代码

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| Q13 ✅ | ~~中~~ **已完成** | JSON 字段解析样板（authors/categories/tags）在 7+ 处逐字重复，已抽出 `_parse_paper_analysis_row` 但未被复用 | `source/storage/row_mapping.py` | 已收口为 `parse_paper_row()` 并全量复用（pages/papers_api/import_api/storage，d3c50d6、d045d0c） |
| Q14 ✅ | ~~中~~ **部分完成** | 40+ 写 API handler 重复 `try/finish_task_log/jsonify(error)` 模板 | `source/web/task_endpoint.py:67` | `@task_endpoint` 装饰器已实现并有测试（`tests/test_task_endpoint.py`），但业务路由尚未接入使用 |
| Q15 ✅ | ~~低~~ **已完成** | 安全取数/归一化函数散落 5 个文件重复（`_safe_int`/`_int_value`/`_request_int`） | `source/value_coercion.py` | 已集中为 `as_int()` 等并全量复用（8dd1ed4、37d4fea、a53338b） |
| Q16 ✅ | ~~低~~ **已完成** | `import json as _json` 在同一函数体内重复两次（顶层已有 `import json`） | — | 冗余重复导入已随 app.py 拆分清除 |

### 4.5 错误处理

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| Q17 ✅ | ~~中~~ **已完成** | 裸 `except:` 静默吞掉所有异常（含 KeyboardInterrupt） | `source/` 全树 | 已全部改为 `except Exception` 或具体异常 + 日志（`source/` 下已无裸 `except:`） |
| Q18 ✅ | ~~中~~ **部分完成** | `except Exception` 严重倾向于无条件 500 兜底，未区分 4xx/5xx | `source/web/papers_api.py` 等 | 部分接口已细分 400/404/409（参数校验、删除保护 409 等）；大量 `str(e)` 500 兜底仍在 |
| Q19 | 低 | `_clean_json_content` 用贪婪正则 `\{[\s\S]*\}` 匹配 JSON，可能误吞 JSON 后的 `}` | `source/analysis/json_support.py:60` | 改 brace-counting 解析（未改） |

### 4.6 测试

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| Q20 ✅ | ~~中~~ **已完成** | 唯一测试文件 3582 行 130+ 方法全在一个 TestCase 中，文件名 `test_ai_provider_config.py` 严重名不副实 | `tests/ai_test/` | 2026-08-07 按功能拆为 17 个文件 + 共享基座 `tests/ai_test/common.py`（143 个方法逐字节平移）；验收：AST 逐方法对比 + 收集数 + 逐测试 outcome 三方一致，覆盖率 66% 前后不变；原巨石文件已删除 |
| Q21 | 中 | 无 pytest / `conftest.py` / fixture 基础设施，每个用例内 `importlib` 重载模块 + monkeypatch 全局 | `tests/test_ai_provider_config.py:1-18` | 引入 pytest + conftest fixture | ✅ 部分完成 2026-08-07：pytest 9.1 + pytest-randomly 已就位，双运行器与统一入口 `scripts/run_all_tests.py`（含乱序验证）；2026-08-07 巨石已拆至 `tests/ai_test/`，conftest/fixture 迁移待后续计划实施 |
| Q22 ✅ | ~~中~~ **部分完成** | 核心渲染逻辑 `generate_report_content`（190行）无针对性单测；模板几乎无渲染断言 | `source/storage/reports.py`, `source/reports/renderer.py` | 补关键路径单测 | 2026-08 新增 `tests/test_search_and_report_trends.py`：覆盖趋势计算（最近 7 数据日、新标签、分布桶）、Web 报告渲染与标签转义；`generate_report_content` 其余路径仍无断言 |
| Q23 | 低 | 无 CI 配置、无覆盖率统计 | 项目根 | 加 GitHub Actions | ✅ 已完成 2026-08-07：新增 `.github/workflows/tests.yml`（push 到 dev/master 自动跑 `scripts/run_all_tests.py` + pytest-cov 覆盖率）；覆盖率命令 `python -m pytest --cov=. --cov-report=term-missing tests/`（当前约 66%，`.coveragerc` 排除 tests/scripts/.venv）；首次 push 后 Actions 跑绿为最终验收 |

### 4.7 全局状态与线程安全

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| Q24 | 中 | `progress_store` 永不清理，手动触发的带 UUID task_id 无限累积 | `source/web/progress.py:8-29` | 加 TTL 或完成任务后 pop（仅记录 timestamp 与 user 归属，未清理） |
| Q25 | 低 | `scheduler` 模块级全局单例，测试中需 monkeypatch 重置 | `source/web/application.py:9,67-68` | 迁入 `create_app`（仍为 `source.pipeline` 模块级单例，仅 start 移入 create_app） |

### 4.8 文档差异

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| Q26 | 中 | README Web API 表仅列 10 个端点，实际有 85+ 路由 | `README.md:119-137` | 补全或引用 AGENTS.md（仍仅 10 个端点，未改） |
| Q27 ✅ | ~~中~~ **已完成** | README 未提及论文学习/推荐/AI任务路由等核心功能 | `README.md` | 已补：论文学习页（`/paper/<arxiv_id>/chat` + PDF 缓存链路）、定时任务六步（含推荐评分）、功能模型路由与 Temperature 采样说明、账号权限模型 |
| Q28 | 低 | README 项目结构未列 tests/、docs/、backup.py 等 | `README.md:95-113` | 更新目录树（仍未列 tests/、docs/、scripts/；backup 已并入 source/backups 条目，未改） |
| Q29 ✅ | ~~低~~ **已完成** | AGENTS.md API 清单未列批量操作、阅读清单、reports 等接口 | `AGENTS.md` 第 6 节 | 已补全：页面路由/任务 API/论文 API/学习 API/设置 API/任务日志 API 全量端点清单 |

---

## 五、优先级总览与实施建议

### 按严重度 × 对核心目标价值排序

#### P0 — 安全与稳定（应立即修复）

1. **✅ S1 + S15：默认无密码安全姿态 + 密码慢哈希** — 已完成（v0.7.0 邀请制用户系统）
2. **✅ S14：CSRF 防护** — 已完成（session token + X-CSRF-Token 全量校验）
3. **S27：错误响应脱敏** — `str(e)` 直传客户端贯穿全站（未完成）
4. **✅ Q4 + Q5 + Q6：数据库连接生命周期 + analysis 唯一约束 + busy_timeout 已完成** — 并发稳定性根基已补齐
5. **✅ Q9 / 待修 Q10：settings 深合并已完成；异常回退保护仍待修复** — 配置丢失风险仍需继续收口（Q10 未完成，仅有 guardrail 告警）

#### P1 — 核心功能价值提升（直接服务"AI 辅助论文阅读"目标）

6. **C1：列表接入推荐分排序与展示** — 高频入口真正个性化（未完成）
7. **C6：基础分析结构化（亮点/局限/方法类型）** — 扫列表即判断价值（未完成）
8. **✅ C7：对话历史窗口 + PDF 文本缓存** — PDF 缓存已完成；长对话滚动摘要/段落检索未完成
9. **C13：推荐引入用户反馈闭环** — 越用越准（未完成）
10. **C3 + C20：笔记/进度/学习状态卡** — 持续精读基础设施（未完成）
11. **C16：论文关联/对比/主题追踪/知识图谱** — 解决研究者真痛点（未完成）
12. **✅ F24 + C12：paper.html 深度阅读 Q&A 渲染统一** — 已完成（2026-07-12）
13. **✅ F8：paper_chat 按钮 async 期间禁用** — 已完成（防重复烧 token）

#### P2 — 用户体验一致性（显著提升体感）

14. **✅ F7：全站 loading 状态 / "正在输入"气泡** — 已完成（打字气泡 + spinner）
15. **F14 + F33：导航统一 include + JS 抽离** — 部分完成（导航已对齐、CSRF 已抽 auth.js；仍无 include、escapeHtml 等未抽离）
16. **✅ F1：缺失 CSS class 补全** — 已完成（含 CSS 契约测试）
17. **F23：index.html 摘要注入方式修复** — 脆弱 + DOM 膨胀（未完成）
18. **F25：paper.html saveField 加 HTTP 错误处理** — 静默失败（未完成）
19. **F9：统一 toast 替代 alert/confirm** — 全站体感提升（未完成）
20. **C2：搜索能力重建** — 主题检索可用（未完成）

#### P3 — 质量与可维护性（中期持续改进）

21. **✅ Q1 + Q2 + Q3：拆分超大文件（已完成）** — 根兼容入口与新模块边界已落地
22. **Q14：路由装饰器消除重复模板** — 装饰器已实现未接入（部分完成）
23. **✅ Q13：提取公共行解析助手** — 已完成（`parse_paper_row` 全量复用）
24. **✅ Q20 + Q21：测试拆分 + pytest 基础设施** — Q20 已完成、Q21 部分完成（conftest/fixture 迁移待做）
25. **C10 + C11：quiz 难度自适应 / 苏格拉底终止** — 学习闭环（未完成）
26. **C14 + C15：推荐可解释 / 跨论文记忆** — 个性化升级（未完成）
27. **C17 + C18 + C19：笔记导出 / 术语解释 / 趋势页** — 知识沉淀（未完成）
28. **C8 + C9：Q&A 类型适配 + 截断检测** — C9 已完成（2026-07-12）、C8 未完成
29. **C22：深度阅读转后台 + 进度** — 不卡死（未完成）
30. **F4 + F2：CSS 变量 + 中文字体回退** — 视觉一致（未完成）

### 实施路径建议

**第一批（安全与稳定性基建）**：✅ S1/S15/S14 → S27 → ✅ Q4/Q5/Q6/Q7/Q8/Q9 → Q10
**第二批（核心功能高价值）**：C1 → C6 → ✅ C7（缓存部分）→ ✅ F24/C12 → ✅ F8 → C13 → C3/C20 → C16
**第三批（体验一致性）**：✅ F7 → F14/F33 → ✅ F1 → F23/F25 → F9 → C2 → F18/F19
**第四批（可维护性 + 深化）**：✅ Q1/Q2/Q3 → Q14 → ✅ Q13 → ✅ Q20/Q21 → C10/C11 → C14/C15 → C17/C18/C19 → C8/✅ C9 → C22

### 对核心目标的总结评价

项目已搭建了一个完整且功能丰富的"AI 论文阅读辅助"框架——从自动抓取、基础 AI 分析、深度 Q&A 阅读、自由对话、主动回忆练习、苏格拉底追问、个性化推荐到每日报告，覆盖了论文阅读的主要环节。但以下核心问题限制了其对"阅读效率和质量"的提升效果：

1. **效率短板**：列表不展示/不排推荐分（个性化形同虚设）；搜索薄弱；无笔记/进度/状态卡，续学无路径。
2. **质量短板**：基础分析信息密度低；对话每轮全发 PDF 全文且不缓存文本（成本+延迟）；深度阅读可能截断无声；Q&A 渲染在详情页降级；quiz 不闭环薄弱点。
3. **个性化短板**：推荐不利用用户反馈（最强信号全闲置）；无跨论文记忆；推荐理由单薄。
4. **关联能力空白**：所有功能是单篇视角，无"这篇和那篇什么关系""这个方向怎么演进"——这是研究者真痛点。
5. **知识沉淀空白**：无笔记、无导出、无学习仪表盘，学过即散。

优先推进第二批和第四批中的"关联分析"与"学习闭环"方向，将从根本上把项目从"论文仓库 + 单篇问答"提升为"个人研究情报与学习系统"。
