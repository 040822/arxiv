"""Web 服务唯一根入口。

模块级 `app` 是已装配的 Flask 实例（仅注册 Blueprint 与签名密钥），供测试导入；
生产部署请使用工厂启动完整运行时，例如单进程 `gunicorn 'app:create_app()'`：
`create_app()` 会初始化数据库、标记中断的任务日志并启动 APScheduler。
注意调度器是进程内单实例（`pipeline_lock` 非阻塞互斥），多进程部署时只应让一个
worker 执行定时任务，否则会发生抓取/日报竞争；数据库迁移本身由跨进程 flock 保护。
"""

import logging

from source.config import WEB_HOST, WEB_PORT
from source.web import app, create_app

logger = logging.getLogger(__name__)


def main():
    """启动 Web 服务：初始化数据库并启动 APScheduler，然后运行开发服务器。"""
    app = create_app()
    logger.info(f"Starting web server at http://{WEB_HOST}:{WEB_PORT}")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False)


if __name__ == "__main__":
    main()
