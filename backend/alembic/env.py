"""Alembic 迁移环境（G10）：用 app.db.Base.metadata + settings.database_url。

- target_metadata = app 的 ORM 元数据，供 autogenerate 比对。
- URL 取自 app.config.settings.database_url（与运行时同源，迁 PG 只改 URL）。
- compare_type=True：列类型变更可被 autogenerate 检出。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# 让 env.py 在 `alembic` CLI 下能 import app（backend/ 已在 sys.path 时亦无妨）
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app import models  # noqa: F401,E402  确保模型注册到 metadata
from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402

config = context.config

# 用运行时 database_url 作 url 占位（若 cfg 已显式设 url，如测试指向临时库，则不覆盖）
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅据 URL 生成 SQL，不需 DBAPI。"""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：建 Engine 并关联连接。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
