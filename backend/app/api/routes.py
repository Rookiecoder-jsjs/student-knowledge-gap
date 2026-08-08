"""教师端最小 API：知识库 → 班级 → 考试导入 → 提交 → 分析 → 报告。

依赖注入统一走 get_db；分析类接口全部 derive-on-read（不变量②）。
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from datetime import date, datetime, time

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from PIL import Image
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.ingestion.commit import add_manual_response, commit_exam
from app.ingestion.excel import import_excel
from app.ingestion.photo import PhotoParseResult, _persist_response_from_payload
from app.ingestion.templates import create_template
from app.kb.graph import KpGraph
from app.kb.loader import KbImportError, import_kb
from app.llm.client import LLMError, MockLLMClient, get_client
from app.llm.prompts import RESPONSE_BATCH_PROMPT_VERSION
from app.models import (
    Attribution,
    Class,
    CorrectionLog,
    ExamResponse,
    ExamTemplate,
    EvidenceEvent,
    KbVersion,
    KnowledgePoint,
    KpRelation,
    ParseBatchItem,
    ParseJob,
    QuestionKp,
    Report,
    ResponseAnswer,
    School,
    Student,
    TeachingProgress,
    TemplateQuestion,
)
from app.pipeline.attribution import run_attribution_for_student
from app.pipeline.mastery import mastery_at
from app.pipeline.weakness import assess_student_kps
from app.reports.quality_analysis import generate_quality_analysis
from app.reports.student_diagnosis import generate_student_diagnosis
from app.schemas import (
    AnswerUpdate,
    AttributionOverride,
    BatchAssignRequest,
    ClassCreate,
    ExamCreate,
    KbImportRequest,
    KpCreateRequest,
    KpUpdateRequest,
    ManualScores,
    ProgressPatchRequest,
    ProgressUpdate,
    QuestionTagsUpdate,
    RelationCreateRequest,
    RelationUpdateRequest,
    SchoolCreate,
    SuggestQuestionRequest,
    KbVersionPatchRequest,
)

router = APIRouter()


def get_db():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _graph(session: Session, kb_version_id: int) -> KpGraph:
    return KpGraph(session, kb_version_id)


def _active_kb(session: Session) -> KbVersion:
    # 优先取 status=active 的最新版本；无 active 时兜底取最新（老库升级过渡期，
    # 避免分析层全 500）。注：§4.5 fork 草稿场景下，fork 的 draft 不应靠兜底成为
    # active——届时需收紧为仅 active 取，兜底仅留给迁移期。
    kb = session.scalar(
        select(KbVersion)
        .where(KbVersion.status == "active")
        .order_by(KbVersion.id.desc())
    )
    if kb is None:
        if os.environ.get("SC_KB_STRICT_ACTIVE", "").lower() in ("1", "true", "yes"):
            raise HTTPException(
                400,
                "无审核通过(active)的知识库版本，请先审核并激活"
                "（SC_KB_STRICT_ACTIVE 已开启）",
            )
        kb = session.scalar(select(KbVersion).order_by(KbVersion.id.desc()))
        if kb is not None and kb.status != "active":
            logging.getLogger(__name__).warning(
                "分析层兜底使用未激活的知识库版本(id=%d, status=%s, %s v%s)，"
                "该版本未经教研审核，归因结果需谨慎核对（improvement-plan §2.1）",
                kb.id,
                kb.status,
                kb.textbook_edition,
                kb.version,
            )
    if kb is None:
        raise HTTPException(400, "尚未导入知识库，请先 POST /kb/import")
    return kb


def _as_dt(d: date | None) -> datetime:
    # 默认=本地今日结束：occurred_at 为考试日 naive 本地时间（中午 12:00），
    # 若用 utcnow() 东八区当天证据会被当成"未来"而漏过证据门槛。
    return datetime.combine(d, time(23, 59)) if d else datetime.combine(
        datetime.now().date(), time(23, 59)
    )


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/kb/import")
def kb_import(req: KbImportRequest, db: Session = Depends(get_db)):
    try:
        kb = import_kb(db, req.yaml_path)
    except (KbImportError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    return {"kb_version_id": kb.id, "status": kb.status, "version": kb.version}


# ---------------------------------------------------------------------------
# 组织
# ---------------------------------------------------------------------------


@router.post("/schools")
def create_school(req: SchoolCreate, db: Session = Depends(get_db)):
    school = School(name=req.name)
    db.add(school)
    db.flush()
    return {"school_id": school.id}


@router.post("/schools/{school_id}/classes")
def create_class(school_id: int, req: ClassCreate, db: Session = Depends(get_db)):
    if db.get(School, school_id) is None:
        raise HTTPException(404, "学校不存在")
    clazz = Class(school_id=school_id, name=req.name, grade=req.grade, subject=req.subject)
    db.add(clazz)
    db.flush()
    student_ids = []
    for alias in req.student_aliases:
        stu = Student(
            school_id=school_id, class_id=clazz.id, name_or_alias=alias
        )
        db.add(stu)
        db.flush()
        student_ids.append(stu.id)
    return {"class_id": clazz.id, "student_ids": student_ids}


@router.post("/classes/{class_id}/progress")
def update_progress(class_id: int, req: ProgressUpdate, db: Session = Depends(get_db)):
    kb = _active_kb(db)
    graph = _graph(db, kb.id)

    added = 0
    for code in req.kp_codes:
        try:
            kp_id = graph.code(code)
        except KeyError:
            raise HTTPException(400, f"知识点编码不存在: {code}")
        if getattr(graph.kp(kp_id), "archived", False):
            raise HTTPException(400, f"知识点 {code} 已归档，不可标记教学进度")
        exists = db.scalar(
            select(TeachingProgress.id).where(
                TeachingProgress.class_id == class_id,
                TeachingProgress.kp_id == kp_id,
            )
        )
        if exists is None:
            db.add(
                TeachingProgress(class_id=class_id, kp_id=kp_id, taught_at=req.taught_at)
            )
            added += 1
    return {"added": added}


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
    suffix = ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
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
    result = commit_exam(db, exam_id)
    return {
        "committed_responses": result.committed_responses,
        "evidence_events": result.evidence_events,
        "skipped": result.skipped[:20],
    }


# ---------------------------------------------------------------------------
# 分析（derive-on-read）
# ---------------------------------------------------------------------------


@router.get("/students/{student_id}/mastery")
def student_mastery(student_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    when = _as_dt(as_of)
    out = []
    for kp_id in graph.grade7_kp_ids():
        kp = graph.kp(kp_id)
        m = mastery_at(db, student_id, kp_id, when)
        if m is not None:
            out.append({"code": kp.code, "name": kp.name, "mastery": round(m, 3)})
    return {"student_id": student_id, "as_of": str(when.date()), "mastery": out}


@router.get("/students/{student_id}/weaknesses")
def student_weaknesses(student_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    stu = db.get(Student, student_id)
    if stu is None:
        raise HTTPException(404, "学生不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    when = _as_dt(as_of)
    assessments = assess_student_kps(db, graph, student_id, stu.class_id, when)
    return {
        "student_id": student_id,
        "as_of": str(when.date()),
        "weak": [
            {
                "code": a.kp_code,
                "name": a.kp_name,
                "mastery": round(a.mastery, 3) if a.mastery is not None else None,
                "criterion": a.weak_criterion,
                "evidence_count": a.evidence_count,
                "trajectory": a.trajectory,
                "stale": a.stale,
                "class_common": a.is_class_common,
            }
            for a in assessments
            if a.is_weak
        ],
        "gates": {
            "未学到": sum(1 for a in assessments if a.gate == "未学到"),
            "数据不足": sum(1 for a in assessments if a.gate == "数据不足"),
        },
    }


@router.post("/students/{student_id}/attributions")
def run_attributions(student_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    stu = db.get(Student, student_id)
    if stu is None:
        raise HTTPException(404, "学生不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    when = _as_dt(as_of)
    active = run_attribution_for_student(db, graph, student_id, stu.class_id, when)
    return {
        "student_id": student_id,
        "attributions": [
            {
                "id": a.id,
                "kp": graph.kp(a.kp_id).name,
                "type": a.type,
                "confidence": a.confidence,
                "root_kp": graph.kp(a.root_kp_id).name if a.root_kp_id else None,
                "prediction": a.prediction,
                "status": a.status,
            }
            for a in active
        ],
    }


# ---------------------------------------------------------------------------
# 报告（数字模板注入，物化留档）
# ---------------------------------------------------------------------------


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
    from app.ingestion.photo import parse_template_from_photo

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
    from app.ingestion.photo import parse_student_response_from_photo

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
    from app.ingestion.photo import approve_template_tags, review_queue

    approved = approve_template_tags(db, exam_id, reviewer)
    pending = len({x["question_id"] for x in review_queue(db, exam_id)["unreviewed_tags"]})
    return {"approved": approved, "pending": pending}


@router.get("/exams/{exam_id}/review-queue")
def review_queue_endpoint(exam_id: int, db: Session = Depends(get_db)):
    """异常式审核：未审标注 + 低置信得分（<0.6 强制人工，0.6~0.9 高亮）。"""
    from app.ingestion.photo import review_queue

    return review_queue(db, exam_id)


# ---------------------------------------------------------------------------
# 批量拍照录入（DESIGN 批量录入 v0.3）
# ---------------------------------------------------------------------------

_BATCH_MAX_FILES = 50
_BATCH_MAX_FILE_BYTES = 10 * 1024 * 1024
_BATCH_MAX_TOTAL_BYTES = 100 * 1024 * 1024

_BATCH_TERMINAL = {"matched", "unmatched", "failed", "duplicate", "discarded"}


def _effective_sync(sync: bool) -> bool:
    """sync=true 仅在 mock / 显式开关生效，避免生产同步阻塞请求线程数十分钟。"""
    if not sync:
        return False
    if os.environ.get("SC_LLM_PROVIDER", "mock").lower() == "mock":
        return True
    try:
        if isinstance(get_client("vision"), MockLLMClient):
            return True
    except LLMError:
        pass
    return bool(settings.allow_sync_batch)


def _cleanup_saved(saved: list[tuple[str, str]]) -> None:
    for _, p in saved:
        try:
            os.remove(p)
        except OSError:
            pass


async def _validate_and_persist(files: list[UploadFile]) -> list[tuple[str, str]]:
    """校验图片类型/大小/数量，落 delete=False tempfile。返回 [(file_name, tmp_path)]。"""
    if not files:
        raise HTTPException(400, "未上传任何文件")
    if len(files) > _BATCH_MAX_FILES:
        raise HTTPException(400, f"单批最多 {_BATCH_MAX_FILES} 张，本次 {len(files)} 张")
    saved: list[tuple[str, str]] = []
    try:
        total = 0
        for f in files:
            raw = await f.read()
            if len(raw) > _BATCH_MAX_FILE_BYTES:
                raise HTTPException(413, f"文件 {f.filename} 超过单文件 10MB 上限")
            total += len(raw)
            if total > _BATCH_MAX_TOTAL_BYTES:
                raise HTTPException(413, "整批超过 100MB 上限")
            try:
                Image.open(io.BytesIO(raw)).verify()
            except Exception:
                raise HTTPException(400, f"文件 {f.filename} 不是有效图片或已损坏")
            # verify 会重置流，重新打开并统一为 JPEG（匹配 LLM 客户端 image/jpeg 媒体类型）
            img = Image.open(io.BytesIO(raw))
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            # G6：统一 sc_batch_ 前缀，便于孤儿文件清扫（batch.gc_orphan_tempfiles）
            from app.ingestion.batch import TEMPFILE_PREFIX

            tmp = tempfile.NamedTemporaryFile(
                delete=False, prefix=TEMPFILE_PREFIX, suffix=".jpg"
            )
            tmp.write(buf.getvalue())
            tmp.close()
            saved.append(((f.filename or f"file_{len(saved)}.jpg")[:200], tmp.name))
        return saved
    except Exception:
        _cleanup_saved(saved)
        raise


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
    saved = await _validate_and_persist(files)
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

    from app.ingestion import batch as batch_mod

    if _effective_sync(sync):
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
    from app.ingestion.batch import reconcile_stale_runtime

    reconcile_stale_runtime()
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
    from app.ingestion.batch import reconcile_stale_runtime

    reconcile_stale_runtime()
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
    """未匹配项指派到具体学生：用 payload_json 落库，免重调 LLM。"""
    from app.ingestion.batch import _safe_remove

    item = db.get(ParseBatchItem, item_id)
    if item is None:
        raise HTTPException(404, "批量项不存在")
    if item.status != "unmatched":
        raise HTTPException(400, f"仅未匹配项可指派，当前状态 {item.status}")
    payload = item.payload_json or {}
    if not payload.get("answers"):
        raise HTTPException(400, "该项无有效作答数据，无法指派")
    template = db.get(ExamTemplate, item.exam_template_id)
    student = db.get(Student, req.student_id)
    if student is None or (template is None) or student.class_id != template.class_id:
        raise HTTPException(400, "学生不属于该班级")

    result = PhotoParseResult()
    try:
        nested = db.begin_nested()
        response = _persist_response_from_payload(db, template, req.student_id, payload, result)
        nested.commit()
        item.status = "matched"
        item.matched_student_id = req.student_id
        item.match_confidence = None  # 人工指派，非算法匹配
        item.response_id = response.id
        item.detected_name = None
        item.warnings = (item.warnings or []) + result.warnings
        db.flush()
    except IntegrityError:
        nested.rollback()
        existing = db.scalar(
            select(ExamResponse.id).where(
                ExamResponse.exam_template_id == item.exam_template_id,
                ExamResponse.student_id == req.student_id,
            )
        )
        item.status = "duplicate"
        item.matched_student_id = req.student_id
        item.response_id = existing
        item.detected_name = None
        item.warnings = (item.warnings or []) + ["该生本场已有作答（重复上传）"]
        db.flush()

    fp = item.file_path
    item.file_path = None
    db.flush()
    _safe_remove(fp)
    return {"response_id": item.response_id, "status": item.status}


@router.post("/batch-items/{item_id}/retry")
def retry_batch_item(item_id: int, sync: bool = False, db: Session = Depends(get_db)):
    """失败项重试：校验 tempfile 仍在 -> 重置 queued -> 重跑。"""
    item = db.get(ParseBatchItem, item_id)
    if item is None:
        raise HTTPException(404, "批量项不存在")
    if item.status != "failed":
        raise HTTPException(400, f"仅失败项可重试，当前状态 {item.status}")
    if not item.file_path or not os.path.exists(item.file_path):
        raise HTTPException(400, "原始文件已不存在，请重新上传")
    item.status = "queued"
    item.warnings = []
    item.detected_name = None
    db.flush()

    from app.ingestion import batch as batch_mod

    if _effective_sync(sync):
        batch_mod.run_item_sync(item.id, db)
        db.flush()
        return {"id": item.id, "status": item.status}
    db.commit()
    batch_mod.submit_item(item.id)
    return {"id": item.id, "status": "queued"}


@router.post("/batch-items/{item_id}/discard")
def discard_batch_item(item_id: int, db: Session = Depends(get_db)):
    """教师主动放弃未匹配/失败项：置 discarded，清 detected_name，删 tempfile。"""
    from app.ingestion.batch import _safe_remove

    item = db.get(ParseBatchItem, item_id)
    if item is None:
        raise HTTPException(404, "批量项不存在")
    if item.status not in ("unmatched", "failed"):
        raise HTTPException(400, f"仅未匹配/失败项可丢弃，当前状态 {item.status}")
    fp = item.file_path
    item.status = "discarded"
    item.detected_name = None
    item.file_path = None
    db.flush()
    _safe_remove(fp)
    return {"id": item.id, "status": "discarded"}


@router.get("/classes/{class_id}/quality-report")
def quality_report(
    class_id: int, exam_id: int, narrative: bool = False, db: Session = Depends(get_db)
):
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    try:
        report = generate_quality_analysis(db, graph, class_id, exam_id, narrative=narrative)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"report_id": report.id, "markdown": report.content_markdown}


@router.get("/students/{student_id}/diagnosis")
def diagnosis(
    student_id: int,
    as_of: date | None = None,
    narrative: bool = False,
    db: Session = Depends(get_db),
):
    if db.get(Student, student_id) is None:
        raise HTTPException(404, "学生不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    # 诊断前先刷新归因（derive-on-read 的归因落库）
    stu = db.get(Student, student_id)
    run_attribution_for_student(db, graph, student_id, stu.class_id, _as_dt(as_of))
    report = generate_student_diagnosis(
        db, graph, student_id, _as_dt(as_of), narrative=narrative
    )
    return {"report_id": report.id, "markdown": report.content_markdown}


# ---------------------------------------------------------------------------
# 列表与回读（前端导航依赖）
# ---------------------------------------------------------------------------


def _kp_brief(k: KnowledgePoint) -> dict:
    """知识点完整字段（浏览 / 前端详情用）。"""
    return {
        "id": k.id,
        "code": k.code,
        "name": k.name,
        "description": k.description,
        "grade": k.grade,
        "semester": k.semester,
        "chapter": k.chapter,
        "cog_levels_expected": k.cog_levels_expected or [],
        "difficulty_prior": k.difficulty_prior,
        "mastery_floor": k.mastery_floor,
        "importance": k.importance,
        "archived": k.archived,
    }


def _kp_node(graph: KpGraph, kid: int) -> dict:
    """关系端点的极简节点视图（按主键回查，跨版本兜底）。"""
    k = graph.kp(kid)
    return {"id": kid, "code": k.code, "name": k.name}


@router.get("/kb/versions")
def list_kb_versions(db: Session = Depends(get_db)):
    """列全部知识库版本（kb-edit §4.1）。"""
    rows = db.scalars(select(KbVersion).order_by(KbVersion.id.desc()))
    out = []
    for kb in rows:
        kp_count = db.scalar(
            select(func.count(KnowledgePoint.id)).where(
                KnowledgePoint.kb_version_id == kb.id
            )
        )
        out.append(
            {
                "id": kb.id,
                "subject": kb.subject,
                "textbook_edition": kb.textbook_edition,
                "version": kb.version,
                "status": kb.status,
                "created_at": kb.created_at.isoformat() if kb.created_at else None,
                "kp_count": kp_count or 0,
                "is_active": kb.status == "active",
            }
        )
    return {"versions": out}


@router.get("/kb/kps")
def list_kps(kb_version_id: int | None = None, db: Session = Depends(get_db)):
    """知识库全部知识点（完整字段）。缺省取 active；?kb_version_id= 查指定版本。

    供向导进度勾选 / 审核台闭集选择器 / 知识库浏览页使用。
    """
    if kb_version_id is not None:
        kb = db.get(KbVersion, kb_version_id)
        if kb is None:
            raise HTTPException(404, "知识库版本不存在")
    else:
        kb = _active_kb(db)
    rows = db.scalars(
        select(KnowledgePoint)
        .where(KnowledgePoint.kb_version_id == kb.id)
        .order_by(KnowledgePoint.code)
    )
    return {"kb_version_id": kb.id, "kps": [_kp_brief(k) for k in rows]}


@router.get("/kb/kps/{kp_id}")
def kp_detail(kp_id: int, db: Session = Depends(get_db)):
    """单知识点详情：属性 + 前置链 + 直接前置 + 后继 + contains 关系（kb-edit §4.1）。"""
    kp = db.get(KnowledgePoint, kp_id)
    if kp is None:
        raise HTTPException(404, "知识点不存在")
    kb = db.get(KbVersion, kp.kb_version_id)
    graph = _graph(db, kb.id)
    version_kp_ids = set(graph._kp.keys())  # 本版本 kp 集合，过滤关系端点（隐式版本隔离）

    prereq_chain = [
        {**_kp_node(graph, aid), "depth": d, "weight": w}
        for aid, d, w in graph.prerequisite_chain(kp_id, 5)
    ]
    direct_prereq = [
        {**_kp_node(graph, pid), "weight": w}
        for pid, w in graph.direct_prerequisites(kp_id)
    ]
    successors: list[dict] = []
    containers: list[dict] = []
    contained: list[dict] = []
    for rel in db.scalars(
        select(KpRelation).where(
            (KpRelation.from_kp_id == kp_id) | (KpRelation.to_kp_id == kp_id)
        )
    ):
        other_id = rel.to_kp_id if rel.from_kp_id == kp_id else rel.from_kp_id
        if other_id not in version_kp_ids:
            continue
        entry = {
            **_kp_node(graph, other_id),
            "relation_id": rel.id,
            "type": rel.type,
            "weight": rel.weight,
        }
        # from->to：from 是 to 的前置。本 kp 为 from -> other 是后继；本 kp 为 to -> other 是直接前置（已由 graph 给出，不重复）
        if rel.type == "prerequisite":
            if rel.from_kp_id == kp_id:
                successors.append(entry)
        elif rel.type == "contains":
            if rel.to_kp_id == kp_id:
                containers.append(entry)
            else:
                contained.append(entry)
    return {
        **_kp_brief(kp),
        "kb_version_id": kp.kb_version_id,
        "prerequisite_chain": prereq_chain,
        "direct_prerequisites": direct_prereq,
        "successors": successors,
        "containers": containers,
        "contained": contained,
    }


@router.get("/kb/relations")
def list_relations(kb_version_id: int | None = None, db: Session = Depends(get_db)):
    """关系列表，按端点 kp 归属版本过滤（隐式版本隔离，kb-edit §4.1/§6.3）。"""
    if kb_version_id is not None:
        kb = db.get(KbVersion, kb_version_id)
        if kb is None:
            raise HTTPException(404, "知识库版本不存在")
    else:
        kb = _active_kb(db)
    graph = _graph(db, kb.id)
    version_kp_ids = set(graph._kp.keys())
    out = []
    for rel in db.scalars(select(KpRelation).order_by(KpRelation.id)):
        if rel.from_kp_id not in version_kp_ids or rel.to_kp_id not in version_kp_ids:
            continue
        out.append(
            {
                "id": rel.id,
                "from": _kp_node(graph, rel.from_kp_id),
                "to": _kp_node(graph, rel.to_kp_id),
                "type": rel.type,
                "weight": rel.weight,
            }
        )
    return {"kb_version_id": kb.id, "relations": out}


# ---------------------------------------------------------------------------
# 知识点 / 关系 CRUD（kb-edit §4.3/§4.4）
# ---------------------------------------------------------------------------

RELATION_TYPES = ("prerequisite", "contains", "confusable", "spiral")
_COG_LEVELS = ("识记", "理解", "应用", "综合")


def _weak_count_for_kp(db: Session, kp_id: int, floor: float) -> int:
    """该 kp 有证据的学生中，掌握度 < floor 的数量。

    preview 影响估算用：纯阈值比较，不含教学进度/证据数门槛（粗略量级）。
    """
    when = _as_dt(None)
    count = 0
    for sid in db.scalars(
        select(EvidenceEvent.student_id).where(EvidenceEvent.kp_id == kp_id).distinct()
    ):
        m = mastery_at(db, sid, kp_id, when)
        if m is not None and m < floor:
            count += 1
    return count


def _floor_impact(
    db: Session, kp_id: int, current_floor: float, projected_floor: float
) -> dict:
    cur = _weak_count_for_kp(db, kp_id, current_floor)
    proj = _weak_count_for_kp(db, kp_id, projected_floor)
    return {
        "current": {"weak_count": cur, "floor": round(current_floor, 4)},
        "projected": {"weak_count": proj, "floor": round(projected_floor, 4)},
        "delta": proj - cur,
    }


@router.post("/kb/suggest-question-tags")
def suggest_question_tags_endpoint(req: SuggestQuestionRequest, db: Session = Depends(get_db)):
    """题干 -> 闭集知识点推荐（improvement-plan §3.3）。

    纯文本 LLM 推荐，不落库；教师审核修改后再 createExam/create_template。
    """
    from app.ingestion.templates import suggest_question_tags

    kb = _active_kb(db)
    return suggest_question_tags(db, kb.id, [q.model_dump() for q in req.questions])


@router.post("/kb/kps")
def create_kp(req: KpCreateRequest, db: Session = Depends(get_db)):
    """新建知识点（属 active kb）。code 同版本唯一（uq_kb_code + IntegrityError 兜底）。"""
    kb = _active_kb(db)
    for level in req.cog_levels_expected:
        if level not in _COG_LEVELS:
            raise HTTPException(400, f"非法认知层级: {level}")
    if req.code.startswith("C"):
        raise HTTPException(400, "C 前缀保留给容器节点，新建知识点不可使用")
    if req.importance not in ("基础", "核心", "拓展"):
        raise HTTPException(400, f"非法重要度: {req.importance}（基础/核心/拓展）")
    kp = KnowledgePoint(
        kb_version_id=kb.id,
        code=req.code,
        name=req.name,
        grade=req.grade,
        chapter=req.chapter,
        semester=req.semester,
        description=req.description,
        cog_levels_expected=req.cog_levels_expected,
        difficulty_prior=req.difficulty_prior,
        mastery_floor=req.mastery_floor,
        importance=req.importance,
    )
    nested = db.begin_nested()
    db.add(kp)
    try:
        db.flush()
    except IntegrityError:
        nested.rollback()
        raise HTTPException(400, f"知识点编码 {req.code} 在当前版本已存在")
    return _kp_brief(kp)


@router.patch("/kb/kps/{kp_id}")
def update_kp(
    kp_id: int,
    req: KpUpdateRequest,
    preview: bool = False,
    db: Session = Depends(get_db),
):
    """改属性（不允许改 code）。〔v0.2〕改 mastery_floor/difficulty_prior 支持 ?preview=true 影响预览。"""
    kp = db.get(KnowledgePoint, kp_id)
    if kp is None:
        raise HTTPException(404, "知识点不存在")
    fields = (
        "name", "description", "chapter", "semester",
        "cog_levels_expected", "difficulty_prior", "mastery_floor",
        "importance", "archived",
    )
    changes: dict[str, tuple] = {}
    for f in fields:
        val = getattr(req, f)
        if val is not None:
            changes[f] = (getattr(kp, f), val)
    if not changes:
        raise HTTPException(400, "未提供任何修改字段")
    if "cog_levels_expected" in changes:
        for level in changes["cog_levels_expected"][1]:
            if level not in _COG_LEVELS:
                raise HTTPException(400, f"非法认知层级: {level}")
    if "importance" in changes and changes["importance"][1] not in ("基础", "核心", "拓展"):
        raise HTTPException(400, f"非法重要度: {changes['importance'][1]}（基础/核心/拓展）")

    new_floor = changes["mastery_floor"][1] if "mastery_floor" in changes else kp.mastery_floor
    hi_lever = "mastery_floor" in changes or "difficulty_prior" in changes

    # 〔v0.2〕preview：不落库，返回当前 vs 预期的影响数
    if preview and hi_lever:
        impact = _floor_impact(db, kp_id, kp.mastery_floor, new_floor)
        if "difficulty_prior" in changes and "mastery_floor" not in changes:
            impact["note"] = "difficulty_prior 当前未参与掌握度计算，无即时影响"
        return {"preview": True, **impact}

    # 落库 + 留痕 CorrectionLog
    for f, (old, new) in changes.items():
        setattr(kp, f, new)
        _log_correction(db, "knowledge_point", kp_id, f, old, new, "teacher")
    db.flush()

    impact = None
    if hi_lever:
        impact = {
            "weak_count": _weak_count_for_kp(db, kp_id, new_floor),
            "floor": round(new_floor, 4),
        }
    return {**_kp_brief(kp), "impact": impact}


@router.delete("/kb/kps/{kp_id}")
def delete_kp(
    kp_id: int, force: bool = False, confirm: bool = False, db: Session = Depends(get_db)
):
    """软归档（默认）/ 硬删（force=true）。引用预检见 kb-edit §5。"""
    kp = db.get(KnowledgePoint, kp_id)
    if kp is None:
        raise HTTPException(404, "知识点不存在")
    if kp.code.startswith("C"):
        raise HTTPException(400, "容器节点不可删除/归档，仅可改名")

    evidence_refs = db.scalar(
        select(func.count(EvidenceEvent.id)).where(EvidenceEvent.kp_id == kp_id)
    ) or 0
    question_refs = db.scalar(
        select(func.count(QuestionKp.id)).where(QuestionKp.kp_id == kp_id)
    ) or 0
    progress_refs = db.scalar(
        select(func.count(TeachingProgress.id)).where(TeachingProgress.kp_id == kp_id)
    ) or 0

    # 硬删：仅当无证据、无题目标注
    if force:
        if evidence_refs > 0 or question_refs > 0:
            raise HTTPException(
                400,
                f"该知识点被 {evidence_refs} 条证据、{question_refs} 道题标注引用，不可硬删",
            )
        db.execute(
            delete(KpRelation).where(
                (KpRelation.from_kp_id == kp_id) | (KpRelation.to_kp_id == kp_id)
            )
        )
        db.execute(delete(TeachingProgress).where(TeachingProgress.kp_id == kp_id))
        db.delete(kp)
        db.flush()
        return {"deleted": True, "hard": True, "kp_id": kp_id}

    # 软归档
    # 〔v0.2〕被题目标注的 kp 归档需 confirm（防题目标注静默失效）
    if question_refs > 0 and not confirm:
        raise HTTPException(
            409,
            f"该知识点被 {question_refs} 道题标注，归档后这些题目的知识点分析将缺失。"
            f"确认归档请带 confirm=true",
        )
    # 〔v0.2〕归档即清教学进度残留（§5.4）
    if progress_refs > 0:
        db.execute(delete(TeachingProgress).where(TeachingProgress.kp_id == kp_id))
    kp.archived = True
    _log_correction(db, "knowledge_point", kp_id, "archived", False, True, "teacher")
    db.flush()
    return {
        "archived": True,
        "evidence_refs": evidence_refs,
        "question_refs": question_refs,
        "progress_refs": progress_refs,
        "progress_cleared": progress_refs,
    }


@router.post("/kb/relations")
def create_relation(req: RelationCreateRequest, db: Session = Depends(get_db)):
    """新建关系：校验 type/weight/同版本/非自环（kb-edit §4.4/§6.3）。"""
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    version_kp_ids = set(graph._kp.keys())
    if req.type not in RELATION_TYPES:
        raise HTTPException(400, f"非法关系类型: {req.type}")
    if not 0.0 <= req.weight <= 1.0:
        raise HTTPException(400, "关系权重须在 [0,1]")
    if req.from_kp_id == req.to_kp_id:
        raise HTTPException(400, "关系端点不可相同（自环）")
    if req.from_kp_id not in version_kp_ids or req.to_kp_id not in version_kp_ids:
        raise HTTPException(400, "关系端点不属于当前 active 版本")
    rel = KpRelation(
        from_kp_id=req.from_kp_id, to_kp_id=req.to_kp_id, type=req.type, weight=req.weight
    )
    db.add(rel)
    db.flush()
    return {
        "id": rel.id,
        "from": _kp_node(graph, rel.from_kp_id),
        "to": _kp_node(graph, rel.to_kp_id),
        "type": rel.type,
        "weight": rel.weight,
    }


@router.patch("/kb/relations/{rel_id}")
def update_relation(rel_id: int, req: RelationUpdateRequest, db: Session = Depends(get_db)):
    rel = db.get(KpRelation, rel_id)
    if rel is None:
        raise HTTPException(404, "关系不存在")
    if req.type is not None:
        if req.type not in RELATION_TYPES:
            raise HTTPException(400, f"非法关系类型: {req.type}")
        rel.type = req.type
    if req.weight is not None:
        if not 0.0 <= req.weight <= 1.0:
            raise HTTPException(400, "关系权重须在 [0,1]")
        rel.weight = req.weight
    db.flush()
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    return {
        "id": rel.id,
        "from": _kp_node(graph, rel.from_kp_id),
        "to": _kp_node(graph, rel.to_kp_id),
        "type": rel.type,
        "weight": rel.weight,
    }


@router.delete("/kb/relations/{rel_id}")
def delete_relation(rel_id: int, db: Session = Depends(get_db)):
    rel = db.get(KpRelation, rel_id)
    if rel is None:
        raise HTTPException(404, "关系不存在")
    db.delete(rel)
    db.flush()
    return {"deleted": rel_id}


# ---------------------------------------------------------------------------
# 版本管理 + 导出（kb-edit §4.5/§4.6）
# ---------------------------------------------------------------------------


@router.post("/kb/versions")
def fork_kb_version(db: Session = Depends(get_db)):
    """fork 当前 active：复制其 kp（含 archived）+ 关系为草稿新版本（kb-edit §4.5/§6.3）。"""
    src = _active_kb(db)
    new = KbVersion(
        subject=src.subject,
        textbook_edition=src.textbook_edition,
        version=f"{src.version}-fork",
        status="draft",
    )
    db.add(new)
    db.flush()
    # 复制 kp（含 archived），建立 src_id -> new_id 映射
    id_map: dict[int, int] = {}
    for kp in db.scalars(
        select(KnowledgePoint).where(KnowledgePoint.kb_version_id == src.id)
    ):
        nk = KnowledgePoint(
            kb_version_id=new.id,
            code=kp.code,
            name=kp.name,
            description=kp.description,
            grade=kp.grade,
            semester=kp.semester,
            chapter=kp.chapter,
            cog_levels_expected=kp.cog_levels_expected,
            difficulty_prior=kp.difficulty_prior,
            mastery_floor=kp.mastery_floor,
            archived=kp.archived,
        )
        db.add(nk)
        db.flush()
        id_map[kp.id] = nk.id
    # 复制关系：端点都在 src 版本内的，按 id_map 映射到新版本（§6.3）
    src_ids = set(id_map)
    for rel in db.scalars(select(KpRelation).order_by(KpRelation.id)):
        if rel.from_kp_id in src_ids and rel.to_kp_id in src_ids:
            db.add(
                KpRelation(
                    from_kp_id=id_map[rel.from_kp_id],
                    to_kp_id=id_map[rel.to_kp_id],
                    type=rel.type,
                    weight=rel.weight,
                    audit_status="draft",
                )
            )
    db.flush()
    return {"id": new.id, "status": new.status, "forked_from": src.id}


def _compatibility(db: Session, active: KbVersion, target: KbVersion) -> dict:
    """目标版本 vs 当前 active：code 差集 + 〔v0.2〕高杠杆属性 diff。"""
    active_kps = {
        k.code: k
        for k in db.scalars(
            select(KnowledgePoint).where(KnowledgePoint.kb_version_id == active.id)
        )
    }
    target_kps = {
        k.code: k
        for k in db.scalars(
            select(KnowledgePoint).where(KnowledgePoint.kb_version_id == target.id)
        )
    }
    missing = sorted(set(active_kps) - set(target_kps))  # active 有、target 无 -> 旧证据失联
    new = sorted(set(target_kps) - set(active_kps))
    attr_changes = []
    for code in set(active_kps) & set(target_kps):
        a, t = active_kps[code], target_kps[code]
        for field in ("mastery_floor", "difficulty_prior", "archived"):
            av, tv = getattr(a, field), getattr(t, field)
            if av != tv:
                attr_changes.append({"code": code, "field": field, "old": av, "new": tv})
    return {"missing_codes": missing, "new_codes": new, "attribute_changes": attr_changes}


@router.get("/kb/versions/{version_id}/compatibility")
def kb_compatibility(version_id: int, db: Session = Depends(get_db)):
    """与当前 active 的 code 差集 + 〔v0.2〕属性 diff（切换前预览）。"""
    target = db.get(KbVersion, version_id)
    if target is None:
        raise HTTPException(404, "版本不存在")
    if target.status == "active":
        return {
            "active_version_id": target.id,
            "target_version_id": target.id,
            "missing_codes": [],
            "new_codes": [],
            "attribute_changes": [],
        }
    active = _active_kb(db)
    return {
        "active_version_id": active.id,
        "target_version_id": target.id,
        **_compatibility(db, active, target),
    }


@router.patch("/kb/versions/{version_id}")
def patch_kb_version(
    version_id: int,
    req: KbVersionPatchRequest,
    confirm: bool = False,
    force: bool = False,
    db: Session = Depends(get_db),
):
    """改 status：draft->reviewed->active。切 active 做超集 + 〔v0.2〕属性 diff 校验（§6.1/§6.2/§6.5）。"""
    target = db.get(KbVersion, version_id)
    if target is None:
        raise HTTPException(404, "版本不存在")
    if req.status not in ("draft", "reviewed", "active"):
        raise HTTPException(400, "非法 status")

    if req.status != "active":
        target.status = req.status
        db.flush()
        return {"id": target.id, "status": target.status}

    # 切 active
    if target.status == "active":
        raise HTTPException(400, "该版本已是 active")
    active = _active_kb(db)
    comp = _compatibility(db, active, target)
    # ① code 超集：缺失 code 需 force（接受旧证据失联）
    missing = comp["missing_codes"]
    if missing and not force:
        raise HTTPException(
            400,
            f"目标版本缺失 code: {missing}（旧证据会从分析消失）。确认丢失请带 force=true",
        )
    # 〔v0.2〕② 属性 diff：高杠杆参数变化需 confirm
    attr = comp["attribute_changes"]
    if attr and not confirm:
        raise HTTPException(
            409,
            f"切换将改变 {len(attr)} 个知识点的高杠杆参数，分析结论会改变。确认请带 confirm=true",
        )
    # 切换：旧 active 降 reviewed（不删，可结构回滚切回），目标置 active，写切换日志
    from_id = active.id
    active.status = "reviewed"
    target.status = "active"
    _log_correction(db, "kb_version", target.id, "active", from_id, target.id, "teacher")
    db.flush()
    return {
        "id": target.id,
        "status": "active",
        "switched_from": from_id,
        "missing_codes_accepted": missing if missing and force else [],
        "attribute_changes_accepted": attr if attr and confirm else [],
        "note": "切换后新产生的考试证据无法迁回旧版本",
    }


@router.get("/kb/export")
def export_kb(kb_version_id: int | None = None, db: Session = Depends(get_db)):
    """从 DB 现状生成 YAML（对齐 loader 可读回，kb-edit §4.6）。"""
    if kb_version_id is not None:
        kb = db.get(KbVersion, kb_version_id)
        if kb is None:
            raise HTTPException(404, "版本不存在")
    else:
        kb = _active_kb(db)
    kps = list(
        db.scalars(
            select(KnowledgePoint)
            .where(KnowledgePoint.kb_version_id == kb.id)
            .order_by(KnowledgePoint.code)
        )
    )
    id_to_code = {kp.id: kp.code for kp in kps}
    version_ids = set(id_to_code)
    relations = []
    for rel in db.scalars(select(KpRelation).order_by(KpRelation.id)):
        if rel.from_kp_id in version_ids and rel.to_kp_id in version_ids:
            relations.append(
                {
                    "from": id_to_code[rel.from_kp_id],
                    "to": id_to_code[rel.to_kp_id],
                    "type": rel.type,
                    "weight": rel.weight,
                }
            )
    points = []
    for kp in kps:
        item = {
            "code": kp.code,
            "name": kp.name,
            "description": kp.description,
            "grade": kp.grade,
            "semester": kp.semester,
            "chapter": kp.chapter,
            "cog_levels_expected": kp.cog_levels_expected,
            "difficulty_prior": kp.difficulty_prior,
            "mastery_floor": kp.mastery_floor,
            "is_container": kp.code.startswith("C"),
        }
        if kp.archived:
            item["archived"] = True
        points.append(item)
    data = {
        "meta": {
            "subject": kb.subject,
            "textbook_edition": kb.textbook_edition,
            "version": kb.version,
            "status": kb.status,
        },
        "knowledge_points": points,
        "relations": relations,
    }
    yaml_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return Response(
        content=yaml_text,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="kb-v{kb.id}.yaml"'},
    )


@router.get("/classes")
def list_classes(db: Session = Depends(get_db)):
    out = []
    for clazz in db.scalars(select(Class).order_by(Class.id)):
        student_n = db.scalar(
            select(func.count(Student.id)).where(Student.class_id == clazz.id)
        )
        exam_n = db.scalar(
            select(func.count(ExamTemplate.id)).where(
                ExamTemplate.class_id == clazz.id
            )
        )
        out.append(
            {
                "class_id": clazz.id,
                "name": clazz.name,
                "grade": clazz.grade,
                "subject": clazz.subject,
                "school_id": clazz.school_id,
                "student_count": student_n or 0,
                "exam_count": exam_n or 0,
            }
        )
    return {"classes": out}


@router.get("/classes/overview")
def classes_overview(db: Session = Depends(get_db)):
    """所有班级的轻量概览（一级「班级概览」页用，一次返回避免前端 N+1）。

    每班汇总：待办考试数、最近一场考试状态、教学进度覆盖（与分析层同分母）。
    active kb 缺失时 progress 返回 {0,0}，不抛错——一级页面不能因未导入知识库整页 500。
    """
    grade7_set: set[int] = set()
    try:
        kb = _active_kb(db)
        grade7_set = set(_graph(db, kb.id).grade7_kp_ids())
    except HTTPException:
        grade7_set = set()

    out = []
    for clazz in db.scalars(select(Class).order_by(Class.id)):
        student_n = db.scalar(
            select(func.count(Student.id)).where(Student.class_id == clazz.id)
        ) or 0
        exams = db.scalars(
            select(ExamTemplate)
            .where(ExamTemplate.class_id == clazz.id)
            .order_by(ExamTemplate.exam_date.desc(), ExamTemplate.id.desc())
        ).all()

        todo_count = 0
        latest_exam = None
        for tpl in exams:
            status_counts = dict(
                db.execute(
                    select(ExamResponse.status, func.count(ExamResponse.id))
                    .where(ExamResponse.exam_template_id == tpl.id)
                    .group_by(ExamResponse.status)
                ).all()
            )
            unreviewed = db.scalar(
                select(func.count(QuestionKp.id))
                .join(TemplateQuestion, QuestionKp.template_question_id == TemplateQuestion.id)
                .where(TemplateQuestion.exam_template_id == tpl.id)
                .where(QuestionKp.reviewed_at.is_(None))
            ) or 0
            pending = status_counts.get("待审核", 0)
            if latest_exam is None:
                latest_exam = {
                    "exam_id": tpl.id,
                    "name": tpl.name,
                    "exam_date": str(tpl.exam_date),
                    "type": tpl.type,
                    "submitted": status_counts.get("已提交", 0),
                    "pending": pending,
                }
            if unreviewed > 0 or pending > 0:
                todo_count += 1

        taught = 0
        if grade7_set:
            taught = db.scalar(
                select(func.count(TeachingProgress.id))
                .where(TeachingProgress.class_id == clazz.id)
                .where(TeachingProgress.kp_id.in_(grade7_set))
            ) or 0

        out.append(
            {
                "class_id": clazz.id,
                "name": clazz.name,
                "grade": clazz.grade,
                "subject": clazz.subject,
                "school_id": clazz.school_id,
                "student_count": student_n,
                "exam_count": len(exams),
                "todo_count": todo_count,
                "latest_exam": latest_exam,
                "progress": {"taught": taught, "total": len(grade7_set)},
            }
        )
    return {"classes": out}


@router.get("/classes/{class_id}/students")
def list_students(class_id: int, db: Session = Depends(get_db)):
    if db.get(Class, class_id) is None:
        raise HTTPException(404, "班级不存在")
    students = db.scalars(
        select(Student).where(Student.class_id == class_id).order_by(Student.id)
    )
    # 名单原序返回；禁止按分数排序由前端约束，此处不提供任何分数字段
    return {
        "class_id": class_id,
        "students": [
            {
                "student_id": s.id,
                "name_or_alias": s.name_or_alias,
                "external_code": s.external_code,
            }
            for s in students
        ],
    }


@router.get("/classes/{class_id}/progress")
def get_progress(class_id: int, db: Session = Depends(get_db)):
    if db.get(Class, class_id) is None:
        raise HTTPException(404, "班级不存在")
    rows = db.scalars(
        select(TeachingProgress)
        .where(TeachingProgress.class_id == class_id)
        .order_by(TeachingProgress.taught_at, TeachingProgress.kp_id)
    )
    out = []
    for p in rows:
        kp = db.get(KnowledgePoint, p.kp_id)
        out.append(
            {
                "kp_id": p.kp_id,
                "code": kp.code if kp else f"kp#{p.kp_id}",
                "name": kp.name if kp else "",
                "taught_at": str(p.taught_at),
                "archived": bool(kp.archived) if kp else False,
            }
        )
    return {"class_id": class_id, "progress": out}


@router.delete("/classes/{class_id}/progress/{kp_id}")
def delete_progress(class_id: int, kp_id: int, db: Session = Depends(get_db)):
    """取消已教标记（kb-edit §4.2）。既有标记可删（含 archived kp 的残留）。"""
    row = db.scalar(
        select(TeachingProgress).where(
            TeachingProgress.class_id == class_id, TeachingProgress.kp_id == kp_id
        )
    )
    if row is None:
        raise HTTPException(404, "未找到该教学进度记录")
    db.delete(row)
    db.flush()
    return {"deleted_kp_id": kp_id}


@router.patch("/classes/{class_id}/progress/{kp_id}")
def patch_progress(
    class_id: int,
    kp_id: int,
    req: ProgressPatchRequest,
    db: Session = Depends(get_db),
):
    """改 taught_at 日期（kb-edit §4.2）。"""
    row = db.scalar(
        select(TeachingProgress).where(
            TeachingProgress.class_id == class_id, TeachingProgress.kp_id == kp_id
        )
    )
    if row is None:
        raise HTTPException(404, "未找到该教学进度记录")
    row.taught_at = req.taught_at
    db.flush()
    return {"class_id": class_id, "kp_id": kp_id, "taught_at": str(row.taught_at)}


@router.get("/exams")
def list_exams(class_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(ExamTemplate).order_by(ExamTemplate.exam_date.desc(), ExamTemplate.id)
    if class_id is not None:
        stmt = stmt.where(ExamTemplate.class_id == class_id)
    out = []
    for tpl in db.scalars(stmt):
        status_counts = dict(
            db.execute(
                select(ExamResponse.status, func.count(ExamResponse.id))
                .where(ExamResponse.exam_template_id == tpl.id)
                .group_by(ExamResponse.status)
            ).all()
        )
        unreviewed_tags = db.scalar(
            select(func.count(QuestionKp.id))
            .join(TemplateQuestion, QuestionKp.template_question_id == TemplateQuestion.id)
            .where(TemplateQuestion.exam_template_id == tpl.id)
            .where(QuestionKp.reviewed_at.is_(None))
        )
        out.append(
            {
                "exam_id": tpl.id,
                "class_id": tpl.class_id,
                "name": tpl.name,
                "exam_date": str(tpl.exam_date),
                "type": tpl.type,
                "source": tpl.source,
                "question_count": len(tpl.questions),
                "response_counts": status_counts,
                "unreviewed_tags": unreviewed_tags or 0,
            }
        )
    return {"exams": out}


@router.get("/exams/{exam_id}")
def exam_detail(exam_id: int, db: Session = Depends(get_db)):
    tpl = db.get(ExamTemplate, exam_id)
    if tpl is None:
        raise HTTPException(404, "考试不存在")
    questions = []
    for q in sorted(tpl.questions, key=lambda x: x.idx):
        kps = []
        for tag in q.kps:
            kp = db.get(KnowledgePoint, tag.kp_id)
            kps.append(
                {
                    "tag_id": tag.id,
                    "code": kp.code if kp else "",
                    "name": kp.name if kp else "",
                    "weight": tag.weight,
                    "source": tag.source,
                    "confidence": tag.confidence,
                    "reviewed": tag.reviewed_at is not None,
                    "reviewed_by": tag.reviewed_by,
                }
            )
        questions.append(
            {
                "question_id": q.id,
                "idx": q.idx,
                "stem": q.stem,
                "q_type": q.q_type,
                "full_score": q.full_score,
                "cog_level": q.cog_level,
                "n_options": q.n_options,
                "kps": kps,
            }
        )
    return {
        "exam_id": tpl.id,
        "class_id": tpl.class_id,
        "name": tpl.name,
        "exam_date": str(tpl.exam_date),
        "type": tpl.type,
        "source": tpl.source,
        "questions": questions,
    }


@router.get("/exams/{exam_id}/responses")
def exam_responses(exam_id: int, db: Session = Depends(get_db)):
    """学生×作答状态矩阵（名单原序；低置信题计数供审核台角标）。"""
    tpl = db.get(ExamTemplate, exam_id)
    if tpl is None:
        raise HTTPException(404, "考试不存在")
    from app.ingestion.photo import AUTO_PASS

    students = db.scalars(
        select(Student).where(Student.class_id == tpl.class_id).order_by(Student.id)
    )
    responses = {
        r.student_id: r
        for r in db.scalars(
            select(ExamResponse).where(ExamResponse.exam_template_id == exam_id)
        )
    }
    rows = []
    counts = {"未采集": 0, "待审核": 0, "已提交": 0}
    for stu in students:
        resp = responses.get(stu.id)
        if resp is None:
            row_status = "未采集"
            row = {
                "student_id": stu.id,
                "name_or_alias": stu.name_or_alias,
                "status": row_status,
                "response_id": None,
                "total_score": None,
                "low_confidence_count": 0,
            }
        else:
            row_status = resp.status if resp.status in counts else "待审核"
            low_n = db.scalar(
                select(func.count(ResponseAnswer.id)).where(
                    ResponseAnswer.exam_response_id == resp.id,
                    ResponseAnswer.parse_confidence < AUTO_PASS,
                )
            )
            row = {
                "student_id": stu.id,
                "name_or_alias": stu.name_or_alias,
                "status": row_status,
                "response_id": resp.id,
                "total_score": resp.total_score,
                "low_confidence_count": low_n or 0,
            }
        counts[row_status] += 1
        rows.append(row)
    return {"exam_id": exam_id, "summary": counts, "responses": rows}


# ---------------------------------------------------------------------------
# 审核台修正（教师改动 → source=教师、留痕 CorrectionLog — 不变量③）
# ---------------------------------------------------------------------------


def _log_correction(db: Session, entity_type: str, entity_id: int, field: str, old, new, by: str):
    db.add(
        CorrectionLog(
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            old=str(old),
            new=str(new),
            corrected_by=by,
        )
    )


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
    now = datetime.utcnow()
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
    _log_correction(db, "template_question", question_id, "kps", old_codes, new_codes, req.reviewer)
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
        _log_correction(db, "response_answer", answer_id, field, old, new, req.reviewer)
    return {
        "answer_id": answer_id,
        "score": ans.score,
        "chosen_option": ans.chosen_option,
        "total_score": resp.total_score,
        "status": resp.status,
    }


# ---------------------------------------------------------------------------
# 教师否决归因（教师否决权 — 引擎重跑永不复活 overridden）
# ---------------------------------------------------------------------------


@router.post("/attributions/{attribution_id}/override")
def override_attribution(attribution_id: int, req: AttributionOverride, db: Session = Depends(get_db)):
    att = db.get(Attribution, attribution_id)
    if att is None:
        raise HTTPException(404, "归因不存在")
    if att.status != "active":
        raise HTTPException(400, f"归因状态为 {att.status}，无需否决")
    att.status = "overridden"
    att.teacher_note = req.note or None
    _log_correction(db, "attribution", attribution_id, "status", "active", "overridden", req.reviewer)
    return {"attribution_id": attribution_id, "status": "overridden", "note": att.teacher_note}


@router.post("/attributions/{attribution_id}/verify")
def verify_attribution(attribution_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    """诊断题证伪：用诊断证据验证前置缺陷归因预测（improvement-plan §1.4-A）。

    诊断题（type=诊断、单 kp）作答提交后派生单 kp 证据；本端点重查前置点掌握度：
    已达标 -> 证伪 -> 置 overridden（跨重跑保留）；仍低 -> 证实（保留 active）。
    """
    from app.pipeline.attribution import verify_attribution_prediction

    att = db.get(Attribution, attribution_id)
    if att is None:
        raise HTTPException(404, "归因不存在")
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    when = _as_dt(as_of)
    try:
        result = verify_attribution_prediction(db, graph, attribution_id, when)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result["status"] == "overridden":
        _log_correction(
            db, "attribution", attribution_id, "status", "active", "overridden", "diagnostic"
        )
    return result


@router.get("/attributions/closure")
def attribution_closure(class_id: int | None = None, db: Session = Depends(get_db)):
    """证伪闭环度量（effectiveness-validation-plan V3-度量）。

    归因按状态/证伪结论的分布、诊断验证率、教师否决率。
    closure_rate = 被诊断题验证过的归因占比；低 = 「可证伪」停留在纸面。
    """
    from app.pipeline.attribution import attribution_closure

    return attribution_closure(db, class_id)


# ---------------------------------------------------------------------------
# 知识库上传与报告回读
# ---------------------------------------------------------------------------


@router.post("/kb/upload")
async def kb_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """浏览器直接上传知识库 YAML（无需服务器文件系统访问）。"""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        kb = import_kb(db, tmp_path)
    except Exception as e:
        raise HTTPException(400, f"知识库导入失败: {e}")
    return {"kb_version_id": kb.id, "status": kb.status, "version": kb.version}


@router.get("/reports")
def list_reports(
    class_id: int | None = None,
    student_id: int | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Report).order_by(Report.generated_at.desc(), Report.id.desc())
    if class_id is not None:
        stmt = stmt.where(Report.class_id == class_id)
    if student_id is not None:
        stmt = stmt.where(Report.student_id == student_id)
    return {
        "reports": [
            {
                "report_id": r.id,
                "type": r.type,
                "class_id": r.class_id,
                "student_id": r.student_id,
                "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            }
            for r in db.scalars(stmt)
        ]
    }


@router.get("/reports/{report_id}")
def report_detail(report_id: int, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    return {
        "report_id": report.id,
        "type": report.type,
        "class_id": report.class_id,
        "student_id": report.student_id,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "markdown": report.content_markdown,
        "snapshot": report.snapshot_json,
    }
