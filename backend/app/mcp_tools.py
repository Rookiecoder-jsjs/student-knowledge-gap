"""Agent 工具实现层（agent-product-design §5.1，Phase 2 批次A）。

mcp_server.py 的 FastMCP 装饰器要求函数签名即 JSON Schema，无法注入
SQLAlchemy Session；本模块把工具逻辑收拢为**显式接收 Session 的纯函数**，
让 HTTP 路由、FastMCP 包装、pytest 三方共用同一份实现（不复制聚合逻辑）。

与 §5.1 约束的对应：
- 只读：全部函数不写库；
- name_or_alias：任何学生仅以别名/外部编码出现（§9 prompt 最小化）;
- 分页：get_kp_mastery / list_students 强制 limit 上限（上下文预算军规）;
- _provenance 由 mcp_server.py 包装层统一追加（本层保持纯数据）。

错误约定：资源不存在抛 ``LookupError``（包装层翻译为工具错误的 message），
参数非法抛 ``ValueError``；两者都不携带栈，模型可读。
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.kb.graph import KpGraph
from app.kb.resolver import KbNotActiveError
from app.models import Class, ExamTemplate, KnowledgePoint, Student
from app.pipeline.attribution import resolve_attributions
from app.pipeline.mastery import mastery_at
from app.pipeline.weakness import assess_student_kps
from app.queries.classes import progress_list
from app.queries.exams import exam_detail

# 上下文硬预算：单次工具返回的行数上限（§5.1 大结果集强制分页）
MAX_PAGE = 50

# get_kp_mastery 默认只回薄弱点全量 + 掌握点摘要，避免全班×全点矩阵撑爆上下文
_WEAK_FIRST_LIMIT = 20


class ToolInputError(ValueError):
    """参数非法（越界/格式错）。包装层直接把 message 回给模型。"""


def resolve_graph(session: Session) -> tuple:
    """active 知识库解析 → (kb_version_id, KpGraph)。无 active 抛 ToolInputError。

    MCP 场景没有 HTTP 层，这里复刻 deps._active_kb 的 strict 语义但不抛 HTTP 异常。
    """
    from app.kb.resolver import active_kb

    try:
        kb = active_kb(session)
    except KbNotActiveError as e:
        raise ToolInputError(str(e)) from e
    if kb is None:
        raise ToolInputError("尚未导入知识库")
    return kb.id, KpGraph(session, kb.id)


def _as_dt(d: date | None) -> datetime:
    # 与 api.deps._as_dt 同语义：当日结束时刻，避免考试日当天证据被判"未来"
    return datetime.combine(d, time(23, 59)) if d else datetime.combine(
        datetime.now().date(), time(23, 59)
    )


def _require_class(session: Session, class_id: int) -> Class:
    clazz = session.get(Class, class_id)
    if clazz is None:
        raise LookupError(f"班级 {class_id} 不存在")
    return clazz


# ---------------------------------------------------------------------------
# 1. get_exam_summary —— 单场考试事实（质量 snapshot_json）
# ---------------------------------------------------------------------------


def latest_exam_id(session: Session, class_id: int) -> int:
    """该班最近一场考试 id（exam_summary 省略 exam_id 时的默认目标）。"""
    eid = session.scalar(
        select(ExamTemplate.id)
        .where(ExamTemplate.class_id == class_id)
        .order_by(ExamTemplate.exam_date.desc(), ExamTemplate.id.desc())
        .limit(1)
    )
    if eid is None:
        raise LookupError(f"班级 {class_id} 暂无考试")
    return eid


def get_exam_summary(
    session: Session,
    graph: KpGraph,
    class_id: int,
    exam_id: int | None = None,
) -> dict:
    """单场考试的班级质量事实：提交/待审、均分、逐题得分率、共性薄弱点。

    数据源与前端概况页同源（quality_analysis 报告 snapshot_json），get-or-generate。
    exam_id 缺省取该班最近一场。
    """
    from app.reports.diagnosis_orchestrator import get_or_generate_quality_report

    _require_class(session, class_id)
    if exam_id is None:
        exam_id = latest_exam_id(session, class_id)
    else:
        tpl = session.get(ExamTemplate, exam_id)
        if tpl is None or tpl.class_id != class_id:
            raise ToolInputError(f"考试 {exam_id} 不属于班级 {class_id}")
    try:
        report = get_or_generate_quality_report(session, graph, class_id, exam_id)
    except ValueError as e:
        raise ToolInputError(str(e)) from e
    snap = report.snapshot_json or {}
    return {
        "exam_id": exam_id,
        "class_id": class_id,
        "report_id": report.id,
        "summary_markdown": report.content_markdown,
        **snap,
    }


# ---------------------------------------------------------------------------
# 2. get_kp_mastery —— 全班掌握度（薄弱优先，分页）
# ---------------------------------------------------------------------------


def get_kp_mastery(
    session: Session,
    graph: KpGraph,
    student_ids: list[int],
    kp_ids: list[int],
    as_of: date | None = None,
) -> dict:
    """按 (学生 × 教过知识点) 给出掌握度，弱项排前。

    输入是调用方已筛选好的 id 列表（MCP 参数校验在 server 层做班级归属检查），
    本函数只负责推导与排序——保持与 pipeline 层同等纯度。
    """
    when = _as_dt(as_of)
    rows: list[dict] = []
    for sid in student_ids:
        for kp_id in kp_ids:
            m = mastery_at(session, sid, kp_id, when)
            if m is None:
                continue
            kp = graph.kp(kp_id)
            floor = getattr(kp, "mastery_floor", None)
            rows.append(
                {
                    "student_id": sid,
                    "kp_code": kp.code,
                    "kp_name": kp.name,
                    "mastery": round(m, 3),
                    "below_floor": bool(floor is not None and m < floor),
                }
            )
    # 弱项在前（低于 floor 优先，其次按掌握度升序）
    rows.sort(key=lambda r: (not r["below_floor"], r["mastery"]))
    weak_rows = [r for r in rows if r["below_floor"]]
    ok_rows = [r for r in rows if not r["below_floor"]]
    return {
        "as_of": str(when.date()),
        "weak_first": weak_rows[:_WEAK_FIRST_LIMIT],
        "weak_total": len(weak_rows),
        "mastered_sample": ok_rows[:10],
        "total_pairs": len(rows),
        "truncated": len(rows) > len(weak_rows[:_WEAK_FIRST_LIMIT]) + len(ok_rows[:10]),
    }


# ---------------------------------------------------------------------------
# 3. run_attribution —— 单生归因（derive-on-read 实时推导，不写库）
# ---------------------------------------------------------------------------


def run_attribution(
    session: Session, graph: KpGraph, student_id: int, as_of: date | None = None
) -> dict:
    """单生归因假设清单：类型 + 根源点 + 置信依据 + 教师裁决叠加。

    用 ``resolve_attributions``（读路径纯推导 ⊕ 持久化人工裁决），**不写库**——
    与 POST /students/{id}/attributions 的 materialize 版本不同，Agent 只调查不动账。
    """
    stu = session.get(Student, student_id)
    if stu is None:
        raise LookupError(f"学生 {student_id} 不存在")
    when = _as_dt(as_of)
    resolved = resolve_attributions(session, graph, student_id, stu.class_id, when)
    out = []
    for a in resolved:
        root = graph.kp(a.root_kp_id) if a.root_kp_id else None
        out.append(
            {
                "kp_code": graph.kp(a.kp_id).code,
                "kp_name": graph.kp(a.kp_id).name,
                "type": a.type,
                "confidence": round(a.confidence, 3),
                "root_kp_name": root.name if root else None,
                "prediction": a.prediction,
                "verdict": a.verdict,
                "teacher_note": a.teacher_note,
                "evidence": a.evidence,
            }
        )
    out.sort(key=lambda x: -x["confidence"])
    stu_alias = stu.name_or_alias
    return {
        "student": {"student_id": student_id, "name_or_alias": stu_alias},
        "as_of": str(when.date()),
        "attributions": out,
    }


# ---------------------------------------------------------------------------
# 4. get_kp_detail —— 知识点结构事实（属性 + 前置链）
# ---------------------------------------------------------------------------


def _kp_brief(k: KnowledgePoint) -> dict:
    return {
        "id": k.id,
        "code": k.code,
        "name": k.name,
        "description": k.description,
        "grade": k.grade,
        "semester": k.semester,
        "chapter": k.chapter,
        "difficulty_prior": k.difficulty_prior,
        "mastery_floor": k.mastery_floor,
        "importance": k.importance,
        "archived": k.archived,
    }


def get_kp_detail(session: Session, graph: KpGraph, code_or_id: str | int) -> dict:
    """单知识点详情：属性 + 前置链 + 直接前置 + 后继 + contains 关系。

    聚合逻辑自 kb 路由下沉至此共用（原路由实现改为委托本函数）；支持按
    code 或 id 定位，方便 Agent 用工具返回里的 code 追问。
    """
    from app.models import KpRelation

    if isinstance(code_or_id, int):
        kp = session.get(KnowledgePoint, code_or_id)
    else:
        raw = str(code_or_id).strip()
        try:
            kid = graph.code(raw)
            kp = session.get(KnowledgePoint, kid)
        except KeyError:
            kp = session.scalar(
                select(KnowledgePoint).where(KnowledgePoint.code == raw).limit(1)
            )
    if kp is None:
        raise LookupError(f"知识点 {code_or_id} 不存在")

    version_kp_ids = set(graph.kp_ids())

    def node(kid: int) -> dict:
        k = graph.kp(kid)
        return {"id": kid, "code": k.code, "name": k.name}

    prereq_chain = [
        {**node(aid), "depth": d, "weight": w}
        for aid, d, w in graph.prerequisite_chain(kp.id, 5)
    ]
    direct_prereq = [
        {**node(pid), "weight": w} for pid, w in graph.direct_prerequisites(kp.id)
    ]
    successors: list[dict] = []
    containers: list[dict] = []
    contained: list[dict] = []
    for rel in session.scalars(
        select(KpRelation).where(
            (KpRelation.from_kp_id == kp.id) | (KpRelation.to_kp_id == kp.id)
        )
    ):
        other_id = rel.to_kp_id if rel.from_kp_id == kp.id else rel.from_kp_id
        if other_id not in version_kp_ids:
            continue
        entry = {
            **node(other_id),
            "relation_id": rel.id,
            "type": rel.type,
            "weight": rel.weight,
        }
        if rel.type == "prerequisite":
            if rel.from_kp_id == kp.id:
                successors.append(entry)
        elif rel.type == "contains":
            if rel.to_kp_id == kp.id:
                containers.append(entry)
            else:
                contained.append(entry)
    return {
        **_kp_brief(kp),
        "kb_version_id": kp.kb_version_id,
        "prerequisite_chain": prereq_chain,
        "direct_prerequisites": direct_prereq,
        "successors": successors,
        "containers": containers,
        "contained": contained,
    }


# ---------------------------------------------------------------------------
# 5. get_teaching_progress —— 班级教学进度（教过没、何时教的）
# ---------------------------------------------------------------------------


def get_teaching_progress(session: Session, class_id: int) -> dict:
    """该班已教知识点清单（teaching_progress 表原样视图 + 元数据回查）。"""
    _require_class(session, class_id)
    rows = progress_list(session, class_id)
    taught_ids = {r["kp_id"] for r in rows}
    covered_count = len(taught_ids)
    return {
        "class_id": class_id,
        "taught_count": covered_count,
        "progress": rows[:MAX_PAGE],
        "truncated": len(rows) > MAX_PAGE,
    }


# ---------------------------------------------------------------------------
# 6. list_students —— 名册（别名制，追问名单用）
# ---------------------------------------------------------------------------


def list_students(session: Session, class_id: int, offset: int = 0, limit: int = MAX_PAGE) -> dict:
    """班级名册分页（name_or_alias 制，绝不含真名与分数）。"""
    _require_class(session, class_id)
    total = session.scalar(
        select(func.count(Student.id)).where(Student.class_id == class_id)
    )
    stmt = (
        select(Student)
        .where(Student.class_id == class_id)
        .order_by(Student.id)
        .offset(offset)
        .limit(max(1, min(limit, MAX_PAGE)))
    )
    students = [
        {
            "student_id": s.id,
            "name_or_alias": s.name_or_alias,
            "external_code": s.external_code,
        }
        for s in session.scalars(stmt)
    ]
    return {
        "class_id": class_id,
        "total": total or 0,
        "offset": offset,
        "students": students,
        "has_more": (offset + len(students)) < (total or 0),
    }


# ---------------------------------------------------------------------------
# 7-8. 写操作工具（Phase 3 批次B，过审批门 §5.3）
# 两把写工具都只产「待确认」状态：报告=draft 入收件箱、干预=suggested 等确认。
# Agent 永远不直接落终态——签发/确认动作由教师在 UI 完成（人在环上，
# 且签发本身不消耗 Agent 循环）。
# ---------------------------------------------------------------------------

WRITE_REPORT_TYPES = ("student_diagnosis", "class_improvement_advice")


def create_report_draft(
    session: Session,
    graph: KpGraph,
    *,
    report_type: str,
    class_id: int | None = None,
    student_id: int | None = None,
    exam_id: int | None = None,
    markdown: str,
) -> dict:
    """Agent 起草报告 → draft 入收件箱（§5.3 审批门）。

    正文必须由模型基于工具取回的确定性数据撰写；系统只做结构护栏：
    类型封闭枚举、目标班级/学生归属校验、正文非空且长度上限。落库后
    教师在收件箱签发或打回，Agent 无权签发自己的产出。
    """
    from app.models import Report

    if report_type not in WRITE_REPORT_TYPES:
        raise ToolInputError(
            f"report_type 必须是 {'/'.join(WRITE_REPORT_TYPES)}，收到 {report_type!r}"
        )
    md = (markdown or "").strip()
    if not md:
        raise ToolInputError("markdown 正文为空")
    if len(md) > 20_000:
        raise ToolInputError(f"markdown 过长（{len(md)} 字符 > 20000 上限），请精简")

    clazz = _require_class(session, class_id) if class_id is not None else None
    stu = None
    if student_id is not None:
        stu = session.get(Student, student_id)
        if stu is None:
            raise LookupError(f"学生 {student_id} 不存在")
        if clazz is not None and stu.class_id != clazz.id:
            raise ToolInputError(f"学生 {student_id} 不属于班级 {clazz.id}")
        clazz = session.get(Class, stu.class_id)
    if clazz is None:
        raise ToolInputError("必须提供 class_id 或 student_id 之一")
    if exam_id is not None:
        tpl = session.get(ExamTemplate, exam_id)
        if tpl is None or tpl.class_id != clazz.id:
            raise ToolInputError(f"考试 {exam_id} 不属于班级 {clazz.id}")

    report = Report(
        type=report_type,
        class_id=clazz.id,
        student_id=stu.id if stu else None,
        exam_id=exam_id,
        content_markdown=md,
        snapshot_json={
            "writer": {"model": "agent-draft", "prompt_version": "agent-draft-v0"},
            "origin": "mcp_tool",
        },
        status="draft",
        status_note="Agent 起草，待教师签发",
    )
    session.add(report)
    session.flush()
    # 批次D 触达：新草稿 → 网关 → 钉钉（fire-and-forget，未配置静默跳过）
    try:
        from app import triggers as _trg

        _trg.notify_draft_ready(session, report)
    except Exception:  # noqa: BLE001 —— 触达失败不影响工具结果
        pass
    return {
        "report_id": report.id,
        "status": report.status,
        "type": report.type,
        "class_id": clazz.id,
        "student_alias": stu.name_or_alias if stu else None,
        "chars": len(md),
        "next": "草稿已入收件箱，等待教师在收件箱中签发或打回",
    }


def record_intervention(
    session: Session,
    graph: KpGraph,
    *,
    student_id: int,
    kp_code: str,
    kind: str,
    exam_id: int,
    note: str | None = None,
) -> dict:
    """登记干预记录 → suggested 行（干预闭环状态机的入口，等教师确认）。

    kind 封闭枚举（labels_source 真源）；知识点必须已教（未教过的点不存在
    证据，写了也无法验证效果）。行落在 suggested 态，教师一键确认后才算
    「已执行」——Agent 的建议与它自己的报告一样要过审批门。exam_id 必填：
    干预行归属到触发它的那场考试（幂等清除按场操作需要它）。
    """
    from app.db import utcnow
    from app.labels_source import KIND_LABEL
    from app.models import ExamTemplate, Intervention, TeachingProgress

    if kind not in KIND_LABEL:
        raise ToolInputError(
            f"kind 必须是 {'/'.join(KIND_LABEL)}，收到 {kind!r}"
        )
    stu = session.get(Student, student_id)
    if stu is None:
        raise LookupError(f"学生 {student_id} 不存在")
    tpl = session.get(ExamTemplate, exam_id)
    if tpl is None or tpl.class_id != stu.class_id:
        raise ToolInputError(f"考试 {exam_id} 不属于学生所在班级 {stu.class_id}")
    try:
        kp_id = graph.code(kp_code.strip())
    except KeyError as e:
        raise ToolInputError(f"知识点编码不存在: {kp_code}") from e

    taught = session.scalar(
        select(TeachingProgress.id).where(
            TeachingProgress.class_id == stu.class_id,
            TeachingProgress.kp_id == kp_id,
        )
    )
    if taught is None:
        raise ToolInputError(
            f"知识点 {kp_code} 未列入该班教学进度，不能产生干预建议"
        )

    row = Intervention(
        class_id=stu.class_id,
        student_id=stu.id,
        kp_id=kp_id,
        exam_id=tpl.id,
        kind=kind,
        scope="student",
        baseline_as_of=utcnow(),
        status="suggested",
        suggested_at=utcnow(),
        note=(note or "").strip() or None,
    )
    session.add(row)
    session.flush()
    # 批次D 触达：待确认建议 → 网关 → 钉钉（同上纪律）
    try:
        from app import triggers as _trg

        _trg.fire_notify({
            "kind": "intervention_suggested",
            "alias": stu.name_or_alias,
            "kp_name": graph.kp(kp_id).name,
            "kind_label": KIND_LABEL[kind],
        })
    except Exception:  # noqa: BLE001
        pass
    return {
        "intervention_id": row.id,
        "status": row.status,
        "kind": kind,
        "kind_label": KIND_LABEL[kind],
        "student_alias": stu.name_or_alias,
        "kp_name": graph.kp(kp_id).name,
        "next": "干预建议已登记，等待教师在行动明细中确认执行；复测后可查效果",
    }
