# AI 论文数据库 — 文档中心

自动从 arXiv 抓取 AI/机器人领域论文，调用 AI 做基础分析（标签、AI 评级、中文翻译、简评）和按需深度阅读（Q&A），存入 SQLite 数据库，通过 Flask Web 界面浏览。论文评级由 AI 初评，用户可手动修正。

## 📖 文档导航

| 文档 | 说明 | 适用人群 |
|------|------|----------|
| [用户使用手册](user-guide.md) | 安装部署、配置、页面功能、常见操作、FAQ | 用户、运维 |
| [systemd 服务管理](systemd-service.md) | systemctl 托管、开机自启、日志查看、常见排查 | 运维 |
| [开发者指南](developer-guide.md) | 项目结构、技术栈、开发规范、数据库设计 | 开发者 |
| [API 接口文档](api-reference.md) | 页面、任务、论文、设置、认证等接口的参数、响应、示例 | 前端/后端开发者 |
| [AI Agent 开发指南](agent-guide.md) | 快速理解项目、常见修改场景、已知坑 | AI Agent |
| [项目架构说明](architecture.md) | 系统架构、数据流、组件依赖、设计决策 | 架构师、新开发者 |
| [更新日志](changelog.md) | 版本历史 | 所有人 |

### 规划文档

| 文档 | 说明 |
|------|------|
| [私有论文阅读 Benchmark 计划](plan/paper-reading-benchmark-2026-08-04.md) | 论文阅读/交流模型评测的题库、评分、成本、版本和实施方案 |

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API（启动后通过 Web 设置页）
# 3. 启动服务
python app.py

# 访问 http://localhost:5000
```

详细步骤请参阅 [用户使用手册](user-guide.md)。
长期运行和开机自启请参阅 [systemd 服务管理](systemd-service.md)。

## 📁 项目结构

```text
arxiv/
├── app.py                  # 唯一 Web 入口
├── source/analysis/     # AI 分析与论文学习
├── source/ingestion/    # arXiv 摄取
├── source/documents/    # PDF 文档操作
├── source/backups/      # WebDAV 备份
├── source/reports/email/# 日报邮件
├── source/storage/      # SQLite 存储
├── source/web/          # Flask Web 层
├── templates/             # Jinja2 页面
├── static/css/           # 模块化业务样式
└── docs/                  # 本目录
```
