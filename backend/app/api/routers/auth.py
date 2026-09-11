"""鉴权路由（G11 + auth-roles-design 三角色）：登录 / 建账号 / 班级授权 / 学生账号。

- ``POST /auth/login``：口令换 token（教师或学生；白名单端点，安全模式下也匿名可达）；
- ``GET /auth/me``：会话恢复，返回 role 与主体现形状（前端按角色路由）；
- 账号与授权管理（``POST /auth/teachers`` 等）仅 admin 可用——首个 admin 由
  bootstrap 脚本/命令行创建（scripts/create_teacher.py），避免鸡生蛋；
- 学生自服务账号由 admin 开通（``POST /auth/students/{id}/enable``）；
- 本 router 只做 HTTP 翻译；裁决逻辑在 app.auth。
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.api.deps import access_ctx, get_db, require_admin
from app.models import Class as ClassModel
from app.models import Student, Teacher

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
    kb_editor: bool = False


class StudentEnableRequest(BaseModel):
    password: str = Field(min_length=6, max_length=128)
    username: str | None = Field(default=None, min_length=2, max_length=64)


class GrantRequest(BaseModel):
    class_ids: list[int]


class KbEditorRequest(BaseModel):
    kb_editor: bool


def _unique_username(db: Session, username: str) -> bool:
    """username 跨 teacher/student 两表唯一（同名登录会歧义，先建先占）。"""
    if db.scalar(select(Teacher.id).where(Teacher.username == username)):
        return False
    if db.scalar(select(Student.id).where(Student.username == username)):
        return False
    return True


def _teacher_payload(ctx: auth.AccessContext) -> dict:
    t = ctx.teacher
    classes = [
        {"class_id": c.id, "name": c.name}
        for c in sorted(t.classes, key=lambda c: c.id)
    ]
    return {
        "role": "admin" if t.admin else "teacher",
        "teacher": {"id": t.id, "name": t.name, "admin": t.admin, "kb_editor": t.kb_editor},
        "classes": classes,
    }


def _student_payload(ctx: auth.AccessContext) -> dict:
    s = ctx.student
    return {
        "role": "student",
        "student": {
            "id": s.id,
            "name_or_alias": s.name_or_alias,
            "class_id": s.class_id,
            "class_name": s.clazz.name if s.clazz is not None else None,
        },
    }


@router.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """口令登录 → Bearer token（教师/admin 或学生；开放模式同样可用）。"""
    try:
        principal, token, kind = auth.authenticate_any(db, req.username, req.password)
    except auth.AuthError as e:
        raise HTTPException(401, str(e)) from e
    ctx = (
        auth.AccessContext(teacher=principal)
        if kind == "t"
        else auth.AccessContext(student=principal)
    )
    body = _teacher_payload(ctx) if kind == "t" else _student_payload(ctx)
    body["token"] = token
    return body


@router.get("/auth/me")
def me(
    ctx=Depends(access_ctx),
):
    """当前身份（前端会话恢复用；匿名返回 authenticated:false）。"""
    if ctx.principal is None:
        return {"authenticated": False}
    body = (
        _teacher_payload(ctx)
        if ctx.teacher is not None
        else _student_payload(ctx)
    )
    body["authenticated"] = True
    return body


@router.post("/auth/teachers")
def create_teacher(
    req: TeacherCreate,
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员建教师账号（口令 PBKDF2 落库；建号即进入安全模式）。"""
    if not _unique_username(db, req.username):
        raise HTTPException(400, f"用户名已存在: {req.username}")
    salt = secrets.token_bytes(16)
    t = Teacher(
        school_id=req.school_id,
        name=req.name,
        username=req.username,
        salt=salt,
        password_hash=auth.hash_password(req.password, salt),
        admin=req.admin,
        kb_editor=req.kb_editor,
    )
    db.add(t)
    db.flush()
    return {"teacher_id": t.id}


@router.get("/auth/teachers")
def list_teachers(
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员查教师账号（frontend-ends-design §C 账号管理列表面）。

    返回 name/username/admin/已授班级——班级授权行内编辑的数据源。
    """
    rows = db.scalars(select(Teacher).order_by(Teacher.id))
    return {
        "teachers": [
            {
                "teacher_id": t.id,
                "name": t.name,
                "username": t.username,
                "admin": bool(t.admin),
                "kb_editor": bool(t.kb_editor),
                "classes": [
                    {"class_id": c.id, "name": c.name}
                    for c in sorted(t.classes, key=lambda c: c.id)
                ],
            }
            for t in rows
        ]
    }


@router.post("/auth/students/{student_id}/enable")
def enable_student(
    student_id: int,
    req: StudentEnableRequest,
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """管理员开通/重置学生自服务账号（username 缺省 = external_code 学籍号）。"""
    try:
        _stu, username = auth.enable_student_login(
            db, student_id, req.password, req.username
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"student_id": student_id, "username": username}


@router.post("/auth/teachers/{teacher_id}/classes")
def grant_classes(
    teacher_id: int,
    req: GrantRequest,
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """授予班级访问权（幂等；admin 全班可见无需授权行）。"""
    t = db.get(Teacher, teacher_id)
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


@router.post("/auth/teachers/{teacher_id}/kb-editor")
def set_teacher_kb_editor(
    teacher_id: int,
    req: KbEditorRequest,
    admin_ctx=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """授予/撤销知识库编辑权（两层写权的授权面）：内容层写入资格，版本治理仍 admin。

    角色从 DB 现读（token 无状态），已发 token 立即生效/失效。
    """
    t = db.get(Teacher, teacher_id)
    if t is None:
        raise HTTPException(404, "教师不存在")
    t.kb_editor = req.kb_editor
    db.flush()
    return {"teacher_id": teacher_id, "kb_editor": bool(t.kb_editor)}
