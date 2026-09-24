"""Restore a SQLite backup into an isolated temporary directory and verify it.

Never replaces the live database. Supports .bak and .bak.gz.
"""
from __future__ import annotations
import argparse
import gzip
import json
import shutil
import sqlite3
from contextlib import closing
import tempfile
from pathlib import Path

TABLES = ("school", "class", "student", "exam_template", "report")


def verify_restore(backup: str | Path) -> dict[str, int]:
    source = Path(backup).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="sc-restore-check-") as directory:
        restored = Path(directory) / "restored.db"
        if source.suffix == ".gz":
            with gzip.open(source, "rb") as reader, restored.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
        else:
            shutil.copyfile(source, restored)
        with closing(sqlite3.connect(restored.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("backup integrity check failed")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("backup contains broken foreign keys")
            # Fixed identifiers only; a valid but empty/unrelated DB must fail.
            return {table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in TABLES}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup")
    args = parser.parse_args()
    print(json.dumps({"status": "ok", "rows": verify_restore(args.backup)}, ensure_ascii=False))
