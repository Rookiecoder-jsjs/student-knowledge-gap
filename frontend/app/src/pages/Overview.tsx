import { ArrowRight, FileArrowUp } from "@phosphor-icons/react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, EmptyState, ErrorState, PageHeader, SectionTitle, Skeleton } from "../components/ui";
import { Reveal } from "../components/motion";
import { TeachingProgressCard } from "../components/TeachingProgress";
import { listClasses, listExams } from "../lib/api";
import { useAsync } from "../lib/hooks";

/** 班级概览：待办（待审核/未提交）+ 最近考试 + 教学进度摘要。 */
export default function Overview() {
  const { classId } = useParams();
  const cid = Number(classId);

  const classes = useAsync(() => listClasses(), []);
  const exams = useAsync(() => listExams(cid), [cid]);

  const clazz = classes.data?.classes.find((c) => c.class_id === cid);
  const todo = (exams.data?.exams ?? []).filter(
    (e) => e.unreviewed_tags > 0 || (e.response_counts["待审核"] ?? 0) > 0
  );

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
            className="inline-flex items-center gap-1.5 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-white shadow-soft transition-all hover:bg-accent-deep hover:shadow-lift active:scale-[0.98]"
          >
            <FileArrowUp size={15} />
            录入新考试
          </Link>
        }
      />

      {classes.error && <ErrorState message={classes.error} onRetry={classes.reload} />}

      <Reveal className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        <section>
          <SectionTitle count={exams.data?.exams.length ?? 0}>考试</SectionTitle>
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
              {exams.data.exams.map((e) => (
                <div key={e.exam_id} className="flex items-center gap-3 px-5 py-3.5 transition-colors hover:bg-surface-2/50">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">{e.name}</p>
                    <p className="mt-0.5 text-xs text-ink-faint">
                      {e.exam_date} · {e.type} · {e.question_count} 题 ·{" "}
                      {e.response_counts["已提交"] ?? 0} 人已提交
                    </p>
                  </div>
                  {e.unreviewed_tags > 0 && (
                    <Link to={`/c/${cid}/exams/${e.exam_id}/review`}>
                      <Badge tone="warn">{e.unreviewed_tags} 条标注待审核</Badge>
                    </Link>
                  )}
                  {(e.response_counts["待审核"] ?? 0) > 0 && (
                    <Link to={`/c/${cid}/exams/${e.exam_id}/collect`}>
                      <Badge tone="accent">{e.response_counts["待审核"]} 卷待提交</Badge>
                    </Link>
                  )}
                  <Link
                    to={`/c/${cid}/exams/${e.exam_id}/collect`}
                    className="text-ink-faint transition-colors hover:text-accent"
                    aria-label={`进入${e.name}`}
                  >
                    <ArrowRight size={16} />
                  </Link>
                </div>
              ))}
            </Card>
          )}
        </section>

        <section className="space-y-6">
          <div>
            <SectionTitle count={todo.length}>待办</SectionTitle>
            <Card className="p-5">
              {exams.loading ? (
                <Skeleton rows={2} />
              ) : todo.length === 0 ? (
                <p className="text-sm text-ink-faint">没有待处理事项。</p>
              ) : (
                <ul className="space-y-3">
                  {todo.map((e) => (
                    <li key={e.exam_id} className="flex items-center gap-2 text-sm">
                      <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-warn" aria-hidden />
                      <Link
                        to={`/c/${cid}/exams/${e.exam_id}/review`}
                        className="truncate transition-colors hover:text-accent"
                      >
                        {e.name}：审核标注 / 核对把握低的得分
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
