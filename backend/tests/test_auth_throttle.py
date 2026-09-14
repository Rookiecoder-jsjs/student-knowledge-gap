from __future__ import annotations

from app import auth_throttle


def test_local_throttle_blocks_after_repeated_failures(monkeypatch):
    monkeypatch.delenv("SC_HA_ENABLED", raising=False)
    monkeypatch.delenv("SC_REDIS_URL", raising=False)
    auth_throttle.reset_for_tests()

    for _ in range(auth_throttle.MAX_FAILURES - 1):
        auth_throttle.record_failure("teacher", "127.0.0.1")
        assert auth_throttle.check("teacher", "127.0.0.1") is None
    auth_throttle.record_failure("teacher", "127.0.0.1")
    retry_after = auth_throttle.check("teacher", "127.0.0.1")
    assert retry_after is not None and retry_after > 0


def test_success_clears_local_throttle(monkeypatch):
    monkeypatch.delenv("SC_HA_ENABLED", raising=False)
    monkeypatch.delenv("SC_REDIS_URL", raising=False)
    auth_throttle.reset_for_tests()
    for _ in range(auth_throttle.MAX_FAILURES):
        auth_throttle.record_failure("teacher", "127.0.0.1")
    assert auth_throttle.check("teacher", "127.0.0.1") is not None

    auth_throttle.record_success("teacher", "127.0.0.1")
    assert auth_throttle.check("teacher", "127.0.0.1") is None
