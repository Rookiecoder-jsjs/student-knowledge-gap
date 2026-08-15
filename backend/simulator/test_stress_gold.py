"""压力金标集（effectiveness-validation-plan V1）。

与 test_gold.py 的区别：test_gold 是**自洽性**测试（单 kp 题、完美标注、完美图谱），
本文件是**诚实度量**测试--注入真实误差源（多kp失分归属、标注错误、图谱扰动、全局薄弱），
打印真实指标、只设宽松 sanity 断言，让测试成为测量仪器而非拉拉队。

场景：
- S1 多kp失分归属污染 + MIX_PENALTY 效果（conftest 迷你 KB）
- S2 标注错误率-薄弱召回退化曲线（真实 kb，注入改标）
- S3 图谱扰动-根源命中下降 + suspect_edges 局限（真实 kb，删前置边）
- S4 全局薄弱学生过度归因 + V3 抑制器（真实 kb，植入全弱学生）
"""

from __future__ import annotations

import os
import random
import tempfile
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.db import Base
from app.ingestion.commit import commit_exam
from app.kb.graph import KpGraph
from app.kb.loader import import_kb
from app.models import (
    Attribution,
    Class,
    ExamResponse,
    KnowledgePoint,
    KpRelation,
    QuestionKp,
    ResponseAnswer,
    School,
    Student,
    TemplateQuestion,
    TeachingProgress,
)
from app.pipeline.attribution import (
    ATTR_FORGET,
    ATTR_PREREQ,
    GLOBAL_WEAK_CONF_CAP,
    materialize_attribution_verdicts,
)
from app.pipeline.mastery import mastery_at, mastery_of_events
from app.pipeline.weakness import assess_student_kps
from simulator.synthetic import EXAM_SCHEDULE, build_simulation

KB_YAML = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "kb" / "math" / "grade7" / "kb.yaml")
FINAL_AS_OF = datetime(2026, 1, 16, 12, 0)


# ---------------------------------------------------------------------------
# 共享仿真夹具（真实 kb + 30 学生 + 提交 + 归因，与 test_gold 同构）
# ---------------------------------------------------------------------------


@pytest.fixture()
def sim():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()

    kb = import_kb(session, KB_YAML)
    school = School(name="压测学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="压测班", grade=7, subject="数学")
    session.add(clazz)
    session.flush()

    truth = build_simulation(session, kb.id, clazz.id, n_students=30, seed=42)
    for tpl_id in truth.exam_ids.values():
        commit_exam(session, tpl_id)

    graph = KpGraph(session, kb.id)
    for stu_id in truth.student_ids.values():
        materialize_attribution_verdicts(session, graph, stu_id, clazz.id, FINAL_AS_OF)
    session.commit()
    yield session, graph, truth, clazz
    session.close()
    engine.dispose()  # Windows: 释放连接池句柄，否则 unlink 被文件锁阻塞
    os.unlink(db_path)


def _weakness_recall(session, graph, truth, clazz, as_of):
    pairs = [
        (alias, code)
        for alias, codes in truth.planted_weak.items()
        for code in codes
    ]
    hit = 0
    for alias, code in pairs:
        sid = truth.student_ids[alias]
        assessments = assess_student_kps(session, graph, sid, clazz.id, as_of)
        a = next((x for x in assessments if x.kp_code == code), None)
        if a and a.is_weak:
            hit += 1
    return hit / len(pairs) if pairs else 0.0


def _root_hit(session, graph, truth, root_code_filter=None):
    """前置缺陷归因根源命中率（仅统计 kp ∈ 植入集合的归因）。"""
    total = hit = 0
    for alias, root_code in truth.planted_roots.items():
        if root_code_filter and root_code != root_code_filter:
            continue
        sid = truth.student_ids[alias]
        planted_set = truth.planted_weak[alias] | {root_code}
        atts = session.scalars(
            select(Attribution).where(
                Attribution.student_id == sid,
                Attribution.type == ATTR_PREREQ,
                Attribution.status == "active",
            )
        )
        for att in atts:
            kp_code = graph.kp(att.kp_id).code
            if kp_code not in planted_set or kp_code == root_code:
                continue
            total += 1
            root = graph.kp(att.root_kp_id).code if att.root_kp_id else None
            if root in planted_set:
                hit += 1
    return hit, total


# ===========================================================================
# S1 · 多kp失分归属污染 + MIX_PENALTY 效果
# ===========================================================================


@pytest.fixture()
def mini():
    """迷你 KB：P1->P2->P3 前置链 + 独立点 U，6 学生，全部已教。"""
    from datetime import time

    from app.models import ExamTemplate, KbVersion, QuestionKp, TemplateQuestion

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    session = S()

    kb = KbVersion(subject="数学", textbook_edition="测试版", version="t")
    session.add(kb)
    session.flush()
    kp_ids: dict[str, int] = {}
    for code in ["P1", "P2", "P3", "U"]:
        kp = KnowledgePoint(
            kb_version_id=kb.id, code=code, name=code, grade=7, semester=1,
            chapter="测试章", cog_levels_expected=["应用"], difficulty_prior=0.5,
            mastery_floor=0.6,
        )
        session.add(kp)
        session.flush()
        kp_ids[code] = kp.id
    session.add(KpRelation(from_kp_id=kp_ids["P1"], to_kp_id=kp_ids["P2"], type="prerequisite", weight=0.9))
    session.add(KpRelation(from_kp_id=kp_ids["P2"], to_kp_id=kp_ids["P3"], type="prerequisite", weight=0.9))
    school = School(name="测试学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="测试班", grade=7)
    session.add(clazz)
    session.flush()
    students: dict[str, int] = {}
    for i in range(1, 7):
        stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias=f"T{i:02d}")
        session.add(stu)
        session.flush()
        students[f"T{i:02d}"] = stu.id
    for kid in kp_ids.values():
        session.add(TeachingProgress(class_id=clazz.id, kp_id=kid, taught_at=date(2025, 9, 1)))
    session.commit()
    yield session, kp_ids, clazz, students
    session.close()


def test_s1_multi_kp_pollution(mini, monkeypatch):
    """强kp(P2)被弱kp(U)拖累的混合题污染：MIX_PENALTY 减弱污染（方向性断言）。"""
    from app.models import ExamTemplate, QuestionKp, TemplateQuestion
    from app.pipeline.evidence import derive_events_for_response

    session, kp_ids, clazz, students = mini
    p2, u = kp_ids["P2"], kp_ids["U"]
    as_of = datetime(2025, 11, 11, 12, 0)

    # 期中卷：q1-3 单kp P2(强)；q4-6 单kp U(弱)；q7-10 多kp {P2,U}(被 U 拖低)
    tpl = ExamTemplate(class_id=clazz.id, name="期中", exam_date=date(2025, 11, 10), type="期中")
    session.add(tpl)
    session.flush()
    q_specs = (
        [(i, [(p2, 1.0)]) for i in range(1, 4)]
        + [(i, [(u, 1.0)]) for i in range(4, 7)]
        + [(i, [(p2, 1.0), (u, 1.0)]) for i in range(7, 11)]
    )
    qs: list[tuple[int, int]] = []
    for idx, tags in q_specs:
        tq = TemplateQuestion(exam_template_id=tpl.id, idx=idx, stem=f"题{idx}", q_type="解答",
                              full_score=10.0, cog_level="应用")
        session.add(tq)
        session.flush()
        for kp_id, w in tags:
            session.add(QuestionKp(template_question_id=tq.id, kp_id=kp_id, weight=w))
        qs.append((idx, tq.id))
    session.flush()

    def add_resp(sid, score_fn):
        resp = ExamResponse(exam_template_id=tpl.id, student_id=sid, source="excel", status="待审核")
        session.add(resp)
        session.flush()
        total = 0.0
        for idx, qid in qs:
            sc = score_fn(idx)
            total += sc
            session.add(ResponseAnswer(exam_response_id=resp.id, template_question_id=qid, score=sc))
        resp.total_score = round(total, 2)
        return resp

    t01_resp = add_resp(students["T01"], lambda i: 9.0 if i <= 3 else 3.0)  # P2 强、U 弱、混合被拖低
    for alias in ["T02", "T03", "T04", "T05", "T06"]:
        add_resp(students[alias], lambda i: 8.0)
    session.flush()

    def derive_all(penalty):
        monkeypatch.setattr("app.pipeline.evidence.EVIDENCE_MIX_PENALTY", penalty)
        for resp in session.scalars(select(ExamResponse).where(ExamResponse.exam_template_id == tpl.id)):
            resp.status = "已提交"
        session.flush()
        for resp in session.scalars(select(ExamResponse).where(ExamResponse.exam_template_id == tpl.id)):
            derive_events_for_response(session, resp.id)

    # MIX=0：含污染
    derive_all(0.0)
    single_answer_ids = {
        ra.id
        for ra in session.scalars(
            select(ResponseAnswer)
            .join(TemplateQuestion, TemplateQuestion.id == ResponseAnswer.template_question_id)
            .where(
                ResponseAnswer.exam_response_id == t01_resp.id,
                TemplateQuestion.idx.in_([1, 2, 3]),
            )
        )
    }
    p2_events = list(
        session.scalars(
            select(models.EvidenceEvent).where(
                models.EvidenceEvent.student_id == students["T01"],
                models.EvidenceEvent.kp_id == p2,
            )
        )
    )
    m_base = mastery_of_events([e for e in p2_events if e.response_answer_id in single_answer_ids], as_of)
    m0 = mastery_at(session, students["T01"], p2, as_of)

    # 重置证据，MIX=1 重派生
    session.execute(delete(models.EvidenceEvent))
    session.flush()
    derive_all(1.0)
    m1 = mastery_at(session, students["T01"], p2, as_of)

    floor = 0.6
    print(f"\n[S1] P2 掌握度：baseline(仅单kp)={m_base:.3f}  MIX=0(污染)={m0:.3f}  MIX=1(减弱)={m1:.3f}")
    print(
        f"[S1] floor={floor}  MIX=0 {'误判薄弱' if m0 < floor else '未薄弱'}  "
        f"MIX=1 {'误判薄弱' if m1 < floor else '未薄弱'}"
    )
    # 方向性断言：MIX_PENALTY 减弱污染 -> m1 更靠近 baseline
    assert m1 > m0, f"MIX_PENALTY 未减弱污染（m1={m1:.3f} <= m0={m0:.3f}）"


# ===========================================================================
# S2 · 标注错误率-薄弱召回退化曲线
# ===========================================================================


def _build_raw_sim(n_students, seed):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    session = S()
    kb = import_kb(session, KB_YAML)
    school = School(name="压测学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="压测班", grade=7, subject="数学")
    session.add(clazz)
    session.flush()
    truth = build_simulation(session, kb.id, clazz.id, n_students=n_students, seed=seed)
    return engine, session, kb, truth, clazz


def _retag_to_wrong_kp(session, kb, clazz, rate, rng):
    """按 rate 把题目标注改到另一个已教 kp（保持证据在局，制造误报而非单纯消失）。"""
    taught_ids = set(
        session.scalars(select(TeachingProgress.kp_id).where(TeachingProgress.class_id == clazz.id))
    )
    pool = [
        kp.id
        for kp in session.scalars(
            select(KnowledgePoint).where(
                KnowledgePoint.kb_version_id == kb.id,
                ~KnowledgePoint.code.like("C%"),
                KnowledgePoint.grade == 7,
            )
        )
        if kp.id in taught_ids
    ]
    n_ret = 0
    for qk in session.scalars(select(QuestionKp)):
        if rng.random() < rate:
            others = [k for k in pool if k != qk.kp_id]
            if others:
                qk.kp_id = rng.choice(others)
                n_ret += 1
    return n_ret


def test_s2_tag_error_degradation():
    """注入标注错误后薄弱召回单调下降；30% 错误率下不彻底崩塌（宽松 sanity）。"""
    rng = random.Random(7)
    results = []
    for rate in [0.0, 0.1, 0.3]:
        _engine, session, kb, truth, clazz = _build_raw_sim(15, 42)
        n_ret = _retag_to_wrong_kp(session, kb, clazz, rate, rng)
        for tpl_id in truth.exam_ids.values():
            commit_exam(session, tpl_id)
        graph = KpGraph(session, kb.id)
        recall = _weakness_recall(session, graph, truth, clazz, FINAL_AS_OF)
        results.append((rate, recall, n_ret))
        session.close()
    print(f"\n[S2] 标注错误率 -> 薄弱召回：{[(r[0], round(r[1],2)) for r in results]}（改标题数 {[r[2] for r in results]}）")
    # 单调性：召回应随错误率下降
    recalls = [r[1] for r in results]
    assert recalls[0] >= recalls[1] >= recalls[2] - 0.01, "召回未随标注错误率单调下降"
    # 宽松 sanity：30% 错误率下召回不应彻底崩塌
    assert recalls[2] >= 0.2, f"30% 标注错误下召回 {recalls[2]:.2f} 彻底崩塌"


# ===========================================================================
# S3 · 图谱扰动-根源命中下降 + suspect_edges 局限
# ===========================================================================


def test_s3_graph_perturbation(sim):
    """删除植入根源所在前置边(105->106)后，GROUP_A 根源命中下降；suspect_edges 无法检测缺失边。"""
    session, graph, truth, clazz = sim
    hit0, total0 = _root_hit(session, graph, truth, root_code_filter="M7A-105")
    print(f"\n[S3] GROUP_A 根源命中（干净图谱）= {hit0}/{total0}")

    root_id = graph.code("M7A-105")
    cons_id = graph.code("M7A-106")
    rel = session.scalar(
        select(KpRelation).where(
            KpRelation.from_kp_id == root_id,
            KpRelation.to_kp_id == cons_id,
            KpRelation.type == "prerequisite",
        )
    )
    assert rel is not None, "kb 中应存在 M7A-105 -> M7A-106 前置边"
    session.delete(rel)
    session.commit()
    graph2 = KpGraph(session, graph.kb_version_id)

    # 重跑 GROUP_A 归因（upsert：105 断开后，106/111/112 的非 105 祖先若强 -> 归因 resolved）
    for alias, root_code in truth.planted_roots.items():
        if root_code == "M7A-105":
            materialize_attribution_verdicts(session, graph2, truth.student_ids[alias], clazz.id, FINAL_AS_OF)
    session.commit()

    hit1, total1 = _root_hit(session, graph2, truth, root_code_filter="M7A-105")
    print(f"[S3] GROUP_A 根源命中（删 105->106 边后）= {hit1}/{total1}")
    print(f"[S3] 归因条数 {total0} -> {total1}（断根后多数归因 resolved，因果故事消失而非找替代因）")

    # suspect_edges 只查现存低相关边，无法检测「缺失边」
    suspects = graph2.suspect_edges(clazz.id, FINAL_AS_OF)
    has_missing = any(
        s["from_code"] == "M7A-105" and s["to_code"] == "M7A-106" for s in suspects
    )
    print(f"[S3] suspect_edges 命中 {len(suspects)} 条可疑边；检测到缺失边？{has_missing}（应为 False：审计只覆盖现存边）")

    # 扰动不应提升命中
    rate0 = hit0 / total0 if total0 else 0.0
    rate1 = hit1 / total1 if total1 else 0.0
    assert rate1 <= rate0 + 0.01, f"删边后命中率 {rate1:.2f} 反升（干净 {rate0:.2f}）"
    assert not has_missing, "suspect_edges 不应检测到已删除的边"


# ===========================================================================
# S5 · 遗忘识别精度（既有局限：3 噪声证据难区分真实遗忘与噪声峰值）
# ===========================================================================


def test_s5_forgetting_precision(sim):
    """遗忘 ATTR_FORGET 在非遗忘学生上的误报（既有局限，非修复引入）。

    根因：3 个噪声证据上，正常学生的"单次高分+后续回落"被误判为遗忘。
    低证据守卫已拦 2 证据 kp；3 证据上的误报需更多数据（生产环境每 kp ≥5 证据）+ 证伪闭环收敛。
    本测试度量并防回归，不硬性归零。
    """
    session, graph, truth, clazz = sim
    # sim 夹具已为所有学生跑过归因，直接查 Attribution 行
    fp = 0
    for alias, sid in truth.student_ids.items():
        atts = list(session.scalars(
            select(Attribution).where(
                Attribution.student_id == sid,
                Attribution.type == ATTR_FORGET,
                Attribution.status == "active",
            )
        ))
        if alias not in truth.forgetting:
            fp += len(atts)
    fk = graph.code("M7A-113")
    hit = sum(
        1 for alias in truth.forgetting
        if session.scalar(
            select(Attribution).where(
                Attribution.student_id == truth.student_ids[alias],
                Attribution.kp_id == fk,
                Attribution.type == ATTR_FORGET,
                Attribution.status == "active",
            )
        )
    )
    print(f"\n[S5] 遗忘：植入识别 {hit}/3，非遗忘学生误报 {fp} 条（3 噪声证据固有限制）")
    # 防回归上界：误报不应失控（低证据守卫 + 证伪闭环兜底）
    assert fp < 80, f"遗忘误报 {fp} 过多，超出已知局限上界"


def _add_student_with_mastery(session, truth, clazz, alias, mastery_fn, rng):
    """为已有仿真追加一名学生，按 mastery_fn 生成全部考试作答（复用 synthetic 的打分逻辑）。"""
    stu = Student(
        school_id=clazz.school_id,
        class_id=clazz.id,
        name_or_alias=alias,
        external_code=f"GW-{alias}",
    )
    session.add(stu)
    session.flush()
    for name, exam_date, _t, _p in EXAM_SCHEDULE:
        tpl_id = truth.exam_ids[name]
        tq_rows = list(
            session.scalars(
                select(TemplateQuestion)
                .where(TemplateQuestion.exam_template_id == tpl_id)
                .order_by(TemplateQuestion.idx)
            )
        )
        resp = ExamResponse(
            exam_template_id=tpl_id, student_id=stu.id, source="excel", status="待审核"
        )
        session.add(resp)
        session.flush()
        total = 0.0
        for tq in tq_rows:
            qkp = tq.kps[0] if tq.kps else None
            code = session.get(KnowledgePoint, qkp.kp_id).code if qkp else None
            m = mastery_fn(code, exam_date) if code else 0.4
            p = max(0.03, min(0.97, m + (0.5 - tq.difficulty_est) * 0.2 + rng.gauss(0, 0.05)))
            if tq.q_type == "选择":
                correct = rng.random() < p
                score = tq.full_score if correct else 0.0
            else:
                ratio = max(0.0, min(1.0, rng.gauss(m, 0.18)))
                score = round(tq.full_score * ratio * 2) / 2
            total += score
            session.add(
                ResponseAnswer(
                    exam_response_id=resp.id, template_question_id=tq.id, score=score
                )
            )
        resp.total_score = round(total, 2)
    session.flush()
    return stu.id


# ===========================================================================
# S4 · 全局薄弱学生过度归因 + V3 抑制器
# ===========================================================================


def test_s4_global_weak_over_attribution(sim):
    """全局薄弱学生(base 0.4)被前置缺陷归因过度归因；V3 抑制器把置信度压到 cap。"""
    session, graph, truth, clazz = sim
    rng = random.Random(99)
    gw_id = _add_student_with_mastery(session, truth, clazz, "GW01", lambda code, when: 0.4, rng)
    # 派生 GW01 证据（commit 幂等，他人已派生跳过）
    for tpl_id in truth.exam_ids.values():
        commit_exam(session, tpl_id)
    session.commit()

    active = materialize_attribution_verdicts(session, graph, gw_id, clazz.id, FINAL_AS_OF)
    prereq = [a for a in active if a.type == ATTR_PREREQ]
    confs = [a.confidence for a in prereq]
    roots = {a.root_kp_id for a in prereq}
    print(
        f"\n[S4] 全局薄弱学生 GW01：前置归因 {len(prereq)} 条，"
        f"置信度 {confs[:8]}{'...' if len(confs)>8 else ''}，相异根源 {len(roots)} 个"
    )

    # V3 抑制器：全局薄弱学生前置归因置信度应被压到 cap
    assert len(prereq) >= 1, "全局薄弱学生应有前置归因（降置信不删除）"
    assert all(c <= GLOBAL_WEAK_CONF_CAP for c in confs), (
        f"全局薄弱学生归因置信度未压到 cap：{confs}"
    )
    # evidence 含 global_weak 标注
    for a in prereq[:3]:
        ev = a.evidence_json or []
        assert any(e.get("global_weak") for e in ev), "evidence 缺 global_weak 标注"
