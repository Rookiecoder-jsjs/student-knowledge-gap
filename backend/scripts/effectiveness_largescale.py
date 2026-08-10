"""大规模多轮真实模拟有效性测试。

在 synthetic.py 固定场景之外做"硬核"有效性检验：
- 数据量大：3 班 × 50 人 = 150 人，12 场考试，约 2 万+ 作答；
- 时间尺度久：2025-09 ~ 2026-07 两学期约 10 个月；
- 随机性：每种子随机选取薄弱植入位置（前置根源/遗忘/班级共性），
  管线须在"未知位置"发现薄弱并归因 -> 打破"用同源假设自证"的循环验证。

Part A · 逐轮轨迹（种子 100，150 人）：12 场考试逐场提交，每轮测覆盖率/召回/误报/根源命中/遗忘。
Part B · 多种子方差（种子 100-105，150 人）：学年末时点全量指标稳定性。

测量优化：预取全班×全 kp 证据事件（get_events_batch）传入 assess_student_kps，
归因用 attribute_assessment 内存路径（不落库 upsert），避免每生重复全量预取。
全局薄弱抑制仅影响置信度不影响"是否归因/根源指向"，故测量路径跳过不影响指标。

用法：
  .venv/bin/python scripts/effectiveness_largescale.py            # default 配置
  SC_MIN_EVIDENCE_COUNT=2 SC_WEAKNESS_MODE=strict ... python ...   # fixes 配置
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

from app.config import (
    MIN_EVIDENCE_COUNT,
    WEAKNESS_MODE,
    WEAKNESS_P25_MARGIN,
    FORGET_PEAK_THRESHOLD,
)
from app.db import Base
from app.ingestion.commit import commit_exam
from app.kb.graph import KpGraph
from app.kb.loader import import_kb
from app.models import School
from app.pipeline.attribution import (
    ATTR_FORGET,
    ATTR_INSUFFICIENT,
    ATTR_PREREQ,
    attribute_assessment,
)
from app.pipeline.mastery import get_events_batch
from app.pipeline.weakness import assess_student_kps, covered_kp_ids
from simulator.large_scale import LARGE_EXAM_SCHEDULE, build_large_simulation

KB_YAML = "kb/math/grade7/kb.yaml"
FINAL_AS_OF = datetime(2026, 7, 18, 12, 0)


def _new_session():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    return S(), db_path


def _build(session, seed, n_classes=3, n_per_class=50):
    kb = import_kb(session, KB_YAML)
    school = School(name=f"大规模学校(seed={seed})")
    session.add(school)
    session.flush()
    truth = build_large_simulation(
        session, kb.id,
        n_classes=n_classes, n_per_class=n_per_class, seed=seed,
        # school 已建；build_large_simulation 会复用已有 school
    )
    # build_large_simulation 自建 school/class，此处复用其 class_ids
    session.flush()
    return kb, truth


def _measure(session, graph, truth, as_of):
    planted_pairs = [
        (alias, code)
        for alias, codes in truth.planted_weak.items()
        for code in codes
    ]
    # 正常学生 = 无个体植入（无前置根源、无遗忘）；班级共性是班级问题，不算个体薄弱
    normal_aliases = [
        a for a in truth.student_ids
        if a not in truth.planted_roots and a not in truth.forgetting
    ]
    total_grade7 = len(graph.grade7_kp_ids())
    kp_ids = list(graph.grade7_kp_ids())

    # 按班预取证据事件（一次查询取全班×全 kp）
    events_cache: dict[int, dict] = {}
    covered_cache: dict[int, set] = {}
    for class_id in truth.class_ids:
        class_sids = [
            sid for a, sid in truth.student_ids.items() if truth.class_of[a] == class_id
        ]
        events_cache[class_id] = get_events_batch(session, class_sids, kp_ids, as_of)
        covered_cache[class_id] = covered_kp_ids(session, class_id, as_of)

    assessable_planted = hit = 0
    normal_assessed = normal_fp = 0
    cov_sum = 0
    n_causal = n_insufficient = 0
    forget_detected = 0
    root_hit = root_total = 0
    n_students = len(truth.student_ids)

    for alias, sid in truth.student_ids.items():
        class_id = truth.class_of[alias]
        ev = events_cache[class_id]
        covered = covered_cache[class_id]
        assessments = assess_student_kps(
            session, graph, sid, class_id, as_of, events_by_sk=ev
        )
        amap = {a.kp_code: a for a in assessments}

        cov_sum += sum(1 for a in assessments if a.gate is None and a.mastery is not None)

        # 归因假设（内存路径，不落库）
        findings = []
        for a in assessments:
            findings.extend(attribute_assessment(session, graph, sid, a, covered, as_of))
        for f in findings:
            if f.type in (ATTR_PREREQ, ATTR_FORGET):
                n_causal += 1
            elif f.type == ATTR_INSUFFICIENT:
                n_insufficient += 1

        # 遗忘识别
        if alias in truth.forgetting:
            fk = truth.forgetting[alias]
            if any(
                f.type == ATTR_FORGET and graph.kp(f.kp_id).code == fk
                for f in findings
            ):
                forget_detected += 1

        # 根源命中：植入的后代是否被归因到植入根源
        if alias in truth.planted_roots:
            root_id = graph.code(truth.planted_roots[alias])
            for dc in truth.planted_descendants.get(alias, set()):
                a = amap.get(dc)
                if a is None or a.gate is not None or a.mastery is None or not a.is_weak:
                    continue  # 后代未评估/未薄弱 -> 非有效归因目标，不计入分母
                root_total += 1
                if any(
                    f.type == ATTR_PREREQ
                    and f.kp_id == a.kp_id
                    and f.root_kp_id == root_id
                    for f in findings
                ):
                    root_hit += 1

        # 召回：植入薄弱是否被检出
        for a_alias, code in planted_pairs:
            if a_alias != alias:
                continue
            a = amap.get(code)
            if a is not None and a.gate is None and a.mastery is not None:
                assessable_planted += 1
                if a.is_weak:
                    hit += 1

        # 误报：正常学生（无个体植入）在非班级共性 kp 上的误判
        if alias in normal_aliases:
            common_kp = truth.class_common_kps.get(class_id)
            for a in assessments:
                if a.gate is not None or a.mastery is None:
                    continue
                if common_kp and a.kp_code == common_kp:
                    continue  # 班级共性是真实薄弱，不计误报
                normal_assessed += 1
                if a.is_weak:
                    normal_fp += 1

    n_forget = len(truth.forgetting)
    return {
        "coverage": cov_sum / (n_students * total_grade7) if total_grade7 else 0.0,
        "recall": hit / assessable_planted if assessable_planted else None,
        "assessable_planted": assessable_planted,
        "fp_rate": normal_fp / normal_assessed if normal_assessed else 0.0,
        "root_hit": f"{root_hit}/{root_total}" if root_total else "0/0",
        "root_rate": root_hit / root_total if root_total else None,
        "forget": f"{forget_detected}/{n_forget}" if n_forget else "0/0",
        "forget_rate": forget_detected / n_forget if n_forget else None,
        "n_causal": n_causal,
        "n_insufficient": n_insufficient,
    }


def _fmt(v, fmt=".3f"):
    if v is None:
        return "  -   "
    if isinstance(v, str):
        return v
    return format(v, fmt)


def part_a_trajectory(seed=100):
    print("=" * 110)
    print(f"Part A · 逐轮累积有效性轨迹（种子 {seed}，3 班 × 50 人 = 150 人，2 学期 12 场考试）")
    print("=" * 110)
    session, db_path = _new_session()
    try:
        kb, truth = _build(session, seed)
        graph = KpGraph(session, kb.id)
        header = (
            f"{'轮次':<16}{'as_of':<13}{'覆盖率':>8}{'可评植入':>8}"
            f"{'召回':>8}{'误报':>8}{'根源命中':>10}{'遗忘':>8}"
            f"{'因果归因':>8}{'数据不足':>8}"
        )
        print(header)
        print("-" * len(header))
        for name, exam_date, _t, _p, _c in LARGE_EXAM_SCHEDULE:
            for class_id in truth.class_ids:
                commit_exam(session, truth.exam_ids[(name, class_id)])
            as_of = datetime.combine(exam_date + timedelta(days=1), time(12, 0))
            m = _measure(session, graph, truth, as_of)
            print(
                f"{name:<16}{str(exam_date + timedelta(days=1)):<13}"
                f"{_fmt(m['coverage']):>8}{m['assessable_planted']:>8}"
                f"{_fmt(m['recall']):>8}{_fmt(m['fp_rate']):>8}"
                f"{m['root_hit']:>10}{m['forget']:>8}"
                f"{m['n_causal']:>8}{m['n_insufficient']:>8}"
            )
        session.commit()
    finally:
        session.close()
        os.unlink(db_path)


def part_b_variance(seeds=range(100, 106)):
    seeds = list(seeds)
    print()
    print("=" * 110)
    print(f"Part B · 多种子学年末指标方差（种子 {seeds[0]}-{seeds[-1]}，150 人，12 场考试全提交）")
    print("=" * 110)
    keys = ["coverage", "recall", "fp_rate", "root_rate", "forget_rate"]
    samples: dict[str, list] = {k: [] for k in keys}
    for seed in seeds:
        session, db_path = _new_session()
        try:
            kb, truth = _build(session, seed)
            graph = KpGraph(session, kb.id)
            for name, _d, _t, _p, _c in LARGE_EXAM_SCHEDULE:
                for class_id in truth.class_ids:
                    commit_exam(session, truth.exam_ids[(name, class_id)])
            m = _measure(session, graph, truth, FINAL_AS_OF)
            for k in keys:
                samples[k].append(m[k])
            print(
                f"  seed {seed}: 覆盖={_fmt(m['coverage'])}  召回={_fmt(m['recall'])}  "
                f"误报={_fmt(m['fp_rate'])}  根源={m['root_hit']}  遗忘={m['forget']}  "
                f"因果={m['n_causal']}  不足={m['n_insufficient']}"
            )
        finally:
            session.close()
            os.unlink(db_path)

    print("-" * 70)
    print("  汇总（mean ± stdev）：")
    labels = {
        "coverage": "覆盖率  ", "recall": "薄弱召回", "fp_rate": "正常误报",
        "root_rate": "根源命中", "forget_rate": "遗忘识别",
    }
    for k in keys:
        vals = [v for v in samples[k] if v is not None]
        if vals:
            print(
                f"    {labels[k]}: {mean(vals):.3f} ± {stdev(vals):.3f}  "
                f"(n={len(vals)}, min={min(vals):.3f}, max={max(vals):.3f})"
            )


def main():
    print("当前配置：")
    print(
        f"  MIN_EVIDENCE_COUNT={MIN_EVIDENCE_COUNT}  WEAKNESS_MODE={WEAKNESS_MODE}  "
        f"P25_MARGIN={WEAKNESS_P25_MARGIN}  FORGET_PEAK={FORGET_PEAK_THRESHOLD}"
    )
    print()
    part_a_trajectory()
    part_b_variance()
    print()
    print("=" * 110)
    print("解读要点：")
    print("  1) 覆盖率：长跨度 12 场考试后应近满覆盖；看前期（单元测）是否受证据门槛保护；")
    print("  2) 召回：随机位置植入的薄弱是否被检出 -- 这是'非循环验证'的核心有效性指标；")
    print("  3) 误报：正常学生在非共性 kp 上的误判率，大样本下应稳定低位；")
    print("  4) 根源命中：前置缺陷是否定位到植入的随机根源（而非噪声祖先）；")
    print("  5) 遗忘：寒假间隔后的遗忘是否被识别（上学期高、下学期低的随机 kp）；")
    print("  6) 多种子方差：指标跨随机配置的稳定性 -- 方差大说明对'哪些 kp 薄弱'敏感。")
    print("=" * 110)


if __name__ == "__main__":
    main()
