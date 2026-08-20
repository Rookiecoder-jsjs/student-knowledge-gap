"""ORM 模型 — 与 DESIGN.md §10 数据模型逐表对齐。

设计不变量在代码中的落点：
- 不变量①：exam_response.status 状态机（待审核→已提交），分析层只查「已提交」；
- 不变量②：evidence_event 只追加不修改；掌握度不建表、实时推导；
- 不变量③：question_kp.source/confidence/reviewed_by 承载 LLM 输出闸门；
- 不变量④：report.snapshot_json 物化留档，数字全部模板注入。
"""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow

# ---------------------------------------------------------------------------
# 租户与组织（多租户仅预留字段，MVP 不建权限体系）
# ---------------------------------------------------------------------------


class School(Base):
    __tablename__ = "school"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))

    classes: Mapped[list[Class]] = relationship(back_populates="school")


class Class(Base):
    __tablename__ = "class"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id"))
    name: Mapped[str] = mapped_column(String(100))
    grade: Mapped[int] = mapped_column(Integer)          # 年级，如 7 = 初一
    subject: Mapped[str] = mapped_column(String(20), default="数学")

    school: Mapped[School] = relationship(back_populates="classes")
    students: Mapped[list[Student]] = relationship(back_populates="clazz")
    exam_templates: Mapped[list[ExamTemplate]] = relationship(back_populates="clazz")
    progress: Mapped[list[TeachingProgress]] = relationship(back_populates="clazz")


class Student(Base):
    __tablename__ = "student"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id"))
    class_id: Mapped[int] = mapped_column(ForeignKey("class.id"))
    name_or_alias: Mapped[str] = mapped_column(String(100))   # 可用化名（PII 最小化）
    external_code: Mapped[str] = mapped_column(String(50), default="")  # 学籍号等外部编码

    clazz: Mapped[Class] = relationship(back_populates="students")


class Teacher(Base):
    __tablename__ = "teacher"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id"))
    name: Mapped[str] = mapped_column(String(100))


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------


class KbVersion(Base):
    __tablename__ = "kb_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(20))
    textbook_edition: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(20), default="0.1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|reviewed|active

    knowledge_points: Mapped[list[KnowledgePoint]] = relationship(back_populates="kb_version")


class KnowledgePoint(Base):
    __tablename__ = "knowledge_point"
    __table_args__ = (UniqueConstraint("kb_version_id", "code", name="uq_kb_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_version_id: Mapped[int] = mapped_column(ForeignKey("kb_version.id"))
    code: Mapped[str] = mapped_column(String(30))            # 如 M7A-105
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    grade: Mapped[int] = mapped_column(Integer)              # 年级是节点属性而非分区
    semester: Mapped[int] = mapped_column(Integer, default=0)  # 1 上 / 2 下 / 0 不限
    chapter: Mapped[str] = mapped_column(String(100), default="")
    # 期望认知层级 JSON 数组：["识记","理解","应用","综合"]
    cog_levels_expected: Mapped[list] = mapped_column(JSON, default=list)
    difficulty_prior: Mapped[float] = mapped_column(Float, default=0.5)
    mastery_floor: Mapped[float] = mapped_column(Float, default=0.6)  # 薄弱绝对底线（可按点配置）
    importance: Mapped[str] = mapped_column(String(10), default="核心")  # 基础/核心/拓展（kb-improvement-design K5）
    archived: Mapped[bool] = mapped_column(default=False)  # 软归档：分析层排除，不删行（见 kb-edit §3.1）

    kb_version: Mapped[KbVersion] = relationship(back_populates="knowledge_points")


class KpRelation(Base):
    __tablename__ = "kp_relation"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_kp_id: Mapped[int] = mapped_column(ForeignKey("knowledge_point.id"))
    to_kp_id: Mapped[int] = mapped_column(ForeignKey("knowledge_point.id"))
    type: Mapped[str] = mapped_column(String(20))  # prerequisite|contains|confusable|spiral
    weight: Mapped[float] = mapped_column(Float, default=1.0)  # prerequisite 强度 0~1
    audit_status: Mapped[str] = mapped_column(String(20), default="draft")


class TeachingProgress(Base):
    """教学进度：某班已教某知识点（未覆盖 → 「未学到」，绝不判薄弱）。"""

    __tablename__ = "teaching_progress"
    __table_args__ = (UniqueConstraint("class_id", "kp_id", name="uq_progress"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("class.id"))
    kp_id: Mapped[int] = mapped_column(ForeignKey("knowledge_point.id"))
    taught_at: Mapped[date] = mapped_column(Date)

    clazz: Mapped[Class] = relationship(back_populates="progress")


# ---------------------------------------------------------------------------
# 考试与作答（两阶段结构：模板 + 学生作答）
# ---------------------------------------------------------------------------


class ExamTemplate(Base):
    __tablename__ = "exam_template"

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("class.id"))
    name: Mapped[str] = mapped_column(String(120))
    exam_date: Mapped[date] = mapped_column(Date)
    type: Mapped[str] = mapped_column(String(20))   # 单元|期中|期末|练习|补录|诊断
    source: Mapped[str] = mapped_column(String(20), default="excel")
    parse_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    clazz: Mapped[Class] = relationship(back_populates="exam_templates")
    questions: Mapped[list[TemplateQuestion]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )
    responses: Mapped[list[ExamResponse]] = relationship(back_populates="template")


class TemplateQuestion(Base):
    __tablename__ = "template_question"
    __table_args__ = (UniqueConstraint("exam_template_id", "idx", name="uq_tpl_q"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_template_id: Mapped[int] = mapped_column(ForeignKey("exam_template.id"))
    idx: Mapped[int] = mapped_column(Integer)                 # 题号
    stem: Mapped[str] = mapped_column(Text, default="")       # 题干摘要
    q_type: Mapped[str] = mapped_column(String(20))           # 选择|填空|解答
    full_score: Mapped[float] = mapped_column(Float)
    cog_level: Mapped[str] = mapped_column(String(20), default="应用")  # 识记|理解|应用|综合
    difficulty_est: Mapped[float] = mapped_column(Float, default=0.5)
    n_options: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 选择题选项数
    sub_items_json: Mapped[list | None] = mapped_column(JSON, nullable=True)

    template: Mapped[ExamTemplate] = relationship(back_populates="questions")
    kps: Mapped[list[QuestionKp]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class QuestionKp(Base):
    """题目-知识点标注（LLM 起草或教师标注，必经审核闸门）。"""

    __tablename__ = "question_kp"
    __table_args__ = (UniqueConstraint("template_question_id", "kp_id", name="uq_q_kp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    template_question_id: Mapped[int] = mapped_column(ForeignKey("template_question.id"))
    kp_id: Mapped[int] = mapped_column(ForeignKey("knowledge_point.id"))
    weight: Mapped[float] = mapped_column(Float, default=1.0)   # 题内知识点权重分摊
    source: Mapped[str] = mapped_column(String(20), default="教师")  # LLM|教师
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    reviewed_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    question: Mapped[TemplateQuestion] = relationship(back_populates="kps")


class ExamResponse(Base):
    """一次学生作答。status 状态机 = 架构不变量①。"""

    __tablename__ = "exam_response"
    __table_args__ = (
        UniqueConstraint("exam_template_id", "student_id", name="uq_tpl_student"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_template_id: Mapped[int] = mapped_column(ForeignKey("exam_template.id"))
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"))
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(20), default="excel")  # excel|manual|photo
    status: Mapped[str] = mapped_column(String(20), default="待审核")
    # 状态机：上传 → 解析中 → 待审核 → 已提交（分析层只读「已提交」）

    template: Mapped[ExamTemplate] = relationship(back_populates="responses")
    answers: Mapped[list[ResponseAnswer]] = relationship(
        back_populates="response", cascade="all, delete-orphan"
    )


class ResponseAnswer(Base):
    __tablename__ = "response_answer"
    __table_args__ = (
        UniqueConstraint("exam_response_id", "template_question_id", name="uq_resp_q"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_response_id: Mapped[int] = mapped_column(ForeignKey("exam_response.id"))
    template_question_id: Mapped[int] = mapped_column(ForeignKey("template_question.id"))
    score: Mapped[float] = mapped_column(Float)
    chosen_option: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sub_scores_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    cascade_flag: Mapped[bool] = mapped_column(default=False)   # 级联错误标记
    parse_confidence: Mapped[float] = mapped_column(Float, default=1.0)

    response: Mapped[ExamResponse] = relationship(back_populates="answers")


# ---------------------------------------------------------------------------
# 题库（干预内容来源；P0 仅建表，P1 启用题族）
# ---------------------------------------------------------------------------


class BankQuestion(Base):
    __tablename__ = "bank_question"

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_version_id: Mapped[int] = mapped_column(ForeignKey("kb_version.id"))
    family_id: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 题族
    stem: Mapped[str] = mapped_column(Text, default="")
    q_type: Mapped[str] = mapped_column(String(20), default="解答")
    full_score: Mapped[float] = mapped_column(Float, default=10.0)
    difficulty: Mapped[float] = mapped_column(Float, default=0.5)
    source_template_question_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="待审核")


# ---------------------------------------------------------------------------
# 证据（不可变追加；掌握度由此推导，不存可变快照 — 不变量②）
# ---------------------------------------------------------------------------


class EvidenceEvent(Base):
    __tablename__ = "evidence_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"))
    kp_id: Mapped[int] = mapped_column(ForeignKey("knowledge_point.id"))
    response_answer_id: Mapped[int] = mapped_column(ForeignKey("response_answer.id"))
    source_type: Mapped[str] = mapped_column(String(20))     # 期中|期末|单元|练习|补录|诊断
    value: Mapped[float] = mapped_column(Float)              # 得分率（含猜测校正）
    weight: Mapped[float] = mapped_column(Float)             # 来源权重×题内分摊×级联降权
    cog_level: Mapped[str] = mapped_column(String(20), default="应用")
    class_avg_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    algo_version: Mapped[str] = mapped_column(String(30))


# ---------------------------------------------------------------------------
# 归因与干预
# ---------------------------------------------------------------------------


class Attribution(Base):
    """归因 = 带证据的方向性假设（教师可否决）。"""

    __tablename__ = "attribution"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"))
    kp_id: Mapped[int] = mapped_column(ForeignKey("knowledge_point.id"))
    type: Mapped[str] = mapped_column(String(30))
    # 前置缺陷|遗忘衰减|数据不足（MVP）；迷思概念|层级断层|熟练度不足（P1）
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    root_kp_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_point.id"), nullable=True
    )
    evidence_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    prediction: Mapped[str] = mapped_column(Text, default="")   # 可证伪预测
    status: Mapped[str] = mapped_column(String(20), default="active")
    # active|overridden（教师否决）|resolved（复测验证）
    teacher_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InterventionPlan(Base):
    __tablename__ = "intervention_plan"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"))
    status: Mapped[str] = mapped_column(String(20), default="草稿")  # 草稿|已批准|执行中|已完成
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(50), nullable=True)

    items: Mapped[list[PlanItem]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class PlanItem(Base):
    __tablename__ = "plan_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("intervention_plan.id"))
    kp_id: Mapped[int] = mapped_column(ForeignKey("knowledge_point.id"))
    action: Mapped[str] = mapped_column(String(20))   # 诊断|补学|练习|复测
    bank_question_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_question.id"), nullable=True
    )
    due: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="已布置")
    # 已布置|已完成|已逾期|已复测
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    plan: Mapped[InterventionPlan] = relationship(back_populates="items")


class RetestOutcome(Base):
    """复测前后掌握度对比 → 北极星指标（干预提升率）的数据源。"""

    __tablename__ = "retest_outcome"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_item_id: Mapped[int] = mapped_column(ForeignKey("plan_item.id"))
    mastery_before: Mapped[float] = mapped_column(Float)
    mastery_after: Mapped[float] = mapped_column(Float)
    improved: Mapped[bool] = mapped_column(default=False)


# ---------------------------------------------------------------------------
# 流水线与飞轮
# ---------------------------------------------------------------------------


class ParseJob(Base):
    __tablename__ = "parse_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    target: Mapped[str] = mapped_column(String(200))
    model_version: Mapped[str] = mapped_column(String(50), default="")
    prompt_version: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    cost: Mapped[float] = mapped_column(Float, default=0.0)


class ParseBatchItem(Base):
    """批量拍照录入的单文件项（DESIGN 批量录入 v0.3）。

    一个 ParseJob(target="batch:{exam_id}") 下挂 N 个 item，每 item 独立状态机：
    queued -> parsing -> matched / duplicate / unmatched / failed / discarded。
    与 ExamResponse 状态机相互独立，勿混。
    """

    __tablename__ = "parse_batch_item"
    __table_args__ = (
        Index("ix_pbi_job", "parse_job_id"),
        Index("ix_pbi_exam", "exam_template_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    parse_job_id: Mapped[int] = mapped_column(ForeignKey("parse_job.id"))
    exam_template_id: Mapped[int] = mapped_column(ForeignKey("exam_template.id"))
    file_name: Mapped[str] = mapped_column(String(200))
    file_path: Mapped[str | None] = mapped_column(String(300), nullable=True)  # tempfile 路径；failed 保留供重试
    detected_name: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 卷面姓名；终态即清空（见 §9）
    matched_student_id: Mapped[int | None] = mapped_column(ForeignKey("student.id"), nullable=True)
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 姓名匹配置信度，与 parse_confidence 无关
    status: Mapped[str] = mapped_column(String(20), default="queued")
    # queued | parsing | matched | unmatched | failed | duplicate | discarded
    response_id: Mapped[int | None] = mapped_column(ForeignKey("exam_response.id"), nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)  # 不得内嵌原始姓名
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 未匹配指派时免重调 LLM
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # G6：进入 parsing 的时刻，看门狗（reconcile_stale_runtime）计时基准；None=未开始解析
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CorrectionLog(Base):
    """教师修正日志 → 训练/审计信号飞轮。"""

    __tablename__ = "correction_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(50))
    old: Mapped[str | None] = mapped_column(Text, nullable=True)
    new: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_by: Mapped[str] = mapped_column(String(50))
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Report(Base):
    """报告物化留档（导出时快照，数字模板注入 — 不变量④）。"""

    __tablename__ = "report"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(30))      # quality_analysis|student_diagnosis
    class_id: Mapped[int | None] = mapped_column(ForeignKey("class.id"), nullable=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("student.id"), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    content_markdown: Mapped[str] = mapped_column(Text, default="")
    # 提交后自动生成时关联到具体考试；历史按需生成的报告为 None
    exam_id: Mapped[int | None] = mapped_column(ForeignKey("exam_template.id"), nullable=True)
    # AI 解读段缓存：首次查看时生成；同 prompt 版本复用，版本升级时刷新
    narrative_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# 便捷 JSON 工具（snapshot 字段）
# ---------------------------------------------------------------------------


def to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
