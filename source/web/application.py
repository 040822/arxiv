"""Flask application assembly and runtime startup."""

import logging
import os
from datetime import timedelta

from flask import Flask

from source.pipeline import configure_daily_job, scheduler
from source.settings import ensure_admin_password, get_session_secret
from source.settings.guardrail import warn_on_settings_rebuild
from source.storage import init_db, interrupt_running_task_logs
from .auth import bp as auth_bp
from .import_api import bp as import_bp
from .learning_api import bp as learning_bp
from .pages import bp as pages_bp
from .papers_api import bp as papers_bp
from .providers_api import bp as providers_bp
from .settings_api import bp as settings_bp
from .tasks_api import bp as tasks_bp

logger = logging.getLogger(__name__)


def build_app():
    credential = ensure_admin_password()
    if credential.generated_password:
        logger.warning(
            "首次启动已生成管理密码（仅显示本次，请登录后尽快修改）：%s",
            credential.generated_password,
        )
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static"),
    )
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or get_session_secret()
    app.config.update(
        MAX_CONTENT_LENGTH=101 * 1024 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(days=180),
        SESSION_REFRESH_EACH_REQUEST=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    for blueprint in (
        auth_bp, pages_bp, papers_bp, import_bp, learning_bp,
        tasks_bp, settings_bp, providers_bp,
    ):
        app.register_blueprint(blueprint)
    return app


app = build_app()


def create_app():
    init_db()
    for warning in warn_on_settings_rebuild():
        logger.warning(warning)
    interrupted = interrupt_running_task_logs()
    if interrupted:
        logger.warning(f"Marked {interrupted} orphaned task logs as interrupted.")
    configure_daily_job()
    if not getattr(scheduler, "running", False):
        scheduler.start()
        logger.info("Scheduler started.")
    return app
