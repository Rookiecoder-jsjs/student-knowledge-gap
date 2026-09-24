"""Low-cardinality operational gauges; sampled only when metrics are scraped."""
from __future__ import annotations
import json
import logging
import math
import time
import os
import shutil
from pathlib import Path
from sqlalchemy import func, select
from app import db
from app.metrics import set_gauge
from app.models import AsyncJob

logger = logging.getLogger(__name__)


def collect() -> None:
    try:
        with db.SessionLocal() as session:
            for status in ("queued", "running", "failed"):
                count = session.scalar(select(func.count()).select_from(AsyncJob).where(AsyncJob.status == status))
                set_gauge("jobs_current", count or 0, labels={"status": status})
            oldest = session.scalar(select(func.min(AsyncJob.created_at)).where(AsyncJob.status == "queued"))
            set_gauge("jobs_oldest_wait_seconds", max(0, (db.utcnow() - oldest).total_seconds()) if oldest else 0)
            last_ok = session.scalar(select(func.max(AsyncJob.finished_at)).where(AsyncJob.status == "succeeded"))
            failures = select(func.count()).select_from(AsyncJob).where(AsyncJob.status == "failed")
            if last_ok:
                failures = failures.where(AsyncJob.finished_at > last_ok)
            set_gauge("jobs_failures_since_success", session.scalar(failures) or 0)
        set_gauge("ops_collect_ok", 1, labels={"source": "jobs"})
    except Exception:
        logger.exception("job metrics collection failed")
        set_gauge("ops_collect_ok", 0, labels={"source": "jobs"})

    data_path = Path(os.environ.get("SC_DATA_DIR") or (Path(db.engine.url.database or ".").parent
                     if db.engine.url.drivername.startswith("sqlite") and db.engine.url.database != ":memory:" else "."))
    try:
        usage = shutil.disk_usage(data_path)
        set_gauge("disk_available_bytes", usage.free)
        set_gauge("disk_available_ratio", usage.free / usage.total)
        set_gauge("ops_collect_ok", 1, labels={"source": "disk"})
    except OSError:
        set_gauge("ops_collect_ok", 0, labels={"source": "disk"})

    directory = os.environ.get("SC_BACKUP_DIR")
    set_gauge("backup_monitor_enabled", 1 if directory else 0)
    if directory:
        try:
            status = json.loads((Path(directory) / ".backup-status.json").read_text())
            timestamp = float(status["last_success_timestamp"])
            if not math.isfinite(timestamp) or timestamp <= 0 or timestamp > time.time() + 60 or status.get("restore_verified") is not True:
                raise ValueError("unverified backup")
            set_gauge("backup_last_success_timestamp_seconds", timestamp)
            set_gauge("ops_collect_ok", 1, labels={"source": "backup"})
        except (OSError, ValueError, KeyError, TypeError):
            set_gauge("backup_last_success_timestamp_seconds", 0)
            set_gauge("ops_collect_ok", 0, labels={"source": "backup"})
