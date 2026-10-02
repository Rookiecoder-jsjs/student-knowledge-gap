"""十五分钟行动卡：确定性教学步骤，例题只引用本班已有试卷。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attribution, ExamTemplate, Intervention, KnowledgePoint, QuestionKp, TemplateQuestion


# 每种行动是一组明确步骤，不通过 LLM 编造题目或答案。
_STEPS = {
    "reteach": ("找出共同误区", "重讲关键概念并示范一个完整步骤", "完成一道同类题，再改变一个条件练习", "独立完成一道检查题并解释关键步骤"),
    "prereq_backfill": ("用一个口头问题检查前置概念", "先补基础概念，再示范它如何支撑当前知识点", "先做基础练习，再回到当前题型", "独立说明基础概念与当前解题步骤的联系"),
    "spaced_review": ("不看笔记，回忆定义和关键步骤", "对照笔记修正遗漏，示范一次完整解题", "合上笔记做同类练习，再换一种表述", "独立复述步骤，记录仍需提醒的地方"),
    "contrast_practice": ("列出两个容易混淆的概念或条件", "对照适用条件与反例，强调判断依据", "交替练习两类题，每题先说明属于哪一类", "独立辨析一个边界例子并说明理由"),
    "evidence_boost": ("询问学生如何理解题意，不预先下结论", "选择两个不同角度的小问题，明确评价标准", "让学生独立作答，记录得分与解题过程", "判断是否仍需补充证据，正式成绩经录入审核后参与分析"),
    "tier_drill": ("让学生用自己的话解释概念", "示范从概念到应用的第一步，逐步撤去提示", "先做带提示练习，再独立完成同类题", "独立完成一个新情境问题并解释使用的概念"),
}


def teaching_card(session: Session, iv: Intervention) -> dict:
    kp = session.get(KnowledgePoint, iv.kp_id)
    if kp is None:
        raise LookupError("知识点不存在")
    steps = _STEPS.get(iv.kind)
    if steps is None:
        raise ValueError("该干预类型暂不支持教学行动卡")
    root = None
    if iv.attribution_id is not None:
        att = session.get(Attribution, iv.attribution_id)
        if att and att.root_kp_id:
            root = session.get(KnowledgePoint, att.root_kp_id)
    examples = list(session.execute(
        select(TemplateQuestion, ExamTemplate)
        .join(QuestionKp, QuestionKp.template_question_id == TemplateQuestion.id)
        .join(ExamTemplate, TemplateQuestion.exam_template_id == ExamTemplate.id)
        .where(QuestionKp.kp_id == iv.kp_id, ExamTemplate.class_id == iv.class_id,
               ExamTemplate.id == iv.exam_id)
        .order_by(TemplateQuestion.idx).limit(2)
    ))
    return {
        "intervention_id": iv.id, "kp_name": kp.name, "kp_code": kp.code,
        "root_kp_name": root.name if root else None,
        "duration_minutes": 15,
        "goal": f"围绕「{kp.name}」完成一次讲解、独立练习与检查，记录下一步需要验证的问题。",
        "preparation": "准备原试卷、纸笔和一道同知识点的新题；检查题不要直接重复已讲例题。",
        "steps": [{"minutes": minutes, "title": title, "instruction": instruction}
                  for minutes, title, instruction in zip(
                      (3, 5, 5, 2), ("定位问题", "讲解示范", "练习迁移", "独立检查"), steps)],
        "examples": [{"exam_id": exam.id, "exam_name": exam.name,
                      "question_idx": q.idx, "stem": q.stem} for q, exam in examples],
        "verification": "记录独立检查结果；确认已执行后默认安排 7 天后复测，也可调整日期。检查结果需作为考试作答提交，系统才能验证。",
    }
