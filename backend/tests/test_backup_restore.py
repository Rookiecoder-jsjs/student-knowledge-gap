import gzip
import json
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine
from app.db import Base
from app import models  # noqa: F401
from scripts.backup_db import backup_db
from scripts.verify_restore import verify_restore


def test_backup_restores_in_isolation_and_publishes_verified_marker(tmp_path):
    source = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{source}")
    Base.metadata.create_all(engine)
    engine.dispose()
    with closing(sqlite3.connect(source)) as db:
        db.execute("INSERT INTO school(name) VALUES ('恢复演练')")
        db.commit()
    before = source.read_bytes()
    target = tmp_path / "backups/test.bak"
    backup_db(f"sqlite:///{source}", str(target))
    assert verify_restore(target)["school"] == 1
    assert source.read_bytes() == before
    marker = json.loads((target.parent / ".backup-status.json").read_text())
    assert marker["restore_verified"] is True
    assert marker["last_success_timestamp"] > 0
    compressed = tmp_path / "test.bak.gz"
    with gzip.open(compressed, "wb") as out:
        out.write(target.read_bytes())
    assert verify_restore(compressed)["school"] == 1


def test_bad_missing_or_live_destination_cannot_produce_success(tmp_path):
    source = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        backup_db(f"sqlite:///{source}", str(tmp_path / "missing.bak"))
    assert not source.exists()
    source.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        backup_db(f"sqlite:///{source}", str(source))
    with pytest.raises(sqlite3.DatabaseError):
        verify_restore(source)
    with pytest.raises(sqlite3.DatabaseError):
        backup_db(f"sqlite:///{source}", str(tmp_path / "bad.bak"))
    assert not (tmp_path / ".backup-status.json").exists()
    assert not (tmp_path / "bad.bak").exists()
