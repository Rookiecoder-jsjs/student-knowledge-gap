"""管理端路由：用量台账「本月消耗」（agent-product-design §5.9，批次D）。

单校部署无多租户；安全模式下仅 admin 教师可读（§5.5），开放模式沿用
既有信任域语义（存量测试零改动）。月度参数缺省=当前月。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from fastapi import Header

from app.admin_usage import usage_by_day_task, usage_summary_month
from app.api.deps import get_db, require_teacher

router = APIRouter()


def _month_param(month: str | None) -> str:
    if month:
        return month
    return date.today().strftime("%Y-%m")


@router.get("/admin/usage")
def admin_usage(
    month: str | None = None,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """按 task/日 聚合的 token 与调用数（管理端「本月消耗」页数据源）。"""
    from app.api.deps import access_ctx

    from app import auth as _auth

    ctx = require_teacher(authorization=authorization, db=db)
    if ctx.teacher is not None and not ctx.is_admin and _auth.security_mode_on(db):
        raise HTTPException(403, "需要管理员权限")
    m = _month_param(month)
    try:
        return usage_by_day_task(db, m)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/admin/usage/summary")
def admin_usage_summary(
    month: str | None = None,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """整月合计摘要（角标/卡片用）。"""
    from app.api.deps import access_ctx

    from app import auth as _auth

    ctx = require_teacher(authorization=authorization, db=db)
    if ctx.teacher is not None and not ctx.is_admin and _auth.security_mode_on(db):
        raise HTTPException(403, "需要管理员权限")
    m = _month_param(month)
    try:
        return usage_summary_month(db, m)
    except ValueError as e:
        raise HTTPException(400, str(e))
