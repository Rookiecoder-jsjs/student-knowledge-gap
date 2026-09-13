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
    jobs._complete(session, claimed.id, {"ok": True})
    session.commit()
    row = session.get(AsyncJob, claimed.id)
    assert row.status == "succeeded"
    assert row.result_json == {"ok": True}


def test_failed_job_requeues_then_becomes_terminal(session):
    job = jobs.enqueue(session, "demo", {}, max_attempts=2)
    session.commit()
    claimed = jobs._claim(session, "worker-a")
    assert claimed is not None
    jobs._fail(session, claimed.id, "upstream timeout")
    session.commit()
    assert session.get(AsyncJob, claimed.id).status == "queued"

    row = session.get(AsyncJob, claimed.id)
    row.available_at = jobs.utcnow()
    session.commit()
    claimed = jobs._claim(session, "worker-a")
    jobs._fail(session, claimed.id, "still unavailable")
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
