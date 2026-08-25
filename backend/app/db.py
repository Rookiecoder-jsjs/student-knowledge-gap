"""数据库引擎与会话管理（SQLAlchemy 2.x）。

MVP 使用 SQLite；模型与代码保持 PostgreSQL 兼容（后续切换只改 URL）。
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import Pool

from app.config import settings


class Base(DeclarativeBase):
    """ORM 基类。"""


def utcnow() -> datetime:
    """naive-UTC 系统时间戳（列默认值 / 看门狗计时用）。

    ``datetime.utcnow()`` 自 Python 3.12 弃用；本助手保持原「naive UTC」语义不变。
    约定：系统戳（created_at/generated_at/started_at/reviewed_at）只写不参与证据
    计算，统一走本助手；领域截止时间（_as_dt 一系的 as_of）用本地时刻——
    两类基准不可混用（东八区下 utcnow 作证据截止会漏掉当天上午的证据）。
    """
    return datetime.now(UTC).replace(tzinfo=None)


_connect_args: dict = {"check_same_thread": False}  # SQLite + FastAPI 线程池
if settings.database_url.startswith("sqlite"):
    # 批量 worker 并发写：SQLite busy_timeout，写锁等待最多 15s（PG 兼容：仅 sqlite 加）
    _connect_args["timeout"] = 15

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Pool, "connect")
def _set_sqlite_pragma(dbapi_conn, _):  # noqa: ANN001
    """每个 SQLite 连接开 WAL（G2）。

    默认 journal_mode=delete 下，写者拿排他锁会阻塞所有读者；而本系统是
    「读极重（derive-on-read）+ 并发写（3 worker）」工作负载，两者互锁会触发
    `database is locked` 风暴。WAL 允许一写多读并发，迁 PG 前的稳态方案。

    监听挂在 Pool 类上：生产 engine 与测试 fixture 替换的 engine 均会触发；
    按 sqlite3 连接类型判定，非 SQLite 方言不受影响。:memory: 库设 WAL 为无操作
    （SQLite 返回 "memory"），不影响测试。
    """
    if not isinstance(dbapi_conn, sqlite3.Connection):
        return
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")  # 与 connect_args timeout 一致
        cur.execute("PRAGMA synchronous=NORMAL")  # WAL 下安全且更快（fsync 频次降低）
    finally:
        cur.close()


def init_db() -> None:
    """建表（schema 迁移的唯一入口；双轨决策见 alembic/README——问题8 统一后）。

    SC_USE_ALEMBIC=1 时走 ``alembic upgrade head``（G10：schema 变更可追踪、可回滚）；
    否则 ``create_all``（测试 fixture / 新库），并为存量库补增量列（``_legacy_alter_bootstrap``
    幂等 ALTER——create_all 不给已有表加列；alembic 轨不需要，初始迁移已含这些列）。
    迁移失败回落 create_all 保证启动。
    """
    from app import models  # noqa: F401  确保模型注册

    if os.environ.get("SC_USE_ALEMBIC", "").lower() in ("1", "true", "yes"):
        try:
            _alembic_upgrade_head()
            return
        except Exception:
            # alembic 不可用 / 未基线的既有库迁移失败：回落 create_all 保证 schema 可用
            Base.metadata.create_all(engine)
            return
    Base.metadata.create_all(engine)
    _legacy_alter_bootstrap()


def _legacy_alter_bootstrap() -> None:
    """存量库增量列（幂等；仅 create_all 轨执行——alembic 轨已由迁移链含列）。

    历史：这两个 ALTER 曾写在 main.py lifespan 每次启动无条件执行（双轨并存，
    改 schema 的人不知该走哪轨）。现收进 init_db 的 create_all 分支，与 alembic 轨
    单点隔离：走 alembic 的库不跑 ALTER；走 create_all 的库跑幂等补列。
    """
    from scripts.migrate_kb_archived import add_archived_column
    from scripts.migrate_drop_legacy_plans import drop_legacy_plan_tables
    from scripts.migrate_llm_tokens import add_token_columns
    from scripts.migrate_parse_batch_started_at import add_started_at_column
    from scripts.migrate_report_status import add_status_columns
    from scripts.migrate_teacher_auth import add_teacher_auth

    add_archived_column()
    add_started_at_column()
    add_status_columns()
    add_token_columns()
    drop_legacy_plan_tables()
    add_teacher_auth()


def _alembic_upgrade_head() -> None:
    """G10：程序化调用 alembic upgrade head（url 由 env.py 取 settings.database_url）。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).resolve().parent.parent / "alembic")
    )
    command.upgrade(cfg, "head")


@contextmanager
def get_session() -> Iterator[Session]:
    """事务性会话上下文管理器。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
