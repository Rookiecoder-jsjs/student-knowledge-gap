"""鉴权路由（G11，agent-product-design §5.5 一期）：登录 / 建账号 / 班级授权。

- ``POST /auth/login``：口令换 token（白名单端点，安全模式下也匿名可达）；
- 账号与授权管理（``POST /auth/teachers`` 等）仅 admin 可用——首个 admin
  由 bootstrap 脚本/命令行创建（scripts/create_teacher.py），避免鸡生蛋；
- 本 router 只做 HTTP 翻译；裁决逻辑在 app.auth。
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.api.deps import get_db
from app.models import Class as ClassModel

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class TeacherCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    school_id: int
    admin: bool = False


class GrantRequest(BaseModel):
    class_ids: list[int]


def _require_admin(db: Session, authorization: str) -> auth.AccessContext:
    from app.api.deps import access_ctx, require_teacher

    ctx = require_teacher(authorization=authorization, db=db)
    if not ctx.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return ctx


@router.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """口令登录 → Bearer token。开放模式同样可用（提前建号不改变模式）。"""
    try:
        teacher, token = auth.authenticate(db, req.username, req.password)
    except auth.AuthError as e:
        raise HTTPException(401, str(e)) from e
    classes = [
        {"class_id": c.id, "name": c.name}
        for c in sorted(teacher.classes, key=lambda c: c.id)
    ]
    return {
        "token": token,
        "teacher": {"id": teacher.id, "name": teacher.name, "admin": teacher.admin},
        "classes": classes,
    }


@router.get("/auth/me")
def me(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """当前身份（前端会话恢复用；匿名返回 authenticated:false）。"""
    from app.api.deps import access_ctx

    try:
        ctx = access_ctx(authorization=authorization, db=db)
    except HTTPException:
        raise
    if ctx.teacher is None:
        return {"authenticated": False}
    t = ctx.teacher
    return {
        "authenticated": True,
        "teacher": {"id": t.id, "name": t.name, "admin": t.admin},
        "classes": [{"class_id": c.id, "name": c.name} for c in sorted(t.classes, key=lambda c: c.id)],
    }


@router.post("/auth/teachers")
def create_teacher(
    req: TeacherCreate,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """管理员建教师账号（口令 PBKDF2 落库；建号即进入安全模式）。"""
    _require_admin(db, authorization)
    if db.scalar(select(auth.Teacher).where(auth.Teacher.username == req.username)):
        raise HTTPException(400, f"用户名已存在: {req.username}")
    salt = secrets.token_bytes(16)
    t = auth.Teacher(
        school_id=req.school_id,
        name=req.name,
        username=req.username,
        salt=salt,
        password_hash=auth.hash_password(req.password, salt),
        admin=req.admin,
    )
    db.add(t)
    db.flush()
    return {"teacher_id": t.id}


@router.post("/auth/teachers/{teacher_id}/classes")
def grant_classes(
    teacher_id: int,
    req: GrantRequest,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """授予班级访问权（幂等；admin 全班可见无需授权行）。"""
    _require_admin(db, authorization)
    t = db.get(auth.Teacher, teacher_id)
    if t is None:
        raise HTTPException(404, "教师不存在")
    added = 0
    for cid in req.class_ids:
        if db.get(ClassModel, cid) is None:
            raise HTTPException(404, f"班级 {cid} 不存在")
        exists = db.scalar(
            select(auth.TeacherClass.id).where(
                auth.TeacherClass.teacher_id == teacher_id,
                auth.TeacherClass.class_id == cid,
            )
        )
        if exists is None:
            db.add(auth.TeacherClass(teacher_id=teacher_id, class_id=cid))
            added += 1
    db.flush()
    return {"teacher_id": teacher_id, "added": added}
