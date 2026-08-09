"""标签映射单一真源（架构修复 候选5b：消除 labels.py ⇄ labels.ts 双写漂移）。

- ``reports/labels.py`` 直接从本模块导入（后端渲染）；
- ``scripts/gen_labels_ts.py`` 读本模块生成前端 ``frontend/app/src/lib/labels.ts``；
- ``tests/test_labels_sync.py`` 断言生成产物与单一真源一致，防新枚举漏译再发。

注意：**新增归因类型等枚举后必须同步在此补标签**（曾漏 ``易混淆``，两处副本同时缺失，
该类型在报告/界面原样显示而非口语标签——即本模块存在的原因）。
"""

from __future__ import annotations

ATTR_LABEL: dict[str, str] = {
    "前置缺陷": "基础没打牢",
    "遗忘衰减": "学过但忘了",
    "数据不足": "数据不足",
    "易混淆": "概念混淆",
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

BAND_LABEL: dict[str, str] = {
    "强制人工": "必须人工核对",
    "高亮提醒": "建议核对",
}

VERSION_STATUS_LABEL: dict[str, str] = {
    "draft": "草稿",
    "reviewed": "已审核",
    "active": "正式",
}
