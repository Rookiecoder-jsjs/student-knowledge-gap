"""Phase 3 出口判据端到端验收（agent-product-design §10.1 Phase 3）。

三条出口判据的测试化：
① 教师甲看不到教师乙的班 → tests/test_auth.py（单元+API+MCP 三层）
② 干预记录经签发落库 → 本文件：写工具 suggested → 教师确认 done →
   效果可查（审批门全链路，真实 HTTP）
③ 预算闸生效 → gateway/test_budget.py（轮数/token/月度三道闸）

本文件补 ② 的 HTTP 全链路 + ① 的收件箱隔离（甲看不到乙班草稿）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth
from app.db import Base
from app.models import (
    Class,
    Intervention,
    KbVersion,
    KnowledgePoint,
    School,
    Student,
    Teacher,
    TeacherClass,
    TeachingProgress,
)

_ENGINE = None


@pytest.fixture()
def client(tmp_path):
    """两教师两班的安全模式环境；乙班有一场已提交考试（干预归属用）。"""
    global _ENGINE
    db_file = tmp_path / "phase3-e2e.db"
    eng = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False, autoflush=False)

    from app.api import deps as deps_mod
    from app import db as dbmod
    from app.main import app

    old = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine = eng
    dbmod.SessionLocal = S
    deps_mod.SessionLocal = S

    s = S()
    school = School(name="学校")
    s.add(school)
    s.flush()
    kb = KbVersion(subject="数学", textbook_edition="t", version="1")
    s.add(kb)
    s.flush()
    kp = KnowledgePoint(
        kb_version_id=kb.id, code="P1", name="基础点", grade=7, semester=1,
        chapter="章", cog_levels_expected=["应用"], difficulty_prior=0.5,
        mastery_floor=0.6,
    )
    s.add(kp)
    s.flush()
    c_a = Class(school_id=school.id, name="甲班", grade=7, subject="数学")
    c_b = Class(school_id=school.id, name="乙班", grade=7, subject="数学")
    s.add_all([c_a, c_b])
    s.flush()
    stu_a = Student(school_id=school.id, class_id=c_a.id, name_or_alias="学生A")
    stu_b = Student(school_id=school.id, class_id=c_b.id, name_or_alias="学生B")
    s.add_all([stu_a, stu_b])
    s.flush()
    from datetime import date

    from app.models import ExamTemplate

    for c in (c_a, c_b):
        s.add(TeachingProgress(class_id=c.id, kp_id=kp.id,
                               taught_at=date(2025, 9, 1)))
        s.add(ExamTemplate(class_id=c.id, name="单元测", exam_date=date(2025, 10, 15),
                           type="单元"))
    jia = Teacher(
        school_id=school.id, name="甲老师", username="jia",
        salt=b"0" * 16, password_hash=auth.hash_password("pass123", b"0" * 16),
    )
    yi = Teacher(
        school_id=school.id, name="乙老师", username="yi",
        salt=b"1" * 16, password_hash=auth.hash_password("pass123", b"1" * 16),
    )
    s.add_all([jia, yi])
    s.flush()
    s.add(TeacherClass(teacher_id=jia.id, class_id=c_a.id))
    s.add(TeacherClass(teacher_id=yi.id, class_id=c_b.id))
    s.commit()
    ids = {"c_a": c_a.id, "c_b": c_b.id, "stu_a": stu_a.id, "stu_b": stu_b.id}
    s.close()

    auth.reset_mode_cache_for_tests()
    with TestClient(app) as c:
        yield c, S, ids
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = old
    eng.dispose()
    auth.reset_mode_cache_for_tests()


def _login(c, username):
    r = c.post("/auth/login", json={"username": username, "password": "pass123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_exit_criteria_inbox_isolation(client):
    """判据①延伸：收件箱草稿也按授权班级收敛——甲看不到乙班的待签发单。"""
    c, S, ids = client
    h_jia, h_yi = _login(c, "jia"), _login(c, "yi")
    with S() as s:
        from app.models import Report

        s.add(Report(type="student_diagnosis", class_id=ids["c_b"],
                     student_id=ids["stu_b"], content_markdown="# 乙班诊断",
                     status="draft"))
        s.commit()

    assert c.get("/inbox", headers=h_yi).json()["total"] == 1
    assert c.get("/inbox", headers=h_jia).json()["total"] == 0


def test_exit_criteria_intervention_through_approval_gate(client):
    """判据②：Agent 写工具产 suggested → 教师确认 done → 效果可查。

    「经签发落库」= 干预行必须经过教师在行动明细里的确认动作才算执行事实，
    Agent 无法直接写终态（confirm 端点是唯一通道，且要求授权教师身份）。
    """
    from datetime import datetime, timedelta

    c, S, ids = client
    h_jia = _login(c, "jia")

    # Agent（MCP 兜底路线模拟）：以乙班学生为目标登记建议——甲无权（403 语义在
    # 工具层由 SC_MCP_TEACHER_ID 裁决）；这里直接验证甲对自己学生的完整链路。
    with S() as s:
        kb_id = s.query(KbVersion).first().id
        from app.kb.graph import KpGraph

        graph = KpGraph(s, kb_id)
        from app.mcp_tools import record_intervention

        # 模拟 MCP 进程注入甲的身份
        import os

        old_env = os.environ.get("SC_MCP_TEACHER_ID")
        jia_id = s.query(Teacher).filter_by(username="jia").first().id
        os.environ["SC_MCP_TEACHER_ID"] = str(jia_id)
        try:
            with pytest.raises(Exception) as ei:
                # 甲试图给乙班学生登记 → 工具层同一裁决拒绝
                record_intervention(
                    s, graph, student_id=ids["stu_b"], kp_code="P1",
                    kind="spaced_review", exam_id=_exam_of(S, ids["c_b"]),
                )
            assert "无权访问" in str(ei.value)

            r = record_intervention(
                s, graph, student_id=ids["stu_a"], kp_code="P1",
                kind="spaced_review", exam_id=_exam_of(S, ids["c_a"]),
                note="调查后建议间隔复习",
            )
            s.commit()
        finally:
            if old_env is None:
                os.environ.pop("SC_MCP_TEACHER_ID", None)
            else:
                os.environ["SC_MCP_TEACHER_ID"] = old_env
    iv_id = r["intervention_id"]
    assert r["status"] == "suggested"

    # 教师视角：行动明细里可见（甲只看到自己班的），确认执行
    rows = c.get("/interventions", headers=h_jia).json()["items"]
    assert [x["id"] for x in rows] == [iv_id]
    ok = c.post(f"/interventions/{iv_id}/confirm", headers=h_jia, json={})
    assert ok.status_code == 200 and ok.json()["status"] == "done"

    # 效果端点可达（无复测证据 → awaiting_retest，分母口径保护正常）
    eff = c.get(f"/interventions/{iv_id}/effect", headers=h_jia).json()
    assert eff.get("effect_status") == "awaiting_retest"

    # 乙老师看不到甲班的干预记录与效果
    h_yi = _login(c, "yi")
    assert c.get("/interventions", headers=h_yi).json()["items"] == []
    denied = c.get(f"/interventions/{iv_id}/effect", headers=h_yi)
    assert denied.status_code == 403


def test_exit_criteria_budget_gate_active():
    """判据③：护栏闸默认值就位且生效（详见 gateway/test_budget.py）。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from gateway.budget import (
        GUARD,
        MAX_TOKENS_PER_TASK,
        MAX_TURNS_PER_TASK,
        BudgetGuard,
    )

    assert MAX_TURNS_PER_TASK == 12 and MAX_TOKENS_PER_TASK == 200_000
    fresh = BudgetGuard()  # 不污染进程级 GUARD
    for i in range(MAX_TURNS_PER_TASK):
        fresh.check_turn_start("exit-t")
    from gateway.budget import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        fresh.check_turn_start("exit-t")


def _exam_of(S, class_id):
    with S() as s:
        return s.scalar(
            __import__("sqlalchemy").select(
                __import__("app.models", fromlist=["ExamTemplate"]).ExamTemplate.id
            ).where(
                __import__("app.models", fromlist=["ExamTemplate"]).ExamTemplate.class_id
                == class_id
            )
        )
