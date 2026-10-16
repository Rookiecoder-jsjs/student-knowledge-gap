"""Exercise the deployment migration path rather than metadata.create_all."""

from pathlib import Path

from alembic import command
from alembic.config import Config
import sqlalchemy as sa

from app.db import Base
from app import models  # noqa: F401
from app.kb.loader import import_kb


def test_migrated_database_accepts_current_kb(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'deployment.db'}")
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(cfg, "head")
    with engine.begin() as connection:
        inspector = sa.inspect(connection)
        for name, table in Base.metadata.tables.items():
            assert inspector.has_table(name), name
            actual = {column["name"] for column in inspector.get_columns(name)}
            assert {column.name for column in table.columns} <= actual, name
    with sa.orm.Session(engine) as db:
        kb = import_kb(db, Path(__file__).resolve().parents[1] / "kb/math/grade7/kb.yaml")
        db.commit()
        point = db.scalar(sa.select(models.KnowledgePoint).where(models.KnowledgePoint.kb_version_id == kb.id))
        assert point.importance in {"基础", "核心", "拓展"}
    engine.dispose()


def test_legacy_upgrade_preserves_rows_and_is_idempotent(tmp_path, monkeypatch):
    from scripts import migrate_kp_importance

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE knowledge_point (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(sa.text("INSERT INTO knowledge_point VALUES (1, '历史知识点')"))
    monkeypatch.setattr(migrate_kp_importance, "engine", engine)
    migrate_kp_importance.add_importance_column()
    migrate_kp_importance.add_importance_column()
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT name, importance FROM knowledge_point")).one() == ("历史知识点", "核心")
    engine.dispose()
