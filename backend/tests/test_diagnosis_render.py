"""候选3：学习诊断单渲染层纯函数（diagnosis_render.py）。

覆盖：成长框架顺序（先进步后薄弱）、归因段（ResolvedAttribution，含根源/依据/
验证方式）、前向影响预警、快照契约。无 DB——直接构造模型与图。
"""

from __future__ import annotations

from datetime import datetime

from app.kb.graph import KpGraph
from app.pipeline.attribution import ResolvedAttribution
from app.pipeline.weakness import KpAssessment
from app.reports.diagnosis_model import DiagnosisReportModel
from app.reports.diagnosis_render import model_to_snapshot, render_diagnosis_markdown


def _assess(**kw) -> KpAssessment:
    base = dict(kp_id=1, kp_code="M7A-105", kp_name="绝对值", mastery=0.4,
                evidence_count=3, is_weak=True, weak_criterion="绝对底线",
                trajectory="下滑", stale=True)
    base.update(kw)
    return KpAssessment(**base)


def test_render_order_and_attribution_section(session):
    """成长框架：进步先于薄弱；归因段渲染 ResolvedAttribution（可能根源/依据/验证）。"""
    # 图仅用于 descendants（前向预警）与根源名称解析
    g = _build_graph(session)
    graph, kp_ids = g["graph"], g["kp_ids"]
    model = DiagnosisReportModel(
        student_alias="小明",
        class_name="七(1)班",
        class_id=1,
        as_of=datetime(2026, 1, 16, 12, 0),
        progress=[_assess(kp_id=kp_ids["U"], kp_code="U", kp_name="独立点",
                          mastery=0.9, is_weak=False, trajectory="稳定")],
        weak=[_assess(kp_id=kp_ids["P2"], kp_code="P2", kp_name="中间点")],
        attributions={
            kp_ids["P2"]: ResolvedAttribution(
                kp_id=kp_ids["P2"], type="前置缺陷", confidence=0.8,
                root_kp_id=kp_ids["P1"],
                evidence=[{"ancestor": "P1", "ancestor_name": "基础点", "mastery": 0.3}],
                prediction="如果是基础没打牢，让该生单独做几道「基础点」的诊断题。",
            )
        },
    )
    md = render_diagnosis_markdown(graph, model)
    assert md.index("## 一、保持与进步") < md.index("## 二、下一步需要关注的知识点")
    assert "### 中间点（P2）" in md
    assert "基础没打牢" in md and "把握 80%" in md
    assert "可能根源：基础点（P1）" in md
    assert "依据：P1 掌握程度 0.3" in md
    assert "验证方式：如果是基础没打牢" in md
    # 策略模板句（intervention-loop §1 升级）：含根源点名与节奏槽位
    assert "建议：建议先补「基础点」再回到本点" in md
    assert "本周内完成首轮" in md
    # 前向影响预警：P2 的直接后继 P3（应用点）
    assert "应用点" in md and "影响预警" in md


def test_render_no_weak_fallback(session):
    g = _build_graph(session)
    model = DiagnosisReportModel(
        student_alias="小红", class_name="七(1)班", class_id=1,
        as_of=datetime(2026, 1, 16, 12, 0), weak=[], progress=[],
    )
    md = render_diagnosis_markdown(g["graph"], model)
    assert "当前没有达到待加强标准的知识点" in md
    assert "暂无足够依据识别明显优势点" in md


def test_snapshot_contract(session):
    g = _build_graph(session)
    graph, kp_ids = g["graph"], g["kp_ids"]
    model = DiagnosisReportModel(
        student_alias="小明", class_name="七(1)班", class_id=1,
        as_of=datetime(2026, 1, 16, 12, 0),
        weak=[_assess(kp_id=kp_ids["P2"], kp_code="P2", kp_name="中间点")],
        attributions={
            kp_ids["P2"]: ResolvedAttribution(
                kp_id=kp_ids["P2"], type="前置缺陷", confidence=0.8,
            )
        },
    )
    snap = model_to_snapshot(model)
    assert snap["student_alias"] == "小明" and snap["class"] == "七(1)班"
    assert snap["as_of"] == "2026-01-16"
    assert snap["weak"][0]["kp"] == "P2"
    assert snap["weak"][0]["mastery"] == 0.4 and snap["weak"][0]["criterion"] == "绝对底线"
    assert snap["attributions"] == [{"kp_id": kp_ids["P2"], "type": "前置缺陷",
                                     "confidence": 0.8}]


def _build_graph(session):
    """建 mini kb 图（P1->P2->P3 + U），返回带 kp_ids 与 graph 的对象。"""
    from app.models import KbVersion, KnowledgePoint, KpRelation

    kb = KbVersion(subject="数学", textbook_edition="测试版", version="t")
    session.add(kb)
    session.flush()
    kp_ids: dict[str, int] = {}
    names = {"P1": "基础点", "P2": "中间点", "P3": "应用点", "U": "独立点"}
    for code in ("P1", "P2", "P3", "U"):
        kp = KnowledgePoint(
            kb_version_id=kb.id, code=code, name=names[code], grade=7, semester=1,
            chapter="测试章", cog_levels_expected=["应用"], difficulty_prior=0.5,
            mastery_floor=0.6,
        )
        session.add(kp)
        session.flush()
        kp_ids[code] = kp.id
    for a, b in [("P1", "P2"), ("P2", "P3")]:
        session.add(KpRelation(from_kp_id=kp_ids[a], to_kp_id=kp_ids[b],
                               type="prerequisite", weight=0.9))
    session.flush()
    return {"kp_ids": kp_ids, "graph": KpGraph(session, kb.id)}
