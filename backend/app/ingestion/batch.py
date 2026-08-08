"""批量拍照录入：后台线程池解析多张学生卷（DESIGN 批量录入 v0.3）。

与单张路径的关键差异：
- 不遮罩原图（需读卷面姓名用于名单匹配）；
- 后台异步：worker 自开短事务，**不在 LLM 调用期间持有写事务**；
- 并发去重靠既有 uq_tpl_student + IntegrityError，不靠先查后建；
- tempfile 脱离请求生命周期：handler 落 delete=False 临时文件，worker 读 file_path。

两种执行路径：
- async（生产）：submit_item -> 线程池 -> 两段短事务，LLM 调用介于其间。
- sync（仅 mock / 显式开关）：run_item_sync 在请求会话内串行执行，不触线程池。
  测试统一用 sync=true（MockLLMClient 顺序可预测），见 §10。
"""

from __future__ import annotations

import glob
import os
import random
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import BATCH_STALE_MINUTES, BATCH_TEMPFILE_MAX_AGE_HOURS, settings
from app.ingestion.photo import PhotoParseResult, _persist_response_from_payload
from app.llm.circuit import CircuitOpenError, get_vision_breaker
from app.llm.client import LLMError, MockLLMClient, get_client
from app.llm.prompts import (
    RESPONSE_BATCH_SYSTEM,
    response_batch_user_prompt,
)
from app.models import ExamResponse, ExamTemplate, ParseBatchItem, ParseJob, Student
from app.observability import get_logger

_log = get_logger("batch")

# ---------------------------------------------------------------------------
# 状态常量（与 parse_batch_item.status 取值对齐）
# ---------------------------------------------------------------------------
QUEUED = "queued"
PARSING = "parsing"
MATCHED = "matched"
UNMATCHED = "unmatched"
FAILED = "failed"
DUPLICATE = "duplicate"
DISCARDED = "discarded"

_TERMINAL = {MATCHED, UNMATCHED, FAILED, DUPLICATE, DISCARDED}
# 终态后删除 tempfile 的状态（failed 保留供重试）
_TEMPFILE_DELETE_STATES = {MATCHED, UNMATCHED, DUPLICATE, DISCARDED}
# G6：批量 tempfile 统一前缀，便于孤儿文件清扫（routes 落盘时用同前缀）
TEMPFILE_PREFIX = "sc_batch_"

# LLM 瞬时失败退避重试（秒）；可重试类做 1-2 次重试。实际 sleep = backoff × uniform(0.5,1.5)
# （G8：±50% 抖动，避免 3 worker 在相同偏移同时重试加剧 provider 限流）
_RETRY_BACKOFFS = (2.0, 6.0)
# LLMError 中含这些关键词的视为不可重试（JSON/schema/mock 空）
_NON_RETRYABLE_HINTS = ("json", "无预设响应")

_executor: ThreadPoolExecutor | None = None


def _make_executor() -> ThreadPoolExecutor:
    """G12：并发数可配置（SC_BATCH_WORKERS，默认 3）。"""
    return ThreadPoolExecutor(max_workers=settings.batch_workers)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


def submit_item(item_id: int) -> None:
    """异步：把 item 投递到线程池。"""
    global _executor
    if _executor is None:
        _executor = _make_executor()
    try:
        _executor.submit(_process_async, item_id)
    except RuntimeError:
        # 上一轮 TestClient 关闭时已 shutdown，重建
        _executor = _make_executor()
        _executor.submit(_process_async, item_id)


def shutdown() -> None:
    """进程退出钩子：cancel 待跑 future，不等在途。"""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


def reconcile_stale() -> None:
    """启动兜底：崩溃遗留的 running batch job 下 parsing/queued item 改判 failed。

    进程内线程池的 future 全在内存，uvicorn --reload / 崩溃后，在途 item 永远停在
    parsing、job 停在 running。这里在 startup（init_db 后）做一次性全局回收。
    MVP 不引入持久化工作队列（Celery/RQ）。
    """
    with _new_session() as s:
        jobs = list(
            s.scalars(
                select(ParseJob)
                .where(ParseJob.status == "running")
                .where(ParseJob.target.like("batch:%"))
            )
        )
        if not jobs:
            return
        job_ids = [j.id for j in jobs]
        items = list(
            s.scalars(
                select(ParseBatchItem)
                .where(ParseBatchItem.parse_job_id.in_(job_ids))
                .where(ParseBatchItem.status.in_((QUEUED, PARSING)))
            )
        )
        for it in items:
            it.status = FAILED
            it.warnings = (it.warnings or []) + ["服务重启中断，可重试"]
        for j in jobs:
            j.status = "done"
        s.commit()


def reconcile_stale_runtime(now: datetime | None = None) -> int:
    """G6 运行期看门狗：parsing 超 ``BATCH_STALE_MINUTES`` 的 item 改判 failed + 收尾 job。

    与 reconcile_stale（启动一次性回收）互补：reconcile_stale 处理进程崩溃遗留，
    本函数处理运行期卡死（worker 线程被外部 kill / 阻塞超 2× LLM TIMEOUT）。
    惰性触发：教师轮询 batch-jobs 时调用，无需后台线程。返回改判的 item 数。
    """
    cutoff = (now or datetime.utcnow()) - timedelta(minutes=BATCH_STALE_MINUTES)
    with _new_session() as s:
        stale = list(
            s.scalars(
                select(ParseBatchItem)
                .where(ParseBatchItem.status == PARSING)
                .where(ParseBatchItem.started_at.is_not(None))
                .where(ParseBatchItem.started_at < cutoff)
            )
        )
        if not stale:
            return 0
        job_ids = {it.parse_job_id for it in stale}
        for it in stale:
            it.status = FAILED
            it.warnings = (it.warnings or []) + [
                f"解析超时（>{BATCH_STALE_MINUTES} 分钟），可重试"
            ]
            _log.warning(
                "batch item 看门狗改判 failed",
                extra={"item_id": it.id, "job_id": it.parse_job_id},
            )
        for jid in job_ids:
            _finalize_job(s, jid)
        s.commit()
        return len(stale)


def gc_orphan_tempfiles(
    referenced: set[str] | None = None,
    max_age_hours: int = BATCH_TEMPFILE_MAX_AGE_HOURS,
    tmp_dir: str | None = None,
) -> int:
    """G6 清扫孤儿 tempfile：按 mtime 兜底，超 ``max_age_hours`` 且未被任何 item 引用即删。

    正常路径终态已删（``_TEMPFILE_DELETE_STATES``）/ failed 保留供重试；孤儿来自进程
    崩在落库前。仅扫 ``sc_batch_`` 前缀，避免误删其他临时文件。返回删除数。
    """
    if referenced is None:
        with _new_session() as s:
            referenced = {
                p
                for (p,) in s.execute(
                    select(ParseBatchItem.file_path).where(
                        ParseBatchItem.file_path.is_not(None)
                    )
                ).all()
            }
    cutoff = time.time() - max_age_hours * 3600
    scan_dir = tmp_dir or tempfile.gettempdir()
    removed = 0
    for path in glob.glob(os.path.join(scan_dir, TEMPFILE_PREFIX + "*")):
        try:
            if os.path.getmtime(path) < cutoff and path not in referenced:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    if removed:
        _log.info("batch tempfile GC", extra={"removed": removed})
    return removed


# ---------------------------------------------------------------------------
# 异步路径（两段短事务，LLM 调用介于其间）
# ---------------------------------------------------------------------------


def _process_async(item_id: int) -> None:
    """异步解析单个 item（两段短事务，LLM 调用介于其间）。

    G1：整体 try/except 兜底--任何逃逸异常都落到 item 终态（failed）并 finalize job，
    绝不留 parsing 孤儿、不静默吞异常。fire-and-forget 的 future 不被 .result() 消费，
    故异常必须在此内部处理（submit_item 不消费 future）。
    """
    try:
        _process_async_impl(item_id)
    except Exception as exc:  # noqa: BLE001  worker 兜底：任何异常都不得留孤儿
        _log.exception(
            "batch worker 异常", extra={"item_id": item_id, "error": str(exc)}
        )
        try:
            with _new_session() as s:
                it = s.get(ParseBatchItem, item_id)
                if it is not None:
                    job_id = it.parse_job_id
                    if it.status not in _TERMINAL:
                        it.status = FAILED
                        it.detected_name = None
                        it.warnings = (it.warnings or []) + [f"解析异常：{exc}，可重试"]
                    _finalize_job(s, job_id)
                s.commit()
        except Exception:
            _log.exception(
                "batch worker 兜底落库失败", extra={"item_id": item_id}
            )


def _process_async_impl(item_id: int) -> None:
    # 事务1：置 parsing，读出执行上下文
    with _new_session() as s:
        item = s.get(ParseBatchItem, item_id)
        if item is None or item.status != QUEUED:
            return
        item.status = PARSING
        item.started_at = datetime.utcnow()  # G6：看门狗计时基准
        _set_job_running(s, item.parse_job_id)
        file_path = item.file_path
        exam_template_id = item.exam_template_id
        job_id = item.parse_job_id
        s.commit()
    _log.info("batch item 开始解析", extra={"item_id": item_id, "job_id": job_id})

    image_bytes = _read_tempfile(file_path)
    if image_bytes is None:
        with _new_session() as s:
            _mark_item_failed(s, item_id, "原始文件丢失，请重新上传")
            _finalize_job(s, job_id)
            s.commit()
        _log.warning(
            "batch item 文件丢失", extra={"item_id": item_id, "job_id": job_id}
        )
        return

    desc = _questions_desc(exam_template_id)
    payload, warnings = _call_llm_with_retry(desc, image_bytes)

    fp_to_remove: str | None = None
    with _new_session() as s:
        final_status = _persist_batch_result(s, item_id, exam_template_id, payload, warnings)
        if final_status in _TEMPFILE_DELETE_STATES:
            it = s.get(ParseBatchItem, item_id)
            fp_to_remove = it.file_path if it else None
            if it:
                it.file_path = None
        _finalize_job(s, job_id)
        s.commit()
    _safe_remove(fp_to_remove)
    _log.info(
        "batch item 解析完成",
        extra={"item_id": item_id, "job_id": job_id, "status": final_status},
    )


# ---------------------------------------------------------------------------
# 同步路径（请求会话内串行，仅 mock / 显式开关；测试用）
# ---------------------------------------------------------------------------


def run_item_sync(item_id: int, session) -> None:
    """在给定会话内串行解析单个 item（不另开 SessionLocal，不触线程池）。"""
    item = session.get(ParseBatchItem, item_id)
    if item is None or item.status != QUEUED:
        return
    item.status = PARSING
    item.started_at = datetime.utcnow()  # G6：与异步路径一致，看门狗计时基准
    _set_job_running(session, item.parse_job_id)
    session.flush()

    image_bytes = _read_tempfile(item.file_path)
    if image_bytes is None:
        _mark_item_failed(session, item_id, "原始文件丢失，请重新上传")
        _finalize_job(session, item.parse_job_id)
        session.flush()
        return

    desc = _questions_desc(item.exam_template_id)
    payload, warnings = _call_llm_with_retry(desc, image_bytes)

    final_status = _persist_batch_result(session, item_id, item.exam_template_id, payload, warnings)
    if final_status in _TEMPFILE_DELETE_STATES:
        it = session.get(ParseBatchItem, item_id)
        fp = it.file_path if it else None
        if it:
            it.file_path = None
        _safe_remove(fp)
    _finalize_job(session, item.parse_job_id)
    session.flush()
    _log.info(
        "batch item 解析完成",
        extra={"item_id": item_id, "job_id": item.parse_job_id, "status": final_status},
    )


# ---------------------------------------------------------------------------
# 落库单个 item 的解析结果
# ---------------------------------------------------------------------------


def _persist_batch_result(
    session,
    item_id: int,
    exam_template_id: int,
    payload: dict | None,
    warnings: list[str],
) -> str:
    """匹配名单 -> 落库 -> 写 item 终态。返回终态字符串。"""
    item = session.get(ParseBatchItem, item_id)
    if item is None:
        return FAILED
    base_warnings = list(warnings or [])

    if payload is None:
        item.status = FAILED
        item.detected_name = None  # failed 清空（重试会重读）
        item.payload_json = None  # G3：failed 不留含姓名的 payload
        item.warnings = base_warnings
        session.flush()
        return FAILED

    detected_name = str(payload.get("student_name") or "").strip() or None
    item.detected_name = detected_name
    item.payload_json = payload  # 暂存：仅 unmatched 终态保留，其余终态下方清空（G3）

    template = session.get(ExamTemplate, exam_template_id)
    if template is None:
        item.status = FAILED
        item.detected_name = None
        item.payload_json = None  # G3：模板缺失失败亦不留 PII payload
        item.warnings = base_warnings + ["考试模板不存在"]
        session.flush()
        return FAILED

    students = list(
        session.scalars(
            select(Student)
            .where(Student.class_id == template.class_id)
            .order_by(Student.id)
        )
    )
    stu_id, conf, match_warnings = _match_student(students, detected_name)
    item.warnings = base_warnings + match_warnings
    session.flush()  # 把 detected_name/payload_json/warnings 落到外层事务，再开 savepoint

    if stu_id is None:
        item.status = UNMATCHED
        item.matched_student_id = None
        item.match_confidence = None
        # detected_name + payload_json 保留：待指派期间供教师参考 + 免重调 LLM 落库（G3 唯一保留项）
        session.flush()
        return UNMATCHED

    result = PhotoParseResult()
    try:
        nested = session.begin_nested()  # SAVEPOINT：只回滚作答写入，不动 item
        response = _persist_response_from_payload(session, template, stu_id, payload, result)
        nested.commit()
        item.status = MATCHED
        item.matched_student_id = stu_id
        item.match_confidence = conf
        item.response_id = response.id
        item.detected_name = None  # matched 清空
        item.payload_json = None  # G3：作答已落 ExamResponse，item 不必再留含姓名的全量 payload
        item.warnings = base_warnings + match_warnings + result.warnings
        session.flush()
        return MATCHED
    except IntegrityError:
        # uq_tpl_student 触发：该生本场已有作答 -> duplicate
        nested.rollback()
        existing = session.scalar(
            select(ExamResponse.id).where(
                ExamResponse.exam_template_id == exam_template_id,
                ExamResponse.student_id == stu_id,
            )
        )
        item.status = DUPLICATE
        item.matched_student_id = stu_id
        item.match_confidence = conf
        item.response_id = existing
        item.detected_name = None  # duplicate 清空
        item.payload_json = None  # G3：duplicate 同样不留含姓名的 payload
        item.warnings = base_warnings + match_warnings + ["该生本场已有作答（重复上传）"]
        session.flush()
        return DUPLICATE


# ---------------------------------------------------------------------------
# 姓名匹配
# ---------------------------------------------------------------------------


def _normalize_name(name: str | None) -> str:
    return re.sub(r"[\s·.・]", "", name or "").lower()


def _match_student(students: list[Student], detected_name: str | None):
    """返回 (student_id, confidence, warnings)。无匹配/歧义返回 (None, None, [...])。"""
    dn = _normalize_name(detected_name)
    if not dn:
        return None, None, ["卷面姓名为空，请人工指派"]
    # ① 精确：name_or_alias 或 external_code 等值
    for stu in students:
        if dn == _normalize_name(stu.name_or_alias):
            return stu.id, 1.0, []
        if stu.external_code and dn == _normalize_name(stu.external_code):
            return stu.id, 1.0, []
    # ② 包含（仅 name_or_alias，external_code 不参与）
    hits = []
    for stu in students:
        alias = _normalize_name(stu.name_or_alias)
        if len(alias) < 2 or len(dn) < 2:
            continue
        if dn in alias or alias in dn:
            hits.append(stu)
    if len(hits) == 1:
        return hits[0].id, 0.8, ["姓名包含匹配（0.8），建议核对"]
    if len(hits) > 1:
        # 多重命中判歧义：不任选，交人工
        return None, None, ["姓名包含匹配存在多名候选，请人工指派"]
    return None, None, ["名单中无匹配姓名，请人工指派"]


# ---------------------------------------------------------------------------
# LLM 调用（含瞬时失败退避重试）
# ---------------------------------------------------------------------------


def _call_llm_with_retry(questions_desc: str, image_bytes: bytes):
    """返回 (payload, warnings)。payload=None 表示最终失败。

    G5：经熔断器--provider 持续不可用时 fast-fail，避免 3 worker 全耗在 120s 超时。
        仅可重试类失败（网络/5xx/429）计费熔断；非可重试（JSON/mock 空）不触发。
    G8：退避带 ±50% 抖动，避免 thundering herd。
    """
    client = get_client("vision")
    breaker = get_vision_breaker()
    last_err: Exception | None = None
    for attempt in range(len(_RETRY_BACKOFFS) + 1):  # 初次 + 2 次重试
        try:
            breaker.before_call()  # G5：open 态直接抛 CircuitOpenError，不触达 provider
        except CircuitOpenError as e:
            _log.warning("LLM 熔断 fast-fail", extra={"attempt": attempt, "error": str(e)})
            return None, [f"LLM 调用失败：{e}"]
        t0 = time.monotonic()
        try:
            payload = client.parse_json(
                RESPONSE_BATCH_SYSTEM,
                response_batch_user_prompt(questions_desc),
                image_bytes,
            )
            breaker.record_success()
            _log.info(
                "LLM 调用成功",
                extra={"attempt": attempt, "ms": round((time.monotonic() - t0) * 1000)},
            )
            return payload, []
        except Exception as e:  # noqa: BLE001  LLM/httpx 各类异常统一判定
            last_err = e
            if _is_retryable(e):
                breaker.record_failure()  # G5：仅 provider 可用性失败计费
            _log.warning(
                "LLM 调用失败",
                extra={
                    "attempt": attempt,
                    "ms": round((time.monotonic() - t0) * 1000),
                    "retryable": _is_retryable(e),
                    "error": str(e),
                },
            )
            if not _is_retryable(e) or attempt == len(_RETRY_BACKOFFS):
                break
            time.sleep(_RETRY_BACKOFFS[attempt] * random.uniform(0.5, 1.5))  # G8 jitter
    return None, [f"LLM 调用失败：{last_err}"]


def _is_retryable(err: Exception) -> bool:
    """网络/超时/5xx 可重试；JSON schema / mock 空 / 4xx 不可重试。"""
    if isinstance(err, httpx.HTTPStatusError):
        return err.response.status_code >= 500 or err.response.status_code == 429
    if isinstance(err, httpx.HTTPError):  # TransportError / TimeoutException
        return True
    if isinstance(err, LLMError):
        msg = str(err).lower()
        return not any(h in msg for h in _NON_RETRYABLE_HINTS)
    return False


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _new_session():
    """动态获取 SessionLocal（测试夹具会替换 app.db.SessionLocal）。"""
    from app import db as dbmod

    return dbmod.SessionLocal()


def _questions_desc(exam_template_id: int) -> str:
    with _new_session() as s:
        template = s.get(ExamTemplate, exam_template_id)
        if template is None:
            return ""
        questions = sorted(template.questions, key=lambda q: q.idx)
        return "\n".join(f"题{q.idx}：{q.q_type}，满分 {q.full_score:g}" for q in questions)


def _set_job_running(session, job_id: int) -> None:
    job = session.get(ParseJob, job_id)
    if job and job.status != "running":
        job.status = "running"


def _finalize_job(session, job_id: int) -> None:
    """同 job 下无 queued/parsing item 则置 done。"""
    items = list(
        session.scalars(
            select(ParseBatchItem).where(ParseBatchItem.parse_job_id == job_id)
        )
    )
    if items and all(it.status in _TERMINAL for it in items):
        job = session.get(ParseJob, job_id)
        if job:
            job.status = "done"


def _mark_item_failed(session, item_id: int, msg: str) -> None:
    it = session.get(ParseBatchItem, item_id)
    if it is None:
        return
    it.status = FAILED
    it.detected_name = None
    it.warnings = (it.warnings or []) + [msg]


def _read_tempfile(file_path: str | None) -> bytes | None:
    if not file_path:
        return None
    try:
        with open(file_path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _safe_remove(path: str | None) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass
