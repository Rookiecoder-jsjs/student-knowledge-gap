import { useState } from "react";
import { Link } from "react-router-dom";
import { diagnosisEvidence } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { Button, ErrorState, Pagination, Skeleton } from "./ui";

export function DiagnosisEvidence({ studentId, kpId, classId, asOf }: {
  studentId: number; kpId: number; classId: number; asOf?: string;
}) {
  const [open, setOpen] = useState(false);
  return <div className="mt-3 border-t border-line pt-2">
    <Button variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
      {open ? "收起诊断依据" : "查看诊断依据"}
    </Button>
    {open && <EvidenceList key={`${studentId}:${kpId}:${asOf}`} studentId={studentId} kpId={kpId} classId={classId} asOf={asOf} />}
  </div>;
}

function EvidenceList({ studentId, kpId, classId, asOf }: {
  studentId: number; kpId: number; classId: number; asOf?: string;
}) {
  const [page, setPage] = useState(1);
  const result = useAsync(() => diagnosisEvidence(studentId, kpId, asOf, (page - 1) * 10), [studentId, kpId, asOf, page]);
  return <div className="mt-2 space-y-2 text-xs">
    <p className="text-ink-faint">只列截至评估日期的已提交作答。掌握度综合得分率、题目权重与时间衰减，并非原始分数平均值。</p>
    {result.loading && <Skeleton rows={3} />}
    {result.error && <ErrorState message={result.error} onRetry={result.reload} />}
    {!result.loading && !result.error && result.data && <>
      {result.data.items.length === 0 && <p className="text-ink-faint">该时点暂无可回溯的作答证据。</p>}
      {result.data.items.map(item => <div key={item.id} className="rounded-lg bg-surface-2 p-3">
        <div className="flex flex-wrap justify-between gap-1">
          <Link className="font-medium text-accent underline" to={`/c/${classId}/exams/${item.exam_id}/report`}>{item.exam_name} · 第 {item.question_idx} 题</Link>
          <b>{item.score} / {item.full_score} 分</b>
        </div>
        <p className="mt-1 text-ink-faint">{item.exam_date} · {item.source_type} · {item.cog_level}</p>
        <p className="mt-1 break-words text-ink-soft">{item.stem || "题干未录入，请查原试卷"}</p>
        <p className="mt-1 text-ink-faint">分析得分率 {Math.round(item.value * 100)}% · 当前有效权重 {item.effective_weight}{item.cascade_flag ? " · 已按级联错误降权" : ""}</p>
      </div>)}
      <Pagination page={page} pageSize={10} total={result.data.total} hasMore={result.data.has_more} onPageChange={setPage} />
    </>}
  </div>;
}
