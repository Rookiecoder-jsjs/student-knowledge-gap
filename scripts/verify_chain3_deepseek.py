#!/usr/bin/env python3
"""Phase 0 验链③：国产模型（DeepSeek 原生 Responses API）工具调用保真度实测。

不依赖壳，直接对 DeepSeek 端点做最小 Responses 往返：
  1. 带 sc MCP 同款工具定义发起请求 → 断言返回 function_call 且参数合法 JSON；
  2. 回喂 function_call_output（真实 sc 数据）→ 断言最终回答引用了真实数字。

用法：DEEPSEEK_API_KEY=sk-... python scripts/verify_chain3_deepseek.py
（key 也可放 backend/.env 的 SC_LLM_API_KEY 或 DEEPSEEK_API_KEY 行）
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = "https://api.deepseek.com"
MODEL = os.environ.get("SC_VERIFY_MODEL", "deepseek-v4-flash")

TOOL_DEF = {
    "type": "function",
    "name": "get_class_overview",
    "description": (
        "获取所有班级的轻量概览。每班返回学生数、考试数、待办数、最近考试状态、"
        "教学进度覆盖。班级级统计，不含学生个人信息。"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

REAL_TOOL_OUTPUT = json.dumps({
    "classes": [{
        "class_id": 1, "name": "七(1)班", "grade": 7, "subject": "数学",
        "student_count": 30, "exam_count": 6,
        "latest_exam": {"name": "期中考试", "exam_date": "2026-01-15"},
        "progress": {"taught": 40, "total": 40},
    }],
}, ensure_ascii=False)


def post(path: str, payload: dict, key: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main() -> int:
    key = os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        env_file = os.path.join(os.path.dirname(__file__), "..", "backend", ".env")
        if os.path.exists(env_file):
            for line in open(env_file):
                if line.startswith(("DEEPSEEK_API_KEY=", "SC_LLM_API_KEY=")):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        print("❌ 未找到 API key。请 DEEPSEEK_API_KEY=sk-... 运行，或写入 backend/.env")
        return 2

    # --- 第 1 步：应触发工具调用 ---
    r1 = post("/responses", {
        "model": MODEL,
        "instructions": "你是教育数据分析师。回答任何班级问题前必须先调用 get_class_overview 工具取数。",
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "三班现在有多少学生？"}]}],
        "tools": [TOOL_DEF],
        "tool_choice": "auto",
        "stream": False,
    }, key)
    calls = [it for it in r1.get("output", []) if it.get("type") == "function_call"]
    if not calls:
        print(f"❌ 第 1 步失败：未返回 function_call。output 类型: "
              f"{[i.get('type') for i in r1.get('output', [])]}")
        return 1
    call = calls[0]
    try:
        args = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        print(f"❌ arguments 不是合法 JSON: {call.get('arguments')!r}")
        return 1
    print(f"✅ 第 1 步：返回 function_call name={call['name']} args={args} call_id={call['call_id']}")

    # --- 第 2 步：回喂真实数据，应引用数字作答 ---
    r2 = post("/responses", {
        "model": MODEL,
        "instructions": "你是教育数据分析师。只引用工具返回的数字，没有数据就明说不知道。",
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "三班现在有多少学生？"}]},
            {"type": "function_call", "call_id": call["call_id"], "name": call["name"],
             "arguments": call["arguments"]},
            {"type": "function_call_output", "call_id": call["call_id"], "output": REAL_TOOL_OUTPUT},
        ],
        "tools": [TOOL_DEF],
        "stream": False,
    }, key)
    msgs = [it for it in r2.get("output", []) if it.get("type") == "message"]
    text_parts = []
    for m in msgs:
        for c in m.get("content", []):
            if isinstance(c.get("text"), str):
                text_parts.append(c["text"])
    answer = "".join(text_parts)
    print(f"第 2 步回答：{answer[:300]}")
    ok = ("30" in answer) and ("七" in answer or "班" in answer)
    usage = r2.get("usage") or {}
    print(f"\n验链③结果：{'✅ 通过' if ok else '❌ 未通过'}"
          f"（model={MODEL}, tokens in/out={usage.get('input_tokens')}/{usage.get('output_tokens')}）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
