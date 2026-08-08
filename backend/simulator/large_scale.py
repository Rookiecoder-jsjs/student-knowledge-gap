"""大规模随机模拟器：随机植入薄弱点（位置随机）-> 长时间尺度考试 -> 真实管线入库。

与 synthetic.py 的核心区别（针对性解决"循环验证"问题）：
- 薄弱植入位置每种子随机选取（不再固定 M7A-105/M6-02/M7A-302/M7A-113），
  管线必须在"未知位置"发现薄弱并归因 -> 真正的有效性检验，而非对已知答案的拟合；
- 时间跨度两个学期 12 场考试（2025-09 ~ 2026-07，约 10 个月），测试衰减/遗忘长程行为；
- 多班（默认 3 班 × 50 人 = 150 人），测试大样本 P25 稳定性与班级共性检测。

每种子随机生成的植入场景：
- 前置缺陷：随机选 N 个"有后代"的知识点作根源，沿前置链植入根源(0.25~0.35)+后代(0.38~0.50)薄弱；
- 遗忘衰减：随机选 ch1/ch2 知识点，上学期高掌握(0.85)、寒假后跌至(0.45)，利用
  E5(1月)->E7(3月) 间隔(≥30天) 触发遗忘检测；
- 班级共性：每班随机 1 个知识点全班偏弱(0.42~0.55)，应触发教学建议而非个体归责；
- 其余学生正常能力(0.65~0.92)。
碰撞避免：同一学生不会被同时植入"前置后代"与"遗忘"在同一知识点上。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PREREQ_MAX_DEPTH
from app.ingestion.templates import create_template
from app.kb.graph import KpGraph
from app.models import (
    Class,
    ExamResponse,
    KnowledgePoint,
    ResponseAnswer,
    School,
    Student,
    TeachingProgress,
    TemplateQuestion,
)

# 两学期 12 场考试（名称, 日期, 类型, 覆盖容器, 是否累积型）
LARGE_EXAM_SCHEDULE: list[tuple[str, date, str, list[str], bool]] = [
    ("入学诊断(补录)", date(2025, 9, 5), "补录", ["C6-BRIDGE"], False),
    ("第一单元测", date(2025, 9, 30), "单元", ["C7A-01"], False),
    ("第二单元测", date(2025, 10, 28), "单元", ["C7A-02"], False),
    ("期中考试(上)", date(2025, 11, 20), "期中", ["C7A-01", "C7A-02"], True),
    ("第三单元测", date(2025, 12, 18), "单元", ["C7A-03"], False),
    ("期末考试(上)", date(2026, 1, 15), "期末", ["C7A-01", "C7A-02", "C7A-03"], True),
    ("第四单元测", date(2026, 3, 5), "单元", ["C7A-04"], False),
    ("一二章复习测", date(2026, 3, 31), "单元", ["C7A-01", "C7A-02"], True),
    ("期中考试(下)", date(2026, 4, 28), "期中", ["C7A-01", "C7A-02", "C7A-03", "C7A-04"], True),
    ("三四章复习测", date(2026, 5, 26), "单元", ["C7A-03", "C7A-04"], True),
    ("一二章复习测2", date(2026, 6, 23), "单元", ["C7A-01", "C7A-02"], True),
    ("学年末考试", date(2026, 7, 17), "期末", ["C7A-01", "C7A-02", "C7A-03", "C7A-04"], True),
]

# 寒假分界：此日期之前的考试 = 上学期，之后 = 下学期（遗忘植入用）
SEMESTER_BREAK = date(2026, 2, 1)


@dataclass
class LargeTruth:
    """大规模模拟器真值表，供金标断言使用。"""

    student_ids: dict[str, int] = field(default_factory=dict)        # alias -> id
    class_of: dict[str, int] = field(default_factory=dict)           # alias -> class_id
    class_ids: list[int] = field(default_factory=list)
    planted_weak: dict[str, set[str]] = field(default_factory=dict)  # alias -> 全部植入薄弱 code
    planted_roots: dict[str, str] = field(default_factory=dict)      # alias -> 植入根源 code
    planted_descendants: dict[str, set[str]] = field(default_factory=dict)  # alias -> 应归因到根源的后代 code
    forgetting: dict[str, str] = field(default_factory=dict)         # alias -> 遗忘 kp code
    class_common_kps: dict[int, str] = field(default_factory=dict)   # class_id -> 班级共性 code
    exam_ids: dict[tuple[str, int], int] = field(default_factory=dict)  # (exam_name, class_id) -> template_id
    exam_names: list[str] = field(default_factory=list)
    exam_dates: dict[str, date] = field(default_factory=dict)


def _container_members(session: Session, kb_version_id: int) -> dict[str, list[str]]:
    """容器 -> 成员 code 列表（按 code 排序）。"""
    from app.models import KpRelation

    kp_rows = {
        kp.code: kp
        for kp in session.scalars(
            select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb_version_id)
        )
    }
    id_to_code = {kp.id: kp.code for kp in kp_rows.values()}
    members: dict[str, list[str]] = {}
    for rel in session.scalars(
        select(KpRelation).where(KpRelation.type == "contains")
    ):
        container_code = id_to_code.get(rel.from_kp_id)
        child_code = id_to_code.get(rel.to_kp_id)
        if container_code and child_code:
            members.setdefault(container_code, []).append(child_code)
    for c in members:
        members[c].sort()
    return members


def _descendant_map(graph: KpGraph) -> dict[int, set[int]]:
    """祖先 kp_id -> 后代 kp_id 集合（深度 ≤ PREREQ_MAX_DEPTH）。

    通过对每个主年级知识点调用 prerequisite_chain 反向构建：
    若 R 在 D 的前置链中，则 D 是 R 的后代。
    """
    desc: dict[int, set[int]] = {}
    for kp_id in graph.grade7_kp_ids():
        for anc_id, _depth, _w in graph.prerequisite_chain(kp_id, PREREQ_MAX_DEPTH):
            desc.setdefault(anc_id, set()).add(kp_id)
    return desc


def build_large_simulation(
    session: Session,
    kb_version_id: int,
    *,
    n_classes: int = 3,
    n_per_class: int = 50,
    n_prereq_roots: int = 10,
    n_forget_kps: int = 3,
    seed: int = 100,
) -> LargeTruth:
    """构建大规模随机模拟：多班、长跨度、随机植入位置。"""
    rng = random.Random(seed)
    truth = LargeTruth()
    truth.exam_names = [e[0] for e in LARGE_EXAM_SCHEDULE]
    truth.exam_dates = {e[0]: e[1] for e in LARGE_EXAM_SCHEDULE}

    clazz0 = session.scalars(select(Class).limit(1)).first()
    school_id = clazz0.school_id if clazz0 else None
    if school_id is None:
        school = School(name=f"大规模测试学校(seed={seed})")
        session.add(school)
        session.flush()
        school_id = school.id

    # ---- 1. 班级 & 学生 ----
    for ci in range(n_classes):
        clazz = Class(school_id=school_id, name=f"大模拟班{ci + 1}", grade=7, subject="数学")
        session.add(clazz)
        session.flush()
        truth.class_ids.append(clazz.id)
        for si in range(n_per_class):
            idx = ci * n_per_class + si + 1
            alias = f"S{idx:03d}"
            stu = Student(
                school_id=school_id,
                class_id=clazz.id,
                name_or_alias=alias,
                external_code=f"2026{idx:04d}",
            )
            session.add(stu)
            session.flush()
            truth.student_ids[alias] = stu.id
            truth.class_of[alias] = clazz.id
            truth.planted_weak[alias] = set()

    kp_rows = {
        kp.code: kp
        for kp in session.scalars(
            select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb_version_id)
        )
    }
    all_codes = sorted(c for c in kp_rows if not c.startswith("C"))
    container_members = _container_members(session, kb_version_id)
    graph = KpGraph(session, kb_version_id)
    desc_map = _descendant_map(graph)

    # 候选根源 = 有后代的知识点（code 形式）。后代须是主年级（grade7）。
    candidate_roots = sorted(
        kp_rows[c].code
        for c, kp in kp_rows.items()
        if not c.startswith("C") and kp.id in desc_map and desc_map[kp.id]
    )
    # 遗忘候选 = ch1/ch2 知识点（上下学期都有考试覆盖，寒假形成间隔）
    forget_candidates = sorted(
        c for c in all_codes
        if not c.startswith("M6")
        and (c in container_members.get("C7A-01", []) or c in container_members.get("C7A-02", []))
    )

    aliases = list(truth.student_ids)
    base_ability = {a: rng.uniform(0.65, 0.92) for a in aliases}

    # ---- 2. 随机前置缺陷植入 ----
    weak_level: dict[tuple[str, str], float] = {}
    used_students: set[str] = set()  # 已被前置/遗忘占用的学生（避免同生多场景碰撞）
    n_roots = min(n_prereq_roots, len(candidate_roots))
    chosen_roots = rng.sample(candidate_roots, n_roots)
    for root_code in chosen_roots:
        root_id = kp_rows[root_code].id
        # 该根源的后代（主年级），随机选 1~2 个
        desc_codes = sorted(
            graph.kp(did).code for did in desc_map.get(root_id, set())
        )
        if not desc_codes:
            continue
        n_desc = min(rng.randint(1, 2), len(desc_codes))
        chosen_desc = rng.sample(desc_codes, n_desc)
        # 随机选 4~8 名未被占用的学生
        avail = [a for a in aliases if a not in used_students]
        if len(avail) < 4:
            break
        group = rng.sample(avail, rng.randint(4, min(8, len(avail))))
        root_level = rng.uniform(0.22, 0.34)  # 根源严重薄弱
        for alias in group:
            used_students.add(alias)
            truth.planted_roots[alias] = root_code
            truth.planted_weak[alias].add(root_code)
            weak_level[(alias, root_code)] = root_level
            truth.planted_descendants.setdefault(alias, set()).update(chosen_desc)
            for dc in chosen_desc:
                truth.planted_weak[alias].add(dc)
                weak_level[(alias, dc)] = rng.uniform(0.38, 0.50)

    # ---- 3. 随机遗忘衰减植入 ----
    n_forget = min(n_forget_kps, len(forget_candidates))
    chosen_forget = rng.sample(forget_candidates, n_forget) if forget_candidates else []
    for fk in chosen_forget:
        avail = [a for a in aliases if a not in used_students]
        if len(avail) < 3:
            break
        group = rng.sample(avail, rng.randint(3, min(5, len(avail))))
        for alias in group:
            used_students.add(alias)
            truth.forgetting[alias] = fk
            truth.planted_weak[alias].add(fk)

    # ---- 4. 班级共性薄弱（每班 1 个，随机） ----
    common_level: dict[tuple[str, str], float] = {}
    for class_id in truth.class_ids:
        class_aliases = [a for a in aliases if truth.class_of[a] == class_id]
        # 候选 = 主年级知识点（排除已作为该班学生个体植入根源/遗忘的，避免混淆）
        indiv_used = {c for a in class_aliases for c in truth.planted_weak.get(a, set())}
        cands = [c for c in all_codes if not c.startswith("M6") and c not in indiv_used]
        if not cands:
            continue
        common_kp = rng.choice(cands)
        truth.class_common_kps[class_id] = common_kp
        for alias in class_aliases:
            lvl = rng.uniform(0.42, 0.55)
            common_level[(alias, common_kp)] = lvl
            truth.planted_weak[alias].add(common_kp)

    session.flush()

    def true_mastery(alias: str, code: str, when: date) -> float:
        m = base_ability[alias]
        if (alias, code) in weak_level:
            m = weak_level[(alias, code)]
        if alias in truth.forgetting and truth.forgetting[alias] == code:
            m = 0.85 if when < SEMESTER_BREAK else 0.45
        if (alias, code) in common_level:
            m = min(m, common_level[(alias, code)])
        return max(0.05, min(0.95, m))

    # ---- 5. 考试模板 + 教学进度 ----
    taught: set[int] = set()
    for name, exam_date, type_, containers, _cumul in LARGE_EXAM_SCHEDULE:
        # 该场考试覆盖的知识点 code
        if "C6-BRIDGE" in containers:
            cov_codes = [c for c in container_members.get("C6-BRIDGE", []) for _ in range(3)]
        else:
            cov_codes = []
            for cont in containers:
                cov_codes.extend(container_members.get(cont, []))
            # 累积型复习测：每个知识点多 1 题，提高证据密度
            if _cumul:
                cov_codes = cov_codes + list(dict.fromkeys(cov_codes))
        for class_id in truth.class_ids:
            questions = []
            for idx, code in enumerate(cov_codes, start=1):
                kp = kp_rows[code]
                cog = kp.cog_levels_expected[0] if kp.cog_levels_expected else "应用"
                is_choice = cog in ("识记", "理解")
                questions.append({
                    "idx": idx,
                    "stem": f"（合成题）考查 {kp.name}",
                    "q_type": "选择" if is_choice else "解答",
                    "full_score": 5.0 if is_choice else 10.0,
                    "cog_level": cog,
                    "difficulty_est": kp.difficulty_prior,
                    "n_options": 4 if is_choice else None,
                    "kps": [{"code": code, "weight": 1.0}],
                })
            tpl = create_template(
                session, kb_version_id, class_id, name, exam_date, type_, questions
            )
            truth.exam_ids[(name, class_id)] = tpl.id

        # 教学进度：首次出现的 grade7 知识点在考前一天标记已教
        for code in set(cov_codes):
            if code.startswith("M6"):
                continue
            kp_id = kp_rows[code].id
            if kp_id not in taught:
                session.add(TeachingProgress(
                    class_id=truth.class_ids[0],  # 教学进度按班；用首班登记即可（同年级进度一致）
                    kp_id=kp_id,
                    taught_at=exam_date - timedelta(days=1),
                ))
                taught.add(kp_id)
    # 多班教学进度：复制到其余班（covered_kp_ids 按班查询）
    for class_id in truth.class_ids[1:]:
        for kp_id in taught:
            session.add(TeachingProgress(
                class_id=class_id,
                kp_id=kp_id,
                taught_at=date(2025, 9, 1),  # 其余班假设同期已教
            ))
    session.flush()

    # ---- 6. 作答（待审核，交给真实 commit 管线提交） ----
    letters = "ABCD"
    # 每个模板的题目行只需查一次（36 个模板，避免 150×12 次重复查询）
    tq_cache: dict[int, list[TemplateQuestion]] = {}
    for alias, stu_id in truth.student_ids.items():
        class_id = truth.class_of[alias]
        for name, exam_date, _t, _p, _c in LARGE_EXAM_SCHEDULE:
            tpl_id = truth.exam_ids[(name, class_id)]
            response = ExamResponse(
                exam_template_id=tpl_id,
                student_id=stu_id,
                source="excel",
                status="待审核",
            )
            session.add(response)
            session.flush()

            total = 0.0
            if tpl_id not in tq_cache:
                tq_cache[tpl_id] = list(
                    session.scalars(
                        select(TemplateQuestion)
                        .where(TemplateQuestion.exam_template_id == tpl_id)
                        .order_by(TemplateQuestion.idx)
                    )
                )
            for tq in tq_cache[tpl_id]:
                qkp = tq.kps[0] if tq.kps else None
                code = session.get(KnowledgePoint, qkp.kp_id).code if qkp else None
                m = true_mastery(alias, code, exam_date) if code else 0.7
                p = max(0.03, min(0.97, m + (0.5 - tq.difficulty_est) * 0.2 + rng.gauss(0, 0.05)))
                if tq.q_type == "选择":
                    correct = rng.random() < p
                    score = tq.full_score if correct else 0.0
                    option = "A" if correct else rng.choice(letters[1:])
                else:
                    ratio = max(0.0, min(1.0, rng.gauss(m, 0.18)))
                    score = round(tq.full_score * ratio * 2) / 2
                    option = None
                total += score
                session.add(ResponseAnswer(
                    exam_response_id=response.id,
                    template_question_id=tq.id,
                    score=score,
                    chosen_option=option,
                ))
            response.total_score = round(total, 2)

    session.flush()
    return truth
