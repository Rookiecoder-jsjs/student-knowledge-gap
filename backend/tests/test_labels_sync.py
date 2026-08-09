"""候选5b：标签映射单一真源防漂移。

- labels_source 覆盖全部归因类型（含 易混淆，曾两处副本同时缺失）；
- 后端 labels.py 渲染函数与真源一致；
- codegen 生成的前端 labels.ts 与真源一致（防手改漂移）。
"""

from __future__ import annotations

from pathlib import Path

from app.labels_source import ATTR_LABEL, BAND_LABEL, CRITERION_LABEL, TRAJ_LABEL, VERSION_STATUS_LABEL
from app.pipeline.attribution import ATTR_CONFUSABLE, ATTR_FORGET, ATTR_INSUFFICIENT, ATTR_PREREQ
from app.reports.labels import attr_label, criterion_label, traj_label
from scripts.gen_labels_ts import build_labels_ts

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LABELS_TS = PROJECT_ROOT / "frontend" / "app" / "src" / "lib" / "labels.ts"


def test_every_attribution_type_has_label():
    # 归因四类都要有口语标签——曾漏 易混淆，回归护栏
    assert ATTR_PREREQ in ATTR_LABEL
    assert ATTR_FORGET in ATTR_LABEL
    assert ATTR_INSUFFICIENT in ATTR_LABEL
    assert ATTR_CONFUSABLE in ATTR_LABEL


def test_render_functions_follow_source():
    assert attr_label("前置缺陷") == "基础没打牢"
    assert attr_label("易混淆") == "概念混淆"
    assert traj_label("震荡") == "时好时坏"
    assert criterion_label("班级P25") == "处于班级后段"
    assert attr_label("未来类型") == "未来类型"  # 未知值原样返回


def test_generated_labels_ts_matches_source():
    """codegen 产物与磁盘文件一致——防止有人手改 labels.ts 造成漂移。"""
    assert LABELS_TS.exists(), f"缺少 {LABELS_TS}"
    on_disk = LABELS_TS.read_text(encoding="utf-8")
    assert on_disk == build_labels_ts()


def test_source_maps_are_nonempty_and_string_typed():
    for m in (ATTR_LABEL, TRAJ_LABEL, CRITERION_LABEL, BAND_LABEL, VERSION_STATUS_LABEL):
        assert isinstance(m, dict) and m
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in m.items())
