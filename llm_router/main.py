"""OpenAI-compatible LLM router.

The router is deliberately stateless from the caller's perspective: provider keys
stay in the router process and callers receive only a short-lived/internal token.
The scheduler enforces global, capability and per-key concurrency plus optional
RPM/TPM windows.  A Redis-backed scheduler can be introduced behind the same
interface without changing API clients; the in-memory implementation keeps local
development dependency-free.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

log = logging.getLogger("sc.llm_router")


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default


def parse_keys(raw: str | None) -> tuple[str, ...]:
    """Parse comma/newline separated keys, preserving order and removing duplicates."""
    if not raw:
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in raw.replace("\r", "\n").replace(",", "\n").split("\n"):
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return tuple(result)


@dataclass(frozen=True)
class RouterConfig:
    upstream_base_url: str
    provider_keys: tuple[str, ...]
    inbound_token: str
    max_concurrency: int = 16
    agent_concurrency: int = 4
    vision_concurrency: int = 3
    text_concurrency: int = 4
    key_concurrency: int = 4
    key_rpm: int = 0
    key_tpm: int = 0
    max_queue_wait: float = 3.0
    retries: int = 2
    backoff_base: float = 0.25
    circuit_threshold: int = 5
    circuit_cooldown: float = 30.0
    fallback_models: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "RouterConfig":
        raw_keys = (
            os.environ.get("LLM_ROUTER_KEYS")
            or os.environ.get("SC_LLM_API_KEYS")
            or os.environ.get("SC_DEEPSEEK_API_KEY")
            or os.environ.get("SC_LLM_API_KEY")
        )
        base_url = (
            os.environ.get("LLM_ROUTER_BASE_URL")
            or os.environ.get("SC_LLM_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        fallbacks = parse_keys(os.environ.get("LLM_ROUTER_FALLBACK_MODELS"))
        return cls(
            upstream_base_url=base_url,
            provider_keys=parse_keys(raw_keys),
            inbound_token=os.environ.get("LLM_ROUTER_TOKEN")
            or os.environ.get("SC_LLM_ROUTER_TOKEN", ""),
            max_concurrency=max(1, _int_env("LLM_ROUTER_MAX_CONCURRENCY", 16)),
            agent_concurrency=max(1, _int_env("LLM_ROUTER_AGENT_CONCURRENCY", 4)),
            vision_concurrency=max(1, _int_env("LLM_ROUTER_VISION_CONCURRENCY", 3)),
            text_concurrency=max(1, _int_env("LLM_ROUTER_TEXT_CONCURRENCY", 4)),
            key_concurrency=max(1, _int_env("LLM_ROUTER_KEY_CONCURRENCY", 4)),
            key_rpm=_int_env("LLM_ROUTER_KEY_RPM", 0),
            key_tpm=_int_env("LLM_ROUTER_KEY_TPM", 0),
            max_queue_wait=_float_env("LLM_ROUTER_MAX_QUEUE_WAIT", 3.0),
            retries=_int_env("LLM_ROUTER_RETRIES", 2),
            backoff_base=_float_env("LLM_ROUTER_BACKOFF_BASE", 0.25),
            circuit_threshold=max(1, _int_env("LLM_ROUTER_CIRCUIT_THRESHOLD", 5)),
            circuit_cooldown=_float_env("LLM_ROUTER_CIRCUIT_COOLDOWN", 30.0),
            fallback_models=fallbacks,
        )


class CapacityError(RuntimeError):
    def __init__(self, retry_after: float, message: str = "LLM router capacity exhausted"):
        super().__init__(message)
        self.retry_after = max(0.05, retry_after)


@dataclass
class _KeyState:
    key: str
    in_flight: int = 0
    failures: int = 0
    opened_until: float = 0.0
    request_times: deque[float] = field(default_factory=deque)
    token_events: deque[tuple[float, int]] = field(default_factory=deque)


@dataclass
class Lease:
    scheduler: "RouterScheduler"
    key_index: int
    capability: str
    estimate_tokens: int
    released: bool = False

    async def release(self, *, success: bool, actual_tokens: int | None = None) -> None:
        if self.released:
            return
        self.released = True
        await self.scheduler.release(self, success=success, actual_tokens=actual_tokens)


class RouterScheduler:
    """Bounded scheduler.  It is safe for async callers and intentionally fair."""

    def __init__(self, config: RouterConfig):
        self.config = config
        self._keys = [_KeyState(k) for k in config.provider_keys]
        self._condition = asyncio.Condition()
        self._global_in_flight = 0
        self._cap_in_flight: dict[str, int] = {}
        self._cursor = 0
        self._requests = 0
        self._failures = 0
        self._rejections = 0

    def _cap_limit(self, capability: str) -> int:
        if capability == "agent":
            return self.config.agent_concurrency
        if capability == "vision":
            return self.config.vision_concurrency
        return self.config.text_concurrency

    @staticmethod
    def _prune(state: _KeyState, now: float) -> None:
        cutoff = now - 60.0
        while state.request_times and state.request_times[0] <= cutoff:
            state.request_times.popleft()
        while state.token_events and state.token_events[0][0] <= cutoff:
            state.token_events.popleft()

    async def acquire(self, capability: str, estimate_tokens: int = 0) -> Lease:
        if not self._keys:
            self._rejections += 1
            raise CapacityError(60.0, "provider credentials are not configured")
        deadline = time.monotonic() + self.config.max_queue_wait
        async with self._condition:
            while True:
                now = time.monotonic()
                candidate: tuple[int, _KeyState] | None = None
                retry_after = max(0.05, deadline - now)
                for offset in range(len(self._keys)):
                    index = (self._cursor + offset) % len(self._keys)
                    state = self._keys[index]
                    self._prune(state, now)
                    if state.opened_until > now:
                        retry_after = min(retry_after, state.opened_until - now)
                        continue
                    if state.in_flight >= self.config.key_concurrency:
                        continue
                    if self.config.key_rpm and len(state.request_times) >= self.config.key_rpm:
                        retry_after = min(retry_after, 60.0 - (now - state.request_times[0]))
                        continue
                    if self.config.key_tpm and sum(v for _, v in state.token_events) + estimate_tokens > self.config.key_tpm:
                        retry_after = min(retry_after, 60.0 - (now - state.token_events[0][0]))
                        continue
                    candidate = (index, state)
                    break
                cap_count = self._cap_in_flight.get(capability, 0)
                if candidate and self._global_in_flight < self.config.max_concurrency and cap_count < self._cap_limit(capability):
                    index, state = candidate
                    state.in_flight += 1
                    state.request_times.append(now)
                    if estimate_tokens:
                        state.token_events.append((now, estimate_tokens))
                    self._global_in_flight += 1
                    self._cap_in_flight[capability] = cap_count + 1
                    self._cursor = (index + 1) % len(self._keys)
                    self._requests += 1
                    return Lease(self, index, capability, estimate_tokens)
                remaining = deadline - now
                if remaining <= 0:
                    self._rejections += 1
                    raise CapacityError(retry_after)
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=min(remaining, retry_after))
                except asyncio.TimeoutError:
                    if time.monotonic() >= deadline:
                        self._rejections += 1
                        raise CapacityError(retry_after) from None

    async def release(self, lease: Lease, *, success: bool, actual_tokens: int | None) -> None:
        async with self._condition:
            state = self._keys[lease.key_index]
            state.in_flight = max(0, state.in_flight - 1)
            self._global_in_flight = max(0, self._global_in_flight - 1)
            self._cap_in_flight[lease.capability] = max(0, self._cap_in_flight.get(lease.capability, 1) - 1)
            if actual_tokens is not None and lease.estimate_tokens:
                delta = actual_tokens - lease.estimate_tokens
                if delta > 0:
                    state.token_events.append((time.monotonic(), delta))
            if success:
                state.failures = 0
            else:
                self._failures += 1
                state.failures += 1
                if state.failures >= self.config.circuit_threshold:
                    state.opened_until = time.monotonic() + self.config.circuit_cooldown
                    state.failures = 0
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        for state in self._keys:
            self._prune(state, now)
        return {
            "keys": len(self._keys),
            "global_in_flight": self._global_in_flight,
            "capability_in_flight": dict(self._cap_in_flight),
            "key_in_flight": [s.in_flight for s in self._keys],
            "requests_total": self._requests,
            "failures_total": self._failures,
            "capacity_rejections_total": self._rejections,
        }


def _estimate_tokens(payload: dict[str, Any]) -> int:
    return max(1, min(200_000, len(json.dumps(payload, ensure_ascii=False)) // 4))


def _capability(request: Request) -> str:
    value = (request.headers.get("x-sc-llm-capability") or "text").lower()
    return value if value in {"text", "vision", "agent"} else "text"


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        return max(0.05, float(response.headers.get("retry-after", default)))
    except (TypeError, ValueError):
        return default


def _safe_headers(headers: httpx.Headers) -> dict[str, str]:
    blocked = {"content-length", "transfer-encoding", "connection", "keep-alive"}
    return {k: v for k, v in headers.items() if k.lower() not in blocked}


def create_app(config: RouterConfig | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    config = config or RouterConfig.from_env()
    if not config.provider_keys:
        log.warning("LLM router has no provider credentials; requests will fail closed")
    if not config.inbound_token:
        log.warning("LLM router inbound token is unset; authentication bypass is intended for local development only")
    scheduler = RouterScheduler(config)
    client = httpx.AsyncClient(base_url=config.upstream_base_url, transport=transport, timeout=120.0)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await client.aclose()

    app = FastAPI(title="SC LLM Router", version="1.0.0", lifespan=lifespan)
    app.state.scheduler = scheduler
    app.state.config = config
    app.state.http_client = client

    async def _authorize(authorization: str | None) -> None:
        if not config.inbound_token:
            return
        expected = f"Bearer {config.inbound_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid router token")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> Response:
        snapshot = scheduler.snapshot()
        lines = [
            "# TYPE sc_llm_router_requests_total counter",
            f"sc_llm_router_requests_total {snapshot['requests_total']}",
            "# TYPE sc_llm_router_failures_total counter",
            f"sc_llm_router_failures_total {snapshot['failures_total']}",
            "# TYPE sc_llm_router_capacity_rejections_total counter",
            f"sc_llm_router_capacity_rejections_total {snapshot['capacity_rejections_total']}",
            "# TYPE sc_llm_router_in_flight gauge",
            f"sc_llm_router_in_flight {snapshot['global_in_flight']}",
        ]
        return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    async def proxy(request: Request, authorization: str | None) -> Response:
        await _authorize(authorization)
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="request body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="request body must be an object")
        capability = _capability(request)
        estimate = _estimate_tokens(payload)
        models = [str(payload.get("model") or "")] + [m for m in config.fallback_models if m != payload.get("model")]
        models = [m for m in models if m] or [""]
        stream = bool(payload.get("stream"))
        last_error: Response | None = None
        for attempt in range(config.retries + 1):
            body = dict(payload)
            body["model"] = models[min(attempt, len(models) - 1)]
            try:
                lease = await scheduler.acquire(capability, estimate)
            except CapacityError as exc:
                return Response(
                    content=json.dumps({"error": {"message": str(exc), "type": "capacity_exhausted"}}),
                    status_code=429,
                    media_type="application/json",
                    headers={"Retry-After": str(round(exc.retry_after, 2))},
                )
            headers = {"content-type": "application/json", "authorization": f"Bearer {self_key(config, lease.key_index)}"}
            for name in ("idempotency-key", "x-request-id"):
                if value := request.headers.get(name):
                    headers[name] = value
            try:
                if stream:
                    stream_cm = client.stream(
                        "POST", "/chat/completions" if request.url.path.endswith("chat/completions") else "/responses",
                        headers=headers, json=body,
                    )
                    upstream = await stream_cm.__aenter__()
                    if upstream.status_code in {408, 409, 429, 500, 502, 503, 504}:
                        await upstream.aread()
                        await stream_cm.__aexit__(None, None, None)
                        await lease.release(success=False)
                        last_error = Response(content=upstream.content, status_code=upstream.status_code, headers=_safe_headers(upstream.headers))
                    else:
                        async def iterator() -> AsyncIterator[bytes]:
                            ok = False
                            try:
                                async for chunk in upstream.aiter_raw():
                                    yield chunk
                                ok = 200 <= upstream.status_code < 400
                            finally:
                                await stream_cm.__aexit__(None, None, None)
                                await lease.release(success=ok)

                        return StreamingResponse(iterator(), status_code=upstream.status_code, headers=_safe_headers(upstream.headers), media_type=upstream.headers.get("content-type"))
                else:
                    upstream = await client.request(
                        "POST", "/chat/completions" if request.url.path.endswith("chat/completions") else "/responses",
                        headers=headers, json=body,
                    )
                    retryable = upstream.status_code in {408, 409, 429, 500, 502, 503, 504}
                    await lease.release(success=not retryable)
                    if retryable:
                        last_error = Response(content=upstream.content, status_code=upstream.status_code, headers=_safe_headers(upstream.headers))
                    else:
                        return Response(content=upstream.content, status_code=upstream.status_code, headers=_safe_headers(upstream.headers), media_type=upstream.headers.get("content-type"))
            except (httpx.HTTPError, RuntimeError) as exc:
                await lease.release(success=False)
                last_error = Response(content=json.dumps({"error": {"message": str(exc), "type": "upstream_error"}}), status_code=502, media_type="application/json")
            if attempt < config.retries:
                delay = _retry_after(last_error, config.backoff_base * (2**attempt)) if last_error else config.backoff_base
                await asyncio.sleep(delay + random.random() * config.backoff_base)
        return last_error or Response(status_code=502)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request, authorization: str | None = Header(default=None)) -> Response:
        return await proxy(request, authorization)

    @app.post("/v1/responses")
    async def responses(request: Request, authorization: str | None = Header(default=None)) -> Response:
        return await proxy(request, authorization)

    return app


def self_key(config: RouterConfig, index: int) -> str:
    return config.provider_keys[index] if config.provider_keys else ""


app = create_app()
