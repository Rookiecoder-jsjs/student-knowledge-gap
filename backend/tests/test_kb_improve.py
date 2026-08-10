"""知识图谱改进（docs/kb-improvement-design.md 第一批）单测。

K1 confusable 激活 / K2 floor 分层 / K4 前向视图 / K7-A difficulty 先验。
金标 8 项不退化是硬约束（见 simulator/test_gold.py）；本文件只测新增能力。
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.db import Base
from app.ingestion.commit import commit_exam
from app.kb.graph import KpGraph
from app.models import (
    Class,
    ExamResponse,
    KbVersion,
    KnowledgePoint,
    KpRelation,
    QuestionKp,
    ResponseAnswer,
    School,
    Student,
    TeachingProgress,
    TemplateQuestion,
    ExamTemplate,
)
from app.pipeline.attribution import (
    ATTR_CONFUSABLE,
    materialize_attribution_verdicts,
)
from app.pipeline.mastery import mastery_of_events
from app.pipeline.weakness import (
    assess_student_kps,
    effective_floor,
)
from tests.conftest import make_exam, add_progress


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    s = S()
    yield s
    s.close()


def _dt(d: date) -> datetime:
    return datetime.combine(d, time(12, 0))


# ===========================================================================
# K2 · mastery_floor 按认知层级派生
# ===========================================================================


def _kp_obj(cog=None, floor=0.6, difficulty=0.5):
    """构造最小 KnowledgePoint-like 对象测派生逻辑。"""
    kp = KnowledgePoint(
        kb_version_id=1, code="K", name="K", grade=7, semester=1,
        chapter="ch", cog_levels_expected=cog or [], difficulty_prior=difficulty,
        mastery_floor=floor,
    )
    return kp


def test_effective_floor_by_cog():
    """未显式标注（floor=默认 0.6）时按主导认知层级派生。"""
    assert effective_floor(_kp_obj(cog=["综合"])) == 0.55
    assert effective_floor(_kp_obj(cog=["应用"])) == 0.60
    assert effective_floor(_kp_obj(cog=["理解"])) == 0.65
    assert effective_floor(_kp_obj(cog=["识记"])) == 0.70
    # 多层级取最高（[应用,综合] → 综合；[理解,应用] → 应用）
    assert effective_floor(_kp_obj(cog=["应用", "综合"])) == 0.55
    assert effective_floor(_kp_obj(cog=["理解", "应用"])) == 0.60
    # 无 cog 信息回退全局默认
    assert effective_floor(_kp_obj(cog=[])) == 0.6


def test_effective_floor_explicit_wins():
    """显式标注的 mastery_floor 优先于派生（M7A-105 等地基点的 0.7 保持）。"""
    assert effective_floor(_kp_obj(cog=["综合"], floor=0.7)) == 0.7
    assert effective_floor(_kp_obj(cog=["识记"], floor=0.5)) == 0.5


# ===========================================================================
# K4 · 前向影响视图
# ===========================================================================


def test_descendants_forward_view(session):
    """P1→P2→P3 前置链：descendants 返回前向后代（depth 1 直接 / 2 间接）。"""
    kb = KbVersion(subject="数学", textbook_edition="t", version="t")
    session.add(kb)
    session.flush()
    ids = {}
    for code in ["P1", "P2", "P3", "U"]:
        kp = KnowledgePoint(kb_version_id=kb.id, code=code, name=code, grade=7,
                            semester=1, chapter="ch", cog_levels_expected=["应用"],
                            difficulty_prior=0.5, mastery_floor=0.6)
        session.add(kp)
        session.flush()
        ids[code] = kp.id
    session.add(KpRelation(from_kp_id=ids["P1"], to_kp_id=ids["P2"],
                           type="prerequisite", weight=0.9))
    session.add(KpRelation(from_kp_id=ids["P2"], to_kp_id=ids["P3"],
                           type="prerequisite", weight=0.8))
    session.flush()

    graph = KpGraph(session, kb.id)
    desc = graph.descendants(ids["P1"], max_depth=2)
    by_code = {graph.kp(did).code: (d, w) for did, d, w in desc}
    print(f"\n[K4] P1 后代 = {by_code}")
    assert by_code["P2"][0] == 1, "P2 应是 P1 的直接后继（depth 1）"
    assert by_code["P2"][1] == 0.9
    assert by_code["P3"][0] == 2, "P3 应通过 P2 间接波及（depth 2）"
    assert "U" not in by_code, "无前置关系的独立点不应在波及范围"
    # 深度截断：max_depth=1 不含 P3
    desc1 = graph.descendants(ids["P1"], max_depth=1)
    assert all(d <= 1 for _, d, _ in desc1)
    assert len(desc1) == 1


# ===========================================================================
# K1 · confusable 激活：易混淆归因
# ===========================================================================


def test_confusable_attribution_when_partner_weak(session):
    """薄弱 KP 的 confusable 伙伴也弱 → 产出「易混淆」而非前置缺陷。

    构造：A 与 B 互标 confusable；A 的前置 P 掌握正常（无前置缺陷），
    但 A、B 同弱 → 应产出 ATTR_CONFUSABLE。
    """
    kb = KbVersion(subject="数学", textbook_edition="t", version="t")
    session.add(kb)
    session.flush()
    ids = {}
    for code in ["P", "A", "B"]:
        kp = KnowledgePoint(kb_version_id=kb.id, code=code, name=code, grade=7,
                            semester=1, chapter="ch", cog_levels_expected=["应用"],
                            difficulty_prior=0.5, mastery_floor=0.6)
        session.add(kp)
        session.flush()
        ids[code] = kp.id
    # P → A 前置（权重高），A ↔ B 易混
    session.add(KpRelation(from_kp_id=ids["P"], to_kp_id=ids["A"],
                           type="prerequisite", weight=0.9))
    session.add(KpRelation(from_kp_id=ids["A"], to_kp_id=ids["B"],
                           type="confusable", weight=1.0))
    session.add(KpRelation(from_kp_id=ids["B"], to_kp_id=ids["A"],
                           type="confusable", weight=1.0))
    session.flush()

    school = School(name="s")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="c", grade=7)
    session.add(clazz)
    session.flush()
    add_progress(session, clazz.id, list(ids.values()))
    stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias="S1")
    session.add(stu)
    session.flush()

    # 3 场考试 × 3 题（P 高分正常，A/B 低分薄弱）
    for i, d in enumerate([date(2025, 9, 30), date(2025, 11, 10), date(2026, 1, 15)]):
        tpl = make_exam(session, clazz.id, f"e{i}", d, "期中",
                        [(1, 10.0, "解答", "应用", [(ids["P"], 1.0)]),
                         (2, 10.0, "解答", "应用", [(ids["A"], 1.0)]),
                         (3, 10.0, "解答", "应用", [(ids["B"], 1.0)])])
        resp = ExamResponse(exam_template_id=tpl.id, student_id=stu.id,
                            source="excel", status="待审核")
        session.add(resp)
        session.flush()
        for q, sc in [(tpl.questions[0], 8.0), (tpl.questions[1], 3.0),
                      (tpl.questions[2], 3.0)]:
            session.add(ResponseAnswer(exam_response_id=resp.id,
                                       template_question_id=q.id, score=sc))
        resp.total_score = 14.0
        commit_exam(session, tpl.id)

    graph = KpGraph(session, kb.id)
    as_of = _dt(date(2026, 1, 16))
    active = materialize_attribution_verdicts(session, graph, stu.id, clazz.id, as_of)
    types = {(a.kp_id, a.type) for a in active}
    print(f"\n[K1] 归因类型 = {sorted(t for _, t in types)}")
    assert (ids["A"], ATTR_CONFUSABLE) in types, "薄弱 A 与同样弱的 B 易混 → 应产出易混淆归因"
    conf_att = next(a for a in active if a.type == ATTR_CONFUSABLE)
    assert conf_att.confidence == 0.65
    assert any(e.get("confused_with") == "B" for e in conf_att.evidence_json or [])
    # A 无前置缺陷（P 正常）：不该把 A 的薄弱归到 P
    prereq_att = [a for a in active if a.kp_id == ids["A"] and a.type == "前置缺陷"]
    assert prereq_att == [], "P 掌握正常，A 的薄弱不应误判前置缺陷"


def test_confusable_partner_strong_no_attribution(session):
    """伙伴掌握正常 → 不产出易混淆归因（避免把单点薄弱硬归为概念混淆）。"""
    kb = KbVersion(subject="数学", textbook_edition="t", version="t")
    session.add(kb)
    session.flush()
    ids = {}
    for code in ["A", "B"]:
        kp = KnowledgePoint(kb_version_id=kb.id, code=code, name=code, grade=7,
                            semester=1, chapter="ch", cog_levels_expected=["应用"],
                            difficulty_prior=0.5, mastery_floor=0.6)
        session.add(kp)
        session.flush()
        ids[code] = kp.id
    session.add(KpRelation(from_kp_id=ids["A"], to_kp_id=ids["B"],
                           type="confusable", weight=1.0))
    session.flush()

    school = School(name="s")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="c", grade=7)
    session.add(clazz)
    session.flush()
    add_progress(session, clazz.id, list(ids.values()))
    stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias="S1")
    session.add(stu)
    session.flush()

    for i, d in enumerate([date(2025, 9, 30), date(2025, 11, 10), date(2026, 1, 15)]):
        tpl = make_exam(session, clazz.id, f"e{i}", d, "期中",
                        [(1, 10.0, "解答", "应用", [(ids["A"], 1.0)]),
                         (2, 10.0, "解答", "应用", [(ids["B"], 1.0)])])
        resp = ExamResponse(exam_template_id=tpl.id, student_id=stu.id,
                            source="excel", status="待审核")
        session.add(resp)
        session.flush()
        for q, sc in [(tpl.questions[0], 3.0), (tpl.questions[1], 8.0)]:
            session.add(ResponseAnswer(exam_response_id=resp.id,
                                       template_question_id=q.id, score=sc))
        resp.total_score = 11.0
        commit_exam(session, tpl.id)

    graph = KpGraph(session, kb.id)
    active = materialize_attribution_verdicts(session, graph, stu.id, clazz.id,
                                         _dt(date(2026, 1, 16)))
    conf = [a for a in active if a.type == ATTR_CONFUSABLE]
    print(f"\n[K1-负例] 易混淆归因数 = {len(conf)}")
    assert conf == [], "伙伴掌握正常时不应产出易混淆归因"


# ===========================================================================
# K7-A · difficulty 先验（贝叶斯收缩，公式单测）
# ===========================================================================


def _ev(value: float, days_ago: float, source="期中"):
    from app.models import EvidenceEvent
    ev = EvidenceEvent()
    ev.value = value
    ev.weight = 1.0
    ev.source_type = source
    from datetime import timedelta
    ev.occurred_at = datetime(2026, 1, 16, 12, 0) - timedelta(days=days_ago)
    return ev


def test_mastery_prior_shrinkage():
    """数据少时向先验收缩（拉回极端值）；数据多时回归观测。"""
    as_of = datetime(2026, 1, 16, 12, 0)
    # 2 证据全 0 分（极端差）：纯观测 0.0；prior=0.6 收缩 → 明显拉向 0.6+
    events = [_ev(0.0, 5), _ev(0.0, 60)]
    raw = mastery_of_events(events, as_of)
    shrunk = mastery_of_events(events, as_of, prior=0.6, prior_strength=5.0)
    print(f"\n[K7-A] 2证据全0: 纯观测={raw:.3f} 先验收缩={shrunk:.3f}")
    assert raw == 0.0
    assert shrunk > 0.4, "先验应把 2 证据极端值从 0.0 拉回（防极端当确定结论）"
    assert shrunk < 0.6, "收缩不越过先验值本身（0.6）"
    # 数据多时回归观测：10 证据接近 0.0，收缩影响弱
    many = [_ev(0.0, 3 + i) for i in range(10)]
    shrunk_many = mastery_of_events(many, as_of, prior=0.6, prior_strength=5.0)
    print(f"[K7-A] 10证据全0: 先验收缩={shrunk_many:.3f}")
    assert shrunk_many < shrunk, "证据越多越回归观测（收缩影响越弱）"
    # prior_strength=0 等价纯观测
    assert mastery_of_events(events, as_of, prior=0.6, prior_strength=0.0) == raw


# ===========================================================================
# K5 · 节点重要度：报告排序 + 全局薄弱加权
# ===========================================================================


def _add_kp(session, kb_id, code, importance="核心"):
    kp = KnowledgePoint(kb_version_id=kb_id, code=code, name=code, grade=7,
                        semester=1, chapter="ch", cog_levels_expected=["应用"],
                        difficulty_prior=0.5, mastery_floor=0.6, importance=importance)
    session.add(kp)
    session.flush()
    return kp


def test_importance_field_roundtrip(session):
    """importance 落库 + brief 暴露 + loader 校验。"""
    kb = KbVersion(subject="数学", textbook_edition="t", version="t")
    session.add(kb)
    session.flush()
    kp = _add_kp(session, kb.id, "K1", "基础")
    session.flush()
    from app.api import routes
    brief = routes._kp_brief(kp)
    print(f"\n[K5] brief importance = {brief['importance']}")
    assert brief["importance"] == "基础"
    # loader 校验非法重要度
    from app.kb.loader import import_kb, KbImportError
    import tempfile, yaml, pathlib
    bad = {
        "meta": {"subject": "数学", "textbook_edition": "t", "version": "t2"},
        "knowledge_points": [{"code": "X1", "name": "x", "grade": 7,
                              "cog_levels_expected": ["应用"], "importance": "乱填"}],
        "relations": [],
    }
    fd = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    yaml.safe_dump(bad, fd)
    fd.close()
    try:
        with pytest.raises(KbImportError):
            import_kb(session, fd.name)
    finally:
        pathlib.Path(fd.name).unlink()


def test_report_sort_by_importance(session):
    """薄弱清单：基础 > 核心 > 拓展，同级别按掌握度缺口降序。"""
    from app.reports.student_diagnosis import generate_student_diagnosis
    from app.models import Report

    kb = KbVersion(subject="数学", textbook_edition="t", version="t")
    session.add(kb)
    session.flush()
    # 构造三类 KP：基础(0.5) / 核心(0.4) / 拓展(0.3) 全薄弱，且核心掌握度最低。
    # 注意：code 不能用 C 开头（容器节点前缀，grade7_kp_ids 会排除）。
    specs = [("K1", "基础", 0.5), ("K2", "核心", 0.4), ("K3", "拓展", 0.3)]
    code_by_imp = {"基础": "K1", "核心": "K2", "拓展": "K3"}
    for code, imp, _t in specs:
        _add_kp(session, kb.id, code, imp)
    school = School(name="s"); session.add(school); session.flush()
    clazz = Class(school_id=school.id, name="c", grade=7)
    session.add(clazz); session.flush()
    from app.models import Student, ExamTemplate, ResponseAnswer, ExamResponse, QuestionKp
    ids = {}
    for code, imp, t in specs:
        ids[code] = session.scalars(
            select(KnowledgePoint).where(KnowledgePoint.code == code)
        ).one().id
    add_progress(session, clazz.id, list(ids.values()))
    stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias="S1")
    session.add(stu); session.flush()
    # 3 场考试，各 KP 一道题，得分对应掌握度 target
    for i, d in enumerate([date(2025, 9, 30), date(2025, 11, 10), date(2026, 1, 15)]):
        tpl = make_exam(session, clazz.id, f"e{i}", d, "期中",
                        [(1, 10.0, "解答", "应用", [(ids["K1"], 1.0)]),
                         (2, 10.0, "解答", "应用", [(ids["K2"], 1.0)]),
                         (3, 10.0, "解答", "应用", [(ids["K3"], 1.0)])])
        resp = ExamResponse(exam_template_id=tpl.id, student_id=stu.id,
                            source="excel", status="待审核")
        session.add(resp); session.flush()
        for q, t in [(tpl.questions[0], 0.5), (tpl.questions[1], 0.4),
                     (tpl.questions[2], 0.3)]:
            session.add(ResponseAnswer(exam_response_id=resp.id,
                                       template_question_id=q.id,
                                       score=round(q.full_score * t * 2) / 2))
        resp.total_score = 12.0
        commit_exam(session, tpl.id)
    graph = KpGraph(session, kb.id)
    report = generate_student_diagnosis(session, graph, stu.id, _dt(date(2026, 1, 16)))
    md = report.content_markdown
    print(f"\n[K5] 报告薄弱顺序：")
    pos = {code: md.index(code) for code in ["K1", "K2", "K3"]}
    print("  位置:", pos)
    assert pos["K1"] < pos["K2"] < pos["K3"], "基础应在核心前、核心应在拓展前"


# ===========================================================================
# K7-B · 认知层级分层掌握度
# ===========================================================================


def test_per_cog_mastery(session):
    """多层 KP：按证据 cog_level 分层，识记高/应用低 → per_cog_mastery 揭示层级断层。"""
    kb = KbVersion(subject="数学", textbook_edition="t", version="t")
    session.add(kb); session.flush()
    kp = KnowledgePoint(kb_version_id=kb.id, code="K", name="K", grade=7,
                        semester=1, chapter="ch", cog_levels_expected=["识记", "应用"],
                        difficulty_prior=0.5, mastery_floor=0.6, importance="核心")
    session.add(kp); session.flush()
    school = School(name="s"); session.add(school); session.flush()
    clazz = Class(school_id=school.id, name="c", grade=7)
    session.add(clazz); session.flush()
    add_progress(session, clazz.id, [kp.id])
    stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias="S1")
    session.add(stu); session.flush()
    # 3 场考试 × 2 题（识记高分 / 应用低分）
    for i, d in enumerate([date(2025, 9, 30), date(2025, 11, 10), date(2026, 1, 15)]):
        tpl = make_exam(session, clazz.id, f"e{i}", d, "期中",
                        [(1, 10.0, "选择", "识记", [(kp.id, 1.0)]),
                         (2, 10.0, "解答", "应用", [(kp.id, 1.0)])])
        resp = ExamResponse(exam_template_id=tpl.id, student_id=stu.id,
                            source="excel", status="待审核")
        session.add(resp); session.flush()
        for q, sc in [(tpl.questions[0], 8.0), (tpl.questions[1], 3.0)]:
            session.add(ResponseAnswer(exam_response_id=resp.id,
                                       template_question_id=q.id, score=sc))
        resp.total_score = 11.0
        commit_exam(session, tpl.id)
    graph = KpGraph(session, kb.id)
    a = next(a for a in assess_student_kps(session, graph, stu.id, clazz.id,
                                           _dt(date(2026, 1, 16))) if a.kp_code == "K")
    print(f"\n[K7-B] per_cog = {a.per_cog_mastery}")
    assert a.per_cog_mastery is not None
    assert a.per_cog_mastery["识记"] > a.per_cog_mastery["应用"], "识记掌握度应高于应用（层级断层）"
    assert a.per_cog_mastery["应用"] < 0.5


# ===========================================================================
# K3 · 边权精炼贝叶斯收缩
# ===========================================================================


def test_edge_weight_bayesian_shrinkage():
    """贝叶斯收缩：α=n/(n+10)；低相关 + 足样本 → 待复核建议降权。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from refine_edge_weights import (
        EDGE_PRIOR_STRENGTH,
        _pearson,
        refine_edge_weights,
    )
    # _pearson 基本正确性
    assert _pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert _pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert _pearson([1, 2], [1, 2]) == pytest.approx(1.0)  # n=2 完美正相关
    assert _pearson([1, 1, 1], [1, 2, 3]) is None  # 零方差无法算相关
    # 收缩方向：样本多 → α→1，posterior 偏向数据；样本少 → α→0，偏向先验
    # α = n/(n+10)
    assert EDGE_PRIOR_STRENGTH == 10.0
    n_low = 2.0
    n_high = 100.0
    a_low = n_low / (n_low + EDGE_PRIOR_STRENGTH)
    a_high = n_high / (n_high + EDGE_PRIOR_STRENGTH)
    assert a_low < a_high
    print(f"\n[K3] α(low n=2)={a_low:.3f} < α(high n=100)={a_high:.3f}")


def test_edge_weight_refine_detects_low_corr(session):
    """端到端：植入低相关边 → 报告标待复核；高相关边 → 确认。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from refine_edge_weights import refine_edge_weights

    kb = KbVersion(subject="数学", textbook_edition="t", version="t")
    session.add(kb); session.flush()
    # A→B 高相关（真前置）；A→C 低相关（噪声边，C 独立高）
    ids = {}
    for code in ["A", "B", "C"]:
        ids[code] = _add_kp(session, kb.id, code).id
    session.add(KpRelation(from_kp_id=ids["A"], to_kp_id=ids["B"],
                           type="prerequisite", weight=0.8))
    session.add(KpRelation(from_kp_id=ids["A"], to_kp_id=ids["C"],
                           type="prerequisite", weight=0.8))
    session.flush()
    school = School(name="s"); session.add(school); session.flush()
    clazz = Class(school_id=school.id, name="c", grade=7)
    session.add(clazz); session.flush()
    add_progress(session, clazz.id, list(ids.values()))
    # 12 个学生，A 高低起伏、B 跟随 A（正相关）、C 固定 0.8（与 A 无关）
    sids = []
    a_vals = [0.3, 0.9, 0.4, 0.8, 0.35, 0.85, 0.5, 0.75, 0.3, 0.9, 0.6, 0.7]
    for i in range(12):
        stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias=f"S{i+1:02d}")
        session.add(stu); session.flush()
        sids.append(stu.id)
    def b_val(a):  # B 跟随 A
        return max(0.1, min(0.95, a - 0.1 + 0.05))
    for i, d in enumerate([date(2025, 9, 30), date(2025, 11, 10), date(2026, 1, 15)]):
        tpl = make_exam(session, clazz.id, f"e{i}", d, "期中",
                        [(1, 10.0, "解答", "应用", [(ids["A"], 1.0)]),
                         (2, 10.0, "解答", "应用", [(ids["B"], 1.0)]),
                         (3, 10.0, "解答", "应用", [(ids["C"], 1.0)])])
        for sid, av in zip(sids, a_vals):
            resp = ExamResponse(exam_template_id=tpl.id, student_id=sid,
                                source="excel", status="待审核")
            session.add(resp); session.flush()
            for q, t in [(tpl.questions[0], av), (tpl.questions[1], b_val(av)),
                         (tpl.questions[2], 0.8)]:
                session.add(ResponseAnswer(exam_response_id=resp.id,
                                           template_question_id=q.id,
                                           score=round(q.full_score * t * 2) / 2))
            resp.total_score = 18.0
        commit_exam(session, tpl.id)
    graph = KpGraph(session, kb.id)
    rows = refine_edge_weights(session, graph, clazz.id, min_n=8,
                               as_of=_dt(date(2026, 1, 16)))
    by = {r["to_code"]: r for r in rows}
    print(f"\n[K3] 边权精炼结果：")
    for code, r in by.items():
        print(f"  A->{code}: corr={r['corr']} n={r['n']} action={r['action']} 建议权={r['suggested_weight']}")
    assert by["B"]["action"] == "确认", "正相关的真前置边应确认"
    assert by["C"]["action"] == "待复核", "低相关的噪声边应标待复核"
    assert by["C"]["suggested_weight"] <= 0.3, "待复核边建议降权到 0.3"
