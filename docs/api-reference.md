# API 接口文档

## 目录

- [页面路由](#页面路由)
- [认证](#认证)
- [页面参数](#页面参数)
- [任务 API](#任务-api)
- [论文 API](#论文-api)
- [阅读清单 API](#阅读清单-api)
- [论文学习 API](#论文学习-api)
- [设置 API](#设置-api)
- [任务日志 API](#任务日志-api)

---

## 页面路由

所有页面路由返回 HTML，使用 Jinja2 模板渲染。

| 路由 | 方法 | 模板 | 说明 |
|------|------|------|------|
| `GET /` | GET | index.html | 首页，论文列表 |
| `GET /paper/<arxiv_id>` | GET | paper.html | 论文详情 |
| `GET /paper/<arxiv_id>/chat` | GET | paper_chat.html | 单篇论文学习页（对话、问答、苏格拉底追问；设置管理密码后需登录） |
| `GET /search?q=` | GET | search.html | 搜索 |
| `GET /browse` | GET | browse.html | 分类浏览 |
| `GET /about` | GET | about.html | 公开项目介绍；首页提供入口 |
| `GET /vision` | GET | vision.html | 实验室科研情报基础设施愿景；仅直接访问 |
| `GET /settings` | GET | settings.html | 设置（设置管理密码后需登录） |
| `GET /tasks` | GET | tasks.html | 论文处理（设置管理密码后需登录） |
| `GET /reports` | GET | reports.html | 报告列表 |
| `GET /reports/<date>` | GET | report_detail.html | 报告详情 |
| `GET /reading-list` | GET | reading_list.html | 阅读清单 |
| `GET /login` | GET | login.html | 管理登录 |

---

## 认证

未设置管理密码时，系统保持本地免登录兼容。设置管理密码后，`/settings`、`/tasks`、`/paper/<arxiv_id>/chat`、所有 `POST/PUT/DELETE` 写接口、设置读取接口、任务日志接口都需要登录。`/about` 与 `/vision` 为公开只读页面，不受管理密码限制。

管理登录默认通过签名 cookie 持久保存 180 天，不需要“记住我”开关。默认使用 `data/settings.json` 内部字段 `session_secret` 作为 Flask session 签名密钥，因此服务重启后仍可保持登录；如果部署环境设置了 `FLASK_SECRET_KEY`，则优先使用该环境变量。修改管理密码后，旧 cookie 会因密码版本 token 不匹配而失效。

例外：阅读清单的加入/移除接口 `POST/DELETE /api/paper/<arxiv_id>/todo` 为公开轻量操作，不要求管理密码；标记已读/未读仍需要登录。

```
GET  /api/auth/status
POST /api/auth/login
POST /api/auth/logout
```

`POST /api/auth/login` Body：

```json
{"password": "管理密码"}
```

未登录访问受保护 API 时返回：

```json
{
    "status": "error",
    "message": "需要登录后才能执行该操作",
    "auth_required": true
}
```

## 页面参数

### 首页参数 `GET /`

| 参数 | 类型 | 说明 |
|------|------|------|
| `page` | int | 页码，默认 1 |
| `tag` | string | 标签筛选 |
| `date` | string | 日期筛选（YYYY-MM-DD） |
| `min_rating` | int | 最低评级（0-5） |
| `per_page` | int | 每页数量（5/10/20/50/100） |

默认显示数据库中最新一天的论文。

### 分类浏览参数 `GET /browse`

| 参数 | 类型 | 说明 |
|------|------|------|
| `page` | int | 页码 |
| `date` | string | 日期 |
| `tag` | string | 标签 |
| `category` | string | 分类（如 cs.RO） |
| `min_rating` | int | 最低评级 |
| `max_rating` | int | 最高评级 |
| `has_analysis` | string | yes/no |
| `has_deep_analysis` | string | yes/no |
| `hidden` | string | yes（显示已隐藏） |

### 阅读清单参数 `GET /reading-list`

| 参数 | 类型 | 说明 |
|------|------|------|
| `status` | string | unread/read（留空显示全部） |

---

## 任务 API

### 抓取论文

```
POST /api/fetch
```

需要登录。

| 参数（Query） | 类型 | 说明 |
|---------------|------|------|
| `category` | string | 分类（如 cs.RO），留空使用默认 |
| `max_results` | int | 最大数量（仅在 days 和 date 都留空时生效） |
| `days` | int | 抓取最近 N 天 |
| `date` | string | 精确抓取某天（YYYY-MM-DD） |
| `task_id` | string | SSE 进度任务 ID |

优先级：date > days > max_results

**响应：**
```json
{
    "status": "ok",
    "count": 42,
    "unanalyzed": 15,
    "message": "抓取完成：42 篇新论文（cs.RO）（最近 30 天）"
}
```

### AI 分析

```
POST /api/analyze
```

需要登录。

| 参数 | 类型 | 说明 |
|------|------|------|
| `limit` | int | 分析数量上限（默认 50） |
| `task_id` | string | SSE 进度任务 ID |

### 生成报告

```
POST /api/generate
```

需要登录。

| 参数（Query） | 类型 | 说明 |
|---------------|------|------|
| `date` | string | 指定日期（留空使用最新日期） |
| `ai_summary` | bool | 传 `1/true` 时使用 `report_summary` 任务模型生成 AI 导读；默认不调用 LLM |
| `recommend` | bool | 默认补齐当前研究兴趣下缺失/过期的推荐分；传 `0/false/no/off` 跳过 |

### 抓取、分析并生成报告

```
POST /api/run
```

需要登录。

| 参数 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | SSE 进度任务 ID |

依次执行：抓取 → 分析 → 推荐评分 → 生成报告。该接口与定时日报共享完整流水线互斥锁；已有流水线运行时返回 HTTP 409。

### SSE 进度流

```
GET /api/progress/<task_id>
```

返回 `text/event-stream`，每个事件格式：
```json
{
    "current": 5,
    "total": 30,
    "status": "running",
    "success": 4,
    "skip": 1,
    "fail": 0,
    "message": "[5/30] 2605.12345"
}
```

status 值：`running` / `completed` / `error`

---

## 论文 API

### 论文列表

```
GET /api/papers
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `page` | int | 页码 |
| `tag` | string | 标签 |
| `date` | string | 日期 |
| `min_rating` | int | 最低评级 |

### 标签列表

```
GET /api/tags
```

返回：`[["VLA", 15], ["World Model", 12], ...]`

### 统计信息

```
GET /api/stats
```

返回：
```json
{
    "total_papers": 1234,
    "analyzed_papers": 567,
    "unanalyzed_papers": 667,
    "concurrency": 5,
    "per_page": 20
}
```

### 更新论文分析

```
PUT /api/paper/<arxiv_id>/analysis
```

Body (JSON)：
```json
{
    "rating": 4,
    "tags": ["VLA", "Diffusion Policy"],
    "summary_cn": "...",
    "value_comment": "...",
    "qa_analysis": "..."
}
```

`rating` 为 AI 初评 + 用户可手动修正字段。基础分析会生成并写入 0-5 星评分；用户仍可通过该接口手动调整。`legacy_ai_rating` 仅作为历史 AI 评级备份/恢复字段。

字段均可选，只传需要更新的字段。

### 隐藏/取消隐藏

```
POST /api/paper/<arxiv_id>/hide
POST /api/paper/<arxiv_id>/unhide
```

### 删除论文

```
DELETE /api/paper/<arxiv_id>
```

级联删除关联的 analysis、reading_list、论文对话和问答练习记录。

### 重新生成深度阅读

```
POST /api/paper/<arxiv_id>/reanalyze
```

下载 PDF，生成/刷新 Q&A 深度阅读；不会覆盖已有标签、AI 评级/人工修正、中文摘要和简评。后端会检查当前深度阅读 Prompt 声明的全部 Q 编号；遇到截断或缺题时最多自动补全一次。补全后仍不完整时返回 `status: "warning"`、`missing_questions`、`continuation_used` 和 `finish_reason`，并保留原有 `qa_analysis`。

### 手动导入论文

```
POST /api/paper/import/preview
POST /api/paper/import
```

预览接口接受 multipart：`source_url` 可为 arXiv、OpenReview、DOI、期刊/会议页面或 PDF 直链，`pdf_file` 可为本地 PDF。预览只返回可编辑的 `draft`，不会写入数据库；PDF 元数据可由独立的 `paper_import` AI 任务预填。

确认接口接受 `metadata` JSON 字符串、可选的同一 `pdf_file`，以及 `run_basic`、`run_deep`。`metadata` 至少包含 `title`，成功响应返回通用 `paper_key` 与 `detail_url`。

`POST /api/paper/<paper_key>/pdf` 可为已有论文上传或替换 PDF。上传和远程 PDF 上限均为 100 MB，服务端链接抓取拒绝本机及私有网络地址。

手动导入不参加定时分析、推荐和日报，但可主动触发全部 AI 学习能力。旧的 `POST /api/paper/add` arXiv 单篇接口继续保留兼容。

### 批量操作

```
POST /api/papers/batch-delete    # 批量删除
POST /api/papers/batch-hide      # 批量隐藏
POST /api/papers/batch-analyze   # 批量分析（基础模式）
```

Body (JSON)：
```json
{
    "paper_keys": ["2603.18336", "p_0123456789abcdef"]
}
```

---


`arxiv_ids` 仍作为兼容字段接受。
## 阅读清单 API

### 添加到清单

```
POST /api/paper/<arxiv_id>/todo
```

公开接口，不要求登录。已在清单中则返回 ok（不重复添加）。

### 检查清单状态

```
GET /api/paper/<arxiv_id>/todo/status
```

返回该论文是否已在阅读清单中。此接口只读，公开页面可使用。

### 从清单移除

```
DELETE /api/paper/<arxiv_id>/todo
```

公开接口，不要求登录。

### 标记已读/未读

```
POST /api/paper/<arxiv_id>/todo/read
POST /api/paper/<arxiv_id>/todo/unread
```

需要登录。

### 获取清单

```
GET /api/reading-list
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `status` | string | unread/read（留空返回全部） |

返回：
```json
{
    "papers": [...],
    "counts": {"total": 10, "unread": 5}
}
```

---

## 论文学习 API

学习接口用于单篇论文的自由讨论、主动问答练习和苏格拉底追问。设置管理密码后，这些接口均需要登录。

学习功能会优先读取 `data/pdf_cache/<arxiv_id>.pdf`；缓存不存在时才调用 PDF 下载逻辑。PDF 下载或文本提取失败时回退到摘要，不阻断接口。模型请求的消息顺序固定为：`system` → 稳定任务说明 → 稳定论文上下文（标题、作者、摘要、PDF 全文/摘要回退）→ 动态历史/用户输入。

学习 API 的写响应通常包含 `meta`：

```json
{
  "used_pdf_cache": true,
  "used_pdf_full_text": true,
  "prompt_tokens": 12000,
  "completion_tokens": 800,
  "total_tokens": 12800,
  "cached_tokens": 9500,
  "cache_miss_tokens": 2500
}
```

### 自由讨论历史

```
GET /api/paper/<arxiv_id>/chat/messages
```

返回该论文的自由讨论历史：

```json
{
  "status": "ok",
  "messages": [
    {"id": 1, "role": "user", "content": "...", "created_at": "..."},
    {"id": 2, "role": "assistant", "content": "...", "created_at": "..."}
  ]
}
```

### 发送论文讨论消息

```
POST /api/paper/<arxiv_id>/chat/messages
```

Body：

```json
{"message": "这篇论文的核心方法和 VLA 有什么关系？"}
```

接口只把最近 12 条历史消息放在稳定论文上下文之后，并保存本轮用户消息和模型回复。

### 创建问答练习

```
POST /api/paper/<arxiv_id>/quiz/sessions
```

Body：

```json
{"mode": "quick3"}
```

`mode` 支持：

- `quick3`：生成 3 道快速主动回忆题
- `standard6`：生成 6 道标准主动回忆题

返回 `session.questions`，每题包含 `question` 和 `expected_points`。

### 读取练习会话

```
GET /api/paper/<arxiv_id>/quiz/sessions/<session_id>
```

返回题目、每题最近一次用户答案和反馈。

### 提交单题答案

```
POST /api/paper/<arxiv_id>/quiz/questions/<question_id>/answer
```

Body：

```json
{"answer": "我的理解是..."}
```

返回结构化反馈：

```json
{
  "status": "ok",
  "feedback": {
    "score": 4,
    "feedback": "总体反馈",
    "correct_points": ["..."],
    "missing_points": ["..."],
    "misconceptions": ["..."],
    "improved_answer": "..."
  }
}
```

### 创建苏格拉底追问会话

```
POST /api/paper/<arxiv_id>/socratic/sessions
```

创建独立追问会话并返回第一问。该模式使用 `paper_quiz` 模型路由，不预生成固定题目。

### 回复苏格拉底追问

```
POST /api/paper/<arxiv_id>/socratic/sessions/<session_id>/reply
```

Body：

```json
{"answer": "我的回答..."}
```

接口会保存用户回答，对本轮回答给出反馈，并追加下一问。

---

## 设置 API

### 供应商管理

```
GET  /api/providers              # 列出所有供应商
POST /api/providers              # 添加供应商
PUT  /api/providers/<key>        # 更新供应商
DELETE /api/providers/<key>      # 删除供应商
GET  /api/providers/presets      # 获取预设供应商
POST /api/providers/models       # 从供应商 /models 接口获取模型列表
```

`POST /api/providers/models` Body：

```json
{
    "provider_key": "deepseek",
    "api_key": "sk-xxx",
    "base_url": "https://api.deepseek.com"
}
```

`provider_key` 可选；传入后会优先复用已保存的 API Key/Base URL，并在成功获取后保存 `available_models`。

供应商配置只保存 `name`、`api_key`、`base_url`、`available_models`。模型、输出长度、Temperature 采样控制与思考模式均保存在功能模型路由。未启用 Temperature 时不发送该参数，其他采样参数不发送并采用模型默认行为。删除仍被任一功能路由引用的供应商会返回 HTTP 409 和引用功能列表。

`GET /api/providers` 只返回 `api_key_masked`，不会返回完整 `api_key`。

### 测试连接

```
POST /api/test_proxy             # 测试 arXiv 代理连接
POST /api/settings/ai-tasks/<task_key>/test  # 测试指定功能模型路由
```

功能路由测试 Body 使用当前未保存草稿：

```json
{
  "config": {
    "provider_key": "deepseek",
    "model": "deepseek-chat",
    "is_thinking": false,
    "temperature_enabled": true,
    "temperature": 0.2,
    "max_tokens_enabled": true,
    "max_tokens": 1200
  }
}
```

它发送短 chat 请求并返回 `task_key/task_name/provider_key/model/duration_ms/thinking_detection`，因此“测试当前连接”具体测试的是 URL 中指定功能的完整路由，而不是抽象的供应商连接。模型列表刷新则只验证供应商连接。路由测试复用全局代理配置且不会自动保存草稿；检测到思考能力仅作为界面提示。

### Prompt 管理

```
GET  /api/prompts                # 获取 prompt
POST /api/prompts                # 保存 prompt
```

`GET /api/prompts` 会返回旧版兼容字段 `system_prompt/user_prompt`，以及新版 `prompt_profiles`。

新版保存单个 Prompt Profile：

```json
{
  "profile_key": "deep_reading",
  "system": "...",
  "instruction": "...{tag_candidates}..."
}
```

旧版 `system_prompt/user_prompt` 保存仍可用，会映射到 `deep_reading`。新版 Profile 中论文动态内容不写入 instruction，而是由后端作为独立 JSON message 传入；`basic_analysis` 可使用 `{tag_candidates}` 和 `{rating_criteria}`，并返回 AI 初评 `rating`；`deep_reading` 只需要描述 Q&A 输出；`paper_chat` 和 `paper_quiz` 用于论文学习页，稳定 PDF 上下文由后端统一拼接。

### 配置管理

```
POST /api/settings/concurrency   # 保存并发数（1-20）
POST /api/settings/per_page      # 保存每页数量（5-100）
GET  /api/settings/ai-tasks      # 获取 AI 功能模型路由
POST /api/settings/ai-tasks      # 保存 AI 功能模型路由
GET  /api/settings/ai-usage      # 获取近期 LLM token 用量汇总和趋势
GET  /api/settings/personalization   # 获取研究兴趣
POST /api/settings/personalization   # 保存研究兴趣（不自动重算）
GET  /api/settings/schedule      # 获取内置日报调度配置
POST /api/settings/schedule      # 保存调度配置并重建 APScheduler job
GET  /api/settings/proxy         # 获取代理配置
POST /api/settings/proxy         # 保存代理配置
GET  /api/settings/fetch         # 获取抓取配置
POST /api/settings/fetch         # 保存抓取配置
GET  /api/settings/webdav-backup # 获取 WebDAV 云备份配置（不返回明文密码）
POST /api/settings/webdav-backup # 保存 WebDAV 云备份配置
GET  /api/settings/email-report  # 获取每日报告邮件配置（不返回明文密码）
POST /api/settings/email-report  # 保存每日报告邮件配置
```

`/api/settings/ai-usage` 查询参数：

- `days`：统计天数，默认 `7`，范围 `1-365`
- `group_by`：趋势图分组方式，`task` 或 `model`，默认 `task`

返回中 `items` 保留按任务/模型的明细汇总；`dates`、`groups`、`totals` 用于账单页趋势图。token 明细包含 `cached_tokens` 和 `cache_miss_tokens`，用于观察 DeepSeek/OpenAI 等供应商的 prompt cache 命中情况。

`POST /api/settings/schedule` Body：

```json
{
    "enabled": true,
    "days_of_week": ["mon", "tue", "wed", "thu", "fri"],
    "hour": 8,
    "minute": 0,
    "fetch_days": 3,
    "analyze_limit": 1000,
    "fetch_retry_interval_minutes": 10,
    "fetch_max_retries": 20
}
```

POST 支持部分更新，缺失字段沿用当前配置；保存后立即重建唯一的 APScheduler job。旧配置迁移时默认全周执行、回看 3 天、分析上限 1000、抓取失败每 10 分钟重试且最多 20 次；时区使用服务器本地时区，不补跑停机期间错过的触发。抓取重试策略仅作用于内置定时日报，不影响手动抓取或一键执行接口。

`POST /api/settings/webdav-backup` Body：

```json
{
    "enabled": true,
    "url": "https://example.com/remote.php/dav/files/user",
    "username": "alice",
    "password": "webdav应用密码",
    "remote_dir": "arxiv-backups",
    "history_days": 3
}
```

`GET /api/settings/webdav-backup` 只返回 `password_masked`，不会返回明文 `password`。POST 时 `password` 为空会保留已有密码。

`POST /api/settings/email-report` Body：

```json
{
    "enabled": true,
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "security": "starttls",
    "username": "alice@example.com",
    "password": "smtp授权码",
    "sender": "alice@example.com",
    "recipients": "bob@example.com; carol@example.com",
    "subject_template": "AI 论文日报 {date} - {paper_count} 篇论文",
    "site_url": "https://papers.example.com"
}
```

`security` 支持 `starttls`、`ssl`、`none`。`recipients` 可传数组，也可用逗号、分号或换行分隔。`GET /api/settings/email-report` 只返回 `password_masked`；POST 时 `password` 为空会保留已有 SMTP 密码。

`GET/POST /api/settings/ai-tasks` 的任务 key 固定为：

- `paper_import`：上传 PDF 预览时提取可编辑元数据，不入库、不执行基础分析
- `basic_analysis`：批量/自动基础分析，建议廉价模型
- `deep_reading`：单篇 Q&A 深度阅读，默认可启用 high 思考，不覆盖基础分析字段
- `report_summary`：报告 AI 导读，只在生成报告时显式启用
- `recommendation`：个性化推荐评分，按研究兴趣返回 `recommendation_score` 和 `recommendation_reason`，模型路由独立配置
- `paper_chat`：单篇论文自由讨论，复用稳定论文全文上下文
- `paper_quiz`：主动问答练习、答案评分和苏格拉底追问，复用稳定论文全文上下文

每个任务支持独立的 `provider_key`、`model`、`is_thinking`、`thinking_effort`、`temperature_enabled/temperature` 和 `max_tokens_enabled/max_tokens`。Temperature 未启用时省略该请求参数；其他采样参数不属于公共接口，也不会发送。

### 个性化推荐

```
POST /api/recommendations/recalculate
```

需要登录。按当前研究兴趣重算缺失或兴趣 hash 过期的推荐分，只处理已有基础分析结果的非隐藏论文。

查询参数：

| 参数 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | SSE 进度 ID，可通过 `/api/progress/<task_id>` 订阅 |
| `limit` | int | 最大处理数量，默认 200，最大 1000 |
| `date` | string | 可选，限制某个发布日期 |

保存研究兴趣只写入配置，不触发 LLM；手动重算、每日定时任务、一键执行和默认报告生成会使用独立 `recommendation` 模型路由补齐推荐分。

### WebDAV 云备份

```
POST /api/backup/webdav/run
```

需要登录。立即创建备份包并上传到 WebDAV；即使未启用每日自动备份，也可用于手动测试。备份包包含 `papers.db` 一致性快照、`settings.json` 和 manifest；Web 日报位于数据库中，会随快照备份。远端会写入 `arxiv-backup-latest.zip` 和 `arxiv-backup-YYYYMMDD-HHMMSS.zip`，并按 `history_days` 清理过期历史备份。

### 报告邮件测试发送

```
POST /api/email-report/test
```

需要登录。使用最近一份已生成的日报告测试 SMTP 发送；即使未启用日报邮件步骤，也可用于手动验证配置。若暂无报告，返回 400。邮件正文是摘要版：先尝试调用 `report_summary` 生成 AI 导读，再展示推荐分高于 `important_score_threshold`（默认 80）的重点论文和最多 `overview_limit`（默认 20）篇速览；导读失败不阻断发送。两个阈值可在 `/api/settings/email-report` 配置。测试发送和每日自动发送都会复用现有 `/api/settings/proxy` 网络代理配置。自动发送失败写入日报 email 步骤并使父任务标记为 `warning`，随后仍执行备份；手动测试发送写独立 `email_report` 日志。

### 数据库信息

```
GET /api/db/info
```

返回：论文总数、已分析数、标签种类、分类数、日期范围、平均评级、数据库文件占用等。数据库大小会合并统计 `papers.db`、`papers.db-wal` 和 `papers.db-shm`，并在 `db_files` 中返回每个文件的明细。

### 管理密码

```
POST /api/admin/password         # 设置密码
DELETE /api/admin/password       # 清除密码
```

`DELETE /api/admin/password` 必须传当前密码：

```json
{"current_password": "当前管理密码"}
```

---

## 任务日志 API

### 任务统计

```
GET /api/tasks/stats
```

兼容保留的统计接口；设置页不再展示该统计卡片。

### 任务日志

```
GET /api/tasks/logs
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `task` | string | 任务类型筛选 |
| `page` | int | 页码 |

日报父日志额外包含 `steps` 数组；步骤状态可能为 `pending/running/success/warning/error/skipped/interrupted`。

### 定时任务

```
GET /api/tasks/scheduled
```

返回完整配置、服务器时区、实际注册的 job 和最近一次日报运行：

```json
{
    "enabled": true,
    "days_of_week": ["mon", "tue", "wed", "thu", "fri"],
    "hour": 8,
    "minute": 0,
    "fetch_days": 3,
    "analyze_limit": 1000,
    "fetch_retry_interval_minutes": 10,
    "fetch_max_retries": 20,
    "timezone": "CST",
    "jobs": [
        {"id": "daily_pipeline", "name": "AI 论文日报", "next_run": "...", "trigger": "..."}
    ],
    "last_run": {"id": 42, "status": "warning", "steps": []}
}
```

### 清理日志

```
POST /api/tasks/clear
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `keep_days` | int | 保留天数（默认 30） |

---

## 通用响应格式

### 成功

```json
{
    "status": "ok",
    "message": "操作成功",
    ...其他字段
}
```

### 错误

```json
{
    "status": "error",
    "message": "错误信息"
}
```

HTTP 状态码：400（参数错误）、404（不存在）、500（服务器错误）
