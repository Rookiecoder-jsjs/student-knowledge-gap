"""枚举值 -> 口语标签映射（后端常量值不改，仅报告渲染层翻译）。

与前端 frontend/app/src/lib/labels.ts 镜像，保持界面与报告口径一致。
后端枚举常量（ATTR_PREREQ / TRAJ_* / weak_criterion）仍是 API 契约，
测试断言这些常量值；此处仅做显示层翻译，未知值原样返回。
"""

from __future__ import annotations

ATTR_LABEL: dict[str, str] = {
    "前置缺陷": "基础没打牢",
    "遗忘衰减": "学过但忘了",
    "数据不足": "数据不足",
}

TRAJ_LABEL: dict[str, str] = {
    "稳定": "稳定",
    "上升": "上升",
    "下滑": "下滑",
    "震荡": "时好时坏",
}

CRITERION_LABEL: dict[str, str] = {
    "绝对底线": "低于及格线",
    "班级P25": "处于班级后段",
    "两者": "两条都中",
}


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
