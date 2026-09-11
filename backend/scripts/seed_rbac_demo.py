# -*- coding: utf-8 -*-
"""RBAC 演示账号一次性配置（rbac-scopes-design §11）：subjadmin1（数学×7 学科管理员）
+ 王老师（wangbj）升任 1 班班主任。走 HTTP API（生产库口径），幂等可重跑。
"""

import json
import urllib.request

BASE = "http://localhost:8080/api"


def call(method, path, token=None, body=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8")) or {}


def login(username, password):
    st, body = call("POST", "/auth/login", body={"username": username, "password": password})
    assert st == 200, (st, body)
    return body["token"]


def main():
    root = login("admin1", "123456")

    # 1) subjadmin1（幂等：已存在则复用）
    st, body = call(
        "POST", "/auth/teachers", root,
        {"name": "数学教研组长", "username": "subjadmin1", "password": "123456",
         "school_id": 1, "admin": False, "kb_editor": False},
    )
    if st == 200:
        print("created subjadmin1:", body["teacher_id"])
    else:
        print("create subjadmin1 ->", st, body)

    st, teachers = call("GET", "/auth/teachers", root)
    subj_id = next(t["teacher_id"] for t in teachers["teachers"] if t["username"] == "subjadmin1")
    wang_id = next(t["teacher_id"] for t in teachers["teachers"] if t["username"] == "wangbj")

    # 2) 学科管理员授权：数学×7（覆盖式）
    st, body = call("PUT", f"/auth/teachers/{subj_id}/subject-scopes", root,
                    {"scopes": [{"subject": "数学", "grade": 7}]})
    assert st == 200, (st, body)
    print("subjadmin1 scopes:", body["subject_scopes"])

    # 3) 王老师任 1 班班主任
    st, body = call("PUT", "/auth/classes/1/homeroom", root, {"teacher_id": wang_id})
    assert st == 200, (st, body)
    print("class1 homeroom ->", body["homeroom_teacher_id"])

    # 4) 双会话验证
    st = login("subjadmin1", "123456")
    _, me = call("GET", "/auth/me", st)
    print("subjadmin1 me: scopes", me["subject_scopes"], "homeroom", me["homeroom_class_ids"])
    _, vs = call("GET", "/kb/versions", st)
    print("subjadmin1 sees subjects:", sorted({v["subject"] for v in vs["versions"]}))

    wt = login("wangbj", "wang123456")
    _, me = call("GET", "/auth/me", wt)
    print("wangbj me: homeroom", me["homeroom_class_ids"], "classes", [c["name"] for c in me["classes"]])


if __name__ == "__main__":
    main()
