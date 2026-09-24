"""创建可重复执行的多班级、多年级、多学科演示数据。

这不是生产数据导入器，只用于本地/Docker 演示。脚本只新增或补齐带有
``demo-2026.09`` 标记的版本、班级、学生与一场阶段测评，不删除已有数据。

知识主题依据：
* 教育部《义务教育课程方案和课程标准（2022 年版）》；
* 人民教育出版社七年级数学目录（有理数、方程、几何、数据等）；
* 人民教育出版社八年级数学教材介绍（全等三角形、轴对称、函数等）。

语文、英语的主题按课标的阅读、表达、语言运用等学习任务群整理为演示目录，
每个版本仍应由学校教研组审核后再作为正式课程目录使用。

用法（backend/ 目录）：
    python scripts/seed_multigrade_demo.py

Docker：
    docker exec deploy-backend-1 python /app/scripts/seed_multigrade_demo.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app.ingestion.commit import add_manual_response, commit_exam  # noqa: E402
from app.ingestion.templates import create_template  # noqa: E402
from app.models import (  # noqa: E402
    Class,
    ExamResponse,
    ExamTemplate,
    KbVersion,
    KnowledgePoint,
    KpRelation,
    QuestionKp,
    School,
    Student,
    TemplateQuestion,
    TeachingProgress,
)


DEMO_VERSION = "demo-2026.09"
DEMO_EXAM = "阶段测评·演示数据"
STUDENT_COUNT = 12

SUBJECT_PREFIX = {"数学": "M", "语文": "YW", "英语": "EN"}

# 每个章节的叶子节点保持短而有意义，便于在圆包图中直接阅读。
CATALOG: dict[str, dict[int, list[tuple[str, list[str]]]]] = {
    "数学": {
        7: [
            ("有理数", ["正负数与数轴", "绝对值", "有理数运算"]),
            ("整式的加减", ["用字母表示数", "合并同类项", "去括号与整式化简"]),
            ("一元一次方程", ["方程与等式性质", "移项与合并", "实际问题建模"]),
            ("几何图形初步", ["直线射线线段", "角的度量", "几何图形表示"]),
            ("相交线与平行线", ["对顶角与邻补角", "平行线判定", "平行线性质"]),
            ("数据与概率基础", ["数据收集", "统计图表", "简单随机事件"]),
        ],
        8: [
            ("三角形与全等", ["三角形边角关系", "三角形内角和", "全等三角形判定"]),
            ("轴对称", ["轴对称图形", "垂直平分线性质", "等腰三角形性质"]),
            ("整式乘法与因式分解", ["幂的运算", "乘法公式", "因式分解方法"]),
            ("分式", ["分式基本性质", "分式运算", "分式方程"]),
            ("一次函数", ["函数概念", "一次函数图象", "一次函数应用"]),
            ("数据分析", ["平均数与加权平均数", "中位数与众数", "数据波动程度"]),
        ],
        9: [
            ("一元二次方程", ["一元二次方程概念", "配方法", "公式法与根的判别式"]),
            ("二次函数", ["二次函数图象", "顶点与对称轴", "二次函数应用"]),
            ("旋转与圆", ["图形旋转", "圆的基本性质", "圆周角与圆心角"]),
            ("相似三角形", ["比例线段", "相似三角形判定", "相似三角形应用"]),
            ("锐角三角函数", ["正弦余弦正切", "特殊角三角函数", "解直角三角形"]),
            ("概率初步", ["随机事件", "频率与概率", "用树状图求概率"]),
        ],
    },
    "语文": {
        7: [
            ("成长与亲情", ["叙事线索", "人物描写", "细节与情感"]),
            ("写景抒情", ["景物层次", "修辞赏析", "借景抒情"]),
            ("文言文入门", ["实词古今异义", "虚词意义", "句子翻译"]),
            ("写作基础", ["审题立意", "材料组织", "修改与润色"]),
            ("名著阅读", ["人物与情节", "主题探究", "阅读摘记"]),
        ],
        8: [
            ("新闻与传记", ["新闻要素", "消息结构", "人物精神概括"]),
            ("科普说明文", ["说明对象", "说明顺序", "说明方法"]),
            ("议论文初步", ["中心论点", "论据作用", "论证思路"]),
            ("文言文阅读", ["一词多义", "文言句式", "内容理解"]),
            ("名著与综合实践", ["专题探究", "读书报告", "口语交际"]),
        ],
        9: [
            ("小说阅读", ["故事情节", "人物形象", "环境描写与主题"]),
            ("议论文阅读", ["论点辨析", "论据补写", "论证语言"]),
            ("文言文比较阅读", ["词义迁移", "对比阅读", "主旨归纳"]),
            ("诗歌鉴赏", ["意象与意境", "炼字赏析", "表达技巧"]),
            ("非连续性文本", ["图表信息", "材料整合", "应用表达"]),
            ("综合写作", ["任务驱动写作", "结构与逻辑", "书面表达规范"]),
        ],
    },
    "英语": {
        7: [
            ("自我与家庭", ["主题词汇", "be 动词与代词", "自我介绍表达"]),
            ("学校与日常", ["一般现在时", "日常活动表达", "阅读寻读策略"]),
            ("兴趣与爱好", ["频度副词", "like doing 句型", "听说交际"]),
            ("天气与节日", ["天气词汇", "现在进行时", "文化话题阅读"]),
            ("购物与饮食", ["可数与不可数名词", "数量表达", "功能意念对话"]),
            ("过去经历", ["一般过去时", "不规则动词", "记叙文阅读"]),
        ],
        8: [
            ("个人成长", ["复合形容词", "比较级与最高级", "成长主题写作"]),
            ("健康与运动", ["身体与健康词汇", "情态动词", "提出建议"]),
            ("文化与旅行", ["旅行场景表达", "动词不定式", "跨文化阅读"]),
            ("环境与社会", ["环保词汇", "被动语态", "观点表达"]),
            ("故事与人物", ["过去进行时", "连词与从句", "叙事阅读策略"]),
            ("语言学习", ["现在完成时", "学习策略", "说明文写作"]),
        ],
        9: [
            ("校园生活", ["校园活动词汇", "定语从句", "校园主题写作"]),
            ("家庭与社会", ["家庭关系表达", "宾语从句", "观点与理由"]),
            ("科学与技术", ["科技词汇", "一般将来时", "科普阅读"]),
            ("文化与沟通", ["文化差异词汇", "状语从句", "跨文化沟通"]),
            ("环境保护", ["环境议题词汇", "情态动词被动语态", "倡议书写作"]),
            ("备考综合", ["语篇衔接", "长难句理解", "综合语言运用"]),
        ],
    },
}

STUDENT_NAMES = [
    "林子涵", "周雨欣", "陈思远", "王晨曦", "赵梓轩", "李欣怡",
    "张浩然", "刘语嫣", "杨致远", "黄诗涵", "吴宇辰", "徐嘉豪",
]


def ensure_kb(session, subject: str, grade: int) -> KbVersion:
    """确保学科×年级有一个可供演示的 active 版本。"""
    existing = session.scalar(
        select(KbVersion).where(
            KbVersion.subject == subject,
            KbVersion.grade == grade,
            KbVersion.version == DEMO_VERSION,
        )
    )
    if existing is not None:
        # 旧版脚本曾把语文叶子编码为 CN*，会被图谱约定误判为容器；
        # 只修复演示版本的编码，保留主键和所有关系。
        if subject == "语文":
            _repair_language_codes(session, existing.id)
        return existing

    # 复用仓库已有的完整七年级数学图谱，避免复制 127 个真实节点。
    if subject == "数学" and grade == 7:
        existing_math = session.scalar(
            select(KbVersion)
            .where(KbVersion.subject == subject)
            .order_by(KbVersion.id)
        )
        if existing_math is not None:
            if existing_math.grade is None:
                existing_math.grade = grade
            return existing_math

    prefix = SUBJECT_PREFIX[subject]
    chapters = CATALOG[subject][grade]
    kb = KbVersion(
        subject=subject,
        grade=grade,
        textbook_edition="人教版·课标主题演示目录",
        version=DEMO_VERSION,
        status="active",
    )
    session.add(kb)
    session.flush()

    chapter_rows: list[tuple[str, list[KnowledgePoint]]] = []
    for chapter_index, (chapter_name, point_names) in enumerate(chapters, start=1):
        container = KnowledgePoint(
            kb_version_id=kb.id,
            code=f"C{prefix}{grade}-{chapter_index:02d}",
            name=f"第{chapter_index}章 {chapter_name}",
            description=f"{grade}年级{subject}·{chapter_name}（演示目录）",
            grade=grade,
            semester=1 if chapter_index <= (len(chapters) + 1) // 2 else 2,
            chapter=chapter_name,
            cog_levels_expected=["理解", "应用"],
            difficulty_prior=0.45 + chapter_index * 0.02,
            mastery_floor=0.6,
            importance="核心",
        )
        session.add(container)
        session.flush()
        leaves: list[KnowledgePoint] = []
        for point_index, point_name in enumerate(point_names, start=1):
            leaf = KnowledgePoint(
                kb_version_id=kb.id,
                code=f"{prefix}{grade}-{chapter_index:02d}{point_index:02d}",
                name=point_name,
                description=f"围绕“{chapter_name}”的{point_name}，用于演示班级掌握度与前置关系。",
                grade=grade,
                semester=container.semester,
                chapter=chapter_name,
                cog_levels_expected=["理解", "应用"],
                difficulty_prior=0.35 + point_index * 0.06,
                mastery_floor=0.62 if point_index == 1 else 0.6,
                importance="基础" if point_index == 1 else "核心",
            )
            session.add(leaf)
            session.flush()
            leaves.append(leaf)
            session.add(KpRelation(from_kp_id=container.id, to_kp_id=leaf.id, type="contains", weight=1.0))
        chapter_rows.append((container.code, leaves))

    # 同章顺序依赖 + 跨章衔接 + 少量易混关系，形成可读的树状知识结构。
    previous_last: KnowledgePoint | None = None
    for _container_code, leaves in chapter_rows:
        if previous_last is not None:
            session.add(KpRelation(from_kp_id=previous_last.id, to_kp_id=leaves[0].id, type="prerequisite", weight=0.65))
        for left, right in zip(leaves, leaves[1:]):
            session.add(KpRelation(from_kp_id=left.id, to_kp_id=right.id, type="prerequisite", weight=0.8))
        if len(leaves) >= 3:
            session.add(KpRelation(from_kp_id=leaves[1].id, to_kp_id=leaves[2].id, type="confusable", weight=0.45))
        previous_last = leaves[-1]
    session.flush()
    return kb


def _repair_language_codes(session, kb_id: int) -> None:
    rows = list(
        session.scalars(
            select(KnowledgePoint).where(
                KnowledgePoint.kb_version_id == kb_id,
                KnowledgePoint.code.startswith("CN"),
            )
        )
    )
    if not rows:
        return
    old_codes = {kp.id: kp.code for kp in rows}
    for kp in rows:
        kp.code = f"TMP-{kp.id}"
    session.flush()
    for kp in rows:
        kp.code = f"YW{old_codes[kp.id][2:]}"
    session.flush()


def ensure_students(session, clazz: Class, grade: int, class_no: int, subject: str) -> list[Student]:
    students = list(session.scalars(select(Student).where(Student.class_id == clazz.id).order_by(Student.id)))
    if len(students) >= STUDENT_COUNT:
        return students
    prefix = SUBJECT_PREFIX[subject]
    for index in range(len(students), STUDENT_COUNT):
        session.add(
            Student(
                school_id=clazz.school_id,
                class_id=clazz.id,
                name_or_alias=STUDENT_NAMES[index],
                external_code=f"DEMO-{grade}-{class_no}-{prefix}-{index + 1:02d}",
            )
        )
    session.flush()
    return list(session.scalars(select(Student).where(Student.class_id == clazz.id).order_by(Student.id)))


def ensure_class_demo(session, school: School, subject: str, grade: int, class_no: int, kb: KbVersion) -> Class:
    grade_name = {7: "七", 8: "八", 9: "九"}.get(grade, str(grade))
    name = f"{grade_name}({class_no})班"
    clazz = session.scalar(
        select(Class).where(
            Class.school_id == school.id,
            Class.name == name,
            Class.grade == grade,
            Class.subject == subject,
        )
    )
    if clazz is None:
        clazz = Class(school_id=school.id, name=name, grade=grade, subject=subject)
        session.add(clazz)
        session.flush()

    students = ensure_students(session, clazz, grade, class_no, subject)
    leaves = list(
        session.scalars(
            select(KnowledgePoint)
            .where(KnowledgePoint.kb_version_id == kb.id, ~KnowledgePoint.code.startswith("C"))
            .order_by(KnowledgePoint.code)
        )
    )
    taught_count = max(3, round(len(leaves) * (0.48 + class_no * 0.1)))
    existing_progress = set(
        session.scalars(select(TeachingProgress.kp_id).where(TeachingProgress.class_id == clazz.id))
    )
    for kp in leaves[:taught_count]:
        if kp.id not in existing_progress:
            session.add(TeachingProgress(class_id=clazz.id, kp_id=kp.id, taught_at=date(2026, 3, 1)))

    exam = session.scalar(
        select(ExamTemplate).where(ExamTemplate.class_id == clazz.id, ExamTemplate.name == DEMO_EXAM)
    )
    selected = leaves[: min(5, len(leaves))]
    if exam is None:
        questions = [
            {
                "idx": index,
                "stem": f"{subject}演示题：{kp.name}的基础理解与应用。",
                "q_type": "解答",
                "full_score": 20.0,
                "cog_level": "应用",
                "kps": [{"code": kp.code, "weight": 1.0}],
            }
            for index, kp in enumerate(selected, start=1)
        ]
        exam = create_template(session, kb.id, clazz.id, DEMO_EXAM, date(2026, 3, 18), "诊断", questions, source="demo")
    elif not exam.questions and selected:
        # 首次运行的旧脚本可能已创建空考试；就地补上题目，避免产生重复考试卡片。
        for index, kp in enumerate(selected, start=1):
            tq = TemplateQuestion(
                exam_template_id=exam.id,
                idx=index,
                stem=f"{subject}演示题：{kp.name}的基础理解与应用。",
                q_type="解答",
                full_score=20.0,
                cog_level="应用",
                difficulty_est=0.5,
            )
            session.add(tq)
            session.flush()
            session.add(
                QuestionKp(
                    template_question_id=tq.id,
                    kp_id=kp.id,
                    weight=1.0,
                    source="教师",
                    confidence=1.0,
                    reviewed_by="teacher",
                )
            )
        session.flush()

    existing_students = set(
        session.scalars(
            select(ExamResponse.student_id).where(ExamResponse.exam_template_id == exam.id)
        )
    )
    for index, student in enumerate(students):
        if student.id in existing_students:
            continue
        scores = {
            question.idx: round(question.full_score * (0.55 + ((index * 11 + question.idx * 7) % 36) / 100), 2)
            for question in exam.questions
        }
        add_manual_response(session, exam.id, student.id, scores)
    commit_exam(session, exam.id)
    return clazz


def main() -> None:
    init_db(allow_create_all_fallback=True)
    with SessionLocal() as session:
        school = session.scalar(select(School).order_by(School.id))
        if school is None:
            school = School(name="知行实验学校")
            session.add(school)
            session.flush()

        created: list[Class] = []
        for subject, grades in CATALOG.items():
            for grade in (7, 8, 9):
                kb = ensure_kb(session, subject, grade)
                for class_no in (1, 2):
                    created.append(ensure_class_demo(session, school, subject, grade, class_no, kb))
        session.commit()

        print(f"多学科演示数据已就绪：{len(created)} 条班级×学科记录，学校={school.name}")
        print("覆盖：七/八/九年级 × 数学/语文/英语 × 每学科 2 个班")
        print(f"知识库演示版本：{DEMO_VERSION}（数学七年级复用现有完整图谱）")


if __name__ == "__main__":
    main()
