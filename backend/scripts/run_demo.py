"""端到端演示：合成班级 → 五场考试 → 分析 → 生成真实报告文件。

用法：.venv/bin/python scripts/run_demo.py
产物：sc.db（演示库）+ output/*.md（质量分析文档与个人诊断单）
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.ingestion.commit import commit_exam  # noqa: E402
from app.kb.graph import KpGraph  # noqa: E402
from app.kb.loader import import_kb  # noqa: E402
from app.models import Class, School  # noqa: E402
from app.pipeline.attribution import materialize_attribution_verdicts  # noqa: E402
from app.reports.quality_analysis import generate_quality_analysis  # noqa: E402
from app.reports.student_diagnosis import generate_student_diagnosis  # noqa: E402
from simulator.synthetic import build_simulation  # noqa: E402


def main() -> None:
    # 干净的演示库
    db_file = settings.database_url.replace("sqlite:///", "")
    if os.path.exists(db_file):
        os.remove(db_file)
    init_db()

    out = Path(settings.output_dir)
    out.mkdir(exist_ok=True)

    with SessionLocal() as session:
        kb = import_kb(session, "kb/math/grade7/kb.yaml")
        graph = KpGraph(session, kb.id)
        print(f"[1/6] 知识库导入：{kb.subject} {kb.textbook_edition} v{kb.version}"
              f"（{len(graph._kp)} 个节点）")

        school = School(name="演示学校")
        session.add(school)
        session.flush()
        clazz = Class(school_id=school.id, name="七(1)班", grade=7, subject="数学")
        session.add(clazz)
        session.flush()

        truth = build_simulation(session, kb.id, clazz.id, n_students=30, seed=42)
        print(f"[2/6] 合成班级：30 名学生（含 4 组植入场景）× 5 场考试")

        total_events = 0
        for name, tpl_id in truth.exam_ids.items():
            r = commit_exam(session, tpl_id)
            total_events += r.evidence_events
            print(f"      提交「{name}」：{r.committed_responses} 人，"
                  f"+{r.evidence_events} 条证据事件")
        print(f"[3/6] 证据事件合计 {total_events} 条（不可变追加）")

        as_of = datetime(2026, 1, 16, 12, 0)
        n_attr = 0
        for stu_id in truth.student_ids.values():
            n_attr += len(
                materialize_attribution_verdicts(session, graph, stu_id, clazz.id, as_of)
            )
        print(f"[4/6] 归因引擎：全班产出 {n_attr} 条 active 归因假设")

        # ---- 班级质量分析（期中 + 期末） ----
        for exam_name in ("期中考试", "期末考试"):
            report = generate_quality_analysis(
                session, graph, clazz.id, truth.exam_ids[exam_name]
            )
            path = out / f"质量分析_{exam_name}.md"
            path.write_text(report.content_markdown, encoding="utf-8")
        print(f"[5/6] 质量分析文档 × 2 → {out}/")

        # ---- 个人诊断单（典型学生） ----
        samples = ["S01", "S06", "S10", "S13", "S20"]
        for alias in samples:
            stu_id = truth.student_ids[alias]
            report = generate_student_diagnosis(session, graph, stu_id, as_of)
            path = out / f"诊断单_{alias}.md"
            path.write_text(report.content_markdown, encoding="utf-8")
        print(f"[6/6] 个人诊断单 × {len(samples)} → {out}/")

        session.commit()

    print("\n完成。示例：")
    print(f"  cat {out}/质量分析_期末考试.md")
    print(f"  cat {out}/诊断单_S01.md   # 植入：绝对值前置缺陷链")
    print(f"  cat {out}/诊断单_S10.md   # 植入：遗忘衰减")
    print("\n启动 API：.venv/bin/uvicorn app.main:app --reload")


if __name__ == "__main__":
    main()
