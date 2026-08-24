#!/usr/bin/env python3
"""Phase 0 验链①/② 驱动器：JSON-RPC over stdio 与 codex app-server 对话。

用法：
    python scripts/verify_chain1.py            # 验链① 单线程问「三班概况」
    python scripts/verify_chain1.py --concurrent  # 验链② 双 thread 并发 Turn

前置：mock 模型服务器已在 127.0.0.1:6060 监听；隔离 CODEX_HOME 已配好 sc MCP。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

CODEX_HOME = "/tmp/sc-phase0/codex-home"
APP_SERVER = "codex"  # PATH 上的 0.149.1


class AppServerClient:
    """一个 app-server 子进程 = 一条 stdio JSON-RPC 连接。"""

    def __init__(self, tag: str):
        self.tag = tag
        self.proc = subprocess.Popen(
            [APP_SERVER, "app-server"],
            env={**os.environ, "CODEX_HOME": CODEX_HOME, "RUST_LOG": "error"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._next_id = 0
        self._pending: dict[int | str, dict] = {}
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._lock:
                if "id" in msg and ("result" in msg or "error" in msg):
                    self._pending[msg["id"]] = msg
                else:
                    self._events.append(msg)

    def request(self, method: str, params: dict, timeout: float = 60.0) -> dict:
        self._next_id += 1
        rid = self._next_id
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if rid in self._pending:
                    msg = self._pending.pop(rid)
                    if "error" in msg:
                        raise RuntimeError(f"{method} error: {msg['error']}")
                    return msg.get("result", {})
            time.sleep(0.02)
        raise TimeoutError(f"{method} timed out after {timeout}s")

    def wait_event(self, method_suffix: str, timeout: float = 90.0) -> dict | None:
        deadline = time.time() + timeout
        seen = 0
        while time.time() < deadline:
            with self._lock:
                evs = self._events[seen:]
                seen = len(self._events)
            for ev in evs:
                if str(ev.get("method", "")).endswith(method_suffix):
                    return ev
            time.sleep(0.05)
        return None

    def wait_thread_done(self, thread_id: str, timeout: float = 150.0) -> tuple[bool, str]:
        """等指定 thread 的 turn/completed；按 threadId 过滤，多线程安全。

        返回 (是否完成, 该 thread 的 agentMessage 拼接文本)。
        """
        deadline = time.time() + timeout
        texts: list[str] = []
        seen = 0
        while time.time() < deadline:
            with self._lock:
                evs = self._events[seen:]
                seen = len(self._events)
            done = False
            for ev in evs:
                params = ev.get("params") or {}
                if str(params.get("threadId") or params.get("thread", {}).get("id") or "") != thread_id:
                    continue
                m = str(ev.get("method", ""))
                if m.endswith("item/completed"):
                    item = params.get("item") or {}
                    if item.get("type") == "agentMessage":
                        for c in item.get("content") or []:
                            if isinstance(c.get("text"), str):
                                texts.append(c["text"])
                elif m.endswith("turn/completed"):
                    done = True
            if done:
                return True, "\n".join(texts)
            time.sleep(0.05)
        return False, "\n".join(texts)

    def drain_events(self) -> list[dict]:
        with self._lock:
            out = self._events[:]
            self._events.clear()
        return out

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def agent_text(ev: dict | None) -> str:
    """从 item/completed 或 turn/completed 里抽 assistant 文本。"""
    if not ev:
        return ""
    params = ev.get("params") or {}
    item = params.get("item") or (params.get("threadItem") or {})
    texts = []
    for c in item.get("content") or []:
        t = c.get("text")
        if isinstance(t, str):
            texts.append(t)
    if not texts:
        texts.append(json.dumps(params, ensure_ascii=False)[:400])
    return "\n".join(texts)


def run_turn(client: AppServerClient, prompt: str, model_hint: str = "", initialize: bool = True) -> tuple[str, float]:
    if initialize:
        init = client.request("initialize", {
            "clientInfo": {"name": "sc-phase0-verify", "title": "Phase 0 verifier", "version": "0.0.1"},
        })
        assert init, "initialize 返回空"

    start_params: dict = {
        "cwd": "/Users/haimianbaobao/Desktop/Item/sc",
        "approvalPolicy": "never",
    }
    if model_hint:
        start_params["model"] = model_hint
    started = client.request("thread/start", start_params)
    thread_id = (started.get("thread") or {}).get("id") or started.get("threadId")

    t0 = time.time()
    client.request("turn/start", {
        "threadId": thread_id,
        "input": [{"type": "text", "text": prompt}],
    })

    done, answer = client.wait_thread_done(thread_id, timeout=150.0)
    elapsed = time.time() - t0
    if not done:
        answer = answer or "(timeout waiting turn/completed)"
    return answer, elapsed


def main():
    concurrent = "--concurrent" in sys.argv
    os.makedirs(CODEX_HOME, exist_ok=True)

    if not concurrent:
        print("== 验链①：单 thread 提问「三班概况」，经 MCP 取真实数据 ==")
        c = AppServerClient("single")
        try:
            answer, dt = run_turn(c, "请告诉我三班现在的概况：学生数、考试数、教学进度。")
            print(f"[{dt:.1f}s] 壳的回答：\n{answer}")
            ok = ("七(1)班" in answer or "30" in answer) and "学生" in answer
            print(f"\n验链①结果：{'✅ 通过' if ok else '❌ 未通过'}（回答含真实数据：{ok}）")
            sys.exit(0 if ok else 1)
        finally:
            c.close()
    else:
        print("== 验链②：同一 app-server 进程内两个 thread 并发 Turn ==")
        c = AppServerClient("shared")
        results = {}
        c.request("initialize", {
            "clientInfo": {"name": "sc-phase0-verify", "title": "Phase 0 verifier", "version": "0.0.1"},
        })

        def work(name: str):
            try:
                # 同一连接上两个 thread 各自 start+turn；initialize 已做，跳过
                answer, dt = run_turn(c, f"教师{name}提问：三班概况如何？", initialize=False)
                results[name] = (answer[:80], dt, None)
            except Exception as e:
                results[name] = ("", 0.0, str(e))

        threads = [threading.Thread(target=work, args=(n,)) for n in ("A", "B")]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total = time.time() - t0
        for name, (ans, dt, err) in results.items():
            print(f"教师{name}: {dt:.1f}s err={err} ans={ans!r}")
        serial_estimate = sum(dt for _, dt, _ in results.values())
        ratio = serial_estimate / total if total > 0 else 0
        print(f"\n总耗时 {total:.1f}s，各 Turn 耗时和 {serial_estimate:.1f}s，并发比 {ratio:.2f}")
        print("判读：两 Turn 时间线显著重叠（总耗时 ≈ max 而非 sum）即真并发。")


if __name__ == "__main__":
    main()
