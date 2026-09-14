"""Request-boundary upload limits."""

from __future__ import annotations

import asyncio

import pytest

from app.upload_limits import UploadTooLargeError, read_upload, read_uploads


class _Upload:
    def __init__(self, payload: bytes, filename: str = "upload.bin"):
        self.filename = filename
        self._payload = payload
        self._offset = 0

    async def read(self, size: int) -> bytes:
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_read_upload_enforces_limit_while_streaming():
    upload = _Upload(b"x" * 11)

    with pytest.raises(UploadTooLargeError, match="测试文件超过"):
        asyncio.run(read_upload(upload, max_bytes=10, label="测试文件"))


def test_read_uploads_enforces_request_total():
    files = [_Upload(b"x" * 6, "a.jpg"), _Upload(b"y" * 6, "b.jpg")]

    with pytest.raises(UploadTooLargeError, match="整批超过"):
        asyncio.run(read_uploads(files, max_file_bytes=10, max_total_bytes=10))


def test_read_upload_returns_complete_payload_in_chunks():
    payload = b"x" * (2 * 1024 * 1024 + 17)

    result = asyncio.run(read_upload(_Upload(payload), max_bytes=len(payload), label="文件"))

    assert result == payload
