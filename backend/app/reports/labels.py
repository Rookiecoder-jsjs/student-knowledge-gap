"""枚举值 -> 口语标签映射（后端常量值不改，仅报告渲染层翻译）。

单一真源在 ``app.labels_source``（候选5b：与前端 labels.ts 由同一份数据生成，
防「同一映射两处写」漂移——曾漏 易混淆）。后端枚举常量（ATTR_PREREQ / TRAJ_* /
weak_criterion）仍是 API 契约，测试断言这些常量值；此处仅做显示层翻译，未知值原样返回。
"""

from __future__ import annotations

from app.labels_source import ATTR_LABEL, CRITERION_LABEL, TRAJ_LABEL


def attr_label(type_: str) -> str:
    return ATTR_LABEL.get(type_, type_)


def traj_label(t: str | None) -> str:
    if not t:
        return t or ""
    return TRAJ_LABEL.get(t, t)


def criterion_label(c: str | None) -> str:
    if not c:
        return c or ""
    return CRITERION_LABEL.get(c, c)
