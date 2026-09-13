import asyncio

import httpx
import pytest

from llm_router.main import CapacityError, RouterConfig, RouterScheduler, create_app, parse_keys


def test_parse_keys_deduplicates_and_accepts_lines():
    assert parse_keys(" a,b\na\n c ") == ("a", "b", "c")


def test_scheduler_capacity_and_release():
    async def scenario():
        scheduler = RouterScheduler(RouterConfig("https://upstream", ("k",), "", max_concurrency=1, text_concurrency=1, key_concurrency=1, max_queue_wait=0.01))
        first = await scheduler.acquire("text")
        with pytest.raises(CapacityError):
            await scheduler.acquire("text")
        await first.release(success=True)
        second = await scheduler.acquire("text")
        await second.release(success=True)

    asyncio.run(scenario())


def test_proxy_replaces_provider_authorization_and_preserves_body():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = request.read()
        return httpx.Response(200, json={"choices": []})

    async def scenario():
        app = create_app(RouterConfig("https://upstream", ("provider-key",), "router-token"), transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            unauthorized = await client.post("/v1/chat/completions", json={"model": "m"})
            assert unauthorized.status_code == 401
            return await client.post("/v1/chat/completions", headers={"Authorization": "Bearer router-token"}, json={"model": "m", "messages": []})

    response = asyncio.run(scenario())
    assert response.status_code == 200
    assert seen["authorization"] == "Bearer provider-key"
    assert b'"model":"m"' in seen["body"]


def test_upstream_429_retries_with_mock_and_then_succeeds():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"message": "busy"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    config = RouterConfig("https://mock.invalid/v1", ("k",), "t", retries=1, backoff_base=0)
    async def scenario():
        app = create_app(config, transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/v1/chat/completions", headers={"Authorization": "Bearer t"}, json={"model": "m"})

    response = asyncio.run(scenario())
    assert response.status_code == 200
    assert calls == 2


def test_invalid_body_and_capacity_return_actionable_errors():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    config = RouterConfig("https://mock.invalid/v1", ("k",), "t", max_concurrency=1, text_concurrency=1, key_concurrency=1, max_queue_wait=0)
    async def scenario():
        app = create_app(config, transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            bad = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer t", "Content-Type": "application/json"}, content="[]")
            assert bad.status_code == 400
            lease = await app.state.scheduler.acquire("text")
            full = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer t"}, json={"model": "m"})
            await lease.release(success=True)
            return full

    full = asyncio.run(scenario())
    assert full.status_code == 429
    assert "retry-after" in {k.lower() for k in full.headers}


def test_no_provider_credentials_fail_closed_without_network():
    async def scenario():
        app = create_app(RouterConfig("https://mock.invalid/v1", (), ""), transport=httpx.MockTransport(lambda request: httpx.Response(200)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/v1/chat/completions", json={"model": "m"})

    response = asyncio.run(scenario())
    assert response.status_code == 429
    assert "provider credentials" in response.json()["error"]["message"]


def test_multiple_keys_are_rotated_for_sequential_requests():
    seen: list[str] = []

    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["authorization"])
            return httpx.Response(200, json={"ok": True})

        app = create_app(RouterConfig("https://mock.invalid/v1", ("a", "b"), "t", key_concurrency=1), transport=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(4):
                response = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer t"}, json={"model": "m"})
                assert response.status_code == 200

    asyncio.run(scenario())
    assert seen == ["Bearer a", "Bearer b", "Bearer a", "Bearer b"]
