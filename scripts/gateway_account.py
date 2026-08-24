"""gateway 账号文件生成/追加工具（§5.5 一期：管理员建账号 + 口令）。

用法：
    python scripts/gateway_account.py add teacher_demo '口令' --teacher-id 1
    # 写入/合并 gateway/accounts.json（gitignore，不入库）

PBKDF2-SHA256 60k 轮；salt 随机 16 字节。与 gateway/main.py 的校验逻辑互为镜像。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path

_PBKDF2_ITERS = 60_000
_DEFAULT_PATH = "gateway/accounts.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["add"])
    ap.add_argument("username")
    ap.add_argument("password")
    ap.add_argument("--teacher-id", type=int, default=0)
    ap.add_argument("--path", default=_DEFAULT_PATH)
    args = ap.parse_args()

    salt = secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac("sha256", args.password.encode(), salt, _PBKDF2_ITERS)

    p = Path(args.path)
    doc = json.loads(p.read_text()) if p.exists() else {"accounts": []}
    doc["accounts"] = [
        a for a in doc.get("accounts", []) if a.get("username") != args.username
    ]
    doc["accounts"].append(
        {
            "username": args.username,
            "teacher_id": args.teacher_id,
            "salt": salt.hex(),
            "password_hash": password_hash.hex(),
        }
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"账号 {args.username} 已写入 {p}（共 {len(doc['accounts'])} 个）")


if __name__ == "__main__":
    main()
