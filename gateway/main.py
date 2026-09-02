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
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from gateway.budget import GUARD, BudgetExceeded

app = FastAPI(title="sc session-gateway (Phase 1)")

# §5.7 月度软限额巡查循环（lifespan 启停；钩子=钉钉/日志）
_STOP: list = [False]


@app.on_event("startup")
async def _start_monthly_watch() -> None:
    import asyncio

    from gateway import monthly_usage

    def _usage_notify(payload: dict) -> None:
        print(f"[monthly-usage] {payload['message']} "
              f"({payload['used_tokens']}/{payload['limit']})")
        from gateway import dingtalk

        dingtalk.notify_monthly_usage(payload["message"])

    monthly_usage.register_monthly_notify_hook(_usage_notify)
    asyncio.create_task(monthly_usage.monthly_watch_loop(_STOP))
    # Phase 4 批次B：每日出站心跳（§8.3；SC_HEARTBEAT_URL 未配置=静默空转）
    from gateway import heartbeat

    asyncio.create_task(heartbeat.heartbeat_loop(_STOP))
    # Phase 4 批次C：rollout 保留期限清理（§9；SC_RETENTION_ROLLOUT_DAYS=0=永不删）
    from gateway import retention

    asyncio.create_task(retention.retention_loop(_STOP))


@app.on_event("shutdown")
async def _stop_monthly_watch() -> None:
    _STOP[0] = True

APP_SERVER_CMD = os.environ.get("SC_GATEWAY_APP_SERVER", "codex")
APP_SERVER_ARGS = os.environ.get("SC_GATEWAY_APP_SERVER_ARGS", "app-server").split()
CODEX_HOME = os.environ.get("SC_GATEWAY_CODEX_HOME", "/tmp/sc-p1/codex-home")
# §5.4 触发器 v1：sc backend → gateway 内网共享密钥（双方一致才放行 /internal/*）
INTERNAL_KEY = os.environ.get("SC_TRIGGER_KEY", "")
# §6.3 school-authz：与 sc 后端共享的身份签名密钥（SC_AUTH_SECRET 配置后，教师身份
# 由 gateway 签为 HMAC token、由壳侧 shim 校验注入，替代「裸 SC_MCP_TEACHER_ID env
# 可信」的兜底路线；未配置=维持旧兜底，灰度安全）
SCHOOL_AUTH_SECRET = os.environ.get("SC_AUTH_SECRET", "")
_SCHOOL_TOKEN_TTL_S = 3600
# 班级持久线程映射落盘（§5.6 一班一线程跨学期滚动；JSON {str(class_id): thread_id}）
THREADS_FILE = Path(os.environ.get("SC_GATEWAY_THREADS_FILE", "/data/threads.json"))

# ---------------------------------------------------------------------------
# 账号与会话（§5.5 一期：管理员建账号 + 口令；二期钉钉 OAuth 替换此层）
# ---------------------------------------------------------------------------

_PBKDF2_ITERS = 60_000


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)


@dataclass
class Account:
    """教师账号（gateway 自管；teacher_id 注入 MCP 连接做班级过滤，G11 §5.5）。"""

    username: str
    password_hash: bytes
    salt: bytes
    teacher_id: int


@dataclass
class Session:
    token: str
    username: str
    teacher_id: int = 0
    created_at: float = 0.0


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
    _SESSIONS[token] = Session(
        token=token, username=req.username, teacher_id=acc.teacher_id,
        created_at=time.time(),
    )
    return {
        "token": token,
        "teacher": req.username,
        "teacher_id": acc.teacher_id,
    }


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


def _sign_school_token(teacher_id: int) -> str:
    """签发教师身份 token（与 sc 后端 auth.py 同款：HMAC-SHA256 hex，`teacher_id.exp.sig`）。

    school-authz shim 在壳侧以同一 SC_AUTH_SECRET 校验、从 token 派生 SC_MCP_TEACHER_ID。
    gateway 每次 spawn 现签现用，TTL 仅约束被盗 token 的可用窗口。
    """
    exp = int(time.time()) + _SCHOOL_TOKEN_TTL_S
    body = f"{teacher_id}.{exp}"
    sig = hmac.new(SCHOOL_AUTH_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _teacher_identity_env(teacher_id: int) -> dict[str, str]:
    """G11 身份传播（§5.5）载体：注入 app-server 子进程 env 的教师身份。

    SC_AUTH_SECRET 配置 → 签名 token（school-authz shim 壳侧校验、注入 SC_MCP_TEACHER_ID，
    替代「裸 env 可信」）；未配置 → 旧兜底裸 SC_MCP_TEACHER_ID（灰度安全）。
    """
    if SCHOOL_AUTH_SECRET:
        return {"SC_SCHOOL_AUTH_TOKEN": _sign_school_token(teacher_id)}
    return {"SC_MCP_TEACHER_ID": str(teacher_id)}


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
    async def spawn(cls, teacher_id: int = 0) -> "Bridge":
        env = {**os.environ, "CODEX_HOME": CODEX_HOME, "RUST_LOG": "error"}
        # G11 身份传播（§5.5）：见 _teacher_identity_env —— school-authz 签名 token
        # 优先，SC_AUTH_SECRET 未配置时维持旧兜底裸 env 注入（灰度安全）。
        if teacher_id:
            env.update(_teacher_identity_env(teacher_id))
        proc = subprocess.Popen(
            [APP_SERVER_CMD, *APP_SERVER_ARGS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )
        self = cls(proc=proc)
        self.teacher_id = teacher_id
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
            else:  # 通知 → 护栏观察 → 广播给所有 SSE 订阅者
                self._observe_budget(msg)
                event = json.dumps({"type": "event", **msg}, ensure_ascii=False)
                for q in list(self.subscribers):
                    q.put_nowait(event)

    def _observe_budget(self, msg: dict) -> None:
        """§5.7 token 闸：thread/tokenUsage/updated 累计，超预算自动收尾本轮。"""
        try:
            if msg.get("method") != "thread/tokenUsage/updated":
                return
            params = msg.get("params") or {}
            tid = params.get("threadId") or (params.get("tokenUsage") or {}).get("threadId")
            usage = params.get("tokenUsage") or {}
            total = ((usage.get("total") or {}).get("totalTokens")) or 0
            if not tid or not total:
                return
            verdict = GUARD.observe_token_usage(str(tid), int(total))
            if verdict and verdict.get("action") == "interrupt":
                # turn_id 从通知取（同载荷），尽力中断——失败只记日志
                turn_id = params.get("turnId") or usage.get("turnId")
                if turn_id:
                    asyncio.get_running_loop().create_task(
                        self._safe_interrupt(str(tid), str(turn_id), verdict["reason"])
                    )
        except Exception:  # noqa: BLE001 —— 护栏观察绝不能打断事件流
            pass

    async def _safe_interrupt(self, thread_id: str, turn_id: str, reason: str) -> None:
        try:
            await self.request("turn/interrupt", {
                "threadId": thread_id, "turnId": turn_id,
            }, timeout=10)
        except Exception as e:  # noqa: BLE001
            print(f"[budget] interrupt failed: {e}; reason={reason}")

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


async def get_bridge(username: str, teacher_id: int = 0) -> Bridge:
    br = _BRIDGES.get(username)
    if br is None or br.proc.poll() is not None:
        if br is not None:
            br.stop()
        br = await Bridge.spawn(teacher_id=teacher_id)
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
    """浏览器侧 RPC：initialize/thread/start/turn/start/interrupt 等透传 app-server。

    §5.7 轮数/token 双闸挂在 turn/start 前——超限返回 402 与教师可读的
    收尾说明（优雅降级：已完成调查照常呈现，不是报错）。
    """
    bridge = await get_bridge(sess.username, sess.teacher_id)
    if req.method == "turn/start":
        thread_id = str((req.params or {}).get("threadId") or "")
        if thread_id:
            try:
                GUARD.check_turn_start(thread_id)
            except BudgetExceeded as e:
                return JSONResponse(
                    {"id": req.id, "error": {"message": e.reason, "usage": e.usage}},
                    status_code=402,
                )
    try:
        result = await bridge.request(req.method, req.params)
    except Exception as e:  # noqa: BLE001 —— 错误语义原样回传
        raise HTTPException(502, str(e)) from e
    return {"id": req.id, "result": result}


@app.get("/threads/{thread_id}/events")
async def thread_events(thread_id: str, sess: Session = Depends(require_auth)):
    """SSE：该教师 app-server 的全部通知流（浏览器按 threadId 自行过滤）。"""
    bridge = await get_bridge(sess.username, sess.teacher_id)
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


BACKEND_URL = os.environ.get("SC_BACKEND_URL", "")


def backend_ready(timeout: float = 3.0) -> bool:
    """sc /ready 探针（心跳用）；不可达=False，绝不抛。"""
    if not BACKEND_URL:
        return False
    try:
        import httpx

        r = httpx.get(f"{BACKEND_URL.rstrip('/')}/ready", timeout=timeout)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def health_snapshot() -> dict:
    """健康快照（/health 端点与每日心跳共用的单一来源）。"""
    return {
        "status": "ok",
        "bridges": {u: b.proc.poll() is None for u, b in _BRIDGES.items()},
        "budget_tasks": GUARD.snapshot(),
    }


@app.get("/health")
def health():
    snap = health_snapshot()
    snap["backend_ready"] = backend_ready()
    return snap


# ---------------------------------------------------------------------------
# 触发器 v1 内部接口（§5.4）：sc backend → 本网关 → 持久线程发起任务
# ---------------------------------------------------------------------------


class TriggerReq(BaseModel):
    kind: str                      # 目前仅 post_exam_analysis
    exam_id: int
    class_id: int
    idempotency_key: str           # 同键 10 分钟内不重复发起（网关侧重试安全）
    message: str                   # 版本化模板渲染好的用户消息
    template_version: str


def _require_internal_key(header_key: str = Header(default="", alias="X-Internal-Key")):
    """内部接口鉴权：未配置密钥=功能关闭（403）；配置后必须精确匹配。"""
    if not INTERNAL_KEY or header_key != INTERNAL_KEY:
        raise HTTPException(403, "internal key invalid")


def _load_thread_map() -> dict[str, str]:
    try:
        return json.loads(THREADS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_thread_map(mapping: dict[str, str]) -> None:
    THREADS_FILE.parent.mkdir(parents=True, exist_ok=True)
    THREADS_FILE.write_text(json.dumps(mapping, ensure_ascii=False, indent=1))


_RECENT_TRIGGERS: dict[str, float] = {}  # idempotency_key -> monotonic time
_TRIGGER_TTL_S = 600.0


def _recently_fired(key: str) -> bool:
    now = time.monotonic()
    for k in [k for k, t in _RECENT_TRIGGERS.items() if now - t > _TRIGGER_TTL_S]:
        _RECENT_TRIGGERS.pop(k, None)
    if key in _RECENT_TRIGGERS:
        return True
    _RECENT_TRIGGERS[key] = now
    return False


# 触发式任务用的系统级会话（不占教师账号；与教师桥同款 app-server 进程）
_SYSTEM_BRIDGE_USER = "__trigger__"


async def _trigger_bridge() -> Bridge:
    br = _BRIDGES.get(_SYSTEM_BRIDGE_USER)
    if br is None or br.proc.poll() is not None:
        if br is not None:
            br.stop()
        br = await Bridge.spawn()
        _BRIDGES[_SYSTEM_BRIDGE_USER] = br
    br.last_used = time.time()
    return br


class NotifyReq(BaseModel):
    kind: str                      # draft_ready | intervention_suggested
    class_name: str | None = None
    type_label: str | None = None
    preview: str | None = None
    alias: str | None = None
    kp_name: str | None = None
    kind_label: str | None = None
    link: str | None = None


@app.post("/internal/notify", dependencies=[Depends(_require_internal_key)])
async def internal_notify(req: NotifyReq):
    """业务触达出口（批次D）：sc 侧事件 → 网关 → 钉钉卡片。

    与 trigger 同一鉴权与 fire-and-forget 纪律；钉钉未配置时静默 no-op
    （返回 delivered:false 而非报错——调用方无需感知通道是否存在）。
    """
    from gateway import dingtalk

    if req.kind == "draft_ready":
        ok = dingtalk.notify_draft_ready(
            req.class_name or "班级", req.type_label or "报告",
            req.preview or "", workbench_url=req.link,
        )
    elif req.kind == "intervention_suggested":
        ok = dingtalk.notify_intervention_suggested(
            req.alias or "", req.kp_name or "", req.kind_label or "",
            workbench_url=req.link,
        )
    else:
        raise HTTPException(400, f"unknown notify kind {req.kind!r}")
    return {"delivered": bool(ok)}


@app.post("/internal/trigger", dependencies=[Depends(_require_internal_key)])
async def internal_trigger(req: TriggerReq):
    """在班级持久线程上发起触发式任务（§5.4）。

    线程映射缺省时 thread/start 建持久线程并落盘（一班一线程，§5.6）；
    turn/start 阻塞至该轮完成（与浏览器侧 RPC 同语义），超时 240s。
    幂等：同一 idempotency_key 在 TTL 内重复请求直接返回已受理。
    """
    if req.kind != "post_exam_analysis":
        raise HTTPException(400, f"unknown trigger kind {req.kind!r}")
    if _recently_fired(req.idempotency_key):
        return {"accepted": False, "reason": "duplicate within TTL"}

    bridge = await _trigger_bridge()
    mapping = _load_thread_map()
    tid = mapping.get(str(req.class_id))
    if not tid:
        started = await bridge.request("thread/start", {
            "cwd": "/tmp",
            "approvalPolicy": "never",
        })
        tid = ((started.get("result") or {}).get("thread") or {}).get("id")
        if not tid:
            raise HTTPException(502, "thread/start returned no thread id")
        mapping[str(req.class_id)] = tid
        _save_thread_map(mapping)
    try:
        GUARD.check_turn_start(str(tid))  # 触发式任务同样过 §5.7 双闸
        await bridge.request("turn/start", {
            "threadId": tid,
            "input": [{"type": "text", "text": req.message}],
        }, timeout=240.0)
    except BudgetExceeded as e:
        # 触发式任务超限：202 受理但标注未执行（sc 侧 fire-and-forget 不重试风暴）
        return {"accepted": False, "reason": e.reason, "usage": e.usage}
    except Exception as e:  # noqa: BLE001 —— 错误语义原样回传
        raise HTTPException(502, f"turn/start failed: {e}") from e
    return {"accepted": True, "thread_id": tid, "template_version": req.template_version}


@app.on_event("startup")
def _startup() -> None:
    load_accounts()


@app.on_event("shutdown")
def _shutdown() -> None:
    for br in _BRIDGES.values():
        br.stop()
