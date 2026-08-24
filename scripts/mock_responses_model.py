"""本地 mock 模型服务器——说 codex 壳要求的 OpenAI **Responses API** 方言（SSE）。

用途（Phase 0 验链①②的受控模型替身）：
- 验链①：第一轮返回 get_class_overview 的 function_call，第二轮把工具结果
  里的真实数字复述成中文回答——证明「壳 ⇄ MCP ⇄ sc」全链路；
- 验链②：并发测试中按请求注入不同延迟，观察两个 thread 的 Turn 是否串行。
- 同时是验链③的前置事实收集器：记录壳发来的完整请求体，供分析工具面与
  prompt 形态（也用于未来网关侧 Responses→Chat 翻译层的规格依据）。

背景注记：锚点版本（rust-v0.149.1）已**移除** wire_api="chat"（WireApi 枚举
只剩 Responses），因此 mock 必须实现 /v1/responses SSE 而非 /v1/chat/completions。
国产模型的接入形态问题由此升级为验链③的新核心：端点原生支持 Responses，
或在网关侧做协议翻译。本服务器的请求日志即翻译层的规格输入。

行为脚本化：
- 请求里若含 function_call_output（工具结果已回喂）→ 输出最终中文回答；
  - 若工具结果含 "七(1)班" 相关数字则复述学生数/考试数/进度；
  - 否则泛答「调查完成」；
- 否则 → 下发 get_class_overview 工具调用（call_id 自增）。
可用环境变量调节：MOCK_DELAY_MS（每响应前延迟，并发测试用）。

用法：python scripts/mock_responses_model.py [port]   （默认 6060）
"""

from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY_S = int(os.environ.get("MOCK_DELAY_MS", "0")) / 1000
_state = {"call_seq": 0, "requests": []}


def _sse(event: dict) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _final_answer(text: str) -> list[bytes]:
    """一条 assistant message + completed 的 SSE 序列。"""
    rid = f"resp-mock-{int(time.time() * 1000)}"
    return [
        _sse({"type": "response.created", "response": {"id": rid}}),
        _sse({
            "type": "response.output_item.done",
            "item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]},
        }),
        _sse({
            "type": "response.completed",
            "response": {
                "id": rid,
                "usage": {
                    "input_tokens": 10,
                    "input_tokens_details": None,
                    "output_tokens": 10,
                    "output_tokens_details": None,
                    "total_tokens": 20,
                },
            },
        }),
    ]


def _pick_tool_name(tools: list | None) -> tuple[str, str]:
    """从壳发来的工具清单里找到本工具，返回 (namespace, name)。

    0.149.1 默认把每个 MCP server 的工具归进一个 namespace 组：
    tools 数组元素形如 {"type":"namespace","name":"mcp__sc","tools":[...]}。
    返回值直接用于 function_call 的独立 namespace/name 字段
    （tools/router.rs build_tool_call：ToolName::new(namespace, name)）。
    """
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "namespace":
            ns = t.get("name") or ""
            for f in t.get("tools") or []:
                fname = (f.get("function") or {}).get("name") or f.get("name") or ""
                if "get_class_overview" in fname:
                    return ns, fname
            continue
        fn = t.get("function") or {}
        name = fn.get("name") or t.get("name") or ""
        if "get_class_overview" in name:
            return "", name
    return "mcp__sc", "get_class_overview"


def _tool_call_overview(tool_ns: str, tool_name: str) -> list[bytes]:
    """下发一次工具调用：function_call 用独立的 name + namespace 字段
    （tools/router.rs build_tool_call：ToolName::new(namespace, name)）。"""
    _state["call_seq"] += 1
    call_id = f"call-mock-{_state['call_seq']}"
    item = {
        "type": "function_call",
        "call_id": call_id,
        "name": tool_name,
        "arguments": "{}",
    }
    if tool_ns:
        item["namespace"] = tool_ns
    return [
        _sse({"type": "response.created", "response": {"id": f"resp-tool-{_state['call_seq']}"}}),
        _sse({"type": "response.output_item.done", "item": item}),
        _sse({
            "type": "response.completed",
            "response": {
                "id": f"resp-tool-{_state['call_seq']}",
                "usage": {
                    "input_tokens": 10,
                    "input_tokens_details": None,
                    "output_tokens": 5,
                    "output_tokens_details": None,
                    "total_tokens": 15,
                },
            },
        }),
    ]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 安静模式：关键信息自行打印
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        _state["requests"].append({
            "path": self.path,
            "tools": [t.get("function", {}).get("name") or t.get("name")
                      for t in body.get("tools", []) if isinstance(t, dict)],
            "has_tool_output": any(
                (i.get("type") == "function_call_output")
                for i in body.get("input", []) if isinstance(i, dict)
            ),
            "n_input_items": len(body.get("input", [])),
        })
        req_no = len(_state["requests"])
        print(f"[mock] request #{req_no}: tools={_state['requests'][-1]['tools']} "
              f"has_tool_output={_state['requests'][-1]['has_tool_output']}", flush=True)
        if os.environ.get("MOCK_DUMP"):
            with open(f"/tmp/sc-phase0/req-{req_no}.json", "w") as f:
                json.dump(body, f, ensure_ascii=False, indent=1)

        if DELAY_S:
            time.sleep(DELAY_S)

        # 从 input 里找 function_call_output 的内容文本
        tool_text = ""
        for item in body.get("input", []):
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                payload = item.get("output")
                tool_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

        if tool_text:
            # 第二轮：把工具结果里的真实数字复述出来（证明数字来自工具返回值）
            answer = "调查完成。" + _extract_numbers(tool_text)
            chunks = _final_answer(answer)
        else:
            ns, name = _pick_tool_name(body.get("tools"))
            chunks = _tool_call_overview(ns, name)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for c in chunks:
            self.wfile.write(c)
            self.wfile.flush()

    def do_GET(self):
        self.send_response(404)
        self.end_headers()


def _extract_numbers(tool_text: str) -> str:
    """从 sc 工具返回 JSON 里抠关键数字，验证「数字来自工具而非模型」。

    output 形如 "Wall time: ...\\nOutput:\\n[{\\"type\\":\\"text\\",\\"text\\":\\"{...}\\"}]"，
    text 字段里才是工具返回的真 JSON；直接正则抽数字字段，不做严格解析。
    """
    import re
    try:
        m_name = re.search(r'"name\\?":\s*"([^"]+)"', tool_text)
        m_students = re.search(r'"student_count\\?":\s*(\d+)', tool_text)
        m_exams = re.search(r'"exam_count\\?":\s*(\d+)', tool_text)
        m_taught = re.search(r'"taught\\?":\s*(\d+)', tool_text)
        m_total = re.search(r'"total\\?":\s*(\d+)', tool_text)
        if m_students:
            return (
                f"{m_name.group(1) if m_name else '该班'}现有 {m_students.group(1)} 名学生、"
                f"{m_exams.group(1) if m_exams else '?'} 场考试，教学进度已覆盖 "
                f"{m_taught.group(1) if m_taught else '?'}/{m_total.group(1) if m_total else '?'} 个知识点"
                f"（数据来自 sc MCP 工具返回值）。"
            )
    except Exception:
        pass
    return "（未解析到班级数据）"


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 6060
    print(f"[mock] Responses API mock listening on http://127.0.0.1:{port}/v1 "
          f"(delay={DELAY_S}s)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
