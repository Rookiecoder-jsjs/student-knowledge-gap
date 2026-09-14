"""Tiny Prometheus exposition registry without a runtime dependency."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Mapping

_lock = Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}


def _key(name: str, labels: Mapping[str, object] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    normalized = tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))
    return name.removeprefix("sc_"), normalized


def inc(
    name: str,
    value: float = 1.0,
    *,
    labels: Mapping[str, object] | None = None,
) -> None:
    with _lock:
        _counters[_key(name, labels)] += value


def set_gauge(
    name: str,
    value: float,
    *,
    labels: Mapping[str, object] | None = None,
) -> None:
    with _lock:
        _gauges[_key(name, labels)] = value


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    escaped = (
        f'{key}="{value.replace(chr(92), chr(92) + chr(92)).replace(chr(34), chr(92) + chr(34))}"'
        for key, value in labels
    )
    return "{" + ",".join(escaped) + "}"


def render_prometheus() -> str:
    with _lock:
        counters = dict(_counters)
        gauges = dict(_gauges)
    lines: list[str] = []
    metric_types: dict[str, str] = {}
    for name, _labels in counters:
        metric_types[name] = "counter"
    for name, _labels in gauges:
        metric_types[name] = "gauge"
    for name, kind in sorted(metric_types.items()):
        metric = name if name.startswith("sc_") else f"sc_{name}"
        lines.append(f"# TYPE {metric} {kind}")
        values = counters if kind == "counter" else gauges
        for (item_name, labels), value in sorted(values.items()):
            if item_name != name:
                continue
            lines.append(f"{metric}{_render_labels(labels)} {value:g}")
    if not lines:
        lines = ["# TYPE sc_up gauge", "sc_up 1"]
    return "\n".join(lines) + "\n"
