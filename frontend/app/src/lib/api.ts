/** 后端 API 客户端：30 端点全覆盖，统一错误语义。 */

import type {
  ActionPlanView,
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
  InterventionRow,
  InterventionSummary,
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
  StudentActionPlan,
  StudentInfo,
  UsageLedger,
  Weaknesses,
} from "./types";
import { getToken } from "./auth";

export type { InboxItem, InboxSummary, ReportFull, UsageLedger } from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const BASE = "/api";

// 401 全局处理：由 AuthContext 注册——token 失效 → 清会话回登录页。
let _unauthorized: (() => void) | null = null;
export function setApiUnauthorized(fn: () => void): void {
  _unauthorized = fn;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const opts: RequestInit | undefined = init ? { ...init, headers } : { headers };
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, opts);
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
    if (res.status === 401) _unauthorized?.();
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
  /** 显式目标版本（图形化建库向导写入 draft）；缺省 = active（旧行为） */
  kb_version_id?: number;
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
  /** 显式目标版本（导图建库在 draft 内连线）；缺省 = active（旧行为） */
  kb_version_id?: number;
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

export const forkKbVersion = (sourceVersionId?: number) =>
  request<{ id: number; status: string; forked_from: number }>(
    `/kb/versions${sourceVersionId ? `?source_version_id=${sourceVersionId}` : ""}`,
    { method: "POST" }
  );

/** 图形化建库第一步：创建空白草稿版本（多学科；grade 落版本年级）。内容随后经 createKp 逐条写入。 */
export const createKbVersion = (body: {
  subject: string;
  grade?: number | null;
  textbook_edition: string;
  version: string;
}) =>
  request<{ id: number; subject: string; grade: number | null; version: string; status: string }>(
    "/kb/versions/create",
    json(body)
  );

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

/** 带鉴权的 YAML 导出下载。不能用 window.open 直开导出 URL——裸请求带不上
 * Authorization 头，安全模式下被 G11 全局闸 401（2026-09-11 用户实报的
 * 「点导出跳到 /api/kb/export」即此）。fetch→blob→<a download> 走正常头。 */
export const downloadKbYaml = async (kbVersionId?: number) => {
  const res = await fetch(exportKbUrl(kbVersionId), {
    headers: { Authorization: `Bearer ${getToken() ?? ""}` },
  });
  if (!res.ok) throw new Error(`导出失败（HTTP ${res.status}）`);
  return res.blob();
};

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

export const listStudents = (
  classId: number,
  options?: { offset?: number; limit?: number },
) => {
  const q = new URLSearchParams();
  if (options?.offset != null) q.set("offset", String(options.offset));
  if (options?.limit != null) q.set("limit", String(options.limit));
  const qs = q.toString();
  return request<{
    class_id: number;
    students: StudentInfo[];
    total?: number;
    offset?: number;
    limit?: number;
    has_more?: boolean;
  }>(`/classes/${classId}/students${qs ? `?${qs}` : ""}`);
};

export const listExams = (
  classId?: number,
  options?: { offset?: number; limit?: number },
) => {
  const q = new URLSearchParams();
  if (classId) q.set("class_id", String(classId));
  if (options?.offset != null) q.set("offset", String(options.offset));
  if (options?.limit != null) q.set("limit", String(options.limit));
  const qs = q.toString();
  return request<{
    exams: ExamSummary[];
    total?: number;
    offset?: number;
    limit?: number;
    has_more?: boolean;
  }>(`/exams${qs ? `?${qs}` : ""}`);
};

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
    action_plans?: number;
    interventions?: number;
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
export const inboxList = (
  status = "draft",
  classId?: number,
  options?: { offset?: number; limit?: number },
) => {
  const q = new URLSearchParams({ status });
  if (classId) q.set("class_id", String(classId));
  if (options?.offset != null) q.set("offset", String(options.offset));
  if (options?.limit != null) q.set("limit", String(options.limit));
  return request<InboxList>(`/inbox?${q.toString()}`);
};

export const inboxSummary = () => request<InboxSummary>("/inbox/summary");

export const reportFull = (reportId: number) =>
  request<ReportFull>(`/reports/${reportId}/full`);

export const issueReport = (reportId: number) =>
  request<ReportTransition>(`/reports/${reportId}/issue`, json({ note: null }));

export const rejectReport = (reportId: number, note: string) =>
  request<ReportTransition>(`/reports/${reportId}/reject`, json({ note }));

// 用量台账（§5.9）
export const adminUsage = (month: string) =>
  request<UsageLedger>(`/admin/usage?month=${encodeURIComponent(month)}`);

// ---- 学生自服务（auth-roles-design §6：/me 只读，仅学生会话可达）----

export interface MeProfile {
  student_id: number;
  name_or_alias: string;
  external_code: string;
  class_id: number;
  class_name: string | null;
}

export const meProfile = () => request<{ student: MeProfile }>("/me");

export const meMastery = (asOf?: string) =>
  request<{ student_id: number; as_of: string; mastery: MasteryItem[] }>(
    `/me/mastery${asOf ? `?as_of=${asOf}` : ""}`
  );

export const meWeaknesses = (asOf?: string) =>
  request<Weaknesses>(`/me/weaknesses${asOf ? `?as_of=${asOf}` : ""}`);

export const meReports = () =>
  request<{ reports: (ReportSummary & { type_label?: string })[] }>("/me/reports");

export const meReportFull = (reportId: number) =>
  request<ReportFull>(`/me/reports/${reportId}/full`);

export const meActionPlan = () =>
  request<{ report_id: number | null; markdown: string | null; as_of: string | null }>(
    "/me/action-plan"
  );

// ---- 我的学习（study-loop-design：/me 唯一可写面——方案生成 + 自报）----

export interface StudyRecordListItem {
  id: number;
  kp_code: string;
  kp_name: string;
  generated_at: string;
  self_marked_at: string | null;
  loop_state?: string | null;
}

export interface StudyPlanView {
  id: number;
  kp_code: string;
  kp_name: string;
  plan_markdown: string;
  plan_writer: { model?: string; prompt_version?: string; template?: boolean } | null;
  generated_at: string;
  self_marked_at: string | null;
  loop_state?: string | null;
}

export const meStudyRecords = () =>
  request<{ student_id: number; records: StudyRecordListItem[] }>("/me/study-records");

/** get-or-generate：首次查看生成并缓存；幂等复用不重调 LLM。 */
export const meStudyPlan = (kpCode: string) =>
  request<StudyPlanView>(`/me/study-plan?kp_code=${encodeURIComponent(kpCode)}`);

/** 自报「我学会了」：软闭合——只推进干预状态机，掌握度不动。 */
export const meSelfMark = (recordId: number) =>
  request<{ id: number; self_marked_at: string; row_done: boolean }>(
    `/me/study-records/${recordId}/self-mark`,
    json({})
  );

// ---- 学生门户预览（frontend-ends-design 超级账号：admin 只读镜像，形状与 /me/* 一致）----

export const portalProfile = (studentId: number) =>
  request<{ student: MeProfile }>(`/admin/students/${studentId}/portal`);

export const portalMastery = (studentId: number, asOf?: string) =>
  request<{ student_id: number; as_of: string; mastery: MasteryItem[] }>(
    `/admin/students/${studentId}/portal/mastery${asOf ? `?as_of=${asOf}` : ""}`
  );

export const portalWeaknesses = (studentId: number, asOf?: string) =>
  request<Weaknesses>(
    `/admin/students/${studentId}/portal/weaknesses${asOf ? `?as_of=${asOf}` : ""}`
  );

export const portalReports = (studentId: number) =>
  request<{ reports: (ReportSummary & { type_label?: string })[] }>(
    `/admin/students/${studentId}/portal/reports`
  );

export const portalReportFull = (studentId: number, reportId: number) =>
  request<ReportFull>(`/admin/students/${studentId}/portal/reports/${reportId}/full`);

export const portalActionPlan = (studentId: number) =>
  request<{ report_id: number | null; markdown: string | null; as_of: string | null }>(
    `/admin/students/${studentId}/portal/action-plan`
  );

// 预览镜像（study-loop-design）：只读——无记录 404，绝不触发生成
export const portalStudyRecords = (studentId: number) =>
  request<{ student_id: number; records: StudyRecordListItem[] }>(
    `/admin/students/${studentId}/portal/study-records`
  );

export const portalStudyPlan = (studentId: number, kpCode: string) =>
  request<StudyPlanView>(
    `/admin/students/${studentId}/portal/study-plan?kp_code=${encodeURIComponent(kpCode)}`
  );

// ---- 管理端（frontend-ends-design §C/§D：账号管理面；admin-only）----

export interface TeacherAccountRow {
  teacher_id: number;
  name: string;
  username: string | null;
  admin: boolean;
  kb_editor: boolean;
  classes: { class_id: number; name: string }[];
  /** 学科管理员授权（学科×年级；rbac-scopes-design §7）。 */
  subject_scopes: { subject: string; grade: number }[];
  /** 担任班主任的班级 id 集。 */
  homeroom_class_ids: number[];
}

export const listTeachers = () =>
  request<{ teachers: TeacherAccountRow[] }>("/auth/teachers");

export const createTeacher = (body: {
  name: string;
  username: string;
  password: string;
  school_id: number;
  admin?: boolean;
  kb_editor?: boolean;
}) => request<{ teacher_id: number }>("/auth/teachers", json(body));

export const grantTeacherClasses = (
  teacherId: number,
  class_ids: number[],
  subject?: string | null
) =>
  request<{ teacher_id: number; added: number }>(
    `/auth/teachers/${teacherId}/classes`,
    json({ class_ids, subject: subject ?? null })
  );

/** 学科管理员授权（学科×年级，覆盖式；rbac-scopes-design §7）。 */
export const setSubjectScopes = (
  teacherId: number,
  scopes: { subject: string; grade: number }[]
) =>
  request<{ teacher_id: number; subject_scopes: { subject: string; grade: number }[] }>(
    `/auth/teachers/${teacherId}/subject-scopes`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scopes }),
    }
  );

/** 指派/取消班主任（teacherId=null 取消；rbac-scopes-design §3）。 */
export const setHomeroom = (classId: number, teacherId: number | null) =>
  request<{ class_id: number; homeroom_teacher_id: number | null }>(
    `/auth/classes/${classId}/homeroom`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ teacher_id: teacherId }),
    }
  );

/** 授予/撤销知识库编辑权（两层写权：内容层资格；版本治理仍 admin）。 */
export const setTeacherKbEditor = (teacherId: number, kb_editor: boolean) =>
  request<{ teacher_id: number; kb_editor: boolean }>(
    `/auth/teachers/${teacherId}/kb-editor`,
    json({ kb_editor })
  );

export const enableStudentAccount = (
  studentId: number,
  password: string,
  username?: string
) =>
  request<{ student_id: number; username: string }>(
    `/auth/students/${studentId}/enable`,
    json({ password, username: username ?? null })
  );

// ---------------------------------------------------------------------------
// 干预闭环（intervention-loop-design §5 七端点）
// ---------------------------------------------------------------------------

export const classActionPlan = (classId: number, examId?: number) =>
  request<ActionPlanView>(
    `/classes/${classId}/action-plan${examId ? `?exam_id=${examId}` : ""}`
  );

export const studentActionPlan = (studentId: number) =>
  request<StudentActionPlan>(`/students/${studentId}/action-plan`);

export const listInterventions = (params: {
  class_id?: number;
  student_id?: number;
  status?: string;
  offset?: number;
  limit?: number;
}) => {
  const q = new URLSearchParams();
  if (params.class_id) q.set("class_id", String(params.class_id));
  if (params.student_id) q.set("student_id", String(params.student_id));
  if (params.status) q.set("status", params.status);
  if (params.offset != null) q.set("offset", String(params.offset));
  if (params.limit != null) q.set("limit", String(params.limit));
  const qs = q.toString();
  return request<{
    total: number;
    items: InterventionRow[];
    offset?: number;
    limit?: number;
    has_more?: boolean;
  }>(
    `/interventions${qs ? `?${qs}` : ""}`
  );
};

/** withGroup：小组代表行批量落事实（同 group_ref 各行各自确认，操作层一次）。
 * note：确认注记——班级行「派发AI学习方案」用它落系统语义。 */
export const confirmIntervention = (id: number, withGroup = false, note?: string) =>
  request<{ id: number; status: string; done_at: string; confirmed?: number }>(
    `/interventions/${id}/confirm${withGroup ? "?with_group=true" : ""}`,
    json({ note: note ?? null })
  );

export const skipIntervention = (id: number, withGroup = false, note?: string) =>
  request<{ id: number; status: string; skipped?: number }>(
    `/interventions/${id}/skip${withGroup ? "?with_group=true" : ""}`,
    json({ note: note ?? null })
  );

export const interventionSummaryOf = (classId: number) =>
  request<InterventionSummary>(
    `/interventions/summary?class_id=${classId}`
  );

// 定向复测（progress-loop-design P2）已于 2026-09-11 软退役：retestBlueprint /
// linkRetest 端点与建卷入口移除，验证语义由软闭合+自然考试被动验证接管。
