import contextvars
import json
import logging
import sys
import uuid
from datetime import datetime, timezone


request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    """Formatter simple para logs estructurados."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "report_id"):
            payload["report_id"] = record.report_id
        if hasattr(record, "task_id"):
            payload["task_id"] = record.task_id
        return json.dumps(payload, ensure_ascii=True)


def set_request_id(request_id: str | None = None) -> str:
    current = request_id or str(uuid.uuid4())
    request_id_ctx.set(current)
    return current


def get_request_id() -> str:
    return request_id_ctx.get()


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level.upper())

