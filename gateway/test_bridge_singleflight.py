"""get_bridge 首建单飞回归（2026-09-11 实锤缺陷）。

页面加载时 SSE 与首个 RPC 并发调用 get_bridge，旧 check-then-act 实现各 spawn
一个 app-server 桥：SSE 订阅落在败者桥上，而 thread/turn RPC 全走胜者桥——事件
（item/*、tokenUsage、turn/completed）广播给胜者桥的空订阅集，浏览器侧表现为
「永不流式、用量不出、busy 挂死」（RPC 结果正常返回，事件通道空转只剩
keepalive）。回归口径：同键并发首建只 spawn 一次且双方拿到同一实例；存活桥
复用不再 spawn；死桥回收重建同样过单飞。
"""

from __future__ import annotations

import asyncio

import gateway.main as gm


class _FakeProc:
    """最小桥进程替身：stdin=None 走 stop() 的兜底路径，poll() 决定存活性。"""

    stdin = None

    def __init__(self, alive: bool = True):
        self._alive = alive

    def poll(self):
        return None if self._alive else 1

    def kill(self):
        pass

    def wait(self, timeout=5):
        return 0


class _LiveReader:
    """Minimal live reader marker matching the production Bridge contract."""

    def done(self):
        return False


class _CaptureStdin:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, value: str):
        self.lines.append(value)

    def flush(self):
        pass


def _live_bridge() -> gm.Bridge:
    bridge = gm.Bridge(proc=_FakeProc())
    bridge._reader = _LiveReader()
    return bridge


def test_server_request_response_keeps_original_id():
    stdin = _CaptureStdin()
    proc = _FakeProc()
    proc.stdin = stdin
    bridge = gm.Bridge(proc=proc)
    bridge._reader = _LiveReader()

    bridge.respond(61, result={"decision": "accept"})

    assert stdin.lines == [
        '{"jsonrpc": "2.0", "id": 61, "result": {"decision": "accept"}}\n'
    ]


def test_concurrent_first_build_spawns_once(monkeypatch):
    calls: list[int] = []

    async def _slow_spawn(teacher_id: int = 0) -> gm.Bridge:
        calls.append(teacher_id)
        await asyncio.sleep(0.05)  # 拉宽窗口：并发第二个调用必须合并到同一次 spawn
        return _live_bridge()

    monkeypatch.setattr(gm.Bridge, "spawn", staticmethod(_slow_spawn))
    gm._BRIDGES.pop("t77", None)

    async def _scenario():
        a, b = await asyncio.gather(
            gm.get_bridge("t77", 77),
            gm.get_bridge("t77", 77),
        )
        # Keep reuse on the same event-loop turn used by the bridge owner.
        again = await gm.get_bridge("t77", 77)
        return a, b, again

    try:
        a, b, again = asyncio.run(_scenario())
        assert a is b, "并发首建必须返回同一桥实例"
        assert calls == [77], f"同键并发只允许一次 spawn，实际 {len(calls)} 次"
        assert gm._BRIDGES["t77"] is a
        # 复用路径：存活桥直接返回，不再 spawn
        assert again is a
        assert calls == [77]
    finally:
        gm._BRIDGES.pop("t77", None)


def test_dead_bridge_recycled_and_respawned(monkeypatch):
    spawns: list[int] = []

    async def _spawn(teacher_id: int = 0) -> gm.Bridge:
        spawns.append(teacher_id)
        return gm.Bridge(proc=_FakeProc())

    monkeypatch.setattr(gm.Bridge, "spawn", staticmethod(_spawn))
    dead = gm.Bridge(proc=_FakeProc(alive=False))
    gm._BRIDGES["t78"] = dead
    try:
        br = asyncio.run(gm.get_bridge("t78", 78))
        assert br is not dead
        assert gm._BRIDGES["t78"] is br
        assert spawns == [78]
    finally:
        gm._BRIDGES.pop("t78", None)
