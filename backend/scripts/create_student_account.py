"""开通学生自服务账号（auth-roles-design §3；admin 路由之外的部署/bootstrap 通道）。

用法：
    python -m scripts.create_student_account --student 5 --password s3cret [--username stu005]

username 缺省 = 学生 external_code（学籍号）。重复执行 = 重置口令（幂等）。
"""

from __future__ import annotations

import argparse

from app import auth
from app.db import SessionLocal, init_db


def main() -> None:
    ap = argparse.ArgumentParser(description="开通学生账号")
    ap.add_argument("--student", type=int, required=True, help="student.id")
    ap.add_argument("--password", required=True)
    ap.add_argument("--username", default=None, help="缺省取 external_code")
    args = ap.parse_args()

    init_db()
    with SessionLocal() as db:
        try:
            stu, username = auth.enable_student_login(
                db, args.student, args.password, args.username
            )
        except ValueError as e:
            print(f"错误: {e}")
            raise SystemExit(1) from e
        db.commit()
        print(f"student_id={stu.id} username={username} "
              f"alias={stu.name_or_alias} class_id={stu.class_id}")


if __name__ == "__main__":
    main()
