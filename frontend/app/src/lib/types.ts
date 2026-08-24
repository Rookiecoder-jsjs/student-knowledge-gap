/** 与后端 app/api/routes.py 响应结构对齐的类型定义。 */

export interface ClassSummary {
  class_id: number;
  name: string;
  grade: number;
  subject: string;
  school_id: number;
  student_count: number;
  exam_count: number;
}

export interface ClassOverviewLatestExam {
  exam_id: number;
  name: string;
  exam_date: string;
  type: string;
  submitted: number;
  pending: number;
}

export interface ClassOverview {
  class_id: number;
  name: string;
  grade: number;
  subject: string;
  school_id: number;
  student_count: number;
  exam_count: number;
  todo_count: number;
  latest_exam: ClassOverviewLatestExam | null;
  progress: { taught: number; total: number };
}

export interface StudentInfo {
  student_id: number;
  name_or_alias: string;
  external_code: string;
}

export interface ProgressEntry {
  kp_id: number;
  code: string;
  name: string;
  taught_at: string;
  archived: boolean;
}

export interface ExamSummary {
  exam_id: number;
  class_id: number;
  name: string;
  exam_date: string;
  type: string;
  source: string;
  question_count: number;
  response_counts: Record<string, number>;
  unreviewed_tags: number;
}

export interface KpTagView {
  tag_id: number;
  code: string;
  name: string;
  weight: number;
  source: string;
  confidence: number;
  reviewed: boolean;
  reviewed_by: string | null;
}

export interface QuestionView {
  question_id: number;
  idx: number;
  stem: string;
  q_type: string;
  full_score: number;
  cog_level: string;
  n_options: number | null;
  kps: KpTagView[];
}

export interface ExamDetail {
  exam_id: number;
  class_id: number;
  name: string;
  exam_date: string;
  type: string;
  source: string;
  questions: QuestionView[];
}

export type CollectStatus = "未采集" | "待审核" | "已提交";

export interface ResponseRow {
  student_id: number;
  name_or_alias: string;
  status: CollectStatus;
  response_id: number | null;
  total_score: number | null;
  low_confidence_count: number;
}

export interface ResponsesMatrix {
  exam_id: number;
  summary: Record<CollectStatus, number>;
  responses: ResponseRow[];
}

export interface ReviewTagItem {
  question_idx: number;
  question_id: number;
  stem: string;
  kp_id: number;
  kp_code: string;
  kp_name: string;
  confidence: number;
  source: string;
  review_reason: "低置信标注" | "高置信抽样" | "待批量批准";
}

export interface ReviewAnswerItem {
  answer_id: number;
  student_id: number;
  student_name: string;
  question_idx: number;
  score: number;
  full_score: number;
  confidence: number;
  band: string;
}

export interface ReviewQueue {
  unreviewed_tags: ReviewTagItem[];
  low_confidence_answers: ReviewAnswerItem[];
}

export interface WeakItem {
  code: string;
  name: string;
  mastery: number | null;
  criterion: string;
  evidence_count: number;
  trajectory: string;
  stale: boolean;
  class_common: boolean;
}

export interface Weaknesses {
  student_id: number;
  as_of: string;
  weak: WeakItem[];
  gates: Record<string, number>;
}

export interface AttributionView {
  id: number;
  kp: string;
  type: string;
  confidence: number;
  root_kp: string | null;
  prediction: string;
  status: string;
}

export interface MasteryItem {
  code: string;
  name: string;
  mastery: number;
}

export interface ReportSummary {
  report_id: number;
  type: string;
  class_id: number | null;
  student_id: number | null;
  exam_id: number | null;
  generated_at: string | null;
}

export interface QuestionCreate {
  idx: number;
  stem?: string;
  q_type?: string;
  full_score: number;
  cog_level?: string;
  difficulty_est?: number;
  n_options?: number | null;
  kps: { code: string; weight: number }[];
}

export interface SuggestQuestionItem {
  idx: number;
  stem: string;
  q_type: string;
}

export interface SuggestedTag {
  code: string;
  name: string;
  weight: number;
}

export interface SuggestionResult {
  suggestions: { idx: number; kps: SuggestedTag[]; confidence: number }[];
  model_version: string;
  prompt_version: string;
  warnings: string[];
}

// ---------------------------------------------------------------------------
// 批量拍照录入（DESIGN 批量录入 v0.3）
// ---------------------------------------------------------------------------

export type BatchItemStatus =
  | "queued"
  | "parsing"
  | "matched"
  | "unmatched"
  | "failed"
  | "duplicate"
  | "discarded";

export interface BatchItem {
  id: number;
  file_name: string;
  detected_name: string | null;
  matched_student_id: number | null;
  matched_student_name: string | null;
  status: BatchItemStatus;
  match_confidence: number | null;
  warnings: string[];
}

export interface BatchJob {
  job_id: number;
  status: string;
  items: BatchItem[];
}

export interface BatchJobSummary {
  job_id: number;
  status: string;
  counts: Record<string, number>;
}

// ---------------------------------------------------------------------------
// 知识库浏览与编辑（kb-edit §4.1）
// ---------------------------------------------------------------------------

export interface KpVersion {
  id: number;
  subject: string;
  textbook_edition: string;
  version: string;
  status: string;
  created_at: string | null;
  kp_count: number;
  is_active: boolean;
}

export interface KpRelationEndpoint {
  id: number;
  code: string;
  name: string;
}

export interface KpRelationView {
  id: number;
  from: KpRelationEndpoint;
  to: KpRelationEndpoint;
  type: string;
  weight: number;
}

// ---------------------------------------------------------------------------
// 班级诊断单 / 考试概况（diagnosis-sheet-redesign F1/F3/F8）
// ---------------------------------------------------------------------------

export interface QualityReportSnapshot {
  class: string;
  exam: string;
  exam_date: string;
  committed: number;
  pending: number;
  stats: { mean: number | null; max: number | null; min: number | null };
  question_rates: {
    idx: number;
    q_type: string;
    full_score: number;
    rate: number | null;
    kps: string;
    low: boolean;
  }[];
  common_weak: {
    code: string;
    name: string;
    class_avg: number;
    weak_share: number;
    n: number;
  }[];
}

export interface DiagnosisSheetStatus {
  student_count: number;
  exam_count: number;
  data_as_of: string | null;
  weak_kp_total: number;
  common_weak: { kp: string; weak_share_pct: number; class_avg_mastery_pct: number }[];
  trend: { prev_exam: string | null; entered: string[]; exited: string[] };
}

export interface ImprovementAdvice {
  report_id: number;
  markdown: string;
  generated_at: string | null;
  writer: { model: string; prompt_version: string } | null;
  exam_id: number | null;
}

export interface PastExamEntry {
  exam_id: number;
  name: string;
  exam_date: string;
  type: string;
}

export interface ClassDiagnosisSheet {
  class_id: number;
  status: DiagnosisSheetStatus;
  improvement_advice: ImprovementAdvice | null;
  actions: { pending_confirm: number; rows: unknown[] };
  intervention_summary: null;
  past_exams: PastExamEntry[];
}
