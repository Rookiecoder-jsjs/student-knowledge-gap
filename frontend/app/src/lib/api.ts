/** 后端 API 客户端：30 端点全覆盖，统一错误语义。 */

import type {
  AttributionView,
  BatchJob,
  BatchJobSummary,
  ClassDiagnosisSheet,
  ClassOverview,
  ClassSummary,
  ExamDetail,
  ExamSummary,
  InboxList,
  InboxSummary,
  KpRelationEndpoint,
  KpRelationView,
  KpVersion,
  MasteryItem,
  ProgressEntry,
  QualityReportSnapshot,
  QuestionCreate,
  ReportFull,
  ReportSummary,
  ReportTransition,
  ResponsesMatrix,
  ReviewQueue,
  StudentInfo,
  UsageLedger,
  Weaknesses,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError(0, "无法连接后端服务（请确认 uvicorn 已在 8000 端口启动）");
  }
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

function multipart(fields: Record<string, string>, file: File, fileKey = "file"): FormData {
  const fd = new FormData();
  fd.append(fileKey, file);
  for (const [k, v] of Object.entries(fields)) fd.append(k, v);
  return fd;
}

function multipartMulti(
  fields: Record<string, string>,
  files: File[],
  fileKey: string
): FormData {
  const fd = new FormData();
  for (const f of files) fd.append(fileKey, f);
  for (const [k, v] of Object.entries(fields)) fd.append(k, v);
  return fd;
}

// ---- 系统 -----------------------------------------------------------------

export const health = () => request<{ status: string }>("/health");

export interface KpNode {
  id: number;
  code: string;
  name: string;
  description: string;
  grade: number;
  semester: number;
  chapter: string;
  cog_levels_expected: string[];
  difficulty_prior: number;
  mastery_floor: number;
  importance: string;
  archived: boolean;
}

export interface KpDetail extends KpNode {
  kb_version_id: number;
  prerequisite_chain: (KpRelationEndpoint & { depth: number; weight: number })[];
  direct_prerequisites: (KpRelationEndpoint & { weight: number })[];
  successors: (KpRelationEndpoint & { relation_id: number; type: string; weight: number })[];
  containers: (KpRelationEndpoint & { relation_id: number; type: string; weight: number })[];
  contained: (KpRelationEndpoint & { relation_id: number; type: string; weight: number })[];
}

export const listKps = (kbVersionId?: number) =>
  request<{ kb_version_id: number; kps: KpNode[] }>(
    `/kb/kps${kbVersionId ? `?kb_version_id=${kbVersionId}` : ""}`
  );

export const listKbVersions = () =>
  request<{ versions: KpVersion[] }>("/kb/versions");

export const kpDetail = (kpId: number) => request<KpDetail>(`/kb/kps/${kpId}`);

export const listRelations = (kbVersionId?: number) =>
  request<{ kb_version_id: number; relations: KpRelationView[] }>(
    `/kb/relations${kbVersionId ? `?kb_version_id=${kbVersionId}` : ""}`
  );

// ---- 知识库编辑（kb-edit §4.3/§4.4）-------------------------------

export interface KpPreviewImpact {
  preview: boolean;
  current: { weak_count: number; floor: number };
  projected: { weak_count: number; floor: number };
  delta: number;
  note?: string;
}

export interface KpUpdateResult extends KpNode {
  impact?: { weak_count: number; floor: number } | null;
}

export interface KpDeleteResult {
  archived?: boolean;
  deleted?: boolean;
  hard?: boolean;
  kp_id?: number;
  evidence_refs?: number;
  question_refs?: number;
  progress_refs?: number;
  progress_cleared?: number;
}

export const createKp = (body: {
  code: string;
  name: string;
  grade: number;
  chapter?: string;
  semester?: number;
  description?: string;
  cog_levels_expected?: string[];
  difficulty_prior?: number;
  mastery_floor?: number;
  importance?: string;
}) => request<KpNode>("/kb/kps", json(body));

export const updateKp = (
  kpId: number,
  body: {
    name?: string;
    description?: string;
    chapter?: string;
    semester?: number;
    cog_levels_expected?: string[];
    difficulty_prior?: number;
    mastery_floor?: number;
    importance?: string;
    archived?: boolean;
  },
  preview = false
) =>
  request<KpUpdateResult | KpPreviewImpact>(
    `/kb/kps/${kpId}${preview ? "?preview=true" : ""}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );

export const deleteKp = (kpId: number, opts?: { force?: boolean; confirm?: boolean }) => {
  const q = new URLSearchParams();
  if (opts?.force) q.set("force", "true");
  if (opts?.confirm) q.set("confirm", "true");
  const qs = q.toString();
  return request<KpDeleteResult>(`/kb/kps/${kpId}${qs ? `?${qs}` : ""}`, {
    method: "DELETE",
  });
};

export const createRelation = (body: {
  from_kp_id: number;
  to_kp_id: number;
  type: string;
  weight?: number;
}) => request<KpRelationView>("/kb/relations", json(body));

export const deleteRelation = (relId: number) =>
  request<{ deleted: number }>(`/kb/relations/${relId}`, { method: "DELETE" });

// ---- 版本管理 + 导出（kb-edit §4.5/§4.6）-------------------------------

export interface KbCompatibility {
  active_version_id: number;
  target_version_id: number;
  missing_codes: string[];
  new_codes: string[];
  attribute_changes: {
    code: string;
    field: string;
    old: number | boolean;
    new: number | boolean;
  }[];
}

export const forkKbVersion = () =>
  request<{ id: number; status: string; forked_from: number }>("/kb/versions", {
    method: "POST",
  });

export const kbCompatibility = (versionId: number) =>
  request<KbCompatibility>(`/kb/versions/${versionId}/compatibility`);

export const patchKbVersion = (
  versionId: number,
  status: string,
  opts?: { confirm?: boolean; force?: boolean }
) => {
  const q = new URLSearchParams();
  if (opts?.confirm) q.set("confirm", "true");
  if (opts?.force) q.set("force", "true");
  const qs = q.toString();
  return request<{
    id: number;
    status: string;
    switched_from?: number;
    missing_codes_accepted?: string[];
    note?: string;
  }>(`/kb/versions/${versionId}${qs ? `?${qs}` : ""}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
};

export const exportKbUrl = (kbVersionId?: number) =>
  `${BASE}/kb/export${kbVersionId ? `?kb_version_id=${kbVersionId}` : ""}`;

// ---- 初始化 ----------------------------------------------------------------

export const kbImport = (yaml_path: string) =>
  request<{ kb_version_id: number; version: string }>("/kb/import", json({ yaml_path }));

export const kbUpload = (file: File) =>
  request<{ kb_version_id: number; version: string }>("/kb/upload", {
    method: "POST",
    body: multipart({}, file),
  });

export const createSchool = (name: string) =>
  request<{ school_id: number }>("/schools", json({ name }));

export const createClass = (
  schoolId: number,
  body: { name: string; grade: number; subject?: string; student_aliases: string[] }
) => request<{ class_id: number; student_ids: number[] }>(`/schools/${schoolId}/classes`, json(body));

export const updateProgress = (classId: number, kp_codes: string[], taught_at: string) =>
  request<{ added: number }>(`/classes/${classId}/progress`, json({ kp_codes, taught_at }));

export const getProgress = (classId: number) =>
  request<{ class_id: number; progress: ProgressEntry[] }>(`/classes/${classId}/progress`);

export const deleteProgress = (classId: number, kpId: number) =>
  request<{ deleted_kp_id: number }>(`/classes/${classId}/progress/${kpId}`, {
    method: "DELETE",
  });

export const patchProgress = (classId: number, kpId: number, taughtAt: string) =>
  request<{ class_id: number; kp_id: number; taught_at: string }>(
    `/classes/${classId}/progress/${kpId}`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ taught_at: taughtAt }) }
  );

// ---- 列表 ------------------------------------------------------------------

export const listClasses = () => request<{ classes: ClassSummary[] }>("/classes");

export const listClassesOverview = () =>
  request<{ classes: ClassOverview[] }>("/classes/overview");

export const listStudents = (classId: number) =>
  request<{ class_id: number; students: StudentInfo[] }>(`/classes/${classId}/students`);

export const listExams = (classId?: number) =>
  request<{ exams: ExamSummary[] }>(`/exams${classId ? `?class_id=${classId}` : ""}`);

export const examDetail = (examId: number) => request<ExamDetail>(`/exams/${examId}`);

export const examResponses = (examId: number) =>
  request<ResponsesMatrix>(`/exams/${examId}/responses`);

// ---- 考试录入 ---------------------------------------------------------------

export const createExam = (body: {
  kb_version_id: number;
  class_id: number;
  name: string;
  exam_date: string;
  type: string;
  questions: QuestionCreate[];
}) => request<{ exam_id: number; questions: number }>("/exams", json(body));

export const suggestQuestionTags = (
  questions: { idx: number; stem: string; q_type: string }[],
) =>
  request<import("./types").SuggestionResult>(
    "/kb/suggest-question-tags",
    json({ questions }),
  );

export const photoTemplate = (
  file: File,
  meta: { class_id: number; name: string; exam_date: string; type: string }
) =>
  request<{ exam_id: number; questions: number; warnings: string[] }>("/exams/photo-template", {
    method: "POST",
    body: multipart(
      { class_id: String(meta.class_id), name: meta.name, exam_date: meta.exam_date, type: meta.type },
      file
    ),
  });

export const importExcel = (examId: number, file: File) =>
  request<{ imported: number; unmatched_students: string[]; warnings: string[] }>(
    `/exams/${examId}/import-excel`,
    { method: "POST", body: multipart({}, file) }
  );

export const manualEntry = (examId: number, student_id: number, scores: Record<string, number>) =>
  request<{ response_id: number; total_score: number; status: string }>(
    `/exams/${examId}/manual`,
    json({ student_id, scores })
  );

export const commitExam = (examId: number) =>
  request<{
    committed_responses: number;
    evidence_events: number;
    quality_report?: boolean;
    diagnoses?: number;
    skipped: string[];
  }>(`/exams/${examId}/commit`, { method: "POST" });

// ---- 采集与审核 --------------------------------------------------------------

export const photoResponse = (examId: number, student_id: number, file: File) =>
  request<{ response_id: number; warnings: string[] }>(`/exams/${examId}/photo-response`, {
    method: "POST",
    body: multipart({ student_id: String(student_id) }, file),
  });

// ---- 批量拍照录入（DESIGN 批量录入 v0.3）-------------------------------

export const photoBatch = (examId: number, files: File[], sync = false) =>
  request<{ job_id: number; items: { id: number; file_name: string; status: string }[] }>(
    `/exams/${examId}/photo-batch`,
    { method: "POST", body: multipartMulti({ sync: String(sync) }, files, "files") }
  );

export const listBatchJobs = (examId: number) =>
  request<{ jobs: BatchJobSummary[] }>(`/exams/${examId}/batch-jobs`);

export const batchJob = (jobId: number) => request<BatchJob>(`/batch-jobs/${jobId}`);

export const assignBatchItem = (itemId: number, student_id: number) =>
  request<{ response_id: number; status: string }>(
    `/batch-items/${itemId}/assign`,
    json({ student_id })
  );

export const retryBatchItem = (itemId: number) =>
  request<{ id: number; status: string }>(`/batch-items/${itemId}/retry`, { method: "POST" });

export const discardBatchItem = (itemId: number) =>
  request<{ id: number; status: "discarded" }>(`/batch-items/${itemId}/discard`, {
    method: "POST",
  });

export const reviewQueue = (examId: number) => request<ReviewQueue>(`/exams/${examId}/review-queue`);

export const approveTags = (examId: number) =>
  request<{ approved: number; pending: number }>(`/exams/${examId}/approve-tags`, { method: "POST" });

export const patchTags = (
  questionId: number,
  kps: { code: string; weight: number }[]
) =>
  request<{ question_id: number; kps: string[]; reviewed: boolean }>(
    `/template-questions/${questionId}/tags`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kps }) }
  );

export const patchAnswer = (
  answerId: number,
  body: { score?: number; chosen_option?: string }
) =>
  request<{ answer_id: number; score: number; total_score: number; status: string }>(
    `/response-answers/${answerId}`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
  );

// ---- 分析 ------------------------------------------------------------------

export const getMastery = (studentId: number, asOf?: string) =>
  request<{ student_id: number; as_of: string; mastery: MasteryItem[] }>(
    `/students/${studentId}/mastery${asOf ? `?as_of=${asOf}` : ""}`
  );

export const getWeaknesses = (studentId: number, asOf?: string) =>
  request<Weaknesses>(`/students/${studentId}/weaknesses${asOf ? `?as_of=${asOf}` : ""}`);

export const runAttributions = (studentId: number) =>
  request<{ attributions: AttributionView[] }>(`/students/${studentId}/attributions`, {
    method: "POST",
  });

export const overrideAttribution = (attributionId: number, note: string) =>
  request<{ attribution_id: number; status: string; note: string | null }>(
    `/attributions/${attributionId}/override`,
    json({ note })
  );

// ---- 报告 ------------------------------------------------------------------

export const qualityReport = (classId: number, examId: number, narrative: boolean) =>
  request<{ report_id: number; markdown: string; snapshot: QualityReportSnapshot | null }>(
    `/classes/${classId}/quality-report?exam_id=${examId}&narrative=${narrative}`
  );

/** 班级诊断单聚合（diagnosis-sheet-redesign B1/F8）：滚动现状 + 最新改进意见。 */
export const classDiagnosisSheet = (classId: number) =>
  request<ClassDiagnosisSheet>(`/classes/${classId}/diagnosis-sheet`);

export const diagnosisReport = (
  studentId: number,
  narrative: boolean,
  asOf?: string,
  examId?: number
) =>
  request<{ report_id: number; markdown: string; as_of?: string | null }>(
    `/students/${studentId}/diagnosis?narrative=${narrative}${asOf ? `&as_of=${asOf}` : ""}${examId ? `&exam_id=${examId}` : ""}`
  );

export const listReports = (classId?: number, studentId?: number) => {
  const q = new URLSearchParams();
  if (classId) q.set("class_id", String(classId));
  if (studentId) q.set("student_id", String(studentId));
  const qs = q.toString();
  return request<{ reports: ReportSummary[] }>(`/reports${qs ? `?${qs}` : ""}`);
};

export const reportDetail = (reportId: number) =>
  request<{ report_id: number; markdown: string; type: string }>(`/reports/${reportId}`);

// 收件箱与 draft 流（§5.3）
export const inboxList = (status = "draft", classId?: number) => {
  const q = new URLSearchParams({ status });
  if (classId) q.set("class_id", String(classId));
  return request<InboxList>(`/inbox?${q.toString()}`);
};

export const inboxSummary = () => request<InboxSummary>("/inbox/summary");

export const reportFull = (reportId: number) =>
  request<ReportFull>(`/reports/${reportId}/full`);

export const issueReport = (reportId: number) =>
  request<ReportTransition>(`/reports/${reportId}/issue`, {
    method: "POST",
    body: JSON.stringify({ note: null }),
  });

export const rejectReport = (reportId: number, note: string) =>
  request<ReportTransition>(`/reports/${reportId}/reject`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });

// 用量台账（§5.9）
export const adminUsage = (month: string) =>
  request<UsageLedger>(`/admin/usage?month=${encodeURIComponent(month)}`);
