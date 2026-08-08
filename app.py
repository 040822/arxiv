"""Web 服务唯一根入口。

模块级 `app` 是已装配的 Flask 实例，供测试与 WSGI（如 `gunicorn app:app`）直接导入；
`main()` 负责启动前初始化（数据库、任务日志中断标记、调度器）并运行开发服务器。
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
