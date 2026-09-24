from app import jobs
from app.models import AsyncJob


def test_enqueue_is_idempotent_and_claim_completes(session):
    first = jobs.enqueue(session, "demo", {"value": 1}, idempotency_key="demo:1")
    session.commit()
    second = jobs.enqueue(session, "demo", {"value": 2}, idempotency_key="demo:1")
    assert second.id == first.id
    assert second.payload_json == {"value": 1}

    claimed = jobs._claim(session, "worker-a")
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.attempts == 1
    jobs._complete(session, claimed.id, {"ok": True}, worker_id="worker-a", attempt=1)
    session.commit()
    row = session.get(AsyncJob, claimed.id)
    assert row.status == "succeeded"
    assert row.result_json == {"ok": True}


def test_failed_job_requeues_then_becomes_terminal(session):
    job = jobs.enqueue(session, "demo", {}, max_attempts=2)
    session.commit()
    claimed = jobs._claim(session, "worker-a")
    assert claimed is not None
    jobs._fail(session, claimed.id, "upstream timeout", worker_id="worker-a", attempt=1)
    session.commit()
    assert session.get(AsyncJob, claimed.id).status == "queued"

    row = session.get(AsyncJob, claimed.id)
    row.available_at = jobs.utcnow()
    session.commit()
    claimed = jobs._claim(session, "worker-a")
    jobs._fail(session, claimed.id, "still unavailable", worker_id="worker-a", attempt=2)
    session.commit()
    row = session.get(AsyncJob, job.id)
    assert row.status == "failed"
    assert "still unavailable" in row.error


def test_object_store_rejects_path_escape(tmp_path):
    from app.ha import ObjectStore

    store = ObjectStore(str(tmp_path))
    assert store.put("safe/item.bin", b"ok") == "safe/item.bin"
    assert store.get("safe/item.bin") == b"ok"
    try:
        store.put("../escape", b"no")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal must be rejected")


def test_stale_attempt_cannot_finish_fail_or_renew(session):
    from datetime import timedelta
    row = jobs.enqueue(session, "demo", {})
    session.commit()
    jobs._claim(session, "worker-a")
    session.commit()
    row.locked_at = jobs.utcnow() - timedelta(seconds=jobs.LEASE_SECONDS + 1)
    session.commit()
    replacement = jobs._claim(session, "worker-b")
    session.commit()
    assert replacement.attempts == 2
    assert not jobs._complete(session, row.id, {"old": True}, worker_id="worker-a", attempt=1)
    assert not jobs._fail(session, row.id, "late error", worker_id="worker-a", attempt=1)
    assert not jobs._renew(session, row.id, "worker-a", 1)
    assert jobs._renew(session, row.id, "worker-b", 2)
    assert jobs._complete(session, row.id, {"new": True}, worker_id="worker-b", attempt=2)
    session.commit()
    session.refresh(row)
    assert row.result_json == {"new": True}


def test_expired_final_attempt_is_failed(session):
    from datetime import timedelta
    row = jobs.enqueue(session, "demo", {}, max_attempts=1)
    session.commit()
    jobs._claim(session, "worker-a")
    row.locked_at = jobs.utcnow() - timedelta(seconds=jobs.LEASE_SECONDS + 1)
    session.commit()
    assert jobs._claim(session, "worker-b") is None
    session.refresh(row)
    assert row.status == "failed"


def test_domain_writes_rollback_after_lease_loss(session, env):
    import pytest
    row = jobs.enqueue(session, "demo", {})
    session.commit()
    jobs._claim(session, "worker-new")
    session.commit()
    token = jobs._current_claim.set((row.id, "worker-old", 1))
    previous = env["class"].name
    try:
        env["class"].name = "must rollback"
        session.flush()
        with pytest.raises(jobs.LeaseLost):
            jobs._commit_result(session, {"ok": True})
        assert env["class"].name == previous
    finally:
        jobs._current_claim.reset(token)


def test_success_and_domain_data_commit_together(session, env):
    row = jobs.enqueue(session, "demo", {})
    session.commit()
    jobs._claim(session, "worker-a")
    session.commit()
    token = jobs._current_claim.set((row.id, "worker-a", 1))
    try:
        env["class"].name = "committed once"
        jobs._commit_result(session, {"ok": True})
        session.expire_all()
        assert row.status == "succeeded"
        assert env["class"].name == "committed once"
        assert jobs._claim(session, "worker-b") is None
    finally:
        jobs._current_claim.reset(token)


def test_expired_unreplaced_owner_can_commit_sqlite_transaction(session):
    from datetime import timedelta
    row = jobs.enqueue(session, "demo", {})
    session.commit()
    jobs._claim(session, "worker-a")
    row.locked_at = jobs.utcnow() - timedelta(seconds=jobs.LEASE_SECONDS + 1)
    session.commit()
    # SQLite's writer lock can block a heartbeat; a successor must still be
    # fenced, but expiry alone must not discard a completed long transaction.
    assert jobs._complete(session, row.id, {"ok": True}, worker_id="worker-a", attempt=1)
    session.commit()
    assert jobs._claim(session, "worker-b") is None


def test_heartbeat_renews_using_independent_session(tmp_path, monkeypatch):
    import threading
    from datetime import timedelta
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    engine = create_engine(f"sqlite:///{tmp_path / 'lease.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        row = jobs.enqueue(db, "demo", {})
        db.commit()
        jobs._claim(db, "worker-a")
        before = jobs.utcnow() - timedelta(seconds=5)
        row.locked_at = before
        db.commit()
        job_id = row.id
    renewed = threading.Event()
    original = jobs._renew
    def observe(db, *args):
        result = original(db, *args)
        if result:
            renewed.set()
        return result
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    monkeypatch.setattr(jobs, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(jobs, "_renew", observe)
    try:
        with jobs._heartbeat(job_id, "worker-a", 1):
            assert renewed.wait(timeout=3)
        with factory() as db:
            assert db.get(AsyncJob, job_id).locked_at > before
    finally:
        engine.dispose()
