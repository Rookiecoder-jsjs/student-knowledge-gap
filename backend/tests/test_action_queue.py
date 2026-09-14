"""行动明细队列化（intervention-queue-design）：生成端合并 / 自动归档 / 队列折叠。

三条纪律对应三组测试：
- 合并（生成端）：同 (班级, 学生, KP) 至多一条挂起建议，跨考试原位刷新；
  存量同键重复（合并纪律生效前的数据形态）只留最新，其余归档；
- 归档（生成端）：前提消失的挂起建议随提交落 skipped + 系统注记——学生
  达标、班级共性跌破阈值且样本充足（n≥4）；未考学生不下结论；
- 队列（读视图）：仅挂起建议、班级行覆盖抑制（跳过后回流）、小组按组
  一行、截前 ACTION_QUEUE_MAX 条且积压数保持全量口径。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.intervention import (
    ACTION_QUEUE_MAX,
    KIND_RETEACH,
    action_plan_view,
)
from app.kb.graph import KpGraph
from app.models import ExamTemplate, Intervention
from tests.conftest import add_progress
from tests.test_intervention_model import (
    _bulk_answer,
    _commit,
    _exam,
    _gen,
    _latest_exam,
    _run,
    _weak_env_common,
)


def _fact(session, env, *, scope, kp_id, student_id=None, kind="spaced_review",
          group_ref=None, exam_id=None, status="suggested", note=None):
    """直造一条干预事实（绕开生成器，测读视图折叠用）。"""
    effective_exam_id = exam_id if exam_id is not None else _latest_exam(session).id
    row = Intervention(
        class_id=env["class"].id, student_id=student_id, kp_id=kp_id,
        exam_id=effective_exam_id, kind=kind, scope=scope, group_ref=group_ref,
        baseline_as_of=datetime(2025, 10, 1, 12, 0), status=status, note=note,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# 生成端：跨考试合并
# ---------------------------------------------------------------------------


def test_coalesce_across_exams(session, env):
    """同生同点连续两场薄弱 → 挂起建议不随考试次数增长，原行刷新到最新场。"""
    _weak_env_common(session, env)
    out1, _ = _gen(session, env)
    before = session.query(Intervention).filter_by(status="suggested").all()
    assert before, "共性薄弱应产出建议行"

    # 第四场：同一批人仍弱 → 只刷新不新增
    _run(session, env,
         _exam(session, env, "E3", date(2025, 11, 5), "P1"),
         {"T01", "T02", "T03"})
    out2, _ = _gen(session, env)
    after = session.query(Intervention).filter_by(status="suggested").all()
    assert {(r.student_id, r.kp_id) for r in after} == {
        (r.student_id, r.kp_id) for r in before
    }, "同键不新增"
    assert len(after) == len(before), "挂起建议数不随考试次数增长"
    assert out2["archived"] == 0
    fresh_cls = next(r for r in after if r.student_id is None)
    assert fresh_cls.exam_id == _latest_exam(session).id, "基线刷新到最新一场"
    _ = out1


def test_coalesce_dedupes_legacy_duplicates(session, env):
    """存量同键重复（旧数据形态）→ 只留最新一条可刷新，其余归档。"""
    _weak_env_common(session, env)
    _gen(session, env)
    exams = session.query(ExamTemplate).order_by(ExamTemplate.id).all()
    cls_row = session.query(Intervention).filter_by(
        status="suggested", student_id=None, kind=KIND_RETEACH
    ).first()
    assert cls_row is not None
    # 两场旧考试各留一条同键重复（旧版跨考试堆积的形态）
    dups = []
    for e in exams[:2]:
        dups.append(
            _fact(session, env, scope="class", kp_id=cls_row.kp_id,
                  kind=KIND_RETEACH, exam_id=e.id, note="旧重复")
        )
    _gen(session, env)  # 重跑最新一场
    session.refresh(dups[0])
    assert dups[0].status == "skipped", "最旧重复应被归档"
    assert "系统合并" in (dups[0].note or "")
    n = session.query(Intervention).filter_by(
        status="suggested", student_id=None, kind=KIND_RETEACH
    ).count()
    assert n == 1, "同键只留一条挂起建议"


# ---------------------------------------------------------------------------
# 生成端：前提消失自动归档
# ---------------------------------------------------------------------------


def test_auto_archive_when_premise_gone(session, env):
    """前提消失（证据补足后已达标）→ 挂起建议自动归档 + 系统注记。

    U 点确定性场景：单题证据全员 gate=数据不足 → 每人一条 evidence_boost
    挂起（gate 行不依赖归因匹配，必然生成）；两题高分补足证据且掌握达标
    → 前提消失，六条全部归档。
    """
    add_progress(session, env["class"].id, [env["kp"]["U"]])
    _run(session, env,
         _exam(session, env, "U单证据", date(2025, 10, 8), "U"), {"T01"})
    _gen(session, env)
    pend = session.query(Intervention).filter_by(status="suggested").all()
    assert len(pend) == 6, "全员证据不足应各挂一条补证据建议"

    # 两题全高分：证据补足（≥2 题）且掌握达标 → 前提消失
    _run(session, env, _exam(session, env, "U补足", date(2025, 11, 5), "U", n_q=2), set())
    out, _ = _gen(session, env)
    assert not session.query(Intervention).filter_by(status="suggested").all()
    assert out["archived"] == 6
    assert all("系统归档" in (r.note or "") for r in pend)


def test_auto_archive_keeps_uncommitted_students(session, env):
    """未参加本场考试的学生：无法重评估，挂起建议保留（不下结论）。"""
    add_progress(session, env["class"].id, [env["kp"]["U"]])
    _run(session, env,
         _exam(session, env, "U单证据", date(2025, 10, 8), "U"), {"T01"})
    _gen(session, env)
    assert session.query(Intervention).filter_by(status="suggested").count() == 6

    # 补足场仅 5 人提交且都高分 → 未提交的 T01 建议保留
    tpl = _exam(session, env, "U补足", date(2025, 11, 5), "U", n_q=2)
    for name in ("T02", "T03", "T04", "T05", "T06"):
        _bulk_answer(session, tpl, env["students"][name],
                     {q.idx: 9.0 for q in tpl.questions})
    _commit(session, tpl)
    out, _ = _gen(session, env)
    t01 = env["students"]["T01"]
    assert out["archived"] == 5, "提交且达标的 5 人归档"
    assert session.query(Intervention).filter_by(
        status="suggested", student_id=t01).count() == 1, "未考学生不归档"


def test_class_row_archive_requires_sample(session, env):
    """班级行归档护栏：n≥4 且占比跌破阈值才归档；样本不足保留。"""
    _weak_env_common(session, env)
    _gen(session, env)
    cls = session.query(Intervention).filter_by(
        status="suggested", student_id=None, kind=KIND_RETEACH).one()

    # 仅 4 人提交（T01 弱 + 三个历史干净学生）：1/4=25% < 40%，n=4≥4
    # → 共性证伪 → 归档（T02/T03 一场高分恢复不了，不进样本）
    tpl = _exam(session, env, "E3", date(2025, 11, 5), "P1")
    for name in ("T01", "T04", "T05", "T06"):
        score = 3.0 if name == "T01" else 9.0
        _bulk_answer(session, tpl, env["students"][name],
                     {q.idx: score for q in tpl.questions})
    _commit(session, tpl)
    _gen(session, env)
    session.refresh(cls)
    assert cls.status == "skipped" and "系统归档" in (cls.note or "")

    # 共性恢复（3/6≥40%）→ 重新建议；随后样本不足（n=2）不下结论
    _run(session, env,
         _exam(session, env, "E4", date(2025, 11, 15), "P1"),
         {"T01", "T02", "T03"})
    _gen(session, env)
    cls2 = session.query(Intervention).filter_by(
        status="suggested", student_id=None, kind=KIND_RETEACH).one()
    tpl2 = _exam(session, env, "E5", date(2025, 11, 25), "P1")
    for name in ("T01", "T02"):
        _bulk_answer(session, tpl2, env["students"][name],
                     {q.idx: 3.0 for q in tpl2.questions})
    _commit(session, tpl2)
    _gen(session, env)
    session.refresh(cls2)
    assert cls2.status == "suggested", "样本不足不下结论，班级行保留"


# ---------------------------------------------------------------------------
# 读视图：队列折叠
# ---------------------------------------------------------------------------


def test_queue_suppresses_class_covered_and_reflows(session, env):
    """挂起班级行抑制同点个体行；班级行跳过后个体行回流（纯视图规则）。"""
    # U 点单题证据 → 全员 gate=数据不足 → 折叠态「证据不足」∈ 队列白名单
    add_progress(session, env["class"].id, [env["kp"]["U"]])
    _run(session, env,
         _exam(session, env, "U单证据", date(2025, 10, 8), "U"), {"T01"})
    t01 = env["students"]["T01"]
    u = env["kp"]["U"]
    cls = _fact(session, env, scope="class", kp_id=u, kind=KIND_RETEACH)
    ind = _fact(session, env, scope="student", student_id=t01, kp_id=u,
                kind="evidence_boost")

    graph = KpGraph(session, env["kb"].id)
    view = action_plan_view(session, graph, env["class"].id)
    ids = {r["id"] for r in view["rows"]}
    assert cls.id in ids and ind.id not in ids, "班级行挂起时个体行被抑制"

    cls.status = "skipped"
    session.flush()
    view2 = action_plan_view(session, graph, env["class"].id)
    ids2 = {r["id"] for r in view2["rows"]}
    assert cls.id not in ids2 and ind.id in ids2, "班级行退出后个体行回流"


def test_queue_collapses_group_to_one_row(session, env):
    """同 group_ref 的小组行在队列里折叠成一行（代表行带 group_size）。"""
    add_progress(session, env["class"].id, [env["kp"]["U"]])
    _run(session, env,
         _exam(session, env, "U单证据", date(2025, 10, 8), "U"), {"T01"})
    u = env["kp"]["U"]
    sids = list(env["students"].values())[:3]
    for sid in sids:
        _fact(session, env, scope="group", student_id=sid, kp_id=u,
              kind="evidence_boost", group_ref="r1:U")

    graph = KpGraph(session, env["kb"].id)
    view = action_plan_view(session, graph, env["class"].id)
    group_rows = [r for r in view["rows"] if r["scope"] == "group"]
    assert len(group_rows) == 1, "同组只出代表行"
    assert group_rows[0]["group_size"] == 3
    assert group_rows[0]["group_ref"] == "r1:U"


def test_queue_dedupes_same_key_rows(session, env):
    """存量同键重复在队列里只露最新一条（与生成端合并纪律同口径）。"""
    add_progress(session, env["class"].id, [env["kp"]["U"]])
    _run(session, env,
         _exam(session, env, "U单证据", date(2025, 10, 8), "U"), {"T01"})
    u = env["kp"]["U"]
    t01 = env["students"]["T01"]
    old = _fact(session, env, scope="student", student_id=t01, kp_id=u,
                kind="evidence_boost")
    new = _fact(session, env, scope="student", student_id=t01, kp_id=u,
                kind="evidence_boost")

    graph = KpGraph(session, env["kb"].id)
    view = action_plan_view(session, graph, env["class"].id)
    mine = [r for r in view["rows"]
            if r.get("student_id") == t01 and r["kp_code"] == "U"]
    assert len(mine) == 1, "同键重复只出一行"
    assert mine[0]["id"] == new.id, "保留最新一条"
    _ = old


def test_queue_cap_and_backlog_honesty(session, env):
    """超过上限：队列截前 ACTION_QUEUE_MAX 条；积压数保持全量口径。"""
    # 三个点各一场单题考试 → 全员「证据不足」（队列白名单态）
    for code in ("P2", "P3", "U"):
        add_progress(session, env["class"].id, [env["kp"][code]])
        _run(session, env,
             _exam(session, env, f"{code}单证据", date(2025, 10, 8), code), set())
    n_made = 0
    for code in ("P2", "P3", "U"):
        for sid in env["students"].values():
            _fact(session, env, scope="student", student_id=sid,
                  kp_id=env["kp"][code], kind="evidence_boost")
            n_made += 1
    assert n_made > ACTION_QUEUE_MAX

    graph = KpGraph(session, env["kb"].id)
    view = action_plan_view(session, graph, env["class"].id)
    assert len(view["rows"]) == ACTION_QUEUE_MAX, "队列截前 10 条"
    assert view["pending_confirm"] == n_made, "积压数保持全量口径"
    assert all(r["status"] == "suggested" for r in view["rows"])
