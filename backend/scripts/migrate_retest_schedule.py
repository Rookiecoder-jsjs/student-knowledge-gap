"""create_all 轨存量库补充复测日期；幂等，不为历史干预自动补日期。"""

import sqlalchemy as sa
from app.db import engine


def add_retest_due_date() -> None:
    if "retest_due_date" not in {c["name"] for c in sa.inspect(engine).get_columns("intervention")}:
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE intervention ADD COLUMN retest_due_date DATE"))
    if "source_warnings_json" not in {c["name"] for c in sa.inspect(engine).get_columns("exam_response")}:
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE exam_response ADD COLUMN source_warnings_json JSON"))


if __name__ == "__main__":
    add_retest_due_date()
