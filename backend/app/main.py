"""FastAPI 应用入口：uvicorn app.main:app"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.db import init_db
from app.observability import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    setup_logging()  # G7：结构化日志先于一切
    init_db()
    # 存量库 ALTER（create_all 不给已有表加列，缺则全站 500）
    from scripts.migrate_kb_archived import add_archived_column
    from scripts.migrate_parse_batch_started_at import add_started_at_column

    add_archived_column()
    add_started_at_column()  # G6：看门狗计时列
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

app.include_router(router)
