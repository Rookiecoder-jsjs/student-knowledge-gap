"""G11 鉴权与教师↔班级归属校验（agent-product-design §5.5，Phase 3）。

设计要点：

- **凭据**：PBKDF2-SHA256（60k 迭代，与 gateway 账号文件同算法同参数）；
  token 为 HMAC 无状态签名（``teacher_id.expiry.sig``），密钥取
  ``SC_AUTH_SECRET``（安全模式下未配置则每次启动随机生成——重启全员下线，
  试点期可接受；生产部署必须显式配）。
- **双模式**：库中不存在任何带凭据的教师 = **开放模式**（bootstrap 兼容，
  存量测试/演示零改动）；存在任一带凭据教师或 ``SC_AUTH_REQUIRED=1`` =
  **安全模式**，全部业务端点要求 Bearer token。模式按请求惰性探测并缓存。
- **授权断言**：``assert_class_access`` 是唯一裁决点——开放模式放行；安全
  模式查 teacher_class 授权表（admin 全班放行）。HTTP 路由（FastAPI 依赖）
  与 MCP 工具层（显式调用，身份来自网关注入的 SC_MCP_TEACHER_ID）共用，
  「教师甲看不到教师乙的班」出口判据只实现一次。
- 学生/考试等子资源的归属一律先解析到 class_id 再走同一断言，不另设路径。

本层不感知 HTTP 异常——抛 AuthError/PermissionError，由 deps 翻译。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Class, Student, Teacher, TeacherClass

PBKDF2_ITERS = 60_000
TOKEN_TTL_S = 7 * 24 * 3600  # 一周（教师口令登录，长会话合理）

_secret_cache: str | None = None


class AuthError(Exception):
    """未认证（HTTP 层翻译为 401）。"""


class PermissionError_(Exception):
    """已认证但无权访问该资源（HTTP 层翻译为 403）。"""


# ---------------------------------------------------------------------------
# 凭据与 token
# ---------------------------------------------------------------------------


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)


def _secret() -> str:
    global _secret_cache
    if _secret_cache is None:
        _secret_cache = os.environ.get("SC_AUTH_SECRET", "") or secrets.token_hex(32)
    return _secret_cache


def issue_token(teacher_id: int, ttl_s: int = TOKEN_TTL_S) -> str:
    exp = int(time.time()) + ttl_s
    body = f"{teacher_id}.{exp}"
    sig = hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token: str) -> int:
    """校验签名与有效期，返回 teacher_id；无效抛 AuthError。"""
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("token 格式非法")
    body = f"{parts[0]}.{parts[1]}"
    expect = hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, parts[2]):
        raise AuthError("token 无效")
    try:
        teacher_id, exp = int(parts[0]), int(parts[1])
    except ValueError as e:
        raise AuthError("token 格式非法") from e
    if exp < time.time():
        raise AuthError("token 已过期")
    return teacher_id


def authenticate(db: Session, username: str, password: str) -> tuple[Teacher, str]:
    """口令登录：成功返回 (教师, token)。用户名不存在也走一次哈希比较防时序侧信道。"""
    row = db.scalar(select(Teacher).where(Teacher.username == username))
    if row is None:
        hash_password(password, b"timing-equalizer")
        raise AuthError("用户名或密码错误")
    stored = row.password_hash or b""
    salt = row.salt or b""
    if not stored or not hmac.compare_digest(hash_password(password, salt), stored):
        raise AuthError("用户名或密码错误")
    return row, issue_token(row.id)


def current_teacher(db: Session, authorization: str | None) -> Teacher | None:
    """Bearer token → 教师实体。

    无 Authorization 头返回 None（开放模式匿名放行；安全模式下 require_teacher
    会拒绝）。带了头但无效则抛 AuthError——「给了凭据但凭据坏」必须显式失败，
    不能静默降级为匿名。
    """
    raw = (authorization or "").removeprefix("Bearer ").strip()
    if not raw:
        return None
    tid = verify_token(raw)
    t = db.get(Teacher, tid)
    if t is None:
        raise AuthError("token 对应的教师不存在")
    return t


# ---------------------------------------------------------------------------
# 模式判定
# ---------------------------------------------------------------------------

_mode_cache: bool | None = None


def security_mode_on(db: Session) -> bool:
    """安全模式 = SC_AUTH_REQUIRED=1 或库里存在任一带凭据教师。

    结果缓存（每进程一次）；管理员建首个账号后需重启生效是可接受的运维语义。
    """
    global _mode_cache
    if _mode_cache is None:
        forced = os.environ.get("SC_AUTH_REQUIRED", "").lower() in ("1", "true", "yes")
        has_cred = (
            db.scalar(
                select(func.count(Teacher.id)).where(Teacher.username.is_not(None))
            )
            or 0
        ) > 0
        _mode_cache = bool(forced or has_cred)
    return _mode_cache


def reset_mode_cache_for_tests() -> None:
    global _mode_cache, _secret_cache
    _mode_cache = None
    _secret_cache = None


# ---------------------------------------------------------------------------
# 授权断言（HTTP 与 MCP 共用的唯一裁决点）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessContext:
    """一次调用的身份上下文。teacher=None = 开放模式匿名（MCP 服务身份同理）。"""

    teacher: Teacher | None

    @property
    def is_admin(self) -> bool:
        return bool(self.teacher and self.teacher.admin)

    @property
    def label(self) -> str:
        return self.teacher.name if self.teacher else "anonymous"


def mcp_context_from_env(db: Session) -> AccessContext:
    """MCP 身份传播兜底路线（§5.5）：网关为每教师进程注入 SC_MCP_TEACHER_ID。

    未注入/教师不存在 → 匿名上下文（开放模式放行、安全模式由断言拒绝）。
    """
    raw = os.environ.get("SC_MCP_TEACHER_ID", "").strip()
    if not raw.isdigit():
        return AccessContext(teacher=None)
    return AccessContext(teacher=db.get(Teacher, int(raw)))


def assert_class_access(db: Session, ctx: AccessContext, class_id: int) -> Class:
    """班级访问裁决：开放模式放行；安全模式要求 admin 或 teacher_class 授权。

    匿名上下文（teacher=None）在安全模式下拒绝——覆盖「MCP 进程未注入身份」
    的兜底缺口：裁决只有一个实现，HTTP 与 MCP 不可能各对匿名语义有不同解释。
    """
    clazz = db.get(Class, class_id)
    if clazz is None:
        raise LookupError(f"班级 {class_id} 不存在")
    if ctx.teacher is None:
        if security_mode_on(db):
            raise PermissionError_("匿名身份无班级访问权限（安全模式）")
        return clazz
    if ctx.is_admin:
        return clazz
    granted = db.scalar(
        select(func.count(TeacherClass.id)).where(
            TeacherClass.teacher_id == ctx.teacher.id,
            TeacherClass.class_id == class_id,
        )
    )
    if not granted:
        raise PermissionError_(
            f"教师 {ctx.label} 无班级 {clazz.name} 的访问权限"
        )
    return clazz


def assert_student_access(db: Session, ctx: AccessContext, student_id: int) -> Student:
    stu = db.get(Student, student_id)
    if stu is None:
        raise LookupError(f"学生 {student_id} 不存在")
    assert_class_access(db, ctx, stu.class_id)
    return stu


def assert_exam_access(db: Session, ctx: AccessContext, exam_template) -> None:
    assert_class_access(db, ctx, exam_template.class_id)


def allowed_class_ids(db: Session, ctx: AccessContext) -> list[int] | None:
    """可见班级 id 集合；None = 不限制（开放模式/admin）。列表端点过滤用。"""
    if ctx.teacher is None or ctx.is_admin:
        return None
    rows = db.scalars(
        select(TeacherClass.class_id).where(TeacherClass.teacher_id == ctx.teacher.id)
    )
    return list(rows)
