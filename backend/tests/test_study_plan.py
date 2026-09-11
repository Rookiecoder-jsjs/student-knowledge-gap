"""AI 学习方案 + 学生自报（study-loop-design）测试。

三层：
- 生成层（app/llm/study_writer）：开关关 / 校验失败 / 空正文 → None 保模板
  （照 test_plan_writer 纪律：开关与熔断 autouse 复位）；
- 领域层（app/study）：资格闸门、同轮幂等复用不重调、自报只消化个体行
  （班级行/小组行不动）、自报不产生证据（掌握度不动）、新一轮换新记录。
  干预行走直落事实（test_progress_state 同纪律：U 点无归因匹配不建行，
  生成器集成已有专项测试覆盖）；
- API 层：/me 学习方案 get-or-generate + 自报 + 折叠态透出 + 预览严格只读。
"""

from __future__ import annotations

import secrets
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.auth as auth
import app.llm.study_writer as study_writer_module
from app import db as dbmod
from app.api import deps as deps_mod
from app.auth import enable_student_login
from app.db import Base
from app.intervention import action_plan_view
from app.kb.graph import KpGraph
from app.llm.client import MockLLMClient, set_client
from app.llm.study_writer import get_study_breaker, write_study_plan
from app.models import (
    ExamResponse,
    Intervention,
    ResponseAnswer,
    School,
    Student,
    StudyRecord,
    Teacher,
)
from app.pipeline.mastery import get_events, mastery_at
from app.pipeline.progress import LOOP_SELF_REPORTED
from app.study import (
    get_or_generate_study_record,
    self_mark_learned,
    self_report_map,
)
from tests.conftest import add_progress, dt
from tests.test_api_intervention import _seed as _api_seed
from tests.test_intervention_model import (
    _exam,
    _latest_exam,
    _run,
)


@pytest.fixture(autouse=True)
def _study_off_by_default(monkeypatch):
    """每条测试默认关闭生成开关；需要开的显式 monkeypatch 置真。"""
    monkeypatch.setattr(study_writer_module.config, "STUDY_PLAN_ENABLE", False)
    get_study_breaker().reset()


def _stu(session, env, alias) -> Student:
    return session.get(Student, env["students"][alias])


def _weak_u(session, env, *, exam_date=date(2025, 10, 10), n_q=2):
    """U 点已教、两题考试：T01 弱（3/10）其余好（9/10）——与状态机测试同基线。"""
    add_progress(session, env["class"].id, [env["kp"]["U"]])
    _run(session, env, _exam(session, env, "U测", exam_date, "U", n_q=n_q), {"T01"})
    return _latest_exam(session)


def _pending_row(session, env, alias, *, exam_id, suggested_at=None,
                 baseline=None, scope="student", kind="spaced_review",
                 group_ref=None):
    """直落一条挂起事实行（绕过生成器，测自报消化与轮次换新）。"""
    row = Intervention(
        class_id=env["class"].id,
        student_id=env["students"][alias] if scope != "class" else None,
        kp_id=env["kp"]["U"], exam_id=exam_id, kind=kind, scope=scope,
        group_ref=group_ref, baseline_as_of=baseline or dt(date(2025, 10, 10)),
        status="suggested", suggested_at=suggested_at or dt(date(2025, 10, 15)),
    )
    session.add(row)
    session.flush()
    return row


_GOOD_MD = """### 先补这一步
- 基础已备好，直接开始。

### 核心讲解
- 先理解概念的含义，再看我什么时候用它。
- 最后把两步连起来：先判断，再动手。

### 针对你的练习
- 例题一：计算 3 + 5，先写步骤再写答案。
  解析：先把两个数对齐，再逐位相加，得到 8。
- 例题二：比较 7 和 9 的大小，说明理由。
  解析：数轴上越靠右越大，9 在 7 的右边，所以 9 更大。

### 怎么确认自己学会了
- 合上书能说出这个知识点讲什么；
- 能独立做对两道同类题。
"""


# ---------------------------------------------------------------------------
# 生成层：开关 / 校验 / 回落
# ---------------------------------------------------------------------------


def test_write_study_plan_switch_and_validation(monkeypatch):
    monkeypatch.setattr(study_writer_module.config, "STUDY_PLAN_ENABLE", False)
    assert write_study_plan({"kp": {"name": "U"}}) is None, "开关关不触达 client"

    monkeypatch.setattr(study_writer_module.config, "STUDY_PLAN_ENABLE", True)
    mock = MockLLMClient([{"markdown": _GOOD_MD}, {"markdown": "   "}])
    set_client(mock)
    try:
        draft = write_study_plan({"kp": {"name": "U"}})
        assert draft is not None and draft.model == "mock-vision-v0"
        assert write_study_plan({"kp": {}}) is None, "空正文整体回落"
    finally:
        set_client(None)


# ---------------------------------------------------------------------------
# 领域层：get-or-generate
# ---------------------------------------------------------------------------


def test_template_fallback_when_llm_off(session, env):
    """LLM 关闭 → 确定性模板兜底（四结构段齐全）；同轮幂等复用同一记录。"""
    _weak_u(session, env)
    graph = KpGraph(session, env["kb"].id)
    t01 = _stu(session, env, "T01")
    rec = get_or_generate_study_record(
        session, graph, t01, env["kp"]["U"], now=dt(date(2025, 10, 21))
    )
    assert rec.plan_writer == {"template": True}
    for seg in ("先补这一步", "核心讲解", "针对你的练习", "怎么确认自己学会了"):
        assert seg in rec.plan_markdown
    again = get_or_generate_study_record(
        session, graph, t01, env["kp"]["U"], now=dt(date(2025, 10, 21))
    )
    assert again.id == rec.id
    assert session.query(StudyRecord).count() == 1


def test_eligibility_gates(session, env):
    """非薄弱点 → ValueError；未知 kp → LookupError。"""
    _weak_u(session, env)
    graph = KpGraph(session, env["kb"].id)
    with pytest.raises(ValueError):
        get_or_generate_study_record(
            session, graph, _stu(session, env, "T02"), env["kp"]["U"],
            now=dt(date(2025, 10, 21)),
        )
    with pytest.raises(LookupError):
        get_or_generate_study_record(
            session, graph, _stu(session, env, "T01"), 999999,
            now=dt(date(2025, 10, 21)),
        )


def test_llm_generation_provenance_and_reuse(session, env, monkeypatch):
    """LLM 开启：生成成功带溯源；同轮复用不重调（mock.calls 恒 1）。"""
    monkeypatch.setattr(study_writer_module.config, "STUDY_PLAN_ENABLE", True)
    _weak_u(session, env)
    graph = KpGraph(session, env["kb"].id)
    mock = MockLLMClient([{"markdown": _GOOD_MD}])
    set_client(mock)
    try:
        rec = get_or_generate_study_record(
            session, graph, _stu(session, env, "T01"), env["kp"]["U"],
            now=dt(date(2025, 10, 21)),
        )
        assert len(mock.calls) == 1
        assert rec.plan_writer == {
            "model": "mock-vision-v0",
            "prompt_version": "study-plan-v0.1.0",
        }
        again = get_or_generate_study_record(
            session, graph, _stu(session, env, "T01"), env["kp"]["U"],
            now=dt(date(2025, 10, 21)),
        )
        assert again.id == rec.id and len(mock.calls) == 1
    finally:
        set_client(None)


def test_llm_bad_output_falls_back_to_template(session, env, monkeypatch):
    """LLM 输出不合格（重试后仍失败）→ 校验失败不计熔断 → 模板兜底。"""
    monkeypatch.setattr(study_writer_module.config, "STUDY_PLAN_ENABLE", True)
    _weak_u(session, env)
    graph = KpGraph(session, env["kb"].id)
    mock = MockLLMClient([
        {"markdown": "### 随便写写\n没有结构"},
        {"markdown": None},  # 重试一次仍不合格（flash 偶发空正文）
    ])
    set_client(mock)
    try:
        rec = get_or_generate_study_record(
            session, graph, _stu(session, env, "T01"), env["kp"]["U"],
            now=dt(date(2025, 10, 21)),
        )
        assert rec.plan_writer == {"template": True}
        assert get_study_breaker().state != "open", "校验失败不是 provider 故障"
    finally:
        set_client(None)


def test_write_study_plan_retries_on_bad_output(monkeypatch):
    """第一次空正文（flash 偶发）→ 校验失败重试一次 → 第二次合格被采纳。"""
    monkeypatch.setattr(study_writer_module.config, "STUDY_PLAN_ENABLE", True)
    mock = MockLLMClient([{"markdown": None}, {"markdown": _GOOD_MD}])
    set_client(mock)
    try:
        draft = write_study_plan({"kp": {"name": "U"}})
        assert draft is not None and draft.model == "mock-vision-v0"
        assert len(mock.calls) == 2
    finally:
        set_client(None)


# ---------------------------------------------------------------------------
# 领域层：自报
# ---------------------------------------------------------------------------


def test_self_mark_consumes_student_row(session, env):
    """自报消化个体挂起行（done + 注记），队列积压减一；重复自报幂等。"""
    _weak_u(session, env)
    tpl = _latest_exam(session)
    row = _pending_row(session, env, "T01", exam_id=tpl.id)
    graph = KpGraph(session, env["kb"].id)
    t01 = _stu(session, env, "T01")
    rec = get_or_generate_study_record(
        session, graph, t01, env["kp"]["U"], now=dt(date(2025, 10, 21))
    )
    assert rec.intervention_id == row.id, "挂起个体行应被指为触发源"
    before = action_plan_view(session, graph, env["class"].id)["pending_confirm"]
    out = self_mark_learned(session, graph, t01, rec.id, now=dt(date(2025, 10, 22)))
    assert out["row_done"] is True
    session.refresh(row)
    assert row.status == "done" and "学生自报" in (row.note or "")
    after = action_plan_view(session, graph, env["class"].id)["pending_confirm"]
    assert after == before - 1, "队列积压随自报消化"
    out2 = self_mark_learned(session, graph, t01, rec.id, now=dt(date(2025, 10, 23)))
    assert out2["row_done"] is False, "已自报幂等"


def test_self_mark_leaves_class_and_group_rows(session, env):
    """班级行/小组行是集体事实：学生个体自报只记录、不消化。"""
    _weak_u(session, env)
    tpl = _latest_exam(session)
    cls = _pending_row(session, env, "T01", exam_id=tpl.id, scope="class",
                       kind="reteach")
    grp = _pending_row(session, env, "T01", exam_id=tpl.id, scope="group",
                       kind="evidence_boost", group_ref="r1:U")
    graph = KpGraph(session, env["kb"].id)
    t01 = _stu(session, env, "T01")
    rec = get_or_generate_study_record(
        session, graph, t01, env["kp"]["U"], now=dt(date(2025, 10, 21))
    )
    out = self_mark_learned(session, graph, t01, rec.id, now=dt(date(2025, 10, 22)))
    assert out["row_done"] is False
    session.refresh(cls)
    session.refresh(grp)
    assert cls.status == "suggested" and grp.status == "suggested"
    assert rec.self_marked_at is not None, "自报事实照常落"


def test_self_report_moves_no_mastery_no_evidence(session, env):
    """硬边界：自报不产生 EvidenceEvent、不移动掌握度。"""
    _weak_u(session, env)
    graph = KpGraph(session, env["kb"].id)
    t01 = _stu(session, env, "T01")
    u = env["kp"]["U"]
    when = dt(date(2025, 11, 1))
    m_before = mastery_at(session, t01.id, u, when)
    ev_before = len(get_events(session, t01.id, u, when))
    rec = get_or_generate_study_record(
        session, graph, t01, u, now=dt(date(2025, 10, 21))
    )
    self_mark_learned(session, graph, t01, rec.id, now=dt(date(2025, 10, 22)))
    assert mastery_at(session, t01.id, u, when) == m_before
    assert len(get_events(session, t01.id, u, when)) == ev_before


def test_new_round_regenerates_record(session, env):
    """新一轮挂起行（二次干预重发——此处直落同形事实）→ 换新记录并指向新行。"""
    _weak_u(session, env)
    tpl = _latest_exam(session)
    row1 = _pending_row(session, env, "T01", exam_id=tpl.id)
    graph = KpGraph(session, env["kb"].id)
    t01 = _stu(session, env, "T01")
    u = env["kp"]["U"]
    rec1 = get_or_generate_study_record(
        session, graph, t01, u, now=dt(date(2025, 10, 21))
    )
    assert rec1.intervention_id == row1.id
    self_mark_learned(session, graph, t01, rec1.id, now=dt(date(2025, 10, 22)))

    row2 = _pending_row(session, env, "T01", exam_id=tpl.id,
                        suggested_at=dt(date(2025, 10, 26)),
                        baseline=dt(date(2025, 10, 25)))
    rec2 = get_or_generate_study_record(
        session, graph, t01, u, now=dt(date(2025, 10, 27))
    )
    assert rec2.id != rec1.id, "新一轮换新记录"
    assert rec2.intervention_id == row2.id
    assert session.query(StudyRecord).count() == 2


def test_reuse_after_self_mark_when_other_pending_row_remains(session, env):
    """自报消化触发行后，同 kp 另有挂起行（如班级行）→ 仍复用原方案。

    同轮判定不能比对 intervention_id：触发行落 done 后挂起行会换成另一条，
    按 id 比对会误判新轮而重复生成（实测踩坑后改为 generated_at >= suggested_at）。
    """
    _weak_u(session, env)
    tpl = _latest_exam(session)
    cls = _pending_row(session, env, "T01", exam_id=tpl.id, scope="class",
                       kind="reteach")
    row = _pending_row(session, env, "T01", exam_id=tpl.id)  # 后落 → 成为触发源
    graph = KpGraph(session, env["kb"].id)
    t01 = _stu(session, env, "T01")
    rec = get_or_generate_study_record(
        session, graph, t01, env["kp"]["U"], now=dt(date(2025, 10, 21))
    )
    assert rec.intervention_id == row.id
    self_mark_learned(session, graph, t01, rec.id, now=dt(date(2025, 10, 22)))
    session.refresh(cls)
    assert cls.status == "suggested", "班级行仍挂起（学生自报不消化集体行）"

    again = get_or_generate_study_record(
        session, graph, t01, env["kp"]["U"], now=dt(date(2025, 10, 23))
    )
    assert again.id == rec.id, "挂起行换成班级行仍属同轮，复用原方案"
    assert session.query(StudyRecord).count() == 1


def test_self_report_map_latest_wins(session, env):
    """批量预取：同键多条取最新 self_marked_at。"""
    _weak_u(session, env)
    t01 = env["students"]["T01"]
    u = env["kp"]["U"]
    session.add_all([
        StudyRecord(class_id=env["class"].id, student_id=t01, kp_id=u,
                    plan_markdown="m", generated_at=dt(date(2025, 10, 20)),
                    self_marked_at=dt(date(2025, 10, 20))),
        StudyRecord(class_id=env["class"].id, student_id=t01, kp_id=u,
                    plan_markdown="m", generated_at=dt(date(2025, 10, 22)),
                    self_marked_at=dt(date(2025, 10, 22))),
    ])
    session.flush()
    out = self_report_map(session, [t01])
    assert out[(t01, u)] == dt(date(2025, 10, 22))


# ---------------------------------------------------------------------------
# API 层：/me 学习方案 + 自报 + 预览只读 + 蓝图剔除
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """tmp 文件库 + 全局引擎替换（同 test_api_intervention 模式）。"""
    monkeypatch.setattr(study_writer_module.config, "STUDY_PLAN_ENABLE", False)
    get_study_breaker().reset()
    db_file = tmp_path / "study-api.db"
    eng = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False, autoflush=False)
    old = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine = eng
    dbmod.SessionLocal = S
    deps_mod.SessionLocal = S
    from app.main import app

    with TestClient(app) as c:
        yield c, S
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = old


def _login_student(c, S, env, alias, username):
    s = S()
    stu, _ = enable_student_login(s, env["students"][alias], "pw123456",
                                  username=username)
    s.commit()
    s.close()
    r = c.post("/auth/login", json={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return stu.id, {"Authorization": f"Bearer {r.json()['token']}"}


def _login_admin(c, S):
    s = S()
    school_id = s.query(School).first().id
    salt = secrets.token_bytes(16)
    s.add(Teacher(school_id=school_id, name="管理员", username="studyadmin",
                  salt=salt, password_hash=auth.hash_password("pw123456", salt),
                  admin=True))
    s.commit()
    s.close()
    r = c.post("/auth/login", json={"username": "studyadmin", "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_me_study_plan_self_mark_and_state(client):
    """混合派发全链路：老师确认班级行（派发）→ 学生生成方案（模板）→
    自报 → 折叠态自报待检验。班级行不由学生自报消化（row_done=False）。"""
    c, S = client
    env = _api_seed(S)
    sid, H = _login_student(c, S, env, "T01", "studyt01")
    cid = env["class_id"]

    # 非薄弱点（U 未教 → 未学到、无挂起建议）→ 400
    r = c.get("/me/study-plan", params={"kp_code": "U"}, headers=H)
    assert r.status_code == 400

    # 薄弱点 P1 → 模板方案（LLM 关；触发源 = 班级 reteach 行）
    r = c.get("/me/study-plan", params={"kp_code": "P1"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kp_code"] == "P1" and body["plan_markdown"]
    assert body["plan_writer"] == {"template": True}
    assert body["self_marked_at"] is None
    rec_id = body["id"]

    # 幂等复用：再次查看同一记录
    r2 = c.get("/me/study-plan", params={"kp_code": "P1"}, headers=H)
    assert r2.json()["id"] == rec_id

    # 老师「派发」：确认班级 reteach 行 → done，队列积压减一
    s = S()
    cls_row = s.scalar(
        select(Intervention).where(
            Intervention.class_id == cid,
            Intervention.student_id.is_(None),
            Intervention.status == "suggested",
            Intervention.kind == "reteach",
        )
    )
    assert cls_row is not None
    s.close()
    before = c.get(f"/classes/{cid}/action-plan").json()["pending_confirm"]
    r = c.post(f"/interventions/{cls_row.id}/confirm", json=None)
    assert r.status_code == 200, r.text
    after = c.get(f"/classes/{cid}/action-plan").json()["pending_confirm"]
    assert after == before - 1

    # 学生自报：只落事实，不消化班级行（row_done=False）
    r = c.post(f"/me/study-records/{rec_id}/self-mark", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["row_done"] is False

    # 折叠态：班级行 done + 当前轮自报 → 自报待检验
    r = c.get("/me/study-records", headers=H)
    assert r.status_code == 200
    rec = next(x for x in r.json()["records"] if x["id"] == rec_id)
    assert rec["self_marked_at"]
    assert rec["loop_state"] == LOOP_SELF_REPORTED

    # 不存在的记录 → 404
    assert c.post("/me/study-records/999999/self-mark", headers=H).status_code == 404


def test_preview_readonly_and_student_forbidden(client):
    """预览严格只读（无记录 404 且绝不触发生成）；学生 token 撞越权面 → 403。"""
    c, S = client
    env = _api_seed(S)
    sid, H = _login_student(c, S, env, "T01", "studyt02")
    AH = _login_admin(c, S)

    # 学生撞 admin 预览（require_admin）与教师 confirm（中间件白名单）
    r = c.get(f"/admin/students/{sid}/portal/study-records", headers=H)
    assert r.status_code == 403
    r = c.post("/interventions/1/confirm", headers=H)
    assert r.status_code == 403

    # 预览只读：无记录 → 404 且库里零生成
    r = c.get(f"/admin/students/{sid}/portal/study-plan",
              params={"kp_code": "P1"}, headers=AH)
    assert r.status_code == 404, r.text
    s = S()
    assert s.query(StudyRecord).count() == 0, "预览绝不触发生成"
    s.close()

    # 学生 self 端点生成后 → 预览可见同一记录（只读）
    r = c.get("/me/study-plan", params={"kp_code": "P1"}, headers=H)
    assert r.status_code == 200
    rec_id = r.json()["id"]
    r = c.get(f"/admin/students/{sid}/portal/study-plan",
              params={"kp_code": "P1"}, headers=AH)
    assert r.status_code == 200 and r.json()["id"] == rec_id
    assert r.json()["plan_markdown"]
