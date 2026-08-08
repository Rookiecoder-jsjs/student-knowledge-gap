"""考试模板创建（两阶段解析的阶段 A：题目结构 + 知识点标注）。

MVP：题目与标注由教师/脚本直接提供（source=教师，confidence=1.0）。
P1 接入拍照解析后，此处将接收 LLM 草稿并进入审核台（不变量③）。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.client import LLMError, get_client
from app.llm.prompts import TAGGER_PROMPT_VERSION, TAGGER_SYSTEM, tagger_user_prompt
from app.models import (
    ExamTemplate,
    KnowledgePoint,
    QuestionKp,
    TemplateQuestion,
)


def create_template(
    session: Session,
    kb_version_id: int,
    class_id: int,
    name: str,
    exam_date: date,
    type_: str,
    questions: list[dict],
    source: str = "excel",
) -> ExamTemplate:
    """questions: [{idx, stem, q_type, full_score, cog_level?, n_options?,
    difficulty_est?, kps: [{code, weight?}]}]"""
    kp_by_code = {
        kp.code: kp.id
        for kp in session.scalars(
            select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb_version_id)
        )
    }

    tpl = ExamTemplate(
        class_id=class_id,
        name=name,
        exam_date=exam_date,
        type=type_,
        source=source,
    )
    session.add(tpl)
    session.flush()

    for q in questions:
        tq = TemplateQuestion(
            exam_template_id=tpl.id,
            idx=q["idx"],
            stem=q.get("stem", f"第{q['idx']}题"),
            q_type=q.get("q_type", "解答"),
            full_score=q["full_score"],
            cog_level=q.get("cog_level", "应用"),
            difficulty_est=q.get("difficulty_est", 0.5),
            n_options=q.get("n_options"),
        )
        session.add(tq)
        session.flush()
        for tag in q.get("kps", []):
            code = tag["code"]
            if code not in kp_by_code:
                raise ValueError(f"题目{q['idx']}标注了不存在的知识点 {code}")
            session.add(
                QuestionKp(
                    template_question_id=tq.id,
                    kp_id=kp_by_code[code],
                    weight=tag.get("weight", 1.0),
                    source="教师",
                    confidence=1.0,
                    # 教师/脚本直供的标注本身即已审核（只有 LLM 草稿才等闸门）
                    reviewed_by="teacher",
                    reviewed_at=datetime.utcnow(),
                )
            )

    session.flush()
    return tpl


def _clamp01(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def suggest_question_tags(
    session: Session, kb_version_id: int, questions: list[dict]
) -> dict:
    """题干 -> 闭集知识点推荐（improvement-plan §3.3）。

    纯文本路径：用题干让 LLM 从知识库闭集中推荐 kp + 权重。
    **不落库**--只返回推荐供教师审核，教师修改后再 create_template。
    questions: [{"idx":1, "stem":"...", "q_type":"解答"}]
    """
    kp_rows = {
        kp.code: kp
        for kp in session.scalars(
            select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb_version_id)
        )
        if not kp.code.startswith("C")
    }
    closed_set = "\n".join(f"{c} | {kp.name}" for c, kp in sorted(kp_rows.items()))
    questions_desc = "\n".join(
        f"{q['idx']}. ({q.get('q_type', '解答')}) {q.get('stem', '')}"
        for q in questions
    )
    client = get_client("text")
    warnings: list[str] = []
    try:
        payload = client.parse_json(
            TAGGER_SYSTEM, tagger_user_prompt(questions_desc, closed_set), None
        )
    except LLMError as e:
        return {
            "suggestions": [],
            "model_version": client.model_version,
            "prompt_version": TAGGER_PROMPT_VERSION,
            "warnings": [f"LLM 调用失败：{e}"],
        }

    by_idx = {q["idx"]: q for q in questions}
    suggestions: list[dict] = []
    for item in payload.get("questions", []):
        idx = item.get("idx")
        if idx not in by_idx:
            warnings.append(f"模型返回了未知题号 {idx!r}，已忽略")
            continue
        tags = []
        for tag in item.get("kp_tags", []):
            code = tag.get("code")
            if code not in kp_rows:
                warnings.append(f"题{idx}：知识点编码 {code!r} 不在闭集内，已丢弃")
                continue
            tags.append(
                {
                    "code": code,
                    "name": kp_rows[code].name,
                    "weight": _clamp01(tag.get("weight", 1.0)),
                }
            )
        suggestions.append(
            {
                "idx": idx,
                "kps": tags,
                "confidence": _clamp01(item.get("confidence", 0.5)),
            }
        )
    return {
        "suggestions": suggestions,
        "model_version": client.model_version,
        "prompt_version": TAGGER_PROMPT_VERSION,
        "warnings": warnings,
    }
