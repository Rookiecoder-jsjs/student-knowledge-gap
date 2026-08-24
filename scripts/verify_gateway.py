#!/usr/bin/env python3
"""Phase 0-T4 网关雏形验证：浏览器侧 WS 协议走通一条完整 Turn。

前置：mock 模型服务器(6060) 与网关 uvicorn(8100) 均已启动。
判定：收到 turn/completed 且 agent 文本含 sc 真实数字。
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx  # venv 内已有（sc 后端依赖）

WS_URL = "ws://127.0.0.1:8100/ws"


async def main() -> int:
    try:
        import websockets  # type: ignore
    except ImportError:
        print("需要 websockets 包：pip install websockets")
        return 2

    async with websockets.connect(WS_URL) as ws:
        # 1) initialize
        await ws.send(json.dumps({"type": "rpc", "id": 1, "method": "initialize",
                                  "params": {"clientInfo": {"name": "gw-verify", "title": "gateway verifier", "version": "0.0.1"}}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "rpc_result" and msg.get("id") == 1:
                print("initialize ok")
                break
            if msg.get("type") == "rpc_error":
                print("initialize FAILED:", msg)
                return 1

        # 2) thread/start
        await ws.send(json.dumps({"type": "rpc", "id": 2, "method": "thread/start",
                                  "params": {"cwd": "/Users/haimianbaobao/Desktop/Item/sc", "approvalPolicy": "never"}}))
        thread_id = None
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "rpc_result" and msg.get("id") == 2:
                thread_id = (msg["result"].get("thread") or {}).get("id")
                print("thread started:", thread_id)
                break
            if msg.get("type") == "rpc_error":
                print("thread/start FAILED:", msg)
                return 1

        # 3) turn/start
        await ws.send(json.dumps({"type": "rpc", "id": 3, "method": "turn/start",
                                  "params": {"threadId": thread_id,
                                             "input": [{"type": "text", "text": "请告诉我三班概况。"}]}}))

        # 4) 收事件直到 turn/completed
        texts: list[str] = []
        deadline = asyncio.get_event_loop().time() + 150
        while asyncio.get_event_loop().time() < deadline:
            msg = json.loads(await ws.recv())
            if msg.get("type") != "event":
                continue
            method = str(msg.get("method", ""))
            params = msg.get("params") or {}
            if str(params.get("threadId") or "") != thread_id:
                continue
            if method.endswith("item/completed"):
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                    texts.append(item["text"])
            elif method.endswith("turn/completed"):
                answer = "\n".join(texts)
                print("turn completed. answer:", answer[:200])
                ok = ("30" in answer or "七" in answer) and "学生" in answer
                print(f"\nT4 验证结果：{'✅ 通过' if ok else '❌ 未通过'}")
                return 0 if ok else 1
        print("超时未等到 turn/completed")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
