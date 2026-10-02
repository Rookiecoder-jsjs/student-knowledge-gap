"""提交前质量预检；阻断结构/分数错误，提示需教师判断的风险。"""

from __future__ import annotations

import math
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import ExamResponse, ExamTemplate, Student, TemplateQuestion


def exam_preflight(session: Session, exam_id: int) -> dict:
    template = session.get(ExamTemplate, exam_id)
    if template is None:
        raise LookupError("考试不存在")
    questions = list(session.scalars(
        select(TemplateQuestion).where(TemplateQuestion.exam_template_id == exam_id)
        .options(selectinload(TemplateQuestion.kps)).order_by(TemplateQuestion.idx)
    ))
    responses = list(session.scalars(
        select(ExamResponse).where(ExamResponse.exam_template_id == exam_id)
        .options(selectinload(ExamResponse.answers)).order_by(ExamResponse.id)
    ))
    roster = {s.id: s.name_or_alias for s in session.scalars(
        select(Student).where(Student.class_id == template.class_id)
    )}
    issues: list[dict] = []

    def issue(code, severity, message, response=None, question=None):
        issues.append({
            "code": code, "severity": severity, "message": message,
            "response_id": response.id if response else None,
            "student_id": response.student_id if response else None,
            "alias": roster.get(response.student_id) if response else None,
            "question_idx": question.idx if question else None,
        })

    by_id = {q.id: q for q in questions}
    if not questions:
        issue("no_questions", "blocking", "试卷尚无题目，请先完善模板")
    for q in questions:
        if not math.isfinite(q.full_score) or q.full_score <= 0:
            issue("invalid_full_score", "blocking", f"第 {q.idx} 题满分必须为正数", question=q)
        if not q.kps:
            issue("untagged_question", "warning", f"第 {q.idx} 题未标知识点，不会进入知识点分析", question=q)
        elif any(k.reviewed_by is None for k in q.kps):
            issue("unreviewed_tags", "warning", f"第 {q.idx} 题知识点标注尚未审核", question=q)

    pending = [r for r in responses if r.status != "已提交"]
    duplicates = Counter(r.student_id for r in responses)
    for r in pending:
        if r.student_id not in roster:
            issue("student_mismatch", "blocking", "作答学生不属于本班，请核对学生匹配", r)
        if duplicates[r.student_id] > 1:
            issue("duplicate_response", "blocking", "该生有重复作答记录", r)
        if r.status != "待审核":
            issue("not_ready", "blocking", f"作答仍处于「{r.status}」，请等待解析完成或处理失败记录", r)
        for warning in r.source_warnings_json or []:
            issue("source_warning", "warning", warning, r)
        ids = Counter(a.template_question_id for a in r.answers)
        for q in questions:
            if ids[q.id] == 0:
                issue("missing_answer", "blocking", f"缺少第 {q.idx} 题得分；零分也需明确录入", r, q)
            elif ids[q.id] > 1:
                issue("duplicate_answer", "blocking", f"第 {q.idx} 题有重复得分", r, q)
        valid = True
        for a in r.answers:
            q = by_id.get(a.template_question_id)
            if q is None:
                issue("foreign_question", "blocking", "作答引用了其他试卷的题目", r)
                valid = False
                continue
            if not math.isfinite(a.score) or not 0 <= a.score <= q.full_score:
                issue("score_out_of_range", "blocking", f"第 {q.idx} 题得分越界（满分 {q.full_score}）", r, q)
                valid = False
            if a.parse_confidence < 0.9:
                severity = "blocking" if a.parse_confidence < 0.6 else "warning"
                issue("low_confidence", severity, f"第 {q.idx} 题得分尚需人工核对，请在审核页确认", r, q)
        if valid and (not math.isfinite(r.total_score) or abs(r.total_score - sum(a.score for a in r.answers)) > 0.011):
            issue("total_mismatch", "blocking", "总分与逐题得分之和不一致，请核对并重新确认一道题的得分以重算总分", r, questions[0] if questions else None)
    missing = len(set(roster) - {r.student_id for r in responses})
    if missing:
        issue("uncollected", "warning", f"还有 {missing} 名学生未采集，本次仅提交已录入作答")
    blocking = sum(i["severity"] == "blocking" for i in issues)
    return {
        "exam_id": exam_id, "ready": blocking == 0,
        "pending_responses": sum(r.status == "待审核" for r in pending),
        "blocking_count": blocking,
        "warning_count": len(issues) - blocking,
        "issues": issues,
    }
