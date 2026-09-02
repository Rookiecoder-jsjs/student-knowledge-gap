"""backend /mcp 挂载与逐请求鉴权（装车批第 5 批：sc MCP 迁入 backend 进程）。

拓扑：sc MCP server（backend/app/mcp_server.py 的 FastMCP 实例，工具已注册其上）
不再以 stdio 子进程跑在 gateway 容器（旧 school-authz-mcp shim 模型退役），而是
同一 uvicorn 进程内经 streamable-http 挂 /mcp。gateway 的 codex agent 以
`url = http://backend:8000/mcp` 远程连入，`Authorization: Bearer` 携带网关签发
的教师 token；本模块逐请求验签（app.auth.verify_token，同格式同密钥）后写
auth contextvar，工具层（app.auth.mcp_context）按教师/班级过滤。gateway 容器
不再挂 sc-data 卷——agent 壳物理不可达 sc.db，直接 DB 读写路径被拓扑切断。

嵌入要点（mcp==1.27.2 spike 实证，见 gateway/.runtime/spike_mcp.py）：
- ``FastMCP.streamable_http_app()`` 返回的 Starlette 内部路由是绝对路径 /mcp，
  不能 ``FastAPI.mount("/mcp", ...)``（Mount 会剥前缀致 404）——把 /mcp 路由并入
  ``app.router.routes`` 即可全路径命中；
- FastMCP 缓存的 ``session_manager.run()`` **每实例只能进一次**——backend 测试
  每轮 ``with TestClient(app)`` 都进出 lifespan，直接复用它第二轮必炸。改为每轮
  lifespan **重建**一个 ``StreamableHTTPSessionManager``（over ``mcp._mcp_server``
  低层 MCPServer），路由以惰性 dispatcher 委托「当前轮」manager；
- app 级 http middleware 设的 contextvar 能传播进 sync/async/``to_thread`` 工具
  （spike 三种形状均可见 42）——mcp_server.py 工具**无需** async 化；
- /mcp 仅 compose 内网可达（无浏览器 origin），DNS-rebinding 保护禁用。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from starlette.routing import Route

from app import auth as _auth
from app.mcp_server import mcp

from mcp.server.fastmcp.server import StreamableHTTPASGIApp  # noqa: E402
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings


class _MCPHttpDispatcher:
    """惰性委托到「当前轮」manager 的 ASGI endpoint（路由只 graft 一次）。

    Starlette Route 对函数型 endpoint 默认只放 GET；带 __call__ 的 ASGI 实例无
    方法限制——镜像 FastMCP 自身 Route 形态，POST/GET 全通。
    """

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        http = _cycle_http
        if http is None:
            # lifespan 未起（理论不可达：请求只在该轮 manager run() 期间到达）
            body = b'{"detail":"mcp not started"}'
            await send({
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        await http(scope, receive, send)


_cycle_manager: StreamableHTTPSessionManager | None = None
_cycle_http: StreamableHTTPASGIApp | None = None


def _start_cycle() -> None:
    """每轮 lifespan 重建 manager：规避 FastMCP session_manager.run() 的 once-only。"""
    global _cycle_manager, _cycle_http
    _cycle_manager = StreamableHTTPSessionManager(
        app=mcp._mcp_server,  # noqa: SLF001 —— 低层 MCPServer（FastMCP 工具已注册其上）
        event_store=None,
        json_response=False,
        stateless=False,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    _cycle_http = StreamableHTTPASGIApp(_cycle_manager)


@asynccontextmanager
async def mcp_lifespan():
    """在 backend 进程 lifespan 内常驻当前轮的 MCP session manager。"""
    global _cycle_manager, _cycle_http
    _start_cycle()
    assert _cycle_manager is not None
    try:
        async with _cycle_manager.run():
            yield
    finally:
        _cycle_manager = None
        _cycle_http = None


# /mcp 路由（绝对路径，全量方法），并入 FastAPI app.router（见 main.py）
mcp_route: Route = Route("/mcp", _MCPHttpDispatcher())


async def mcp_auth(request, call_next):  # noqa: ANN001
    """仅拦 /mcp 的逐请求鉴权：Authorization Bearer = 网关签发的教师 token。

    空头 → 开放模式匿名放行 / 安全模式 401（fail-closed）；带但无效 → 401
    （给凭据但凭据坏必须显式失败，不静默降级匿名）。验签后把 teacher_id 写
    auth contextvar——FastMCP 工具在任意线程/任务经 auth.mcp_context 读取。
    """
    path = request.url.path
    if path != "/mcp" and not path.startswith("/mcp/"):
        return await call_next(request)
    from fastapi.responses import JSONResponse

    from app.api.deps import SessionLocal as _SL

    raw = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    teacher_id: int | None = None
    if raw:
        try:
            teacher_id = _auth.verify_token(raw)
        except _auth.AuthError as e:
            return JSONResponse({"detail": f"mcp 鉴权失败：{e}"}, status_code=401)
    else:
        db = _SL()
        try:
            if _auth.security_mode_on(db):
                return JSONResponse(
                    {"detail": "需要 MCP 教师 token（安全模式）"}, status_code=401
                )
        finally:
            db.close()
    _auth.set_mcp_teacher_id(teacher_id)
    return await call_next(request)
