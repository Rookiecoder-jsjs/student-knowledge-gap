"""结构化日志（G7）：JSON 行格式，便于聚合检索。

无第三方依赖（stdlib logging + json）。关键路径（batch worker 生命周期、LLM 调用、
commit 派生事件数）通过 ``sc.*`` 命名空间输出结构化字段，使失败可定位、数据质量
可观测（G1 的静默失败正是可观测性缺失的症状之一）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

_CONFIGURED = False

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
    """单行 JSON：ts / level / logger / msg + 调用方 extra 字段。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key in _STDLIB_ATTRS or key.startswith("_"):
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
    handler.setFormatter(JsonFormatter())
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
