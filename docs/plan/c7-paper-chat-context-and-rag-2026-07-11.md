# C7：论文对话上下文、文本缓存与 RAG 技术方案

> 方案日期：2026-07-11  
> 来源：`docs/plan/project-review-2026-07-10.md` 第 3.2 节 C7  
> 状态：仅规划，尚未实施

## 1. 背景与问题边界

当前论文自由讨论链路为：

```text
POST /api/paper/<arxiv_id>/chat/messages
  → get_paper_chat_messages(limit=12)
  → analyzer.chat_about_paper()
  → get_learning_paper_text()
      → 复用已下载 PDF
      → 每轮重新 extract_text_from_pdf()
  → build_paper_learning_messages()
      → system → 稳定任务说明 → 整篇论文 → 最近 12 条消息 → 当前问题
  → paper_chat 模型
```

现有 PDF 文件缓存避免了重复下载，但仍有三个独立问题：

1. **重复解析 PDF**：每轮对话都会再次用 PyMuPDF 提取同一文件。
2. **长对话丢失上下文**：只保留最近 12 条消息，较早的研究目标、纠错和未决问题会静默消失。
3. **超长论文成本与上下文压力**：普通论文每轮仍发送整篇文本；prompt cache 可能降低部分计费和延迟，但不能消除请求体、上下文占用及超过模型窗口的风险。

本方案只处理单篇论文自由讨论。以下内容不在本轮范围：

- C15 跨论文记忆或全局用户画像；
- 修改主动问答、苏格拉底追问的业务行为；
- PDF OCR；
- 一开始就引入向量数据库或强制依赖 embedding 模型；
- 删除原始聊天消息。

## 2. 目标与成功标准

### 2.1 目标

- 同一 PDF 未变化时只做一次文本提取。
- 对话超过短窗口后，旧内容以可追溯的滚动摘要继续参与推理。
- 普通论文保持全文上下文和 prompt cache 优势；只有超过预算的论文切换到检索式上下文。
- 调用方不需要了解缓存文件、摘要游标、分块算法或检索降级规则。
- 任一增强步骤失败时仍能回退到当前可用行为，不阻断对话。

### 2.2 可验收指标

| 指标 | 目标 |
|---|---|
| 同一 PDF 第二次读取 | 不再调用 `extract_text_from_pdf()` |
| 文本缓存命中延迟 | 相比首次提取显著下降，记录到响应 `meta` |
| 长对话上下文 | 最近消息 + 已覆盖旧消息的摘要，无重复、无消息空洞 |
| 超长论文请求上下文 | 始终不超过配置预算，且返回命中的分块编号/字符数 |
| 故障降级 | 缓存损坏、摘要失败、检索低置信度均能继续回答 |
| 数据安全 | 日志不记录论文全文、对话正文或摘要正文 |

## 3. 核心设计决策

### 3.1 建立一个深模块，而不是在调用方叠加条件

新增 `paper_learning_context.py`，把文本缓存、历史压缩、上下文预算和检索放在同一个深模块中。外部 seam 保持小接口：

```python
def prepare_chat_context(paper_data, user_message) -> ChatContext:
    """返回本轮模型需要的论文上下文、摘要、近期消息和可观测元数据。"""

def advance_chat_summary(paper_id) -> SummaryUpdate:
    """在达到阈值时推进滚动摘要；失败可安全重试。"""
```

`ChatContext` 建议包含：

```python
@dataclass
class ChatContext:
    paper_context: str
    conversation_summary: str
    recent_messages: list[dict]
    mode: str                  # full_text / retrieval / abstract
    meta: dict                 # 缓存命中、分块数、字符数、摘要游标等
```

接口不暴露缓存路径、BM25 参数、摘要批大小或数据库 SQL。删除该模块时，这些复杂度会重新散落到 `app.py`、`analyzer.py`、`pdf_reader.py` 和 `database.py`，因此该模块具备足够深度。

`analyzer.chat_about_paper()` 仍负责模型调用和消息构造；`app.py` 只负责校验请求、保存成功的 user/assistant 消息并返回结果。后续若需要把整轮事务进一步收口，可另行评估，不在本轮同时重构。

### 3.2 普通论文全文模式，超预算才启用 RAG

不对所有论文无条件使用 RAG：全文能保留跨章节联系，也最适合当前稳定前缀的 prompt cache。上下文选择规则为：

```text
可用预算 = paper_chat 上下文预算
           - system/任务说明
           - 滚动摘要
           - 最近消息
           - 当前问题
           - 输出预留

全文估算大小 <= 可用预算  → full_text
全文估算大小 >  可用预算  → retrieval
PDF 文本不可用             → abstract
```

首版用保守字符数估算 token，并在供应商配置中预留 `context_window_tokens` 后再精确计算。不能仅依据 PDF 页数或文件大小判断。

### 3.3 首版检索不增加 embedding 依赖

首版采用“结构化分块 + 词法相关度 + 邻块扩展”：

- 按标题/段落边界分块，目标约 1,500–2,500 字符，重叠 200–300 字符；
- 保留 `chunk_id`、页码范围、章节标题、字符范围和正文；
- 使用 BM25 或等价的纯 Python 词法评分；
- 选取 Top-K 命中块，并补充每个命中块的前后相邻块；
- 固定加入摘要、引言开头、结论附近等 landmark 块；
- 检索置信度低时扩大 K，而不是返回空上下文；
- 最终按论文原始顺序拼接，并严格裁剪到预算。

论文通常为英文、用户可能使用中文，因此词法检索存在跨语言弱点。首版需要记录低置信度率和用户问题语言；只有数据证明不足时，再进入可选的 embedding 阶段。embedding 阶段必须新增独立任务路由和本地向量缓存，不能借用 Chat Completions 模型名称假设其支持 embeddings。

## 4. 分阶段实施方案

### 阶段 A：纯文本旁缓存

#### 文件与接口

扩展 `pdf_reader.py`：

```python
def get_cached_paper_text(pdf_url, arxiv_id) -> TextArtifact
```

建议缓存文件：

```text
data/pdf_cache/<safe_arxiv_id>.pdf
data/pdf_cache/<safe_arxiv_id>.txt
data/pdf_cache/<safe_arxiv_id>.text-meta.json
data/pdf_cache/<safe_arxiv_id>.chunks.json   # 阶段 C 才生成
```

`text-meta.json` 至少包含：

- PDF 文件大小、mtime 和内容哈希；
- 文本缓存哈希、字符数和页数；
- `extractor_version`，用于清理规则变化后失效；
- 生成时间。

#### 一致性规则

- 仅当 PDF 指纹和 `extractor_version` 均匹配时命中 `.txt`。
- 使用同目录临时文件 + `os.replace()` 原子写入，不能直接覆盖目标文件。
- 使用按 arXiv ID 的进程内锁，避免同进程并发重复提取；多进程下允许重复计算，但最终文件必须保持完整。
- `.txt` 或 metadata 损坏时删除/覆盖旁缓存并重新提取，PDF 本体不受影响。
- PDF 提取失败仍回退摘要，不写入“永久失败”的空文本缓存。

#### 调用调整

`get_learning_paper_text()` 改为通过该接口获取文本，不再直接调用 `extract_text_from_pdf()`。返回 meta 增加：

```json
{
  "used_pdf_cache": true,
  "used_text_cache": true,
  "text_cache_status": "hit",
  "paper_text_chars": 123456
}
```

阶段 A 不改变发送给模型的上下文内容，风险最低，可单独上线和回滚。

### 阶段 B：滚动对话摘要

#### 数据库

新增表：

```sql
CREATE TABLE paper_chat_summaries (
    paper_id INTEGER PRIMARY KEY,
    summary TEXT NOT NULL,
    summarized_through_message_id INTEGER NOT NULL DEFAULT 0,
    prompt_version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);
```

原始 `paper_chat_messages` 永久保留。摘要只是派生缓存，可删除后重建。

#### 摘要推进策略

- 保留最近 6 个完整 user/assistant turn，不进入摘要。
- 当未摘要且不在近期窗口内的消息达到 4 个 turn 时，合并到既有摘要。
- 摘要输入为“旧摘要 + 本批原始消息”，输出必须保留：
  - 用户研究目标、假设和偏好；
  - 已确认结论及对应论文依据；
  - 用户或模型曾被纠正的误解；
  - 尚未解决的问题和承诺的后续动作；
  - 关键术语与符号约定。
- 只有模型成功返回新摘要后，才原子更新 `summary` 和 `summarized_through_message_id`。
- 使用游标实现幂等；并发推进时以旧游标为条件更新，失败的一方重新读取即可。
- 摘要失败不阻断当前回复，回退为最近 12 条消息，并在 meta 标记 `summary_status=failed`。

#### 模型路由

新增 `paper_chat_summary` 任务和 Prompt Profile，允许使用更便宜的模型，输出上限建议 1,200–2,000 tokens。修改 `settings.py` 时必须同步：

- `AI_TASK_KEYS` 和显示名称；
- 默认任务配置与 Prompt Profile；
- `_migrate_old_settings()` 白名单及 `load_settings()` 显式合并；
- 设置页模型路由 UI、API 文档和测试。

#### 消息顺序

```text
system
稳定任务说明
稳定论文元数据/全文（full_text 模式）
滚动摘要
未被摘要覆盖的近期消息
当前问题
```

摘要和消息 ID 不得放进稳定论文前缀，避免破坏 prompt cache。

### 阶段 C：超长论文检索式上下文

#### 分块缓存

首次进入 retrieval 模式时，从 `.txt` 构建 `.chunks.json`。缓存键包含文本哈希和 `chunker_version`。分块时尽量保留页码与章节标题；为此阶段 A 的提取结果应同时保留页边界元数据，不能只留下不可追溯的拼接字符串。

#### 检索查询

查询由以下内容组成，但不得把完整历史直接用于检索：

- 当前用户问题；
- 最近一个 user turn；
- 滚动摘要中的“未决问题/术语”短字段。

结果去重、邻块扩展、landmark 补充后，按原文顺序拼接。每块使用明确边界：

```text
[chunk 17 | pages 6-7 | section: Method]
...
```

这些引用标记先用于调试和可观测性，不要求模型生成严格文献引用。

#### 稳定前缀调整

retrieval 模式下，稳定前缀只包含论文 ID、标题、作者、摘要等固定元数据；查询相关分块放在其后、历史之前。不能把每轮变化的 Top-K 分块伪装成稳定论文上下文。

#### 可观测元数据

响应 `meta` 增加：

```json
{
  "context_mode": "retrieval",
  "paper_text_chars": 420000,
  "selected_context_chars": 82000,
  "retrieved_chunk_ids": [2, 3, 17, 18, 41],
  "retrieval_score_max": 8.42,
  "retrieval_low_confidence": false,
  "summary_used": true,
  "summarized_through_message_id": 120
}
```

前端默认只展示 `context_mode`、缓存命中、选取分块数和摘要是否启用；详细分数保留给调试日志/API，不记录正文。

### 阶段 D：基于数据决定是否增加语义检索

仅在以下任一条件持续出现时推进：

- 中文问题对英文论文的低置信度率高；
- 用户反馈相关章节经常漏召回；
- 词法检索需要扩大到接近全文才能回答。

若推进，新增真正的 embedding seam：生产 adapter 调用配置的 embeddings 端点，测试 adapter 使用确定性本地向量。向量以 `text_hash + chunker_version + embedding_model` 为缓存键存 SQLite 或旁文件；不在首版引入独立向量数据库。

## 5. 代码改动清单

| 文件 | 计划改动 |
|---|---|
| `paper_learning_context.py` | 新深模块：预算选择、历史摘要装配、检索与降级 |
| `pdf_reader.py` | 文本/页边界旁缓存、原子写、失效检测、分块缓存 |
| `database.py` | `paper_chat_summaries` 迁移及摘要游标 CRUD |
| `analyzer.py` | 消费 `ChatContext`；增加摘要模型调用；保持稳定/动态消息顺序 |
| `app.py` | 去除固定 `limit=12` 决策，返回新增 meta；成功保存后触发摘要推进 |
| `settings.py` | `paper_chat_summary` 路由、上下文预算和迁移兼容 |
| `templates/settings.html` | 摘要任务模型路由与必要的预算配置 |
| `templates/paper_chat.html` | 展示全文/检索模式、文本缓存和摘要状态 |
| `tests/` | 缓存、摘要游标、预算、检索、降级和端到端消息顺序测试 |
| `AGENTS.md`、`docs/` | 更新数据流、表结构、设置/API 和运维说明 |

## 6. 测试策略

测试以深模块接口为主要测试面，不依赖真实网络或真实 LLM。

### 6.1 文本缓存

- 首次读取提取并原子生成旁缓存；
- 第二次读取不调用 PyMuPDF；
- PDF 指纹或 extractor 版本变化后重新提取；
- 空/损坏 metadata、`.txt` 半文件能自愈；
- 两个线程并发读取只执行一次提取；
- 提取失败回退摘要且不污染缓存。

### 6.2 滚动摘要

- 少于阈值时不调用摘要模型；
- 达到阈值后只总结游标之后、近期窗口之前的完整 turn；
- 旧摘要与新批次正确合并，游标单调前进；
- 摘要与近期消息不重复、消息 ID 无空洞；
- 模型失败和乐观并发冲突可安全降级/重试；
- 删除论文时摘要级联删除。

### 6.3 检索与预算

- 普通论文使用全文，超预算论文使用 retrieval；
- 明确关键词能召回目标块及邻块；
- 多块按原文顺序输出且不重复；
- landmark 块始终存在；
- 低置信度扩大候选但仍不超过预算；
- 最终消息估算值加输出预留不超过上下文预算；
- retrieval 模式的动态分块位于稳定前缀之后。

### 6.4 集成回归

- 对话 API 的保存语义保持不变：模型成功才保存 user/assistant；
- PDF/摘要降级仍能回答；
- `paper_chat` token 用量继续入账，摘要调用单独记为 `paper_chat_summary`；
- 现有 quiz/socratic 消息构造不受影响；
- 旧数据库和旧 `settings.json` 启动后自动迁移且不丢配置。

## 7. 发布、回滚与清理

建议每阶段独立提交和发布：

1. **A：文本缓存**——不改变模型输入，可直接灰度。
2. **B：滚动摘要**——先只记录摘要 meta，再启用摘要参与回答。
3. **C：RAG**——先仅对超过预算的论文启用，并保留强制全文调试开关。
4. **D：语义检索**——仅在指标证明需要时另立计划。

回滚原则：

- `.txt`、metadata、chunks 和摘要表均为派生数据，删除后可以重建；
- 禁用摘要/RAG 时原始聊天和 PDF 不受影响；
- 不允许通过回滚删除 `paper_chat_messages`；
- 运维文档需提供清理单篇/全部派生缓存的命令或管理入口。

## 8. 实施顺序与完成定义

建议拆成以下可独立验收的提交：

1. 文本缓存 artifact、失效规则及测试；
2. `get_learning_paper_text()` 接入文本缓存和 meta；
3. 摘要表、CRUD、任务路由及迁移测试；
4. 滚动摘要推进和上下文装配；
5. 页边界/分块 artifact 与纯本地检索；
6. 上下文预算选择、retrieval 降级和 meta；
7. 前端状态展示、文档和端到端回归。

C7 完成必须同时满足：文本缓存命中、长对话连续性、超长论文预算受控、失败可降级、全量测试通过。只增加 `.txt` 缓存或只把 12 条窗口调大，均不视为完成 C7。

## 9. 实施前待确认参数

以下参数不阻塞方案设计，但实施前应结合实际供应商与论文样本定标：

- `paper_chat` 上下文窗口和输出预留；
- 保留的近期 turn 数、摘要触发批次；
- 分块大小、重叠和 Top-K；
- 低置信度阈值；
- 是否在设置页暴露这些参数，还是首版先使用后端保守默认值。

建议首版只在设置页暴露“上下文预算”和“启用超长论文检索”两个选项，其余参数保留为代码常量，避免形成过大的配置接口。
