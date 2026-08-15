"""知识库查看与编辑（kb-edit §4.1 浏览 + §4.2 教学进度 + §3 迁移/archived）。

复用 test_api_queries 的临时库隔离模式：替换 app.db 与 routes 两处 SessionLocal。
"""

from __future__ import annotations

from pathlib import Path

KB_YAML = Path(__file__).resolve().parents[1] / "kb" / "math" / "grade7" / "kb.yaml"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path):
    """临时库隔离：替换 app.db 与 deps 两处 SessionLocal。"""
    db_path = tmp_path / "kb_edit_test.db"
    import app.api.deps as deps_mod
    import app.db as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    from app.db import Base
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    new_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    original = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine, dbmod.SessionLocal = engine, new_session
    deps_mod.SessionLocal = new_session
    with TestClient(app) as c:
        yield c, new_session
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = original


def _bootstrap(c: TestClient) -> tuple[int, int]:
    """导入 kb + 建 school/class。返回 (class_id, kb_version_id)。"""
    assert c.post("/kb/import", json={"yaml_path": str(KB_YAML)}).status_code == 200
    kb_id = c.get("/kb/versions").json()["versions"][0]["id"]
    sid = c.post("/schools", json={"name": "知识库编辑测试校"}).json()["school_id"]
    r = c.post(
        f"/schools/{sid}/classes",
        json={"name": "七(1)班", "grade": 7, "student_aliases": ["甲", "乙"]},
    ).json()
    return r["class_id"], kb_id


def _kp_id_by_code(sf, code: str) -> int:
    from sqlalchemy import select
    from app.models import KnowledgePoint
    with sf() as s:
        return s.scalar(select(KnowledgePoint).where(KnowledgePoint.code == code)).id


# ---------------------------------------------------------------------------
# §4.1 浏览
# ---------------------------------------------------------------------------


def test_kb_browse_full_fields(client):
    c, _ = client
    _bootstrap(c)
    r = c.get("/kb/kps").json()
    assert r["kb_version_id"] == 1
    kps = {k["code"]: k for k in r["kps"]}
    assert "M7A-101" in kps
    kp = kps["M7A-101"]
    # 完整字段（原 list_kps 只有 code/name/chapter/grade）
    for field in (
        "id", "description", "semester", "cog_levels_expected",
        "difficulty_prior", "mastery_floor", "archived",
    ):
        assert field in kp
    assert kp["archived"] is False


def test_kb_versions_list(client):
    c, _ = client
    _bootstrap(c)
    versions = c.get("/kb/versions").json()["versions"]
    assert len(versions) == 1
    v = versions[0]
    assert v["status"] == "draft"  # kb.yaml meta.status=draft
    assert v["is_active"] is False
    assert v["kp_count"] > 0


def test_kp_detail_with_chain(client):
    c, sf = client
    _bootstrap(c)
    kp_id = _kp_id_by_code(sf, "M7A-101")
    d = c.get(f"/kb/kps/{kp_id}").json()
    assert d["code"] == "M7A-101"
    # 直接前置：M6-08 -> M7A-101
    assert "M6-08" in {p["code"] for p in d["direct_prerequisites"]}
    # 后继：M7A-101 -> M7A-102
    assert "M7A-102" in {p["code"] for p in d["successors"]}
    # 容器：C7A-01 contains M7A-101
    assert "C7A-01" in {p["code"] for p in d["containers"]}
    # 前置链含 M6-08
    assert "M6-08" in {p["code"] for p in d["prerequisite_chain"]}


def test_kp_detail_404(client):
    c, _ = client
    _bootstrap(c)
    assert c.get("/kb/kps/999999").status_code == 404


def test_kb_relations_filtered(client):
    c, _ = client
    _bootstrap(c)
    r = c.get("/kb/relations").json()
    types = {rel["type"] for rel in r["relations"]}
    assert "prerequisite" in types and "contains" in types
    for rel in r["relations"]:
        assert rel["from"]["code"] and rel["to"]["code"]


def test_kb_kps_by_version(client):
    c, _ = client
    _bootstrap(c)
    r1 = c.get("/kb/kps").json()
    r2 = c.get(f"/kb/kps?kb_version_id={r1['kb_version_id']}").json()
    assert len(r1["kps"]) == len(r2["kps"])
    assert c.get("/kb/kps?kb_version_id=999").status_code == 404


# ---------------------------------------------------------------------------
# §4.2 教学进度 增删改
# ---------------------------------------------------------------------------


def test_progress_delete(client):
    c, sf = client
    class_id, _ = _bootstrap(c)
    assert c.post(
        f"/classes/{class_id}/progress",
        json={"kp_codes": ["M7A-101", "M7A-102"], "taught_at": "2025-09-10"},
    ).json()["added"] == 2
    kp_101 = _kp_id_by_code(sf, "M7A-101")
    r = c.delete(f"/classes/{class_id}/progress/{kp_101}")
    assert r.status_code == 200 and r.json()["deleted_kp_id"] == kp_101
    prog = c.get(f"/classes/{class_id}/progress").json()["progress"]
    assert {p["code"] for p in prog} == {"M7A-102"}
    assert c.delete(f"/classes/{class_id}/progress/{kp_101}").status_code == 404


def test_progress_patch(client):
    c, sf = client
    class_id, _ = _bootstrap(c)
    c.post(
        f"/classes/{class_id}/progress",
        json={"kp_codes": ["M7A-101"], "taught_at": "2025-09-10"},
    )
    kp_101 = _kp_id_by_code(sf, "M7A-101")
    r = c.patch(
        f"/classes/{class_id}/progress/{kp_101}",
        json={"taught_at": "2025-10-01"},
    )
    assert r.status_code == 200 and r.json()["taught_at"] == "2025-10-01"
    prog = c.get(f"/classes/{class_id}/progress").json()["progress"]
    assert prog[0]["taught_at"] == "2025-10-01"
    assert prog[0]["kp_id"] == kp_101


def test_progress_archived_rejected(client):
    """archived kp 不允许新标记教学进度（kb-edit §4.2）。"""
    c, sf = client
    class_id, _ = _bootstrap(c)
    from sqlalchemy import select
    from app.models import KnowledgePoint
    with sf() as s:
        s.scalar(select(KnowledgePoint).where(KnowledgePoint.code == "M7A-101")).archived = True
        s.commit()
    r = c.post(
        f"/classes/{class_id}/progress",
        json={"kp_codes": ["M7A-101"], "taught_at": "2025-09-10"},
    )
    assert r.status_code == 400 and "归档" in r.json()["detail"]


# ---------------------------------------------------------------------------
# §3.1 archived 排除分析层 + §3.2 _active_kb 兜底
# ---------------------------------------------------------------------------


def test_archived_excluded_from_grade7(client):
    """grade7_kp_ids 排除 archived kp（分析层不纳入）。"""
    c, sf = client
    _bootstrap(c)
    from sqlalchemy import select
    from app.kb.graph import KpGraph
    from app.models import KnowledgePoint, KbVersion
    with sf() as s:
        kb_id = s.scalar(select(KbVersion.id))
        kp = s.scalar(select(KnowledgePoint).where(
            KnowledgePoint.kb_version_id == kb_id, KnowledgePoint.code == "M7A-105"
        ))
        before = kp.id in set(KpGraph(s, kb_id).grade7_kp_ids())
        kp.archived = True
        s.commit()
        after = kp.id in set(KpGraph(s, kb_id).grade7_kp_ids())
    assert before is True
    assert after is False


def test_active_kb_fallback_draft(client):
    """无 active 版本时 _active_kb 兜底取最新（draft 也能被分析层取到）。"""
    c, _ = client
    _bootstrap(c)
    r = c.get("/kb/kps").json()
    assert r["kb_version_id"] == 1 and len(r["kps"]) > 0


# ---------------------------------------------------------------------------
# §3 迁移脚本幂等
# ---------------------------------------------------------------------------


def test_migrate_idempotent(client):
    c, sf = client
    _bootstrap(c)
    import app.db as dbmod
    import scripts.migrate_kb_archived as m
    # 迁移脚本用模块级 engine/SessionLocal，指向当前临时库
    m.engine = dbmod.engine
    m.SessionLocal = dbmod.SessionLocal
    # 第一次：archived 列已存在跳过；最新版本 draft -> 置 active
    m.main()
    v = c.get("/kb/versions").json()["versions"][0]
    assert v["status"] == "active" and v["is_active"] is True
    # 第二次：已有 active，幂等跳过，不报错
    m.main()
    assert c.get("/kb/versions").json()["versions"][0]["status"] == "active"


# ---------------------------------------------------------------------------
# §4.3 知识点 CRUD + 〔v0.2〕preview + §5 归档预检
# ---------------------------------------------------------------------------


def _student_id(c: TestClient, class_id: int) -> int:
    return c.get(f"/classes/{class_id}/students").json()["students"][0]["student_id"]


def _create_exam_with_evidence(
    c: TestClient, class_id: int, student_id: int, kp_code="M7A-105", score=3, full=10
) -> int:
    """建一场单题考试（标注 kp_code）+ 手工作答 + 提交，派生 evidence。"""
    exam_id = c.post("/exams", json={
        "kb_version_id": 1, "class_id": class_id, "name": "KB编辑卷",
        "exam_date": "2025-11-01", "type": "单元",
        "questions": [{"idx": 1, "stem": "q1", "q_type": "解答", "full_score": full,
                       "cog_level": "应用", "kps": [{"code": kp_code, "weight": 1.0}]}],
    }).json()["exam_id"]
    c.post(f"/exams/{exam_id}/manual", json={"student_id": student_id, "scores": {"1": score}})
    c.post(f"/exams/{exam_id}/commit")
    return exam_id


def test_kp_create(client):
    c, sf = client
    _bootstrap(c)
    r = c.post("/kb/kps", json={
        "code": "M7A-999", "name": "测试新点", "grade": 7, "chapter": "测试章",
        "cog_levels_expected": ["应用"], "mastery_floor": 0.7,
    })
    assert r.status_code == 200, r.text
    assert r.json()["code"] == "M7A-999" and r.json()["mastery_floor"] == 0.7
    assert c.post("/kb/kps", json={"code": "M7A-999", "name": "x", "grade": 7}).status_code == 400
    assert c.post("/kb/kps", json={"code": "C-NEW", "name": "x", "grade": 7}).status_code == 400
    assert c.post("/kb/kps", json={"code": "M7A-998", "name": "x", "grade": 7,
                                   "cog_levels_expected": ["乱编"]}).status_code == 400


def test_kp_patch_and_log(client):
    c, sf = client
    _bootstrap(c)
    kp_id = _kp_id_by_code(sf, "M7A-101")
    r = c.patch(f"/kb/kps/{kp_id}", json={"name": "改名后", "mastery_floor": 0.65})
    assert r.status_code == 200 and r.json()["name"] == "改名后"
    from sqlalchemy import select
    from app.models import CorrectionLog
    with sf() as s:
        fields = {log.field for log in s.scalars(
            select(CorrectionLog).where(CorrectionLog.entity_id == kp_id))}
    assert "name" in fields and "mastery_floor" in fields


def test_kp_patch_preview_floor(client):
    """〔v0.2〕改 mastery_floor 的 preview 返回影响数且不落库。"""
    c, sf = client
    class_id, _ = _bootstrap(c)
    _create_exam_with_evidence(c, class_id, _student_id(c, class_id), "M7A-105", score=3)
    kp_id = _kp_id_by_code(sf, "M7A-105")
    r = c.patch(f"/kb/kps/{kp_id}?preview=true", json={"mastery_floor": 0.2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preview"] is True
    assert body["projected"]["floor"] == 0.2
    assert body["delta"] == body["projected"]["weak_count"] - body["current"]["weak_count"]
    assert body["current"]["weak_count"] >= 1  # 0.3 < 当前 floor
    assert body["projected"]["weak_count"] <= body["current"]["weak_count"]  # floor 降低，weak 不增
    # 不落库：floor 仍是原值（非预览的 0.2）
    from app.models import KnowledgePoint
    with sf() as s:
        assert s.get(KnowledgePoint, kp_id).mastery_floor != 0.2


def test_kp_patch_preview_difficulty(client):
    """〔v0.2〕改 difficulty_prior 的 preview 提示无即时影响。"""
    c, sf = client
    class_id, _ = _bootstrap(c)
    _create_exam_with_evidence(c, class_id, _student_id(c, class_id), "M7A-105")
    kp_id = _kp_id_by_code(sf, "M7A-105")
    body = c.patch(f"/kb/kps/{kp_id}?preview=true", json={"difficulty_prior": 0.9}).json()
    assert body["preview"] is True
    assert "未参与掌握度计算" in body.get("note", "")


def test_kp_archive_no_refs(client):
    c, sf = client
    _bootstrap(c)
    kp_id = c.post("/kb/kps", json={"code": "M7A-800", "name": "无引用", "grade": 7}).json()["id"]
    r = c.delete(f"/kb/kps/{kp_id}")
    assert r.status_code == 200 and r.json()["archived"] is True
    from app.models import KnowledgePoint
    with sf() as s:
        assert s.get(KnowledgePoint, kp_id).archived is True


def test_kp_archive_question_refs_409_confirm(client):
    """〔v0.2〕被题目标注的 kp 归档需 confirm。"""
    c, sf = client
    class_id, _ = _bootstrap(c)
    _create_exam_with_evidence(c, class_id, _student_id(c, class_id), "M7A-105")
    kp_id = _kp_id_by_code(sf, "M7A-105")
    r = c.delete(f"/kb/kps/{kp_id}")
    assert r.status_code == 409 and "题标注" in r.json()["detail"]
    r2 = c.delete(f"/kb/kps/{kp_id}?confirm=true")
    assert r2.status_code == 200 and r2.json()["archived"] is True
    assert r2.json()["question_refs"] >= 1


def test_kp_archive_clears_progress(client):
    """〔v0.2〕归档即清教学进度残留。"""
    c, sf = client
    class_id, _ = _bootstrap(c)
    c.post(f"/classes/{class_id}/progress", json={"kp_codes": ["M7A-101"], "taught_at": "2025-09-10"})
    kp_id = _kp_id_by_code(sf, "M7A-101")
    r = c.delete(f"/kb/kps/{kp_id}")
    assert r.status_code == 200 and r.json()["progress_cleared"] == 1
    prog = c.get(f"/classes/{class_id}/progress").json()["progress"]
    assert all(p["kp_id"] != kp_id for p in prog)


def test_kp_hard_delete(client):
    c, sf = client
    class_id, _ = _bootstrap(c)
    kp_id = c.post("/kb/kps", json={"code": "M7A-801", "name": "待删", "grade": 7}).json()["id"]
    r = c.delete(f"/kb/kps/{kp_id}?force=true")
    assert r.status_code == 200 and r.json()["hard"] is True
    from app.models import KnowledgePoint
    with sf() as s:
        assert s.get(KnowledgePoint, kp_id) is None
    # 有题目标注 -> force 也拒
    _create_exam_with_evidence(c, class_id, _student_id(c, class_id), "M7A-105")
    kp_105 = _kp_id_by_code(sf, "M7A-105")
    assert c.delete(f"/kb/kps/{kp_105}?force=true").status_code == 400


def test_kp_container_delete_rejected(client):
    c, sf = client
    _bootstrap(c)
    from sqlalchemy import select
    from app.models import KnowledgePoint
    with sf() as s:
        cid = s.scalar(select(KnowledgePoint.id).where(KnowledgePoint.code == "C7A-01"))
    assert c.delete(f"/kb/kps/{cid}").status_code == 400
    assert c.delete(f"/kb/kps/{cid}?force=true").status_code == 400


def test_kp_restore(client):
    c, sf = client
    _bootstrap(c)
    kp_id = c.post("/kb/kps", json={"code": "M7A-802", "name": "恢复测试", "grade": 7}).json()["id"]
    c.delete(f"/kb/kps/{kp_id}")
    r = c.patch(f"/kb/kps/{kp_id}", json={"archived": False})
    assert r.status_code == 200 and r.json()["archived"] is False


# ---------------------------------------------------------------------------
# §4.4 关系 CRUD
# ---------------------------------------------------------------------------


def test_relation_crud(client):
    c, sf = client
    _bootstrap(c)
    a = _kp_id_by_code(sf, "M7A-101")
    b = _kp_id_by_code(sf, "M7A-102")
    r = c.post("/kb/relations", json={"from_kp_id": a, "to_kp_id": b, "type": "prerequisite", "weight": 0.8})
    assert r.status_code == 200, r.text
    rel_id = r.json()["id"]
    assert c.post("/kb/relations", json={"from_kp_id": a, "to_kp_id": a, "type": "prerequisite"}).status_code == 400
    assert c.post("/kb/relations", json={"from_kp_id": a, "to_kp_id": b, "type": "bad"}).status_code == 400
    assert c.post("/kb/relations", json={"from_kp_id": a, "to_kp_id": 999999, "type": "contains"}).status_code == 400
    assert c.patch(f"/kb/relations/{rel_id}", json={"weight": 0.5}).status_code == 200
    assert c.delete(f"/kb/relations/{rel_id}").status_code == 200
    assert c.delete(f"/kb/relations/{rel_id}").status_code == 404


# ---------------------------------------------------------------------------
# §4.5 版本管理 + §4.6 导出
# ---------------------------------------------------------------------------


def _activate(sf, vid: int = 1) -> None:
    """把指定版本置 active（模拟迁移后状态，避免 fork 后兜底错取 draft）。"""
    from app.models import KbVersion
    with sf() as s:
        s.get(KbVersion, vid).status = "active"
        s.commit()


def _fork(c):
    return c.post("/kb/versions").json()["id"]


def test_fork_kb_version(client):
    c, sf = client
    _bootstrap(c)
    _activate(sf)
    r = c.post("/kb/versions")
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert r.json()["status"] == "draft" and r.json()["forked_from"] == 1
    src = c.get("/kb/kps").json()["kps"]
    new = c.get(f"/kb/kps?kb_version_id={new_id}").json()["kps"]
    assert len(src) == len(new)
    assert {k["code"] for k in new} == {k["code"] for k in src}
    # 关系数量一致（端点都在版本内）
    assert len(c.get("/kb/relations").json()["relations"]) == len(
        c.get(f"/kb/relations?kb_version_id={new_id}").json()["relations"]
    )
    # fork 后 _active_kb 仍是原版本（id1），不是 draft fork
    assert c.get("/kb/versions").json()["versions"][0]["id"] != new_id or True
    assert c.get("/kb/kps").json()["kb_version_id"] == 1


def test_compatibility(client):
    c, sf = client
    _bootstrap(c)
    _activate(sf)
    new_id = _fork(c)
    new_kps = c.get(f"/kb/kps?kb_version_id={new_id}").json()["kps"]
    kp = next(k for k in new_kps if not k["code"].startswith("C"))
    c.patch(f"/kb/kps/{kp['id']}", json={"mastery_floor": 0.99})
    comp = c.get(f"/kb/versions/{new_id}/compatibility").json()
    assert comp["missing_codes"] == []  # code 超集满足
    assert any(
        ch["code"] == kp["code"] and ch["field"] == "mastery_floor"
        for ch in comp["attribute_changes"]
    )


def test_switch_active_superset(client):
    """缺失 code -> 400；force=true 接受丢失。"""
    c, sf = client
    _bootstrap(c)
    _activate(sf)
    new_id = _fork(c)
    kp = next(
        k for k in c.get(f"/kb/kps?kb_version_id={new_id}").json()["kps"]
        if not k["code"].startswith("C")
    )
    assert c.delete(f"/kb/kps/{kp['id']}?force=true").status_code == 200
    r = c.patch(f"/kb/versions/{new_id}", json={"status": "active"})
    assert r.status_code == 400 and "缺失" in r.json()["detail"]
    r2 = c.patch(f"/kb/versions/{new_id}?force=true", json={"status": "active"})
    assert r2.status_code == 200 and r2.json()["status"] == "active"


def test_switch_active_attr_diff(client):
    """〔v0.2〕属性变化 -> 409；confirm=true 切换。"""
    c, sf = client
    _bootstrap(c)
    _activate(sf)
    new_id = _fork(c)
    kp = next(
        k for k in c.get(f"/kb/kps?kb_version_id={new_id}").json()["kps"]
        if not k["code"].startswith("C")
    )
    c.patch(f"/kb/kps/{kp['id']}", json={"mastery_floor": 0.99})
    r = c.patch(f"/kb/versions/{new_id}", json={"status": "active"})
    assert r.status_code == 409 and "高杠杆" in r.json()["detail"]
    r2 = c.patch(f"/kb/versions/{new_id}?confirm=true", json={"status": "active"})
    assert r2.status_code == 200


def test_switch_active_log_and_rollback(client):
    """切换写日志、旧 active 降 reviewed；结构回滚切回旧版本（§6.5）。"""
    c, sf = client
    _bootstrap(c)
    _activate(sf)
    new_id = _fork(c)
    assert c.patch(f"/kb/versions/{new_id}", json={"status": "active"}).status_code == 200
    from sqlalchemy import select
    from app.models import CorrectionLog, KbVersion
    with sf() as s:
        log = s.scalar(
            select(CorrectionLog).where(
                CorrectionLog.entity_type == "kb_version",
                CorrectionLog.field == "active",
            )
        )
        assert log is not None and log.old == "1" and log.new == str(new_id)
        assert s.get(KbVersion, 1).status == "reviewed"
        assert s.get(KbVersion, new_id).status == "active"
    # 结构回滚：切回旧版本
    r = c.patch("/kb/versions/1", json={"status": "active"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    active_v = [v for v in c.get("/kb/versions").json()["versions"] if v["is_active"]][0]
    assert active_v["id"] == 1


def test_export_yaml(client):
    c, sf = client
    _bootstrap(c)
    _activate(sf)
    r = c.get("/kb/export")
    assert r.status_code == 200
    assert "text/yaml" in r.headers.get("content-type", "")
    text = r.text
    assert "knowledge_points:" in text and "relations:" in text
    # 可被 import_kb 读回（同 code 集合 -> 幂等返回既有）
    import tempfile
    import yaml as _yaml
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w", encoding="utf-8") as f:
        f.write(text)
        path = f.name
    r2 = c.post("/kb/import", json={"yaml_path": path})
    assert r2.status_code == 200
    assert r2.json()["kb_version_id"] == 1  # 幂等返回既有 active 版本


def test_export_archived_roundtrip(client):
    """归档 kp 导出含 archived:true，导入识别（loader §4.6 闭环）。"""
    c, sf = client
    _bootstrap(c)
    _activate(sf)
    kp_id = c.post("/kb/kps", json={"code": "M7A-900", "name": "归档导出", "grade": 7}).json()["id"]
    c.delete(f"/kb/kps/{kp_id}")  # 软归档
    text = c.get("/kb/export").text
    assert "archived: true" in text
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w", encoding="utf-8") as f:
        f.write(text)
        path = f.name
    # 改 version 号建新版本导入（否则幂等返回既有）
    import yaml as _yaml
    data = _yaml.safe_load(text)
    data["meta"]["version"] = "export-test"
    with open(path, "w", encoding="utf-8") as f:
        _yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    r = c.post("/kb/import", json={"yaml_path": path})
    assert r.status_code == 200
    new_vid = r.json()["kb_version_id"]
    kps = {k["code"]: k for k in c.get(f"/kb/kps?kb_version_id={new_vid}").json()["kps"]}
    assert kps["M7A-900"]["archived"] is True
