"""小题组只从已审核的本班题源取材；预览无副作用，确认时重验。"""
from datetime import date, timedelta
import math
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import MIN_EVIDENCE_COUNT
from app.kb.graph import KpGraph
from app.models import Attribution, KbVersion, ExamResponse, ExamTemplate, QuestionKp, ResponseAnswer, Student, TemplateQuestion
from app.pipeline.weakness import assess_student_kps, covered_kp_ids


def evidence_gaps(db: Session, graph: KpGraph, student: Student, when) -> dict:
    rows = assess_student_kps(db, graph, student.id, student.class_id, when)
    return {"student_id": student.id, "as_of": when.date().isoformat(), "items": [
        {"kp_id": a.kp_id, "kp_name": a.kp_name, "kp_code": a.kp_code,
         "evidence_count": a.evidence_count, "questions_needed": max(1, MIN_EVIDENCE_COUNT - a.evidence_count)}
        for a in rows if a.gate == "数据不足"
    ]}


def _slots(db, graph, student, kp_id, mode, attribution_id, when):
    if kp_id not in graph.grade_kp_ids(student.clazz.grade):
        raise LookupError("知识点不存在或不属于当前学科年级")
    if mode == "evidence":
        a = next((a for a in assess_student_kps(db, graph, student.id, student.class_id, when) if a.kp_id == kp_id), None)
        if a is None or a.gate != "数据不足":
            raise ValueError("该点当前不需要补证据，未学到的知识点不安排诊断")
        n = max(1, min(3, MIN_EVIDENCE_COUNT - a.evidence_count))
        return [(kp_id, "理解检查", ("识记", "理解"))] + [(kp_id, "独立应用", ("应用", "综合"))] * (n - 1), "补充独立作答后重新评估；题数达到门槛不保证足够判定。"
    att = db.get(Attribution, attribution_id) if attribution_id is not None else None
    if att is None or att.student_id != student.id or att.kp_id != kp_id or att.status != "active":
        raise ValueError("归因不存在、已被否决或不属于该学生与知识点")
    if att.type == "数据不足":
        raise ValueError("数据不足不是方向性归因，请使用补证据任务")
    if att.type == "前置缺陷" and att.root_kp_id not in graph.kp_ids():
        raise ValueError("前置根源不属于当前知识库，需重新分析归因")
    slots = [(kp_id, "目标概念检查", ("识记", "理解")), (kp_id, "目标独立应用", ("应用", "综合"))]
    guidance = "比较概念理解与独立应用的表现，由教师复核假设；小题组不自动确认归因。"
    if att.type == "前置缺陷" and att.root_kp_id in graph.kp_ids():
        slots = [(att.root_kp_id, "前置基础检查", ("识记", "理解", "应用")), slots[1]]
        guidance = "对照前置基础与目标应用的失分；表现仅用于核对前置缺陷假设。"
    elif att.type in ("迷思概念", "易混淆"):
        partners = graph.confusable_partners(kp_id)
        if partners:
            # 优先验证归因记录实际引用的易混点，不能随意换成另一条图谱关系。
            recorded_codes = {item.get("confused_with") for item in (att.evidence_json or []) if isinstance(item, dict)}
            recorded = [pid for pid in partners if graph.kp(pid).code in recorded_codes]
            partner = sorted(recorded or partners)[0]
            slots.append((partner, "易混概念对照", ("识记", "理解", "应用")))
        guidance = "要求每题说明概念与适用条件，教师对照易混点判断。"
    elif att.type == "遗忘衰减":
        guidance = "本次检查不能区分遗忘与未掌握，需间隔复测与历史表现共同验证。"
    return slots, guidance


def diagnostic_blueprint(db: Session, graph: KpGraph, student: Student, kp_id: int,
                         mode: str, attribution_id: int | None, when) -> dict:
    slots, guidance = _slots(db, graph, student, kp_id, mode, attribution_id, when)
    # 连待审核作答也排除，避免刚做过的题被当成独立新证据。
    seen_stems = {stem.strip() for stem in db.scalars(select(TemplateQuestion.stem).join(ResponseAnswer)
        .join(ExamResponse).where(ExamResponse.student_id == student.id))}
    subject = db.get(KbVersion, graph.kb_version_id).subject
    subject_filter = (ExamTemplate.subject == subject)
    if subject == student.clazz.subject:
        subject_filter = subject_filter | ExamTemplate.subject.is_(None)
    candidates = list(db.scalars(select(TemplateQuestion).join(ExamTemplate)
        .where(ExamTemplate.class_id == student.class_id,
               ExamTemplate.source != "diagnostic_task",
               ExamTemplate.exam_date <= when.date(),
               subject_filter)
        .options(selectinload(TemplateQuestion.kps))
        .order_by(ExamTemplate.exam_date.desc(), TemplateQuestion.id)))
    covered = covered_kp_ids(db, student.class_id, when)
    selected, used_stems = [], set(seen_stems)
    for target, purpose, levels in slots:
        found = None
        for q in candidates:
            if target not in covered or graph.kp(target).archived:
                break
            tags = q.kps
            if (not q.stem.strip() or q.stem.strip() in used_stems or q.cog_level not in levels
                    or not math.isfinite(q.full_score) or q.full_score <= 0):
                continue
            # 单点且已审核的题才适合分辨假设，混合标签题不冒充隔离测量。
            if len(tags) != 1 or tags[0].kp_id != target or tags[0].reviewed_at is None or not math.isfinite(tags[0].weight) or tags[0].weight <= 0:
                continue
            found = q
            break
        kp = graph.kp(target)
        item = {"kp_id": target, "kp_name": kp.name, "purpose": purpose, "question": None}
        if found:
            used_stems.add(found.stem.strip())
            exam = db.get(ExamTemplate, found.exam_template_id)
            item["question"] = {"id": found.id, "stem": found.stem, "full_score": found.full_score,
                "cog_level": found.cog_level, "exam_id": exam.id, "exam_name": exam.name, "question_idx": found.idx}
        selected.append(item)
    result = {"student_id": student.id, "kp_id": kp_id, "kp_name": graph.kp(kp_id).name,
            "subject": subject, "mode": mode, "attribution_id": attribution_id, "slots": selected,
            "ready": all(x["question"] is not None for x in selected), "guidance": guidance,
            "limitation": "现有题源未必提供完整题面与答案，请教师预览原卷、确认适用后建卷。已作答题及相同题干不会重复选用。"}

    # 绑定教师实际预览的题面、分值和完整结构；同主键内容变化也要求重新确认。
    content = []
    for slot in selected:
        q = db.get(TemplateQuestion, slot["question"]["id"]) if slot["question"] else None
        content.append(None if q is None else {
            "id": q.id, "stem": q.stem, "q_type": q.q_type, "full_score": q.full_score,
            "cog_level": q.cog_level, "difficulty_est": q.difficulty_est,
            "n_options": q.n_options, "sub_items": q.sub_items_json,
            "kp_id": q.kps[0].kp_id, "weight": q.kps[0].weight,
            "reviewed_at": q.kps[0].reviewed_at.isoformat(),
        })
    result["preview_token"] = hashlib.sha256(json.dumps(
        {"preview": result, "content": content}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return result


def create_diagnostic_exam(db: Session, student: Student, blueprint: dict, question_ids: list[int], exam_date: date, preview_token: str) -> ExamTemplate:
    if not date.today() <= exam_date <= date.today() + timedelta(days=90):
        raise ValueError("诊断日期须在今天至未来 90 天内")
    expected = [slot["question"]["id"] for slot in blueprint["slots"] if slot["question"]]
    if not blueprint["ready"] or question_ids != expected or preview_token != blueprint["preview_token"]:
        raise ValueError("题源不足或预览已变化，请重新预览后确认")
    exam = ExamTemplate(class_id=student.class_id, subject=blueprint["subject"],
        name=f"{student.name_or_alias}·{blueprint['kp_name']}·{'补证据' if blueprint['mode'] == 'evidence' else '归因验证'}"[:120],
        exam_date=exam_date, type="诊断", source="diagnostic_task")
    db.add(exam); db.flush()
    for idx, slot in enumerate(blueprint["slots"], 1):
        original = db.get(TemplateQuestion, slot["question"]["id"])
        q = TemplateQuestion(exam_template_id=exam.id, idx=idx, stem=original.stem,
            q_type=original.q_type, full_score=original.full_score, cog_level=original.cog_level,
            difficulty_est=original.difficulty_est, n_options=original.n_options, sub_items_json=original.sub_items_json)
        db.add(q); db.flush()
        tag = original.kps[0]
        # 复制标签仍走新卷审核，确认建卷不等于签发或提交作答。
        db.add(QuestionKp(template_question_id=q.id, kp_id=tag.kp_id, weight=tag.weight, source="教师"))
    return exam
