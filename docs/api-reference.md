# API 接口文档

## 目录

- [页面路由](#页面路由)
- [认证](#认证)
- [页面参数](#页面参数)
- [任务 API](#任务-api)
- [论文 API](#论文-api)
- [阅读清单 API](#阅读清单-api)
- [设置 API](#设置-api)
- [任务日志 API](#任务日志-api)

---

## 页面路由

所有页面路由返回 HTML，使用 Jinja2 模板渲染。

| 路由 | 方法 | 模板 | 说明 |
|------|------|------|------|
| `GET /` | GET | index.html | 首页，论文列表 |
| `GET /paper/<arxiv_id>` | GET | paper.html | 论文详情 |
| `GET /search?q=` | GET | search.html | 搜索 |
| `GET /browse` | GET | browse.html | 分类浏览 |
| `GET /settings` | GET | settings.html | 设置（设置管理密码后需登录） |
| `GET /tasks` | GET | tasks.html | 任务管理（设置管理密码后需登录） |
| `GET /reports` | GET | reports.html | 报告列表 |
| `GET /reports/<date>` | GET | report_detail.html | 报告详情 |
| `GET /reading-list` | GET | reading_list.html | 阅读清单 |
| `GET /login` | GET | login.html | 管理登录 |

---

## 认证

未设置管理密码时，系统保持本地免登录兼容。设置管理密码后，`/settings`、`/tasks`、所有 `POST/PUT/DELETE` 写接口、设置读取接口、任务日志接口都需要登录。

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

### 一键执行

```
POST /api/run
```

需要登录。

| 参数 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | SSE 进度任务 ID |

依次执行：抓取 → 分析 → 生成报告

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

级联删除关联的 analysis 和 reading_list 记录。

### 重新生成深度阅读

```
POST /api/paper/<arxiv_id>/reanalyze
```

下载 PDF，生成/刷新 Q&A 深度阅读；不会覆盖已有标签、评级、中文摘要和简评。

### 添加指定论文

```
POST /api/paper/add
```

Body (JSON)：
```json
{
    "input": "2603.18336",
    "task_id": "add_123"
}
```

支持 arXiv ID、PDF 链接、摘要页面链接。自动获取论文信息，先做基础分析，再补充 Q&A 深度阅读。

### 批量操作

```
POST /api/papers/batch-delete    # 批量删除
POST /api/papers/batch-hide      # 批量隐藏
POST /api/papers/batch-analyze   # 批量分析（基础模式）
```

Body (JSON)：
```json
{
    "arxiv_ids": ["2603.18336", "2603.12345"]
}
```

---

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

## 设置 API

### 供应商管理

```
GET  /api/providers              # 列出所有供应商
POST /api/providers              # 添加供应商
PUT  /api/providers/<key>        # 更新供应商
DELETE /api/providers/<key>      # 删除供应商
POST /api/providers/<key>/activate  # 切换供应商
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

供应商配置支持 `max_tokens_enabled`、`temperature_enabled`、`top_p_enabled`、`presence_penalty_enabled`、`frequency_penalty_enabled`、`is_thinking`、`thinking_effort` 等字段。未启用的参数不会发送给模型；思考模式下采样参数会被后端自动省略。

`GET /api/providers` 只返回 `api_key_masked`，不会返回完整 `api_key`。

### 测试连接

```
POST /api/test_connection        # 测试 AI API 连接
POST /api/detect_thinking        # 检测是否为思考模型
POST /api/test_proxy             # 测试 arXiv 代理连接
```

`POST /api/detect_thinking` 返回 `is_thinking`、`confidence`、`thinking_protocol`，并会把检测结果保存到当前激活供应商。

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
  "instruction": "...{tag_candidates}...{rating_criteria}..."
}
```

旧版 `system_prompt/user_prompt` 保存仍可用，会映射到 `deep_reading`。新版 Profile 中论文动态内容不写入 instruction，而是由后端作为最后一条 JSON message 传入；`basic_analysis` 可使用 `{tag_candidates}` 和 `{rating_criteria}`，`deep_reading` 只需要描述 Q&A 输出。

### 配置管理

```
POST /api/settings/concurrency   # 保存并发数（1-20）
POST /api/settings/per_page      # 保存每页数量（5-100）
GET  /api/settings/ai-tasks      # 获取 AI 功能模型路由
POST /api/settings/ai-tasks      # 保存 AI 功能模型路由
GET  /api/settings/ai-usage      # 获取近期 LLM token 用量汇总和趋势
GET  /api/settings/schedule      # 获取每日定时任务配置
POST /api/settings/schedule      # 保存每日定时任务配置并重建 APScheduler job
GET  /api/settings/proxy         # 获取代理配置
POST /api/settings/proxy         # 保存代理配置
GET  /api/settings/fetch         # 获取抓取配置
POST /api/settings/fetch         # 保存抓取配置
```

`/api/settings/ai-usage` 查询参数：

- `days`：统计天数，默认 `7`，范围 `1-365`
- `group_by`：趋势图分组方式，`task` 或 `model`，默认 `task`

返回中 `items` 保留按任务/模型的明细汇总；`dates`、`groups`、`totals` 用于账单页趋势图。

`POST /api/settings/schedule` Body：

```json
{
    "enabled": true,
    "hour": 8,
    "minute": 0
}
```

保存后会立即重建 APScheduler 中的每日任务。未设置 `data/settings.json.schedule` 时，首次默认值来自 `config.py` 的 `SCHEDULE_HOUR/SCHEDULE_MINUTE`。

`GET/POST /api/settings/ai-tasks` 的任务 key 固定为：

- `basic_analysis`：批量/自动基础分析，建议廉价模型
- `deep_reading`：单篇 Q&A 深度阅读，默认可启用 high 思考，不覆盖基础分析字段
- `report_summary`：报告 AI 导读，只在生成报告时显式启用

每个任务支持独立的 `provider_key`、`model`、`is_thinking`、`thinking_effort`、`max_tokens_enabled/max_tokens`、`temperature/top_p/presence_penalty/frequency_penalty` 及其启用开关。

### 数据库信息

```
GET /api/db/info
```

返回：论文总数、已分析数、标签种类、分类数、数据库大小、日期范围等。

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

返回每个任务类型的执行次数、成功率、平均耗时、最后执行时间。

### 任务日志

```
GET /api/tasks/logs
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `task` | string | 任务类型筛选 |
| `page` | int | 页码 |

### 定时任务

```
GET /api/tasks/scheduled
```

返回当前配置和实际注册到 APScheduler 的 job：

```json
{
    "enabled": true,
    "hour": 8,
    "minute": 0,
    "jobs": [
        {"id": "daily_pipeline", "name": "daily_pipeline", "next_run_time": "..."}
    ]
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
