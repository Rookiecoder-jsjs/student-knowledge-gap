"""真实测试数据集构建：全学年知识库 + 真实试卷 + 大规模模拟 → 真实管线落库。

用法（backend/ 目录下）：
    SC_DATABASE_URL=sqlite:///./realistic.db .venv/bin/python scripts/seed_realistic.py

产物：backend/realistic.db —— 4 班 × 50 人、全学年 13 场/班共 52 场真实试卷、
     200 名学生全部「已提交」+ 证据事件、全班归因假设、抽样真实叙事报告
     （output/质量分析_*.md / 诊断单_*.md）。

规模：~127 知识点（全学年）/ ~52 场考试 / ~1800 题 / ~9 万条题目得分证据。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 目标库：realistic.db（不覆盖 sc.db 演示库）。须在导入 app.* 前设置。
os.environ.setdefault("SC_DATABASE_URL", "sqlite:///./realistic.db")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.ingestion.commit import commit_exam  # noqa: E402
from app.kb.graph import KpGraph  # noqa: E402
from app.kb.loader import import_kb  # noqa: E402
from app.pipeline.attribution import materialize_attribution_verdicts  # noqa: E402
from app.reports.quality_analysis import generate_quality_analysis  # noqa: E402
from app.reports.student_diagnosis import generate_student_diagnosis  # noqa: E402
from simulator.realistic import REALISTIC_SCHEDULE, build_realistic_simulation  # noqa: E402

KB_YAML = ROOT / "kb" / "math" / "grade7" / "kb.yaml"


def main() -> None:
    # 干净的 realistic.db
    db_file = settings.database_url.replace("sqlite:///", "")
    if os.path.exists(db_file):
        os.remove(db_file)
    init_db()

    out = Path(settings.output_dir)
    out.mkdir(exist_ok=True)

    with SessionLocal() as session:
        kb = import_kb(session, str(KB_YAML))
        graph = KpGraph(session, kb.id)
        n_teach = len([k for k in graph.grade7_kp_ids()])
        print(f"[1/6] 知识库导入：{kb.subject} {kb.textbook_edition} v{kb.version}"
              f"（{len(graph._kp)} 节点 / {n_teach} 主年级知识点）")

        truth = build_realistic_simulation(
            session, kb.id, n_classes=4, n_per_class=50, seed=20250810
        )
        print(f"[2/6] 真实模拟：4 班 × 50 人 = {len(truth.student_ids)} 人，"
              f"{len(REALISTIC_SCHEDULE)} 场/班 × 4 班 = {len(truth.exam_ids)} 场")
        print(f"      植入：前置缺陷根源 {len(truth.planted_roots)} 人 / "
              f"遗忘 {len(truth.forgetting)} 人 / 班级共性 {len(truth.class_common_kps)} 班")

        # ---- 3. 真实 commit 管线：52 场考试逐场提交（状态机 + 证据事件） ----
        total_events = 0
        total_responses = 0
        for name, _d, _t, _c, _cu in REALISTIC_SCHEDULE:
            for class_id in truth.class_ids:
                r = commit_exam(session, truth.exam_ids[(name, class_id)])
                total_events += r.evidence_events
                total_responses += r.committed_responses
        print(f"[3/6] 提交 {total_responses} 份作答 / {total_events} 条证据事件（不可变追加）")

        # ---- 4. 全班归因（学年末） ----
        as_of = datetime(2026, 7, 18, 12, 0)
        n_attr = 0
        for nm, stu_id in truth.student_ids.items():
            n_attr += len(
                materialize_attribution_verdicts(
                    session, graph, stu_id, truth.class_of[nm], as_of
                )
            )
        print(f"[4/6] 归因引擎：4 班共产出 {n_attr} 条 active 归因假设")

        # ---- 5. 真实叙事报告（代表性学生 / 关键考试） ----
        sample_names = sorted(truth.planted_roots.keys())[:2] + \
                       sorted(truth.forgetting.keys())[:1] + \
                       sorted(truth.student_ids.keys())[-1:]
        for nm in sample_names:
            stu_id = truth.student_ids[nm]
            report = generate_student_diagnosis(
                session, graph, stu_id, as_of, narrative=True
            )
            path = out / f"诊断单_{nm}.md"
            path.write_text(report.content_markdown, encoding="utf-8")
            print(f"      诊断单 {nm} → {path.name}")
        for exam_name in ("期末考试（上）", "期中考试（下）", "学年期末考试"):
            exam_id = truth.exam_ids[(exam_name, truth.class_ids[0])]
            report = generate_quality_analysis(
                session, graph, truth.class_ids[0], exam_id, narrative=True
            )
            path = out / f"质量分析_{exam_name}.md"
            path.write_text(report.content_markdown, encoding="utf-8")
            print(f"      质量分析 {exam_name} → {path.name}")
        print(f"[5/6] 报告 → {out}/")

        # ---- 5.5 真值表落盘（仅供 effectiveness_realistic.py 自证对照，不进入分析管线） ----
        truth_path = ROOT / "realistic_truth.json"
        truth_path.write_text(json.dumps({
            "student_ids": {n: sid for n, sid in truth.student_ids.items()},
            "class_of": {n: c for n, c in truth.class_of.items()},
            "class_ids": truth.class_ids,
            "planted_weak": {n: sorted(cs) for n, cs in truth.planted_weak.items()},
            "planted_roots": dict(truth.planted_roots),
            "planted_descendants": {n: sorted(cs) for n, cs in truth.planted_descendants.items()},
            "forgetting": dict(truth.forgetting),
            "class_common_kps": {str(c): k for c, k in truth.class_common_kps.items()},
        }, ensure_ascii=False, indent=1))
        print(f"      真值表 → {truth_path.name}（供有效性自证对照）")

        session.commit()

    print("\n完成。realistic.db 已就绪，示例：")
    print(f"  cat {out}/诊断单_{sample_names[0]}.md")
    print("启动 API（指向 realistic.db）：")
    print("  SC_DATABASE_URL=sqlite:///./realistic.db .venv/bin/uvicorn app.main:app --port 8001")


if __name__ == "__main__":
    main()
