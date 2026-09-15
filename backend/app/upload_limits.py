"""Bounded reads for browser-uploaded files.

``UploadFile`` is backed by a spooled file, but calling ``read()`` without a
limit still copies the complete payload into application memory.  Keep the
limit at the request boundary so each route gets the same behaviour and error
message.
"""

from __future__ import annotations

import os
from collections.abc import Sequence


def _env_mb(name: str, default: int) -> int:
    """env 读取上限（MB）。空值/非法值回落默认（.env 模板允许 ``KEY=`` 空值）。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# 单文件 10MB（手机全分辨率照片可能超限，学校可经 SC_MAX_UPLOAD_FILE_MB 放宽）；
# 整批 100MB 需与 frontend/app/nginx.conf 的 client_max_body_size(110m) 同步调整。
MAX_FILE_MB = _env_mb("SC_MAX_UPLOAD_FILE_MB", 10)
MAX_BATCH_MB = _env_mb("SC_MAX_UPLOAD_BATCH_MB", 100)
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
MAX_TOTAL_BYTES = MAX_BATCH_MB * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024


class UploadTooLargeError(ValueError):
    """Raised when a single file or a request total exceeds its limit."""


async def read_upload(file, *, max_bytes: int, label: str) -> bytes:  # noqa: ANN001
    """Read one upload in bounded chunks.

    The check is performed while reading rather than trusting a client supplied
    ``Content-Length`` header.  At most ``max_bytes + READ_CHUNK_BYTES`` bytes
    are held before the request is rejected.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(f"{label}超过 {max_bytes // (1024 * 1024)}MB 上限")
        chunks.append(chunk)
    return b"".join(chunks)


async def read_uploads(
    files: Sequence,
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> list[tuple[str, bytes]]:
    """Read a batch without allowing an unbounded in-memory request."""
    uploads: list[tuple[str, bytes]] = []
    total = 0
    for index, file in enumerate(files):
        name = getattr(file, "filename", None) or f"file_{index}.jpg"
        raw = await read_upload(file, max_bytes=max_file_bytes, label=f"文件 {name}")
        total += len(raw)
        if total > max_total_bytes:
            raise UploadTooLargeError(
                f"整批超过 {max_total_bytes // (1024 * 1024)}MB 上限"
            )
        uploads.append((name, raw))
    return uploads
