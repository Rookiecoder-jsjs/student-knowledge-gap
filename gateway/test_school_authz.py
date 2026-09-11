"""gateway school-authz 身份签发 + 统一登录校验测试（§6.3 / §5.5 / auth-roles-design）。

覆盖：
- `_sign_school_token`：格式与 sc 后端 auth.py 一致（`t.{teacher_id}.{exp}.{sig}`，
  HMAC-SHA256 hex，身份种类 kind 前置）；
- `require_auth`：接受 sc backend 签发的教师 token（统一登录），拒绝 student token；
- `_teacher_identity_env`：SC_AUTH_SECRET 配置 → 只带签名 token（SC_SCHOOL_AUTH_TOKEN）；
  未配置 → {}（装车批第 5 批：不再注入裸 SC_MCP_TEACHER_ID env——远程 /mcp 逐请求
  头才是身份载体，无 token 即开放模式匿名）。
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from fastapi import HTTPException

import gateway.main as gm

SECRET = "test-school-secret"


def _recompute_sig(token: str, secret: str) -> str:
    body, sig = token.rsplit(".", 1)
    expect = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert len(sig) == 64
    return expect


def _sign(kind: str, uid: int, secret: str = SECRET, ttl: int = 60) -> str:
    exp = int(time.time()) + ttl
    body = f"{kind}.{uid}.{exp}"
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def test_sign_school_token_format_and_signature(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", SECRET)
    token = gm._sign_school_token(7)

    kind, teacher_raw, exp_raw, sig = token.split(".")
    assert kind == "t"
    assert teacher_raw == "7"
    exp = int(exp_raw)
    now = time.time()
    assert now < exp <= now + gm._SCHOOL_TOKEN_TTL_S + 5
    assert _recompute_sig(token, SECRET) == sig


def test_sign_school_token_different_teacher_distinct(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", SECRET)
    a = gm._sign_school_token(7)
    b = gm._sign_school_token(8)
    assert a.split(".")[0] == "t"
    assert a.split(".")[1] == "7" and b.split(".")[1] == "8"


def test_teacher_identity_env_signed_when_secret(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", SECRET)
    env = gm._teacher_identity_env(7)

    assert "SC_MCP_TEACHER_ID" not in env  # 远程模型不再注入裸身份
    token = env["SC_SCHOOL_AUTH_TOKEN"]
    assert token.split(".")[0] == "t"
    assert token.split(".")[1] == "7"
    assert _recompute_sig(token, SECRET) == token.rsplit(".", 1)[1]


def test_teacher_identity_env_empty_without_secret(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", "")
    env = gm._teacher_identity_env(7)

    assert env == {}  # 无密钥 → 不发身份头（codex 省略 Authorization → backend 匿名）


# ---------------------------------------------------------------------------
# require_auth：统一登录（backend 签发的教师 token 主路径）
# ---------------------------------------------------------------------------


def test_require_auth_accepts_backend_teacher_token(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", SECRET)
    tok = _sign("t", 7)
    sess = gm.require_auth(authorization=f"Bearer {tok}")
    assert sess.teacher_id == 7
    assert sess.username == "t7"  # 按教师身份寻址 bridge


def test_require_auth_rejects_student_token(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", SECRET)
    tok = _sign("s", 99)
    with pytest.raises(HTTPException) as exc:
        gm.require_auth(authorization=f"Bearer {tok}")
    assert exc.value.status_code == 401


def test_require_auth_rejects_without_secret(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", "")
    tok = _sign("t", 7, secret="other-secret")
    with pytest.raises(HTTPException) as exc:
        gm.require_auth(authorization=f"Bearer {tok}")
    assert exc.value.status_code == 401


def test_require_auth_rejects_expired(monkeypatch):
    monkeypatch.setattr(gm, "SCHOOL_AUTH_SECRET", SECRET)
    tok = _sign("t", 7, ttl=-10)
    with pytest.raises(HTTPException) as exc:
        gm.require_auth(authorization=f"Bearer {tok}")
    assert exc.value.status_code == 401
