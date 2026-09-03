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

# 装车批第 3 步后规范壳 = runtime 源码构建的 codex-app-server(gateway 镜像内
# 直启,无子命令,args 空;旧 npm `codex app-server` 形态仅由外部 env 显式还原)
APP_SERVER_CMD = os.environ.get("SC_GATEWAY_APP_SERVER", "codex-app-server")
APP_SERVER_ARGS = os.environ.get("SC_GATEWAY_APP_SERVER_ARGS", "").split()
# 装车批第 6 批：CODEX_HOME 是**根**（卷挂载点）；每个驱动（教师身份）用其下
# t<teacher_id>/ 作自己的 codex home（config/models/rollout 互不混局）。浅层收窄：
# 同容器同 root，目录级不构成注入 agent 越级读同级目录的内核边界（见 _driver_home）。
CODEX_HOME = os.environ.get("SC_GATEWAY_CODEX_HOME", "/tmp/sc-p1/codex-home")
# §5.4 触发器 v1：sc backend → gateway 内网共享密钥（双方一致才放行 /internal/*）
INTERNAL_KEY = os.environ.get("SC_TRIGGER_KEY", "")
# §6.3 school-authz：与 sc 后端共享的身份签名密钥（SC_AUTH_SECRET 配置后，教师身份
# 由 gateway 签为 HMAC token、由壳侧 shim 校验注入，替代「裸 SC_MCP_TEACHER_ID env
# 可信」的兜底路线；未配置=维持旧兜底，灰度安全）
SCHOOL_AUTH_SECRET = os.environ.get("SC_AUTH_SECRET", "")
# 装车批第 5 批：sc MCP 迁 backend 后为**逐请求**重验——token 须盖过常驻 bridge
# 生命周期。对齐 backend auth.py 的一周 TTL；gateway 重部署即重签，TTL 只约束
# dumped token 的重放窗口。
_SCHOOL_TOKEN_TTL_S = 7 * 24 * 3600
# 班级持久线程映射落盘（§5.6 一班一线程跨学期滚动）。装车批第 6 批：
# 键 = "{class_id}.{teacher_id}"（见 _thread_key）——按教师分 home 后两教师不共享
# 一类线程；文件默认落在 CODEX_HOME 卷内（根下 threads.json），容器重建不丢——
# 旧默认 /data/threads.json 在容器临时层，重建即丢，与跨学期滚动矛盾。可覆盖。
THREADS_FILE = Path(os.environ.get(
    "SC_GATEWAY_THREADS_FILE", str(Path(CODEX_HOME) / "threads.json")))


# 装车批第 6 批：驱动 home 与按驱动惰性播种（Bridge.spawn 前调用）。
def _driver_home(teacher_id: int) -> Path:
    """该驱动（教师身份）的 codex home：CODEX_HOME 根下 t<teacher_id>/。

    teacher_id=0 = 匿名/开放模式驱动。目录级分离（浅层）：诚实进程各自只指向
    自己的 home、默认路径不再混局；同容器同 root，注入 agent `ls ..` 越级读同级
    目录仍不被内核阻止（DEPLOY.md §8 残留如实收窄，真关闭需 per-teacher UID）。
    """
    return Path(CODEX_HOME) / f"t{teacher_id or 0}"


def _assets_dir() -> Path:
    return Path(os.environ.get(
        "SC_GATEWAY_ASSETS",
        str(Path(__file__).resolve().parent / "assets" / "deepseek"),
    ))


def _seed_driver_home(teacher_id: int) -> Path:
    """确保该驱动 home 已播种（config.toml/models.json）；幂等，失败不阻断 spawn。"""
    home = _driver_home(teacher_id)
    try:
        from gateway import codex_home as _ch

        _ch.seed_codex_home(home, _assets_dir(), env=os.environ)
    except Exception as e:  # noqa: BLE001 —— 播种失败不阻断网关（可手工补 config.toml）
        print(f"[codex-home] seed t{teacher_id} failed: {e}")
    return home

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
    """教师身份载体（§5.5，装车批第 5 批）：app-server 子进程 env 里的教师 token。

    SC_AUTH_SECRET 配置 → 网关签发的教师 token（SC_SCHOOL_AUTH_TOKEN）；codex 的
    MCP streamable-http 客户端按 config.toml [mcp_servers.sc] 的 bearer_token_env_var
    以 `Authorization: Bearer` 逐请求发往 backend /mcp，backend 验签得 teacher_id——
    身份不再靠进程级 env 裸注入（旧 school-authz shim 模型退役）。未配置
    SC_AUTH_SECRET → {}：codex 不发头 → backend 开放模式匿名。
    """
    if SCHOOL_AUTH_SECRET:
        return {"SC_SCHOOL_AUTH_TOKEN": _sign_school_token(teacher_id)}
    return {}


def _child_env(teacher_id: int) -> dict[str, str]:
    """app-server 子进程 env 最小权限白名单（装车批第 5 批）+ 驱动 home（第 6 批）。

    旧实现 `{**os.environ, ...}` 全盘继承——agent（可执行任意 shell）一次 `env`
    即得 SC_AUTH_SECRET/SC_TRIGGER_KEY/SC_DATABASE_URL/SC_LLM_*，可伪造任意教师
    token。改为只放 codex 运行所需（HOME/PATH/代理/TZ…）+ 该教师的签名 token +
    该教师的驱动 CODEX_HOME（第 6 批起指向 t<teacher_id>/ 子目录，见 _driver_home）；
    一切 SC_* 共享密钥都不进子进程（gateway 进程自身仍持全集，签名与播种在进程侧完成）。
    """
    passthrough = {
        "HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "NO_PROXY", "no_proxy",
    }
    env = {k: os.environ[k] for k in passthrough if k in os.environ}
    env["CODEX_HOME"] = str(_driver_home(teacher_id))
    env["RUST_LOG"] = "error"
    if teacher_id:
        env.update(_teacher_identity_env(teacher_id))
    return env


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
        # 最小权限 env（装车批第 5 批）：白名单 + 该教师的签名 token（见 _child_env）——
        # 共享密钥/数据库 URL/LLM key 都不进 agent 进程。装车批第 6 批：先按驱动 home
        # 惰性播种（config.toml/models.json 落 t<teacher_id>/），codex 以该 home 启动。
        _seed_driver_home(teacher_id)
        env = _child_env(teacher_id)
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
    teacher_id: int | None = None  # 触发身份（装车批第 5 批）：提交教师实名；None=匿名
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


def _thread_key(class_id: int, teacher_id: int) -> str:
    """持久线程映射键（装车批第 6 批）："{class_id}.{teacher_id}"。

    按教师分 CODEX_HOME（t<tid>/）后两教师不再共享一类线程——同一班的不同教师
    各自持自己的持久线程，互不越界；班主教师与系统（以同一教师身份 trigger）
    双方以同一键寻址、落在同一驱动 home，可跨 bridge 重建 resume。teacher_id=0
    = 匿名/开放模式驱动。
    """
    return f"{class_id}.{teacher_id or 0}"


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


# 触发式任务用的 app-server 桥（不占教师账号；按身份分桥）。
# 装车批第 5 批：trigger 按「提交教师」身份驱动（backend commit 实名教师带
# teacher_id 入载荷）——安全模式下自动考后分析才能经 sc MCP 读本班数据（匿名在
# /mcp 是 fail-closed 401）。teacher_id=0 = 开放模式匿名兜底。桥与教师交互桥分开，
# 触发式长 turn 不打断浏览器会话。装车批第 6 批：持久线程键 = class_id.teacher_id
# （_thread_key），每个（班,教师）一个持久线程、落在该教师驱动 home（t<tid>/）——
# 同教师跨 bridge 重建可 resume（同 home 同键），不同教师互不越界。
_TRIGGER_BRIDGES: dict[int, Bridge] = {}


async def _trigger_bridge(teacher_id: int = 0) -> Bridge:
    br = _TRIGGER_BRIDGES.get(teacher_id)
    if br is None or br.proc.poll() is not None:
        if br is not None:
            br.stop()
        br = await Bridge.spawn(teacher_id=teacher_id)
        _TRIGGER_BRIDGES[teacher_id] = br
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

    bridge = await _trigger_bridge(req.teacher_id or 0)
    mapping = _load_thread_map()
    key = _thread_key(req.class_id, req.teacher_id or 0)
    tid = mapping.get(key)
    if not tid:
        started = await bridge.request("thread/start", {
            "cwd": "/tmp",
            "approvalPolicy": "never",
        })
        tid = ((started.get("result") or {}).get("thread") or {}).get("id")
        if not tid:
            raise HTTPException(502, "thread/start returned no thread id")
        mapping[key] = tid
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
    # 装车批第 6 批：CODEX_HOME 播种改**按驱动惰性**——Bridge.spawn 前
    # _seed_driver_home(teacher_id) 为 t<teacher_id>/ 播种 config.toml + models.json，
    # 不再启动时对根单次播种（根仅是卷挂载点，非任何驱动的 home）。


@app.on_event("shutdown")
def _shutdown() -> None:
    for br in _BRIDGES.values():
        br.stop()
    for br in _TRIGGER_BRIDGES.values():
        br.stop()
