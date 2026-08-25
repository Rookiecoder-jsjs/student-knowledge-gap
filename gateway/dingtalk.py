"""钉钉通知（agent-product-design §5.7/D4，Phase 3 批次D）。

D4 定案：钉钉先行，**Stream 模式 = 出站 WebSocket**——校内无公网环境友好，
不需要公网回调地址。本模块职责边界刻意收窄：

- **出站通知**（本期实现）：草稿待签发 / 干预建议待确认 / 月度限额提醒，
  经钉钉「机器人 webhook」或「企业内部应用工作通知」API 发送卡片；
- **入站指令**（远期）：Stream 长连接接收教师在群里的 @ 指令——需要会话
  上下文映射，本期不实现，接口留位。

配置（.env / compose env，全部缺省关闭=零依赖可跑）：
    SC_DINGTALK_WEBHOOK=   # 自定义机器人 webhook（含 access_token）；最简通道
    SC_DINGTALK_SECRET=    # 机器人加签密钥（可选；HMAC-SHA256 时间戳签名）
    SC_DINGTALK_ENABLED=0  # 总开关（默认关）

纪律与 §5.8 一致：通知是锦上添花，任何失败只记日志、绝不上抛——
断网/限流不影响主流程。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import urllib.parse

import httpx

ENABLED = os.environ.get("SC_DINGTALK_ENABLED", "0").lower() in ("1", "true", "yes")
WEBHOOK = os.environ.get("SC_DINGTALK_WEBHOOK", "")
SECRET = os.environ.get("SC_DINGTALK_SECRET", "")

_TIMEOUT_S = float(os.environ.get("SC_DINGTALK_TIMEOUT", "6"))


def _signed_url() -> str:
    """加签机器人：timestamp+secret HMAC 后追加 query（钉钉安全设置0）。"""
    if not SECRET:
        return WEBHOOK
    ts = str(round(time.time() * 1000))
    sign = base64.b64encode(
        hmac.new(
            SECRET.encode(), f"{ts}\n{SECRET}".encode(), hashlib.sha256
        ).digest()
    )
    return f"{WEBHOOK}&timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"


def _card(title: str, text: str, link: str | None = None) -> dict:
    """markdown 卡片载荷；link 提供时附「查看详情」跳转（直达工作台页面）。"""
    md = text
    if link:
        md += f"\n\n[查看详情]({link})"
    return {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": md},
    }


def send_text(title: str, text: str, *, link: str | None = None,
              webhook: str | None = None, client: httpx.Client | None = None) -> bool:
    """发一张 markdown 卡片。返回是否成功；任何异常吞掉记 False。

    webhook/client 参数供测试注入；生产路径用模块级配置。
    """
    hook = webhook or WEBHOOK
    if not ENABLED or not hook:
        return False
    payload = _card(title, text, link)
    try:
        url = _signed_url() if (webhook is None and SECRET) else (
            webhook or WEBHOOK)
        if client is not None:
            r = client.post(url, json=payload)
        else:
            with httpx.Client(timeout=_TIMEOUT_S) as hc:
                r = hc.post(url, json=payload)
        ok = r.status_code == 200 and r.json().get("errcode") == 0
        if not ok:
            print(f"[dingtalk] rejected: {r.status_code} {r.text[:200]}")
        return ok
    except Exception as e:  # noqa: BLE001 —— 通知失败不上抛（§5.8 纪律）
        print(f"[dingtalk] send failed: {e}")
        return False


# ---------------------------------------------------------------------------
# 业务通知模板（触发点在 gateway/main.py 与 sc 侧钩子）
# ---------------------------------------------------------------------------


def notify_draft_ready(class_name: str, type_label: str, preview: str,
                       workbench_url: str | None = None,
                       client: httpx.Client | None = None) -> bool:
    """新草稿待签发（§4.3 收件箱的触达延伸：「找老师」变「老师被找到」）。"""
    return send_text(
        f"{class_name}·{type_label}待签发",
        f"**{type_label}** 草稿已生成，等待你签发。\n\n> {preview[:80]}…",
        link=f"{workbench_url}/inbox" if workbench_url else None,
        client=client,
    )


def notify_intervention_suggested(alias: str, kp_name: str, kind_label: str,
                                  workbench_url: str | None = None,
                                  client: httpx.Client | None = None) -> bool:
    """干预建议待确认（行动明细的一键确认入口在卡片落地页）。"""
    return send_text(
        "行动建议待确认",
        f"为 **{alias}** 的「{kp_name}」登记了 **{kind_label}** 建议，"
        "可在行动明细中确认执行。",
        link=f"{workbench_url}/exams?tab=diagnosis" if workbench_url else None,
        client=client,
    )


def notify_monthly_usage(message: str) -> bool:
    """月度软限额提醒（budget.check_monthly → monthly_usage 钩子注册此函数）。"""
    return send_text("模型用量提醒", message)
