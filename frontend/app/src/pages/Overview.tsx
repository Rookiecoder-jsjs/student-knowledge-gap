import { ArrowRight, FileArrowUp } from "@phosphor-icons/react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, EmptyState, ErrorState, PageHeader, SectionTitle, Skeleton, StatTile } from "../components/ui";
import { Reveal } from "../components/motion";
import { TeachingProgressCard } from "../components/TeachingProgress";
import { listClasses, listClassesOverview, listExams } from "../lib/api";
import { useAsync } from "../lib/hooks";
import type { ExamSummary } from "../lib/types";

/** 由考试摘要推算下一动作（与 Exams 页一致）。 */
function nextAction(e: ExamSummary): { label: string; to: string; tone: "warn" | "accent" } {
  const committed = (e.response_counts["已提交"] ?? 0) > 0;
  if (e.unreviewed_tags > 0) return { label: "去审核", to: "review", tone: "warn" };
  if (!committed) return { label: "去采集", to: "collect", tone: "warn" };
  return { label: "查看报告", to: "report", tone: "accent" };
}

/** 工作台：统计条 + 待办 + 最近考试（流水线下一动作）+ 教学进度。 */
export default function Overview() {
  const { classId } = useParams();
  const cid = Number(classId);

  const classes = useAsync(() => listClasses(), []);
  const overview = useAsync(() => listClassesOverview(), []);
  const exams = useAsync(() => listExams(cid), [cid]);

  const clazz = classes.data?.classes.find((c) => c.class_id === cid);
  const ov = overview.data?.classes.find((c) => c.class_id === cid);

  const todo = (exams.data?.exams ?? []).filter(
    (e) => e.unreviewed_tags > 0 || (e.response_counts["待审核"] ?? 0) > 0
  );
  const pct = ov && ov.progress.total > 0 ? Math.round((ov.progress.taught / ov.progress.total) * 100) : 0;

  return (
    <div>
      <PageHeader
        title={clazz?.name ?? "班级概览"}
        desc={
          clazz
            ? `${clazz.grade} 年级 · ${clazz.subject} · ${clazz.student_count} 名学生`
            : undefined
        }
        actions={
          <Link
            to={`/c/${cid}/exams/new`}
            className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-white shadow-soft transition-all hover:bg-accent-deep hover:shadow-lift active:scale-[0.98]"
          >
            <FileArrowUp size={15} />
            录入新考试
          </Link>
        }
      />

      {/* 统计条 */}
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile value={ov?.exam_count ?? 0} label="考试" />
        <StatTile value={ov?.todo_count ?? 0} label="待办" tone={(ov?.todo_count ?? 0) > 0 ? "warn" : "neutral"} />
        <StatTile value={`${pct}%`} label="教学进度" hint={ov ? `${ov.progress.taught}/${ov.progress.total}` : undefined} tone="accent" />
        <StatTile value={clazz?.student_count ?? 0} label="学生" />
      </div>

      {classes.error && <ErrorState message={classes.error} onRetry={classes.reload} />}

      <Reveal className="grid gap-5 lg:grid-cols-[1.6fr_1fr]">
        <section>
          <SectionTitle
            count={exams.data?.exams.length ?? 0}
            right={
              <Link to={`/c/${cid}/exams`} className="text-xs font-medium text-accent hover:text-accent-deep">
                全部 →
              </Link>
            }
          >
            最近考试
          </SectionTitle>
          {exams.loading && <Skeleton rows={3} />}
          {exams.error && <ErrorState message={exams.error} onRetry={exams.reload} />}
          {exams.data && exams.data.exams.length === 0 && (
            <Card>
              <EmptyState
                title="还没有考试"
                hint="录入第一场考试（拍照或 Excel），提交后即可生成班级质量分析。冷启动建议先补录 2~3 次历史考试。"
              />
            </Card>
          )}
          {exams.data && exams.data.exams.length > 0 && (
            <Card className="divide-y divide-line">
              {exams.data.exams.slice(0, 6).map((e) => {
                const next = nextAction(e);
                return (
                  <Link
                    key={e.exam_id}
                    to={`/c/${cid}/exams/${e.exam_id}/${next.to}`}
                    className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-surface-2/50"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold">{e.name}</p>
                      <p className="mt-0.5 text-xs text-ink-faint tabular-nums">
                        {e.exam_date} · {e.type} · {e.question_count} 题 ·{" "}
                        {e.response_counts["已提交"] ?? 0} 人已提交
                      </p>
                    </div>
                    <Badge tone={next.tone}>{next.label}</Badge>
                    <ArrowRight size={15} className="text-ink-faint" />
                  </Link>
                );
              })}
            </Card>
          )}
        </section>

        <section className="space-y-5">
          <div>
            <SectionTitle count={todo.length}>待办</SectionTitle>
            <Card className="p-4">
              {exams.loading ? (
                <Skeleton rows={2} />
              ) : todo.length === 0 ? (
                <p className="text-sm text-ink-faint">没有待处理事项。</p>
              ) : (
                <ul className="space-y-2.5">
                  {todo.map((e) => (
                    <li key={e.exam_id} className="flex items-center gap-2 text-sm">
                      <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-warn" aria-hidden />
                      <Link
                        to={`/c/${cid}/exams/${e.exam_id}/${e.unreviewed_tags > 0 ? "review" : "collect"}`}
                        className="truncate transition-colors hover:text-accent"
                      >
                        {e.name}：{e.unreviewed_tags > 0 ? "审核标注" : "核对把握低的得分"}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
          <div>
            <SectionTitle>教学进度</SectionTitle>
            <TeachingProgressCard classId={cid} />
          </div>
        </section>
      </Reveal>
    </div>
  );
}
