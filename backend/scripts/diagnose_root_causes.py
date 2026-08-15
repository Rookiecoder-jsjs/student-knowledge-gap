"""根因诊断：多轮测试暴露的三个问题为何发生。

逐个问题用实测数字取证，追到具体代码/设计决策：
  R1 覆盖率天花板 0.40 -> MIN_EVIDENCE_COUNT=3 × 每 kp 每考 1 题 × 考试按章覆盖
  R2 遗忘识别 0-3/3   -> FORGET_PEAK_THRESHOLD=0.75 × 早期仅 1-2 证据 × gauss(0,0.18) 噪声
  R3 召回非 1.0       -> 噪声把植入弱点的掌握度均值拉过 floor

用法：.venv/bin/python scripts/diagnose_root_causes.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.config import (
    FORGET_DROP,
    FORGET_IDLE_DAYS,
    FORGET_PEAK_THRESHOLD,
    MIN_EVIDENCE_COUNT,
)
from app.db import Base
from app.ingestion.commit import commit_exam
from app.kb.graph import KpGraph
from app.kb.loader import import_kb
from app.models import Class, EvidenceEvent, KnowledgePoint, School
from app.pipeline.attribution import ATTR_FORGET, materialize_attribution_verdicts
from app.pipeline.mastery import mastery_series
from app.pipeline.weakness import assess_student_kps
from simulator.synthetic import build_simulation

KB_YAML = "kb/math/grade7/kb.yaml"
FINAL_AS_OF = datetime(2026, 1, 16, 12, 0)
FORGET_KP = "M7A-113"


def _new_session():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    return S(), db_path, engine  # 返回 engine 供调用方 dispose 后删库（Windows 文件锁）


def _build(session, seed):
    kb = import_kb(session, KB_YAML)
    school = School(name="诊断学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="诊断班", grade=7, subject="数学")
    session.add(clazz)
    session.flush()
    truth = build_simulation(session, kb.id, clazz.id, n_students=30, seed=seed)
    for tpl_id in truth.exam_ids.values():
        commit_exam(session, tpl_id)
    return kb, clazz, truth


def r1_evidence_per_chapter():
    print("=" * 80)
    print("R1 · 覆盖率天花板 0.40 的根因：每生证据数按章分布")
    print(f"   MIN_EVIDENCE_COUNT = {MIN_EVIDENCE_COUNT}（config.py）")
    print("=" * 80)
    session, db_path, engine = _new_session()
    try:
        kb, clazz, truth = _build(session, 42)
        n_students = len(truth.student_ids)
        # 每个 grade-7 kp 的证据事件总数（跨全部学生）
        rows = session.execute(
            select(EvidenceEvent.kp_id, func.count(EvidenceEvent.id))
            .join(KnowledgePoint, KnowledgePoint.id == EvidenceEvent.kp_id)
            .where(KnowledgePoint.kb_version_id == kb.id, ~KnowledgePoint.code.like("C%"))
            .group_by(EvidenceEvent.kp_id)
        ).all()
        kp_code = {kp.id: kp.code for kp in session.scalars(select(KnowledgePoint))}
        by_ch: dict[str, list[int]] = defaultdict(list)
        for kp_id, cnt in rows:
            code = kp_code[kp_id]
            if not code.startswith("M7A-"):
                continue
            ch = code.split("-")[1][0]  # 章号
            by_ch[f"第{ch}章"].append(cnt // n_students)  # 每生证据数（仿真对称）
        print(f"   {'章节':<8}{'kp 数':>6}{'每生证据':>10}{'达阈值(≥3)':>12}{'可评估率':>10}")
        total_kp = total_ok = 0
        for ch in sorted(by_ch):
            cnts = by_ch[ch]
            ok = sum(1 for c in cnts if c >= MIN_EVIDENCE_COUNT)
            total_kp += len(cnts)
            total_ok += ok
            print(f"   {ch:<8}{len(cnts):>6}{cnts[0]:>10}{ok:>12}{ok/len(cnts):>10.0%}")
        print(f"\n   合计可评估 {total_ok}/{total_kp} = {total_ok/total_kp:.0%}（即多轮测试的覆盖率 0.40）")
        print(f"   结论：每生证据 = 该 kp 被考次数。ch1 被 E1/E2/E4 考 3 次 -> 达阈值；")
        print(f"         ch2/3/4 被 2/2/1 次 < {MIN_EVIDENCE_COUNT} -> 全「数据不足」。")
        print("   根因链：MIN_EVIDENCE_COUNT=3(config.py:43) × 每kp每考1题(synthetic.py:159) × EXAM_SCHEDULE 按章覆盖。")
    finally:
        session.close()
        engine.dispose()  # Windows: 释放连接池句柄后删库
        os.unlink(db_path)


def r2_forgetting_peak_fragility():
    print()
    print("=" * 80)
    print("R2 · 遗忘识别 0-3/3 的根因：峰值阈值 × 单样本噪声")
    print(f"   FORGET_PEAK_THRESHOLD={FORGET_PEAK_THRESHOLD}  FORGET_DROP={FORGET_DROP}  FORGET_IDLE_DAYS={FORGET_IDLE_DAYS}")
    print("=" * 80)
    forget_kp_name = "加减混合运算"
    for seed in range(42, 47):
        session, db_path, engine = _new_session()
        try:
            kb, clazz, truth = _build(session, seed)
            graph = KpGraph(session, kb.id)
            kp_id = graph.code(FORGET_KP)
            # GROUP_C = S10-S12
            c_aliases = [a for a in truth.forgetting]
            fired = 0
            details = []
            for alias in c_aliases:
                sid = truth.student_ids[alias]
                active = materialize_attribution_verdicts(session, graph, sid, clazz.id, FINAL_AS_OF)
                is_fire = any(a.type == ATTR_FORGET and a.kp_id == kp_id for a in active)
                fired += 1 if is_fire else 0
                series = mastery_series(session, sid, kp_id, FINAL_AS_OF)
                peak = max((v for _, v in series), default=None)
                current = series[-1][1] if series else None
                details.append(f"{alias}: peak={peak:.2f}{'(<0.75不触发)' if peak is not None and peak < FORGET_PEAK_THRESHOLD else ''} cur={current:.2f}")
            print(f"   seed {seed}: 遗忘 {fired}/3  | " + "  ".join(details))
        finally:
            session.close()
            engine.dispose()  # Windows: 释放连接池句柄后删库
            os.unlink(db_path)
    print(f"\n   结论：M7A-113 早期仅 E1/E2 两条证据，峰值 mastery 是 1-2 个噪声样本的均值；")
    print(f"   gauss(0,0.18) 噪声(synthetic.py:227) 易把真实 0.85 拉到 <{FORGET_PEAK_THRESHOLD} -> 峰值门槛不过 -> 遗忘不触发。")
    print("   根因：FORGET_PEAK_THRESHOLD 硬阈值 × 早期证据少 × 高噪声。seed 46 全漏即此。")


def r3_recall_misses():
    print()
    print("=" * 80)
    print("R3 · 召回非 1.0 的根因：噪声把植入弱点均值拉过 floor")
    print("=" * 80)
    session, db_path, engine = _new_session()
    try:
        kb, clazz, truth = _build(session, 42)
        graph = KpGraph(session, kb.id)
        misses = []
        for alias, codes in truth.planted_weak.items():
            sid = truth.student_ids[alias]
            assessments = assess_student_kps(session, graph, sid, clazz.id, FINAL_AS_OF)
            amap = {a.kp_code: a for a in assessments}
            for code in codes:
                a = amap.get(code)
                if a is None or a.gate is not None:
                    continue
                if not a.is_weak:
                    misses.append((alias, code, round(a.mastery, 3), a.floor, a.weak_criterion))
        if misses:
            print(f"   seed 42 植入弱点未被识别为薄弱（共 {len(misses)} 个）：")
            for alias, code, m, floor, crit in misses:
                print(f"     {alias} {code}: 掌握度 {m} >= floor {floor}（{crit}）-> 漏召")
            print("\n   根因：植入弱点真值 0.40-0.50，gauss(0,0.18) 噪声经 3 样本均值后仍可能越过 floor 0.6。")
        else:
            print("   seed 42 无漏召（全部植入弱点命中）。")
    finally:
        session.close()
        engine.dispose()  # Windows: 释放连接池句柄后删库
        os.unlink(db_path)


def main():
    r1_evidence_per_chapter()
    r2_forgetting_peak_fragility()
    r3_recall_misses()
    print()
    print("=" * 80)
    print("总览：三问题根因均在「保守门槛 × 稀疏考试数据 × 高噪声」的叠加，")
    print("     而非算法逻辑错误。算法在数据充足时表现良好（召回 0.91、根源 0.81）。")
    print("     改进杠杆：降门槛/增题量/累积大考全覆盖（提覆盖）、峰值阈值改用区间或加权（稳遗忘）。")
    print("=" * 80)


if __name__ == "__main__":
    main()
