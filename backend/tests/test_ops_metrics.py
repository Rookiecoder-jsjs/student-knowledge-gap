from datetime import timedelta
import json
from types import SimpleNamespace
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app import jobs, ops_metrics, db
from app.backup_identity import database_identity
from app.metrics import render_prometheus


def test_queue_backup_and_disk_gauges(session, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=session.get_bind()))
    monkeypatch.setenv("SC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SC_BACKUP_DIR", str(tmp_path))
    (tmp_path / ".backup-status.json").write_text(json.dumps({
        "last_success_timestamp": 123456, "restore_verified": True,
        "database_identity": database_identity(db.engine.url),
    }))
    row = jobs.enqueue(session, "demo", {})
    row.created_at = db.utcnow() - timedelta(minutes=20)
    session.commit()
    ops_metrics.collect()
    metrics = render_prometheus()
    assert 'sc_jobs_current{status="queued"} 1' in metrics
    wait = float(next(line.split()[-1] for line in metrics.splitlines() if line.startswith("sc_jobs_oldest_wait_seconds ")))
    assert wait >= 1200
    assert "sc_backup_last_success_timestamp_seconds 123456" in metrics
    assert "sc_disk_available_ratio" in metrics
    (tmp_path / ".backup-status.json").write_text("broken")
    ops_metrics.collect()
    assert 'sc_ops_collect_ok{source="backup"} 0' in render_prometheus()


def test_backup_marker_must_match_current_database(session, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=session.get_bind()))
    # HA keeps the backup volume, while its live domain database is PostgreSQL.
    monkeypatch.setattr(db, "engine", SimpleNamespace(url=make_url("postgresql+psycopg://sc@postgres/sc")))
    monkeypatch.setenv("SC_BACKUP_DIR", str(tmp_path))
    marker = tmp_path / ".backup-status.json"
    for identity in (None, database_identity("sqlite:////old/sc.db")):
        marker.write_text(json.dumps({
            "last_success_timestamp": 123456, "restore_verified": True,
            "database_identity": identity,
        }))
        ops_metrics.collect()
        metrics = render_prometheus()
        assert "sc_backup_last_success_timestamp_seconds 0" in metrics
        assert 'sc_ops_collect_ok{source="backup"} 0' in metrics


def test_disabling_monitor_resets_previously_published_backup_time(session, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=session.get_bind()))
    monkeypatch.setenv("SC_BACKUP_DIR", str(tmp_path))
    (tmp_path / ".backup-status.json").write_text(json.dumps({
        "last_success_timestamp": 123456, "restore_verified": True,
        "database_identity": database_identity(db.engine.url),
    }))
    ops_metrics.collect()
    monkeypatch.setenv("SC_BACKUP_DIR", "")
    ops_metrics.collect()
    metrics = render_prometheus()
    assert "sc_backup_monitor_enabled 0" in metrics
    assert "sc_backup_last_success_timestamp_seconds 0" in metrics
