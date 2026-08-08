"""合成学生模拟器：植入真值 → 生成考试数据 → 走真实管线入库。

植入场景（真值表 = gold set v0 的基准）：
- 组A（5人）：根源 M7A-105 绝对值缺陷 → 沿前置链波及 106/111/112；
- 组B（4人）：小学根源 M6-02 分数运算缺陷（跨年级归因）→ 114/115/116；
- 组C（3人）：M7A-113 遗忘衰减（前期 0.85，期中后无练习，期末跌到 0.45）；
- 组D（3人）：M7A-302 等式性质缺陷 → 303/304；
- 班级共性：M7A-123 科学记数法全班偏弱（应触发教学建议而非个体归责）；
- 其余学生：正常能力（0.65~0.9）。

考试安排（含教学进度联动）：
- E0 小学基础诊断（补录）2025-09-05 → 桥接节点证据，支撑跨年级归因；
- E1 第一章单元测 2025-09-30；E2 期中 2025-11-10；
- E3 第三章单元测 2025-12-05；E4 期末 2026-01-15。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.templates import create_template
from app.models import (
    Class,
    ExamResponse,
    KnowledgePoint,
    ResponseAnswer,
    Student,
    TeachingProgress,
    TemplateQuestion,
)

EXAM_SCHEDULE = [
    # (名称, 日期, 类型, 覆盖编码前缀)
    ("小学基础诊断（补录）", date(2025, 9, 5), "补录", ["M6-"]),
    ("第一章单元测", date(2025, 9, 30), "单元", ["M7A-1"]),
    ("期中考试", date(2025, 11, 10), "期中", ["M7A-1", "M7A-2"]),
    ("第三章单元测", date(2025, 12, 5), "单元", ["M7A-3"]),
    ("期末考试", date(2026, 1, 15), "期末", ["M7A-1", "M7A-2", "M7A-3", "M7A-4"]),
]

GROUP_A = {"root": "M7A-105", "weak": {"M7A-105", "M7A-106", "M7A-111", "M7A-112"}}
GROUP_B = {"root": "M6-02", "weak": {"M6-02", "M7A-114", "M7A-115", "M7A-116"}}
GROUP_C_FORGET_KP = "M7A-113"
GROUP_D = {"root": "M7A-302", "weak": {"M7A-302", "M7A-303", "M7A-304"}}
CLASS_COMMON_KP = "M7A-123"


@dataclass
class SimTruth:
    """模拟器真值表，供金标断言使用。"""

    student_ids: dict[str, int] = field(default_factory=dict)   # alias → id
    planted_weak: dict[str, set[str]] = field(default_factory=dict)
    planted_roots: dict[str, str] = field(default_factory=dict)  # alias → 植入根源
    forgetting: set[str] = field(default_factory=set)            # 植入遗忘的 alias
    class_common_kp: str = CLASS_COMMON_KP
    exam_ids: dict[str, int] = field(default_factory=dict)       # 考试名 → template_id


def build_simulation(
    session: Session,
    kb_version_id: int,
    class_id: int,
    n_students: int = 30,
    seed: int = 42,
) -> SimTruth:
    rng = random.Random(seed)
    truth = SimTruth()

    clazz = session.get(Class, class_id)
    kp_rows = {
        kp.code: kp
        for kp in session.scalars(
            select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb_version_id)
        )
    }
    all_codes = sorted(c for c in kp_rows if not c.startswith("C"))

    # ---- 1. 学生、分组与植入真值（一次性采样，保证跨考试一致） ----
    groups: dict[str, str] = {}
    weak_level: dict[tuple[str, str], float] = {}   # (alias, code) → 植入掌握度
    for i in range(1, n_students + 1):
        alias = f"S{i:02d}"
        stu = Student(
            school_id=clazz.school_id,
            class_id=class_id,
            name_or_alias=alias,
            external_code=f"2025{i:03d}",
        )
        session.add(stu)
        session.flush()
        truth.student_ids[alias] = stu.id

        if i <= 5:
            g = "A"
        elif i <= 9:
            g = "B"
        elif i <= 12:
            g = "C"
        elif i <= 15:
            g = "D"
        else:
            g = ""
        groups[alias] = g

        planted: set[str] = set()
        if g == "A":
            spec = GROUP_A
        elif g == "B":
            spec = GROUP_B
        elif g == "D":
            spec = GROUP_D
        else:
            spec = None
        if spec is not None:
            truth.planted_roots[alias] = spec["root"]
            planted = {c for c in spec["weak"] if c in kp_rows and not c.startswith("M6")}
            root_level = rng.uniform(0.25, 0.40)
            for code in spec["weak"]:
                if code not in kp_rows:
                    continue
                level = root_level if code == spec["root"] else rng.uniform(0.40, 0.50)
                weak_level[(alias, code)] = level
        if g == "C":
            planted = {GROUP_C_FORGET_KP}
            truth.forgetting.add(alias)
        truth.planted_weak[alias] = planted

    # 班级共性薄弱：每人在 M7A-123 上的植入水平（一次性采样）
    common_level = {
        alias: rng.uniform(0.42, 0.55) for alias in truth.student_ids
    }
    base_ability = {alias: rng.uniform(0.65, 0.90) for alias in truth.student_ids}
    session.flush()

    def true_mastery(alias: str, code: str, when: date) -> float:
        m = base_ability[alias]
        if (alias, code) in weak_level:
            m = weak_level[(alias, code)]
        if groups[alias] == "C" and code == GROUP_C_FORGET_KP:
            m = 0.85 if when < date(2025, 11, 20) else 0.45
        if code == CLASS_COMMON_KP:
            m = min(m, common_level[alias])
        return max(0.05, min(0.95, m))

    # ---- 2. 考试模板 + 教学进度 ----
    taught: set[str] = set()
    for name, exam_date, type_, prefixes in EXAM_SCHEDULE:
        codes = [c for c in all_codes if any(c.startswith(p) for p in prefixes)]
        if prefixes == ["M6-"]:
            # 入学诊断小测：每个桥接知识点 3 题，使前置追溯也有足量证据
            codes = [c for c in codes for _ in range(3)]
        questions = []
        for idx, code in enumerate(codes, start=1):
            kp = kp_rows[code]
            cog = kp.cog_levels_expected[0] if kp.cog_levels_expected else "应用"
            is_choice = cog in ("识记", "理解")
            questions.append(
                {
                    "idx": idx,
                    "stem": f"（合成题）考查 {kp.name}",
                    "q_type": "选择" if is_choice else "解答",
                    "full_score": 5.0 if is_choice else 10.0,
                    "cog_level": cog,
                    "difficulty_est": kp.difficulty_prior,
                    "n_options": 4 if is_choice else None,
                    "kps": [{"code": code, "weight": 1.0}],
                }
            )
        tpl = create_template(
            session, kb_version_id, class_id, name, exam_date, type_, questions
        )
        truth.exam_ids[name] = tpl.id

        for code in codes:
            if code not in taught and not code.startswith("M6"):
                session.add(
                    TeachingProgress(
                        class_id=class_id,
                        kp_id=kp_rows[code].id,
                        taught_at=exam_date - timedelta(days=1),
                    )
                )
                taught.add(code)
    session.flush()

    # ---- 3. 作答（待审核，交给真实 commit 管线提交） ----
    letters = "ABCD"
    for alias, stu_id in truth.student_ids.items():
        for name, exam_date, _type, _prefixes in EXAM_SCHEDULE:
            tpl_id = truth.exam_ids[name]
            response = ExamResponse(
                exam_template_id=tpl_id,
                student_id=stu_id,
                source="excel",
                status="待审核",
            )
            session.add(response)
            session.flush()

            total = 0.0
            tq_rows = list(
                session.scalars(
                    select(TemplateQuestion)
                    .where(TemplateQuestion.exam_template_id == tpl_id)
                    .order_by(TemplateQuestion.idx)
                )
            )
            for tq in tq_rows:
                qkp = tq.kps[0] if tq.kps else None
                code = session.get(KnowledgePoint, qkp.kp_id).code if qkp else None
                m = true_mastery(alias, code, exam_date) if code else 0.7
                p = max(
                    0.03,
                    min(0.97, m + (0.5 - tq.difficulty_est) * 0.2 + rng.gauss(0, 0.05)),
                )
                if tq.q_type == "选择":
                    correct = rng.random() < p
                    score = tq.full_score if correct else 0.0
                    option = "A" if correct else rng.choice(letters[1:])
                else:
                    ratio = max(0.0, min(1.0, rng.gauss(m, 0.18)))
                    score = round(tq.full_score * ratio * 2) / 2
                    option = None
                total += score
                session.add(
                    ResponseAnswer(
                        exam_response_id=response.id,
                        template_question_id=tq.id,
                        score=score,
                        chosen_option=option,
                    )
                )
            response.total_score = round(total, 2)

    session.flush()
    return truth
