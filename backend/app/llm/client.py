"""多模态 LLM 客户端：OpenAI 兼容 / Anthropic / Mock，环境变量切换。

配置（环境变量 / .env）：
- SC_LLM_PROVIDER = openai | anthropic | mock（默认 mock，便于测试与演示）
- SC_LLM_API_KEY  = 供应商密钥（兼容别名：qwen_api_key）
- SC_LLM_MODEL    = 默认模型（如 qwen3.7-flash / gpt-4o）
- SC_LLM_BASE_URL = openai 兼容端点（兼容别名：base_url）
- SC_LLM_VISION_MODEL = 拍照解析专用模型（可选，需视觉能力；缺省用 SC_LLM_MODEL）
- SC_LLM_TEXT_MODEL   = 报告叙述专用模型（可选；缺省用 SC_LLM_MODEL）
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()  # .env：SC_LLM_PROVIDER / SC_LLM_API_KEY / SC_LLM_MODEL / SC_LLM_BASE_URL

TIMEOUT = 120.0

# G13：LLM 幂等键（provider 支持时透传，避免 retry 重复计费）。
# 开启后按 (model, system, user, image) 内容哈希派生 Idempotency-Key 头，稳定跨重试--
# 同一 item 的 retry_batch_item 重读同一 tempfile + 同一 prompt -> 同一 key -> 命中 provider 缓存，
# 避免首次已成功、仅落库失败时重试再付费。默认关闭，仅当确认 provider 支持时开启。
LLM_IDEMPOTENCY_KEY = os.environ.get("SC_LLM_IDEMPOTENCY_KEY", "").lower() in (
    "1", "true", "yes",
)


def _idempotency_key(
    model: str, system: str, user: str, image_bytes: bytes | None
) -> str:
    """内容哈希派生幂等键（OpenAI/Anthropic 均接受字符串，取 36 位 hex）。"""
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(b"|")
    h.update(system.encode())
    h.update(b"|")
    h.update(user.encode())
    h.update(b"|")
    if image_bytes:
        h.update(image_bytes)
    return h.hexdigest()[:36]


class LLMError(RuntimeError):
    pass


class BaseClient:
    model_version: str = "unknown"

    def parse_json(self, system: str, user: str, image_bytes: bytes | None) -> dict:
        """发送 prompt（可带图片），返回解析后的 JSON 对象。"""
        raise NotImplementedError

    @staticmethod
    def _extract_json(text: str) -> dict:
        """从模型输出中稳健提取 JSON（容忍 ```json 围栏与前后杂讯）。"""
        text = text.strip()
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
        else:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                raise LLMError(f"模型输出不含 JSON: {text[:200]!r}")
            text = text[start : end + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"模型输出 JSON 解析失败: {e}") from e


class OpenAICompatClient(BaseClient):
    """OpenAI 及兼容供应商（含国产视觉模型的 OpenAI 兼容端点）。"""

    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model_version = model
        self.base_url = base_url.rstrip("/")

    def parse_json(self, system: str, user: str, image_bytes: bytes | None) -> dict:
        content: list[dict] = [{"type": "text", "text": user}]
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            )
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if LLM_IDEMPOTENCY_KEY:
            headers["Idempotency-Key"] = _idempotency_key(
                self.model_version, system, user, image_bytes
            )
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model_version,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return self._extract_json(text)


class AnthropicClient(BaseClient):
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model_version = model

    def parse_json(self, system: str, user: str, image_bytes: bytes | None) -> dict:
        content: list[dict] = []
        if image_bytes:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode(),
                    },
                }
            )
        content.append({"type": "text", "text": user})
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        if LLM_IDEMPOTENCY_KEY:
            headers["Idempotency-Key"] = _idempotency_key(
                self.model_version, system, user, image_bytes
            )
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json={
                "model": self.model_version,
                "max_tokens": 8192,
                "system": system,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        text = "".join(b["text"] for b in resp.json()["content"] if b["type"] == "text")
        return self._extract_json(text)


class MockLLMClient(BaseClient):
    """确定性 mock：按调用次序返回预设响应（测试与无密钥演示用）。"""

    model_version = "mock-vision-v0"

    def __init__(self, responses: list[dict] | None = None):
        self._responses = list(responses or [])
        self.calls: list[dict] = []

    def queue(self, payload: dict) -> None:
        self._responses.append(payload)

    def parse_json(self, system: str, user: str, image_bytes: bytes | None) -> dict:
        self.calls.append({"system": system, "user": user, "has_image": bool(image_bytes)})
        if not self._responses:
            raise LLMError("MockLLMClient 无预设响应")
        return self._responses.pop(0)


_override: BaseClient | None = None


def set_client(client: BaseClient | None) -> None:
    """测试注入钩子：set_client(MockLLMClient([...]))；传 None 恢复。"""
    global _override
    _override = client


def get_client(capability: str = "text") -> BaseClient:
    """capability: "vision"（拍照解析）| "text"（报告叙述）。

    视觉与文本任务可分别指定模型（SC_LLM_VISION_MODEL / SC_LLM_TEXT_MODEL），
    缺省回落到 SC_LLM_MODEL。
    """
    if _override is not None:
        return _override
    provider = os.environ.get("SC_LLM_PROVIDER", "mock").lower()
    api_key = os.environ.get("SC_LLM_API_KEY") or os.environ.get("qwen_api_key", "")
    default_model = os.environ.get("SC_LLM_MODEL", "")
    cap_key = "SC_LLM_VISION_MODEL" if capability == "vision" else "SC_LLM_TEXT_MODEL"
    model = os.environ.get(cap_key) or default_model
    if provider == "mock":
        raise LLMError(
            "未配置 LLM：请在 .env 设置 SC_LLM_PROVIDER=openai|anthropic 与密钥"
        )
    if provider == "anthropic":
        return AnthropicClient(api_key, model or "claude-sonnet-4-20250514")
    base_url = os.environ.get("SC_LLM_BASE_URL") or os.environ.get(
        "base_url", "https://api.openai.com/v1"
    )
    return OpenAICompatClient(api_key, model or "gpt-4o", base_url)
