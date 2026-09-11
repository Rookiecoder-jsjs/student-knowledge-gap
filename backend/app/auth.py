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
- **三角色（auth-roles-design）**：身份双承载——Teacher（`admin` 布尔=管理员，
  纯校级与授课兼管同形）与 Student（增凭据列）向上统一为 ``AccessContext`` 的
  principal；token 携带身份种类 kind（t/s，admin 权限每次从 DB 现读）；学生自服务
  走 ``/me`` 只读面（main.py 中间件前缀白名单隔离），MCP/gateway 仍教师专用。

本层不感知 HTTP 异常——抛 AuthError/PermissionError，由 deps 翻译。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Class, Student, Teacher, TeacherClass

PBKDF2_ITERS = 60_000
TOKEN_TTL_S = 7 * 24 * 3600  # 一周（教师口令登录，长会话合理）

# HTTP /mcp 通道的逐请求教师身份（backend/app/mcp_http.py 中间件验签后写入）。
# 工具在任意线程/任务里经 auth.mcp_context 读取；默认 None = 匿名/走 env 兜底。
_mcp_teacher_id: ContextVar[int | None] = ContextVar("mcp_teacher_id", default=None)


def set_mcp_teacher_id(teacher_id: int | None) -> None:
    """把已验证的教师 id 写进当前请求上下文（无 → 匿名）。"""
    _mcp_teacher_id.set(teacher_id)


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


def _sign(kind: str, uid: int, ttl_s: int) -> str:
    exp = int(time.time()) + ttl_s
    body = f"{kind}.{uid}.{exp}"
    sig = hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def issue_token(teacher_id: int, ttl_s: int = TOKEN_TTL_S) -> str:
    """教师/admin token（签名与历史调用方一致）。"""
    return _sign("t", teacher_id, ttl_s)


def issue_student_token(student_id: int, ttl_s: int = TOKEN_TTL_S) -> str:
    return _sign("s", student_id, ttl_s)


def _parse_token(token: str) -> tuple[str, int]:
    """解析并验签，返回 (kind, uid)；无效抛 AuthError。

    新格式 ``{kind}.{id}.{exp}.{sig}``（kind ∈ t/s；admin 角色每次从 DB 现读，
    不信任 token 内角色）；兼容旧三段 ``{id}.{exp}.{sig}``（视为 t）——避免 dev
    localStorage 残留 token 与 gateway 过渡期现签 token 直接失效。
    """
    parts = token.split(".")
    if len(parts) == 4:
        kind, uid_raw, exp_raw, sig = parts
        if kind not in ("t", "s"):
            raise AuthError("token 格式非法")
        body = f"{kind}.{uid_raw}.{exp_raw}"
    elif len(parts) == 3:
        kind, uid_raw, exp_raw = "t", parts[0], parts[1]
        body = f"{parts[0]}.{parts[1]}"
        sig = parts[2]
    else:
        raise AuthError("token 格式非法")
    expect = hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        raise AuthError("token 无效")
    try:
        uid, exp = int(uid_raw), int(exp_raw)
    except ValueError as e:
        raise AuthError("token 格式非法") from e
    if exp < time.time():
        raise AuthError("token 已过期")
    return kind, uid


def verify_token(token: str) -> int:
    """教师专用解析：student token 在此层拒绝（MCP/教师专用路径）。返回 teacher_id。"""
    kind, uid = _parse_token(token)
    if kind != "t":
        raise AuthError("非教师 token")
    return uid


def verify_principal_token(token: str) -> tuple[str, int]:
    """HTTP 通用解析：返回 (kind, uid)，供 access_ctx 解析教师或学生。"""
    return _parse_token(token)


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


def authenticate_student(db: Session, username: str, password: str) -> tuple[Student, str]:
    """学生口令登录（自服务门户）：成功返回 (学生, token)。"""
    row = db.scalar(select(Student).where(Student.username == username))
    if row is None:
        hash_password(password, b"timing-equalizer")
        raise AuthError("用户名或密码错误")
    stored = row.password_hash or b""
    salt = row.salt or b""
    if not stored or not hmac.compare_digest(hash_password(password, salt), stored):
        raise AuthError("用户名或密码错误")
    return row, issue_student_token(row.id)


def enable_student_login(
    db: Session, student_id: int, password: str, username: str | None = None
) -> tuple[Student, str]:
    """开通/重置学生账号口令（admin 路由与 bootstrap CLI 共用）。

    用户名缺省 = external_code（学籍号）；跨 teacher/student 两表唯一。返回
    (student, 生效 username)；冲突/缺名抛 ValueError，由 HTTP 层译 400。
    """
    stu = db.get(Student, student_id)
    if stu is None:
        raise ValueError(f"学生 {student_id} 不存在")
    uname = (username or "").strip() or (stu.external_code or "").strip()
    if not uname:
        raise ValueError("学生无学籍号（external_code），请显式指定 username")
    if (stu.username or "") != uname:
        if db.scalar(select(Teacher.id).where(Teacher.username == uname)):
            raise ValueError(f"用户名已被占用: {uname}")
        if db.scalar(select(Student.id).where(Student.username == uname)):
            raise ValueError(f"用户名已被占用: {uname}")
    salt = secrets.token_bytes(16)
    stu.username = uname
    stu.salt = salt
    stu.password_hash = hash_password(password, salt)
    return stu, uname


def authenticate_any(
    db: Session, username: str, password: str
) -> tuple[Teacher | Student, str, str]:
    """统一登录：教师优先、无则学生。返回 (principal, token, kind)。

    同名不跨表（Teacher.username 与 Student.username 各自治）；若配置撞名，
    /auth/login 教师优先——admin 建号时应用层预检兜底。
    """
    trow = db.scalar(select(Teacher).where(Teacher.username == username))
    if trow is not None:
        stored = trow.password_hash or b""
        salt = trow.salt or b""
        if stored and hmac.compare_digest(hash_password(password, salt), stored):
            return trow, issue_token(trow.id), "t"
        raise AuthError("用户名或密码错误")
    srow = db.scalar(select(Student).where(Student.username == username))
    if srow is not None:
        stored = srow.password_hash or b""
        salt = srow.salt or b""
        if stored and hmac.compare_digest(hash_password(password, salt), stored):
            return srow, issue_student_token(srow.id), "s"
        raise AuthError("用户名或密码错误")
    hash_password(password, b"timing-equalizer")
    raise AuthError("用户名或密码错误")


def current_teacher(db: Session, authorization: str | None) -> Teacher | None:
    """Bearer token → 教师实体（教师专用路径：MCP / require_teacher 兜底）。

    无 Authorization 头返回 None（开放模式匿名放行；安全模式下 require_teacher
    会拒绝）。带了头但无效抛 AuthError——「给了凭据但凭据坏」必须显式失败，
    不能静默降级为匿名。student token 在此层同样抛 AuthError（非教师）。
    """
    raw = (authorization or "").removeprefix("Bearer ").strip()
    if not raw:
        return None
    tid = verify_token(raw)
    t = db.get(Teacher, tid)
    if t is None:
        raise AuthError("token 对应的教师不存在")
    return t


def resolve_principal(db: Session, kind: str, uid: int) -> Teacher | Student | None:
    return db.get(Teacher, uid) if kind == "t" else db.get(Student, uid)


def current_principal(db: Session, authorization: str | None) -> Teacher | Student | None:
    """Bearer token → 教师或学生实体（HTTP 通用，三角色登录）。

    无 Authorization 头返回 None；带了头但 token 无效/主体不存在抛 AuthError。
    """
    raw = (authorization or "").removeprefix("Bearer ").strip()
    if not raw:
        return None
    kind, uid = verify_principal_token(raw)
    p = resolve_principal(db, kind, uid)
    if p is None:
        raise AuthError(f"token 对应的{'教师' if kind == 't' else '学生'}不存在")
    return p


# ---------------------------------------------------------------------------
# 模式判定
# ---------------------------------------------------------------------------

_mode_cache: bool | None = None


def security_mode_on(db: Session) -> bool:
    """安全模式 = SC_AUTH_REQUIRED=1 或库里存在任一带凭据教师。

    学生账号**不计入**——试点可先开学生门户、后建教师账号；SC_AUTH_REQUIRED=1
    仍强制全闸。结果缓存（每进程一次）；管理员建首个账号后需重启生效是可接受的
    运维语义。
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
    """一次调用的身份上下文。teacher/student 皆 None = 开放模式匿名（MCP 服务身份同理）。

    角色从 DB 实体现读，不信任 token：teacher+admin → admin；teacher → teacher；
    student → student。归属裁决只认 ctx.teacher（教师↔班级）；学生由中间件路由
    白名单 + 各自 /me self 断言隔离，不进入教师裁决路径。
    """

    teacher: Teacher | None = None
    student: Student | None = None

    @property
    def is_admin(self) -> bool:
        return bool(self.teacher and self.teacher.admin)

    @property
    def role(self) -> str:
        if self.teacher is not None:
            return "admin" if self.teacher.admin else "teacher"
        if self.student is not None:
            return "student"
        return "anonymous"

    @property
    def principal(self) -> Teacher | Student | None:
        return self.teacher if self.teacher is not None else self.student

    @property
    def label(self) -> str:
        if self.teacher is not None:
            return self.teacher.name
        if self.student is not None:
            return self.student.name_or_alias
        return "anonymous"


def mcp_context(db: Session) -> AccessContext:
    """当前 MCP 调用的教师身份上下文。

    优先级：HTTP /mcp 通道的**逐请求**教师 token（backend/app/mcp_http.py 中间件
    验签后经 ``set_mcp_teacher_id`` 写入 contextvar，装车批第 5 批起为生产路径）
    > stdio/本地兜底 env ``SC_MCP_TEACHER_ID``（本地 dev/旧测试）。两者皆无 =
    匿名（开放模式放行、安全模式由断言拒绝）。同一进程并发服务多教师，身份
    只能逐请求携带，进程级 env 注入已死。
    """
    teacher_id = _mcp_teacher_id.get()
    if teacher_id is None:
        env_raw = os.environ.get("SC_MCP_TEACHER_ID", "").strip()
        if env_raw.isdigit():
            teacher_id = int(env_raw)
    if teacher_id is None:
        return AccessContext(teacher=None)
    return AccessContext(teacher=db.get(Teacher, teacher_id))


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
