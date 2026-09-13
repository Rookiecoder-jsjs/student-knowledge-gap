"""Tiny Prometheus exposition registry without a runtime dependency."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock

_lock = Lock()
_counters: dict[str, float] = defaultdict(float)


def inc(name: str, value: float = 1.0) -> None:
    with _lock:
        _counters[name] += value


def render_prometheus() -> str:
    with _lock:
        snapshot = dict(_counters)
    lines: list[str] = []
    for name, value in sorted(snapshot.items()):
        metric = name if name.startswith("sc_") else f"sc_{name}"
        lines.append(f"# TYPE {metric} counter")
        lines.append(f"{metric} {value:g}")
    if not lines:
        lines = ["# TYPE sc_up gauge", "sc_up 1"]
    return "\n".join(lines) + "\n"
