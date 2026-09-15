"""init_db 双轨语义回归：服务进程 fail-fast；脚本入口允许未基线化库回落自举。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from app import db as dbmod
from app import models  # noqa: F401  确保模型注册
from app.db import Base


@pytest.fixture()
def isolated_engine(monkeypatch):
    """内存库替换 app.db.engine（init_db 内部经模块全局引用，替换即生效）。"""
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(dbmod, "engine", engine)
    yield engine
    engine.dispose()


def _boom_upgrade() -> None:
    raise RuntimeError("migration boom")


def test_alembic_failure_is_fail_fast_by_default(monkeypatch, isolated_engine):
    """服务启动路径：alembic 失败直接抛出，绝不静默回落 create_all。"""
    monkeypatch.setenv("SC_USE_ALEMBIC", "1")
    monkeypatch.setattr(dbmod, "_alembic_upgrade_head", _boom_upgrade)

    with pytest.raises(RuntimeError, match="migration boom"):
        dbmod.init_db()
    assert inspect(isolated_engine).get_table_names() == []


def test_script_fallback_bootstraps_unbaselined_db(monkeypatch, isolated_engine):
    """脚本入口（allow_create_all_fallback=True）对未基线化存量库回落 create_all。"""
    monkeypatch.setenv("SC_USE_ALEMBIC", "1")
    monkeypatch.setattr(dbmod, "_alembic_upgrade_head", _boom_upgrade)

    dbmod.init_db(allow_create_all_fallback=True)

    tables = set(inspect(isolated_engine).get_table_names())
    assert "teacher" in tables  # create_all 自举成功
    assert "alembic_version" not in tables  # 未纳入 alembic 管理，属预期回落


def test_script_fallback_still_fails_when_baselined(monkeypatch, isolated_engine):
    """已基线化库的迁移失败是真故障：任何入口都不允许静默降级掩盖 schema 缺口。"""
    monkeypatch.setenv("SC_USE_ALEMBIC", "1")
    monkeypatch.setattr(dbmod, "_alembic_upgrade_head", _boom_upgrade)
    with isolated_engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )

    with pytest.raises(RuntimeError, match="migration boom"):
        dbmod.init_db(allow_create_all_fallback=True)


def test_default_create_all_track_bootstraps(monkeypatch, isolated_engine):
    """默认轨（SC_USE_ALEMBIC 关）行为不变：create_all + 存量补列。"""
    monkeypatch.delenv("SC_USE_ALEMBIC", raising=False)

    dbmod.init_db()

    assert "teacher" in set(inspect(isolated_engine).get_table_names())
