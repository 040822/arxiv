# 4.2 数据库层重构实施记录

> 状态：已完成（2026-07-24）
> 范围：项目检查报告 Q4–Q8

## 目标与决策

本次重构保持 SQLite 与现有 `get_connection()` 调用接口兼容，同时解决连接
泄漏、短时锁竞争、分析重复记录及无版本迁移的问题。

- 使用兼容式托管连接；`with` 正常退出提交，异常退出回滚，两种路径均关闭。
- 每条连接启用 WAL、外键和 5000 ms `busy_timeout`。
- 使用 `schema_migrations` 记录连续版本；每个版本独立事务执行。
- 有待执行迁移时，先用 SQLite online backup 创建一致性快照；最近保留 3 份。
- 快照、版本校验或迁移失败均中止启动，不继续运行在未知 schema 上。
- 历史 `analysis` 重复记录在 v2 中合并后建立唯一索引。

## 实现

### 连接生命周期

`source/storage/connection.py` 返回 `sqlite3.Connection` 的兼容子类，因此旧代码
仍可直接调用；生产调用点已全部迁移为 `with get_connection() as conn:`，业务层
不再手动 `commit()` 或 `close()`。

### 迁移系统

`source/storage/schema.py` 保留 baseline schema，公开 `init_db()` 委托给
`source/storage/migrations.py`：

| 版本 | 名称 | 内容 |
|---|---|---|
| 1 | `baseline` | 应用现有基线 schema，并登记旧数据库 |
| 2 | `analysis_unique` | 合并重复分析，创建 `uq_analysis_paper_id` |

迁移器拒绝未来版本和版本断层。已有非空数据库执行迁移前，会在
`data/migration_backups/` 创建 `<数据库名>-v旧-to-v新-时间.db`；新建空数据库
无需快照。

### 重复分析合并规则

同一 `paper_id` 的最早记录作为主记录。主记录为空的字段从后续记录按时间顺序
补齐；双方均非空且值冲突时保留主记录并写 warning。其余重复行删除后创建唯一
索引。运行时插入使用 `INSERT OR IGNORE`，部分更新会先原子确保记录存在，再
执行字段白名单更新。

### 快照复用

一致性复制集中在 `source/storage/snapshot.py`。schema 迁移与 WebDAV 备份复用
同一 online-backup 实现，失败时清理不完整目标文件。

## 验证

- 173 项 `unittest` 全部通过。
- 覆盖提交/回滚/关闭、PRAGMA、并发插入与部分更新、短时写锁等待、重复数据
  合并、唯一索引、迁移幂等、版本异常、快照失败、迁移回滚及三份快照保留。
- 在 `/tmp` 中复制真实 57 MB `data/papers.db` 演练迁移：12132 条分析记录迁移
  后无重复，v1/v2 记录完整，唯一索引存在。
- 演练未修改真实数据库；真实实例会在下一次应用启动时先快照再迁移。

## 非目标

- 不增加 HTTP 迁移状态接口。
- 不引入 PostgreSQL、ORM 或分布式锁。
- 不处理检查报告 Q10 及其他 4.3 之后的问题。
