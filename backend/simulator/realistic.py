"""真实模拟器：真实中文姓名 + 全学年真实试卷 + 随机薄弱植入（自包含）。

与 synthetic.py / large_scale.py 的区别（解决「演示数据不像真实班级」问题）：
- 学生用真实中文姓名（姓氏池 + 双字名池，班内去重），学籍号格式外部编码；
- 试卷由 real_papers.build_realistic_paper 生成：真实题干、真实分值结构
  （选择/填空/解答，满分 100/120），而非「（合成题）考查 xxx」占位；
- 植入逻辑沿用 large_scale 已验证的随机方案（前置缺陷根源 / 遗忘衰减 / 班级共性），
  位置随机，管线必须在「未知位置」发现薄弱并归因；
- 13 场考试覆盖全学年（七上 7 场 + 七下 6 场），七下考七下内容（C7B-*）。

时间线（人教版 七年级）：
  七上：入学诊断(9/5) → 一单元(9/30) → 二单元(10/28) → 期中(11/20)
        → 三单元(12/18) → 四单元(1/8) → 期末(1/15)
  七下：五单元(3/5) → 六单元(3/31) → 期中(4/28) → 七单元(5/26)
        → 八单元(6/23) → 学年期末(7/17)
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
from simulator.real_papers import build_realistic_paper

# 全学年 13 场考试（名称, 日期, 类型, 覆盖容器, 是否累积复习型）
REALISTIC_SCHEDULE: list[tuple[str, date, str, list[str], bool]] = [
    ("入学诊断（补录）", date(2025, 9, 5), "诊断", ["C6-BRIDGE"], False),
    ("第一单元检测", date(2025, 9, 30), "单元", ["C7A-01"], False),
    ("第二单元检测", date(2025, 10, 28), "单元", ["C7A-02"], False),
    ("期中考试（上）", date(2025, 11, 20), "期中", ["C7A-01", "C7A-02"], True),
    ("第三单元检测", date(2025, 12, 18), "单元", ["C7A-03"], False),
    ("第四单元检测", date(2026, 1, 8), "单元", ["C7A-04"], False),
    ("期末考试（上）", date(2026, 1, 15), "期末", ["C7A-01", "C7A-02", "C7A-03", "C7A-04"], True),
    ("第五单元检测", date(2026, 3, 5), "单元", ["C7B-05"], False),
    ("第六单元检测", date(2026, 3, 31), "单元", ["C7B-06"], False),
    ("期中考试（下）", date(2026, 4, 28), "期中", ["C7A-04", "C7B-05", "C7B-06"], True),
    ("第七单元检测", date(2026, 5, 26), "单元", ["C7B-07"], False),
    ("第八单元检测", date(2026, 6, 23), "单元", ["C7B-08", "C7B-09", "C7B-10"], False),
    ("学年期末考试", date(2026, 7, 17), "期末",
     ["C7A-01", "C7A-02", "C7A-03", "C7A-04", "C7B-05", "C7B-06", "C7B-07", "C7B-08", "C7B-09", "C7B-10"], True),
]

# 寒假分界（遗忘植入：上学期高掌握、寒假后跌至低掌握）
SEMESTER_BREAK = date(2026, 2, 1)

# 中文姓名素材
SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何林罗高郑梁谢宋唐许韩冯邓曹彭曾萧田董潘袁蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
GIVEN_POOL = list("伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英慧玉建华文军雪云凤斌海珍红梅琴欣晨宇昊昊然俊哲家豪思涵雨欣梦琪志强志远晓东国华嘉豪子涵欣怡文静晓燕雅婷欣怡浩然天佑致远润泽浩然心怡天翊")

# 学籍号年份前缀 + 班号
STUDENT_ID_YEAR = 2025


@dataclass
class RealisticTruth:
    """真实模拟器真值表，供验证断言与报告抽样使用。"""

    student_ids: dict[str, int] = field(default_factory=dict)        # 姓名 -> id
    names_by_id: dict[int, str] = field(default_factory=dict)        # id -> 姓名
    class_of: dict[str, int] = field(default_factory=dict)           # 姓名 -> class_id
    class_ids: list[int] = field(default_factory=list)
    planted_weak: dict[str, set[str]] = field(default_factory=dict)  # 姓名 -> 植入薄弱 code 集
    planted_roots: dict[str, str] = field(default_factory=dict)      # 姓名 -> 植入根源 code
    planted_descendants: dict[str, set[str]] = field(default_factory=dict)  # 姓名 -> 应归因后代
    forgetting: dict[str, str] = field(default_factory=dict)         # 姓名 -> 遗忘 kp code
    class_common_kps: dict[int, str] = field(default_factory=dict)   # class_id -> 班级共性 code
    exam_ids: dict[tuple[str, int], int] = field(default_factory=dict)  # (考试名, class_id) -> template_id
    exam_names: list[str] = field(default_factory=list)
    exam_dates: dict[str, date] = field(default_factory=dict)


def _make_names(rng: random.Random, n: int, used_global: set[str]) -> list[str]:
    """生成 n 个真实中文姓名（姓氏 + 1~2 字名），与已用集合去重。"""
    names: list[str] = []
    while len(names) < n:
        given = rng.choice(GIVEN_POOL)
        if rng.random() < 0.35:
            given += rng.choice(GIVEN_POOL)
        name = rng.choice(SURNAMES) + given
        if name not in used_global and name not in names:
            names.append(name)
    return names


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
    for rel in session.scalars(select(KpRelation).where(KpRelation.type == "contains")):
        container_code = id_to_code.get(rel.from_kp_id)
        child_code = id_to_code.get(rel.to_kp_id)
        if container_code and child_code:
            members.setdefault(container_code, []).append(child_code)
    for c in members:
        members[c].sort()
    return members


def build_realistic_simulation(
    session: Session,
    kb_version_id: int,
    *,
    n_classes: int = 4,
    n_per_class: int = 50,
    n_prereq_roots: int = 12,
    n_forget_kps: int = 4,
    seed: int = 20250810,
) -> RealisticTruth:
    """构建真实大规模模拟：4 班 × 50 人、全学年 13 场、随机薄弱植入位置。

    试卷用 real_papers.build_realistic_paper 生成（真实题干 + 真实分值）。
    作答走真实 mastery 模型（基础能力 + 植入 + 难度噪声），状态「待审核」，
    交给真实 commit 管线提交（证据事件不可变追加）。
    """
    rng = random.Random(seed)
    truth = RealisticTruth()
    truth.exam_names = [e[0] for e in REALISTIC_SCHEDULE]
    truth.exam_dates = {e[0]: e[1] for e in REALISTIC_SCHEDULE}

    clazz0 = session.scalars(select(Class).limit(1)).first()
    school_id = clazz0.school_id if clazz0 else None
    if school_id is None:
        school = School(name="实验中学（东校区）")
        session.add(school)
        session.flush()
        school_id = school.id

    # ---- 1. 班级 & 学生（真实姓名） ----
    used_global: set[str] = set()
    for ci in range(n_classes):
        clazz = Class(
            school_id=school_id,
            name=f"七年级({ci + 1})班",
            grade=7,
            subject="数学",
        )
        session.add(clazz)
        session.flush()
        truth.class_ids.append(clazz.id)
        names = _make_names(rng, n_per_class, used_global)
        used_global.update(names)
        for si, name in enumerate(names, start=1):
            stu = Student(
                school_id=school_id,
                class_id=clazz.id,
                name_or_alias=name,
                external_code=f"{STUDENT_ID_YEAR}{ci + 1:02d}{si:03d}",
            )
            session.add(stu)
            session.flush()
            truth.student_ids[name] = stu.id
            truth.names_by_id[stu.id] = name
            truth.class_of[name] = clazz.id
            truth.planted_weak[name] = set()

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

    # 候选根源 = 有后代的主年级知识点；遗忘候选 = 上下学期都有覆盖的知识点
    candidate_roots = sorted(
        c for c, kp in kp_rows.items()
        if not c.startswith("C") and c not in container_members.get("C6-BRIDGE", [])
        and kp.id in desc_map and desc_map[kp.id]
    )
    forget_candidates = sorted(
        c for c in all_codes
        if not c.startswith("M6")
        and (c in container_members.get("C7A-01", []) or c in container_members.get("C7A-02", []))
    )

    names = list(truth.student_ids)
    base_ability = {n: rng.uniform(0.65, 0.92) for n in names}

    # ---- 2. 随机前置缺陷植入 ----
    weak_level: dict[tuple[str, str], float] = {}
    used_students: set[str] = set()
    n_roots = min(n_prereq_roots, len(candidate_roots))
    chosen_roots = rng.sample(candidate_roots, n_roots) if candidate_roots else []
    for root_code in chosen_roots:
        root_id = kp_rows[root_code].id
        desc_codes = sorted(
            graph.kp(did).code for did in desc_map.get(root_id, set())
        )
        if not desc_codes:
            continue
        n_desc = min(rng.randint(1, 2), len(desc_codes))
        chosen_desc = rng.sample(desc_codes, n_desc)
        avail = [n for n in names if n not in used_students]
        if len(avail) < 4:
            break
        group = rng.sample(avail, rng.randint(4, min(8, len(avail))))
        root_level = rng.uniform(0.22, 0.34)
        for nm in group:
            used_students.add(nm)
            truth.planted_roots[nm] = root_code
            truth.planted_weak[nm].add(root_code)
            weak_level[(nm, root_code)] = root_level
            truth.planted_descendants.setdefault(nm, set()).update(chosen_desc)
            for dc in chosen_desc:
                truth.planted_weak[nm].add(dc)
                weak_level[(nm, dc)] = rng.uniform(0.38, 0.50)

    # ---- 3. 随机遗忘衰减植入（寒假间隔） ----
    n_forget = min(n_forget_kps, len(forget_candidates))
    chosen_forget = rng.sample(forget_candidates, n_forget) if forget_candidates else []
    for fk in chosen_forget:
        avail = [n for n in names if n not in used_students]
        if len(avail) < 3:
            break
        group = rng.sample(avail, rng.randint(3, min(5, len(avail))))
        for nm in group:
            used_students.add(nm)
            truth.forgetting[nm] = fk
            truth.planted_weak[nm].add(fk)

    # ---- 4. 班级共性薄弱（每班 1 个随机） ----
    common_level: dict[tuple[str, str], float] = {}
    for class_id in truth.class_ids:
        class_names = [n for n in names if truth.class_of[n] == class_id]
        indiv_used = {c for n in class_names for c in truth.planted_weak.get(n, set())}
        cands = [c for c in all_codes if not c.startswith("M6") and c not in indiv_used]
        if not cands:
            continue
        common_kp = rng.choice(cands)
        truth.class_common_kps[class_id] = common_kp
        for nm in class_names:
            lvl = rng.uniform(0.42, 0.55)
            common_level[(nm, common_kp)] = lvl
            truth.planted_weak[nm].add(common_kp)

    session.flush()

    def true_mastery(nm: str, code: str, when: date) -> float:
        m = base_ability[nm]
        if (nm, code) in weak_level:
            m = weak_level[(nm, code)]
        if nm in truth.forgetting and truth.forgetting[nm] == code:
            m = 0.85 if when < SEMESTER_BREAK else 0.45
        if (nm, code) in common_level:
            m = min(m, common_level[(nm, code)])
        return max(0.05, min(0.95, m))

    # ---- 5. 试卷生成 + 教学进度 ----
    taught: set[int] = set()
    for name, exam_date, type_, containers, _cumul in REALISTIC_SCHEDULE:
        for class_id in truth.class_ids:
            questions = build_realistic_paper(kp_rows, containers, type_, rng)
            tpl = create_template(
                session, kb_version_id, class_id, name, exam_date, type_, questions
            )
            truth.exam_ids[(name, class_id)] = tpl.id

        # 教学进度：该场覆盖的 grade7 知识点在考前一天标记已教（首班登记，复制到其余班）
        covered_codes = set()
        for cont in containers:
            covered_codes.update(container_members.get(cont, []))
        for code in covered_codes:
            if code.startswith("M6"):
                continue
            kp_id = kp_rows[code].id
            if kp_id not in taught:
                session.add(TeachingProgress(
                    class_id=truth.class_ids[0],
                    kp_id=kp_id,
                    taught_at=exam_date - timedelta(days=1),
                ))
                taught.add(kp_id)
    for class_id in truth.class_ids[1:]:
        for kp_id in taught:
            session.add(TeachingProgress(
                class_id=class_id,
                kp_id=kp_id,
                taught_at=date(2025, 9, 1),
            ))
    session.flush()

    # ---- 6. 作答（待审核，交给真实 commit 管线提交） ----
    letters = "ABCD"
    tq_cache: dict[int, list[TemplateQuestion]] = {}
    for nm, stu_id in truth.student_ids.items():
        class_id = truth.class_of[nm]
        for name, exam_date, _t, _p, _c in REALISTIC_SCHEDULE:
            tpl_id = truth.exam_ids[(name, class_id)]
            response = ExamResponse(
                exam_template_id=tpl_id,
                student_id=stu_id,
                source="excel",
                status="待审核",
            )
            session.add(response)
            session.flush()

            if tpl_id not in tq_cache:
                tq_cache[tpl_id] = list(
                    session.scalars(
                        select(TemplateQuestion)
                        .where(TemplateQuestion.exam_template_id == tpl_id)
                        .order_by(TemplateQuestion.idx)
                    )
                )
            total = 0.0
            for tq in tq_cache[tpl_id]:
                qkp = tq.kps[0] if tq.kps else None
                code = session.get(KnowledgePoint, qkp.kp_id).code if qkp else None
                m = true_mastery(nm, code, exam_date) if code else 0.7
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
                session.add(ResponseAnswer(
                    exam_response_id=response.id,
                    template_question_id=tq.id,
                    score=score,
                    chosen_option=option,
                ))
            response.total_score = round(total, 2)

    session.flush()
    return truth


def _descendant_map(graph: KpGraph) -> dict[int, set[int]]:
    """祖先 kp_id -> 后代 kp_id 集合（复用 KpGraph.prerequisite_chain 反向构建）。"""
    desc: dict[int, set[int]] = {}
    for kp_id in graph.grade7_kp_ids():
        for anc_id, _depth, _w in graph.prerequisite_chain(kp_id, PREREQ_MAX_DEPTH):
            desc.setdefault(anc_id, set()).add(kp_id)
    return desc
