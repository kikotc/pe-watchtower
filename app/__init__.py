import logging
import time
import uuid

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
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]

    @app.after_request
    def _log_request(response):
        start = getattr(g, "start_time", None)
        duration_ms = round((time.monotonic() - start) * 1000, 2) if start else 0
        level = (
            "ERROR" if response.status_code >= 500
            else "WARNING" if response.status_code >= 400
            else "INFO"
        )
        logger.log(
            getattr(logging, level),
            "request",
            extra={
                "request_id": getattr(g, "request_id", "unknown"),
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Request-ID"] = getattr(g, "request_id", "unknown")
        return response

    @app.route("/health")
    def health():
        try:
            from app.database import db
            db.execute_sql("SELECT 1")
            db_status = "ok"
        except Exception as e:
            logger.error("Health check: DB unreachable", exc_info=e)
            return jsonify(status="degraded", database="unreachable"), 503
        return jsonify(status="ok", database=db_status)

    logger.info("App started")

    return app
