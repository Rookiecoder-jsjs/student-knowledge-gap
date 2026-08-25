"""管理端路由：用量台账「本月消耗」（agent-product-design §5.9，批次D）。

单校部署无多租户，管理页不另设鉴权（与既有管理操作同信任域）；
月度参数缺省=当前月。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.admin_usage import usage_by_day_task, usage_summary_month
from app.api.deps import get_db

router = APIRouter()


def _month_param(month: str | None) -> str:
    if month:
        return month
    return date.today().strftime("%Y-%m")


@router.get("/admin/usage")
def admin_usage(month: str | None = None, db: Session = Depends(get_db)):
    """按 task/日 聚合的 token 与调用数（管理端「本月消耗」页数据源）。"""
    m = _month_param(month)
    try:
        return usage_by_day_task(db, m)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/admin/usage/summary")
def admin_usage_summary(month: str | None = None, db: Session = Depends(get_db)):
    """整月合计摘要（角标/卡片用）。"""
    m = _month_param(month)
    try:
        return usage_summary_month(db, m)
    except ValueError as e:
        raise HTTPException(400, str(e))
