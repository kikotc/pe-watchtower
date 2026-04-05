import json
import logging
import os
from collections import deque
from datetime import datetime, timezone

LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "app.log")

# In-memory ring buffer: holds the last 200 log records for the /logs endpoint
_log_buffer: deque = deque(maxlen=200)

_SKIP_FIELDS = {
    "message", "asctime", "levelname", "levelno", "name", "pathname",
    "filename", "module", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "threadName", "processName",
    "process", "exc_info", "exc_text", "stack_info", "msg", "args",
    "taskName",
}


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON, including any extra fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        # Include any extra= fields passed to the logger call
        for key, value in record.__dict__.items():
            if key not in _SKIP_FIELDS:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


class BufferHandler(logging.Handler):
    """Appends formatted log records to the in-memory ring buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = json.loads(self.format(record))
            _log_buffer.append(entry)
        except Exception:
            pass


def setup_logging() -> logging.Logger:
    """
    Configure the app logger with:
    - A file handler writing JSON to logs/app.log
    - A buffer handler keeping the last 200 entries in memory
    - A stream handler for terminal output during development
    """
    logger = logging.getLogger("app")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger  # already configured (e.g. reloaded in debug mode)

    formatter = JSONFormatter()

    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    buffer_handler = BufferHandler()
    buffer_handler.setFormatter(formatter)
    buffer_handler.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(buffer_handler)
    logger.addHandler(stream_handler)

    return logger


def get_log_buffer() -> list:
    """Return recent log entries as a list (newest last)."""
    return list(_log_buffer)
