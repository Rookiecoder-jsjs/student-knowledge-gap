"""sc 会话网关（Phase 0 雏形）。

浏览器 WebSocket ⇄ codex app-server stdio JSON-RPC 的双向翻译层。
设计文档 §2「网」；鉴权/触发器/身份注入属 Phase 1+。
"""

from .main import app

__all__ = ["app"]
