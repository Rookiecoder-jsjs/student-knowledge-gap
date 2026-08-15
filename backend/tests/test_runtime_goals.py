"""runtime-goals.md 各改进项的有效性测试（G1-G14 落地验证）。

每条测试对应一个 goal，断言其验收条件，而非仅覆盖既有功能。
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.llm.circuit import CircuitBreaker, CircuitOpenError, get_vision_breaker
from app.llm.client import LLMError, MockLLMClient, set_client
from app.main import app

# 复用 test_photo 的批量录入辅助（已含 _bootstrap_batch / _batch_upload 等）
from tests.test_photo import (
    _batch_payload,
    _batch_upload,
    _bootstrap_batch,
    _jpeg_bytes,
)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    """隔离临时库：替换 app.db / deps 的 SessionLocal + engine。"""
    import app.api.deps as deps_mod
    import app.db as dbmod
    from app import models  # noqa: F401
    from app.db import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'rt.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    new_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    original = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine, dbmod.SessionLocal = engine, new_session
    deps_mod.SessionLocal = new_session
    get_vision_breaker().reset()
    with TestClient(app) as c:
        yield c
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = original
    set_client(None)
    get_vision_breaker().reset()


@pytest.fixture()
def reset_llm():
    """不依赖 DB 的单测用：仅复位全局 LLM 客户端 + 熔断器。"""
    get_vision_breaker().reset()
    yield
    set_client(None)
    get_vision_breaker().reset()


def _item_db(item_id):
    from app.db import SessionLocal
    from app.models import ParseBatchItem

    with SessionLocal() as s:
        return s.scalar(select(ParseBatchItem).where(ParseBatchItem.id == item_id))


# ---------------------------------------------------------------------------
# G2 · SQLite WAL
# ---------------------------------------------------------------------------


def test_g2_sqlite_wal_enabled(tmp_path):
    """G2：sqlite 连接开 WAL + busy_timeout（经 Pool 类监听，任意 engine 生效）。"""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'g2.db'}", connect_args={"check_same_thread": False}
    )
    try:
        with engine.connect() as c:
            assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert c.execute(text("PRAGMA busy_timeout")).scalar() == 15000
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# G1 · worker 异常兜底
# ---------------------------------------------------------------------------


def test_g1_worker_exception_marks_failed(client, monkeypatch):
    """G1：worker 抛非 IntegrityError 异常 -> item 落 failed、job done，无 parsing 孤儿。"""
    from app.db import SessionLocal
    from app.ingestion import batch as batch_mod
    from app.models import ParseBatchItem, ParseJob

    exam_id, _ = _bootstrap_batch(client)
    tmp = tempfile.NamedTemporaryFile(
        delete=False, prefix=batch_mod.TEMPFILE_PREFIX, suffix=".jpg"
    )
    tmp.write(_jpeg_bytes())
    tmp.close()
    with SessionLocal() as s:
        job = ParseJob(
            target=f"batch:{exam_id}", model_version="mock",
            prompt_version="p", status="running",
        )
        s.add(job)
        s.flush()
        item = ParseBatchItem(
            parse_job_id=job.id, exam_template_id=exam_id, file_name="s.jpg",
            file_path=tmp.name, status="queued", warnings=[],
        )
        s.add(item)
        s.flush()
        item_id, job_id = item.id, job.id
        s.commit()

    set_client(MockLLMClient([_batch_payload("P01")]))  # 让流程走到 _persist_batch_result

    def boom(*a, **kw):
        raise RuntimeError("注入异常")

    monkeypatch.setattr(batch_mod, "_persist_batch_result", boom)
    batch_mod._process_async(item_id)  # 直接驱动 worker（同步模拟线程内执行）

    with SessionLocal() as s:
        it = s.get(ParseBatchItem, item_id)
        assert it.status == "failed"  # 不留 parsing 孤儿
        assert any("解析异常" in w for w in it.warnings)
        assert s.get(ParseJob, job_id).status == "done"
    try:
        os.remove(tmp.name)  # failed 保留 file_path，手动清理
    except OSError:
        pass


# ---------------------------------------------------------------------------
# G3 · payload_json PII 清洗
# ---------------------------------------------------------------------------


def test_g3_pii_scrub_on_terminal(client):
    """G3：matched/duplicate 终态 payload_json 清空；unmatched 保留（供指派）。"""
    exam_id, _ = _bootstrap_batch(client)

    # matched -> 清空
    r = _batch_upload(client, exam_id, [_batch_payload("P01")])
    assert r.json()["items"][0]["status"] == "matched"
    it = _item_db(r.json()["items"][0]["id"])
    assert it.payload_json is None
    assert it.detected_name is None

    # duplicate -> 清空
    r2 = _batch_upload(client, exam_id, [_batch_payload("P01")])
    assert r2.json()["items"][0]["status"] == "duplicate"
    assert _item_db(r2.json()["items"][0]["id"]).payload_json is None

    # unmatched -> 保留 payload_json（含 student_name，供教师指派免重调 LLM）
    r3 = _batch_upload(client, exam_id, [_batch_payload("无名氏")])
    assert r3.json()["items"][0]["status"] == "unmatched"
    u = _item_db(r3.json()["items"][0]["id"])
    assert u.payload_json is not None
    assert u.payload_json.get("student_name") == "无名氏"


# ---------------------------------------------------------------------------
# G5 · LLM 熔断器
# ---------------------------------------------------------------------------


def test_g5_circuit_breaker_states(reset_llm):
    """G5：closed -> open（阈值）-> half_open（冷却到期）-> closed（试探成功）。"""
    clock = [100.0]
    cb = CircuitBreaker(threshold=3, cooldown=60, clock=lambda: clock[0])
    assert cb.state == "closed"
    for _ in range(3):
        cb.before_call()  # closed 不抛
        cb.record_failure()
    assert cb.state == "open"
    with pytest.raises(CircuitOpenError):
        cb.before_call()
    clock[0] += 10
    assert cb.state == "open"  # 冷却期内仍 open
    clock[0] += 50  # 到期
    assert cb.state == "half_open"
    cb.before_call()
    cb.record_success()
    assert cb.state == "closed"


def test_g5_breaker_fast_fail_skips_provider(reset_llm, monkeypatch):
    """G5：熔断开启后 fast-fail，不再触达 provider。"""
    from app.ingestion import batch as batch_mod

    calls = {"n": 0}

    class AlwaysFail(MockLLMClient):
        def parse_json(self, *a, **kw):
            calls["n"] += 1
            raise LLMError("网络超时，请重试")

    monkeypatch.setattr(batch_mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(batch_mod.random, "uniform", lambda a, b: 1.0)
    cb = CircuitBreaker(threshold=2, cooldown=60, clock=lambda: 0.0)
    monkeypatch.setattr(batch_mod, "get_vision_breaker", lambda: cb)
    set_client(AlwaysFail([]))

    p1, _ = batch_mod._call_llm_with_retry("desc", _jpeg_bytes())
    assert p1 is None
    assert cb.state == "open"
    n_after_first = calls["n"]

    p2, w2 = batch_mod._call_llm_with_retry("desc", _jpeg_bytes())
    assert p2 is None
    assert any("熔断" in w for w in w2)
    assert calls["n"] == n_after_first  # 熔断期间 0 次 provider 调用


# ---------------------------------------------------------------------------
# G6 · 看门狗 + tempfile GC
# ---------------------------------------------------------------------------


def test_g6_watchdog_marks_stale_parsing_failed(client):
    """G6：parsing 超 BATCH_STALE_MINUTES -> 惰性改判 failed + 收尾 job。"""
    from app.db import SessionLocal
    from app.ingestion.batch import reconcile_stale_runtime
    from app.models import ParseBatchItem, ParseJob

    exam_id, _ = _bootstrap_batch(client)
    with SessionLocal() as s:
        job = ParseJob(
            target=f"batch:{exam_id}", model_version="m",
            prompt_version="p", status="running",
        )
        s.add(job)
        s.flush()
        s.add(
            ParseBatchItem(
                parse_job_id=job.id, exam_template_id=exam_id, file_name="z.jpg",
                file_path=None, status="parsing", warnings=[],
                started_at=datetime.utcnow() - timedelta(minutes=30),
            )
        )
        s.commit()
        job_id = job.id

    n = reconcile_stale_runtime(now=datetime.utcnow())
    assert n == 1
    with SessionLocal() as s:
        it = s.scalar(select(ParseBatchItem).where(ParseBatchItem.parse_job_id == job_id))
        assert it.status == "failed"
        assert any("超时" in w for w in it.warnings)
        assert s.get(ParseJob, job_id).status == "done"


def test_g6_tempfile_gc(tmp_path):
    """G6：孤儿 tempfile（旧 mtime + 未引用）删除；被引用/新文件保留。"""
    from app.ingestion.batch import TEMPFILE_PREFIX, gc_orphan_tempfiles

    orphan = tmp_path / (TEMPFILE_PREFIX + "orphan.jpg")
    orphan.write_bytes(b"x")
    kept = tmp_path / (TEMPFILE_PREFIX + "kept.jpg")
    kept.write_bytes(b"y")
    fresh = tmp_path / (TEMPFILE_PREFIX + "fresh.jpg")
    fresh.write_bytes(b"z")
    old = time.time() - 48 * 3600
    os.utime(orphan, (old, old))
    os.utime(kept, (old, old))

    removed = gc_orphan_tempfiles(
        referenced={str(kept)}, max_age_hours=24, tmp_dir=str(tmp_path)
    )
    assert removed == 1
    assert not orphan.exists()
    assert kept.exists()  # 被引用
    assert fresh.exists()  # 未超 max_age


# ---------------------------------------------------------------------------
# G7 · 结构化日志
# ---------------------------------------------------------------------------


def test_g7_structured_logs(client, caplog):
    """G7：批量解析产出结构化日志，关键字段经 extra 注入。"""
    from app.observability import setup_logging

    setup_logging()
    caplog.set_level(logging.INFO, logger="sc")
    exam_id, _ = _bootstrap_batch(client)
    _batch_upload(client, exam_id, [_batch_payload("P01")])

    done = [
        r for r in caplog.records
        if r.name.startswith("sc") and "解析完成" in r.getMessage()
    ]
    assert done, "缺少 batch item 解析完成 日志"
    assert hasattr(done[0], "status")  # extra 注入的结构化字段
    assert hasattr(done[0], "item_id")


# ---------------------------------------------------------------------------
# G8 · 重试 jitter
# ---------------------------------------------------------------------------


def test_g8_retry_jitter(reset_llm, monkeypatch):
    """G8：退避带 ±50% 抖动（sleep = backoff × uniform(0.5,1.5)）。"""
    from app.ingestion import batch as batch_mod

    sleeps: list[float] = []
    monkeypatch.setattr(batch_mod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(batch_mod.random, "uniform", lambda a, b: 0.75)

    class FailTwice(MockLLMClient):
        def __init__(self):
            super().__init__([_batch_payload("P01")])
            self._n = 0

        def parse_json(self, *a, **kw):
            self._n += 1
            if self._n <= 2:
                raise LLMError("网络超时，请重试")
            return super().parse_json(*a, **kw)

    cb = CircuitBreaker(threshold=100, cooldown=60, clock=lambda: 0.0)  # 不熔断
    monkeypatch.setattr(batch_mod, "get_vision_breaker", lambda: cb)
    set_client(FailTwice())

    payload, _ = batch_mod._call_llm_with_retry("desc", _jpeg_bytes())
    assert payload is not None
    # backoff (2.0, 6.0) × 0.75 -> 1.5, 4.5
    assert sleeps == [1.5, 4.5]


# ---------------------------------------------------------------------------
# G9 · 班级题均得分率 N+1
# ---------------------------------------------------------------------------


def test_g9_class_question_rates_constant_queries(session, env):
    """G9：_class_question_rates 查询数常数（不随学生数膨胀）。"""
    from app.models import ExamResponse, ResponseAnswer
    from app.pipeline.evidence import _class_question_rates
    from tests.conftest import make_exam

    kpid = env["kp"]["P1"]
    tpl = make_exam(
        session, env["class"].id, "e", date(2025, 9, 1), "单元",
        [
            (1, 10.0, "解答", "应用", [(kpid, 1.0)]),
            (2, 10.0, "解答", "应用", [(kpid, 1.0)]),
        ],
    )
    for sid in list(env["students"].values())[:3]:
        resp = ExamResponse(
            exam_template_id=tpl.id, student_id=sid, status="已提交", total_score=18.0
        )
        session.add(resp)
        session.flush()
        session.add(ResponseAnswer(exam_response_id=resp.id, template_question_id=tpl.questions[0].id, score=8.0))
        session.add(ResponseAnswer(exam_response_id=resp.id, template_question_id=tpl.questions[1].id, score=10.0))
    session.commit()

    counts = {"n": 0}

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _c(*a, **kw):
        counts["n"] += 1

    rates = _class_question_rates(session, tpl)
    assert counts["n"] <= 3  # 1 取题 + 1 join 取答案（常数）
    q1, q2 = sorted(tpl.questions, key=lambda q: q.idx)
    assert rates == {q1.id: 0.8, q2.id: 1.0}


# ---------------------------------------------------------------------------
# G12 · 并发数配置 + 进度暴露
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# G4 · 掌握度批量取事件（查询数不随规模膨胀）
# ---------------------------------------------------------------------------


def test_g4_assess_constant_queries_at_scale(tmp_path):
    """G4：assess_student_kps 查询数常数，不随 学生数×kp数 膨胀（原 N+1 千次级）。"""
    from app.kb.graph import KpGraph
    from app.models import (
        Class,
        EvidenceEvent,
        KbVersion,
        KnowledgePoint,
        School,
        Student,
        TeachingProgress,
    )
    from app.pipeline.weakness import assess_student_kps

    def build(n_students: int, n_kps: int):
        engine = create_engine(
            f"sqlite:///{tmp_path / f'scale_{n_students}_{n_kps}.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        s = sessionmaker(bind=engine, expire_on_commit=False)()
        kb = KbVersion(subject="数学", textbook_edition="t", version="t")
        s.add(kb)
        s.flush()
        kp_ids = []
        for i in range(n_kps):
            kp = KnowledgePoint(
                kb_version_id=kb.id, code=f"K{i}", name=f"k{i}", grade=7, mastery_floor=0.6
            )
            s.add(kp)
            s.flush()
            kp_ids.append(kp.id)
        school = School(name="s")
        s.add(school)
        s.flush()
        clazz = Class(school_id=school.id, name="c", grade=7)
        s.add(clazz)
        s.flush()
        for kpid in kp_ids:
            s.add(TeachingProgress(class_id=clazz.id, kp_id=kpid, taught_at=date(2025, 9, 1)))
        s.flush()
        stu_ids = []
        for i in range(n_students):
            stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias=f"S{i}")
            s.add(stu)
            s.flush()
            stu_ids.append(stu.id)
        occurred = datetime(2025, 10, 1, 12, 0)
        for sid in stu_ids:
            for kpid in kp_ids:
                for v in (0.9, 0.7, 0.5):  # 3 条 -> 过 MIN_EVIDENCE_COUNT 门槛
                    s.add(
                        EvidenceEvent(
                            student_id=sid, kp_id=kpid, response_answer_id=1,
                            source_type="单元", value=v, weight=1.0, cog_level="应用",
                            occurred_at=occurred, algo_version="t",
                        )
                    )
        s.commit()
        return engine, s, kb.id, clazz.id, stu_ids[0]

    as_of = datetime(2025, 11, 1, 23, 59)
    for n_stu, n_kp in [(5, 5), (20, 10)]:
        engine, s, kb_id, class_id, target_sid = build(n_stu, n_kp)
        graph = KpGraph(s, kb_id)
        counts = {"n": 0}

        @event.listens_for(engine, "before_cursor_execute")
        def _c(*a, **kw):
            counts["n"] += 1

        res = assess_student_kps(s, graph, target_sid, class_id, as_of)
        # 常数级：covered + student_ids + batch events（原为 学生数×kp 级千次查询）
        assert counts["n"] <= 10, f"N={n_stu},K={n_kp} -> {counts['n']} queries"
        assert any(a.mastery is not None for a in res)
        engine.dispose()


def test_g12_batch_workers_default():
    """G12：默认并发 3，可经 SC_BATCH_WORKERS 配置。"""
    from app.config import Settings

    assert Settings().batch_workers == 3
    from app.ingestion.batch import _make_executor

    assert _make_executor()._max_workers == 3


def test_g12_progress_in_batch_jobs(client):
    """G12：batch-jobs 响应带 total/done 进度。"""
    exam_id, _ = _bootstrap_batch(client)
    r = _batch_upload(client, exam_id, [_batch_payload("P01"), _batch_payload("无名氏")])
    job_id = r.json()["job_id"]

    job = client.get(f"/batch-jobs/{job_id}").json()
    assert job["total"] == 2
    assert job["done"] == 2  # matched + unmatched 均终态

    lst = client.get(f"/exams/{exam_id}/batch-jobs").json()
    assert lst["jobs"][0]["total"] == 2
    assert lst["jobs"][0]["done"] == 2


# ---------------------------------------------------------------------------
# G14 · lifespan
# ---------------------------------------------------------------------------


def test_g14_lifespan_replaces_on_event():
    """G14：startup/shutdown 迁 lifespan，on_event 处理器列表为空。"""
    assert app.router.on_startup == []
    assert app.router.on_shutdown == []


# ---------------------------------------------------------------------------
# G13 · LLM 幂等键
# ---------------------------------------------------------------------------


def test_g13_idempotency_key_header(monkeypatch):
    """G13：SC_LLM_IDEMPOTENCY_KEY 开启时透传 Idempotency-Key 头（内容哈希，稳定跨重试）。"""
    from app.llm import client as client_mod
    from app.llm.client import OpenAICompatClient

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"student_name":"x","answers":[]}'}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers or {}
        return FakeResp()

    monkeypatch.setattr(client_mod.httpx, "post", fake_post)
    c = OpenAICompatClient(api_key="k", model="m", base_url="https://x/v1")

    # 开启：头存在，同内容两次调用键一致（retry 不重复付费的关键）
    monkeypatch.setattr(client_mod, "LLM_IDEMPOTENCY_KEY", True)
    c.parse_json("sys", "u", b"img")
    key1 = captured["headers"].get("Idempotency-Key")
    assert key1 and len(key1) == 36
    c.parse_json("sys", "u", b"img")
    assert captured["headers"]["Idempotency-Key"] == key1
    # 不同内容 -> 不同键
    c.parse_json("sys", "u2", b"img")
    assert captured["headers"]["Idempotency-Key"] != key1

    # 关闭：无头
    monkeypatch.setattr(client_mod, "LLM_IDEMPOTENCY_KEY", False)
    c.parse_json("sys", "u", b"img")
    assert "Idempotency-Key" not in captured["headers"]


# ---------------------------------------------------------------------------
# G10 · Alembic 迁移 + 备份
# ---------------------------------------------------------------------------


def test_g10_alembic_upgrade_downgrade(tmp_path):
    """G10：alembic upgrade head 建全表（含 started_at/archived），downgrade base 可回滚。"""
    import sqlite3
    from alembic import command
    from alembic.config import Config

    db = tmp_path / "alembic.db"
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).resolve().parent.parent / "alembic")
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")

    command.upgrade(cfg, "head")
    c = sqlite3.connect(str(db))
    n_tables = c.execute(
        "select count(*) from sqlite_master where type='table' and name not like 'sqlite_%'"
    ).fetchone()[0]
    assert n_tables >= 19  # 18 模型表 + alembic_version
    assert (
        c.execute(
            "select count(*) from pragma_table_info('parse_batch_item') where name='started_at'"
        ).fetchone()[0]
        == 1
    )
    assert (
        c.execute(
            "select count(*) from pragma_table_info('knowledge_point') where name='archived'"
        ).fetchone()[0]
        == 1
    )
    c.close()

    command.downgrade(cfg, "base")
    c2 = sqlite3.connect(str(db))
    n_after = c2.execute(
        "select count(*) from sqlite_master where type='table' "
        "and name not like 'sqlite_%' and name != 'alembic_version'"
    ).fetchone()[0]
    assert n_after == 0
    c2.close()


def test_g10_backup_db(tmp_path):
    """G10：SQLite 在线热备产出可读副本。"""
    import sqlite3
    from scripts.backup_db import backup_db

    src = tmp_path / "src.db"
    con = sqlite3.connect(str(src))
    con.execute("create table t(x int)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()

    dest = backup_db(src_url=f"sqlite:///{src}", dest=str(tmp_path / "bak.db"))
    c = sqlite3.connect(dest)
    assert c.execute("select x from t").fetchone()[0] == 42
    c.close()
    assert Path(dest).exists()
