# 4.1 超大文件拆分实施记录

> 计划日期：2026-07-13  
> 完成日期：2026-07-24  
> 目标版本：v0.6.0  
> 状态：已完成

## 目标与结果

解决项目检查报告 Q1/Q2/Q3，同时保持 HTTP、SQLite schema、`settings.json`、CLI
和根模块公开导入兼容。

| 根模块 | 拆分前 | 拆分后 | 兼容职责 |
|---|---:|---:|---|
| `app.py` | 2695 行 | 14 行 | Flask `app`、`create_app` 和直接启动 |
| `database.py` | 2641 行 | 8 行 | storage/report 公开函数 re-export |
| `settings.py` | 1909 行 | 9 行 | runtime settings 公开函数 re-export |

真实 Flask 应用保留 86 条非静态路由；实施完成时 156 项测试全部通过。

## 最终模块

```text
source/
├── settings/   # defaults / normalize / store / providers / prompts / runtime
├── storage/    # connection / schema / papers / analysis / operations / reports / learning
├── reports/    # HTML report renderer
├── pipeline/   # daily/manual orchestrator + scheduler
└── web/        # application assembly + auth/pages/API Blueprints + progress
```

- `source.settings` 和 `source.storage` 通过显式 `__all__` 提供稳定 interface。
- 根 shim 保留生产导入兼容；测试 patch 新模块中符号的实际使用位置。
- Web 使用 7 个 Blueprint；URL 和 HTTP method 由
  `tests/fixtures/web_routes.json` 固化。
- `import app` 只组装应用，不启动 scheduler；`create_app()` 负责数据库初始化、
  遗留任务收口和 scheduler 单次启动。
- 手动和定时组合流水线共享私有互斥锁，冲突语义仍为 HTTP 409 / scheduled skipped。

## 实施提交

1. `740610d` — 拆分 runtime settings 模块。
2. `7b2662c` — 独立实现 Q9 通用 deep merge。
3. `1b4fbb0` — 拆分 SQLite storage 与 report renderer。
4. `f3594e5` — 抽取 pipeline orchestrator 与 scheduler。
5. `4cd2c1f` — 拆分 Flask Blueprints、application assembly 和手动 pipeline。

最终文档收口与 review 使用后续提交完成。

## 验收

```bash
python3 -m py_compile app.py database.py settings.py source/**/*.py
python3 -m unittest discover -s tests
python3 -c "import app, settings, database, analyzer, email_report, backup, fetcher, pdf_reader"
```

另外验证：

- 根模块均低于 100 行；
- `source/` 不反向导入根 `app/database/settings`；
- 86 条 URL + method 契约保持一致；
- 真实 Flask 下公开页、登录重定向和受保护 API 行为保持一致；
- 旧版与新版设置格式均保留未知字段，Q9 不再依赖两处顶层白名单。

## 不在本次范围

- Q4 数据库连接关闭改造。`sqlite3.Connection` 的 context manager 只负责
  commit/rollback，不负责 close；后续需设计真正关闭连接的 context manager 并逐调用点迁移。
- Q5/Q6/Q8 数据库约束、busy timeout 和 schema version。
- Q10–Q15、Q20/Q21 的完整后续重构。
- HTTP 响应、数据库 schema、配置 wire format 和业务规则调整。
