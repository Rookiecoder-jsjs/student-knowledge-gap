"""拍照解析两阶段流水线（DESIGN §5）。

阶段A 试卷模板（每次考试 1 次）：图片 → LLM → 题目结构 + 闭集知识点标注
      → 全部落为 source=LLM、带 confidence，等待教师审核（不变量③）。
阶段B 学生卷（每人 1 次）：匹配已有模板 → 仅抽每题得分/选项
      → response_answer（带 parse_confidence），状态=待审核。

置信度三级路由（DESIGN §5）：≥0.9 自动通过；0.6~0.9 高亮提醒；<0.6 强制人工。
每次调用记录 parse_job（model_version + prompt_version，默认不重跑）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import TAG_REVIEW_SAMPLE_RATE
from app.ingestion.pii import mask_image
from app.llm.client import LLMError, get_client
from app.llm.prompts import (
    PROMPT_VERSION,
    RESPONSE_SYSTEM,
    TEMPLATE_SYSTEM,
    response_user_prompt,
    template_user_prompt,
)
from app.models import (
    ExamResponse,
    ExamTemplate,
    KnowledgePoint,
    ParseJob,
    QuestionKp,
    ResponseAnswer,
    Student,
    TemplateQuestion,
)

Q_TYPES = {"选择", "填空", "解答"}
COG_LEVELS = {"识记", "理解", "应用", "综合"}

AUTO_PASS = 0.9
NEEDS_HIGHLIGHT = 0.6


@dataclass
class PhotoParseResult:
    parse_job_id: int | None = None
    exam_id: int | None = None
    response_id: int | None = None
    questions: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 阶段A：试卷模板
# ---------------------------------------------------------------------------


def parse_template_from_photo(
    session: Session,
    kb_version_id: int,
    class_id: int,
    name: str,
    exam_date: date,
    type_: str,
    image_bytes: bytes,
) -> PhotoParseResult:
    client = get_client("vision")
    job = ParseJob(
        target=f"template:{name}",
        model_version=client.model_version,
        prompt_version=PROMPT_VERSION,
        status="running",
    )
    session.add(job)
    session.flush()
    result = PhotoParseResult(parse_job_id=job.id)

    kp_rows = {
        kp.code: kp
        for kp in session.scalars(
            select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb_version_id)
        )
        if not kp.code.startswith("C")
    }
    closed_set = "\n".join(f"{c} | {kp.name}" for c, kp in sorted(kp_rows.items()))

    try:
        payload = client.parse_json(TEMPLATE_SYSTEM, template_user_prompt(closed_set), image_bytes)
    except LLMError as e:
        job.status = "failed"
        session.flush()
        result.warnings.append(f"LLM 调用失败：{e}")
        return result

    questions = payload.get("questions", [])
    if not questions:
        job.status = "failed"
        session.flush()
        result.warnings.append("模型未返回任何题目")
        return result

    tpl = ExamTemplate(
        class_id=class_id,
        name=name,
        exam_date=exam_date,
        type=type_,
        source="photo",
        parse_job_id=job.id,
    )
    session.add(tpl)
    session.flush()
    result.exam_id = tpl.id

    seen_idx: set[int] = set()
    for i, q in enumerate(questions, start=1):
        warnings, idx = _validate_question(q, seen_idx, i, result)
        seen_idx.add(idx)
        conf = _conf(q.get("confidence"), 0.5)

        tq = TemplateQuestion(
            exam_template_id=tpl.id,
            idx=idx,
            stem=str(q.get("stem", ""))[:200],
            q_type=q.get("q_type") if q.get("q_type") in Q_TYPES else "解答",
            full_score=_safe_score(q.get("full_score"), result, idx),
            cog_level=q.get("cog_level") if q.get("cog_level") in COG_LEVELS else "应用",
            n_options=q.get("n_options") if isinstance(q.get("n_options"), int) else None,
        )
        session.add(tq)
        session.flush()

        # 闭集校验：只接受知识库内存在的编码（不变量③的 Schema 侧）
        tags = q.get("kp_tags") or []
        accepted = 0
        for tag in tags:
            code = tag.get("code")
            if code not in kp_rows:
                warnings.append(f"题{idx}：知识点编码 {code!r} 不在知识库闭集内，已丢弃")
                continue
            session.add(
                QuestionKp(
                    template_question_id=tq.id,
                    kp_id=kp_rows[code].id,
                    weight=_clamp01(tag.get("weight", 1.0)),
                    source="LLM",
                    confidence=conf,
                )
            )
            accepted += 1
        if accepted == 0:
            warnings.append(f"题{idx}：无有效知识点标注，需教师补标")
        result.questions += 1

    job.status = "done"
    session.flush()
    result.warnings.extend(warnings)
    return result


def _validate_question(q: dict, seen: set[int], fallback_idx: int, result: PhotoParseResult):
    warnings: list[str] = []
    idx = q.get("idx")
    if not isinstance(idx, int) or idx <= 0 or idx in seen:
        warnings.append(f"第{fallback_idx}个题目题号非法({idx!r})，改用 {fallback_idx}")
        idx = fallback_idx
    return warnings, idx


def _safe_score(raw, result: PhotoParseResult, idx: int) -> float:
    try:
        score = float(raw)
        if score <= 0:
            raise ValueError
        return score
    except (TypeError, ValueError):
        result.warnings.append(f"题{idx}：满分非法({raw!r})，按 10 分处理，需教师核对")
        return 10.0


def _conf(raw, default: float) -> float:
    try:
        return _clamp01(float(raw))
    except (TypeError, ValueError):
        return default


def _clamp01(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# 阶段B：学生卷得分抽取
# ---------------------------------------------------------------------------


def parse_student_response_from_photo(
    session: Session,
    template_id: int,
    student_id: int,
    image_bytes: bytes,
) -> PhotoParseResult:
    template = session.get(ExamTemplate, template_id)
    if template is None:
        raise ValueError(f"exam_template {template_id} 不存在")

    client = get_client("vision")
    job = ParseJob(
        target=f"response:{template_id}:{student_id}",
        model_version=client.model_version,
        prompt_version=PROMPT_VERSION,
        status="running",
    )
    session.add(job)
    session.flush()
    result = PhotoParseResult(parse_job_id=job.id)

    questions = sorted(template.questions, key=lambda q: q.idx)
    desc = "\n".join(
        f"题{q.idx}：{q.q_type}，满分 {q.full_score:g}" for q in questions
    )
    # PII 剥离前置：遮盖姓名栏后再送模型
    masked = mask_image(image_bytes)

    try:
        payload = client.parse_json(RESPONSE_SYSTEM, response_user_prompt(desc), masked)
    except LLMError as e:
        job.status = "failed"
        session.flush()
        result.warnings.append(f"LLM 调用失败：{e}")
        return result

    existing = session.scalar(
        select(ExamResponse.id).where(
            ExamResponse.exam_template_id == template_id,
            ExamResponse.student_id == student_id,
        )
    )
    if existing is not None:
        job.status = "failed"
        session.flush()
        result.warnings.append("该生已有本场考试的作答记录")
        return result

    _persist_response_from_payload(session, template, student_id, payload, result)
    job.status = "done"
    session.flush()
    return result


def _persist_response_from_payload(
    session: Session,
    template: ExamTemplate,
    student_id: int,
    payload: dict,
    result: PhotoParseResult,
) -> ExamResponse:
    """从 LLM payload 落库 ExamResponse + ResponseAnswer（单张/批量/指派共用）。

    调用方负责并发去重：本函数直接 flush ExamResponse，uq_tpl_student 触发的
    IntegrityError 向上抛出，由调用方 try/except 判定 duplicate（不靠先查后建）。
    """
    questions = sorted(template.questions, key=lambda q: q.idx)
    response = ExamResponse(
        exam_template_id=template.id,
        student_id=student_id,
        total_score=0.0,
        source="photo",
        status="待审核",
    )
    session.add(response)
    session.flush()  # IntegrityError(uq_tpl_student) 在此抛出
    result.response_id = response.id

    answers = {a.get("idx"): a for a in payload.get("answers", []) if isinstance(a, dict)}

    total = 0.0
    for q in questions:
        a = answers.get(q.idx)
        if a is None:
            result.warnings.append(f"题{q.idx}：模型未返回该题，按 0 分记录，需教师核对")
            score, conf, option = 0.0, 0.0, None
        else:
            score = _clamp_score(a.get("score"), q.full_score, q.idx, result)
            conf = _conf(a.get("confidence"), 0.5)
            option = a.get("chosen_option") if q.q_type == "选择" else None
            if q.q_type == "选择" and not option:
                result.warnings.append(f"题{q.idx}：选择题缺少所选选项（迷思分析将受限）")
        total += score
        session.add(
            ResponseAnswer(
                exam_response_id=response.id,
                template_question_id=q.id,
                score=score,
                chosen_option=str(option)[:10] if option else None,
                parse_confidence=conf,
            )
        )

    response.total_score = round(total, 2)
    session.flush()
    return response


def _clamp_score(raw, full: float, idx: int, result: PhotoParseResult) -> float:
    try:
        score = float(raw)
    except (TypeError, ValueError):
        result.warnings.append(f"题{idx}：得分非法({raw!r})，按 0 分记录")
        return 0.0
    if score < 0 or score > full:
        result.warnings.append(f"题{idx}：得分 {score} 越界（满分 {full:g}），已裁剪")
        score = max(0.0, min(full, score))
    return score


# ---------------------------------------------------------------------------
# 审核支持
# ---------------------------------------------------------------------------


def review_queue(session: Session, template_id: int) -> dict:
    """异常式审核队列：未审标注 + 低置信得分（三级路由）。"""
    unreviewed_tags = []
    for tq in session.scalars(
        select(TemplateQuestion).where(TemplateQuestion.exam_template_id == template_id)
    ):
        for qk in tq.kps:
            if qk.reviewed_at is None:
                kp = session.get(KnowledgePoint, qk.kp_id)
                if qk.confidence < AUTO_PASS:
                    reason = "低置信标注"
                elif TAG_REVIEW_SAMPLE_RATE > 0 and _tag_sampled(
                    template_id, tq.id, TAG_REVIEW_SAMPLE_RATE
                ):
                    reason = "高置信抽样"
                else:
                    reason = "待批量批准"
                unreviewed_tags.append(
                    {
                        "question_idx": tq.idx,
                        "question_id": tq.id,
                        "stem": tq.stem,
                        "kp_id": qk.kp_id,
                        "kp_code": kp.code if kp else "",
                        "kp_name": kp.name if kp else "",
                        "confidence": qk.confidence,
                        "source": qk.source,
                        "review_reason": reason,
                    }
                )
    low_confidence_answers = []
    for resp in session.scalars(
        select(ExamResponse).where(ExamResponse.exam_template_id == template_id)
    ):
        stu = session.get(Student, resp.student_id)
        for ans in resp.answers:
            if ans.parse_confidence < AUTO_PASS:
                tq = session.get(TemplateQuestion, ans.template_question_id)
                low_confidence_answers.append(
                    {
                        "answer_id": ans.id,
                        "student_id": resp.student_id,
                        "student_name": stu.name_or_alias if stu else f"学生{resp.student_id}",
                        "question_idx": tq.idx,
                        "score": ans.score,
                        "full_score": tq.full_score,
                        "confidence": ans.parse_confidence,
                        "band": "强制人工" if ans.parse_confidence < NEEDS_HIGHLIGHT else "高亮提醒",
                    }
                )
    return {
        "unreviewed_tags": unreviewed_tags,
        "low_confidence_answers": low_confidence_answers,
    }


def _tag_sampled(template_id: int, question_id: int, rate: float) -> bool:
    """按题稳定抽样；同一题的多个 kp 标签始终一起抽中/放行。"""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(f"{template_id}:{question_id}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") / 2**64
    return bucket < rate


def approve_template_tags(session: Session, template_id: int, reviewer: str = "teacher") -> int:
    """批量确认模板标注；抽样模式下低置信和抽样题保留待逐题确认。

    `SC_TAG_REVIEW_SAMPLE_RATE=0`（默认）保持历史的一键全批行为；大于 0 时，
    confidence<0.9 的标注不批量通过，高置信题按稳定哈希抽样保留。
    教师在审核台逐题保存（PATCH tags）后，才会真正落闸。
    """
    n = 0
    now = datetime.utcnow()
    for tq in session.scalars(
        select(TemplateQuestion).where(TemplateQuestion.exam_template_id == template_id)
    ):
        sampled = _tag_sampled(template_id, tq.id, TAG_REVIEW_SAMPLE_RATE)
        for qk in tq.kps:
            if qk.reviewed_at is not None:
                continue
            if TAG_REVIEW_SAMPLE_RATE > 0 and (
                qk.confidence < AUTO_PASS or sampled
            ):
                continue
            qk.reviewed_by = reviewer
            qk.reviewed_at = now
            n += 1
    session.flush()
    return n
