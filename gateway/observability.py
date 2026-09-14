"""Gateway structured logging with the same JSON contract as the backend."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

_CONFIGURED = False
_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "cookie",
    "authorization",
    "api_key",
    "prompt",
    "request_body",
    "response_body",
)
_STDLIB_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "gateway",
            "version": os.environ.get("SC_BOX_VERSION") or "unknown",
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STDLIB_ATTRS or key.startswith("_"):
                continue
            if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str | None = None) -> None:
    """Configure one JSON stderr handler for gateway application events."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger("sc.gateway")
    logger.setLevel((level or os.environ.get("SC_LOG_LEVEL", "INFO")).upper())
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    _CONFIGURED = True


def get_logger(name: str = "gateway") -> logging.Logger:
    return logging.getLogger(name if name.startswith("sc.") else f"sc.gateway.{name}")
