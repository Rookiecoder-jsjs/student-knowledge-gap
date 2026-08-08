"""模拟试卷全流程演练：造图 → 拍照建卷(A) → 审核 → 逐人拍照(B) → 修正 → 提交 → 报告。

对着正在运行的后端（默认 http://127.0.0.1:8000）走真实 HTTP + 真实多模态模型。
同一批学生跑 3 轮考试，使每个知识点证据数 ≥3，跨过证据门槛产出薄弱判定。

用法：../.venv/bin/python scripts/mock_photo_run.py [wave]   # wave=1|2|3
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parents[1] / "output" / "mock_papers"
OUT.mkdir(parents=True, exist_ok=True)
WAVE = int(sys.argv[1]) if len(sys.argv) > 1 else 1

# ---- 试卷设计：5 题，嵌入知识点意图 -------------------------------------

QUESTIONS = [
    {"idx": 1, "type": "选择", "full": 5, "kp": "M7A-105",
     "stem": "下列关于绝对值的说法正确的是", "options": "A.|a|一定大于0  B.互为相反数的两数绝对值相等  C.|a|=-a不可能成立  D.绝对值等于本身的数只有0"},
    {"idx": 2, "type": "选择", "full": 5, "kp": "M7A-104",
     "stem": "-3 的相反数是", "options": "A.-3  B.1/3  C.3  D.-1/3"},
    {"idx": 3, "type": "解答", "full": 10, "kp": "M7A-111", "stem": "计算：(-3)+5"},
    {"idx": 4, "type": "解答", "full": 10, "kp": "M7A-112", "stem": "计算：2-(-5)"},
    {"idx": 5, "type": "解答", "full": 10, "kp": "M7A-123", "stem": "将 696000 用科学记数法表示"},
]
CORRECT_OPTION = {1: "B", 2: "C"}

# 8 名学生 × 3 轮的植入得分（顺序对应 QUESTIONS）。
# 薄弱植入：S02 绝对值+减法、S03 相反数+减法、S04 绝对值+科学记数法、S07 全面；
# S06 逐轮上升（轨迹演示）；S01/S05/S08 稳定优秀。
PROFILES: dict[str, dict[int, list[int]]] = {
    "S01": {1: [5, 5, 10, 10, 10], 2: [5, 5, 10, 10, 10], 3: [5, 5, 10, 10, 10]},
    "S02": {1: [0, 5, 8, 2, 9], 2: [0, 5, 8, 3, 9], 3: [2, 5, 9, 3, 10]},
    "S03": {1: [5, 0, 9, 3, 10], 2: [5, 0, 9, 3, 10], 3: [5, 2, 9, 4, 10]},
    "S04": {1: [0, 5, 7, 4, 2], 2: [0, 5, 7, 4, 3], 3: [2, 5, 8, 5, 3]},
    "S05": {1: [5, 5, 10, 9, 10], 2: [5, 5, 10, 10, 10], 3: [5, 5, 10, 10, 10]},
    "S06": {1: [3, 5, 8, 5, 8], 2: [5, 5, 9, 7, 8], 3: [5, 5, 9, 8, 9]},
    "S07": {1: [0, 0, 6, 2, 5], 2: [0, 0, 6, 3, 5], 3: [0, 2, 7, 3, 6]},
    "S08": {1: [5, 5, 9, 10, 9], 2: [5, 5, 10, 10, 9], 3: [5, 5, 10, 10, 10]},
}
NAMES = list(PROFILES)
EXAM_DATE = {1: "2026-08-01", 2: "2026-08-04", 3: "2026-08-07"}

RED = (196, 32, 32)
INK = (30, 30, 30)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit("未找到中文字体，无法渲染模拟试卷")


def render_paper(name: str | None, scores: list[int] | None) -> bytes:
    """name=None 且 scores=None → 空白卷；否则学生作答卷（红笔得分）。"""
    img = Image.new("RGB", (900, 1250), "white")
    d = ImageDraw.Draw(img)
    f_title, f_body, f_score = _font(34), _font(24), _font(26)

    d.rectangle([0, 0, 900, 90], fill=(245, 245, 240))
    d.text((40, 25), "七年级数学单元测试（模拟卷）　满分 40 分", font=f_title, fill=INK)
    who = f"姓名：{name}　　班级：七(8)班" if name else "姓名：__________　　班级：__________"
    d.text((40, 105), who, font=f_body, fill=INK)

    y = 170
    for q, sc in zip(QUESTIONS, scores or [None] * len(QUESTIONS)):
        d.text((40, y), f"{q['idx']}.（{q['full']} 分）{q['type']}：{q['stem']}", font=f_body, fill=INK)
        y += 40
        if q["type"] == "选择":
            blank = "（　　）" if scores is None else (
                f"（{CORRECT_OPTION[q['idx']]}）" if sc == q["full"]
                else f"（{'A' if CORRECT_OPTION[q['idx']] != 'A' else 'D'}）"
            )
            d.text((60, y), blank, font=f_body, fill=INK)
            y += 38
            d.text((60, y), q["options"], font=f_body, fill=INK)
            y += 40
        else:
            d.text((60, y), "解：", font=f_body, fill=INK)
            y += 60
        if scores is not None:
            d.text((760, y - 45), f"得分：{sc}", font=f_score, fill=RED)
        y += 30
        d.line([(40, y), (860, y)], fill=(220, 220, 220), width=1)
        y += 25

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=180)

    def stage(t: str) -> None:
        print(f"\n=== [wave {WAVE}] {t} ", flush=True)

    stage("1 知识库导入（幂等）")
    kb = c.post("/kb/import", json={"yaml_path": "kb/math/grade7/kb.yaml"}).json()
    print("kb_version_id =", kb["kb_version_id"])

    if WAVE == 1:
        stage("2 建班 + 名单（8 人）+ 教学进度")
        school = c.post("/schools", json={"name": "模拟演练校"}).json()["school_id"]
        cls = c.post(
            f"/schools/{school}/classes",
            json={"name": "七(8)班", "grade": 7, "student_aliases": NAMES},
        ).json()
        class_id, student_ids = cls["class_id"], cls["student_ids"]
        kps = c.get("/kb/kps").json()["kps"]
        ch1 = [k["code"] for k in kps if k["code"].startswith("M7A-1")]
        c.post(f"/classes/{class_id}/progress", json={"kp_codes": ch1, "taught_at": "2026-07-20"})
    else:
        stage("2 复用既有班级")
        classes = c.get("/classes").json()["classes"]
        class_id = next(x["class_id"] for x in classes if x["name"] == "七(8)班")
        student_ids = [s["student_id"] for s in c.get(f"/classes/{class_id}/students").json()["students"]]
    print("class_id =", class_id)

    stage("3 阶段A：空白卷拍照建卷")
    blank = render_paper(None, None)
    (OUT / f"blank_w{WAVE}.jpg").write_bytes(blank)
    r = c.post(
        "/exams/photo-template",
        files={"file": (f"blank_w{WAVE}.jpg", blank, "image/jpeg")},
        data={"class_id": str(class_id), "name": f"模拟单元测{WAVE}",
              "exam_date": EXAM_DATE[WAVE], "type": "单元"},
    ).json()
    exam_id = r["exam_id"]
    print(f"exam_id={exam_id} 解析题数={r['questions']} warnings={r['warnings']}")

    stage("4 审核落闸")
    print(c.post(f"/exams/{exam_id}/approve-tags").json())

    stage("5 阶段B：8 份学生答卷拍照")
    for name, sid in zip(NAMES, student_ids):
        scores = PROFILES[name][WAVE]
        img = render_paper(name, scores)
        (OUT / f"stu_{name}_w{WAVE}.jpg").write_bytes(img)
        r = c.post(
            f"/exams/{exam_id}/photo-response",
            files={"file": (f"stu_{name}_w{WAVE}.jpg", img, "image/jpeg")},
            data={"student_id": str(sid)},
        ).json()
        print(f"  {name}: response_id={r['response_id']}{' 警告:' + ';'.join(r['warnings']) if r['warnings'] else ''}")

    stage("6 低置信核对与修正")
    q = c.get(f"/exams/{exam_id}/review-queue").json()
    names = dict(zip(NAMES, student_ids))
    by_sid = {v: k for k, v in names.items()}
    for a in q["low_confidence_answers"]:
        planted = PROFILES[by_sid[a["student_id"]]][WAVE][a["question_idx"] - 1]
        if a["score"] != planted:
            fix = c.patch(f"/response-answers/{a['answer_id']}", json={"score": float(planted)}).json()
            print(f"  修正 {by_sid[a['student_id']]} 题{a['question_idx']}: {a['score']}→{planted} [{a['band']}]")
        else:
            print(f"  确认 {by_sid[a['student_id']]} 题{a['question_idx']} = {a['score']} [{a['band']}]")
    if not q["low_confidence_answers"]:
        print("  无低置信项")

    stage("7 提交并派生证据")
    print(c.post(f"/exams/{exam_id}/commit").json())

    if WAVE == 3:
        stage("8 三轮后：班级质量分析（含 AI 解读）")
        rep = c.get(f"/classes/{class_id}/quality-report",
                    params={"exam_id": exam_id, "narrative": True}).json()
        print("  含 AI 解读段：", "AI 解读" in rep["markdown"])

        stage("9 个体诊断（S02：植入绝对值+减法薄弱）")
        sid2 = student_ids[1]
        weak = c.get(f"/students/{sid2}/weaknesses").json()
        print("  薄弱点：", [(w["code"], w["name"], round(w["mastery"] or 0, 2),
                              f"证据{w['evidence_count']}题") for w in weak["weak"]])
        att = c.post(f"/students/{sid2}/attributions").json()
        print("  归因：", [(a["kp"], a["type"], a["root_kp"]) for a in att["attributions"] if a["type"] != "数据不足"])
        diag = c.get(f"/students/{sid2}/diagnosis", params={"narrative": True}).json()
        print("  诊断单含 AI 解读：", "AI 解读" in diag["markdown"])

        stage("10 班级共性（S07 全面薄弱视角）")
        weak7 = c.get(f"/students/{student_ids[6]}/weaknesses").json()
        print("  S07 薄弱点：", [(w["code"], "共性" if w["class_common"] else "") for w in weak7["weak"]])

    print(f"\nwave {WAVE} 完成。图片留存于 {OUT}")


if __name__ == "__main__":
    main()
