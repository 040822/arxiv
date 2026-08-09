# 4.1 剩余重构执行计划：根模块迁入、CSS 模块化与文档收口

> 状态：已完成（2026-08-09）
> 范围：仅记录 4.1 剩余工作；已完成历史保留在 Git 与 changelog 中。

## 已完成基线

| 已完成事项 | 提交 |
|---|---|
| 测试巨石拆分与统一测试入口 | `0a828df` 等 Q20 提交 |
| 删除 `main.py/settings.py/database.py` | `4f1d322` |
| `config.py` 迁入与 `app.py` 单入口收口 | `cab4cea` |
| 日期窗口抓取与 `/api/fetch` 契约收口 | `bb3c499`、`0f771d5` |

执行前基线：227 项测试全绿、86 条非静态路由。`TAG_CANDIDATES`、
`RATING_CRITERIA` 和 `ARXIV_CATEGORIES` 可配置化不属于本计划；本轮保持
HTTP 接口、数据库 schema、`settings.json` 和业务行为不变。

## Python 模块收口

最终根目录只保留 `app.py`，不保留兼容 shim。包通过 `__init__.py` 与显式
`__all__` 提供稳定接口，调用方只使用 `source.*` 路径。

1. 清理 Web Blueprint 遗留无用 import，固化路由与测试基线。
2. 建立 `source.documents`，迁入 PDF 校验、持久上传、缓存下载、删除与文本提取。
3. 建立 `source.ingestion`，迁入最近窗口、分批、指定日期、单篇查询与 ID 解析。
4. 将数据库文件信息迁入 `source.storage.info`；建立 `source.backups`，迁入快照打包、WebDAV 上传、清理与编排。
5. 建立 `source.reports.email`，把邮件 CSS 迁为包资源，拆分内容构建、SMTP/代理传输与发送编排。
6. 建立 `source.analysis`，按客户端与用量、消息构建、分析、学习、报告和批处理划分内部职责；内部 JSON 修复、响应归一化和续写逻辑不作为公共接口。
7. 删除五个旧根模块，收紧边界测试并禁止源码和测试导入旧模块。

## CSS 模块化

目标结构：

```text
static/css/
├── core.css
├── components.css
├── rich-text.css
└── pages/
    ├── library.css
    ├── paper.css
    ├── learning.css
    ├── tasks.css
    ├── settings.css
    ├── reports.css
    └── auth.css
```

所有业务页加载 `core.css`、`components.css`；图书馆页面加载
`pages/library.css`；论文详情与学习页额外加载 `rich-text.css` 和各自页面样式；
任务、设置、报告、登录加载对应页面样式。`promo.css` 与第三方资源不变。

先增加资源矩阵、CSS URL 与类名覆盖测试，再机械迁移规则并分批更新模板，最后
删除 `style.css`。随后统一按钮、状态、表单、Tab、空态和加载状态接口，清理固定
内联样式与重复选择器，并仅修复移动端溢出/堆叠、键盘 focus、低对比度文字以及
当前使用但未定义的样式。视觉回归脚本使用现有 Firefox/geckodriver，不新增依赖。

## 验收门槛

- 每阶段运行相关测试、`py_compile` 与 `git diff --check`。
- 每个旧根文件删除后运行 `python scripts/run_all_tests.py --quick`。
- Python 阶段与最终阶段运行完整 `python scripts/run_all_tests.py`。
- 86 条非静态路由及认证行为不变；根目录仅 `app.py`；不存在旧根模块 import。
- CSS 加载矩阵正确，全部 URL 返回 200，模板不再引用 `style.css`，使用的类有定义。
- `python app.py` 能完成数据库初始化，APScheduler 只启动一次。
- 同步 README、AGENTS、架构/开发者/用户/Agent 指南、模板维护文档和未发布 changelog。

## 完成记录

- 根目录仅保留 `app.py`；五个旧模块迁入正式包并由 AST 边界测试禁止回流。
- 邮件拆为 config/content/transport/service，analysis 拆出 client/messages/usage/JSON/report summary；数据库文件信息独立到 `source.storage.info`。
- `style.css` 已删除，11 个业务模板采用模块化加载矩阵；资源 URL、类名覆盖与 86 路由契约均有回归测试。
- Firefox/geckodriver 截图脚本已生成 11 个页面的 before/after 桌面与移动矩阵。
- 最终验收结果记录在未发布 changelog。

## 非目标

- 不配置化标签、评级标准或抓取分类。
- 不修改 `promo.css`、about/vision 页面，不增加深色模式或整体视觉改版。
- 不恢复旧 CLI 或旧根模块 import 兼容性。
