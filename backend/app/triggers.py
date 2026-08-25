"""任务触发器 v1（agent-product-design §5.4，Phase 2 批次C）。

sc 摄取完成事件（commit 端点组合点）→ 网关内部接口 → 该班持久线程拉起
「考后分析」Task。设计约束：

- 触发器本体在 sc 侧（本模块），网关只暴露「在指定线程上发起任务」的
  内部接口——零核改；
- prompt 由**版本化的任务模板**生成（POST_EXAM_ANALYSIS_V1）；
- fire-and-forget：触发失败只记日志、绝不阻塞 commit 主流程（Agent 断供
  不降低可用性，§5.8 兜底不变量③延伸）；
- 幂等：同一考试同一天不重复触发（网关侧重试/教师手点都安全）。

网络立场（§8.2 出站-only）：backend → gateway 是 compose 内网 HTTP，
共享密钥 X-Internal-Key 鉴权（TRIGGER_INTERNAL_KEY 双方一致即放行）。
"""

from __future__ import annotations

import logging
import os

import httpx
from sqlalchemy.orm import Session

from app.models import ExamTemplate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 任务模板（版本化管理，沿用 NARRATIVE_PROMPT_VERSION 文化）
# ---------------------------------------------------------------------------

POST_EXAM_ANALYSIS_TEMPLATE_VERSION = "post-exam-analysis-v0.1.0"

_POST_EXAM_ANALYSIS_V1 = """请基于刚提交的考试数据完成一次考后分析，面向任课教师输出。

考试：{exam_name}（{exam_date}，{class_name}）

要求：
1. 先调用 get_exam_summary 获取本场事实（提交人数、均分、逐题得分率、共性薄弱点）；
2. 对最突出的 1~2 个共性薄弱知识点，用 get_kp_detail 查看前置链结构；
3. 只引用工具返回的数字，没有数据就明说；归因一律是「待确认假设」；
4. 结论给出 1~3 条杠杆排序的教学建议（成长框架措辞，不排名不贴标签）；
5. 全文中文，篇幅克制：先一段总览，再列要点。
"""


def post_exam_analysis_prompt(session: Session, exam_id: int) -> dict:
    """渲染触发式任务的用户消息。返回 {text, template_version, exam_id}。

    考试不存在抛 LookupError（调用方决定是否吞掉——fire-and-forget 语义下记日志即可）。
    """
    tpl = session.get(ExamTemplate, exam_id)
    if tpl is None:
        raise LookupError(f"考试 {exam_id} 不存在")
    text = _POST_EXAM_ANALYSIS_V1.format(
        exam_name=tpl.name,
        exam_date=tpl.exam_date,
        class_name=f"班级{tpl.class_id}",
    )
    return {"text": text, "template_version": POST_EXAM_ANALYSIS_TEMPLATE_VERSION, "exam_id": exam_id}


# ---------------------------------------------------------------------------
# 触发出口：POST gateway /internal/trigger（fire-and-forget）
# ---------------------------------------------------------------------------

_TRIGGER_TIMEOUT_S = float(os.environ.get("SC_TRIGGER_TIMEOUT", "8"))


def fire_post_exam_analysis(
    session: Session,
    exam_id: int,
    *,
    gateway_url: str | None = None,
    internal_key: str | None = None,
    client: httpx.Client | None = None,
) -> bool:
    """commit 成功后调用：通知网关在该班持久线程上发起考后分析。

    返回是否成功投递（网关不可达/未配置时 False 并记 warning，不抛异常——
    commit 主流程绝不因此失败）。测试注入 fake client 断言载荷与幂等键。
    """
    try:
        payload = post_exam_analysis_prompt(session, exam_id)
    except LookupError as e:
        logger.warning("trigger skip: %s", e)
        return False

    base = gateway_url or os.environ.get("SC_GATEWAY_URL", "")
    key = internal_key or os.environ.get("SC_TRIGGER_KEY", "")
    if not base or not key:
        logger.info("trigger disabled: SC_GATEWAY_URL/SC_TRIGGER_KEY 未配置")
        return False

    body = {
        "kind": "post_exam_analysis",
        "exam_id": exam_id,
        "class_id": session.get(ExamTemplate, exam_id).class_id,
        "idempotency_key": f"post_exam_analysis:{exam_id}",
        "message": payload["text"],
        "template_version": payload["template_version"],
    }
    try:
        if client is not None:
            resp = client.post(
                f"{base.rstrip('/')}/internal/trigger",
                json=body,
                headers={"X-Internal-Key": key},
            )
        else:
            with httpx.Client(timeout=_TRIGGER_TIMEOUT_S) as hc:
                resp = hc.post(
                    f"{base.rstrip('/')}/internal/trigger",
                    json=body,
                    headers={"X-Internal-Key": key},
                )
    except Exception as e:  # noqa: BLE001 —— fire-and-forget：任何网络异常都不上抛
        logger.warning("trigger delivery failed: %s", e)
        return False
    if resp.status_code >= 300:
        logger.warning("trigger rejected: HTTP %s %s", resp.status_code, resp.text[:200])
        return False
    return True
