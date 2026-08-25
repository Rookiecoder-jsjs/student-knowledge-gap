"""sc 领域能力的 MCP Server 入口（Agent 产品化 §5.1）。

独立进程，stdio 传输；壳（codex 运行时）作为 MCP client 经此调用 sc 的
确定性管线。原则：**业务代码零改动**——本文件只做「领域能力 → MCP 工具」的
薄包装，聚合逻辑在 ``app.mcp_tools``（与 HTTP 路由/pytest 共用），不复制实现。

工具约束（§5.1）：
- 只读（写操作工具属 Phase 3 且必须过审批门）；
- 返回不含学生真名（name_or_alias 原则，§9 prompt 最小化）；
- 每个响应携带 ``_provenance``（来源端点 + 参数），供前端「依据」链接回溯；
- 大结果集分页是硬约束：get_kp_mastery 弱项优先截断、list_students 分页。

用法（codex 配置示例，见仓库 handbook/ 或 DEPLOY 文档后续补充）：
    [mcp_servers.sc]
    command = "<venv>/bin/python"
    args = ["/abs/path/to/backend/app/mcp_server.py"]

数据库定位：未设置 SC_DATABASE_URL 时回落到 backend/sc.db（绝对路径），
使本进程可从任意工作目录启动；生产部署由 compose 显式注入环境变量。
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

# --- 引导：允许以脚本路径直接启动（cwd 无关）-------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

os.environ.setdefault("SC_DATABASE_URL", f"sqlite:///{_BACKEND_DIR / 'sc.db'}")

import mcp.server.fastmcp as _fastmcp  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from pydantic import Field  # noqa: E402

from app.mcp_tools import (  # noqa: E402
    ToolInputError,
    create_report_draft as _create_report_draft,
    get_exam_summary as _get_exam_summary,
    get_kp_detail as _get_kp_detail,
    get_kp_mastery as _get_kp_mastery,
    get_teaching_progress as _get_teaching_progress,
    list_students as _list_students,
    record_intervention as _record_intervention,
    resolve_graph,
    run_attribution as _run_attribution,
)
from app.queries.classes import progress_list  # noqa: E402

_READONLY = {"readOnlyHint": True, "destructiveHint": False}
# 写操作刻意**不带** readOnlyHint（FINDINGS F2：该注解决定壳的免审批放行）；
# 产出本身是 draft/suggested 态，教师签发/确认才是终审（§5.3）。
_WRITES = {"readOnlyHint": False, "destructiveHint": False}

mcp = FastMCP("sc")


def _provenance(endpoint: str, params: dict | None = None) -> dict:
    """响应溯源块：来源端点 + 参数快照 + 生成时刻。"""
    return {
        "source": "sc",
        "endpoint": endpoint,
        "params": params or {},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _run(fn, endpoint: str, params: dict | None = None) -> dict:
    """统一包装：开短会话 → 执行纯函数 → 追加 _provenance。

    LookupError/ToolInputError 翻译为 ValueError——FastMCP 会把异常 message
    回给模型（isError 载荷），模型可据此自行纠正参数。
    """
    from app.db import get_session

    try:
        with get_session() as session:
            data = fn(session)
    except (LookupError, ToolInputError) as e:
        raise ValueError(str(e)) from e
    data["_provenance"] = _provenance(endpoint, params)
    return data


def _opt_date(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError as e:
        raise ValueError(f"日期格式应为 YYYY-MM-DD：{v!r}") from e


def _check_class_students(session, class_id: int, student_ids: list[int]) -> None:
    """越界校验（§5.1：school/class 越界一律拒绝）：学生必须都属于该班。"""
    from sqlalchemy import select

    from app.models import Student

    if not student_ids:
        return
    owned = set(
        session.scalars(
            select(Student.id).where(
                Student.class_id == class_id, Student.id.in_(student_ids)
            )
        )
    )
    bad = [sid for sid in student_ids if sid not in owned]
    if bad:
        raise ToolInputError(f"学生 {bad} 不属于班级 {class_id}")


def _guard_class(session, class_id: int) -> None:
    """MCP 身份传播兜底路线（§5.5）：网关按教师注入 SC_MCP_TEACHER_ID。

    裁决走 app.auth.assert_class_access（与 HTTP 同一实现）；拒绝翻译为
    ToolInputError——模型可读、不重试同参。
    """
    from app import auth as _auth

    ctx = _auth.mcp_context_from_env(session)
    try:
        _auth.assert_class_access(session, ctx, class_id)
    except _auth.PermissionError_ as e:
        raise ToolInputError(f"无权访问该班级：{e}") from e


def _filter_classes_to_allowed(session, classes: list[dict]) -> list[dict]:
    """get_class_overview 的授权过滤：教师身份在场且非 admin 时收敛列表。"""
    from app import auth as _auth

    allowed = _auth.allowed_class_ids(session, _auth.mcp_context_from_env(session))
    if allowed is None:
        return classes
    want = set(allowed)
    return [c for c in classes if c.get("class_id") in want]


# ---------------------------------------------------------------------------
# 工具注册（§5.1 一期清单七个只读工具）
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
def get_class_overview() -> dict:
    """获取所有班级的轻量概览。

    每班返回：学生数、考试数、待办考试数、最近一场考试状态（已提交/待审核人数）、
    教学进度覆盖（已教知识点数 / 分析同分母总知识点数）。班级级统计，不含任何
    学生个人信息。适用于「现在各个班的情况怎么样」「哪个班有待办」类问题。
    """
    from app.db import get_session
    from app.kb.graph import KpGraph
    from app.kb.resolver import KbNotActiveError, active_kb
    from app.queries.classes_overview import classes_overview

    with get_session() as db:
        try:
            kb = active_kb(db)
        except KbNotActiveError:
            kb = None
        grade7_set = set(KpGraph(db, kb.id).grade7_kp_ids()) if kb is not None else set()
        data = classes_overview(db, grade7_set)

    data["classes"] = _filter_classes_to_allowed(db, data.get("classes", []))
    data["_provenance"] = _provenance("GET /classes/overview")
    return data


@mcp.tool(annotations=_READONLY)
def get_exam_summary(
    class_id: Annotated[int, Field(ge=1, description="班级 id")],
    exam_id: Annotated[
        int | None, Field(ge=1, description="考试 id；缺省取该班最近一场")
    ] = None,
) -> dict:
    """获取一场考试的班级质量事实：提交/待审人数、均分最高最低分、逐题得分率（低得分率题标出）、共性薄弱知识点（按全班弱项占比排序）。

    回答「这场考试考得怎么样」「哪些题错得多」「这次考试暴露了什么薄弱点」的主要数据源。exam_id 缺省时自动取该班最近一场考试。
    """
    def op(session):
        _guard_class(session, class_id)
        graph = resolve_graph(session)
        return _get_exam_summary(session, graph, class_id, exam_id)

    return _run(op, "GET /classes/{id}/quality-report", {"class_id": class_id, "exam_id": exam_id})


@mcp.tool(annotations=_READONLY)
def get_kp_mastery(
    class_id: Annotated[int, Field(ge=1, description="班级 id")],
    student_ids: Annotated[
        list[int], Field(min_length=1, max_length=50, description="学生 id 列表（须属于该班）")
    ],
    kp_codes: Annotated[
        list[str] | None,
        Field(description="知识点编码列表（如 [\"P1\"]）；缺省=该班教过的全部知识点"),
    ] = None,
    as_of: Annotated[str | None, Field(description="截止日期 YYYY-MM-DD；缺省今天")] = None,
) -> dict:
    """查询学生对知识点的掌握度矩阵（薄弱优先）。

    返回弱项全列（低于掌握度底线）+ 掌握点样本 + 截断标记。适用于「这几个孩子哪些点没掌握」「某某的掌握情况」类问题。大结果集自动截断：先看弱项，追问再缩小范围。
    """
    def op(session):
        _guard_class(session, class_id)
        graph = resolve_graph(session)
        _check_class_students(session, class_id, student_ids)
        kp_ids: list[int] | None = None
        if kp_codes:
            kp_ids = []
            for c in kp_codes:
                try:
                    kp_ids.append(graph.code(c))
                except KeyError as e:
                    raise ToolInputError(f"知识点编码不存在: {c}") from e
        else:
            taught = {r["kp_id"] for r in progress_list(session, class_id)}
            kp_ids = sorted(taught)
            if not kp_ids:
                raise ToolInputError(f"班级 {class_id} 尚无教学进度记录，无法推导掌握度")
        return _get_kp_mastery(session, graph, student_ids, kp_ids, _opt_date(as_of))

    return _run(
        op,
        "GET /students/{id}/mastery",
        {"class_id": class_id, "student_ids": student_ids, "kp_codes": kp_codes, "as_of": as_of},
    )


@mcp.tool(annotations=_READONLY)
def run_attribution(
    student_id: Annotated[int, Field(ge=1, description="学生 id")],
    as_of: Annotated[str | None, Field(description="截止日期 YYYY-MM-DD；缺省今天")] = None,
) -> dict:
    """对一名学生运行归因引擎，给出薄弱点的成因假设：类型（前置缺陷/遗忘/易混）、根源知识点、置信度与证据。

    只做实时推导，不写库不改任何数据。假设是「待确认」而非结论——呈现给教师时应保持这个措辞。适用于「他为什么这个点不会」类追问。
    """
    def op(session):
        from app.models import Student

        stu = session.get(Student, student_id)
        if stu is None:
            raise ToolInputError(f"学生 {student_id} 不存在")
        _guard_class(session, stu.class_id)
        graph = resolve_graph(session)
        return _run_attribution(session, graph, student_id, _opt_date(as_of))

    return _run(op, "POST /students/{id}/attributions", {"student_id": student_id, "as_of": as_of})


@mcp.tool(annotations=_READONLY)
def get_kp_detail(
    code_or_id: Annotated[
        str, Field(description="知识点编码（如 P3）或数字 id")
    ],
) -> dict:
    """查询单个知识点的结构事实：属性（章节/难度先验/掌握度底线）+ 前置链（深度5）+ 直接前置 + 后继 + 包含关系。

    用于解释「为什么先学 A 才能学 B」「这个点属于哪一章」。当其他工具返回 kp_code 时可用本工具深挖结构背景。
    """
    raw = code_or_id.strip()
    target: str | int
    if raw.isdigit():
        target = int(raw)
    else:
        target = raw

    def op(session):
        graph = resolve_graph(session)
        return _get_kp_detail(session, graph, target)

    return _run(op, "GET /kb/kps/{id}", {"code_or_id": code_or_id})


@mcp.tool(annotations=_READONLY)
def get_teaching_progress(
    class_id: Annotated[int, Field(ge=1, description="班级 id")],
) -> dict:
    """查询班级教学进度：已教过哪些知识点、各自何时教的。

    用于回答「这个点教过没有」「什么时候教的」，以及解释为什么某些知识点没有分析数据（没教过就没有证据门槛）。返回按教学时间排序。
    """
    def op(session):
        _guard_class(session, class_id)
        return _get_teaching_progress(session, class_id)

    return _run(op, "GET /classes/{id}/progress", {"class_id": class_id})


@mcp.tool(annotations=_READONLY)
def list_students(
    class_id: Annotated[int, Field(ge=1, description="班级 id")],
    offset: Annotated[int, Field(ge=0, description="分页偏移")] = 0,
    limit: Annotated[int, Field(ge=1, le=50, description="每页人数上限（默认50）")] = 50,
) -> dict:
    """获取班级名册（分页）：学生 id 与别名列表现。

    名册只含 name_or_alias 别名与外部编码，绝不含真名和分数。用于把教师口述的学生对应到 student_id 再追问掌握度/归因。
    """
    def op(session):
        _guard_class(session, class_id)
        return _list_students(session, class_id, offset, limit)

    return _run(op, "GET /classes/{id}/students", {"class_id": class_id, "offset": offset})


# ---------------------------------------------------------------------------
# 写操作工具（Phase 3 批次B）：产出 draft/suggested 态，过审批门
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITES)
def create_report_draft_tool(
    report_type: Annotated[
        str, Field(description="报告类型：student_diagnosis（学生诊断单）或 class_improvement_advice（班级改进意见）")
    ],
    markdown: Annotated[str, Field(description="报告正文 markdown；数字必须来自工具返回，禁止编造")],
    class_id: Annotated[int | None, Field(ge=1, description="班级 id（班级级报告必填）")] = None,
    student_id: Annotated[int | None, Field(ge=1, description="学生 id（学生级报告用，与班级二选一）")] = None,
    exam_id: Annotated[int | None, Field(ge=1, description="关联考试 id（可选）")] = None,
) -> dict:
    """起草一份报告草稿，送入教师审批收件箱（不会直接发布）。

    适用场景：你完成了调查分析、需要把结论沉淀为正式文档时。正文要求：
    只引用工具取回的数字与名称、成长框架措辞、不排名不贴标签。草稿落库后
    由教师在收件箱中签发或打回——你没有签发权限，也无需等待签发结果。
    """
    def op(session):
        graph = resolve_graph(session)
        if student_id is not None:
            from app.models import Student

            stu = session.get(Student, student_id)
            if stu is None:
                raise ToolInputError(f"学生 {student_id} 不存在")
            _guard_class(session, stu.class_id)
        elif class_id is not None:
            _guard_class(session, class_id)
        return _create_report_draft(
            session, graph,
            report_type=report_type,
            class_id=class_id,
            student_id=student_id,
            exam_id=exam_id,
            markdown=markdown,
        )

    return _run(
        op, "POST /reports (draft)",
        {"report_type": report_type, "class_id": class_id,
         "student_id": student_id, "exam_id": exam_id},
    )


@mcp.tool(annotations=_WRITES)
def record_intervention_tool(
    student_id: Annotated[int, Field(ge=1, description="学生 id")],
    kp_code: Annotated[str, Field(description="知识点编码（如 M7A-102）")],
    kind: Annotated[
        str,
        Field(description=(
            "干预类型封闭枚举：reteach（重讲）/ prereq_backfill（回补基础点）/ "
            "spaced_review（间隔复习）/ contrast_practice（概念辨析）/ "
            "evidence_boost（补证据练习）/ tier_drill（层级补强）"
        )),
    ],
    exam_id: Annotated[int, Field(ge=1, description="关联考试 id（干预归属到触发它的那场考试）")],
    note: Annotated[str | None, Field(description="一句话说明建议理由（可选）")] = None,
) -> dict:
    """登记一条干预建议（状态=建议中，需教师在行动明细里确认后生效）。

    适用场景：调查中发现某个学生的薄弱点有明确的干预方向，值得列为正式行动项。
    知识点必须已列入该班教学进度。建议登记后由教师确认执行，复测后系统自动
    推导效果——不要向教师承诺效果。
    """
    def op(session):
        from app.models import Student

        stu = session.get(Student, student_id)
        if stu is None:
            raise ToolInputError(f"学生 {student_id} 不存在")
        _guard_class(session, stu.class_id)
        graph = resolve_graph(session)
        return _record_intervention(
            session, graph,
            student_id=student_id, kp_code=kp_code, kind=kind,
            exam_id=exam_id, note=note,
        )

    return _run(
        op, "POST /interventions (suggested)",
        {"student_id": student_id, "kp_code": kp_code, "kind": kind, "exam_id": exam_id},
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
