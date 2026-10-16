"""Keep existing create_all databases compatible with the KB importance field."""

import sqlalchemy as sa
from app.db import engine


def add_importance_column() -> None:
    columns = {c["name"] for c in sa.inspect(engine).get_columns("knowledge_point")}
    if "importance" not in columns:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "ALTER TABLE knowledge_point ADD COLUMN importance VARCHAR(10) "
                "NOT NULL DEFAULT '核心'"
            ))
