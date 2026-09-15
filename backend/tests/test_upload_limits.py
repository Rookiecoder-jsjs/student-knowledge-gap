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


def test_env_mb_helper_parses_and_falls_back(monkeypatch):
    """上限 env 旋钮：空值/非法值回落默认，非正值抬到下限 1。"""
    from app.upload_limits import MAX_FILE_BYTES, MAX_FILE_MB, _env_mb

    monkeypatch.setenv("SC_TEST_UPLOAD_MB", "25")
    assert _env_mb("SC_TEST_UPLOAD_MB", 10) == 25
    monkeypatch.setenv("SC_TEST_UPLOAD_MB", "")
    assert _env_mb("SC_TEST_UPLOAD_MB", 10) == 10
    monkeypatch.setenv("SC_TEST_UPLOAD_MB", "garbage")
    assert _env_mb("SC_TEST_UPLOAD_MB", 10) == 10
    monkeypatch.setenv("SC_TEST_UPLOAD_MB", "0")
    assert _env_mb("SC_TEST_UPLOAD_MB", 10) == 1

    # 默认值口径不变（10MB 单文件），字节换算一致
    assert MAX_FILE_MB == 10
    assert MAX_FILE_BYTES == 10 * 1024 * 1024
