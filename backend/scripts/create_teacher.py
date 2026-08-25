"""创建教师账号（G11 bootstrap：首个 admin 由命令行建，避免鸡生蛋）。

用法：
    python -m scripts.create_teacher --name 李老师 --username li --password s3cret \
        --school 1 [--admin] [--grant 3,5]

--grant 授予班级 id（逗号分隔）。建号即进入安全模式（auth.security_mode_on
探测到带凭据教师即生效，进程重启后全端点要求登录）。
"""

from __future__ import annotations

import argparse
import secrets

from app import auth
from app.db import SessionLocal, init_db
from app.models import TeacherClass


def main() -> None:
    ap = argparse.ArgumentParser(description="创建教师账号")
    ap.add_argument("--name", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--school", type=int, default=1)
    ap.add_argument("--admin", action="store_true")
    ap.add_argument("--grant", default="", help="逗号分隔的班级 id 列表")
    args = ap.parse_args()

    init_db()
    with SessionLocal() as db:
        salt = secrets.token_bytes(16)
        t = auth.Teacher(
            school_id=args.school,
            name=args.name,
            username=args.username,
            salt=salt,
            password_hash=auth.hash_password(args.password, salt),
            admin=args.admin,
        )
        db.add(t)
        db.flush()
        granted = []
        for raw in args.grant.split(","):
            raw = raw.strip()
            if not raw:
                continue
            cid = int(raw)
            db.add(TeacherClass(teacher_id=t.id, class_id=cid))
            granted.append(cid)
        db.commit()
        print(f"teacher_id={t.id} username={args.username} admin={args.admin} "
              f"granted_classes={granted}")


if __name__ == "__main__":
    main()
