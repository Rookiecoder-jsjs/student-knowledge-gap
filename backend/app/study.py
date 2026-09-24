"""AI 学习方案 + 学生自报（study-loop-design）。

修复段责任重分配的内容与进度事实层：AI 产方案（模板兜底）、学生自报推进
干预状态机。两条硬边界（设计 §0）：

- 学生自报**永不移动掌握度**——只消化干预队列、推进折叠状态（progress.py）；
  掌握度裁判永远是判分作答（自报不是证据，不产生 EvidenceEvent）；
- 轮次界定：自报只在当前干预轮内有效（``self_marked_at >= 本轮建议行的
  suggested_at``——轮次开始的时刻；不用 baseline_as_of，那是「考试日 23:59」
  的评估约定时点，同日建议会晚于当天自报）——二次干预（复测未闭合后重发
  建议）自然要求重学重报，旧轮自报不会伪装成新轮进度。

生成纪律（与 plan_writer 同构）：证据包（KP 教学内容 + 该生归因 prediction +
前置点状态）→ LLM → 结构校验，失败整体回落确定性模板——LLM 关闭/熔断时
方案退化为「教材内容 + 错因提示 + 自查清单」，闭环不断。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.intervention import SCOPE_STUDENT
from app.kb.graph import KpGraph
from app.llm.prompts import STUDY_PROMPT_VERSION
from app.llm.study_writer import write_study_plan
from app.models import Intervention, StudyRecord, Student
from app.pipeline.attribution import resolve_attributions
from app.pipeline.mastery import mastery_at
from app.pipeline.weakness import KpAssessment, assess_student_kps
from app.reports.labels import attr_label, traj_label


# ---------------------------------------------------------------------------
# 证据包（app/reports/evidence_pack.py 同形态：数字系统注入，LLM 只落笔）
# ---------------------------------------------------------------------------


def _pct(x: float | None) -> int | None:
    return round(x * 100) if x is not None else None


def study_evidence_pack(
    session: Session,
    graph: KpGraph,
    student: Student,
    kp_id: int,
    a: KpAssessment,
    as_of: datetime,
) -> dict:
    """单点学习证据包：教材内容 + 该生状态 + 归因 + 薄弱前置。学生版纪律同证据包：
    只含本人数据，无班级统计、无他人信息。"""
    kp = graph.kp(kp_id)
    floor = kp.mastery_floor if kp.mastery_floor is not None else 0.6
    prerequisites = []
    for anc_id, _depth, _w in graph.prerequisite_chain(kp_id, 3):
        anc = graph.kp(anc_id)
        m = mastery_at(session, student.id, anc_id, as_of)
        anc_floor = anc.mastery_floor if anc.mastery_floor is not None else floor
        if m is not None and m < anc_floor:
            prerequisites.append(
                {"name": anc.name, "mastery_pct": _pct(m)}
            )

    att = None
    for r in resolve_attributions(session, graph, student.id, student.class_id, as_of):
        if r.kp_id == kp_id and r.verdict == "active":
            att = {
                "type": attr_label(r.type),
                "confidence_pct": _pct(r.confidence),
                "root_kp": graph.kp(r.root_kp_id).name if r.root_kp_id else None,
                # 验证方式（归因预测现成话术），LLM 应转述为「你可能在哪卡住」
                "how_stuck": r.prediction or "",
                "teacher_note": r.teacher_note,
            }
            break

    return {
        "kind": "study_plan",
        "alias": student.name_or_alias,
        "kp": {
            "name": kp.name,
            "code": kp.code,
            "chapter": kp.chapter,
            "description": kp.description,
        },
        "mastery_pct": _pct(a.mastery),
        "criterion": a.weak_criterion,
        "evidence_count": a.evidence_count,
        "trajectory": traj_label(a.trajectory),
        "prerequisites": prerequisites,
        "attribution": att,
    }


def _template_plan(pack: dict) -> str:
    """确定性模板（LLM 关闭/失败/不合格时的兜底）：结构段与 LLM 版一致。"""
    kp = pack["kp"]
    lines = ["### 先补这一步"]
    if pack["prerequisites"]:
        for p in pack["prerequisites"]:
            lines.append(
                f"- 「{p['name']}」（当前掌握 {p['mastery_pct']}%）是"
                f"「{kp['name']}」的直接基础，建议先翻课本把对应小节过一遍。"
            )
    else:
        lines.append("- 基础已备好，直接开始。")
    lines.append("### 核心讲解")
    lines.append(
        kp.get("description")
        or "暂无教材内容，建议向老师索取本知识点的讲义或例题。"
    )
    lines.append("### 针对你的练习")
    att = pack.get("attribution")
    if att:
        if att.get("how_stuck"):
            lines.append(f"- 你可能是在「{att['type']}」上卡住了：{att['how_stuck']}")
        if att.get("root_kp"):
            lines.append(f"- 建议先把「{att['root_kp']}」练熟，再回到本知识点。")
    lines.append(
        "- 暂无 AI 生成例题：建议找老师要 2~3 道同类题，先自己做完再对答案。"
    )
    lines.append("### 怎么确认自己学会了")
    lines.append("- 合上书能自己说出这个知识点讲的是什么、什么时候用；")
    lines.append("- 找 2 道同类题能独立做对；")
    lines.append("- 下一场考试里这类题不再丢分。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 方案获取（get-or-generate）与自报
# ---------------------------------------------------------------------------


def _pending_row(
    session: Session, student: Student, kp_id: int
) -> Intervention | None:
    """该生该点当前挂起建议（个体行；班级行 student_id=None 一并视为触发源）。"""
    return session.scalar(
        select(Intervention)
        .where(
            Intervention.class_id == student.class_id,
            Intervention.kp_id == kp_id,
            Intervention.status == "suggested",
            or_(
                Intervention.student_id == student.id,
                Intervention.student_id.is_(None),
            ),
        )
        .order_by(Intervention.id.desc())
    )


def get_or_generate_study_record(
    session: Session,
    graph: KpGraph,
    student: Student,
    kp_id: int,
    *,
    allow_generate: bool = True,
    now: datetime | None = None,
) -> StudyRecord:
    """该生该点的学习方案（幂等复用，新一轮重生成）。

    - 资格：该点在薄弱清单**或**存在挂起建议（含班级行覆盖），否则 ValueError；
    - 同轮复用：已有记录与当前挂起行同轮（或无挂起行）→ 直接返回，不重调 LLM；
    - ``allow_generate=False``（管理员预览只读）：无记录抛 LookupError，绝不触发
      生成——预览纪律「学生永不触发分析/写操作」不因新功能破例；
    - ``allow_generate=True`` 而资格不符同样拒绝——生成是可写操作，先验资格。
    """
    now = now or datetime.now()
    if kp_id not in set(graph.grade_kp_ids(student.clazz.grade if student.clazz else None)):
        raise LookupError("知识点不存在")

    assessments = assess_student_kps(session, graph, student.id, student.class_id, now)
    by_kp = {a.kp_id: a for a in assessments}
    a = by_kp.get(kp_id)
    pending = _pending_row(session, student, kp_id)
    if (a is None or not a.is_weak) and pending is None:
        raise ValueError("该知识点当前不在你的待加强清单里")

    existing = session.scalar(
        select(StudyRecord)
        .where(StudyRecord.student_id == student.id, StudyRecord.kp_id == kp_id)
        .order_by(StudyRecord.id.desc())
    )
    if existing is not None:
        # 同轮判定：记录生成于当前挂起行建议之后（或无挂起行）→ 同一轮，复用。
        # 不能比 intervention_id——自报/派发把触发行落 done 后，同 kp 的挂起行
        # 会换成另一条（如班级行），按 id 比对会误判新轮而重复生成。
        same_round = pending is None or (
            existing.generated_at is not None
            and existing.generated_at >= pending.suggested_at
        )
        if same_round:
            return existing
    if not allow_generate:
        raise LookupError("该知识点还没有生成过学习方案")
    if a is None:
        raise ValueError("该知识点暂无评估数据，无法生成学习方案")

    pack = study_evidence_pack(session, graph, student, kp_id, a, now)
    draft = write_study_plan(pack)
    if draft is not None:
        writer: dict = {"model": draft.model, "prompt_version": STUDY_PROMPT_VERSION}
        md = draft.markdown
    else:
        writer = {"template": True}
        md = _template_plan(pack)
    rec = StudyRecord(
        class_id=student.class_id,
        student_id=student.id,
        kp_id=kp_id,
        intervention_id=pending.id if pending is not None else None,
        plan_markdown=md,
        plan_writer=writer,
        # generated_at = 调用方 now（默认真实时刻；轮次判定与展示同源）
        generated_at=now,
    )
    session.add(rec)
    session.flush()
    return rec


def self_mark_learned(
    session: Session,
    graph: KpGraph,
    student: Student,
    record_id: int,
    *,
    now: datetime | None = None,
) -> dict:
    """学生自报「我学会了」：置 self_marked_at；个体挂起建议行消化为 done。

    - 幂等：已自报直接返回（row_done=False）；
    - 只消化 **student scope** 的挂起行——班级行是集体事实（归老师派发/确认），
      小组行按组批量操作（with_group），学生个体自报都不碰；
    - 自报不产生任何 EvidenceEvent（掌握度裁判仍是判分作答）。
    """
    rec = session.get(StudyRecord, record_id)
    if rec is None or rec.student_id != student.id:
        raise LookupError("学习记录不存在")
    if rec.self_marked_at is not None:
        return {"id": rec.id, "self_marked_at": rec.self_marked_at, "row_done": False}
    now = now or datetime.now()
    rec.self_marked_at = now

    row_done = False
    row = session.scalar(
        select(Intervention)
        .where(
            Intervention.class_id == student.class_id,
            Intervention.student_id == student.id,
            Intervention.kp_id == rec.kp_id,
            Intervention.scope == SCOPE_STUDENT,
            Intervention.status == "suggested",
        )
        .order_by(Intervention.id.desc())
    )
    if row is not None:
        row.status = "done"
        row.done_at = now
        row.note = f"学生自报：已自学「{graph.kp(rec.kp_id).name}」"
        row_done = True
    session.flush()
    return {"id": rec.id, "self_marked_at": now, "row_done": row_done}


def self_report_map(
    session: Session, student_ids: list[int]
) -> dict[tuple[int, int], datetime]:
    """批量预取 (student_id, kp_id) → 最新自报时间（折叠/蓝图零 N+1）。"""
    if not student_ids:
        return {}
    out: dict[tuple[int, int], datetime] = {}
    for r in session.scalars(
        select(StudyRecord).where(
            StudyRecord.student_id.in_(student_ids),
            StudyRecord.self_marked_at.is_not(None),
        )
    ):
        key = (r.student_id, r.kp_id)
        cur = out.get(key)
        if cur is None or r.self_marked_at > cur:
            out[key] = r.self_marked_at
    return out
