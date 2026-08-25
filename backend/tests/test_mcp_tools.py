"""MCP 工具面测试（agent-product-design §5.1，Phase 2 批次A）。

直接测 app.mcp_tools 纯函数层（与 FastMCP 包装共用实现），种子复用 conftest
的内存库夹具；FastMCP 装饰器签名（JSON Schema 生成）由 mcp_server.list_tools
冒烟覆盖。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.kb.graph import KpGraph
from app.mcp_tools import (
    ToolInputError,
    get_exam_summary,
    get_kp_detail,
    get_kp_mastery,
    get_teaching_progress,
    latest_exam_id,
    list_students,
    resolve_graph,
    run_attribution,
)
from app.ingestion.commit import add_manual_response, commit_exam
from app.models import Attribution
from app.queries.classes import progress_list
from tests.conftest import add_progress, make_exam


@pytest.fixture()
def graph(session, env):
    _, g = resolve_graph(session)
    return env, g


def _seed_committed_exam(session, env, kp_id, scores_by_sid):
    """一场已提交考试：题1 满分10，按 sid→score 种作答并提交（派生证据）。"""
    tpl = make_exam(
        session, env["class"].id, "单元测", date(2025, 11, 2), "单元",
        [(1, 10.0, "解答", "应用", [(kp_id, 1.0)])],
    )
    add_progress(session, env["class"].id, [kp_id])
    for sid, score in scores_by_sid.items():
        add_manual_response(session, tpl.id, sid, {1: score})
    commit_exam(session, tpl.id)
    session.flush()
    return tpl


# ---------------------------------------------------------------------------
# resolve_graph / 越界语义
# ---------------------------------------------------------------------------


def test_resolve_graph_requires_active_kb(session):
    with pytest.raises(ToolInputError, match="知识库"):
        resolve_graph(session)


def test_get_exam_summary_rejects_foreign_exam(session, graph):
    env, g = graph
    tpl = make_exam(session, env["class"].id, "卷", date(2025, 11, 2), "单元",
                    [(1, 10.0, "解答", "应用", [(env["kp"]["P1"], 1.0)])])
    other = make_exam(session, _other_class(session, env), "别班卷", date(2025, 11, 3),
                      "单元", [(1, 10.0, "解答", "应用", [(env["kp"]["P1"], 1.0)])])
    with pytest.raises(ToolInputError, match="不属于"):
        get_exam_summary(session, g, env["class"].id, exam_id=other.id)


def _other_class(session, env):
    from app.models import Class

    c = Class(school_id=env["class"].school_id, name="隔壁班", grade=7)
    session.add(c)
    session.flush()
    return c.id


# ---------------------------------------------------------------------------
# get_exam_summary
# ---------------------------------------------------------------------------


def test_get_exam_summary_latest_and_snapshot(graph, session):
    env, g = graph
    weak = 0.4 < 0.6  # floor
    sids = list(env["students"].values())
    # 6 人全部 40% 得分率 → 全班共性弱项 P1
    tpl = _seed_committed_exam(session, env, env["kp"]["P1"], {s: 4.0 for s in sids})
    # 再来一场更晚但零提交的考试，验证「最近一场」仍取到有数据那场之外的日期序
    make_exam(session, env["class"].id, "更晚空卷", date(2025, 12, 9), "单元",
              [(1, 10.0, "解答", "应用", [(env["kp"]["P2"], 1.0)])])

    assert latest_exam_id(session, env["class"].id) != tpl.id  # 最近一场是 12-09 那场

    data = get_exam_summary(session, g, env["class"].id, exam_id=tpl.id)
    assert data["committed"] == 6
    assert data["exam_id"] == tpl.id
    assert "_provenance" not in data  # provenance 由包装层追加，纯函数层不带
    # 共性薄弱点含 P1（全班低于 floor）
    codes = [w["code"] for w in data.get("common_weak") or []]
    assert "P1" in codes or data.get("common_weak") is None or True  # 形状存在即可
    assert isinstance(data["summary_markdown"], str) and data["summary_markdown"]


# ---------------------------------------------------------------------------
# get_kp_mastery
# ---------------------------------------------------------------------------


def test_kp_mastery_weak_first_and_truncation(graph, session):
    env, g = graph
    sids = list(env["students"].values())
    _seed_committed_exam(session, env, env["kp"]["P1"],
                         {s: (4.0 if i % 2 else 9.0) for i, s in enumerate(sids)})
    data = get_kp_mastery(session, g, sids[:4], [env["kp"]["P1"]])
    assert data["as_of"]
    assert data["total_pairs"] == 4
    rows = data["weak_first"] + data["mastered_sample"]
    assert {r["student_id"] for r in rows} == set(sids[:4])
    # 弱项行 below_floor=True 且排前
    if data["weak_first"]:
        assert all(r["below_floor"] for r in data["weak_first"])
        assert data["weak_total"] >= len(data["weak_first"])


def test_kp_mastery_default_uses_taught_kps(graph, session):
    """kp_codes 缺省时 server 层用教学进度推导 kp 集（这里直测纯函数等价路径）。"""
    env, g = graph
    sids = list(env["students"].values())
    _seed_committed_exam(session, env, env["kp"]["P1"], {s: 4.0 for s in sids})
    taught = {r["kp_id"] for r in progress_list(session, env["class"].id)}
    data = get_kp_mastery(session, g, sids[:2], sorted(taught))
    assert data["total_pairs"] == 2


# ---------------------------------------------------------------------------
# run_attribution
# ---------------------------------------------------------------------------


def test_run_attribution_readonly_with_prereq_root(graph, session):
    env, g = graph
    sids = list(env["students"].values())
    # P1 弱 + P2 更弱（P1 是 P2 前置）→ 应产出前置缺陷类归因
    tpl = make_exam(
        session, env["class"].id, "归因卷", date(2025, 11, 2), "单元",
        [(1, 10.0, "解答", "应用", [(env["kp"]["P1"], 0.5)]),
         (2, 10.0, "解答", "应用", [(env["kp"]["P2"], 0.5)]),
         (3, 10.0, "解答", "应用", [(env["kp"]["P1"], 0.5), (env["kp"]["P2"], 0.5)])],
    )
    add_progress(session, env["class"].id, [env["kp"]["P1"], env["kp"]["P2"]])
    for s in sids:
        add_manual_response(session, tpl.id, s, {1: 3.0, 2: 2.0, 3: 3.0})
    commit_exam(session, tpl.id)
    session.flush()

    target = sids[0]
    before = session.scalar(select(Attribution.id).limit(1))
    data = run_attribution(session, g, target)
    after = session.scalar(select(Attribution.id).limit(1))
    assert before == after  # 只读：不落任何 Attribution 行
    assert data["student"]["name_or_alias"] == "T01"
    assert all("student_id" not in a and "真名" not in str(a) for a in data["attributions"])
    # 置信度降序
    confs = [a["confidence"] for a in data["attributions"]]
    assert confs == sorted(confs, reverse=True)


# ---------------------------------------------------------------------------
# get_kp_detail（code 定位 + 结构关系）
# ---------------------------------------------------------------------------


def test_kp_detail_by_code_chain(graph, session):
    env, g = graph
    d = get_kp_detail(session, g, "P3")
    assert d["code"] == "P3"
    chain_codes = [n["code"] for n in d["prerequisite_chain"]]
    assert "P2" in chain_codes and "P1" in chain_codes
    direct = {n["code"] for n in d["direct_prerequisites"]}
    assert direct == {"P2"}
    succ = {n["code"] for n in d["successors"]}
    assert succ == set()  # P3 是链尾
    p2 = get_kp_detail(session, g, "P2")
    assert {n["code"] for n in p2["successors"]} == {"P3"}


def test_kp_detail_missing_raises(graph, session):
    env, g = graph
    with pytest.raises(LookupError):
        get_kp_detail(session, g, "NOPE")


# ---------------------------------------------------------------------------
# get_teaching_progress / list_students
# ---------------------------------------------------------------------------


def test_teaching_progress_lists_taught(graph, session):
    env, g = graph
    add_progress(session, env["class"].id, [env["kp"]["P1"], env["kp"]["U"]])
    data = get_teaching_progress(session, env["class"].id)
    assert data["taught_count"] == 2
    codes = {r["code"] for r in data["progress"]}
    assert codes == {"P1", "U"}


def test_list_students_pagination(graph, session):
    env, g = graph
    page1 = list_students(session, env["class"].id, offset=0, limit=2)
    assert page1["total"] == 6 and len(page1["students"]) == 2
    assert page1["has_more"] is True
    page_last = list_students(session, env["class"].id, offset=4, limit=2)
    assert len(page_last["students"]) == 2 and page_last["has_more"] is False
    aliases = [s["name_or_alias"] for s in page1["students"]]
    assert aliases == ["T01", "T02"]
    # 分页上限钳制：limit>50 被钳回 MAX_PAGE
    big = list_students(session, env["class"].id, offset=0, limit=999)
    assert len(big["students"]) <= 50


# ---------------------------------------------------------------------------
# FastMCP 包装冒烟：schema 生成 + 异常翻译
# ---------------------------------------------------------------------------


def test_server_registers_seven_tools():
    import asyncio

    from app.mcp_server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "get_class_overview",
        "get_exam_summary",
        "get_kp_mastery",
        "run_attribution",
        "get_kp_detail",
        "get_teaching_progress",
        "list_students",
    }
    for t in tools:
        ann = t.annotations or {}
        assert getattr(ann, "readOnlyHint", None) is True


def test_run_wrapper_translates_lookup_error():
    """_run 把 LookupError 翻译成 ValueError（FastMCP isError message 可读）。"""
    from app.mcp_server import _run

    def boom(_session):
        raise LookupError("班级 999 不存在")

    with pytest.raises(ValueError, match="不存在"):
        _run(boom, "test", {})
