"""Bounded reads for browser-uploaded files.

``UploadFile`` is backed by a spooled file, but calling ``read()`` without a
limit still copies the complete payload into application memory.  Keep the
limit at the request boundary so each route gets the same behaviour and error
message.
"""

from __future__ import annotations

from collections.abc import Sequence


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
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
