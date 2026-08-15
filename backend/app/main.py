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

# 教师端本地联调（生产部署应收紧为实际域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 健康检查（与各域路由并列；候选2 拆分后独立于业务 router）
@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(org.router)
app.include_router(kb.router)
app.include_router(ingestion.router)
app.include_router(analysis.router)
app.include_router(reports.router)