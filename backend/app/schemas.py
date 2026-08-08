"""Pydantic 请求/响应模型（与 DESIGN §10 字段对齐）。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class KbImportRequest(BaseModel):
    yaml_path: str


class SchoolCreate(BaseModel):
    name: str


class ClassCreate(BaseModel):
    name: str
    grade: int
    subject: str = "数学"
    student_aliases: list[str] = Field(default_factory=list)


class ProgressUpdate(BaseModel):
    kp_codes: list[str]
    taught_at: date


class KpTag(BaseModel):
    code: str
    weight: float = 1.0


class QuestionCreate(BaseModel):
    idx: int
    stem: str = ""
    q_type: str = "解答"        # 选择|填空|解答
    full_score: float
    cog_level: str = "应用"     # 识记|理解|应用|综合
    difficulty_est: float = 0.5
    n_options: int | None = None
    kps: list[KpTag] = Field(default_factory=list)


class ExamCreate(BaseModel):
    kb_version_id: int
    class_id: int
    name: str
    exam_date: date
    type: str = "单元"          # 单元|期中|期末|练习|补录|诊断
    questions: list[QuestionCreate]


class ManualScores(BaseModel):
    student_id: int
    scores: dict[int, float]    # {题号: 得分}


class MasteryQuery(BaseModel):
    as_of: date | None = None


# ---------------------------------------------------------------------------
# 前端交互补丁端点（审核台逐题改标 / 低置信得分修正 / 教师否决）
# ---------------------------------------------------------------------------


class QuestionTagsUpdate(BaseModel):
    kps: list[KpTag] = Field(min_length=1)
    reviewer: str = "teacher"


class AnswerUpdate(BaseModel):
    score: float | None = None
    chosen_option: str | None = None
    reviewer: str = "teacher"


class AttributionOverride(BaseModel):
    note: str = ""
    reviewer: str = "teacher"


class BatchAssignRequest(BaseModel):
    student_id: int


class ProgressPatchRequest(BaseModel):
    taught_at: date


class KpCreateRequest(BaseModel):
    code: str
    name: str
    grade: int
    chapter: str = ""
    semester: int = 0
    description: str = ""
    cog_levels_expected: list[str] = Field(default_factory=list)
    difficulty_prior: float = 0.5
    mastery_floor: float = 0.6
    importance: str = "核心"    # 基础/核心/拓展（kb-improvement-design K5）


class KpUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    chapter: str | None = None
    semester: int | None = None
    cog_levels_expected: list[str] | None = None
    difficulty_prior: float | None = None
    mastery_floor: float | None = None
    importance: str | None = None
    archived: bool | None = None


class RelationCreateRequest(BaseModel):
    from_kp_id: int
    to_kp_id: int
    type: str
    weight: float = 1.0


class RelationUpdateRequest(BaseModel):
    type: str | None = None
    weight: float | None = None


class KbVersionPatchRequest(BaseModel):
    status: str


class SuggestQuestionItem(BaseModel):
    idx: int
    stem: str = ""
    q_type: str = "解答"  # 选择|填空|解答


class SuggestQuestionRequest(BaseModel):
    questions: list[SuggestQuestionItem]
