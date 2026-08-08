"""图谱可疑边反查 CLI（improvement-plan §2.2）。

对每条 prerequisite 边，取班级学生在两端 kp 的掌握度算相关性，
低相关 -> 前置关系可能不成立。用于发现 LLM 起草图谱中的错边/弱边，
让"图谱缺边/错边"从隐性变为可观测。

用法（在 backend/ 目录）：
    ../.venv/bin/python -m scripts.audit_kb_edges --class-id 1 --as-of 2026-01-16

可选参数：
    --min-samples N     参与相关计算的最少学生数（默认 8）
    --corr THRESHOLD    |相关| 低于此值判可疑（默认 0.3）
"""

from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.kb.graph import KpGraph
from app.models import Class, KbVersion


def main() -> None:
    parser = argparse.ArgumentParser(description="可疑前置边反查")
    parser.add_argument("--class-id", type=int, required=True, help="班级 id")
    parser.add_argument(
        "--as-of", type=str, default=None, help="评估时点 YYYY-MM-DD（默认今天）"
    )
    parser.add_argument("--min-samples", type=int, default=8, help="最少学生样本数（默认 8）")
    parser.add_argument(
        "--corr", type=float, default=0.3, help="|相关| 低于此值判可疑（默认 0.3）"
    )
    args = parser.parse_args()

    as_of = (
        datetime.strptime(args.as_of, "%Y-%m-%d").replace(hour=12, minute=0)
        if args.as_of
        else datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    )

    session = SessionLocal()
    try:
        kb = session.scalar(
            select(KbVersion)
            .where(KbVersion.status == "active")
            .order_by(KbVersion.id.desc())
        )
        if kb is None:
            kb = session.scalar(select(KbVersion).order_by(KbVersion.id.desc()))
        if kb is None:
            raise SystemExit("尚未导入知识库，请先 POST /kb/import")

        clazz = session.get(Class, args.class_id)
        if clazz is None:
            raise SystemExit(f"班级 {args.class_id} 不存在")

        graph = KpGraph(session, kb.id)
        suspects = graph.suspect_edges(args.class_id, as_of, args.min_samples, args.corr)

        print(
            f"知识库: {kb.textbook_edition} v{kb.version} "
            f"(id={kb.id}, status={kb.status})"
        )
        print(
            f"班级: {clazz.name}  评估时点: {as_of.date()}  "
            f"样本门槛: {args.min_samples}  相关阈值: {args.corr}"
        )
        print(f"可疑前置边: {len(suspects)} 条\n")
        for e in suspects:
            print(
                f"  {e['from_code']}({e['from_name']}) -> {e['to_code']}({e['to_name']})  "
                f"weight={e['weight']}  n={e['n']}  corr={e['corr']}"
            )
        if not suspects:
            print("  （无可疑边，所有前置边两端掌握度相关性达标）")
    finally:
        session.close()


if __name__ == "__main__":
    main()
