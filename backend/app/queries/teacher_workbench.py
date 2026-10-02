"""今日队列覆盖全部授权学科考试，紧迫度与计数从事实派生。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Class, ExamTemplate, Intervention, KnowledgePoint, Report
from app.queries.exams import aggregate_exam_rows
from app.retest import retest_progress


def teacher_workbench(db: Session, class_id: int, subject: str, kp_ids: set[int], today, offset=0, limit=20):
    base = f"/c/{class_id}"
    clazz = db.get(Class, class_id)
    subject_filter = (ExamTemplate.subject == subject)
    if subject == clazz.subject:
        subject_filter = subject_filter | ExamTemplate.subject.is_(None)
    exams = list(db.scalars(select(ExamTemplate).where(ExamTemplate.class_id == class_id,
        subject_filter)))
    exam_ids = {e.id for e in exams}
    items = []
    def add(key, kind, title, reason, path, priority, due=None):
        items.append({"id": key, "kind": kind, "title": title, "reason": reason,
            "to": path, "priority": priority, "due_date": due})
    for e in aggregate_exam_rows(db, exams):
        n = e["response_counts"].get("待审核", 0)
        if n:
            add(f"scores-{e['exam_id']}", "scores", e["name"], f"{n} 份作答需核对与提交",
                f"{base}/exams/{e['exam_id']}/commit", 1)
        if e["unreviewed_tags"]:
            add(f"tags-{e['exam_id']}", "tags", e["name"], f"{e['unreviewed_tags']} 个知识点标注待审核",
                f"{base}/exams/{e['exam_id']}/review", 1)
    seen_groups = set()
    for iv in db.scalars(select(Intervention).where(Intervention.class_id == class_id,
            Intervention.kp_id.in_(kp_ids), Intervention.status.in_(["suggested", "done"]))
            .order_by(Intervention.id)):
        if iv.exam_id not in exam_ids:
            continue
        # 小组一次操作覆盖成员，队列不重复打扰。
        key = (iv.status, iv.group_ref) if iv.scope == "group" and iv.group_ref else (iv.status, iv.id)
        if key in seen_groups:
            continue
        kp = db.get(KnowledgePoint, iv.kp_id)
        if iv.status == "suggested":
            seen_groups.add(key)
            add(f"action-{iv.id}", "action", kp.name, "教学建议待确认，可查看十五分钟行动卡",
                f"{base}/exams?tab=diagnosis", 3)
        else:
            progress = retest_progress(db, iv)
            if progress["retest_status"] in ("overdue", "scheduled"):
                seen_groups.add(key)
                due = iv.retest_due_date.isoformat()
                overdue = iv.retest_due_date < today
                add(f"retest-{iv.id}", "retest", kp.name,
                    f"{'复测已到期' if overdue else '今日复测' if iv.retest_due_date == today else '待复测'} · 已有证据 {progress['retested_students']}/{progress['target_students']} 人",
                    f"{base}/exams?tab=diagnosis#retest-schedule", 0 if iv.retest_due_date <= today else 4, due)
    for report in db.scalars(select(Report).where(Report.class_id == class_id, Report.status == "draft")):
        if report.exam_id is not None and report.exam_id not in exam_ids:
            continue
        # 无考试来源的草稿由班级默认学科解释，不向其他科任泄露。
        if report.exam_id is None and db.get(Class, class_id).subject != subject:
            continue
        add(f"report-{report.id}", "report", "报告待签发", "复核内容后签发给学生",
            "/inbox", 2)
    items.sort(key=lambda x: (x["priority"], x["due_date"] or "", x["id"]))
    counts = {kind: sum(i["kind"] == kind for i in items) for kind in ("scores", "tags", "report", "action", "retest")}
    return {"as_of": today.isoformat(), "total": len(items), "offset": offset, "limit": limit,
        "counts": counts, "items": items[offset:offset+limit], "has_more": offset+limit < len(items)}
