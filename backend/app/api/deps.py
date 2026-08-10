"""API 共享依赖（架构修复 候选2：routes 里反复出现的 HTTP 基础设施收归一处）。

- ``get_db``：会话生命周期（提交/回滚/关闭）；
- ``_active_kb`` / ``_graph`` / ``_as_dt``：分析类端点共同的派生输入。
  服务/查询层不反向依赖本模块——这里只做 HTTP 层信号翻译，领域层抛领域异常。
"""

from __future__ import annotations

from datetime import date, datetime, time

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.kb.graph import KpGraph
from app.kb.resolver import KbNotActiveError, active_kb
from app.models import KbVersion


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
