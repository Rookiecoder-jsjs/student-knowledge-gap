"""FastAPI 应用入口：uvicorn app.main:app"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import analysis, ingestion, kb, org, reports
from app.db import init_db
from app.observability import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    setup_logging()  # G7：结构化日志先于一切
    # 单一 schema 入口（问题8：存量库增量列 ALTER 已并入 init_db 的 create_all 分支）
    init_db()
    # 批量录入：回收崩溃遗留的 parsing 僵尸 item / running job（见 §7）
    from app.ingestion.batch import gc_orphan_tempfiles, reconcile_stale

    reconcile_stale()
    gc_orphan_tempfiles()  # G6：清扫孤儿 tempfile
    yield
    # ---- shutdown ----
    from app.ingestion.batch import shutdown as batch_shutdown

    batch_shutdown()


app = FastAPI(
    title="学生知识薄弱点分析归因系统",
    version="0.1.0",
    description="DESIGN.md v0.3 MVP：知识库 -> 采集 -> 追踪 -> 归因 -> 报告",
    lifespan=lifespan,
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

# 健康检查（与各域路由并列；候选2 拆分后独立于业务 router）
# liveness 探针：只问进程存活（静态 ok），依赖可用性见下方 /ready。
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """就绪探针：DB 可达 = 就绪（200）；LLM 熔断属「降级」，不构成不健康。

    设计：liveness（/health）只问进程死活；readiness（/ready）问依赖是否可用。
    LLM 断供时确定性路径（录入/推导/报告模板）仍工作，故降级返回 200 仅标
    ``degraded:true``——编排器据此决定是否摘流量，业务信号由日志与前端呈现。
    DB 不可达则 503（容器 healthcheck 据此触发重启自愈）。

    注意：**必须运行时动态读 ``app.db.engine``**（而非 import 期绑定）——测试
    夹具会 monkeypatch ``app.db.engine`` 做引擎隔离，import 期绑定会破坏该机制。
    """
    from fastapi.responses import JSONResponse
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

    vision, text_state = get_vision_breaker().state, get_text_breaker().state
    body = {
        "status": "ok" if db_ok else "error",
        "database": "ok" if db_ok else "error",
        "llm": {"vision": vision, "text": text_state},
        "degraded": (not db_ok) or vision != "closed" or text_state != "closed",
    }
    if not db_ok:
        body["detail"] = db_err
        return JSONResponse(status_code=503, content=body)
    return body


app.include_router(org.router)
app.include_router(kb.router)
app.include_router(ingestion.router)
app.include_router(analysis.router)
app.include_router(reports.router)