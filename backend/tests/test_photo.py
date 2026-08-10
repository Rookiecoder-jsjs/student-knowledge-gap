"""拍照解析路径：两阶段流水线 + 审核闸门（DESIGN §5，不变量③）。"""

from __future__ import annotations

import io
import os
from pathlib import Path

KB_YAML = Path(__file__).resolve().parents[1] / "kb" / "math" / "grade7" / "kb.yaml"

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.llm.client import LLMError, MockLLMClient, set_client
from app.main import app

STAGE_A_PAYLOAD = {
    "questions": [
        {
            "idx": 1,
            "stem": "下列关于绝对值的说法正确的是",
            "q_type": "选择",
            "full_score": 5,
            "cog_level": "理解",
            "n_options": 4,
            "kp_tags": [{"code": "M7A-105", "weight": 1.0}],
            "confidence": 0.92,
        },
        {
            "idx": 2,
            "stem": "计算：(-3)+5",
            "q_type": "解答",
            "full_score": 10,
            "cog_level": "应用",
            "n_options": None,
            "kp_tags": [{"code": "M7A-111", "weight": 1.0}],
            "confidence": 0.55,
        },
        {
            "idx": 3,
            "stem": "标注了不存在知识点的题",
            "q_type": "解答",
            "full_score": 8,
            "cog_level": "应用",
            "kp_tags": [{"code": "FAKE-999", "weight": 1.0}],
            "confidence": 0.8,
        },
    ]
}

STAGE_B_PAYLOAD = {
    "answers": [
        {"idx": 1, "score": 5, "chosen_option": "B", "confidence": 0.95},
        {"idx": 2, "score": 4, "chosen_option": None, "confidence": 0.5},
        {"idx": 3, "score": 8, "chosen_option": None, "confidence": 0.99},
    ]
}


def _jpeg_bytes() -> bytes:
    img = Image.new("RGB", (200, 300), color=(250, 250, 250))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path):
    """隔离临时库：同时替换 app.db 与 routes 模块内的 SessionLocal 引用。"""
    db_path = tmp_path / "photo_test.db"
    import app.api.routes as routes_mod
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
        yield c
    dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = original
    set_client(None)


def _bootstrap(client: TestClient) -> int:
    assert client.post("/kb/import", json={"yaml_path": str(KB_YAML)}).status_code == 200
    sid = client.post("/schools", json={"name": "拍照测试校"}).json()["school_id"]
    r = client.post(
        f"/schools/{sid}/classes",
        json={"name": "七(3)班", "grade": 7, "student_aliases": ["P01", "P02"]},
    )
    return r.json()["student_ids"][0]


def test_stage_a_creates_template_with_llm_tags(client):
    student_id = _bootstrap(client)
    mock = MockLLMClient([STAGE_A_PAYLOAD])
    set_client(mock)

    r = client.post(
        "/exams/photo-template",
        files={"file": ("paper.jpg", _jpeg_bytes(), "image/jpeg")},
        data={"class_id": 1, "name": "拍照月考", "exam_date": "2025-10-20", "type": "单元"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["questions"] == 3
    # 闭集校验：FAKE-999 被丢弃并告警
    assert any("FAKE-999" in w for w in body["warnings"])
    assert any("题3" in w and "补标" in w for w in body["warnings"])
    assert mock.calls and mock.calls[0]["has_image"] is True

    # parse_job 记录了模型与 prompt 版本
    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import ParseJob, QuestionKp

    with SessionLocal() as s:
        job = s.scalar(select(ParseJob))
        assert job.model_version == "mock-vision-v0"
        assert job.prompt_version.startswith("parse-v")
        tags = list(s.scalars(select(QuestionKp)))
        assert all(t.source == "LLM" for t in tags)
        assert all(t.reviewed_at is None for t in tags)  # 未过审核闸门


def test_stage_b_and_review_flow(client):
    student_id = _bootstrap(client)
    mock = MockLLMClient([STAGE_A_PAYLOAD, STAGE_B_PAYLOAD])
    set_client(mock)

    tpl = client.post(
        "/exams/photo-template",
        files={"file": ("paper.jpg", _jpeg_bytes(), "image/jpeg")},
        data={"class_id": 1, "name": "拍照月考2", "exam_date": "2025-10-21", "type": "单元"},
    ).json()
    exam_id = tpl["exam_id"]

    r = client.post(
        f"/exams/{exam_id}/photo-response",
        files={"file": ("student.jpg", _jpeg_bytes(), "image/jpeg")},
        data={"student_id": student_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["response_id"]

    # 审核队列：未审标注 + 低置信得分（0.5 → 强制人工）
    queue = client.get(f"/exams/{exam_id}/review-queue").json()
    assert len(queue["unreviewed_tags"]) == 2
    bands = {a["confidence"]: a["band"] for a in queue["low_confidence_answers"]}
    assert bands[0.5] == "强制人工"
    assert bands[0.9] == "高亮提醒" if 0.9 in bands else True

    # 教师落闸 approve，然后走既有提交状态机 → 证据事件
    assert client.post(f"/exams/{exam_id}/approve-tags").json()["approved"] == 2
    commit = client.post(f"/exams/{exam_id}/commit").json()
    assert commit["committed_responses"] == 1
    # 题3 标注被闭集校验拦截（无有效知识点）→ 未标注题不进分析，只派生 2 条
    assert commit["evidence_events"] == 2

    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import ExamResponse, ResponseAnswer, TemplateQuestion

    with SessionLocal() as s:
        resp = s.scalar(select(ExamResponse))
        assert resp.status == "已提交" and resp.source == "photo"
        assert resp.total_score == pytest.approx(17.0)
        chosen = {
            s.get(TemplateQuestion, a.template_question_id).idx: a.chosen_option
            for a in s.scalars(select(ResponseAnswer))
        }
        assert chosen[1] == "B"  # 选择题选项被记录（迷思分析前提）

def test_tag_review_sampling_keeps_sampled_and_low_confidence(client, monkeypatch):
    """采样模式：高置信抽样题 + 全部低置信题保留待逐题确认。"""
    _bootstrap(client)
    set_client(MockLLMClient([STAGE_A_PAYLOAD]))

    import app.ingestion.photo as photo_mod

    monkeypatch.setattr(photo_mod, "TAG_REVIEW_SAMPLE_RATE", 1.0)  # 所有高置信题均抽中
    tpl = client.post(
        "/exams/photo-template",
        files={"file": ("paper.jpg", _jpeg_bytes(), "image/jpeg")},
        data={"class_id": 1, "name": "抽样复核卷", "exam_date": "2025-10-25", "type": "单元"},
    ).json()
    exam_id = tpl["exam_id"]

    queue = client.get(f"/exams/{exam_id}/review-queue").json()
    reasons = {x["question_idx"]: x["review_reason"] for x in queue["unreviewed_tags"]}
    assert reasons == {1: "高置信抽样", 2: "低置信标注"}

    approved = client.post(f"/exams/{exam_id}/approve-tags").json()
    assert approved == {"approved": 0, "pending": 2}

    # 采样算法稳定且边界明确
    assert photo_mod._tag_sampled(exam_id, 1, 0.0) is False
    assert photo_mod._tag_sampled(exam_id, 1, 1.0) is True
    assert photo_mod._tag_sampled(exam_id, 1, 0.1) == photo_mod._tag_sampled(exam_id, 1, 0.1)


def test_suggest_question_tags_closed_set_and_no_persist(client):
    """题干->闭集 kp 推荐：非法编码丢弃、不落库。"""
    _bootstrap(client)  # 导入 kb
    set_client(
        MockLLMClient(
            [
                {
                    "questions": [
                        {
                            "idx": 1,
                            "kp_tags": [
                                {"code": "M7A-105", "weight": 0.6},
                                {"code": "FAKE", "weight": 0.4},  # 闭集外，丢弃
                            ],
                            "confidence": 0.9,
                        }
                    ]
                }
            ]
        )
    )
    r = client.post(
        "/kb/suggest-question-tags",
        json={"questions": [{"idx": 1, "stem": "求 -3 的绝对值", "q_type": "填空"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prompt_version"] == "tagger-v0.1.0"
    sug = body["suggestions"][0]
    assert sug["idx"] == 1
    codes = [t["code"] for t in sug["kps"]]
    assert codes == ["M7A-105"]  # FAKE 被闭集校验丢弃
    assert any("FAKE" in w for w in body["warnings"])

    # 不落库：bank_question / question_kp 无新增
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import QuestionKp

    with SessionLocal() as s:
        assert list(s.scalars(select(QuestionKp))) == []


def test_stage_b_out_of_range_score_clamped(client):
    student_id = _bootstrap(client)
    payload = {
        "questions": [
            {"idx": 1, "stem": "x", "q_type": "解答", "full_score": 10,
             "cog_level": "应用", "kp_tags": [{"code": "M7A-105", "weight": 1.0}],
             "confidence": 0.9}
        ]
    }
    bad_answer = {"answers": [{"idx": 1, "score": 99, "chosen_option": None, "confidence": 0.3}]}
    mock = MockLLMClient([payload, bad_answer])
    set_client(mock)

    tpl = client.post(
        "/exams/photo-template",
        files={"file": ("paper.jpg", _jpeg_bytes(), "image/jpeg")},
        data={"class_id": 1, "name": "越界测试", "exam_date": "2025-10-22", "type": "单元"},
    ).json()
    r = client.post(
        f"/exams/{tpl['exam_id']}/photo-response",
        files={"file": ("student.jpg", _jpeg_bytes(), "image/jpeg")},
        data={"student_id": student_id},
    )
    assert any("越界" in w for w in r.json()["warnings"])


def test_pii_masking_applied():
    """阶段B送模型的图片必须经过姓名栏遮盖。"""
    from app.ingestion.pii import mask_image

    masked = mask_image(_jpeg_bytes(), ratio=0.12)
    img = Image.open(io.BytesIO(masked))
    # 顶部条带应为黑色（遮盖）
    assert img.getpixel((10, 5)) == (0, 0, 0)
    # 中部不受影响
    assert img.getpixel((100, 150)) != (0, 0, 0)


# ---------------------------------------------------------------------------
# 批量拍照录入（DESIGN 批量录入 v0.3）
# ---------------------------------------------------------------------------


def _batch_payload(name: str) -> dict:
    """批量学生卷 payload：含 student_name + answers（idx 与 STAGE_A_PAYLOAD 对齐）。"""
    return {
        "student_name": name,
        "answers": [
            {"idx": 1, "score": 5, "chosen_option": "B", "confidence": 0.95},
            {"idx": 2, "score": 4, "chosen_option": None, "confidence": 0.5},
            {"idx": 3, "score": 8, "chosen_option": None, "confidence": 0.99},
        ],
    }


def _bootstrap_batch(client: TestClient, aliases=("P01", "P02")) -> tuple[int, dict[str, int]]:
    """建 KB + 班级(指定化名) + 拍照模板，返回 (exam_id, {alias: student_id})。"""
    assert client.post("/kb/import", json={"yaml_path": str(KB_YAML)}).status_code == 200
    sid = client.post("/schools", json={"name": "批量测试校"}).json()["school_id"]
    r = client.post(
        f"/schools/{sid}/classes",
        json={"name": "七(3)班", "grade": 7, "student_aliases": list(aliases)},
    )
    student_ids = r.json()["student_ids"]
    set_client(MockLLMClient([STAGE_A_PAYLOAD]))
    tpl = client.post(
        "/exams/photo-template",
        files={"file": ("paper.jpg", _jpeg_bytes(), "image/jpeg")},
        data={"class_id": 1, "name": "批量月考", "exam_date": "2025-10-25", "type": "单元"},
    ).json()
    return tpl["exam_id"], dict(zip(aliases, student_ids))


def _batch_upload(client: TestClient, exam_id: int, payloads: list[dict], sync=True):
    """便捷：预设 payloads 后上传 N 张，返回响应。"""
    set_client(MockLLMClient(payloads))
    files = [("files", (f"s{i}.jpg", _jpeg_bytes(), "image/jpeg")) for i in range(len(payloads))]
    return client.post(
        f"/exams/{exam_id}/photo-batch",
        files=files,
        data={"sync": "true"} if sync else {"sync": "false"},
    )


def _item_db(item_id: int):
    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import ParseBatchItem

    with SessionLocal() as s:
        return s.scalar(select(ParseBatchItem).where(ParseBatchItem.id == item_id))


def test_batch_matched_and_version_isolation(client):
    exam_id, students = _bootstrap_batch(client)
    r = _batch_upload(client, exam_id, [_batch_payload("P01")])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"][0]["status"] == "matched"

    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import ExamResponse, ParseBatchItem, ParseJob

    with SessionLocal() as s:
        # ExamResponse 落库、待审核
        assert s.scalar(select(func.count(ExamResponse.id)).where(ExamResponse.exam_template_id == exam_id)) == 1
        # detected_name 终态清空（PII 留存边界）
        it = s.scalar(select(ParseBatchItem).where(ParseBatchItem.id == body["items"][0]["id"]))
        assert it.detected_name is None
        # batch 的 ParseJob 记独立版本号；模板 job 仍为旧版本
        batch_job = s.scalar(select(ParseJob).where(ParseJob.target == f"batch:{exam_id}"))
        assert batch_job.prompt_version == "parse-v0.2.0"
        tpl_job = s.scalar(select(ParseJob).where(ParseJob.target.like("template:%")))
        assert tpl_job.prompt_version == "parse-v0.1.0"


def test_batch_unmatched_then_assign(client):
    exam_id, students = _bootstrap_batch(client)
    r = _batch_upload(client, exam_id, [_batch_payload("无名氏")])
    item_id = r.json()["items"][0]["id"]
    assert r.json()["items"][0]["status"] == "unmatched"
    # 待指派期间 detected_name 保留
    assert _item_db(item_id).detected_name == "无名氏"

    # 指派到 P01
    r2 = client.post(f"/batch-items/{item_id}/assign", json={"student_id": students["P01"]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "matched"
    # 指派后 detected_name 清空
    assert _item_db(item_id).detected_name is None

    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import ExamResponse

    with SessionLocal() as s:
        assert s.scalar(select(func.count(ExamResponse.id)).where(ExamResponse.exam_template_id == exam_id)) == 1

    # 对已 matched 项再 assign -> 400
    assert client.post(f"/batch-items/{item_id}/assign", json={"student_id": students["P02"]}).status_code == 400


def test_batch_duplicate_via_constraint(client):
    exam_id, students = _bootstrap_batch(client)
    # 同名两次上传：第一条 matched，第二条靠 uq_tpl_student 触发 duplicate
    r = _batch_upload(client, exam_id, [_batch_payload("P01"), _batch_payload("P01")])
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items[0]["status"] == "matched"
    assert items[1]["status"] == "duplicate"

    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import ExamResponse

    with SessionLocal() as s:
        # 不重复落库
        assert s.scalar(select(func.count(ExamResponse.id)).where(ExamResponse.exam_template_id == exam_id)) == 1


def test_batch_failed_when_no_payload(client):
    exam_id, students = _bootstrap_batch(client)
    # MockLLMClient 无预设 -> 不可重试的 LLMError -> failed，且 tempfile 保留供重试
    set_client(MockLLMClient([]))
    r = client.post(
        f"/exams/{exam_id}/photo-batch",
        files=[("files", ("s0.jpg", _jpeg_bytes(), "image/jpeg"))],
        data={"sync": "true"},
    )
    assert r.status_code == 200, r.text
    item_id = r.json()["items"][0]["id"]
    assert r.json()["items"][0]["status"] == "failed"
    it = _item_db(item_id)
    assert it.detected_name is None  # failed 清空
    assert it.file_path and os.path.exists(it.file_path)  # tempfile 保留


def test_batch_contains_match(client):
    exam_id, students = _bootstrap_batch(client, aliases=("王小明", "张三"))
    r = _batch_upload(client, exam_id, [_batch_payload("小明")])
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["status"] == "matched"
    assert _item_db(item["id"]).match_confidence == 0.8


def test_batch_contains_multi_hit_unmatched(client):
    exam_id, students = _bootstrap_batch(client, aliases=("王小明", "李小明"))
    r = _batch_upload(client, exam_id, [_batch_payload("小明")])
    # upload 响应只含 id/file_name/status；warnings 走 GET job
    job = client.get(f"/batch-jobs/{r.json()['job_id']}").json()
    item = job["items"][0]
    assert item["status"] == "unmatched"
    assert any("多名候选" in w for w in item["warnings"])


def test_batch_external_code_not_substring_matched(client):
    """external_code 只精确等值，不参与包含匹配。"""
    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import Student

    exam_id, students = _bootstrap_batch(client, aliases=("张三",))
    # 直接给张三设学籍号 2024001
    with SessionLocal() as s:
        stu = s.scalar(select(Student).where(Student.id == students["张三"]))
        stu.external_code = "2024001"
        s.commit()
    # 读到 "001"：精确不等、包含不命中 external_code、name_or_alias 也不含 -> unmatched
    r = _batch_upload(client, exam_id, [_batch_payload("001")])
    assert r.json()["items"][0]["status"] == "unmatched"


def test_batch_file_validation(client):
    exam_id, students = _bootstrap_batch(client)
    # 非图片 -> 400
    r = client.post(
        f"/exams/{exam_id}/photo-batch",
        files=[("files", ("bad.txt", b"not an image", "image/jpeg"))],
        data={"sync": "true"},
    )
    assert r.status_code == 400
    # 单文件 >10MB -> 413
    big = b"\x00" * (10 * 1024 * 1024 + 1)
    r = client.post(
        f"/exams/{exam_id}/photo-batch",
        files=[("files", ("big.jpg", big, "image/jpeg"))],
        data={"sync": "true"},
    )
    assert r.status_code == 413
    # >50 文件 -> 400
    files = [("files", (f"s{i}.jpg", _jpeg_bytes(), "image/jpeg")) for i in range(51)]
    r = client.post(f"/exams/{exam_id}/photo-batch", files=files, data={"sync": "true"})
    assert r.status_code == 400


def test_batch_retry_success_and_missing_file(client):
    exam_id, students = _bootstrap_batch(client)
    # 两个 failed 项（空 mock），tempfile 保留
    set_client(MockLLMClient([]))
    r = client.post(
        f"/exams/{exam_id}/photo-batch",
        files=[("files", ("a.jpg", _jpeg_bytes(), "image/jpeg")),
               ("files", ("b.jpg", _jpeg_bytes(), "image/jpeg"))],
        data={"sync": "true"},
    )
    ids = [it["id"] for it in r.json()["items"]]
    # 项 A 重试 -> matched
    set_client(MockLLMClient([_batch_payload("P01")]))
    rr = client.post(f"/batch-items/{ids[0]}/retry?sync=true")
    assert rr.status_code == 200, rr.text
    assert rr.json()["status"] == "matched"
    # 项 B 删 tempfile 后重试 -> 400
    it_b = _item_db(ids[1])
    os.remove(it_b.file_path)
    assert client.post(f"/batch-items/{ids[1]}/retry?sync=true").status_code == 400
    # 非 failed 项重试 -> 400
    assert client.post(f"/batch-items/{ids[0]}/retry?sync=true").status_code == 400


def test_batch_discard(client):
    exam_id, students = _bootstrap_batch(client)
    r = _batch_upload(client, exam_id, [_batch_payload("无名氏"), _batch_payload("P01")])
    ids = [it["id"] for it in r.json()["items"]]
    assert r.json()["items"][0]["status"] == "unmatched"
    # 丢弃 unmatched 项 -> discarded，detected_name 清空，tempfile 删除
    rd = client.post(f"/batch-items/{ids[0]}/discard")
    assert rd.status_code == 200 and rd.json()["status"] == "discarded"
    it = _item_db(ids[0])
    assert it.detected_name is None and it.file_path is None
    # matched 项不可丢弃 -> 400
    assert client.post(f"/batch-items/{ids[1]}/discard").status_code == 400


def test_batch_zombie_reconcile(client):
    """构造 parsing 僵尸 item + running job，reconcile_stale 后改判 failed、job done。"""
    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import ParseBatchItem, ParseJob

    exam_id, students = _bootstrap_batch(client)
    with SessionLocal() as s:
        job = ParseJob(target=f"batch:{exam_id}", model_version="mock", prompt_version="parse-v0.2.0", status="running")
        s.add(job)
        s.flush()
        s.add(ParseBatchItem(parse_job_id=job.id, exam_template_id=exam_id, file_name="z.jpg",
                             file_path=None, status="parsing", warnings=[]))
        s.commit()
        job_id = job.id

    from app.ingestion.batch import reconcile_stale
    reconcile_stale()

    with SessionLocal() as s:
        it = s.scalar(select(ParseBatchItem).where(ParseBatchItem.parse_job_id == job_id))
        assert it.status == "failed"
        assert any("重启" in w for w in it.warnings)
        assert s.get(ParseJob, job_id).status == "done"


class _FlakyClient(MockLLMClient):
    """前 N 次抛网络类 LLMError，之后正常返回。"""
    def __init__(self, payload, fails=2):
        super().__init__([payload])
        self._fails = fails
        self._n = 0

    def parse_json(self, system, user, image_bytes):
        self._n += 1
        if self._n <= self._fails:
            raise LLMError("网络超时，请重试")
        return super().parse_json(system, user, image_bytes)


def test_batch_llm_retry(client, monkeypatch):
    exam_id, students = _bootstrap_batch(client)
    # 跳过真实退避 sleep
    from app.ingestion import batch as batch_mod
    monkeypatch.setattr(batch_mod.time, "sleep", lambda *_: None)
    set_client(_FlakyClient(_batch_payload("P01"), fails=2))
    r = client.post(
        f"/exams/{exam_id}/photo-batch",
        files=[("files", ("s1.jpg", _jpeg_bytes(), "image/jpeg"))],
        data={"sync": "true"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["status"] == "matched"  # 退避 2 次后成功


def test_batch_sync_guard(monkeypatch):
    """SC_LLM_PROVIDER!=mock 且无 MockLLMClient 时，sync=true 仍走异步（立即返回 queued）。"""
    import app.api.routes as routes_mod
    import app.api.deps as deps_mod
    from app.ingestion import batch as batch_mod

    # 确保无 mock override（_effective_sync 走 openai 分支 -> 异步）
    set_client(None)
    submitted: list[int] = []
    monkeypatch.setattr(batch_mod, "submit_item", lambda iid: submitted.append(iid))

    from fastapi.testclient import TestClient
    from app.main import app
    import tempfile as _tmp, os as _os
    # 用一个隔离库避免污染
    db_path = _tmp.mkdtemp() + "/syncguard.db"
    import app.db as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app import models  # noqa: F401
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 15})
    Base.metadata.create_all(eng)
    sl = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    orig = (dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal)
    dbmod.engine, dbmod.SessionLocal = eng, sl
    deps_mod.SessionLocal = sl
    try:
        with TestClient(app) as c:
            assert c.post("/kb/import", json={"yaml_path": str(KB_YAML)}).status_code == 200
            sid = c.post("/schools", json={"name": "守卫校"}).json()["school_id"]
            c.post(f"/schools/{sid}/classes", json={"name": "c1", "grade": 7, "student_aliases": ["P01"]})
            set_client(MockLLMClient([STAGE_A_PAYLOAD]))
            tpl = c.post("/exams/photo-template",
                         files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")},
                         data={"class_id": 1, "name": "t", "exam_date": "2025-10-25", "type": "单元"}).json()
            eid = tpl["exam_id"]
            set_client(None)  # 关键：无 mock override
            r = c.post(f"/exams/{eid}/photo-batch",
                       files=[("files", ("s.jpg", _jpeg_bytes(), "image/jpeg"))],
                       data={"sync": "true"})
            assert r.status_code == 200, r.text
            # 异步路径：立即返回 queued，且 submit_item 被调用
            assert r.json()["items"][0]["status"] == "queued"
            assert len(submitted) == 1
    finally:
        dbmod.engine, dbmod.SessionLocal, deps_mod.SessionLocal = orig
        set_client(None)
        _os.remove(db_path)

