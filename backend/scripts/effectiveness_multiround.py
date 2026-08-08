"""多轮有效性测试：考试逐轮累积下，诊断有效性如何演化。

Part A：种子 42，按 E0->E4 逐轮提交，每轮在「该轮考试次日」测：
  - 覆盖率（已评估 kp 占比，反映数据饥饿）
  - 薄弱召回 / 正常学生误报率
  - 前置归因根源命中
  - 遗忘识别
  - 归因条数
Part B：种子 42-46，期末时点全量指标方差（稳定性）。

用法：.venv/bin/python scripts/effectiveness_multiround.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.ingestion.commit import commit_exam
from app.kb.graph import KpGraph
from app.kb.loader import import_kb
from app.models import Attribution, Class, School
from app.pipeline.attribution import (
    ATTR_FORGET,
    ATTR_INSUFFICIENT,
    ATTR_PREREQ,
    run_attribution_for_student,
)
from app.pipeline.weakness import assess_student_kps
from simulator.synthetic import EXAM_SCHEDULE, build_simulation

KB_YAML = "kb/math/grade7/kb.yaml"
FINAL_AS_OF = datetime(2026, 1, 16, 12, 0)


def _new_session():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    return S(), db_path


def _build(session, n, seed):
    kb = import_kb(session, KB_YAML)
    school = School(name="多轮测试学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="多轮班", grade=7, subject="数学")
    session.add(clazz)
    session.flush()
    truth = build_simulation(session, kb.id, clazz.id, n_students=n, seed=seed)
    return kb, clazz, truth


def _measure(session, graph, truth, clazz, as_of):
    planted_pairs = [
        (alias, code)
        for alias, codes in truth.planted_weak.items()
        for code in codes
    ]
    normal_aliases = [a for a, c in truth.planted_weak.items() if not c]
    total_grade7 = len(graph.grade7_kp_ids())

    assessable_planted = hit = 0
    normal_assessed = normal_fp = 0
    cov_sum = 0
    n_causal = n_insufficient = 0
    forget_detected = 0
    forget_kp_id = graph.code("M7A-113")

    for alias, sid in truth.student_ids.items():
        assessments = assess_student_kps(session, graph, sid, clazz.id, as_of)
        active = run_attribution_for_student(session, graph, sid, clazz.id, as_of)
        for a in active:
            if a.type in (ATTR_PREREQ, ATTR_FORGET):
                n_causal += 1
            elif a.type == ATTR_INSUFFICIENT:
                n_insufficient += 1
        cov_sum += sum(1 for a in assessments if a.gate is None and a.mastery is not None)
        if alias in truth.forgetting and any(
            a.type == ATTR_FORGET and a.kp_id == forget_kp_id for a in active
        ):
            forget_detected += 1
        amap = {a.kp_code: a for a in assessments}
        for a_alias, code in planted_pairs:
            if a_alias != alias:
                continue
            a = amap.get(code)
            if a is not None and a.gate is None and a.mastery is not None:
                assessable_planted += 1
                if a.is_weak:
                    hit += 1
        if alias in normal_aliases:
            for a in assessments:
                if a.gate is None and a.mastery is not None:
                    normal_assessed += 1
                    if a.is_weak:
                        normal_fp += 1

    root_hit = root_total = 0
    for alias, root_code in truth.planted_roots.items():
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
            root_total += 1
            root = graph.kp(att.root_kp_id).code if att.root_kp_id else None
            if root in planted_set:
                root_hit += 1

    n_students = len(truth.student_ids)
    return {
        "coverage": cov_sum / (n_students * total_grade7) if total_grade7 else 0.0,
        "recall": hit / assessable_planted if assessable_planted else None,
        "assessable_planted": assessable_planted,
        "fp_rate": normal_fp / normal_assessed if normal_assessed else 0.0,
        "root_hit": f"{root_hit}/{root_total}" if root_total else "0/0",
        "root_rate": root_hit / root_total if root_total else None,
        "forget": f"{forget_detected}/{len(truth.forgetting)}",
        "n_causal": n_causal,
        "n_insufficient": n_insufficient,
    }


def _fmt(v, fmt=".2f"):
    if v is None:
        return "  -  "
    if isinstance(v, str):
        return v
    return format(v, fmt)


def part_a_trajectory():
    print("=" * 92)
    print("Part A · 考试逐轮累积有效性轨迹（种子 42，30 学生）")
    print("=" * 92)
    session, db_path = _new_session()
    try:
        kb, clazz, truth = _build(session, 30, 42)
        graph = KpGraph(session, kb.id)
        header = f"{'轮次':<22}{'as_of':<12}{'覆盖率':>8}{'可评植入':>8}{'召回':>8}{'误报':>8}{'根源命中':>10}{'遗忘':>8}{'因果归因':>10}{'数据不足':>10}"
        print(header)
        print("-" * len(header))
        for name, exam_date, _t, _p in EXAM_SCHEDULE:
            commit_exam(session, truth.exam_ids[name])
            as_of = datetime.combine(exam_date + timedelta(days=1), time(12, 0))
            m = _measure(session, graph, truth, clazz, as_of)
            print(
                f"{name:<22}{str(exam_date+timedelta(days=1)):<12}"
                f"{_fmt(m['coverage']):>8}{m['assessable_planted']:>8}"
                f"{_fmt(m['recall']):>8}{_fmt(m['fp_rate']):>8}"
                f"{m['root_hit']:>10}{m['forget']:>8}"
                f"{m['n_causal']:>10}{m['n_insufficient']:>10}"
            )
        session.commit()
    finally:
        session.close()
        os.unlink(db_path)


def part_b_variance():
    print()
    print("=" * 92)
    print("Part B · 多种子期末指标方差（种子 42-46，30 学生，期末时点）")
    print("=" * 92)
    keys = ["coverage", "recall", "fp_rate", "root_rate"]
    samples: dict[str, list] = {k: [] for k in keys}
    forget_hits = []
    for seed in range(42, 47):
        session, db_path = _new_session()
        try:
            kb, clazz, truth = _build(session, 30, seed)
            graph = KpGraph(session, kb.id)
            for name in truth.exam_ids:
                commit_exam(session, truth.exam_ids[name])
            m = _measure(session, graph, truth, clazz, FINAL_AS_OF)
            for k in keys:
                samples[k].append(m[k])
            forget_hits.append(m["forget"])
            print(f"  seed {seed}: 召回={_fmt(m['recall'])}  误报={_fmt(m['fp_rate'])}  "
                  f"根源={m['root_hit']}  遗忘={m['forget']}  覆盖={_fmt(m['coverage'])}")
        finally:
            session.close()
            os.unlink(db_path)

    print("-" * 60)
    print("  汇总（mean ± stdev）：")
    labels = {"coverage": "覆盖率", "recall": "薄弱召回", "fp_rate": "正常误报", "root_rate": "根源命中"}
    for k in keys:
        vals = [v for v in samples[k] if v is not None]
        if vals:
            print(f"    {labels[k]:<8}: {mean(vals):.3f} ± {stdev(vals):.3f}  (n={len(vals)}, "
                  f"min={min(vals):.3f}, max={max(vals):.3f})")


def main():
    part_a_trajectory()
    part_b_variance()
    print()
    print("=" * 92)
    print("解读要点：")
    print("  1) 覆盖率随考试累积上升--MIN_EVIDENCE_COUNT=3 意味着多数 kp 要到累积型大考才可评估；")
    print("  2) 召回在数据充足后跃升，前期受「数据不足」闸门保护（不误判，但也不可用）；")
    print("  3) 误报率反映 P25 结构性误报 + 自然变异；多种子方差指示指标稳定性；")
    print("  4) 根源命中与遗忘识别是归因有效性的核心--看它们在期末是否达标。")
    print("=" * 92)


if __name__ == "__main__":
    main()
