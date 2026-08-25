"""钉钉通知测试（Phase 3 批次D，D4）。

未配置=静默跳过（返回 False 不报错）；卡片载荷形状；加签 URL。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _reload(monkeypatch, **env):
    for k in ("SC_DINGTALK_ENABLED", "SC_DINGTALK_WEBHOOK", "SC_DINGTALK_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        if v is not None:
            monkeypatch.setenv(k, v)
    from gateway import dingtalk

    return importlib.reload(dingtalk)


def test_disabled_by_default(monkeypatch):
    dt = _reload(monkeypatch)
    assert dt.send_text("t", "x") is False  # 未配置：False 而非异常


def test_card_payload_shape(monkeypatch):
    dt = _reload(
        monkeypatch,
        SC_DINGTALK_ENABLED="1",
        SC_DINGTALK_WEBHOOK="https://oapi.dingtalk.com/robot/send?access_token=x",
    )
    captured = {}

    def fake_post(url, json=None, **kw):
        captured["url"] = url
        captured["json"] = json

        class R:
            status_code = 200

            def json(self):
                return {"errcode": 0}

        return R()

    with httpx.Client() as c:
        monkeypatch.setattr(c, "post", fake_post)
        ok = dt.send_text("标题", "正文内容", link="https://wb/inbox", client=c)
    assert ok is True
    body = captured["json"]
    assert body["msgtype"] == "markdown"
    assert body["markdown"]["title"] == "标题"
    assert "[查看详情](https://wb/inbox)" in body["markdown"]["text"]


def test_signed_url_contains_signature(monkeypatch):
    dt = _reload(
        monkeypatch,
        SC_DINGTALK_ENABLED="1",
        SC_DINGTALK_WEBHOOK="https://oapi.dingtalk.com/robot/send?access_token=x",
        SC_DINGTALK_SECRET="SECxyz",
    )
    url = dt._signed_url()
    q = parse_qs(urlparse(url).query)
    assert "timestamp" in q and "sign" in q


def test_notify_draft_ready_builds_payload(monkeypatch):
    dt = _reload(
        monkeypatch,
        SC_DINGTALK_ENABLED="1",
        SC_DINGTALK_WEBHOOK="https://oapi.dingtalk.com/robot/send?access_token=x",
    )
    got = []

    class R:
        status_code = 200

        def json(self):
            return {"errcode": 0}

    def fake_post(url, json=None, **kw):
        got.append(json)

        class _R:
            status_code = 200

            def json(self):
                return {"errcode": 0}

        return _R()

    client = httpx.Client()
    monkeypatch.setattr(client, "post", fake_post)
    assert dt.notify_draft_ready("三班", "学生诊断单·小A", "# 小A 的诊断…", client=client) is True
    text = got[0]["markdown"]["text"]
    assert "小A" in text and "签发" in text
