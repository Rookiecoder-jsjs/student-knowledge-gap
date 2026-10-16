"""Scope survives reconnects and is checked before a model receives a turn."""

import asyncio
from copy import deepcopy
import json
import os
import stat

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from gateway import thread_scope as scopes


@pytest.mark.skipif(os.name != "posix", reason="POSIX per-teacher UID isolation")
def test_scope_database_is_private_including_existing_file(tmp_path):
    path = tmp_path / "scopes.db"
    store = scopes.ThreadScopeStore(path)
    store.bind(7, "thread", 3)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o644)
    assert store.lookup(7, "thread") == 3
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_thread_list_previews_hide_internal_context_and_keep_user_question(tmp_path):
    context = scopes._CONTEXT_INTRO + json.dumps({"id": 3, "name": scopes._CONTEXT_SUFFIX}) + scopes._CONTEXT_SUFFIX
    rows = [{"id": "full", "preview": context + "看看班级情况"},
            {"id": "truncated", "preview": context[:40]},
            {"id": "plain", "preview": "解释[知行班级范围]的意思"}]

    async def request(method, params):
        assert method == "thread/list"
        return {"data": rows, "nextCursor": "next"}

    result = call(tmp_path, "thread/list", {}, request)
    assert result == {"data": [
        {"id": "full", "preview": "看看班级情况"},
        {"id": "truncated", "preview": "班级教研会话"},
        {"id": "plain", "preview": "解释[知行班级范围]的意思"},
    ], "nextCursor": "next"}
    assert rows[0]["preview"].startswith(scopes.CLASS_CONTEXT_PREFIX)


def call(tmp_path, method, params, request, teacher_id=7):
    return asyncio.run(scopes.scoped_rpc(
        method, params, teacher_id=teacher_id, bearer_token="teacher-token",
        backend_url="http://backend:8000", scopes_file=tmp_path / "scopes.db", request=request,
    ))


@pytest.mark.parametrize("method", ["thread/read", "thread/resume"])
@pytest.mark.parametrize("merged", [False, True])
def test_history_hides_only_injected_context_and_keeps_teacher_input(tmp_path, method, merged):
    context = scopes._CONTEXT_INTRO + json.dumps({"id": 3, "name": scopes._CONTEXT_SUFFIX}) + scopes._CONTEXT_SUFFIX
    question = "[知行班级范围]是什么意思？"
    parts = ([{"type": "text", "text": context + "\n\n" + question}] if merged else [
        {"type": "text", "text": context}, {"type": "text", "text": question},
    ])
    original = {"thread": {"id": "legacy", "preview": context + question, "turns": [{
        "id": "turn", "items": [
            {"id": "user", "type": "userMessage", "content": parts},
            {"id": "answer", "type": "agentMessage", "text": "回答"},
            {"id": "tool", "type": "mcpToolCall", "tool": "read"},
        ],
    }]}, "cursor": "next"}
    before = deepcopy(original)

    async def request(actual_method, params):
        assert actual_method == method
        return original

    result = call(tmp_path, method, {"threadId": "legacy"}, request)
    assert result["thread"]["preview"] == question
    items = result["thread"]["turns"][0]["items"]
    assert items[0]["content"] == [{"type": "text", "text": question}]
    assert items[1:] == original["thread"]["turns"][0]["items"][1:]
    assert result["cursor"] == "next"
    assert original == before


@pytest.fixture()
def backend(monkeypatch):
    allowed = {3, 4}

    async def authorize(url, token, class_id):
        assert url == "http://backend:8000" and token == "teacher-token"
        if class_id not in allowed:
            raise HTTPException(403, "无访问权")
        return {"id": class_id, "name": f"班级{class_id}", "subject": "数学"}

    monkeypatch.setattr(scopes, "authorized_class", authorize)
    return allowed


def test_start_resume_and_turn_keep_authoritative_class_across_reconnect(tmp_path, backend):
    calls = []

    async def request(method, params):
        calls.append((method, params))
        return {"thread": {"id": "th-1", "turns": []}}

    started = call(tmp_path, "thread/start", {"classId": 3, "cwd": "/tmp"}, request)
    assert started["classScope"]["id"] == 3
    assert calls[0] == ("thread/start", {"cwd": "/tmp"})
    # Recreate the store to model a new gateway process, not an in-memory cache.
    assert scopes.ThreadScopeStore(tmp_path / "scopes.db").lookup(7, "th-1") == 3
    assert scopes.ThreadScopeStore(tmp_path / "scopes.db").lookup(8, "th-1") is None
    resumed = call(tmp_path, "thread/resume", {"threadId": "th-1", "classId": 4}, request)
    assert resumed["classScope"] == started["classScope"]
    original = {"threadId": "th-1", "classId": 3, "input": [{"type": "text", "text": "我们班怎么样"}]}
    call(tmp_path, "turn/start", original, request)
    forwarded = calls[-1][1]
    assert "classId" not in forwarded
    assert forwarded["input"][0]["text"].startswith(scopes.CLASS_CONTEXT_PREFIX)
    assert '"id": 3' in forwarded["input"][0]["text"]
    assert forwarded["input"][1:] == original["input"]
    assert len(original["input"]) == 1


def test_wrong_class_and_revoked_access_never_reach_runtime(tmp_path, backend):
    scopes.ThreadScopeStore(tmp_path / "scopes.db").bind(7, "th-1", 3)
    calls = []

    async def request(method, params):
        calls.append(method)
        return {}

    with pytest.raises(HTTPException) as wrong:
        call(tmp_path, "turn/start", {"threadId": "th-1", "classId": 4, "input": []}, request)
    assert wrong.value.status_code == 409
    backend.remove(3)
    for method in ("thread/resume", "turn/start"):
        with pytest.raises(HTTPException) as revoked:
            call(tmp_path, method, {"threadId": "th-1", "classId": 3, "input": []}, request)
        assert revoked.value.status_code == 403
    assert not calls


def test_legacy_thread_can_be_read_without_guessing_a_class_but_cannot_continue(tmp_path, backend):
    async def request(method, params):
        return {"thread": {"id": "legacy", "turns": []}}

    restored = call(tmp_path, "thread/resume", {"threadId": "legacy"}, request)
    assert restored["classScope"] is None
    with pytest.raises(HTTPException) as unbound:
        call(tmp_path, "turn/start", {"threadId": "legacy", "classId": 3, "input": []}, request)
    assert unbound.value.status_code == 409
    with pytest.raises(HTTPException) as invalid:
        call(tmp_path, "thread/start", {"classId": True}, request)
    assert invalid.value.status_code == 400


def test_scope_binding_cannot_be_reassigned(tmp_path):
    store = scopes.ThreadScopeStore(tmp_path / "scopes.db")
    store.bind(7, "th-1", 3)
    store.bind(7, "th-1", 3)
    with pytest.raises(HTTPException):
        store.bind(7, "th-1", 4)
    assert store.lookup(7, "th-1") == 3


def test_browser_rpc_enforces_scope_and_returns_authoritative_scope(monkeypatch, tmp_path):
    from gateway import main as gm
    from gateway.budget import BudgetGuard

    calls = []

    class Bridge:
        async def request(self, method, params):
            calls.append((method, params))
            return {"thread": {"id": "browser-thread", "turns": []}}

    async def bridge(username, teacher_id):
        return Bridge()

    async def authorize(url, token, class_id):
        if class_id != 3:
            raise HTTPException(403, "无访问权")
        return {"id": 3, "name": "三班", "subject": "数学"}

    monkeypatch.setattr(gm, "get_bridge", bridge)
    monkeypatch.setattr(gm, "GUARD", BudgetGuard())
    monkeypatch.setattr(gm, "THREADS_FILE", tmp_path / "threads.json")
    monkeypatch.setattr(scopes, "authorized_class", authorize)
    monkeypatch.setitem(gm.app.dependency_overrides, gm.require_auth, lambda: gm.Session(token="test", username="t7", teacher_id=7))
    client = TestClient(gm.app)
    started = client.post("/rpc", json={"method": "thread/start", "params": {"classId": 3}})
    assert started.status_code == 200
    assert started.json()["result"]["classScope"]["id"] == 3
    denied = client.post("/rpc", json={"method": "thread/start", "params": {"classId": 4}})
    assert denied.status_code == 403
    resumed = client.post("/rpc", json={"method": "thread/resume", "params": {"threadId": "browser-thread"}})
    assert resumed.json()["result"]["classScope"]["name"] == "三班"
    wrong = client.post("/rpc", json={"method": "turn/start", "params": {
        "threadId": "browser-thread", "classId": 4, "input": [{"type": "text", "text": "分析"}],
    }})
    assert wrong.status_code == 409
    assert [method for method, _ in calls] == ["thread/start", "thread/resume"]


@pytest.mark.parametrize("status, expected", [(200, None), (401, 401), (503, 503)])
def test_authorization_uses_backend_teacher_identity_and_fails_closed(monkeypatch, status, expected):
    client_type = httpx.AsyncClient

    def respond(request):
        assert request.url.path == "/classes"
        assert request.headers["Authorization"] == "Bearer teacher-token"
        return httpx.Response(status, json={"classes": [{"class_id": 3, "name": "三班", "subject": "数学"}]})

    monkeypatch.setattr(scopes.httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(respond), **kw))
    if expected:
        with pytest.raises(HTTPException) as failure:
            asyncio.run(scopes.authorized_class("http://backend:8000/", "teacher-token", 3))
        assert failure.value.status_code == expected
    else:
        actual = asyncio.run(scopes.authorized_class("http://backend:8000/", "teacher-token", 3))
        # The class default subject is not the caller's teaching subject.
        assert actual == {"id": 3, "name": "三班"}
