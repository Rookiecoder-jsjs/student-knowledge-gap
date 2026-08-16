import { ChartBar, FirstAidKit } from "@phosphor-icons/react";
import { Link, useParams } from "react-router-dom";
import { Card, EmptyState, ErrorState, Page, PageHeader, Skeleton } from "../components/ui";
import { Reveal } from "../components/motion";
import { listStudents } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { ACCENTS } from "../lib/theme";

/** 学生列表（名单原序，不按任何分数排序）。 */
export default function Students() {
  const { classId } = useParams();
  const cid = Number(classId);
  const { data, loading, error, reload } = useAsync(() => listStudents(cid), [cid]);

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
          {data.students.map((s) => (
            <div key={s.student_id} className="flex items-center gap-3 px-5 py-3.5 transition-colors hover:bg-surface-2/50">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent-deep">
                {s.name_or_alias.slice(0, 1)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{s.name_or_alias}</p>
                {s.external_code && (
                  <p className="text-xs text-ink-faint">{s.external_code}</p>
                )}
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
                  to={`/c/${cid}/students/${s.student_id}/mastery`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-3 py-2 text-sm font-medium text-ink-soft transition-colors hover:border-accent/50 hover:text-accent"
                >
                  <ChartBar size={14} />
                  掌握程度
                </Link>
              </span>
            </div>
          ))}
          </Card>
        </Reveal>
      )}
    </Page>
  );
}
