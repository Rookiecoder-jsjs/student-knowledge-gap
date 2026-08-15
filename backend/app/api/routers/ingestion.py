"""采集域路由：考试创建 / Excel / 手工 / 拍照解析 / 批量录入 / 审核台修正（候选2 拆分）。

文件策略在 ``ingestion.batch_upload``、批量状态机在 ``ingestion.batch``、
拍照流水线在 ``ingestion.photo``；本文件只做 UploadFile 到字节/策略翻译。
"""

from __future__ import annotations

import tempfile
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import _active_kb, _graph, get_db
from app.db import utcnow
# 批量模块经模块引用（batch_mod.X / batch_up.X）而非指名导入：测试会 monkeypatch
# batch.submit_item 等模块属性（test_photo 的 test_batch_sync_guard），
# 指名导入会把函数对象焊死在调用方，patch 失效。
from app.ingestion import batch as batch_mod
from app.ingestion import batch_upload as batch_up
from app.ingestion.commit import add_manual_response, commit_exam
from app.ingestion.excel import import_excel
from app.ingestion.photo import (
    approve_template_tags,
    parse_student_response_from_photo,
    parse_template_from_photo,
    review_queue,
)
from app.ingestion.templates import create_template
from app.kb.edit import log_correction
from app.llm.client import LLMError, get_client
from app.llm.prompts import RESPONSE_BATCH_PROMPT_VERSION
from app.models import (
    ExamResponse,
    ExamTemplate,
    KnowledgePoint,
    ParseBatchItem,
    ParseJob,
    QuestionKp,
    ResponseAnswer,
    Student,
    TemplateQuestion,
)
from app.reports.auto_generate import generate_exam_reports
from app.schemas import (
    AnswerUpdate,
    BatchAssignRequest,
    ExamCreate,
    ManualScores,
    QuestionTagsUpdate,
)

router = APIRouter()

_BATCH_TERMINAL = {"matched", "unmatched", "failed", "duplicate", "discarded"}


def _upload_error(e: batch_up.BatchUploadError) -> HTTPException:
    """上传策略异常 → HTTP 状态码：大小超限 413，其余 400。"""
    if isinstance(e, batch_up.UploadLimitError):
        return HTTPException(413, str(e))
    return HTTPException(400, str(e))


def _batch_item_error(e: ValueError) -> HTTPException:
    """批量项状态机异常 → 404（目标不存在）/ 400（非法操作）。"""
    if isinstance(e, batch_mod.BatchItemNotFound):
        return HTTPException(404, str(e))
    return HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# 考试：模板 → 导入 → 提交
# ---------------------------------------------------------------------------


@router.post("/exams")
def create_exam(req: ExamCreate, db: Session = Depends(get_db)):
    try:
        tpl = create_template(
            db,
            req.kb_version_id,
            req.class_id,
            req.name,
            req.exam_date,
            req.type,
            [q.model_dump() for q in req.questions],
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"exam_id": tpl.id, "questions": len(req.questions)}


@router.post("/exams/{exam_id}/import-excel")
async def excel_import(exam_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    if db.get(ExamTemplate, exam_id) is None:
        raise HTTPException(404, "考试不存在")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = import_excel(db, exam_id, tmp_path)
    except Exception as e:
        raise HTTPException(400, f"Excel 解析失败: {e}")
    return {
        "imported": result.imported,
        "unmatched_students": result.unmatched_students,
        "warnings": result.warnings[:50],
    }


@router.post("/exams/{exam_id}/manual")
def manual_entry(exam_id: int, req: ManualScores, db: Session = Depends(get_db)):
    try:
        resp = add_manual_response(db, exam_id, req.student_id, req.scores)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"response_id": resp.id, "total_score": resp.total_score, "status": resp.status}


@router.post("/exams/{exam_id}/commit")
def commit(exam_id: int, db: Session = Depends(get_db)):
    if db.get(ExamTemplate, exam_id) is None:
        raise HTTPException(404, "考试不存在")
    # 产品语义「提交即自动生成」：commit 只做状态机，报告生成在此显式组合（候选4）。
    result = commit_exam(db, exam_id)
    if result.committed_responses > 0:
        reports = generate_exam_reports(db, exam_id)
        result.quality_report = reports.quality
        result.diagnoses = reports.diagnoses
    return {
        "committed_responses": result.committed_responses,
        "evidence_events": result.evidence_events,
        "quality_report": result.quality_report,
        "diagnoses": result.diagnoses,
        "skipped": result.skipped[:20],
    }


# ---------------------------------------------------------------------------
# 拍照解析（两阶段；LLM 输出必经审核闸门 — 不变量③）
# ---------------------------------------------------------------------------


@router.post("/exams/photo-template")
async def photo_template(
    file: UploadFile = File(...),
    class_id: int = Form(...),
    name: str = Form(...),
    exam_date: date = Form(...),
    type: str = Form("单元"),
    db: Session = Depends(get_db),
):
    """阶段A：试卷照片 → 结构化模板 + 闭集知识点标注（source=LLM，待审核）。"""
    kb = _active_kb(db)
    image = await file.read()
    result = parse_template_from_photo(db, kb.id, class_id, name, exam_date, type, image)
    if result.exam_id is None:
        raise HTTPException(400, "; ".join(result.warnings) or "解析失败")
    return {
        "exam_id": result.exam_id,
        "parse_job_id": result.parse_job_id,
        "questions": result.questions,
        "warnings": result.warnings,
        "next": "教师审核标注后调用 POST /exams/{id}/approve-tags",
    }


@router.post("/exams/{exam_id}/photo-response")
async def photo_response(
    exam_id: int,
    student_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """阶段B：学生卷照片 → 每题得分/选项（source=photo，状态=待审核）。"""
    image = await file.read()
    try:
        result = parse_student_response_from_photo(db, exam_id, student_id, image)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result.response_id is None:
        raise HTTPException(400, "; ".join(result.warnings) or "解析失败")
    return {
        "response_id": result.response_id,
        "parse_job_id": result.parse_job_id,
        "warnings": result.warnings,
        "next": "核对低置信题目（GET /exams/{id}/review-queue）后 POST /exams/{id}/commit",
    }


@router.post("/exams/{exam_id}/approve-tags")
def approve_tags(exam_id: int, reviewer: str = "teacher", db: Session = Depends(get_db)):
    """批量确认 LLM 标注；抽样模式保留低置信/抽样题待逐题确认。"""
    approved = approve_template_tags(db, exam_id, reviewer)
    pending = len({x["question_id"] for x in review_queue(db, exam_id)["unreviewed_tags"]})
    return {"approved": approved, "pending": pending}


@router.get("/exams/{exam_id}/review-queue")
def review_queue_endpoint(exam_id: int, db: Session = Depends(get_db)):
    """异常式审核：未审标注 + 低置信得分（<0.6 强制人工，0.6~0.9 高亮）。"""
    return review_queue(db, exam_id)


# ---------------------------------------------------------------------------
# 批量拍照录入（DESIGN 批量录入 v0.3）
# 文件策略（校验/落盘/sync 判定）在 ingestion.batch_upload（候选2），本层只翻译。
# ---------------------------------------------------------------------------


@router.post("/exams/{exam_id}/photo-batch")
async def photo_batch(
    exam_id: int,
    files: list[UploadFile] = File(...),
    sync: bool = Form(False),
    db: Session = Depends(get_db),
):
    """批量上传学生卷 -> 后台解析 + 卷面姓名匹配名单（不阻塞）。"""
    if db.get(ExamTemplate, exam_id) is None:
        raise HTTPException(404, "考试不存在")
    uploads = [(f.filename or f"file_{i}.jpg", await f.read()) for i, f in enumerate(files)]
    try:
        saved = batch_up.validate_and_persist(uploads)
    except batch_up.BatchUploadError as e:
        raise _upload_error(e)
    try:
        model_version = get_client("vision").model_version
    except LLMError:
        model_version = ""

    job = ParseJob(
        target=f"batch:{exam_id}",
        model_version=model_version,
        prompt_version=RESPONSE_BATCH_PROMPT_VERSION,
        status="running",
    )
    db.add(job)
    db.flush()
    item_ids: list[int] = []
    items_out: list[dict] = []
    for file_name, tmp_path in saved:
        item = ParseBatchItem(
            parse_job_id=job.id,
            exam_template_id=exam_id,
            file_name=file_name,
            file_path=tmp_path,
            status="queued",
            warnings=[],
        )
        db.add(item)
        db.flush()
        item_ids.append(item.id)
        items_out.append({"id": item.id, "file_name": file_name, "status": "queued"})

    if batch_up.effective_sync(sync):
        for iid in item_ids:
            batch_mod.run_item_sync(iid, db)
        db.flush()
        items_out = [
            {"id": iid, "file_name": db.get(ParseBatchItem, iid).file_name,
             "status": db.get(ParseBatchItem, iid).status}
            for iid in item_ids
        ]
    else:
        db.commit()  # 先落库，worker 自开会话才能看到 item
        for iid in item_ids:
            batch_mod.submit_item(iid)
    return {"job_id": job.id, "items": items_out}


@router.get("/exams/{exam_id}/batch-jobs")
def list_batch_jobs(exam_id: int, db: Session = Depends(get_db)):
    # G6：惰性触发运行期看门狗（parsing 卡死改判 failed），教师轮询即自愈
    batch_mod.reconcile_stale_runtime()
    db.expire_all()  # 看门狗可能改了 item 状态，让后续查询重读
    jobs = db.scalars(
        select(ParseJob)
        .where(ParseJob.target == f"batch:{exam_id}")
        .order_by(ParseJob.id.desc())
    )
    out = []
    for job in jobs:
        items = list(
            db.scalars(
                select(ParseBatchItem).where(ParseBatchItem.parse_job_id == job.id)
            )
        )
        counts: dict[str, int] = {}
        for it in items:
            counts[it.status] = counts.get(it.status, 0) + 1
        total = len(items)
        done = sum(c for s, c in counts.items() if s in _BATCH_TERMINAL)
        out.append(
            {
                "job_id": job.id,
                "status": job.status,
                "counts": counts,
                "total": total,  # G12：进度（已完成/总数）
                "done": done,
            }
        )
    return {"jobs": out}


@router.get("/batch-jobs/{job_id}")
def get_batch_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ParseJob, job_id)
    if job is None:
        raise HTTPException(404, "批任务不存在")
    # G6：惰性触发运行期看门狗（parsing 超 BATCH_STALE_MINUTES 改判 failed）
    batch_mod.reconcile_stale_runtime()
    db.expire_all()  # 看门狗另开会话提交，重读 item 状态
    items = list(
        db.scalars(
            select(ParseBatchItem)
            .where(ParseBatchItem.parse_job_id == job_id)
            .order_by(ParseBatchItem.id)
        )
    )
    # 僵尸收尾：全终态则 job 置 done；job 已 done 但仍有 parsing/queued 则改判 failed
    changed = False
    if items:
        if all(it.status in _BATCH_TERMINAL for it in items):
            if job.status != "done":
                job.status = "done"
                changed = True
        elif job.status == "done":
            for it in items:
                if it.status in ("queued", "parsing"):
                    it.status = "failed"
                    it.warnings = (it.warnings or []) + ["服务中断，可重试"]
                    changed = True
    if changed:
        db.flush()
    total = len(items)
    done = sum(1 for it in items if it.status in _BATCH_TERMINAL)
    out_items = []
    for it in items:
        stu_name = None
        if it.matched_student_id:
            stu = db.get(Student, it.matched_student_id)
            stu_name = stu.name_or_alias if stu else None
        out_items.append(
            {
                "id": it.id,
                "file_name": it.file_name,
                "detected_name": it.detected_name,
                "matched_student_id": it.matched_student_id,
                "matched_student_name": stu_name,
                "status": it.status,
                "match_confidence": it.match_confidence,
                "warnings": it.warnings or [],
            }
        )
    return {
        "job_id": job.id,
        "status": job.status,
        "items": out_items,
        "total": total,  # G12：进度（已完成/总数）
        "done": done,
    }


@router.post("/batch-items/{item_id}/assign")
def assign_batch_item(item_id: int, req: BatchAssignRequest, db: Session = Depends(get_db)):
    """未匹配项指派到具体学生：用 payload_json 落库，免重调 LLM。状态机在 batch.assign_item。"""
    try:
        return batch_mod.assign_item(db, item_id, req.student_id)
    except ValueError as e:
        raise _batch_item_error(e)


@router.post("/batch-items/{item_id}/retry")
def retry_batch_item(item_id: int, sync: bool = False, db: Session = Depends(get_db)):
    """失败项重试：状态迁移在 batch.retry_item；同步/异步执行决策在调用方。"""
    try:
        item = batch_mod.retry_item(db, item_id)
    except ValueError as e:
        raise _batch_item_error(e)

    if batch_up.effective_sync(sync):
        batch_mod.run_item_sync(item.id, db)
        db.flush()
        return {"id": item.id, "status": item.status}
    db.commit()
    batch_mod.submit_item(item.id)
    return {"id": item.id, "status": "queued"}


@router.post("/batch-items/{item_id}/discard")
def discard_batch_item(item_id: int, db: Session = Depends(get_db)):
    """教师主动放弃未匹配/失败项：置 discarded，清 detected_name，删 tempfile。"""
    try:
        batch_mod.discard_item(db, item_id)
    except ValueError as e:
        raise _batch_item_error(e)
    return {"id": item_id, "status": "discarded"}


# ---------------------------------------------------------------------------
# 审核台修正（教师改动 → source=教师、留痕 CorrectionLog — 不变量③）
# ---------------------------------------------------------------------------


@router.patch("/template-questions/{question_id}/tags")
def update_question_tags(question_id: int, req: QuestionTagsUpdate, db: Session = Depends(get_db)):
    """逐题改标：替换知识点标注，闭集校验，改后即视为教师已审核。"""
    q = db.get(TemplateQuestion, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    committed = db.scalar(
        select(func.count(ExamResponse.id)).where(
            ExamResponse.exam_template_id == q.exam_template_id,
            ExamResponse.status == "已提交",
        )
    )
    if committed:
        raise HTTPException(400, "该考试已有已提交作答，改标会使已派生证据失效；请以补录考试处理")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    resolved = []
    for tag in req.kps:
        try:
            resolved.append((graph.code(tag.code), tag.weight))
        except KeyError:
            raise HTTPException(400, f"知识点编码不存在: {tag.code}")
    old_codes = sorted(
        kp.code for t in q.kps if (kp := db.get(KnowledgePoint, t.kp_id)) is not None
    )
    for tag in list(q.kps):
        db.delete(tag)
    db.flush()
    now = utcnow()
    for kp_id, weight in resolved:
        db.add(
            QuestionKp(
                template_question_id=question_id,
                kp_id=kp_id,
                weight=weight,
                source="教师",
                confidence=1.0,
                reviewed_by=req.reviewer,
                reviewed_at=now,
            )
        )
    new_codes = sorted(graph.kp(kp_id).code for kp_id, _ in resolved)
    log_correction(db, "template_question", question_id, "kps", old_codes, new_codes, req.reviewer)
    db.flush()
    return {"question_id": question_id, "kps": new_codes, "reviewed": True}


@router.patch("/response-answers/{answer_id}")
def update_answer(answer_id: int, req: AnswerUpdate, db: Session = Depends(get_db)):
    """低置信得分人工修正：仅限「待审核」作答；修正后置信度置 1。"""
    ans = db.get(ResponseAnswer, answer_id)
    if ans is None:
        raise HTTPException(404, "作答记录不存在")
    resp = db.get(ExamResponse, ans.exam_response_id)
    if resp.status != "待审核":
        raise HTTPException(400, f"作答已{resp.status}，不能再修改；如需更正请以补录处理")
    q = db.get(TemplateQuestion, ans.template_question_id)
    changes = {}
    if req.score is not None:
        if not (0 <= req.score <= q.full_score):
            raise HTTPException(400, f"得分越界：题{q.idx} 满分 {q.full_score}")
        changes["score"] = (ans.score, req.score)
        ans.score = req.score
    if req.chosen_option is not None:
        changes["chosen_option"] = (ans.chosen_option, req.chosen_option)
        ans.chosen_option = req.chosen_option
    if not changes:
        raise HTTPException(400, "未提供任何修改字段")
    ans.parse_confidence = 1.0  # 人工确认过，退出低置信队列
    resp.total_score = sum(a.score for a in resp.answers)
    for field, (old, new) in changes.items():
        log_correction(db, "response_answer", answer_id, field, old, new, req.reviewer)
    return {
        "answer_id": answer_id,
        "score": ans.score,
        "chosen_option": ans.chosen_option,
        "total_score": resp.total_score,
        "status": resp.status,
    }