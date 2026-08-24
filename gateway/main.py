"""sc 会话网关雏形（Agent 产品化 §2 网关 / Phase 0-T4）。

职责边界（设计文档 §2）：对上是浏览器友好的 WebSocket + JSON 消息，对下是
codex app-server 子进程的 stdio JSON-RPC。本雏形只做**双向翻译与多路复用**，
鉴权（§5.5）、触发器（§5.4）、身份注入属 Phase 1+，接口位已预留。

协议约定（浏览器侧）：
- 客户端发 {"type":"rpc","method":"...","params":{...}} → 网关分配自增 id，
  转给 app-server，回 {"type":"rpc_result","id":N,"result":...} 或 rpc_error；
- app-server 的通知（thread/started、item/completed、turn/completed…）
  原样以 {"type":"event","method":"...","params":{...}} 下发；
- 客户端可发 {"type":"ping"} → {"type":"pong"}。

一个 WS 连接 = 一个 app-server 子进程 = 一条 stdio 连接（Phase 0 雏形语义；
Phase 1 换为连接池/每教师进程的正式形态——取决于验链②结论的运维化）。

用法：uvicorn gateway.main:app --port 8100
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI(title="sc session-gateway (Phase 0 skeleton)")

APP_SERVER_CMD = os.environ.get("SC_GATEWAY_APP_SERVER", "codex")
APP_SERVER_ARGS = os.environ.get("SC_GATEWAY_APP_SERVER_ARGS", "app-server").split()


class AppServerBridge:
    """一个 app-server 子进程的异步 stdio 桥。"""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._next_id = 0
        # JSON-RPC id -> 为之等待的 Future（一次性的）
        self._pending: dict[int, asyncio.Future] = {}
        self._client: WebSocket | None = None
        self._reader_task: asyncio.Task | None = None

    async def start(self, client: WebSocket) -> None:
        self._client = client
        env = {
            **os.environ,
            "CODEX_HOME": os.environ.get(
                "SC_GATEWAY_CODEX_HOME", "/tmp/sc-phase0/codex-home"
            ),
            "RUST_LOG": "error",
        }
        self.proc = subprocess.Popen(
            [APP_SERVER_CMD, *APP_SERVER_ARGS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        assert self.proc and self.proc.stdout
        while True:
            line = await loop.run_in_executor(None, self.proc.stdout.readline)
            if not line:
                break  # EOF：子进程退出
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: dict) -> None:
        if self._client is None:
            return
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError(f"rpc error: {msg['error']}"))
                else:
                    fut.set_result(msg.get("result"))
        else:
            await self._client.send_json({"type": "event", **msg})

    async def request(self, method: str, params: dict, timeout: float = 120.0):
        assert self.proc and self.proc.stdin
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        self.proc.stdin.write(payload + "\n")
        self.proc.stdin.flush()
        return await asyncio.wait_for(fut, timeout)

    def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """浏览器 ⇄ app-server 的双向通道（Phase 0：无鉴权，仅校内验证用）。"""
    await ws.accept()
    bridge = AppServerBridge()
    await bridge.start(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "invalid json"})
                continue
            kind = msg.get("type")
            if kind == "ping":
                await ws.send_json({"type": "pong"})
            elif kind == "rpc":
                try:
                    result = await bridge.request(msg["method"], msg.get("params") or {})
                    await ws.send_json({"type": "rpc_result", "id": msg.get("id"), "result": result})
                except Exception as e:  # noqa: BLE001 —— 错误原样回传客户端
                    await ws.send_json({"type": "rpc_error", "id": msg.get("id"), "message": str(e)})
            else:
                await ws.send_json({"type": "error", "message": f"unknown type: {kind}"})
    except WebSocketDisconnect:
        pass
    finally:
        bridge.stop()
