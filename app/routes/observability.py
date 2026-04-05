import os
import time

import psutil
from flask import Blueprint, jsonify

from app.logging_config import get_log_buffer

observability_bp = Blueprint("observability", __name__)

_start_time = time.time()


@observability_bp.route("/metrics")
def metrics():
    process = psutil.Process(os.getpid())
    mem = psutil.virtual_memory()

    uptime_seconds = int(time.time() - _start_time)

    return jsonify({
        "uptime_seconds": uptime_seconds,
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory": {
            "used_mb": round(mem.used / 1024 / 1024, 1),
            "total_mb": round(mem.total / 1024 / 1024, 1),
            "percent": mem.percent,
        },
        "process": {
            "pid": process.pid,
            "memory_mb": round(process.memory_info().rss / 1024 / 1024, 1),
            "cpu_percent": process.cpu_percent(interval=0.1),
            "threads": process.num_threads(),
        },
    })


@observability_bp.route("/logs")
def logs():
    return jsonify(get_log_buffer())
