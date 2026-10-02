import { createPortal } from "react-dom";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { createDiagnosticExam, diagnosticBlueprint, studentEvidenceGaps } from "../lib/api";
import type { DiagnosticBlueprint } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { Badge, Button, Card, ErrorState, Input, Modal, Pagination, Skeleton } from "./ui";

export function EvidenceGapTasks({ studentId, classId }: { studentId: number; classId: number }) {
  const [page, setPage] = useState(1);
  const gaps = useAsync(() => studentEvidenceGaps(studentId), [studentId]);
  return <section className="space-y-3" aria-label="最小补证据任务">
    <h2 className="text-sm font-semibold">最小补证据任务</h2>
    <p className="text-xs text-ink-faint">只检查已学但证据不足的点，先补少量独立作答，再决定是否需要补学。按今天的数据评估。</p>
    {gaps.loading && <Skeleton rows={2} />}
    {gaps.error && <ErrorState message={gaps.error} onRetry={gaps.reload} />}
    {!gaps.loading && !gaps.error && gaps.data && <>
      {gaps.data.items.length === 0 && <Card className="p-4 text-sm text-ink-faint">当前没有需要补证据的知识点。</Card>}
      {gaps.data.items.slice((page-1)*5, page*5).map(g => <Card key={g.kp_id} className="space-y-2 p-4">
        <p className="font-semibold">{g.kp_name} <Badge>已有 {g.evidence_count} 题证据</Badge></p>
        <p className="text-xs text-ink-soft">建议先检查 {g.questions_needed} 题；题源不足时由教师补充。</p>
        <DiagnosticTaskButton key={`${studentId}:${g.kp_id}`} studentId={studentId} classId={classId} kpId={g.kp_id} mode="evidence" />
      </Card>)}
      <Pagination page={page} pageSize={5} total={gaps.data.items.length} hasMore={page*5<gaps.data.items.length} onPageChange={setPage} />
    </>}
  </section>;
}

export function DiagnosticTaskButton({ studentId, classId, kpId, mode, attributionId }: {
  studentId: number; classId: number; kpId: number; mode: DiagnosticBlueprint["mode"]; attributionId?: number;
}) {
  const [open, setOpen] = useState(false);
  return <><Button variant="secondary" onClick={() => setOpen(true)}>{mode === "evidence" ? "预览补证据任务" : "预览归因验证题组"}</Button>
    {open && createPortal(<BlueprintModal studentId={studentId} classId={classId} kpId={kpId} mode={mode} attributionId={attributionId} onClose={() => setOpen(false)} />, document.body)}</>;
}

function BlueprintModal({ studentId, classId, kpId, mode, attributionId, onClose }: {
  studentId: number; classId: number; kpId: number; mode: DiagnosticBlueprint["mode"]; attributionId?: number; onClose: () => void;
}) {
  const preview = useAsync(() => diagnosticBlueprint(studentId, kpId, mode, attributionId), [studentId, kpId, mode, attributionId]);
  const navigate = useNavigate();
  const now = new Date();
  const [due, setDue] = useState(`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const create = async () => {
    if (!preview.data) return;
    setBusy(true); setError(null);
    try { const result = await createDiagnosticExam(preview.data, due); navigate(`/c/${result.class_id}/exams/${result.exam_id}/review`); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <Modal open onClose={busy ? () => {} : onClose} title={mode === "evidence" ? "最小补证据任务" : "归因验证题组"}>
    {preview.loading && <Skeleton rows={3} />}
    {preview.error && <ErrorState message={preview.error} onRetry={preview.reload} />}
    {!preview.loading && !preview.error && preview.data && <div className="space-y-3">
      <p className="font-semibold">{preview.data.kp_name} · {preview.data.slots.length} 题</p>
      <p className="text-sm text-ink-soft">{preview.data.guidance}</p>
      {preview.data.slots.map((slot, index) => <div key={index} className="rounded-lg border border-line p-3 text-sm">
        <b>{index+1}. {slot.purpose} · {slot.kp_name}</b>
        {slot.question ? <><p className="my-2 whitespace-pre-wrap break-words">{slot.question.stem}</p>
          <p className="text-xs text-ink-faint">{slot.question.cog_level} · 满分 {slot.question.full_score} 分 ·
            <Link className="text-accent underline" target="_blank" to={`/c/${classId}/exams/${slot.question.exam_id}`}>{slot.question.exam_name} 第 {slot.question.question_idx} 题</Link></p></> :
          <p className="mt-2 text-warn">缺少未作答且已审核的适用题目，请先补充题源并完成标注审核。</p>}
      </div>)}
      <p className="text-xs text-ink-faint">{preview.data.limitation}</p>
      <label className="flex flex-wrap items-center gap-2 text-sm">诊断日期 <Input type="date" aria-label="诊断题组日期" value={due} onChange={e => setDue(e.target.value)} /></label>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} />我已核对原卷题面、答案与适用性</label>
      {error && <p role="alert" className="text-xs text-danger">{error} <button className="underline" onClick={() => {setChecked(false); preview.reload();}}>重新预览</button></p>}
      <Button onClick={create} disabled={busy || !checked || !preview.data.ready || !due}>{busy ? "建卷中…" : "确认建卷，继续审核"}</Button>
      <p className="text-xs text-ink-faint">建卷不会给学生自动派发或计入成绩；审核题目后按现有采集流程录入该学生作答。</p>
    </div>}
  </Modal>;
}
