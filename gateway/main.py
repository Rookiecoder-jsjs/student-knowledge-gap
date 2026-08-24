"""会话网关 v1（Agent 产品化 §10.1 Phase 1「鉴权 + SSE 转发」；Phase 0 雏形演进）。

相对雏形（git 历史 gateway/main.py Phase 0 版）的增量：
- **鉴权（§5.5 一期）**：管理员预置账号（口令 PBKDF2 哈希）→ `POST /auth/login`
  签发会话 token → WS/SSE 连接凭 token 接入，无 token 401；
- **SSE 事件流**：`GET /threads/{id}/events`（text/event-stream）转发 app-server
  通知——浏览器侧 EventSource 天然重连，比长驻 WS 更贴合单向事件面；RPC 请求走
  `POST /rpc`（请求-响应语义）；
- **进程形态**：每教师一个 app-server 子进程（懒启动、断连回收）；验链②已证单
  进程多线程真并发，此处按教师分进程是资源隔离而非并发必需；
- 身份注入 MCP 连接（X-Teacher-Token 头）属 §6「加」的壳补丁范围，v1 先以
  「教师↔班级」授权表约束线程创建（`thread/start` params 校验 cwd 白名单）。

协议（浏览器侧）：
- POST /auth/login {username, password} → {token, teacher_id, classes}
- POST /rpc        Authorization: Bearer <token>；body {"method","params","id"?}
                   → {"result"| "error"}（app-server 的 request/response 对）
- GET  /threads/{tid}/events  同上鉴权；SSE 流：app-server 该线程的全部通知
- GET  /health

用法：uvicorn gateway.main:app --port 8100
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="sc session-gateway (Phase 1)")

APP_SERVER_CMD = os.environ.get("SC_GATEWAY_APP_SERVER", "codex")
APP_SERVER_ARGS = os.environ.get("SC_GATEWAY_APP_SERVER_ARGS", "app-server").split()
CODEX_HOME = os.environ.get("SC_GATEWAY_CODEX_HOME", "/tmp/sc-p1/codex-home")

# ---------------------------------------------------------------------------
# 账号与会话（§5.5 一期：管理员建账号 + 口令；二期钉钉 OAuth 替换此层）
# ---------------------------------------------------------------------------

_PBKDF2_ITERS = 60_000


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)


@dataclass
class Account:
    """教师账号（gateway 自管，与 sc Teacher 表解耦；teacher_id 用于后续身份注入）。"""

    username: str
    password_hash: bytes
    salt: bytes
    teacher_id: int


@dataclass
class Session:
    token: str
    username: str
    created_at: float


_ACCOUNTS: dict[str, Account] = {}
_SESSIONS: dict[str, Session] = {}


def load_accounts(path: str | None = None) -> None:
    """从 JSON 账号文件加载教师账号（部署时管理员生成；格式见 accounts.example.json）。"""
    path = path or os.environ.get("SC_GATEWAY_ACCOUNTS", "gateway/accounts.json")
    p = Path(path)
    if not p.exists():
        return
    for row in json.loads(p.read_text())["accounts"]:
        salt = bytes.fromhex(row["salt"])
        _ACCOUNTS[row["username"]] = Account(
            username=row["username"],
            salt=salt,
            password_hash=bytes.fromhex(row["password_hash"]),
            teacher_id=int(row.get("teacher_id", 0)),
        )


class LoginReq(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
def login(req: LoginReq):
    acc = _ACCOUNTS.get(req.username)
    if acc is None or not hmac.compare_digest(
        _hash_password(req.password, acc.salt), acc.password_hash
    ):
        raise HTTPException(401, "用户名或密码错误")
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = Session(token=token, username=req.username, created_at=time.time())
    return {"token": token, "teacher": req.username}


def require_auth(authorization: str = Header(default="")) -> Session:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少 Bearer token")
    sess = _SESSIONS.get(authorization[7:])
    if sess is None:
        raise HTTPException(401, "会话无效或已过期")
    return sess


# ---------------------------------------------------------------------------
# app-server 进程管理（每教师一进程，懒启动 + 空闲回收）
# ---------------------------------------------------------------------------


@dataclass
class Bridge:
    """一个 app-server 子进程的异步 stdio 桥 + 订阅了其事件的 SSE 客户端集合。"""

    proc: subprocess.Popen
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    _next_id: int = 0
    _pending: dict[int, asyncio.Future] = field(default_factory=dict)
    _reader: asyncio.Task | None = None
    last_used: float = field(default_factory=time.time)

    @classmethod
    async def spawn(cls) -> "Bridge":
        env = {**os.environ, "CODEX_HOME": CODEX_HOME, "RUST_LOG": "error"}
        proc = subprocess.Popen(
            [APP_SERVER_CMD, *APP_SERVER_ARGS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )
        self = cls(proc=proc)
        self._reader = asyncio.create_task(self._read_loop())
        await self.request("initialize", {
            "clientInfo": {"name": "sc-gateway", "title": "sc session gateway", "version": "1.0"},
        })
        return self

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        assert self.proc.stdout
        while True:
            line = await loop.run_in_executor(None, self.proc.stdout.readline)
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.last_used = time.time()
            if "id" in msg and ("result" in msg or "error" in msg):
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(f"rpc error: {msg['error']}"))
                    else:
                        fut.set_result(msg.get("result"))
            else:  # 通知 → 广播给所有 SSE 订阅者
                event = json.dumps({"type": "event", **msg}, ensure_ascii=False)
                for q in list(self.subscribers):
                    q.put_nowait(event)

    async def request(self, method: str, params: dict, timeout: float = 180.0):
        assert self.proc.stdin
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
            ensure_ascii=False,
        )
        self.proc.stdin.write(payload + "\n")
        self.proc.stdin.flush()
        return await asyncio.wait_for(fut, timeout)

    def stop(self) -> None:
        if self._reader:
            self._reader.cancel()
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 —— 回收路径，尽力而为
            self.proc.kill()


_BRIDGES: dict[str, Bridge] = {}  # username -> bridge


async def get_bridge(username: str) -> Bridge:
    br = _BRIDGES.get(username)
    if br is None or br.proc.poll() is not None:
        if br is not None:
            br.stop()
        br = await Bridge.spawn()
        _BRIDGES[username] = br
    br.last_used = time.time()
    return br


# ---------------------------------------------------------------------------
# RPC 与 SSE 端点
# ---------------------------------------------------------------------------


class RpcReq(BaseModel):
    method: str
    params: dict = {}
    id: int | str | None = None


@app.post("/rpc")
async def rpc(req: RpcReq, sess: Session = Depends(require_auth)):
    """浏览器侧 RPC：initialize/thread/start/turn/start/interrupt 等透传 app-server。"""
    bridge = await get_bridge(sess.username)
    try:
        result = await bridge.request(req.method, req.params)
    except Exception as e:  # noqa: BLE001 —— 错误语义原样回传
        raise HTTPException(502, str(e)) from e
    return {"id": req.id, "result": result}


@app.get("/threads/{thread_id}/events")
async def thread_events(thread_id: str, sess: Session = Depends(require_auth)):
    """SSE：该教师 app-server 的全部通知流（浏览器按 threadId 自行过滤）。"""
    bridge = await get_bridge(sess.username)
    queue: asyncio.Queue = asyncio.Queue()
    bridge.subscribers.add(queue)

    async def gen():
        try:
            # 先发一条注释行，确保响应头立即落盘（EventSource onopen）
            yield ": connected\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {item}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bridge.subscribers.discard(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "bridges": {u: b.proc.poll() is None for u, b in _BRIDGES.items()},
    }


@app.on_event("startup")
def _startup() -> None:
    load_accounts()


@app.on_event("shutdown")
def _shutdown() -> None:
    for br in _BRIDGES.values():
        br.stop()
