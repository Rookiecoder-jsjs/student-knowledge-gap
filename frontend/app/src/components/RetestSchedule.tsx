import { useState } from "react";
import { rescheduleRetest, retestSchedule } from "../lib/api";
import { useAsync } from "../lib/hooks";
import type { InterventionRow } from "../lib/types";
import { Badge, Button, Card, ErrorState, Input, Pagination, Skeleton } from "./ui";

const STATUS = { scheduled: "待复测", overdue: "已到期，待复测", verified: "已有复测证据" };

export function RetestSchedule({ classId, revision }: { classId: number; revision?: unknown }) {
  const [page, setPage] = useState(1);
  const schedule = useAsync(() => retestSchedule(classId, (page - 1) * 20), [classId, revision, page]);
  return <div id="retest-schedule"><Card className="p-4">
    <p className="text-sm font-semibold">复测安排</p>
    <p className="mt-1 text-xs text-ink-faint">确认干预后默认安排 7 天后复测。可使用后续考试或练习验证；获得证据不代表已经改善。全班进度按当前班级名册统计。</p>
    {schedule.loading && <Skeleton rows={2} />}
    {schedule.error && <ErrorState message={schedule.error} onRetry={schedule.reload} />}
    {!schedule.loading && !schedule.error && schedule.data && <div className="mt-3 space-y-2">
      {schedule.data.items.length === 0 && <p className="text-xs text-ink-faint">暂无复测计划，确认执行一条行动建议后会自动排期。</p>}
      {schedule.data.items.map(row => <ScheduleRow key={`${row.id}:${row.retest_due_date}`} row={row} onChanged={schedule.reload} />)}
      <Pagination page={page} pageSize={20} total={schedule.data.total} hasMore={schedule.data.has_more} onPageChange={setPage} />
    </div>}
  </Card></div>;
}

function ScheduleRow({ row, onChanged }: { row: InterventionRow; onChanged: () => void }) {
  const [due, setDue] = useState(row.retest_due_date || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true); setError(null);
    try { await rescheduleRetest(row.id, due, row.scope === "group"); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <div className="rounded-lg border border-line p-3 text-xs">
    <div className="flex flex-wrap items-center gap-2">
      <b>{row.kp_name}</b>
      <span className="text-ink-faint">{row.scope === "class" ? "全班" : row.alias ?? "学生"}</span>
      <Badge tone={row.retest_status === "overdue" ? "warn" : row.retest_status === "verified" ? "accent" : "neutral"}>{row.retest_status ? STATUS[row.retest_status] : "待安排"}</Badge>
      <span className="text-ink-faint">已有证据 {row.retested_students ?? 0} / {row.target_students ?? 0} 人</span>
    </div>
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-2">复测日期 <Input type="date" aria-label={`${row.kp_name}的复测日期`} value={due} onChange={e => setDue(e.target.value)} disabled={busy || row.retest_status === "verified"} /></label>
      {row.retest_status !== "verified" && <Button variant="secondary" onClick={save} disabled={busy || !due || due === row.retest_due_date}>{busy ? "保存中…" : row.scope === "group" ? "调整小组日期" : "调整日期"}</Button>}
    </div>
    {error && <p className="mt-2 text-danger" role="alert">{error}</p>}
  </div>;
}
