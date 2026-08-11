# templates/ — Jinja2 页面模板

## 这个目录是什么

本目录存放 Flask 服务端渲染的 **Jinja2 模板**，即浏览器最终看到的那批页面的源文件，但**不是最终 HTML**：

- 浏览器最终收到的 HTML 由服务端执行 `render_template()` 渲染产出——先做 `{{ 变量 }}` 替换、执行 `{% 逻辑 %}`（循环/判断），再返回给浏览器；
- 多数页面（尤其 `settings.html`、`tasks.html`、`paper_chat.html`）只是带样式的**空壳**，正文内容靠页面内嵌的 JavaScript 调用 `/api/*` JSON 接口（`fetch` / `EventSource`）动态填充；
- 纯展示页（`about.html`、`vision.html`、`login.html`）内容在模板内写死，几乎不含动态数据。

**路由对应关系**：页面路由定义在 `source/web/pages.py`，每个路由调用 `render_template("xxx.html", ...)` 传入变量；模板中不要写死数据。

## 文件职责一览

| 模板 | 路由 | 说明 |
|------|------|------|
| `index.html` | `GET /` | 首页：论文列表、日期/标签/评级筛选、分页、热门标签、统计数据 |
| `browse.html` | `GET /browse` | 分类浏览：日期/标签/分类/评级范围/已分析/深度分析/隐藏状态等多条件组合筛选 |
| `search.html` | `GET /search?q=` | 搜索结果：跨字段命中高亮（`title_highlight`/`search_preview` 由后端预切分） |
| `paper.html` | `GET /paper/<key>` | 论文详情：元数据、AI 基础分析、深度阅读 Q&A、手动编辑评级/标签/摘要 |
| `paper_chat.html` | `GET /paper/<key>/chat` | 论文学习页：自由讨论 / 主动问答练习 / 苏格拉底追问三个标签页，正文全部由内嵌 JS 调学习 API 渲染 |
| `tasks.html` | `GET /tasks` | 论文处理页：抓取、分析、生成报告、完整流水线、两步手动导入（预览→确认）、SSE 进度条 |
| `settings.html` | `GET /settings` | 设置页（最大，1919 行）：供应商/功能路由、Prompt、个性化推荐、网络/代理、抓取、WebDAV、报告邮件、定时任务、日志管理 |
| `login.html` | `GET /login` | 管理密码登录页 |
| `reports.html` | `GET /reports` | 每日报告列表 |
| `report_detail.html` | `GET /reports/<date>` | 单份日报详情（服务端生成的 HTML 内容） |
| `reading_list.html` | `GET /reading-list` | 阅读清单：未读/已读筛选与状态统计 |
| `about.html` | `GET /about` | 公开项目宣传页（首页入口） |
| `vision.html` | `GET /vision` | 实验室愿景页（仅直接访问，深色独立样式） |

## 模板与 JS/样式约定

- **业务页 CSS** 位于 `/static/css/`：所有业务页加载 `core.css` + `components.css`；首页/浏览/搜索/阅读清单加载 `pages/library.css`；论文详情与学习页再加载 `rich-text.css` 和各自页面 CSS；任务、设置、报告、登录加载对应 `pages/*.css`。`promo.css` 仅供 `about.html`/`vision.html`，第三方资源位于 `vendor/`；
- **富文本渲染**：论文详情页与学习页中的 Markdown/数学公式必须通过 `static/rich_text.js` 的 `RichText.render()` / `RichText.renderMath()` 渲染，不要在模板里复制清洗逻辑；
- **认证变量**：`auth.py` 的上下文处理器向所有模板注入 `auth_enabled`、`is_authenticated`、`password_change_recommended`（仅登录后可能为真）、`now`（当前时间）、`_remove_param`/`_build_query`（分页 URL 辅助），模板可直接使用；
- **动态页面模式**：`settings.html`/`tasks.html`/`paper_chat.html` 均采用「模板骨架 + 内嵌 JS 调 API」模式，页面逻辑改动优先看对应 Blueprint 的 API 端点与模板内 `<script>` 区块；
- **进度显示**：耗时操作前端通过 `EventSource('/api/progress/<task_id>')` 订阅 SSE，任务终态（completed/error）后自动断开。

## 修改页面的开发步骤

1. 找对应模板文件（见上表）与 `source/web/pages.py` 中渲染它的路由；
2. 静态数据改动直接改模板；动态数据改动需同时改后端 API（`source/web/*_api.py`）与模板内 JS；
3. 共享布局与组件分别加入 `static/css/core.css`、`components.css`；富文本加入 `rich-text.css`；页面专属规则加入 `static/css/pages/` 并只在对应模板加载。宣传页继续使用 `static/promo.css`；
4. 改完在浏览器实际访问验证（本地 `python app.py`）。
