"""FastAPI 应用入口：uvicorn app.main:app"""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
import time
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.api.routers import admin, analysis, auth as auth_router, ingestion, intervention, jobs, kb, me, org, reports
from app import mcp_http  # /mcp 挂载 + 逐请求教师鉴权（装车批第 5 批）
from app.db import init_db
from app.observability import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    setup_logging()  # G7：结构化日志先于一切
    # 单一 schema 入口（问题8：存量库增量列 ALTER 已并入 init_db 的 create_all 分支）
    init_db()
    # 安全/HA 模式不能依赖进程随机密钥，否则重启会让会话全部失效，
    # 多副本之间也无法互相验签。这里在服务开始接收请求前 fail-fast。
    from app import auth as auth_mod, db as dbmod

    with dbmod.SessionLocal() as auth_db:
        auth_mod.validate_runtime_secret(auth_db)
    # 批量录入：回收崩溃遗留的 parsing 僵尸 item / running job（见 §7）
    from app.ingestion.batch import gc_orphan_tempfiles, reconcile_stale

    reconcile_stale()
    gc_orphan_tempfiles()  # G6：清扫孤儿 tempfile
    # LLM 调用全程审计（rollout 思想）：单写线程异步落 llm_call_log
    if os.environ.get("SC_LLM_AUDIT", "").lower() not in ("0", "false", "no"):
        from app.llm.audit import start_audit_worker

        start_audit_worker(None)  # None -> 延迟取 SessionLocal（测试可注入工厂）
    job_stop = None
    job_task = None
    if os.environ.get("SC_JOB_QUEUE_ENABLE", "").lower() in ("1", "true", "yes"):
        import asyncio
        from app.jobs import worker_loop

        job_stop = asyncio.Event()
        job_task = asyncio.create_task(worker_loop(job_stop))
    # 装车批第 5 批：sc MCP 迁入本进程（streamable-http 挂 /mcp，见 app.mcp_http）。
    # 每轮 lifespan 重建 manager——规避 FastMCP session_manager.run() once-only
    # （backend 测试每轮 with TestClient(app) 都进出 lifespan）。
    from app import mcp_http

    try:
        async with mcp_http.mcp_lifespan():
            yield
    finally:
        # ---- shutdown ----
        from app.ingestion.batch import shutdown as batch_shutdown
        from app.llm.audit import stop_audit_workers

        if job_stop is not None:
            job_stop.set()
            if job_task is not None:
                await job_task
        batch_shutdown()
        stop_audit_workers()


app = FastAPI(
    title="知行教研 · 教学质量分析平台",
    version="0.1.0",
    description="DESIGN.md v0.3 MVP：知识库 -> 采集 -> 追踪 -> 归因 -> 报告",
    lifespan=lifespan,
)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or str(uuid4())


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    """Return a safe validation response without echoing submitted values."""
    from app.metrics import inc
    from app.observability import get_logger

    request_id = _request_id(request)
    request.state.http_error_recorded = True
    get_logger("http").info(
        "request validation failed",
        extra={
            "event": "http.request_rejected",
            "request_id": request_id,
            "method": request.method,
            "route": _route_template(request),
            "status_code": 422,
            "error_code": "validation_error",
            "field_count": len(exc.errors()),
        },
    )
    inc("http_errors_total", labels={"status_class": "4xx", "error_code": "validation_error"})
    return JSONResponse(
        status_code=422,
        content={
            "detail": "请求参数无效",
            "error_code": "validation_error",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Log the traceback internally and expose only a correlation-safe error."""
    from app.metrics import inc
    from app.observability import get_logger

    request_id = _request_id(request)
    request.state.http_error_recorded = True
    get_logger("http").exception(
        "unhandled request exception",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={
            "event": "http.request_failed",
            "request_id": request_id,
            "method": request.method,
            "route": _route_template(request),
            "status_code": 500,
            "error_type": type(exc).__name__,
            "error_code": "internal_error",
            "retryable": False,
        },
    )
    inc("http_errors_total", labels={"status_class": "5xx", "error_code": "internal_error"})
    return JSONResponse(
        status_code=500,
        content={
            "detail": "服务内部错误",
            "error_code": "internal_error",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )

# CORS：教师端本地联调 + 部署环境化（SC_CORS_ORIGINS 逗号分隔）。
# 生产同源经 nginx 反代（/api 前缀剥离）不需要跨域，未配置时回落本地开发默认，
# 保持 vite dev 代理工作流不变。
from app.config import settings  # noqa: E402

_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# G11 全局鉴权闸（agent-product-design §5.5 + auth-roles-design 三角色）：安全
# 模式下全部业务端点要求 Bearer token；白名单 = 探针（/health /ready）+ 登录本身
# + /mcp（MCP 走自己的逐请求教师 token 校验，见 app.mcp_http.mcp_auth）。裁决逻辑
# 在 app.auth，这里只做「要不要拦/角色放行」的路径判定——开放模式（无凭据账号）
# 整层透明。班级级授权不在此层（各端点经 guard_class/断言函数做归属校验）。
_EXEMPT_PREFIXES = ("/health", "/ready", "/metrics", "/auth/login", "/mcp")
# 学生主体可触达的前缀（自服务只读面 + 会话探测）；其余一切路径教师/admin 专属。
# 教师端点假定 kind=t，学生 token 在此层即被 403，端点无需各自防御。
_STUDENT_ALLOWED_PREFIXES = ("/me", "/auth/me")
_MAX_LOGIN_BODY_BYTES = 64 * 1024


@app.middleware("http")
async def _request_size_guard(request, call_next):  # noqa: ANN001
    """Reject oversized login payloads before JSON parsing and password work."""
    if request.url.path == "/auth/login":
        raw_length = request.headers.get("content-length")
        try:
            content_length = int(raw_length) if raw_length is not None else None
        except (TypeError, ValueError):
            content_length = None
        if content_length is not None and content_length > _MAX_LOGIN_BODY_BYTES:
            from fastapi.responses import JSONResponse

            request_id = _request_id(request)
            request.state.request_id = request_id
            return JSONResponse(
                {
                    "detail": "登录请求体过大",
                    "error_code": "request_body_too_large",
                    "request_id": request_id,
                },
                status_code=413,
                headers={"X-Request-ID": request_id},
            )
    return await call_next(request)


@app.middleware("http")
async def _auth_gate(request, call_next):  # noqa: ANN001
    path = request.url.path
    if not any(path == p or path.startswith(p + "/") for p in _EXEMPT_PREFIXES):
        from fastapi.responses import JSONResponse

        from app import auth as _auth
        from app.api.deps import SessionLocal as _SL

        raw = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        db = _SL()
        try:
            # token 先验：有效则按角色放行；无效显式 401（给凭据但凭据坏不降级匿名）。
            if raw:
                try:
                    kind, uid = _auth.verify_principal_token(raw)
                except _auth.AuthError as e:
                    return JSONResponse({"detail": str(e)}, status_code=401)
                if _auth.resolve_principal(db, kind, uid) is None:
                    return JSONResponse(
                        {
                            "detail": f"token 对应的"
                            f"{'教师' if kind == 't' else '学生'}不存在"
                        },
                        status_code=401,
                    )
                if kind == "s":
                    # 学生主体只放行自服务白名单（独立于安全模式，防御性收口）
                    if not any(
                        path == p or path.startswith(p + "/")
                        for p in _STUDENT_ALLOWED_PREFIXES
                    ):
                        return JSONResponse(
                            {"detail": "学生账号无该操作权限"}, status_code=403
                        )
                else:
                    request.state.teacher_id = uid
            if _auth.security_mode_on(db) and not raw:
                return JSONResponse({"detail": "需要登录"}, status_code=401)
        finally:
            db.close()
    return await call_next(request)


@app.middleware("http")
async def _request_metrics(request, call_next):  # noqa: ANN001
    from app.metrics import inc
    from app.observability import get_logger

    raw_request_id = (request.headers.get("X-Request-ID") or "").strip()
    try:
        request_id = str(UUID(raw_request_id))
    except (ValueError, TypeError, AttributeError):
        request_id = str(uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    logger = get_logger("http")
    response = None
    status_code = 500
    try:
        response = await call_next(request)
    except Exception:
        raise
    else:
        status_code = response.status_code
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        route = _route_template(request)
        labels = {
            "method": request.method,
            "route": route,
            "status_class": f"{status_code // 100}xx",
        }
        inc("http_requests_total", labels=labels)
        if status_code >= 500 and not getattr(request.state, "http_error_recorded", False):
            inc(
                "http_errors_total",
                labels={"status_class": "5xx", "error_code": "internal_error"},
            )
        inc("http_request_duration_ms_sum", duration_ms, labels=labels)
        inc("http_request_duration_ms_count", labels=labels)
        logger.info(
            "request complete",
            extra={
                "event": "http.request_completed",
                "request_id": request_id,
                "method": request.method,
                "route": route,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 3),
            },
        )
        if response is not None:
            response.headers["X-Request-ID"] = request_id
    return response


# /mcp 逐请求教师鉴权（外层；/mcp 已在 _EXEMPT_PREFIXES，故 _auth_gate 让路）
app.middleware("http")(mcp_http.mcp_auth)

# 健康检查（与各域路由并列；候选2 拆分后独立于业务 router）
# liveness 探针：只问进程存活（静态 ok），依赖可用性见下方 /ready。
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    """Low-dependency Prometheus-compatible runtime counters."""
    from app.metrics import render_prometheus

    return Response(content=render_prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/ready")
def ready():
    """就绪探针：DB 可达且（HA 模式下）Redis 可达 = 就绪（200）；LLM 熔断属「降级」，不构成不健康。

    设计：liveness（/health）只问进程死活；readiness（/ready）问依赖是否可用。
    LLM 断供时确定性路径（录入/推导/报告模板）仍工作，故降级返回 200 仅标
    ``degraded:true``——编排器据此决定是否摘流量，业务信号由日志与前端呈现。
    DB 不可达则 503（容器 healthcheck 据此触发重启自愈）。

    注意：**必须运行时动态读 ``app.db.engine``**（而非 import 期绑定）——测试
    夹具会 monkeypatch ``app.db.engine`` 做引擎隔离，import 期绑定会破坏该机制。
    """
    from sqlalchemy import text

    from app import db as dbmod  # 动态读取：测试夹具替换 app.db.engine 后仍生效
    from app.llm.circuit import get_vision_breaker
    from app.llm.gateway import get_text_breaker

    try:
        with dbmod.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
        db_err = ""
    except Exception as exc:  # noqa: BLE001
        db_ok, db_err = False, str(exc)

    from app.ha import check_redis, enabled as ha_enabled

    if ha_enabled() and db_ok and dbmod.engine.url.drivername.startswith("sqlite"):
        db_ok = False
        db_err = "SC_HA_ENABLED=1 requires PostgreSQL; SQLite is single-writer only"

    redis_ok, redis_err = check_redis()
    from app.metrics import set_gauge

    set_gauge("dependency_ready", 1 if db_ok else 0, labels={"dependency": "database"})
    set_gauge("dependency_ready", 1 if redis_ok else 0, labels={"dependency": "redis"})

    vision, text_state = get_vision_breaker().state, get_text_breaker().state
    failed_checks: list[str] = []
    if not db_ok:
        failed_checks.append("database_unavailable")
    if not redis_ok:
        failed_checks.append("redis_unavailable")
    body = {
        "status": "ok" if db_ok else "error",
        "database": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
        "llm": {"vision": vision, "text": text_state},
        "degraded": (not db_ok) or (not redis_ok) or vision != "closed" or text_state != "closed",
    }
    if not db_ok or not redis_ok:
        from app.observability import get_logger

        get_logger("health").warning(
            "readiness dependency failed",
            extra={
                "event": "dependency.health_changed",
                "dependency": ",".join(failed_checks),
                "error_code": "dependencies_unavailable",
                "database_error": db_err[:500] if db_err else "",
                "redis_error": redis_err[:500] if redis_err else "",
            },
        )
        body["error_code"] = (
            failed_checks[0] if len(failed_checks) == 1 else "dependencies_unavailable"
        )
        body["failed_checks"] = failed_checks
        body["detail"] = "依赖不可用"
        return JSONResponse(status_code=503, content=body)
    return body


app.include_router(auth_router.router)
app.include_router(me.router)
app.include_router(org.router)
app.include_router(kb.router)
app.include_router(ingestion.router)
app.include_router(analysis.router)
app.include_router(intervention.router)
app.include_router(reports.router)
app.include_router(admin.router)
app.include_router(jobs.router)

# sc MCP streamable-http 端点（绝对路径 /mcp；并入 router——Mount 会剥前缀致 404）
app.router.routes.append(mcp_http.mcp_route)
