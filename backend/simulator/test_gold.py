"""金标集 v0（DESIGN §12）：合成学生端到端断言。

真实数据到来之前，用植入真值验证整条管线：
采集 → 提交 → 证据 → 掌握度 → 薄弱判定 → 归因。
真实数据接入后本文件转为回归测试。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

KB_YAML = Path(__file__).resolve().parents[1] / "kb" / "math" / "grade7" / "kb.yaml"

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models  # noqa: F401 注册模型
from app.config import CLASS_COMMON_WEAK_RATIO
from app.ingestion.commit import commit_exam
from app.kb.graph import KpGraph
from app.kb.loader import import_kb
from app.models import Attribution, Class, School
from app.pipeline.attribution import (
    ATTR_FORGET,
    ATTR_PREREQ,
    materialize_attribution_verdicts,
)
from app.pipeline.weakness import (
    GATE_INSUFFICIENT,
    GATE_NOT_LEARNED,
    assess_student_kps,
)
from simulator.synthetic import build_simulation

FINAL_AS_OF = datetime(2026, 1, 16, 12, 0)
MID_AS_OF = datetime(2025, 10, 5, 12, 0)  # 第一次单元测之后、期中之前


@pytest.fixture(scope="module")
def env():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()

    kb = import_kb(session, str(KB_YAML))
    school = School(name="合成学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="七(1)班", grade=7, subject="数学")
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
    os.unlink(db_path)


def _kp_code(graph: KpGraph, kp_id: int) -> str:
    return graph.kp(kp_id).code


def test_weakness_recall(env):
    """植入薄弱点召回率 ≥ 0.60。"""
    session, graph, truth, clazz = env
    pairs = [
        (alias, code)
        for alias, codes in truth.planted_weak.items()
        for code in codes
    ]
    assert pairs, "真值表为空"
    hit = 0
    for alias, code in pairs:
        assessments = assess_student_kps(
            session, graph, truth.student_ids[alias], clazz.id, FINAL_AS_OF
        )
        a = next(x for x in assessments if x.kp_code == code)
        if a.is_weak:
            hit += 1
    recall = hit / len(pairs)
    print(f"\n[金标] 薄弱召回率 = {hit}/{len(pairs)} = {recall:.2f}")
    assert recall >= 0.60, f"召回率 {recall:.2f} 低于 0.60"


def test_not_learned_gate(env):
    """学期中评估：未教章节必须判「未学到」，绝不判薄弱。"""
    session, graph, truth, clazz = env
    alias = next(iter(truth.student_ids))
    assessments = assess_student_kps(
        session, graph, truth.student_ids[alias], clazz.id, MID_AS_OF
    )
    not_learned = [a for a in assessments if a.gate == GATE_NOT_LEARNED]
    assert len(not_learned) >= 20, "期中前应有大量未学到知识点（二/三/四章）"
    assert all(not a.is_weak for a in not_learned), "「未学到」被误判为薄弱"


def test_insufficient_gate(env):
    """证据 < 3 题 → 数据不足，绝不判薄弱。"""
    session, graph, truth, clazz = env
    insufficient_total = 0
    for stu_id in truth.student_ids.values():
        for a in assess_student_kps(session, graph, stu_id, clazz.id, FINAL_AS_OF):
            if a.gate == GATE_INSUFFICIENT:
                insufficient_total += 1
                assert not a.is_weak, f"数据不足的 {a.kp_code} 被判薄弱"
    assert insufficient_total > 0, "应存在数据不足的知识点（如仅期末考过的第四章）"


def test_prereq_attribution_root(env):
    """对植入薄弱点，前置缺陷归因的根源命中率 ≥ 0.5（根源 ∈ 植入薄弱集合）。

    只统计 kp ∈ 植入集合的归因：噪声弱点的成因本就不在真值表内，
    计入会稀释对归因逻辑本身的度量。
    """
    session, graph, truth, clazz = env
    total = hit = 0
    misses: list[str] = []
    for alias, root_code in truth.planted_roots.items():
        stu_id = truth.student_ids[alias]
        planted_set = truth.planted_weak[alias] | {root_code}
        atts = session.scalars(
            select(Attribution).where(
                Attribution.student_id == stu_id,
                Attribution.type == ATTR_PREREQ,
                Attribution.status == "active",
            )
        )
        for att in atts:
            kp_code = _kp_code(graph, att.kp_id)
            if kp_code not in planted_set or kp_code == root_code:
                continue  # 根源知识点自身的成因不在"前置缺陷"真值内
            total += 1
            root = _kp_code(graph, att.root_kp_id) if att.root_kp_id else None
            if root in planted_set:
                hit += 1
            else:
                misses.append(f"{alias}:{kp_code}→{root}")
    assert total > 0, "植入薄弱点未产生任何前置缺陷归因"
    rate = hit / total
    print(f"[金标] 前置缺陷根源命中 = {hit}/{total} = {rate:.2f}（未中: {misses[:6]}）")
    assert rate >= 0.50, f"根源命中率 {rate:.2f} 低于 0.50"


def test_forgetting_attribution(env):
    """植入遗忘的学生中至少 1 人被识别出遗忘衰减。"""
    session, graph, truth, clazz = env
    forget_kp_id = graph.code("M7A-113")
    found = 0
    for alias in truth.forgetting:
        att = session.scalar(
            select(Attribution).where(
                Attribution.student_id == truth.student_ids[alias],
                Attribution.kp_id == forget_kp_id,
                Attribution.type == ATTR_FORGET,
                Attribution.status == "active",
            )
        )
        if att is not None:
            found += 1
    print(f"[金标] 遗忘识别 = {found}/{len(truth.forgetting)}")
    assert found >= 1, "植入遗忘未被识别"


def test_class_common_flag(env):
    """全班共性薄弱（科学记数法）应主要触发班级共性标记。"""
    session, graph, truth, clazz = env
    weak_n = common_n = 0
    for stu_id in truth.student_ids.values():
        for a in assess_student_kps(session, graph, stu_id, clazz.id, FINAL_AS_OF):
            if a.kp_code == truth.class_common_kp and a.is_weak:
                weak_n += 1
                if a.is_class_common:
                    common_n += 1
    assert weak_n > 0, "植入的班级共性薄弱点无人被判薄弱"
    share = common_n / weak_n
    print(f"[金标] 班级共性标记覆盖 = {common_n}/{weak_n} = {share:.2f}")
    assert share >= 0.6, f"共性标记覆盖率 {share:.2f} 过低"


def test_floor_false_positive_bound(env):
    """正常学生按绝对底线误报率不应失控（≤35%）。"""
    session, graph, truth, clazz = env
    normal = [
        alias
        for alias, codes in truth.planted_weak.items()
        if not codes
    ]
    pairs = fp = 0
    for alias in normal:
        for a in assess_student_kps(
            session, graph, truth.student_ids[alias], clazz.id, FINAL_AS_OF
        ):
            if a.gate is not None:
                continue
            pairs += 1
            if a.is_weak and a.weak_criterion in ("绝对底线", "两者"):
                fp += 1
    rate = fp / pairs if pairs else 0.0
    print(f"[金标] 正常学生绝对底线误报 = {fp}/{pairs} = {rate:.2f}")
    assert rate <= 0.35, f"误报率 {rate:.2f} 过高"


def test_class_common_threshold_sanity(env):
    """配置一致性：共性阈值与文档一致。"""
    assert CLASS_COMMON_WEAK_RATIO == 0.40
