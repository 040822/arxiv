"""Backward-compatible Flask application entrypoint."""

import logging

from config import WEB_HOST, WEB_PORT
from source.web import app, create_app

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    app = create_app()
    logger.info(f"Starting web server at http://{WEB_HOST}:{WEB_PORT}")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False)
