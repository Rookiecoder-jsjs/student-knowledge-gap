"""gateway school-authz 身份签发测试（§6.3 / §5.5）。

覆盖：
- `_sign_school_token`：格式与 sc 后端 auth.py 一致（`teacher_id.exp.sig`，HMAC-SHA256 hex）；
- `_teacher_identity_env`：SC_AUTH_SECRET 配置 → 只带签名 token（SC_SCHOOL_AUTH_TOKEN）；
  未配置 → {}（装车批第 5 批：不再注入裸 SC_MCP_TEACHER_ID env——远程 /mcp 逐请求
  头才是身份载体，无 token 即开放模式匿名）。
"""

from __future__ import annotations

import hashlib
import hmac
import time

import gateway.main as gm

SECRET = "test-school-secret"


def _recompute_sig(token: str, secret: str) -> str:
    body, sig = token.rsplit(".", 1)
    expect = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert len(sig) == 64
    return expect


def test_sign_school_token_format_and_signature(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", SECRET)
    token = gm._sign_school_token(7)

    teacher_raw, exp_raw, sig = token.split(".")
    assert teacher_raw == "7"
    exp = int(exp_raw)
    now = time.time()
    assert now < exp <= now + gm._SCHOOL_TOKEN_TTL_S + 5
    assert _recompute_sig(token, SECRET) == sig


def test_sign_school_token_different_teacher_distinct(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", SECRET)
    a = gm._sign_school_token(7)
    b = gm._sign_school_token(8)
    assert a.split(".")[0] == "7" and b.split(".")[0] == "8"


def test_teacher_identity_env_signed_when_secret(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", SECRET)
    env = gm._teacher_identity_env(7)

    assert "SC_MCP_TEACHER_ID" not in env  # 远程模型不再注入裸身份
    token = env["SC_SCHOOL_AUTH_TOKEN"]
    assert token.split(".")[0] == "7"
    assert _recompute_sig(token, SECRET) == token.rsplit(".", 1)[1]


def test_teacher_identity_env_empty_without_secret(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", "")
    env = gm._teacher_identity_env(7)

    assert env == {}  # 无密钥 → 不发身份头（codex 省略 Authorization → backend 匿名）
