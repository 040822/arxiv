# 项目全面检查报告（2026-08-26）

> 检查日期：2026-08-26
> 检查范围：安全性、设置与存储层、抓取/AI 分析/流水线/邮件/备份、Benchmark 深模块、**前端显示效果**、**用户使用便捷性（论文阅读宗旨）**、**代码规范与风格**、文档一致性
> 检查方式：七条线并行深度代码审阅（storage/benchmark 全量精读 + 运行时验证），关键结论已在源码逐条交叉核实；测试套件全量回归
> **与前次报告关系：** [project-review-2026-07-10.md](project-review-2026-07-10.md) 中仍未完成的条目（S8、S9 部分项、S10、S13 部分项）已并入本报告对应章节，编号保留原文。
>
> **修订记录（v1.1，外部复审勘误）：** 依复审意见逐条源码复核后更正——① PIPE-11 机制描述写反（`transport.py:128` 仅在 `security == "starttls"` 分支调用 starttls，`none` 时静默明文发送、并不报错），降为低危并改写；② BM-5 前提有误（作者/裁判实际使用 `json.loads(_clean_json_content(_extract_first_json_object(...)))` 平衡花括号抽取，并非"裸 json.loads"），保留其成立的"裁判失败静默计零、无告警"部分；③ PIPE-1 触发条件收窄（下载侧已有 `%PDF-` 校验，非 PDF 内容不会落盘），降为中危并移出 P0；④ PIPE-13 收敛为良性不一致；⑤ UX-P05 更正（报告 404 为中文，仅论文 404 为英文）；⑥ 行号/路径/特异性计数笔误（SEC-M2、FE-H1、PIPE-19、UX-P14）；⑦ FE-L4 补充"未经逐项实测"标注。其余结论经复核与源码吻合。

---

## 目录

- [一、总体评价与问题统计](#一总体评价与问题统计)
- [二、测试基线](#二测试基线)
- [三、高优先级问题速览](#三高优先级问题速览p0)
- [四、安全与认证（SEC）](#四安全与认证sec)
- [五、设置与存储层（SET）](#五设置与存储层set)
- [六、抓取 / AI 分析 / 流水线 / 邮件 / 备份（PIPE）](#六抓取--ai-分析--流水线--邮件--备份pipe)
- [七、Benchmark 深模块（BM）](#七benchmark-深模块bm)
- [八、前端显示效果（FE）](#八前端显示效果fe)
- [九、用户使用便捷性（UX）](#九用户使用便捷性ux)
- [十、代码规范与风格（CS）](#十代码规范与风格cs)
- [十一、文档漂移与遗留项](#十一文档漂移与遗留项)
- [十二、确认良好的方面](#十二确认良好的方面)
- [十三、修复路线图](#十三修复路线图)

---

## 一、总体评价与问题统计

项目整体工程质量高于同类自研系统平均水准：模块边界遵守 AGENTS.md 约定、fail-closed 安全策略、版本化会话撤销、迁移快照体系、三层配置守卫、双运行器测试基建均属少见规范实现。当前正处于 **v7 Benchmark 快速迭代后的技术债沉淀期**，问题集中在三类：

1. **写入原子性与异常回退语义**（settings.json 非原子写 + 读失败静默回退默认值，是唯一可能导致不可逆数据丢失的路径）
2. **v7 新引入的逻辑 bug**（交流轨错位污染评测结果、冻结状态可被打回、预算异常被误判为可重试）
3. **前端"静默失败"与新页面样式被公共规则压制**（直接对应用户反馈的显示问题）

### 问题统计

| 维度 | 高 | 中 | 低 | 小计 |
|------|----|----|----|------|
| 四、安全与认证 SEC | 0 | 3 | 10 | 13 |
| 五、设置与存储层 SET | 3 | 7 | 8 | 18 |
| 六、抓取/分析/流水线等 PIPE | 0 | 11 | 13 | 24 |
| 七、Benchmark BM | 1 | 6 | 10 | 17 |
| 八、前端显示 FE | 1 | 4 | 5 | 10 |
| 九、用户体验 UX | 5 | 12 | 9 | 26 |
| 十、代码规范 CS | — | 3（违反既定规范） | 若干卫生项 | — |

---

## 二、测试基线

```
.venv/bin/python -m pytest tests/ -q   # 383 passed, 1560 subtests passed（39.6s）
python scripts/run_all_tests.py --quick
```

- ✅ 全量测试通过。`tests/test_static_css_contract.py`（685 subtests）守住"模板类名必须在所加载 CSS 中有定义"的底线——因此本报告发现的前端问题全部是该合同**覆盖不到**的类别（CSS 特异性冲突、JS 动态拼类名、`hidden` 属性 vs display 冲突等），建议顺带把"规则必须可生效"纳入合同。
- ⚠️ 运维注意：`scripts/run_all_tests.py` 使用 `sys.executable`，系统 Python 无 pytest 时会以 "No module named pytest" 失败；须用 `.venv/bin/python` 或先激活虚拟环境。

---

## 三、高优先级问题速览（P0）

按"不可逆损害 × 触发概率"排序，均为已源码验证：

| # | 问题 | 位置 | 一句话说明 |
|---|------|------|-----------|
| 1 | settings.json 非原子写入 | `source/settings/store.py:319-320` | 直接 `open(path,"w")` 截断写，崩溃/断电产生损坏 JSON；而正确的 `strict_io.write_settings_atomic()` 已存在但主路径未复用 |
| 2 | load_settings 失败静默回退默认值 | `source/settings/store.py:288-290` | 文件损坏 → 任一 `save_*` 函数把默认模板整体写回真实文件 → API Key/session secret 被覆盖（与 #1 叠加构成"配置丢失放大器"） |
| 3 | 设置读改写全程无锁 | `source/settings/store.py:229-324` 等 | 定时线程 `update_email_report_status` 与 Web 请求并发互相覆盖 |
| 4 | 交流轨轮次错位污染评测结果 | `source/benchmark/__init__.py:740-753` + `runner.py:53-69` | 某轮瞬时失败后后续轮回答错位的题目并以 ok 状态永久固化、按错误 rubric 评分——网络抖动即触发，评测结果静默失真 |
| 5 | 批量分析覆盖用户手评 | `source/analysis/batch.py:76-87` | 冲突分支无条件 `update_analysis(rating=AI值)`，违背"AI 初评 + 用户手动修正"的产品语义 |
| 6 | benchmark 路由控件布局被压制 | `templates/benchmark.html:39-77` + `components.css:524-544` | `.form-group select{width:100%}` 特异性压过新页面紧凑样式，路由表单 3 组 × 约 7 行全宽堆叠——**最可能就是本次反馈的显示问题根因之一** |
| 7 | 详情页编辑失败零反馈 | `templates/paper.html:382-461` | 评分/标签/摘要保存失败时什么都不做，破坏"修正 AI 结果服务后续筛选"核心循环 |
| 8 | 会话 cookie 缺 Secure 标志 | `source/web/application.py:34-41` | 单行修复；HTTPS 部署下防明文截获 |
| 9 | 登录 next 参数开放重定向绕过 | `source/web/auth.py:155-158` | `/\evil.com` 未拒绝，浏览器 `\`→`/` 规范化后跳外域钓鱼 |

---

## 四、安全与认证（SEC）

未发现高危问题。安全架构成熟度高，文档声明（不信任 XFF、SSE 隔离、密码脱敏）经逐条验证属实。

### 中危

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| SEC-M1 | `source/web/application.py:34-41` | 会话 cookie 配置了 HttpOnly/SameSite=Lax 但缺 `SESSION_COOKIE_SECURE=True`；30 天滑动会话若经任何 http 明文入口可被截获重放 | config.update 加一行 |
| SEC-M2 | `source/imports/__init__.py:29-38`（重定向循环 `:44` 起） | SSRF DNS rebinding TOCTOU：`validate_public_http_url` 校验解析结果后，`requests.get` 再次独立解析，短 TTL 域名可在两步间切换到内网 IP。导入预览为 member 级端点 | 校验后将连接固定到已验证 IP（自定 Host/SNI 或 transport 层固定 resolver） |
| SEC-M3 | `source/web/auth.py:155-158` + `login.html:42,73` | 开放重定向：`_safe_next_url` 拒绝非 `/` 与 `//` 开头，但不拒绝 `/\evil.com`（浏览器规范化后跳外域）；`enforce_request_policy` 自动把 full_path 塞入 next 降低了构造门槛 | 同时拒绝含 `\` 与控制字符的 next，或校验 `urlparse(next).netloc == ""` |

### 低危

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| SEC-L1 | `settings_api.py:158-161`、`runtime.py:301-320` | `GET /api/settings/proxy` 对 `http://user:pass@host` 形态代理 URL 明文回显凭据，与 WebDAV/SMTP 的脱敏策略不一致 | userinfo 部分脱敏 |
| SEC-L2 | `providers_api.py:168`、`settings_api.py` 多处 | 异常 `str(e)` 直通响应，可能泄露内部 base_url 等 | 推广 `_safe_server_error` 范式（见 CS 部分） |
| SEC-L3 | `providers_api.py:105-136,183-206` | admin 级 SSRF 面：请求体任意 base_url 由服务器发起请求（admin 本可配供应商，属知情接受） | 可选限制 https 公网地址 |
| SEC-L4 | `auth.py:74-79,169-175` | 内存限流重启清零、多 worker 不共享（已如实声明）；反代场景所有流量共用同一 remote_addr 桶，攻击者故意失败 5 次可使全站登录冷却 15 分钟 | 多 worker 用共享存储；提供可信代理层开关 |
| SEC-L5 | `users.py:65-68` | 兼容接受无盐 SHA-256 旧哈希（登录时自动升级 scrypt）；升级前 DB 泄露可快速离线破解 | 批量强制重置或设迁移截止期 |
| SEC-L6 | `auth.py:205-210` | CSRF token 登录前后不轮换（Flask 整体重签 cookie 使可利用性极低） | 登录/改密后重新生成 |
| SEC-L7 | `auth.py:95-97,322-325` | 登出仅清客户端 cookie，无服务端失效（签名 cookie 架构固有取舍） | 可选登出时递增 session_version |
| SEC-L8 | `auth.py:78-79` | 端点策略匹配去蓝图前缀，跨蓝图同名函数共享策略（当前无冲突，脆弱设计；fail-closed 兜底） | 完整端点名匹配 |
| SEC-L9 | `auth.py:328-343` | 改密接口无节流（scrypt 成本部分缓解） | 加失败计数节流 |
| SEC-L10 | `auth.py:262-271` | query string 构造未 urlencode（Jinja 转义阻断注入，仅功能性缺陷） | 用 `urllib.parse.urlencode` |

### 确认良好的机制（抽样）

fail-closed 路由策略与四道鉴权闸顺序正确；CSRF 全覆盖含登录 POST 本身且 `compare_digest` 防时序；session_version 版本化会话撤销完整；scrypt + 不存在用户 dummy 运算均衡时序；存储层统一剥离 password_hash 后返回；唯一 admin 保护（API 无法删除/降级/停用）；审计日志脱敏过滤；限流确证不信任 X-Forwarded-For；SSE `(user_id, task_id)` 复合键隔离无 IDOR；导入 SSRF 校验覆盖环回/私网/link-local/CGNAT 且重定向逐跳复验限 6 跳；上传文件名不落盘 + realpath 锁死 data/ 目录。

---

## 五、设置与存储层（SET）

SQL 注入面干净（全部参数化 + LIKE 通配符转义正确）；迁移系统（flock 双序列化、版本连续性双向校验、TOCTOU 二次核对、foreign_key_check 后置）设计成熟；删除保护事务边界正确。主要风险集中在 **settings.json 写入路径**。

### 高危

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| SET-H1 | `store.py:319-320` | 主保存路径直接 `open(SETTINGS_PATH,"w")` 截断写，崩溃/断电/磁盘满产生损坏 JSON；`strict_io.write_settings_atomic()`（tempfile+fsync+os.replace）已存在但未复用 | 改用原子写 |
| SET-H2 | `store.py:288-290` | `load_settings()` 任何异常返回 `DEFAULT_SETTINGS`（api_key 空、secret 空）。危险链条：文件损坏 → 任一 `save_*` 内部先 load 得到默认值 → 整体写回 → 用户配置被覆盖。guardrail 只能事后告警。对比 `get_session_secret()` 已刻意 fail-closed | 读失败 raise 或只读降级；禁止在加载失败状态下执行 save |
| SET-H3 | `store.py:229-324`、`runtime.py` 全部 `save_*` | 读-改-写三步无任何锁；定时调度线程与 Web 请求并发互相覆盖丢更新 | 模块级 `threading.Lock`；配合 H1 原子写后窗口收敛 |

### 中危

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| SET-M1 | `migrations.py:92-123`、`snapshot.py:8-26` | 迁移快照/WebDAV 包内 `*.db` 含密码哈希与私有数据但未 chmod 0600，依赖 umask | 落盘后立即 0600、目录 0700 |
| SET-M2 | `benchmark_migrations.py:148` | 唯一约束含可空 `round_index`，SQLite NULL≠NULL，deep_reading 行约束失效（实测确认可插两行）；当前靠应用层补偿查询兜底 | partial unique index `WHERE round_index IS NULL` 或哨兵值 0 |
| SET-M3 | `operations.py:33` vs `:300-301` | `task_logs.started_at` 写本地时间，清理却与 SQLite UTC 比较；UTC−x 时区提前删日志；`:242-267` 中断时长用本地串喂 `julianday()` 系统性偏差。项目内 UTC/本地两套并存 | 统一存 UTC |
| SET-M4 | `papers.py:162-164` | `insert_paper` 吞掉全部 IntegrityError 当"重复"，CHECK 违规/非法 source_type 的手动导入论文无声消失 | 区分异常类型，CHECK 违规记 warning 上抛 |
| SET-M5 | `papers.py:740-744` 等 | `paper_key OR arxiv_id` 双匹配理论上可命中两行，fetchone 取行不定；409 影响统计可能与实际删除的不是同一篇 | 先解析为唯一 id 再操作 |
| SET-M6 | `papers.py:686-707,783-804` | 未保护的 `delete_paper`/`batch_delete_papers` 仍公开导出（生产全走 *_protected），属等待误用的 API 面；批量占位符 2×N 不分片（旧 SQLite <999 变量报错） | 删除/私有化未保护函数；批量分片 ≤500 |
| SET-M7 | `learning.py:29-33` | `add_to_reading_list` 裸 `except Exception: return False` 把锁超时/磁盘错误伪装成"已存在" | 只捕 IntegrityError，其余记日志上抛 |

### 低危

| 编号 | 位置 | 问题 |
|------|------|------|
| SET-L1 | `connection.py:47-49` | 每次取连接执行 3 条 PRAGMA；WAL 为持久属性无需重复设（首次切换需独占锁）；busy_timeout 与 connect(timeout=5) 冗余 |
| SET-L2 | `papers.py:230-231,333-335` | 标签筛选 LIKE 未转义 `%`/`_`（search_papers:616 已转义，标准不一）；JSON 子串匹配有语义误报（"MoE" 命中 "MoePose"）。≈旧报告 S9 未完成项 |
| SET-L3 | `user_migration.py:34-56` | v4 引导密码仅存进程内存，同批迁移失败重启后永久丢失（有 reset 脚本兜底） |
| SET-L4 | `users.py:82` | 不存在用户实时生成 dummy scrypt（约百毫秒 CPU），有限流缓解仍属放大点 |
| SET-L5 | `users.py:250-251` | `int(limit)` 非数字抛 ValueError → 500 |
| SET-L6 | `analysis.py:52-57` | insert_analysis 下标访问必填键，缺键 KeyError 而非可读错误 |
| SET-L7 | 多文件头 | 死导入/重复 logger 定义（复制粘贴痕迹） |
| SET-L8 | `store.py:101-112` | `_deep_merge` 语义经确认符合文档承诺（list 整体替换、类型变更覆盖、未知键保留），仅记录无需行动 |

---

## 六、抓取 / AI 分析 / 流水线 / 邮件 / 备份（PIPE）

邮件 HTML 注入防护完备（12 处插值点全部 `_esc()` 后进 f-string，顺序正确）；SQLite 快照 ro URI + online backup 一致性正确；流水线步骤状态机自洽；ContextVar 用量归属正确。

### 中危

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| PIPE-1 | `documents/__init__.py:288,344-349` | PDF 坏缓存风险（勘误：下载侧已有 `%PDF-` 魔数校验，非 PDF 载荷不会落盘，"永远毒化"表述已收敛）：但写入终态路径仍非原子且命中只查存在性零校验——并发交错写或写盘中断可残留坏文件，此后永远命中、提取失败**静默回退摘要**、无告警无自愈。触发概率低但后果难排查（对照同文件 `store_uploaded_pdf:194-208` 已有 temp+fsync+replace 正确范式） | tmp 文件 + os.replace 原子落位；命中加最小大小/魔数复验；提取失败删除坏缓存 |
| PIPE-2 | `ingestion/__init__.py:159-162` | 结果乱序时一条旧记录触发 `break` 丢弃其后窗口内论文；`reached_old` 赋值后从不读取（死变量） | 改 continue + 连续 N 条早于 start_date 才 break，记 warning |
| PIPE-3 | `ingestion/__init__.py:36-55` | `_apply_proxy()` 修改进程级环境变量，多线程下竞态走错出口（对比 `documents/_get_proxy_dict()` 已用显式 proxies 的正确做法） | 弃 env 方案改显式代理参数 |
| PIPE-4 | `analysis/client.py:30-37`、`core.py:174-200` | OpenAI client 每次调用新建不关闭（批量分析 FD/半开连接堆积）；全链路无显式 timeout（依赖 SDK 默认 600s） | 按 (provider,model,params) 缓存单例；暴露 timeout/max_retries 配置 |
| PIPE-5 | `analysis/json_support.py:60`、`core.py:184-194` | JSON 提取贪婪匹配"首个 `{` 到末个 `}`"，模型附加解释文字必解析失败；`_call_ai` 对 JSONDecodeError 无重试，该篇判死 | `raw_decode` 定位首对象；短任务失败重试 1 次 |
| PIPE-6 | `analysis/batch.py:76-87` | 冲突分支无条件 update_analysis 覆盖用户手评 rating（违背产品语义，见 P0 速览 #5） | 比对是否等于上次 AI 结果/引入 user_edited 标记，只刷新未被用户修改的字段 |
| PIPE-7 | `analysis/papers.py:169-179` | 深读续写字符串直接拼接，缝合点易非法 JSON；二次失败后整次长上下文调用费用作废 | 拼接前去重叠片段、修剪到合法 JSON 边界 |
| PIPE-8 | `pipeline/orchestrator.py:145-175` | 抓取重试循环持 pipeline_lock 长 sleep（最坏 20×10min≈3.3h）：手动 /api/run 全程 409、无法响应停止信号、占用 APScheduler 公共池 worker | 分段睡眠 ≤30s 检查停止事件；或交给独立一次性 retry job |
| PIPE-9 | `pipeline/scheduler.py:27-39` | 多 worker 误配 gunicorn 会每天双跑日报（pipeline_lock 进程内无效，无 flock 强制防护）；`misfire_grace_time=60` 过短，进程忙超 60s 该天日报静默缺失 | scheduler.start() 前 flock 非阻塞抢锁；grace 提升至 ≥3600 |
| PIPE-10 | `documents/__init__.py:352-354` | download_pdf 吞掉所有异常 return None，404/429/超时不可辨，"多少论文因限流没有全文"不可观测 | 结构化错误类别传播 |
| PIPE-12 | `backups/__init__.py:220-223` | 两次上传非原子：历史包成功而 latest.zip 失败时远端 latest 落后；异常路径不清理孤儿历史包 | 先传 latest 再传历史包，或失败补偿删除 |

### 低危

| 编号 | 位置 | 问题 |
|------|------|------|
| PIPE-11 | `reports/email/config.py:36-39` + `transport.py:127-129` | （勘误：`transport` 仅在 `security == "starttls"` 分支调用 starttls，`none` 时静默明文发送、**并不报错**，此前"必然发送失败"描述有误，故降为低危。）实际问题是 normalize 白名单接受 `"none"` 即静默明文传信，用户无任何提示与确认。保留 none 时应在设置页警示明文传输风险，或不提供该选项 |
| PIPE-13 | `ingestion/__init__.py:145-147,469-471` | 老 ID 版本剥离行为不一致（无 "." 的 pre-2007 老 ID 不剥 vN 后缀）。勘误收敛：仅在老 ID 进入抓取窗口时才可能引发重复入库，当前监控分类（cs.AI/cs.RO 等现代分类）下不可达，属良性不一致 |
| PIPE-14 | `ingestion/__init__.py:171` | 逐条 paper_exists 每次新建连接，大窗口回填放大开销 |
| PIPE-15 | `ingestion/__init__.py:431-433,513-515` | 单篇查询吞掉一切异常，"不存在"与"429 限流"对上层相同 |
| PIPE-16 | `analysis/papers.py:109-113` | 深读全文无上限直进 prompt，超大 PDF 必然超上下文白费一次失败调用 |
| PIPE-17 | `analysis/core.py:31-32` | 用量记录失败仅 debug 级，统计长期失效不可发现 |
| PIPE-18 | `orchestrator.py:341-347` | 异常收口路径自身可能抛错掩盖根因；步骤行不存在时 error 步骤状态丢失 |
| PIPE-19 | `tasks_api.py:175-238` vs `source/pipeline/manual.py:22-109` | `/api/run` 与 `run_manual_pipeline` 双份编排已开始漂移（后者零调用方） |
| PIPE-20 | `tasks_api.py:122`、`storage/papers.py:548-555` | limit/days 参数未钳制，`limit=-1` 变无限分析 |
| PIPE-21 | `documents/__init__.py:379-388` | extract_text_from_pdf 的 doc.close() 不在 finally，页遍历异常泄漏句柄（fitz 支持上下文管理器） |
| PIPE-22 | `documents/__init__.py:301-308` | arXiv 分支 allow_redirects=True 绕过受控重定向校验（纵深防御缺口，现实风险低） |
| PIPE-23 | `backups/__init__.py:141,164-177` | MKCOL 把 301/302 当成功掩盖 base url 配置错误；PROPFIND 仅识别 DAV: 命名空间，网关非标时清理静默失效 |
| PIPE-24 | `transport.py:30-77` | CONNECT 代理隧道总体稳健；小瑕疵：HTTP/2 风格状态行不识别、http 代理 Basic 凭据明文（协议固有，建议文档提示优先 https 代理） |

---

## 七、Benchmark 深模块（BM）

AGENTS.md 七条关键约定逐条验证：冻结守卫/复用键四元组/裁判优先序/幻觉封顶 60/单 worker+202/interrupted 收口/Web 层不碰 SQL 均**基本符合**（例外见下）。领域异常层次（BenchmarkError/BudgetExceeded/RetryFailed）设计规范。

### 高危

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| BM-1 | `__init__.py:740-753` + `runner.py:53-69` | 交流轨某轮瞬时失败存为 retry_failed 后循环继续；重建 history 跳过失败轮导致长度缩短，`_chat_messages` 以 `questions[len(history)]` 取"当前问题"——**下一轮实际回答的是上一轮的问题**，且以 ok 状态永久保存、resume 不再重跑、按错误 rubric 评分。网络抖动即触发的评测结果静默污染 | 当前轮 retry_failed 时 break 该 (candidate,paper,repeat) 后续轮次；或遇前置失败不调用模型 |

### 中危

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| BM-2 | `__init__.py:262-263` + `storage/benchmark.py:260-277` | 出题任务结束时无条件置回 `status="review"`：长耗时出题期间完成冻结会被打回可编辑状态，违反冻结不可变约定；`add_benchmark_case` 亦无冻结守卫 | 条件更新 `WHERE status!='frozen'`；写入前重读状态；add 层加断言 |
| BM-3 | `_ai.py:105-125` + `runner.py:72-80` | `before_attempt()`（扣预算）在重试循环 try 块内执行，`BudgetExceeded` 落入宽 except；max_calls 恰为 500/503/429 等值时消息中数字被 `is_retryable_error` 正则误判为可重试，预算耗尽被包装成一批 retry_failed | except 最前面显式 `except BudgetExceeded: raise`；收紧数字正则 |
| BM-4 | `__init__.py:607,631` | resume 白名单包含 running：Web 路径退化为空翻排队；同步门面混用时 `set_run_status(queued)` 绕过 claim 的 WHERE 防线，可能双线程并发执行同一 run | 白名单移除 running；僵死运行走显式管理接口 |
| BM-5 | `judge.py:94-97,268-270`、`author.py:31-34` | （勘误：解析链实为 `json.loads(_clean_json_content(_extract_first_json_object(...)))`，平衡花括号抽取已较稳健，并非"裸 json.loads"；仅双层转义信封场景未覆盖——`json_support.loads_first_json:46` 具备该兜底能力却未被使用。）真正的问题是：裁判组调用/解析异常被 `except Exception` 吞掉后 `return 0`，整组无 primary 判定而 scoring revision 照常 completed，报告无 warning 区分"裁判失败"与"未被抽样"，质量劣化完全静默 | 解析改用 loads_first_json 兜底转义信封；裁判组失败向报告 warnings 注入显式告警或令 revision 进入 warning 状态 |
| BM-6 | `judge.py:537-602`、`report.py:59-64` | 人工判定绑定单一 scoring revision；rejudge 切换 active 后此前人工覆盖/校准样本全部从报告消失，冲突题重回 needs_human_review，与"人工优先级最高"精神相悖 | 切换 active 时克隆 human 判定到新 revision，或聚合时跨 revision 并集读取 |

### 低危

| 编号 | 位置 | 问题 |
|------|------|------|
| BM-7 | `__init__.py:530-546` | create_run 与 add_candidate 两步独立事务，中途崩溃遗留缺候选的 queued 运行，resume 后以不完整候选集静默执行 |
| BM-8 | `__init__.py:91-101` | `_next_version_label` 读-写竞态，并发 create_draft 撞 UNIQUE 返回 500 |
| BM-9 | `benchmark_migrations.py:33` | `retired` 状态声明了但不可达；且 `_require_not_frozen` 只判 `=="frozen"`，未来引入 retired 将被当可编辑 |
| BM-10 | `benchmark_migrations.py:48-49` | suite_papers.paper_id 无 FK 无置 NULL，业务论文删除后用量日志溯源字段悬挂 |
| BM-11 | `storage/benchmark.py:726-743` | 每次插入响应全表扫本 run 所有响应抬计数器（SUM 兜底扫过 MB 级大文本列），O(N²) I/O；正常路径 consume_candidate_call 已精确计数 |
| BM-12 | `__init__.py:452-468,672,789-802` | `_load_run` 全量加载含 prompt_snapshot 大字段，单次执行加载 3 遍；GET runs/report 端点同样全量序列化 |
| BM-13 | `__init__.py:735-737` 等 | N+1 零散：每保存一条响应查一次 run 只为读计数器（已有 count_run_calls 未用）；幂等检查逐 case 一查询 |
| BM-14 | `benchmark_api.py:517-531` + `progress.py:39-45` | SSE 可能永不终止：worker 重启丢 _TASKS 或终态被懒清理后 get_progress 永 None，循环不 break 不超时 |
| BM-15 | `benchmark_api.py:170-189` | create_draft 在 HTTP 请求线程内同步下载/提取多篇 PDF，分钟级阻塞易触发网关超时，与其余长任务 202 模式不一致 |
| BM-16 | `report.py:100,300-302` | 0 分候选与 null 同权排序垫底；`max_tokens_enabled=False` 仍按存储值比较可能误报非等预算；`_resolve_case_score` 死代码 |
| BM-17 | `judge.py:145-153,525-552` | 裁判返回条件被改名时逐条件查不到全部计 0（位置兜底仅在 rubric 为空时启用）；set_human_judgment 不校验 chat 轮次对应且允许给 retry_failed 打分 |

---

## 八、前端显示效果（FE）

> 回应用户反馈："之前一次大规模功能修改导致部分前端的显示效果有点问题"。最近两次大改动为 benchmark 管理页新增（cb8a1fa）与账号体验改进（4e82527）。以下前两项分别源于这两次改动，**最可能是反馈所指**。

### 高危

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| FE-H1 | `benchmark.html:39-77` + `pages/benchmark.css:14-21` vs `components.css:524-544` | benchmark 路由配置区 compact 样式（`.route-effort-control{width:auto}` 等单类选择器，特异性 0,1,0）被 components.css 的 `.form-group select{width:100%}`（0,1,1）、`.form-group input[type="number"]`（0,2,1）、`.form-group label{display:block}`（0,1,1）**级联压制**：每组路由的 5 个控件各独占一行全宽拉伸 + label 块级换行，设计中的紧凑单行变约 7 行堆叠 ×3 组。CSS 合同测试只验证类"存在"、不验证"赢下级联"故漏网 | 提高作用域特异性（`.form-group .route-effort-control` 等）或改用不嵌套 `.form-group` 的容器（参照候选行的做法） |

### 中危

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| FE-M1 | `settings.html:463` + `components.css:342-369` | 设置页「管理」tab 的"一次性临时密码"警告框 `<div hidden>` 依赖 UA 样式，但 `.action-status.warning{display:block}` 作者样式必然胜出 → admin 进设置页**常驻一个空的琥珀色横条**（账号体验改动的直接回归；core.css:122 对同类问题已有 `[hidden]` 补丁先例） | 改 is-hidden 类切换或补 `#member-onetime-password[hidden]{display:none}` |
| FE-M2 | `index.html:163,168,266-277` | 摘要展开按钮 onclick 用模板字面量注入原文 `` `{{ abstract \| e }}` ``：`\| e` 只做 HTML 实体转义，属性解析后还原原始字符再交给 JS 编译——摘要含反引号/`${`（arXiv LaTeX 宏常见写法）即 SyntaxError，**这些卡片展开按钮完全无反应** | 全文放 data-fulltext 属性由 DOM 取回，或沿用 paper.html 的 tojson 方案 |
| FE-M3 | `components.css:233-239`、`pages/tasks.css:14-18` | `.pagination`（5 组控件）与 `.add-paper-row` 无 flex-wrap 无断点适配，≤768px 整页横向滚动；新增 benchmark 表格反而做了 overflow-x 适配，两个高频旧组件落下 | flex-wrap:wrap + 小屏整行折行 |
| FE-M4 | `benchmark.html:233-235,1080` | `statusClass()` 回退产出的 `.status-unknown` 无样式定义（裸文本胶囊）；动态创建的 `.benchmark-condition-score` 在 CSS 完全未定义（合同测试不扫 JS 动态类名） | 补两条样式定义 |

### 低危

| 编号 | 位置 | 问题 |
|------|------|------|
| FE-L1 | 多处死样式（确认全仓库零引用） | `components.css:590-603` help-text 族；`paper.css:41-47,236-242` detail-top-row/detail-links；`settings.css` task-stat-* 一族约 27 行（最大死块）、active-badge、thinking-badge、provider-name、usage-dot、log-header 等；`benchmark.css:150-162` status-review/status-retired（STATUS_CLASSES 永不产出）。注：reports.css 的 report-* 由 renderer.py 服务端生成存活；katex-* 由 KaTeX 运行时生成存活 |
| FE-L2 | `benchmark.html:165` vs `components.css:395-404` | 进度条标题/计数配色规则绑定 tasks 页的 `#progress-title/#progress-count` ID，benchmark 用自己的 ID 故配色失效（功能正常） |
| FE-L3 | `tasks.html:451-506` | 手动导入重构遗留死函数 `addPaper()` 引用已不存在的 `#paper-input`，建议删除 |
| FE-L4 | 可访问性（静态走查判断，未逐项实测渲染效果） | 多处输入 `outline:none` 击穿全局 focus-visible 焦点环（library/tasks/settings/benchmark css）；benchmark 路由区 label 无 for 关联；`.section-desc(#888)` 等对比度疑似低于 AA |
| FE-L5 | 一致性观察 | benchmark 进度容器无面板底色（ID 选择器无法复用）；`setStatus` kind 空时尾随空格类名；settings.css 与 components.css 各有一套 stat-success/error 颜色不一致（页面互斥加载暂未冲突，潜在地雷） |

---

## 九、用户使用便捷性（UX）

> 对照项目宗旨"更好地帮助用户阅读论文"审查。正面确认：首页卡片信息密度好（标签/评级/中文摘要/评价齐备）、搜索多关键词 AND+权重+命中片段高亮、学习页 typing 气泡/spinner/防重复提交/token 透明化用心。体验债集中在**静默失败、隐式截断/状态不同步、宗旨功能最后一公里缺失**三类。

### 高影响

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| UX-P07 | `paper.html:382-461` | 详情页评分/标签/摘要/QA 编辑失败时 `result.status!=='ok'` 分支**什么都不做**（403/CSRF 过期/网络错误均无提示），星级点不动、保存无反应 | 统一失败 toast；403 提示"成员仅可修改评分和标签" |
| UX-P17 | `learning_api.py:72` | 聊天模型上下文仅最近 **12 条**，界面却展示最多 200 条完整历史且零告知——深聊后引用早期内容模型答非所问，用户感知为"AI 变笨"；user-guide 也未披露 | 输入框旁常驻轻提示；超限后在聊天顶部插分隔线"更早消息不参与模型上下文" |
| UX-P10 | `index.html`/`browse.html` 卡片 | 个性化推荐分（0-100，花 LLM 成本计算的核心差异化信号）在每日刷论文的主战场**完全不露出**，排序也只有 submittedDate | 卡片 meta 加推荐分徽标；支持按推荐分排序 |
| UX-P13 | `components.css:158-161` | `.paper-links` 不换行，首页卡片最多 5 个链接在 375px 窄屏必然溢出视口（手机最高频操作行） | `flex-wrap:wrap; row-gap:6px` |
| UX-P01 | `index.html:193`、`browse.html:264` | 「加入清单」初始态从不回显（仅详情页查 todo/status）：已加入仍显示"加入"，连点两次变成 add→remove 误删 | 列表渲染批量注入 in_list 标志，统一详情页范式 |

### 中影响

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| UX-P02 | `paper_chat.html` | 学习页（精读主场景）无加入清单/标记入口，须退回详情页操作，回路断裂 | hero 区加清单/已读按钮 |
| UX-P03 | `index.html:259-264` | 首页日期选择器重建 URL 时**静默丢弃 tag/min_rating 筛选**（底部分页却保留），行为不一致 | 基于 URLSearchParams 改写而非重建 |
| UX-P04 | `tasks.html:172-213` | 抓取/生成报告完成后是死胡同反馈（无"查看新论文/查看报告"链接；手动导入反而做了正确示范） | 完成消息附跳转链接 |
| UX-P05 | `pages.py:134,154`（论文）、`pages.py:354`（报告） | 论文详情/学习页 404 是英文裸文本 "Paper not found"，报告 404 为中文裸文本"报告不存在"——均是无导航的死胡同（旧邮件/日报失效链接直达），无法回首页或搜索近似标题 | 全局 404 模板（站头+搜索框+回首页）+ 中文文案 |
| UX-P14 | `static/css/pages/library.css:207-217` | 浏览页移动端筛选面板全宽置顶，8 个下拉堆叠把列表顶出首屏 | `<details>` 折叠 + 显示激活筛选数 |
| UX-P18 | `paper_chat.html:84-94` | 苏格拉底历史会话恢复入口放在 quiz tab 的 chips 里，苏格拉底 tab 自己找不到续读入口 | socratic tab 过滤渲染 mode==='socratic' 的会话 |
| UX-P19 | `paper_chat.html:88-92` | 练习 chip 显示英文原码（quick3 · 3题）不含时间，同模式多次练习长得一样；新建会话后列表不刷新 | 中文模式名 + 相对时间；前端 prepend |
| UX-P20 | `renderQuizSession()` | 练习无整体进度/得分汇总（无"已答 3/6、均分 3.7"），做完不知道掌握度 | quiz 顶部加进度徽章 |
| UX-P22 | papers_api/tasks_api 等几乎所有 except | 英文技术错误直透用户（`❌ 分析失败: Connection error.`），中文用户无从自救 | 后端映射常见异常→中文话术，未知异常通用文案+日志（与 SEC-L2/CS 同源，一并修） |
| UX-P24 | 三种页面三种行为 | 加入清单：首页/浏览 toggle 但初始态错（P-01）、详情页先查再切（正确范式）、搜索页干脆没有 | 统一为详情页范式 |
| UX-P26 | search.html/reports.html | 导航栏缺账号区（未引 auth.js），访客在搜索页想登录得绕回首页 | 统一导航组件 |

### 低影响

| 编号 | 位置 | 问题 |
|------|------|------|
| UX-P08 | `paper_chat.html:374-447` | submitQuizAnswer/replySocratic 无 finally，会话过期弹窗被取消后输入框永久冻结需刷新 |
| UX-P09 | `tasks.html:183-194,326-337` | SSE 先订阅后 POST，POST 失败时进度框悬挂、ES 空转 |
| UX-P11 | `index.html:200-204` | 所选日期无论文也提示"请到论文处理页抓取"（误导，访客还会被 /tasks 弹登录）；应区分空库与筛选无结果 |
| UX-P12 | pages.py:79 vs index.html | min_rating 只有 URL 支持，首页无 UI 入口（隐藏功能） |
| UX-P15 | 全站 | 除 Ctrl/Cmd+Enter 外无快捷键；详情页返回链接非 history.back()，从搜索进入会丢筛选上下文 |
| UX-P16 | — | 列表渲染量可控（分页 ≤100），无性能问题（正面确认） |
| UX-P21 | 学习页 | 讨论记录不可清除/删除，200 条截断静默发生 |
| UX-P23 | 各模板 | alert/confirm 与行内 status 混用，风格割裂 |
| UX-P25 | paper.html | QA 保存后整页 reload（丢滚动位置）、摘要就地替换、星级即时更新——三个编辑器三种反馈 |

### 宣传与实现差异（docs/user-guide.md）

- 「😐 不感兴趣」宣称人人可用，实为 admin 门控可见，未注明角色限制；
- 已读论文"自动移到下方"实为样式变灰+删除线，需刷新才重排；
- 学习页"历史自动保存"属实，但 12 条上下文窗口未披露（同 UX-P17）。

---

## 十、代码规范与风格（CS）

整体健康度 **6.6/10（B−）**：架构面明显强于细节面，失分点是 v7 快速迭代沉淀的机械性卫生问题而非设计问题。

### 违反已文档化规范（应优先修）

| 编号 | 位置 | 问题 |
|------|------|------|
| CS-1 | `source/config.py:48` + AGENTS.md §5.1 | `ANALYSIS_CONCURRENCY` 已无任何 import（死常量），AGENTS.md 仍将其列为有效配置——文档漂移 |
| CS-2 | `storage/__init__.py:13,17` | 未保护的 `delete_paper`/`batch_delete_papers` 仍在公共接口再导出，与 §3/v4 删除保护语义冲突（生产全走 *_protected，见 SET-M6） |
| CS-3 | papers_api/settings_api/tasks_api/task_endpoint | Web 错误处理两种体制并存：多数 blueprint `return str(e),500`（泄漏内部细节且不记日志），benchmark_api 的 `_safe_server_error`（记日志+稳定文案）才是正确范式——应推广为 app 级 errorhandler |

### 主要发现

1. **命名**（7/10）：集合查询两代命名并存（老 `get_*` 返回列表 vs 新 `list_*`），应向 list_ 收敛；storage/benchmark.py 8 个 "Compatibility alias" 与正名混排。
2. **死代码**（5/10，最需清理）：`reports/email/content.py:25 _rewrite_relative_links`、`benchmark/json_support.py:46 loads_first_json`（讽刺：裁判正需要它，见 BM-5）、`benchmark/report.py:10 _resolve_case_score`、`config.py BENCHMARK_ROUTE_LABELS`、`storage/benchmark.py:386 edit_benchmark_case`（绕过审计快照，留着是陷阱）、`ingestion reached_old`、`defaults.py logger 连续定义两次`；5 个生产零调用别名 + 2 个仅测试引用别名无删除时间表。
3. **重复代码**（5/10）：runtime.py 三对孪生函数（webdav/email 的 get/save/update 逻辑逐行复制）应提取 `_mask_secret`/`_merge_secret_save`；JSON 容错解析至少 4 处独立实现（两个同名不同实现的 `_clean_json_content`）；`batch.py:60-65 与 149-154` 一模一样的 future.result 异常块；分页约定不一（硬编码 30 vs 钳位 1–100）。
4. **异常处理**（6/10）：205 个 except 中宽捕获 118 个、仅 20% 记日志；裸 except 为 0（好）；`benchmark/_ai.py:131-150` 对纯属性访问连包 4 层 `except Exception: pass` 属过度防御。
5. **日志**（8/10）：0 print 残留、100% 英文（好）；f-string 急切插值 54 处与 lazy %s 并存应统一。
6. **类型注解/docstring**（5/10）：返回值注解约 0%，唯 task_endpoint.py 100% 注解形成孤岛；docstring 覆盖 67% 且两极分化——web/auth.py 21 个公共函数缺 20 个、storage/users.py 15 缺 12（鉴权/账号核心反成空白）；docstring 语言中英混杂应定方向。
7. **魔法数字**（6/10）：task_logs 状态字符串以 SQL 字面量散落 operations.py ≥5 处，缺 `TASK_LOG_STATUSES` 常量表（对比 benchmark 已有常量表并写入前校验）；PDF 回退截断 50000、学习历史 12、per_page 30、sha256 截断 [:12] 等散落。
8. **文件组织**（7/10）：`storage/benchmark.py`(1469) 可按聚合拆六块；`benchmark/__init__.py`(956) 职责越界最明显——包 __init__ 里塞内存任务队列+编排+facade 三角色，队列应独立 tasks.py；settings 子模块互相整表 re-import 形成网状 shim（pyflakes 60+ unused import）。
9. **测试风格**（9/10）：51 类 100% unittest.TestCase，双运行器风格统一，共享桩组织良好（好）。
10. **依赖健康**（8/10）：requirements 九项与 import 全对应；**唯一缺口：werkzeug 被直接导入（users.py:10、user_migration.py:7）却未声明**，靠 flask 传递依赖存活。

---

## 十一、文档漂移与遗留项

| 项 | 说明 |
|----|------|
| AGENTS.md §5.1 | `ANALYSIS_CONCURRENCY` 已死但仍列为有效配置（CS-1） |
| docs/user-guide.md | 三处宣传与实现差异（见 §九末） |
| requirements.txt | 补 werkzeug |
| static/vendor/katex.min.js | 仍无版本声明（旧报告 S13 部分完成项；marked 已带 v4.3.0 头） |
| templates/report_detail.html:47 | `{{ content \| safe }}` 仍是 Python 端统一 escape + safe 旧模式，新增字段忘转义即存储型 XSS（旧报告 S10，未改） |
| storage/operations.py:162 | `query.replace("SELECT *", "SELECT COUNT(*)...")` 脆弱 count 构造（旧报告 S8，未改） |
| papers.py:230,334 | 标签 LIKE 未转义（旧报告 S9 残留 = SET-L2） |
| AGENTS.md §7.1 第 5 步 | 本次审查报告已按惯例放入 docs/plan/，如认为 docs/ 根目录更合适可移动 |

---

## 十二、确认良好的方面

- **安全**：fail-closed 路由策略、版本化会话撤销、存储层剥离密码哈希、审计脱敏、CSRF 含登录自身、SSRF 校验完备、SQL 注入面干净、富文本白名单 sanitizer、SSE 用户隔离——文档安全声明逐条验证属实
- **架构**：模块边界遵守 AGENTS.md；benchmark 领域异常层次规范；迁移系统（flock+版本双向校验+快照+foreign_key_check）成熟；删除保护 BEGIN IMMEDIATE 事务边界正确
- **可靠性设计**：三层 settings 守卫、WebDAV 一致性快照、流水线步骤状态机、ContextVar 用量归属、邮件 HTML 全量转义
- **工程纪律**：0 print、0 裸 except、日志全英文、测试 100% unittest.TestCase 双运行器兼容、pytest-randomly 防顺序耦合、CSS 合同测试 685 subtests
- **产品**：搜索权重+高亮、学习页交互细节（typing 气泡/防重/token 透明化）、浏览页 active-filter chips 同类工具少见

---

## 十三、修复路线图

### P0 —— 立即（本周；多为小改动，消除不可逆风险与用户可感知缺陷）

1. **SET-H1/H2/H3**：save_settings 改用 write_settings_atomic；load 失败 fail-closed；模块级读写锁（基础设施 strict_io 已就绪，成本最低收益最大）
2. **BM-1**：交流轨当前轮失败即 break，杜绝错位固化
3. **PIPE-6**：批量分析冲突分支不得覆盖用户手评
4. **FE-H1 + FE-M1**：benchmark 路由控件特异性修复；设置页空警告框——两次大改动的显示回归就此收敛（回应用户反馈的最小修复集）
5. **SEC-M1 + SEC-M3**：cookie Secure、next 反斜杠拒绝（各一行）
6. **FE-M2**：首页摘要展开按钮 data 属性方案（数据相关静默失效）

### P1 —— 短期（两周内）

1. **BM-2/BM-3/BM-5**：冻结条件更新、BudgetExceeded 显式重抛、裁判解析兜底转义信封 + 裁判组失败告警
2. **PIPE-1/2/8/9**：PDF 缓存原子写+坏缓存自愈（勘误后降级但仍建议尽早，修复成本极低）、翻页终止条件、重试分段睡眠、scheduler flock+grace≥3600
3. **UX-P07/P22**（与 CS-3 合并）：app 级 errorhandler + 前端失败 toast + 中文错误映射
4. **UX-P10/P13/P01**：推荐分上卡片、paper-links 换行、清单态回显
5. **SET-M1/M3/M4/M7**：备份权限 0600、时区统一 UTC、insert_paper 异常分类、reading_list 收窄捕获
6. **CS 清理批**：删除 §10.2 死函数/别名、TASK_LOG_STATUSES 常量表、requirements 补 werkzeug、更新 AGENTS.md §5.1
7. **UX-P17**：聊天 12 条窗口的用户告知

### P2 —— 择机（下次迭代顺手）

- 其余 BM/PIPE/SET/FE/UX 低危项；runtime.py 孪生函数提取；benchmark/__init__ 任务队列拆分；storage/benchmark.py 拆分与 O(N²) 计数优化（BM-11/12）；命名向 list_ 收敛；404 页面、移动端折叠筛选、练习进度徽章等体验打磨
- CSS 合同测试增强：纳入"规则必须赢下级联"与 JS 动态类扫描
- 旧报告 S8/S10 收尾（count SQL 显式构造、report 渲染改 Jinja 或清洗库）

---

*报告生成方式：七条并行深度审查线（安全 / 存储与设置 / 数据管道 / Benchmark / 前端显示 / 用户体验 / 代码规范），关键结论经源码逐条复核；测试基线 383 passed。各编号（SEC/SET/PIPE/BM/FE/UX/CS）可直接用于 issue 标题与提交信息。*
