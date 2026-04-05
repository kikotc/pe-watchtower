import logging
import time

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request

from app.database import init_db
from app.logging_config import setup_logging
from app.routes import register_routes


def create_app():
    load_dotenv()

    app = Flask(__name__)

    logger = setup_logging()

    init_db(app)

    from app import models  # noqa: F401 - registers models with Peewee

    register_routes(app)

    @app.before_request
    def _start_timer():
        g.start_time = time.monotonic()

    @app.after_request
    def _log_request(response):
        duration_ms = round((time.monotonic() - g.start_time) * 1000, 2)
        level = (
            "ERROR" if response.status_code >= 500
            else "WARNING" if response.status_code >= 400
            else "INFO"
        )
        logger.log(
            getattr(logging, level),
            "request",
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    @app.route("/health")
    def health():
        return jsonify(status="ok")

    logger.info("App started")

    return app
