"""Optional high-availability dependency checks and storage boundaries."""

from __future__ import annotations

import os
from pathlib import Path


def enabled() -> bool:
    return (os.environ.get("SC_HA_ENABLED") or "").lower() in {"1", "true", "yes"}


def redis_url() -> str:
    return (os.environ.get("SC_REDIS_URL") or "").strip()


def check_redis() -> tuple[bool, str]:
    """Ping Redis when HA mode is enabled; no-op in the default SQLite profile."""
    url = redis_url()
    if not enabled() or not url:
        return True, "disabled"
    try:
        import redis  # optional dependency, installed by the HA image/profile

        client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        client.close()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


class ObjectStore:
    """Small object-store boundary; local disk is the safe single-node default.

    Production can replace this implementation with S3/MinIO using the same
    key contract. The queue payload stores keys, never request-local bytes.
    """

    def __init__(self, root: str | None = None):
        self.root = Path(root or os.environ.get("SC_OBJECT_STORAGE_DIR", "./object-data"))

    def put(self, key: str, data: bytes) -> str:
        path = self._safe_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._safe_path(key).read_bytes()

    def _safe_path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if root != path and root not in path.parents:
            raise ValueError("object key escapes storage root")
        return path
