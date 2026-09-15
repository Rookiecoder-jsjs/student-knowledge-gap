"""G11 鉴权与教师↔班级归属校验（agent-product-design §5.5，Phase 3）。

设计要点：

- **凭据**：PBKDF2-SHA256；旧的 60k 原始摘要继续兼容，新建或成功登录的
  账号渐进升级为带迭代次数标记的更强摘要。token 为 HMAC 无状态签名
  （``teacher_id.expiry.sig``），密钥取 ``SC_AUTH_SECRET``。安全/HA 模式启动
  时必须显式配置稳定密钥，开放模式的本地库仍保留进程内随机回退。
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
- **RBAC 范围体系（rbac-scopes-design）**：学科管理员 ``teacher_subject_scope``
  （学科×年级）、班主任 ``class.homeroom_teacher_id``、科任收窄
  ``teacher_class.subject``。裁决函数：``subject_scopes`` / ``class_subject`` /
  ``assert_exam_access``（学科收窄）/ ``can_write_kb`` / ``can_govern_kb`` /
  ``can_enable_student``；班级层裁决仍只有 ``assert_class_access`` 一个实现。

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

from app.models import Class, Student, Teacher, TeacherClass, TeacherSubjectScope

PBKDF2_ITERS = 60_000  # legacy raw gateway-compatible hashes
CURRENT_PBKDF2_ITERS = 120_000
PASSWORD_HASH_PREFIX = b"pbkdf2-sha256"
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


def hash_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERS) -> bytes:
    """计算原始 PBKDF2 摘要，保留旧调用方与 gateway 文件兼容。"""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)


def hash_password_for_storage(
    password: str, salt: bytes, iterations: int = CURRENT_PBKDF2_ITERS
) -> bytes:
    """生成带算法和迭代次数标记的摘要，便于未来再次升级参数。"""
    digest = hash_password(password, salt, iterations).hex().encode("ascii")
    return PASSWORD_HASH_PREFIX + b"$" + str(iterations).encode("ascii") + b"$" + digest


def _burn_password_hash_budget(password: str, salt: bytes, iterations: int) -> None:
    """Spend the remaining PBKDF2 work needed to reach the current cost.

    Legacy rows contain a raw 60k digest and therefore need one real 60k
    verification. On a wrong password that would otherwise return faster than
    a missing-user dummy hash (120k), so perform the remaining work without
    using its result. This keeps migration-compatible verification from
    reintroducing username enumeration through timing.
    """
    remaining = CURRENT_PBKDF2_ITERS - iterations
    if remaining > 0:
        hash_password(password, salt, remaining)


def verify_password(password: str, salt: bytes, stored: bytes) -> tuple[bool, bool]:
    """校验密码并返回 ``(valid, needs_upgrade)``。

    历史 raw 60k 摘要没有元数据，按旧参数校验且成功后升级；标记格式
    自带迭代次数，坏格式直接拒绝而不降级为另一种解释。
    """
    iterations = PBKDF2_ITERS
    expected = stored
    needs_upgrade = True
    if stored.startswith(PASSWORD_HASH_PREFIX + b"$"):
        parts = stored.split(b"$", 2)
        if len(parts) != 3 or parts[0] != PASSWORD_HASH_PREFIX:
            _burn_password_hash_budget(password, salt, 0)
            return False, False
        try:
            iterations = int(parts[1])
            expected = bytes.fromhex(parts[2].decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            _burn_password_hash_budget(password, salt, 0)
            return False, False
        if iterations <= 0 or len(expected) != 32:
            _burn_password_hash_budget(password, salt, 0)
            return False, False
        needs_upgrade = iterations < CURRENT_PBKDF2_ITERS
    candidate = hash_password(password, salt, iterations)
    valid = hmac.compare_digest(candidate, expected)
    if not valid:
        _burn_password_hash_budget(password, salt, iterations)
    return valid, bool(valid and needs_upgrade)


def _verify_row_password(row, password: str) -> bool:  # noqa: ANN001
    stored = row.password_hash or b""
    salt = row.salt or b""
    valid, needs_upgrade = verify_password(password, salt, stored)
    if valid and needs_upgrade:
        row.salt = secrets.token_bytes(16)
        row.password_hash = hash_password_for_storage(password, row.salt)
    return valid


def _secret() -> str:
    global _secret_cache
    if _secret_cache is None:
        _secret_cache = os.environ.get("SC_AUTH_SECRET", "") or secrets.token_hex(32)
    return _secret_cache


def validate_runtime_secret(db: Session) -> None:
    """拒绝在安全或 HA 模式下用随机 token 密钥启动。

    ``_secret`` 的随机回退只服务于开放模式和底层单元调用；服务进程一旦
    进入安全模式，重启后随机密钥会让所有会话失效，HA 多副本还会互相
    无法验签。因此在 lifespan 启动阶段提前失败，而不是等到第一条请求。
    """
    forced = os.environ.get("SC_AUTH_REQUIRED", "").lower() in ("1", "true", "yes")
    ha = os.environ.get("SC_HA_ENABLED", "").lower() in ("1", "true", "yes")
    if (forced or ha or security_mode_on(db)) and not os.environ.get(
        "SC_AUTH_SECRET", ""
    ).strip():
        raise RuntimeError(
            "安全/HA 模式必须配置 SC_AUTH_SECRET。"
            "存量部署升级：在 backend/.env 设置稳定随机值后重建容器"
            "（旧行为为每次启动随机生成——重启即全员下线，故本版本起拒绝启动）。"
        )


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
        # 均衡哈希必须用当前迭代参数：账号成功登录即升级为 CURRENT 迭代，
        # 均衡侧若停在 legacy 60k，响应时间差会泄露「用户名是否存在/是否已升级」。
        hash_password(password, b"timing-equalizer", CURRENT_PBKDF2_ITERS)
        raise AuthError("用户名或密码错误")
    if not _verify_row_password(row, password):
        raise AuthError("用户名或密码错误")
    return row, issue_token(row.id)


def authenticate_student(db: Session, username: str, password: str) -> tuple[Student, str]:
    """学生口令登录（自服务门户）：成功返回 (学生, token)。"""
    row = db.scalar(select(Student).where(Student.username == username))
    if row is None:
        hash_password(password, b"timing-equalizer", CURRENT_PBKDF2_ITERS)
        raise AuthError("用户名或密码错误")
    if not _verify_row_password(row, password):
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
    stu.password_hash = hash_password_for_storage(password, salt)
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
        if _verify_row_password(trow, password):
            return trow, issue_token(trow.id), "t"
        raise AuthError("用户名或密码错误")
    srow = db.scalar(select(Student).where(Student.username == username))
    if srow is not None:
        if _verify_row_password(srow, password):
            return srow, issue_student_token(srow.id), "s"
        raise AuthError("用户名或密码错误")
    hash_password(password, b"timing-equalizer", CURRENT_PBKDF2_ITERS)
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
    """班级访问裁决：开放模式放行；安全模式要求 admin、班主任或 teacher_class 授权。

    班主任豁免（rbac-scopes-design §2）：homeroom_teacher_id 命中即放行——
    班主任自动获得本班全科视野，不需要授权行。
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
    if clazz.homeroom_teacher_id == ctx.teacher.id:
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
    """考试访问裁决（rbac-scopes-design §4）：班级可见性 + 学科收窄。

    班级层（是否在授权班内）由 assert_class_access 承担；在其之上叠加学科维度：
    授权行带 subject 且与考试学科（exam.subject ?? 班级默认）不符 → 拒绝。
    班主任全科豁免；admin / 匿名（开放模式）不收窄。
    """
    clazz = assert_class_access(db, ctx, exam_template.class_id)
    if ctx.teacher is None or ctx.is_admin:
        return
    if clazz.homeroom_teacher_id == ctx.teacher.id:
        return
    exam_subject = exam_template.subject or clazz.subject
    bound = db.scalars(
        select(TeacherClass.subject).where(
            TeacherClass.teacher_id == ctx.teacher.id,
            TeacherClass.class_id == clazz.id,
            TeacherClass.subject.is_not(None),
        )
    ).all()
    if bound and exam_subject not in bound:
        raise PermissionError_(
            f"教师 {ctx.label} 对班级 {clazz.name} 仅有 "
            f"{'、'.join(bound)} 学科视野，该考试学科为 {exam_subject}"
        )


def allowed_class_ids(db: Session, ctx: AccessContext) -> list[int] | None:
    """可见班级 id 集合；None = 不限制（开放模式/admin）。列表端点过滤用。

    授权行 ∪ 班主任班级（rbac-scopes-design §2）——班主任未持授权行也必须在
    列表（/classes 等）里看到自己的班。
    """
    if ctx.teacher is None or ctx.is_admin:
        return None
    granted = set(
        db.scalars(
            select(TeacherClass.class_id).where(
                TeacherClass.teacher_id == ctx.teacher.id
            )
        )
    )
    granted.update(
        db.scalars(
            select(Class.id).where(Class.homeroom_teacher_id == ctx.teacher.id)
        )
    )
    return sorted(granted)


# ---------------------------------------------------------------------------
# RBAC 范围体系（rbac-scopes-design §4）：学科管理员 / 班主任 / 科任收窄
# ---------------------------------------------------------------------------


def subject_scopes(db: Session, ctx: AccessContext) -> list[tuple[str, int]] | None:
    """学科管理员授权范围；None = 不受限（admin / 开放模式匿名）。

    空列表 = 教师无任何学科授权（普通教师语义，不等于受限）。
    """
    if ctx.teacher is None or ctx.is_admin:
        return None
    rows = db.execute(
        select(TeacherSubjectScope.subject, TeacherSubjectScope.grade).where(
            TeacherSubjectScope.teacher_id == ctx.teacher.id
        )
    ).all()
    return [(r[0], r[1]) for r in rows]


def homeroom_class_ids(db: Session, teacher_id: int) -> list[int]:
    """教师担任班主任的班级 id 集（/auth/me 派生 + 前端旗标用）。"""
    return list(
        db.scalars(select(Class.id).where(Class.homeroom_teacher_id == teacher_id))
    )


def is_homeroom(db: Session, ctx: AccessContext, class_id: int) -> bool:
    """是否该班班主任（admin 不算——admin 有更宽通道，不冒充班主任语义）。"""
    if ctx.teacher is None:
        return False
    return bool(
        db.scalar(
            select(Class.id).where(
                Class.id == class_id, Class.homeroom_teacher_id == ctx.teacher.id
            )
        )
    )


def class_subject(db: Session, ctx: AccessContext, clazz: Class) -> str | None:
    """班级语境的知识库学科（rbac-scopes-design §4）。

    科任学科绑定行覆盖班级默认学科（收窄后的视野口径）；班主任/全科授权/admin
    → 班级默认（clazz.subject）。考试语境不走本函数——一律 exam.subject ?? clazz.subject。
    """
    if ctx.teacher is not None and not ctx.is_admin:
        bound = db.scalar(
            select(TeacherClass.subject).where(
                TeacherClass.teacher_id == ctx.teacher.id,
                TeacherClass.class_id == clazz.id,
                TeacherClass.subject.is_not(None),
            )
        )
        if bound:
            return bound
    return clazz.subject


def _scope_covers(
    db: Session, ctx: AccessContext, subject: str | None, grade: int | None
) -> bool:
    """范围匹配：任一授权行覆盖 (subject, grade)。

    grade 为 None（未标年级版本/存量）→ 按学科匹配（未标年级视为全年级共有）。
    """
    if subject is None:
        return False
    scopes = subject_scopes(db, ctx)
    if not scopes:
        return False
    if grade is None:
        return any(s == subject for s, _g in scopes)
    return (subject, grade) in scopes


def can_read_kb(
    db: Session, ctx: AccessContext, subject: str | None, grade: int | None = None
) -> bool:
    """知识库读取范围裁决。

    普通教师沿用既有语义（可读全校知识库）；学科范围教师按学科×年级
    收窄。未标年级版本视为该学科的共享版本，与写权限的匹配规则一致。
    """
    scopes = subject_scopes(db, ctx)
    if scopes is None or not scopes:
        return True
    if subject is None:
        return False
    if grade is None:
        return any(s == subject for s, _g in scopes)
    return any(s == subject and (g == grade or g is None) for s, g in scopes)


def can_write_kb(
    db: Session, ctx: AccessContext, subject: str | None, grade: int | None = None
) -> bool:
    """KB 内容写权（rbac-scopes-design §4）：admin ∨ kb_editor ∨ 范围授权。

    开放模式匿名放行（bootstrap 导库依赖，与 require_kb_editor 语义一致）。
    """
    if ctx.teacher is None:
        return not security_mode_on(db)
    if ctx.is_admin or ctx.teacher.kb_editor:
        return True
    return _scope_covers(db, ctx, subject, grade)


def can_govern_kb(
    db: Session, ctx: AccessContext, subject: str | None, grade: int | None = None
) -> bool:
    """KB 治理权（启用/停用版本 = 全校口径切换）：admin ∨ 范围授权。

    治理权下放学科管理员——教研组长决定本学科启用哪版（rbac-scopes-design §2）。
    """
    if ctx.teacher is None:
        return not security_mode_on(db)
    if ctx.is_admin:
        return True
    return _scope_covers(db, ctx, subject, grade)


def can_enable_student(db: Session, ctx: AccessContext, student: Student) -> bool:
    """学生账号开通/重置权：admin ∨ 本班班主任（rbac-scopes-design §8）。"""
    if ctx.teacher is None:
        return not security_mode_on(db)
    if ctx.is_admin:
        return True
    clazz = db.get(Class, student.class_id)
    return bool(clazz and clazz.homeroom_teacher_id == ctx.teacher.id)
