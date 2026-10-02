import { ArrowRight } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { teacherTodayTasks } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { Badge, Card, ErrorState, Pagination, Skeleton } from "./ui";

const LABELS = { scores: "作答审核", tags: "标注审核", report: "报告签发", action: "教学行动", retest: "复测" };

/** 全班任务由统一队列排序，最近考试列表只用于回看进展。 */
export function TodayActions({ classId }: { classId: number }) {
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [classId]);
  const tasks = useAsync(() => teacherTodayTasks(classId, (page - 1) * 10), [classId, page]);
  return <section aria-labelledby="workbench-tasks-title">
    <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h2 id="workbench-tasks-title" className="text-lg font-bold text-ink">现在要处理</h2>
        <p className="mt-1 text-xs text-ink-faint">覆盖本班当前学科全部考试 · 到期复测优先，其次审核、签发与教学行动；未来复测列在最后。</p>
      </div>
      {tasks.data && <span className="text-xs tabular-nums text-ink-faint">共 {tasks.data.total} 项</span>}
    </div>
    {tasks.loading && <Skeleton rows={3} />}
    {tasks.error && <ErrorState message={tasks.error} onRetry={tasks.reload} />}
    {!tasks.loading && !tasks.error && tasks.data && <>
      <div className="mb-3 flex flex-wrap gap-2">{Object.entries(tasks.data.counts).map(([kind, count]) =>
        <Badge key={kind}>{LABELS[kind as keyof typeof LABELS]} {count}</Badge>)}</div>
      <Card className="divide-y divide-line">
        {tasks.data.items.length === 0 ? <p className="px-5 py-6 text-sm text-ink-soft">当前没有待处理事项。</p> :
          tasks.data.items.map((item, index) => <div key={item.id} className="flex flex-wrap items-center gap-4 px-5 py-4 sm:flex-nowrap">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center bg-accent-soft text-xs font-bold tabular-nums text-accent-deep">{(page - 1) * 10 + index + 1}</span>
            <div className="min-w-0 flex-1">
              <div className="mb-2 flex flex-wrap gap-2"><Badge tone={item.priority === 0 ? "danger" : "warn"}>{LABELS[item.kind]}</Badge>
                {item.due_date && <span className="text-xs text-ink-faint">{item.due_date}</span>}</div>
              <p className="break-words text-sm font-semibold text-ink">{item.title}</p>
              <p className="mt-1 text-xs text-ink-soft">{item.reason}</p>
            </div>
            <Link to={item.to} className="inline-flex items-center gap-1 text-sm font-semibold text-accent-deep underline underline-offset-4 hover:text-accent">
              去处理<ArrowRight size={15} aria-hidden />
            </Link>
          </div>)}
      </Card>
      <Pagination page={page} pageSize={10} total={tasks.data.total} hasMore={tasks.data.has_more} onPageChange={setPage} />
    </>}
  </section>;
}
