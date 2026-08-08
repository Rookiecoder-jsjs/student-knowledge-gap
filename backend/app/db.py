"""数据库引擎与会话管理（SQLAlchemy 2.x）。

MVP 使用 SQLite；模型与代码保持 PostgreSQL 兼容（后续切换只改 URL）。
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import Pool

from app.config import settings


class Base(DeclarativeBase):
    """ORM 基类。"""


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
    """建表。

    SC_USE_ALEMBIC=1 时走 ``alembic upgrade head``（G10：schema 变更可追踪、可回滚）；
    否则 ``create_all``（测试 fixture / 新库）。既有库切 alembic 前需先
    ``alembic stamp head`` 基线（见 alembic/README）。迁移失败回落 create_all 保证启动。
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
