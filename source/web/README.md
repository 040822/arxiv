# source/web/ — Flask Web 层

本目录实现 Flask 应用组装、页面路由与全部 HTTP API 端点，是浏览器与后端业务（`analyzer`、`fetcher`、`source/storage`、`source/settings`、`source/pipeline`）之间的薄层。路由不直接操作数据库，业务逻辑统一调用上层模块。

## 文件职责一览

| 文件 | Blueprint 名 | 职责 |
|------|--------------|------|
| `__init__.py` | — | 包入口，导出 `app` / `build_app` / `create_app` |
| `application.py` | — | Flask 应用组装与启动（注册全部 Blueprint、初始化 DB、启动调度器） |
| `auth.py` | `auth` | 邀请制账号、三级权限、CSRF、用户管理、审计与模板 principal |
| `pages.py` | `pages` | 全部页面路由（首页、浏览、搜索、论文详情、学习页、设置、任务、报告、阅读清单） |
| `papers_api.py` | `papers_api` | 论文 JSON 接口（列表/标签/统计、分析编辑、隐藏/删除、批量操作、手动添加、阅读清单） |
| `import_api.py` | `paper_import_api` | 手动论文导入（链接/PDF 预览解析、确认入库、附件替换） |
| `learning_api.py` | `learning_api` | 论文学习接口（自由讨论、主动问答练习、苏格拉底追问） |
| `tasks_api.py` | `tasks_api` | 手动任务执行与日志接口（抓取/分析/生成报告/流水线/备份/邮件） |
| `task_endpoint.py` | — | 手动任务端点的共享工具：`TaskEndpointResult` 与 `task_endpoint` 装饰器（非 Blueprint） |
| `settings_api.py` | `settings_api` | 运行时配置读写接口（并发数、AI 任务路由、代理、WebDAV、邮件、抓取、定时、Prompt、DB 信息） |
| `providers_api.py` | `providers_api` | AI 供应商连接管理（CRUD、预设、模型列表拉取、功能路由连通性测试） |
| `progress.py` | — | 进程内任务进度注册表（`update_progress` / `get_progress`），供 SSE 轮询 |

## 各文件详细说明

### `__init__.py`
包入口，仅做转发导出，保证外部可 `from source.web import app`。

### `application.py`
- `build_app()`：创建 Flask 实例，严格读取 `session_secret`（优先环境变量 `FLASK_SECRET_KEY`）、配置上传上限（101 MB）和 30 天滑动 session，注册全部 8 个 Blueprint；`create_app()` 的 v4 数据迁移负责引导唯一 admin。
- `app`：模块级单例，供 WSGI/`app.py` 直接引用。
- `create_app()`：应用工厂，依次执行 `init_db()`（含迁移）、遗留 `running` 日志标记 `interrupted`、`configure_daily_job()` 重建定时任务、启动 APScheduler。

### `auth.py`
- `route_policy()` 将非静态路由明确归为 public/member/admin，未知路由失败关闭为 admin；API 未登录返回 401、权限不足返回 403。
- session 保存 `user_id/session_version`，逐请求校验存在、启用和版本；改密、重置、停用或删除立即撤销旧会话。
- 全部非安全方法校验 session-backed CSRF；登录失败按 IP 与规范化用户名执行 15 分钟 5 次限制且使用统一文案。
- 路由包括登录/退出、账号改密、admin 兼容改密别名，以及管理员用户 CRUD 和分页审计。
- 模板上下文注入 `current_user`、`is_admin`、`csrf_token`、时间与分页辅助函数。

### `pages.py`
页面渲染路由（Jinja2 模板）：
- `/` 首页（page/tag/date/min_rating/per_page 筛选，默认最新日期）
- `/browse` 多条件组合筛选浏览（日期/标签/分类/评级范围/已分析/深度分析/隐藏/来源）
- `/search` 跨字段 AND 搜索，含命中高亮（`_highlight_search_text`）与摘要预览（`_search_excerpt`）
- `/paper/<key>` 论文详情；`/paper/<key>/chat` 论文学习页（含最近 10 个练习会话）
- `/settings`（admin）、`/tasks`（成员见导入、admin 见批处理）、`/reports`、`/reports/<date>`、`/reading-list`、`/about`、`/vision`

### `papers_api.py`
论文相关 JSON 接口：
- 只读：`/api/papers`、`/api/tags`、`/api/stats`、`/api/paper/<id>/todo/status`、`/api/reading-list`
- 编辑：`PUT /api/paper/<id>/analysis`（星级/标签/摘要手动修正）
- 状态：`/api/paper/<id>/hide|unhide`、`DELETE /api/paper/<id>`、批量删除/隐藏
- 分析：`/api/papers/batch-analyze`、`/api/paper/<id>/reanalyze`（重跑深度阅读，校验 `### Qn:` 完整性）
- 手动添加 arXiv 论文：`/api/paper/add`（解析编号 → 抓取 → 基础分析 → 深度阅读，SSE 进度）
- 阅读清单：`/api/paper/<id>/todo`（POST 加入 / DELETE 移除）、`/todo/read`、`/todo/unread`（均需登录）

### `import_api.py`
手动导入非 arXiv 来源论文（`source/imports` 的 HTTP 层）：
- `POST /api/paper/import/preview`：解析链接或上传 PDF，返回可编辑元数据草稿（不入库）
- `POST /api/paper/import`：确认入库（`p_{uuid}` 生成 paper_key），可选基础分析/深度阅读，失败时清理未提交 PDF
- `POST /api/paper/<key>/pdf`：为已有论文上传/替换 PDF，失败时回滚旧 PDF

### `learning_api.py`
论文学习功能接口（PDF 全文驱动的 AI 对话）：
- `/api/paper/<id>/chat/messages`：自由讨论 GET（历史）/ POST（发送并保存回复）
- `/api/paper/<id>/quiz/sessions`：创建 quick3/standard6 主动问答练习；`GET .../<session_id>` 读取详情；`POST .../questions/<qid>/answer` 提交答案并评分
- `/api/paper/<id>/socratic/sessions`：创建独立苏格拉底追问会话；`POST .../<session_id>/reply` 提交回答并获取下一问

### `tasks_api.py`
手动任务执行（每次调用写 `task_logs` 日志 + 内存进度）：
- `POST /api/fetch`（支持 category/days/date；优先级 `date > days > 默认最近 1 天`；废弃的 `max_results` 出现即返回 400）、`POST /api/analyze`（limit）、`POST /api/recommendations/recalculate`、`POST /api/generate`（date/ai_summary/recommend）、`POST /api/run`（完整流水线，`pipeline_lock` 非阻塞互斥，冲突返回 409）
- `POST /api/backup/webdav/run`、`POST /api/email-report/test`（force 手动执行，不影响自动去重日期）
- `GET /api/progress/<task_id>`：SSE 实时进度（completed/error 自动断开）
- 日志：`/api/tasks/stats`、`/api/tasks/logs`、`/api/tasks/clear`、`/api/tasks/scheduled`

### `task_endpoint.py`
非 Blueprint 工具模块：
- `TaskEndpointResult`：dataclass，统一描述「HTTP 响应 + 终态任务日志」（`ok`/`error` 构造器）
- `task_endpoint(task_name, start_message)` 装饰器：包裹手动任务端点，保证一条 task log 的 start → finish（success/error）生命周期，异常时兜底 500 并记日志；通过 `__globals__` 保留对 `start_task_log`/`finish_task_log`/`jsonify` 的模块级 patch 接缝（供测试）。

### `settings_api.py`
设置页各配置项读写接口，密码类字段 GET 时脱敏：
- 简单标量：`/api/settings/concurrency`、`/api/settings/per_page`
- AI：`/api/settings/ai-tasks`（GET/POST）、`/api/settings/ai-usage`
- 个性化：`/api/settings/personalization`
- 网络：`/api/settings/proxy`、`/api/test_proxy`（测试 arXiv 连通性）
- 备份/邮件：`/api/settings/webdav-backup`、`/api/settings/email-report`（密码留空保留旧值）
- 抓取/定时：`/api/settings/fetch`、`/api/settings/schedule`（保存后立即 `configure_daily_job()` 重建调度）
- Prompt：`/api/prompts`（支持 profile_key 保存 Prompt Profile，保存前 `validate_prompt_template`）
- 数据库：`/api/db/info`（含 DB 文件大小、平均评级）

### `providers_api.py`
AI 供应商连接管理：
- `GET/POST /api/providers`、`PUT/DELETE /api/providers/<key>`（GET 只返回 `api_key_masked`，绝不返回明文；被功能路由引用的供应商禁止删除，返回 409 + 引用列表）
- `GET /api/providers/presets`（内置预设）、`POST /api/providers/models`（调供应商 /models 拉取可用模型）
- `POST /api/settings/ai-tasks/<task_key>/test`：用未保存的草稿配置发短请求，返回延迟、思考能力检测（reasoning_content / reasoning_tokens / 启发式判断）与思考协议提示

### `progress.py`
进程内进度注册表：`update_progress(task_id, data)` 写入带时间戳的 dict，`get_progress(task_id)` 读取。线程安全（`threading.Lock`），仅存内存——服务重启后进度丢失，前端配合 SSE 短轮询使用。

## 注意事项

- **鉴权边界**：新增路由必须在 `route_policy()` 分类；私有存储查询必须从当前 principal 注入 `user_id`，不得接收客户端 user id。隐藏论文是公开展示筛选属性，不是访问控制。
- **进度上报**：耗时任务通过 `update_progress(task_id, ...)` 上报，前端用 `/api/progress/<task_id>` SSE 订阅；task_id 由前端传入（如 `add_paper`、`fetch`）。
- **手动任务日志**：推荐新任务端点使用 `task_endpoint.py` 的装饰器与 `TaskEndpointResult`；`tasks_api.py` 中部分旧端点仍为手写 `start_task_log`/`finish_task_log` 模式。
- **部分文件存在未使用的共享 import 块**（`analyzer`/`backup`/`fetcher`/`source.pipeline` 整块引入），属历史遗留，维护时无需全部清理。
