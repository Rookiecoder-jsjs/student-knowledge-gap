"""学生下一步最多三张卡；只读建议，不生成学习方案或考试证据。"""
from datetime import datetime
from sqlalchemy import or_, select
from app.models import Intervention, StudyRecord
from app.pipeline.progress import LOOP_CLOSED, LOOP_ON_TRACK, loop_states_for_student
from app.pipeline.weakness import assess_student_kps


def next_tasks(db, graph, student, now=None):
    now = now or datetime.now()
    assessments = assess_student_kps(db, graph, student.id, student.class_id, now)
    loops = loop_states_for_student(db, graph, student.id, student.class_id, now, assessments=assessments)
    latest = {}
    for iv in db.scalars(select(Intervention).where(Intervention.class_id == student.class_id,
        Intervention.kp_id.in_(graph.grade_kp_ids(student.clazz.grade)),
        or_(Intervention.student_id == student.id, Intervention.student_id.is_(None)),
        Intervention.status != "skipped").order_by(Intervention.suggested_at.desc(), Intervention.id.desc())):
        latest.setdefault(iv.kp_id, iv)
    candidates = [a for a in assessments if a.is_weak or a.kp_id in latest]
    candidates = [a for a in candidates if a.gate != "未学到" and loops.get(a.kp_id) not in (LOOP_CLOSED, LOOP_ON_TRACK)]
    candidates.sort(key=lambda a: (0 if a.kp_id in latest else 1, a.mastery if a.mastery is not None else 1, a.kp_code))
    records = {}
    for rec in db.scalars(select(StudyRecord).where(StudyRecord.student_id == student.id).order_by(StudyRecord.id.desc())):
        records.setdefault(rec.kp_id, rec)
    out = []
    for a in candidates[:3]:
        iv, rec = latest.get(a.kp_id), records.get(a.kp_id)
        current = rec is not None and (iv is None or rec.generated_at >= iv.suggested_at)
        marked = current and rec.self_marked_at is not None
        awaiting = bool(loops.get(a.kp_id) in ("待复测", "自报待检验") or a.gate == "数据不足" and (marked or iv and iv.status == "done"))
        out.append({"kp_code": a.kp_code, "kp_name": a.kp_name,
            "status": "awaiting_retest" if awaiting else "study", "loop_state": loops.get(a.kp_id),
            "reason": ("你已自报学习完成，接下来用独立作答检查" if marked else "老师已安排，接下来用独立作答检查") if awaiting else "老师建议先加强这一点" if iv else "近期作答提示这一点值得加强",
            "minutes": 10, "steps": ["合上书，用自己的话说出概念和适用条件", "查看学习方案与例题，再独立完成两道同类题", "对照解题步骤，记录一个仍有疑问的地方"],
            "completion": "能独立解释概念与关键步骤，并完成两道同类题；学完可在学习方案里自报，之后通过老师安排的复测验证。",
            "retest_due_date": iv.retest_due_date.isoformat() if iv and iv.retest_due_date else None,
            "has_plan": bool(current), "self_marked": bool(marked)})
    return {"student_id": student.id, "as_of": now.date().isoformat(), "items": out,
            "remaining": max(0, len(candidates)-3)}
