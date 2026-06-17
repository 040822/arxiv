# 用户使用手册

## 目录

- [安装部署](#安装部署)
- [配置 AI 供应商](#配置-ai-供应商)
- [配置代理](#配置-代理)
- [页面功能说明](#页面功能说明)
- [常见操作](#常见操作)
- [定时任务配置](#定时任务配置)
- [常见问题 FAQ](#常见问题-faq)

---

## 安装部署

### 方式 A — conda

```bash
conda create -n arxiv python=3.11 -y
conda activate arxiv
pip install -r requirements.txt
```

### 方式 B — uv（推荐）

```bash
pip install uv
uv venv
uv pip install -r requirements.txt
source .venv/bin/activate
```

### 方式 C — venv + pip

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 依赖列表

| 包 | 用途 |
|----|------|
| `arxiv>=2.1.0` | arXiv API 客户端 |
| `openai>=1.0.0` | OpenAI 兼容 API SDK |
| `flask>=3.0.0` | Web 框架 |
| `apscheduler>=3.10.0` | 定时任务调度 |
| `requests>=2.31.0` | HTTP 客户端 |
| `PyMuPDF>=1.24.0` | PDF 文本提取 |

### 启动

```bash
# 启动 Web 服务（含定时任务）
python app.py

# 或使用 CLI
python main.py          # 完整流程
python main.py fetch    # 仅抓取
python main.py analyze  # 仅分析
python main.py generate # 仅生成报告
```

浏览器访问 `http://localhost:5000`

---

## 配置 AI 供应商

### 通过 Web 设置页配置

1. 访问 `/settings` → AI 设置 tab
2. 点击预设供应商卡片（如 DeepSeek）或「自定义」
3. 填写 API Key、Base URL，点击「刷新模型」自动获取模型列表，或手动输入模型名称
4. 点击「保存供应商」
5. 点击「🔗 测试连接」验证

默认不会发送 Max Tokens 限制；只有勾选「限制输出长度」后才会把该参数传给模型。Temperature 默认开启，Top P、Presence Penalty、Frequency Penalty 默认关闭，可在高级采样参数中按需启用。

### 支持的预设供应商

| 供应商 | Base URL | 推荐模型 |
|--------|----------|----------|
| DeepSeek | `https://api.deepseek.com` | deepseek-chat, deepseek-reasoner |
| 小米 Token Plan | `https://token-plan-cn.xiaomimimo.com/v1` | MiMo-7B-RL |
| SiliconFlow | `https://api.siliconflow.cn/v1` | DeepSeek-V3, Qwen2.5-72B |
| 智谱 AI | `https://open.bigmodel.cn/api/paas/v4` | glm-4-plus |
| Moonshot | `https://api.moonshot.cn/v1` | moonshot-v1-128k |
| OpenAI | `https://api.openai.com/v1` | gpt-4o |
| 自定义 | 任意 OpenAI 兼容接口 | 任意模型 |

### 思考模型

如果你的模型支持思考/推理模式（如 DeepSeek-R1、MiMo-7B-RL）：

1. 编辑供应商，勾选「🧠 思考模型」
2. 选择思考强度：自动、低、中、高、最高
3. 或点击「🧠 检测是否为思考模型」自动检测并保存结果
4. 启用后后端会按供应商协议传递思考参数，例如 OpenAI 的 `reasoning_effort`、DeepSeek 的 `thinking`、Qwen/MiMo 的 `enable_thinking`/`thinking_budget`

思考模式下，系统会自动省略 `temperature`、`top_p`、`presence_penalty`、`frequency_penalty` 等采样参数。

---

## 配置代理

如果服务器需要代理才能访问 arXiv：

1. 访问 `/settings` → 代理设置
2. 勾选「启用代理」
3. 填写 HTTP 和 HTTPS 代理地址（如 `http://127.0.0.1:7890`）
4. 点击「保存」
5. 点击「🔗 测试 arXiv 连接」验证

代理配置会应用于 arXiv API 请求和 PDF 下载。

---

## 页面功能说明

### 📅 每日论文（首页）

- 默认显示数据库中最新一天的论文
- 日期选择器可切换到任意日期
- 支持按标签筛选、每页数量调整
- 论文卡片显示：标题、作者、评级、标签、摘要、中文摘要
- 操作：📄 arXiv / 📥 PDF / 📖 详情 / 📌 加入清单 / 😐 不感兴趣

### 📂 分类浏览

- 左侧筛选面板：日期、分类、评级范围、AI 报告、深度分析、可见性、标签
- 批量操作：全选、批量隐藏、批量分析、批量删除
- 支持页码跳转和每页数量调整

### 🔍 搜索

- 关键词搜索：标题、摘要、标签、中文摘要、Q&A 内容
- arXiv ID 搜索：输入 `2603.18336` 或完整链接

### 📖 论文详情

- 完整论文信息：元数据、作者、分类标签
- 原始摘要 → 中文摘要 → AI 深度阅读（Q&A）→ 评价
- 编辑功能：评级、标签、摘要翻译、评价、Q&A
- 「📝 生成报告」：下载 PDF 生成/刷新 Q&A 深度阅读

### 📋 任务管理

- **抓取论文**：选择分类、数量、天数或指定日期
- **AI 分析**：设置数量上限，实时进度条
- **生成报告**：可指定日期
- **一键执行**：抓取→分析→生成报告
- **添加指定论文**：输入 arXiv ID 或链接
- **定时任务**：显示执行时间和状态
- **任务统计**：各任务的执行次数、成功率、平均耗时
- **执行日志**：可筛选、分页、清理

### 📊 每日报告

- 按日期列出所有报告
- 点击查看详情：统计概览、个性化推荐、分类分布、热门标签、全部论文列表；个性化推荐区先展示研究兴趣，再在卡片中展示中文摘要、推荐语和评价
- 在报告详情页点击「重新生成该日报告」可按当前配置覆盖刷新该日期报告

### 📌 阅读清单

- 添加论文到待读清单
- 标记已读/未读，已读论文自动移到下方并加删除线
- 筛选：全部/未读/已读

### ⚙️ 设置

- **AI 设置**：供应商管理、测试连接、并发数、每页数量、Prompt 编辑、Q&A 问题管理
- **数据库**：统计信息、存储路径、WebDAV 云同步备份
- **管理**：密码保护

---

## 常见操作

### 抓取论文

1. 进入「任务管理」页面
2. 选择分类（默认 cs.RO）
3. 选择抓取方式：
   - **按数量**：填「最大数量」，留空天数和日期
   - **按天数**：填「抓取天数」（如 30 表示最近 30 天）
   - **按日期**：选择「指定日期」精确抓取某天
4. 点击「📥 开始抓取」

> 已抓取过的论文会自动跳过，不会重复。

### AI 分析

- **基础分析**（批量）：在任务管理页点击「🤖 开始分析」，只用摘要生成标签、翻译、AI 评级和简评；星级可在详情页手动修正
- **深度阅读**（单篇）：在论文详情页点击「📝 生成报告」，下载 PDF 生成 Q&A 深度阅读，不覆盖已有标签、AI 评级、摘要和简评

### 批量操作

在「分类浏览」页面：
1. 勾选论文（或点击「全选」）
2. 点击操作按钮：👁️ 批量隐藏 / 🤖 批量分析 / 🗑️ 批量删除

### 添加指定论文

在「任务管理」页面的「添加指定论文」区域，输入：
- arXiv 编号：`2603.18336`
- PDF 链接：`https://arxiv.org/pdf/2603.18336`
- 摘要页面：`https://arxiv.org/abs/2603.18336`

系统会自动获取论文信息，先做基础分析，再补充 Q&A 深度阅读。

---

## 定时任务配置

进入「任务管理 → 定时任务设置」，可启用/停用每日任务并保存执行时间。配置会写入 `data/settings.json`；`config.py` 中的 `SCHEDULE_HOUR/SCHEDULE_MINUTE` 只作为首次默认值。

如在「设置 → 数据库 → WebDAV 云同步备份」启用自动备份，每日任务会在报告生成后同步一次。备份失败只会记录错误，不会中断抓取、分析和日报生成。

## WebDAV 云同步备份

进入「设置 → 数据库」，填写 WebDAV 地址、用户名、密码或应用密码、远端目录和历史保留天数。点击「立即备份」可以手动测试并上传一次；打开「启用每日自动 WebDAV 备份」后，每日定时任务会自动同步。

远端会保存 `arxiv-backup-latest.zip` 和按时间命名的 `arxiv-backup-YYYYMMDD-HHMMSS.zip`。历史备份默认保留 3 天，可根据 WebDAV 空间大小调整。

备份包包含 `papers.db` 一致性快照、`data/settings.json` 和 `output/` 报告目录。`settings.json` 内含 API Key、管理密码哈希和 session secret，请只同步到可信 WebDAV 空间。

每日任务执行内容：
1. 抓取近 3 日 cs.RO 论文（防止周末无论文）
2. 分析所有未分析的论文
3. 生成最新日期的 Web 报告

---

## 常见问题 FAQ

### Q: 为什么抓取不到论文？

A: 可能原因：
- 网络问题：检查代理配置，测试 arXiv 连接
- 分类配置：确认 `config.py` 中 `ARXIV_CATEGORIES` 包含目标分类
- arXiv 无更新：周末/节假日可能没有新论文

### Q: PDF 下载失败怎么办？

A: 
- 检查网络和代理配置
- arXiv 临时封禁：等待 10-30 分钟后重试
- 扫描版 PDF：无法提取文本，系统会自动降级使用摘要

### Q: 如何修改 AI 分析的 Prompt？

A: 进入「设置」→「Prompt 设置」，编辑 system/user prompt。可用变量：
- `{title}` — 论文标题
- `{authors}` — 作者
- `{abstract}` — 摘要或全文
- `{tag_candidates}` — 标签候选列表

### Q: 如何添加新的 arXiv 分类？

A: 编辑 `config.py`，在 `ARXIV_CATEGORIES` 列表中添加：
```python
ARXIV_CATEGORIES = [
    "cs.RO",   # Robotics
    "cs.AI",   # Artificial Intelligence
    "cs.CV",   # Computer Vision
]
```

### Q: 数据库在哪里？如何备份？

A: 数据库文件在 `data/papers.db`。推荐在「设置 → 数据库」使用 WebDAV 云同步备份；手动备份也可以复制该文件：
```bash
cp data/papers.db data/papers.db.bak
```

### Q: 如何清理旧数据？

A: 在「任务管理」页面点击「🗑️ 清理旧日志」，可清理 30 天前的任务日志。论文数据需手动操作数据库。
