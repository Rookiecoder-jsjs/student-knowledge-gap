"""网关 v1 端到端验证（Phase 1 出口判据的网关部分）。

链路：登录拿 token → POST /rpc thread/start + turn/start →
GET /threads/x/events SSE 流收 agentMessage → 断言回答含 sc 真实班级数字。

前置：
- backend/.env 有 DEEPSEEK_API_KEY；CODEX_HOME 指向含 DeepSeek 配置与 sc MCP 的隔离目录；
- gateway/accounts.json 已由 scripts/gateway_account.py 生成。

用法：
    python scripts/verify_gateway_v1.py [--base http://127.0.0.1:8100]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))


def _http(method: str, url: str, body: dict | None = None, token: str | None = None, timeout=60):
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--username", default=os.environ.get("GW_USER", "teacher_demo"))
    ap.add_argument("--password", default=os.environ.get("GW_PASS", ""))
    args = ap.parse_args()
    base = args.base.rstrip("/")

    # 1. 未鉴权必须 401
    status, _ = _http("POST", f"{base}/rpc", {"method": "thread/start", "params": {}})
    assert status == 401, f"无 token 应 401，实际 {status}"
    print("[1] 未鉴权请求被拒 (401) ✅")

    # 2. 错误口令 401
    status, _ = _http("POST", f"{base}/auth/login", {"username": args.username, "password": "wrong"})
    assert status == 401, f"错误口令应 401，实际 {status}"
    print("[2] 错误口令拒绝 ✅")

    # 3. 登录
    status, login = _http(
        "POST", f"{base}/auth/login",
        {"username": args.username, "password": args.password},
    )
    assert status == 200 and login.get("token"), f"登录失败: {status} {login}"
    token = login["token"]
    print(f"[3] 登录成功，teacher={login['teacher']} ✅")

    # 4. 先开 SSE 订阅（后台线程收流），再发起 turn
    events: list[dict] = []
    stop = threading.Event()

    def consume():
        req = urllib.request.Request(f"{base}/threads/x/events")
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in resp:
                line = raw.decode().strip()
                if not line.startswith("data: "):
                    continue
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
                if any(e.get("method") == "turn/completed" for e in events):
                    break
                if stop.is_set():
                    break

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    time.sleep(1.5)  # 等 SSE 建立连接

    # 5. RPC：thread/start → turn/start（问题指向真实班级数据）
    status, started = _http(
        "POST", f"{base}/rpc",
        {"id": 1, "method": "thread/start", "params": {"cwd": "/tmp"}},
        token=token,
    )
    assert status == 200, f"thread/start 失败: {status} {started}"
    thread_id = ((started.get("result") or {}).get("thread") or {}).get("id")
    print(f"[4] thread/start ✅ thread_id={thread_id}")

    status, turned = _http(
        "POST", f"{base}/rpc",
        {
            "id": 2,
            "method": "turn/start",
            "params": {
                "threadId": thread_id,
                "input": [{"type": "text", "text": "查询班级概览，一句话说有几个班、共多少学生。"}],
            },
        },
        token=token,
        timeout=240,
    )
    assert status == 200, f"turn/start 失败: {status} {turned}"
    print("[5] turn/start 已提交（阻塞至 Turn 完成）✅")

    # 6. SSE 流里应有该线程的事件；turn/completed 可能晚于 RPC 响应到达，多等一会
    time.sleep(8)
    stop.set()
    t.join(timeout=10)
    methods = [e.get("method") for e in events]
    assert "turn/completed" in methods, f"SSE 未收到 turn/completed，收到: {set(methods)}"
    texts = []
    for e in events:
        if e.get("method") == "item/completed":
            item = (e.get("params") or {}).get("item", {})
            if item.get("item_type") == "agentMessage" or item.get("type") == "agentMessage":
                texts.append(item.get("text") or "")
    answer = "\n".join(texts)
    print(f"[6] SSE 收到完整事件流 ✅ 共 {len(events)} 条")
    print(f"    模型回答: {answer[:200]}")

    # 7. 回答含真实班级数据（sc 库当前 1 个班）
    assert ("1 个班" in answer or "一个班" in answer or "七(1)" in answer), \
        f"回答未引用真实班级数据: {answer[:300]}"
    print("[7] 回答引用 sc 真实数据 ✅")

    print("\n网关 v1 全部通过：鉴权 / RPC / SSE / 真实数据贯通")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
