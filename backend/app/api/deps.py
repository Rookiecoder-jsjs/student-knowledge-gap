"""API 共享依赖（架构修复 候选2：routes 里反复出现的 HTTP 基础设施收归一处）。

- ``get_db``：会话生命周期（提交/回滚/关闭）；
- ``_active_kb`` / ``_graph`` / ``_as_dt``：分析类端点共同的派生输入；
- ``require_teacher`` / ``access_ctx`` / ``guard_class`` 等：G11 鉴权依赖
  （裁决逻辑在 app.auth，本模块只做 HTTP 信号翻译——401/403）。
  服务/查询层不反向依赖本模块——这里只做 HTTP 层信号翻译，领域层抛领域异常。
"""

from __future__ import annotations

from datetime import date, datetime, time

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import auth as _auth
from app.db import SessionLocal
from app.kb.graph import KpGraph
from app.kb.resolver import KbNotActiveError, active_kb
from app.models import KbVersion

# 兼容旧名引用（tests / routers 以 PermissionError_ 捕获翻译）
PermissionError_ = _auth.PermissionError_


def get_db():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# G11 鉴权依赖（agent-product-design §5.5）
# ---------------------------------------------------------------------------


def access_ctx(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> "_auth.AccessContext":
    """解析身份上下文（开放模式匿名 → teacher=None；token 无效 401）。"""
    try:
        return _auth.AccessContext(teacher=_auth.current_teacher(db, authorization))
    except _auth.AuthError as e:
        raise HTTPException(401, str(e)) from e


def require_teacher(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> "_auth.AccessContext":
    """业务端点守卫：安全模式下必须持有效 token（开放模式匿名放行）。"""
    ctx = access_ctx(authorization=authorization, db=db)
    if ctx.teacher is None and _auth.security_mode_on(db):
        raise HTTPException(401, "需要登录")
    return ctx


def guard_class(class_id: int, db: Session, ctx: "_auth.AccessContext"):
    """班级归属校验：403 翻译（LookupError 由调用方先做 404 或共用此函数）。"""
    try:
        return _auth.assert_class_access(db, ctx, class_id)
    except _auth.PermissionError_ as e:
        raise HTTPException(403, str(e)) from e


def _graph(session: Session, kb_version_id: int) -> KpGraph:
    return KpGraph(session, kb_version_id)


def _active_kb(session: Session) -> KbVersion:
    """active 知识库（strict 策略统一在 kb.resolver，候选5a）。

    strict 无 active → 400；无任何版本 → 400「尚未导入」。HTTP 层只做信号翻译。
    """
    try:
        kb = active_kb(session)
    except KbNotActiveError as e:
        raise HTTPException(400, str(e))
    if kb is None:
        raise HTTPException(400, "尚未导入知识库，请先 POST /kb/import")
    return kb


def _as_dt(d: date | None) -> datetime:
    # 默认=本地今日结束：occurred_at 为考试日 naive 本地时间（中午 12:00），
    # 若用 utcnow() 东八区当天证据会被当成"未来"而漏过证据门槛。
    return datetime.combine(d, time(23, 59)) if d else datetime.combine(
        datetime.now().date(), time(23, 59)
    )
