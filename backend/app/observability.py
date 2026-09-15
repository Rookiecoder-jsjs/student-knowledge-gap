"""结构化日志（G7）：JSON 行格式，便于聚合检索。

无第三方依赖（stdlib logging + json）。关键路径（batch worker 生命周期、LLM 调用、
commit 派生事件数）通过 ``sc.*`` 命名空间输出结构化字段，使失败可定位、数据质量
可观测（G1 的静默失败正是可观测性缺失的症状之一）。

安全边界提示：``_SENSITIVE_KEY_PARTS`` / ``_STDLIB_ATTRS`` 与
``gateway/observability.py`` 是同一契约的两份拷贝（gateway 镜像自包含、不 COPY
backend，无法共享模块）——任何一侧增删敏感字段必须同步另一侧，
``backend/tests/test_observability_sync.py`` 会解析两侧常量字面量强制一致。
"""

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

# LogRecord 标准属性白名单：其余属性视为调用方经 extra= 传入的结构化字段。
_STDLIB_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """单行 JSON：基础服务字段 + 调用方 extra 字段。"""

    def __init__(self, service: str = "backend", version: str = "unknown"):
        super().__init__()
        self.service = service
        self.version = version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "version": self.version,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key in _STDLIB_ATTRS or key.startswith("_"):
                continue
            normalized_key = key.lower()
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                continue
            try:
                json.dumps(val)
                payload[key] = val
            except (TypeError, ValueError):
                payload[key] = repr(val)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str | None = None) -> None:
    """配置 sc 命名空间日志（幂等）。默认 INFO，SC_LOG_LEVEL 可调。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = (level or os.environ.get("SC_LOG_LEVEL", "INFO")).upper()
    logger = logging.getLogger("sc")
    logger.setLevel(lvl)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter(
            service=os.environ.get("SC_SERVICE_NAME", "backend"),
            version=(
                os.environ.get("SC_APP_VERSION")
                or os.environ.get("SC_BOX_VERSION")
                or "unknown"
            ),
        )
    )
    logger.addHandler(handler)
    # 保留向 root 传播：测试用 pytest caplog（挂 root）可捕获 sc 日志；生产 root 无
    # handler 不重复输出。
    logger.propagate = True
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """返回 sc.<name> 日志器（调用前确保 setup_logging 已执行）。"""
    if not name.startswith("sc"):
        name = f"sc.{name}"
    return logging.getLogger(name)
