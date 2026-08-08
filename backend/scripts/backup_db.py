"""数据库备份（G10）：SQLite 在线热备（backup API，WAL 下不阻塞读写）。

迁 PG 后改用 pg_dump，此脚本仅 SQLite 期。

用法：
    cd backend && python -m scripts.backup_db [dest]
    或 PYTHONPATH=backend python scripts/backup_db.py [dest]
缺省 dest = <源库名>.YYYYMMDD-HHMMSS.bak（与源库同目录）。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402


def backup_db(src_url: str | None = None, dest: str | None = None) -> str:
    """SQLite 在线热备（backup API），返回目标路径。

    src_url 缺省取 settings.database_url。dest 缺省按时间戳生成。
    """
    url = src_url or settings.database_url
    if not url.startswith("sqlite:///"):
        raise ValueError(
            f"backup_db 仅支持 SQLite，当前 url={url!r}（迁 PG 后用 pg_dump）"
        )
    src = url.replace("sqlite:///", "", 1)
    if src in ("", ":memory:"):
        raise ValueError("不支持内存库备份")
    if not dest:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = str(Path(src).with_name(f"{Path(src).name}.{ts}.bak"))

    src_con = sqlite3.connect(src)
    dest_con = sqlite3.connect(dest)
    try:
        src_con.backup(dest_con)  # 在线热备：WAL 下不阻塞读写
    finally:
        dest_con.close()
        src_con.close()
    return dest


def main() -> None:
    dest = backup_db(dest=sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"[backup] 已备份至 {dest}")


if __name__ == "__main__":
    main()
