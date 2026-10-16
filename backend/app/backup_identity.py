"""Stable backup source identity without credentials in status files or metrics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy.engine import URL, make_url


def database_identity(url: str | URL) -> str:
    source = make_url(url)
    dialect = source.get_backend_name()
    database = source.database
    if dialect == "sqlite" and database and database != ":memory:":
        database = str(Path(database).resolve())
    identity = (dialect, source.host, source.port, source.username, database)
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()
