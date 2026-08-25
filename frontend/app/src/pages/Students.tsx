import { ChartBar, FirstAidKit, ListChecks } from "@phosphor-icons/react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, EmptyState, ErrorState, Page, PageHeader, Skeleton } from "../components/ui";
import { Reveal } from "../components/motion";
import { listInterventions, listStudents } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { ACCENTS } from "../lib/theme";

/** 学生列表（名单原序，不按任何分数排序）。 */
export default function Students() {
  const { classId } = useParams();
  const cid = Number(classId);
  const { data, loading, error, reload } = useAsync(() => listStudents(cid), [cid]);
  // 干预摘要（intervention-loop §6）：每行 chip「N 项建议 · M 已执行」——聚合一次
  const iv = useAsync(
    () => listInterventions({ class_id: cid }).catch(() => ({ total: 0, items: [] })),
    [cid]
  );
  const byStudent = new Map<number, { suggested: number; done: number }>();
  for (const row of iv.data?.items ?? []) {
    if (row.student_id == null) continue;
    const slot = byStudent.get(row.student_id) ?? { suggested: 0, done: 0 };
    if (row.status === "suggested") slot.suggested += 1;
    if (row.status === "done") slot.done += 1;
    byStudent.set(row.student_id, slot);
  }

  return (
    <Page accent={ACCENTS.student}>
      <PageHeader
        title="学生诊断"
        desc="按名单原序展示；诊断单先看进步，再看待加强项"
      />

      {loading && <Skeleton rows={5} />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && data.students.length === 0 && (
        <Card>
          <EmptyState title="暂无学生" hint="请在初始化向导中添加班级名单。" />
        </Card>
      )}

      {data && data.students.length > 0 && (
        <Reveal>
          <Card className="divide-y divide-line">
          {data.students.map((s) => {
            const stat = byStudent.get(s.student_id);
            return (
            <div key={s.student_id} className="flex items-center gap-3 px-5 py-3.5 transition-colors hover:bg-surface-2/50">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent-deep">
                {s.name_or_alias.slice(0, 1)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{s.name_or_alias}</p>
                <p className="flex items-center gap-2 text-xs text-ink-faint">
                  {s.external_code}
                  {stat && (stat.suggested > 0 || stat.done > 0) && (
                    <Badge tone={stat.suggested > 0 ? "warn" : "neutral"}>
                      {stat.suggested > 0
                        ? `${stat.suggested} 条行动待确认`
                        : `${stat.done} 项干预已执行`}
                    </Badge>
                  )}
                </p>
              </div>
              <span className="flex gap-2">
                <Link
                  to={`/c/${cid}/students/${s.student_id}/diagnosis`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-3 py-2 text-sm font-medium text-ink-soft transition-colors hover:border-accent/50 hover:text-accent"
                >
                  <FirstAidKit size={14} />
                  诊断单
                </Link>
                <Link
                  to={`/c/${cid}/students/${s.student_id}/diagnosis?view=plan`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-3 py-2 text-sm font-medium text-ink-soft transition-colors hover:border-accent/50 hover:text-accent"
                >
                  <ListChecks size={14} />
                  改进单
                </Link>
                <Link
                  to={`/c/${cid}/students/${s.student_id}/mastery`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-3 py-2 text-sm font-medium text-ink-soft transition-colors hover:border-accent/50 hover:text-accent"
                >
                  <ChartBar size={14} />
                  掌握程度
                </Link>
              </span>
            </div>
            );
          })}
          </Card>
        </Reveal>
      )}
    </Page>
  );
}
