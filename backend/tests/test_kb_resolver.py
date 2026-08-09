"""候选5a：active 知识库解析统一策略（kb/resolver.py）。"""

from __future__ import annotations

import pytest

from app.kb.resolver import KbNotActiveError, active_kb, strict_active
from app.models import KbVersion


def _mk_kb(session, status: str) -> KbVersion:
    kb = KbVersion(subject="数学", textbook_edition="测试版", version="t", status=status)
    session.add(kb)
    session.flush()
    return kb


def test_strict_active_flag(monkeypatch):
    assert strict_active() is False
    monkeypatch.setenv("SC_KB_STRICT_ACTIVE", "1")
    assert strict_active() is True
    monkeypatch.setenv("SC_KB_STRICT_ACTIVE", "true")
    assert strict_active() is True
    monkeypatch.setenv("SC_KB_STRICT_ACTIVE", "0")
    assert strict_active() is False


def test_active_kb_returns_active_when_present(session):
    draft = _mk_kb(session, "draft")
    active = _mk_kb(session, "active")
    got = active_kb(session)
    assert got is not None
    assert got.id == active.id
    assert got.id != draft.id


def test_active_kb_falls_back_to_latest_draft_when_not_strict(session, monkeypatch):
    monkeypatch.setenv("SC_KB_STRICT_ACTIVE", "0")
    _mk_kb(session, "draft")
    kb = active_kb(session)
    assert kb is not None
    assert kb.status == "draft"


def test_active_kb_returns_none_when_no_versions_not_strict(session, monkeypatch):
    monkeypatch.setenv("SC_KB_STRICT_ACTIVE", "0")
    assert active_kb(session) is None


def test_active_kb_raises_when_strict_and_no_active(session, monkeypatch):
    monkeypatch.setenv("SC_KB_STRICT_ACTIVE", "1")
    _mk_kb(session, "draft")
    with pytest.raises(KbNotActiveError):
        active_kb(session)


def test_active_kb_strict_prefers_active_even_if_draft_latest(session, monkeypatch):
    monkeypatch.setenv("SC_KB_STRICT_ACTIVE", "1")
    draft = _mk_kb(session, "draft")
    active = _mk_kb(session, "active")
    got = active_kb(session)
    assert got.id == active.id
    assert got.id != draft.id
