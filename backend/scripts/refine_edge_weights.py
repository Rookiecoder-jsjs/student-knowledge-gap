"""知识图谱边权精炼（kb-improvement-design K3）：数据驱动的建议权重，不自动改图。

对每条 prerequisite 边 (from->to)，取班级学生两端均达 MIN_EVIDENCE_COUNT 的样本：
  1. 算两端掌握度的皮尔逊相关性 observed_corr（前置关系成立 → 两端正相关）；
  2. 贝叶斯收缩：weight_posterior = α·weight_prior + (1-α)·|observed_corr|
     α = n / (n + K)，K=EDGE_PRIOR_STRENGTH（先验强度，默认 10）——样本越多越信数据；
  3. 低相关（|corr| < 0.2 且 n >= 8）：标「待复核」，建议降权到 0.3；
  4. 高相关（corr > 0.5）：权重维持或微调（方向正确，数据支持）。

纪律：
  - 不自动改图（不落库）——只产 diff 报告 + 建议权重，教师在前端 /kb 审核确认后落库；
  - 样本不足（n < 8）不触发建议——前期数据少时保持 LLM 先验，避免噪声驱动；
  - 与 K7-A 共享贝叶斯框架（先验 + 数据后验）。

用法：
  ../.venv/bin/python scripts/refine_edge_weights.py [--class-id 1] [--min-n 8] [--out edge_weights_diff.md]
  需已导入知识库 + 有真实考试数据（每边两端 ≥8 样本才触发建议）。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import MIN_EVIDENCE_COUNT, settings
from app.db import Base
from app.kb.graph import KpGraph
from app.kb.loader import import_kb
from app.models import Class, KbVersion, Student
from app.pipeline.mastery import evidence_summary, mastery_at

# 贝叶斯先验强度（K3）：α = n/(n+K)。K 越大越保守（需更多样本才偏离 LLM 先验）。
EDGE_PRIOR_STRENGTH = 10.0
CORR_RECOMPUTE_THRESHOLD = 0.2   # |corr| < 此值 且 n>=min_n → 建议降权（标待复核）
CORR_CONFIRM_THRESHOLD = 0.5     # corr > 此值 → 高相关，数据支持当前权重
LOW_WEIGHT_SUGGESTION = 0.3      # 待复核边的建议降权值


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / ((sx * sy) ** 0.5)


def refine_edge_weights(
    session, graph: KpGraph, class_id: int, min_n: int = 8, as_of: datetime | None = None
) -> list[dict]:
    """返回每边的建议权重报告；样本不足的边不进结果（保持 LLM 先验）。"""
    if as_of is None:
        as_of = datetime.utcnow()
    students = list(
        session.scalars(select(Student).where(Student.class_id == class_id))
    )

    rows: list[dict] = []
    for to_id, pres in graph._prereq.items():
        for from_id, weight in pres:
            xs: list[float] = []
            ys: list[float] = []
            for stu in students:
                sf = evidence_summary(session, stu.id, from_id, as_of)
                if sf.count < MIN_EVIDENCE_COUNT:
                    continue
                st = evidence_summary(session, stu.id, to_id, as_of)
                if st.count < MIN_EVIDENCE_COUNT:
                    continue
                m_from = mastery_at(session, stu.id, from_id, as_of)
                m_to = mastery_at(session, stu.id, to_id, as_of)
                if m_from is None or m_to is None:
                    continue
                xs.append(m_from)
                ys.append(m_to)
            n = len(xs)
            if n < min_n:
                continue  # 样本不足：不触发建议，保持先验

            corr = _pearson(xs, ys)
            if corr is None:
                continue

            # 贝叶斯收缩：α = n/(n+K)
            alpha = n / (n + EDGE_PRIOR_STRENGTH)
            posterior = alpha * weight + (1 - alpha) * abs(corr)

            action = "维持"
            note = ""
            if abs(corr) < CORR_RECOMPUTE_THRESHOLD:
                action = "待复核"
                posterior = min(posterior, LOW_WEIGHT_SUGGESTION)
                note = f"两端低相关（|corr|={corr:.2f}），该边可能不成立或被横切因素稀释，建议降权到 {LOW_WEIGHT_SUGGESTION}"
            elif abs(corr) > CORR_CONFIRM_THRESHOLD:
                action = "确认"
                note = f"两端高相关（corr={corr:.2f}），数据支持该前置关系"

            rows.append(
                {
                    "from_code": graph.kp(from_id).code,
                    "from_name": graph.kp(from_id).name,
                    "to_code": graph.kp(to_id).code,
                    "to_name": graph.kp(to_id).name,
                    "prior_weight": round(weight, 2),
                    "n": n,
                    "corr": round(corr, 3),
                    "alpha": round(alpha, 3),
                    "suggested_weight": round(posterior, 2),
                    "action": action,
                    "note": note,
                }
            )

    # 建议权重变化幅度排序：低相关（待复核）优先，其次变化大的
    rows.sort(
        key=lambda r: (
            0 if r["action"] == "待复核" else 1,
            -abs(r["suggested_weight"] - r["prior_weight"]),
        )
    )
    return rows


def render_markdown(rows: list[dict]) -> str:
    lines = [
        "# 图谱边权精炼建议（kb-improvement-design K3）",
        "",
        "> 仅建议，不自动改图。教师确认后在知识库审核界面逐条落库。",
        f"> 规则：α = n/(n+10) 贝叶斯收缩；|corr| < 0.2 且 n≥8 → 待复核建议降权到 0.3；corr > 0.5 → 数据支持。",
        "",
        f"| 前置 | 后继 | 先验权 | n | corr | α | 建议权 | 动作 | 说明 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['from_code']} {r['from_name']} | {r['to_code']} {r['to_name']} "
            f"| {r['prior_weight']} | {r['n']} | {r['corr']} | {r['alpha']} "
            f"| **{r['suggested_weight']}** | {r['action']} | {r['note']} |"
        )
    pending = sum(1 for r in rows if r["action"] == "待复核")
    lines += [
        "",
        f"共 {len(rows)} 条边有足够样本；其中 **{pending} 条待复核**（建议降权）。",
        f"样本不足（n < 8）的边保持 LLM 先验，未列入。",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--class-id", type=int, required=True, help="班级 id（取该班学生样本）")
    ap.add_argument("--min-n", type=int, default=8, help="最小样本数（默认 8）")
    ap.add_argument("--out", type=str, default=None, help="diff 报告输出路径（默认 stdout）")
    args = ap.parse_args()

    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()

    clazz = session.get(Class, args.class_id)
    if clazz is None:
        print(f"班级 {args.class_id} 不存在", file=sys.stderr)
        sys.exit(1)

    kb = session.scalars(
        select(KbVersion).where(KbVersion.status == "active")
    ).first()
    if kb is None:
        print("无 active 知识库版本", file=sys.stderr)
        sys.exit(1)
    graph = KpGraph(session, kb.id)

    rows = refine_edge_weights(session, graph, args.class_id, min_n=args.min_n)
    md = render_markdown(rows)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"已写入 {args.out}（{len(rows)} 条建议）")
    else:
        print(md)


if __name__ == "__main__":
    main()
