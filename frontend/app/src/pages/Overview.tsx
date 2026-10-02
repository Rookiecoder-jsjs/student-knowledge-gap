import { ArrowRight, FileArrowUp } from "@phosphor-icons/react";
import { Link, useParams } from "react-router-dom";
import { TodayActions } from "../components/TodayActions";
import { Card, EmptyState, ErrorState, Page, PageHeader, Skeleton } from "../components/ui";
import { classDiagnosisSheet, interventionSummaryOf, listClasses, listExams } from "../lib/api";
import { useAuth } from "../lib/AuthContext";
import { useAsync } from "../lib/hooks";
import { roleFlags } from "../lib/portal";
import { ACCENTS } from "../lib/theme";

/** 工作台只回答当前状态、待办和最近进展；完整分析在班级诊断单。 */
export default function Overview() {
  const { classId } = useParams();
  const cid = Number(classId);
  const base = `/c/${cid}`;
  const { session } = useAuth();
  const assistantVisible = roleFlags(session).assistantVisible;
  const classes = useAsync(() => listClasses(), []);
  const exams = useAsync(() => listExams(cid, { limit: 6 }), [cid]);
  const diagnosis = useAsync(() => classDiagnosisSheet(cid), [cid]);
  const effects = useAsync(() => interventionSummaryOf(cid), [cid]);
  const clazz = classes.data?.classes.find((item) => item.class_id === cid);
  const status = diagnosis.data?.status;
  const firstWeak = status?.common_weak[0];
  const latestExams = exams.data?.exams.slice(0, 3) ?? [];

  return (
    <Page accent={ACCENTS.dashboard}>
      <PageHeader
        title={clazz?.name ?? "班级工作台"}
        desc={clazz ? `${clazz.grade} 年级 · ${clazz.subject} · ${clazz.student_count} 名学生` : undefined}
        actions={
          <Link to={`${base}/exams/new`} className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-white shadow-soft transition-[background-color,box-shadow,transform] hover:bg-accent-deep hover:shadow-lift active:scale-[0.98]">
            <FileArrowUp size={15} aria-hidden />录入新考试
          </Link>
        }
      />

      <section className="mb-7" aria-labelledby="class-status-title">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 id="class-status-title" className="text-lg font-bold text-ink">班级当前状态</h2>
          {status?.data_as_of && <span className="text-xs text-ink-faint">数据截至 {status.data_as_of}</span>}
        </div>
        {diagnosis.loading && <Skeleton rows={2} />}
        {diagnosis.error && <ErrorState message={diagnosis.error} onRetry={diagnosis.reload} />}
        {status && (
          <Card className="flex flex-wrap items-center justify-between gap-5 px-5 py-5 sm:px-6">
            <div className="min-w-0 flex-1">
              <p className="text-base font-semibold leading-relaxed text-ink">
                {status.exam_count === 0
                  ? "还没有可用于班级诊断的考试数据。"
                  : status.weak_kp_total === 0
                    ? "当前诊断没有识别到需要重点关注的共性薄弱点。"
                    : `当前识别到 ${status.weak_kp_total} 个薄弱点${firstWeak ? `，可先查看${firstWeak.kp}` : "，可进入诊断单查看"}。`}
              </p>
              <p className="mt-2 text-xs text-ink-soft">
                {status.exam_count > 0
                  ? `基于已纳入诊断的 ${status.exam_count} 场考试 · 具体判断和依据由教师在诊断单中核对`
                  : "录入并提交考试后，可在这里查看班级诊断。"}
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2">
              <Link to={`${base}/exams?tab=diagnosis`} className="inline-flex items-center gap-1 text-sm font-semibold text-accent-deep underline underline-offset-4 hover:text-accent">
                查看班级诊断<ArrowRight size={15} aria-hidden />
              </Link>
              {assistantVisible && <Link to="/assistant" className="inline-flex items-center gap-1 text-sm font-semibold text-accent-deep underline underline-offset-4 hover:text-accent">追问 AI 教研员<ArrowRight size={15} aria-hidden /></Link>}
            </div>
          </Card>
        )}
      </section>

      <div className="mb-7">
        <TodayActions classId={cid} />
      </div>

      <section aria-labelledby="recent-title">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 id="recent-title" className="text-lg font-bold text-ink">最近进展</h2>
          <Link to={`${base}/exams`} className="text-xs font-medium text-accent-deep hover:text-accent">全部考试 →</Link>
        </div>
        <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
          <Card className="divide-y divide-line">
            {exams.loading && <div className="p-5"><Skeleton rows={2} /></div>}
            {exams.error && <div className="p-5"><ErrorState message={exams.error} onRetry={exams.reload} /></div>}
            {exams.data && latestExams.length === 0 && <EmptyState title="还没有考试" hint="录入第一场考试后，可在这里回看考试进展。" />}
            {latestExams.map((exam) => (
              <Link key={exam.exam_id} to={`${base}/exams/${exam.exam_id}`} className="flex items-center gap-3 px-5 py-4 transition-colors hover:bg-surface-2/50">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-ink">{exam.name}</p>
                  <p className="mt-1 text-xs text-ink-faint">{exam.exam_date} · 已提交 {exam.response_counts["已提交"] ?? 0} 份作答</p>
                </div>
                <ArrowRight size={15} className="shrink-0 text-ink-faint" aria-hidden />
              </Link>
            ))}
          </Card>
          <Card className="p-5">
            <p className="text-sm font-semibold text-ink">教学行动复测</p>
            {effects.loading && <div className="mt-4"><Skeleton rows={2} /></div>}
            {effects.error && <div className="mt-3"><ErrorState message={effects.error} onRetry={effects.reload} /></div>}
            {effects.data && (
              <>
                {effects.data.evaluable_count > 0
                  ? <p className="mt-4 text-2xl font-bold tabular-nums text-ink">{effects.data.effects.improved}<span className="ml-2 text-sm font-normal text-ink-soft">/ {effects.data.evaluable_count} 项可评估行动显示改善</span></p>
                  : <p className="mt-4 text-sm text-ink-soft">目前还没有可评估的复测结果。</p>}
                <p className="mt-2 text-xs text-ink-faint">另有 {effects.data.effects.awaiting_retest} 项等待后续考试检验</p>
                <Link to={`${base}/exams?tab=diagnosis`} className="mt-5 inline-flex items-center gap-1 text-sm font-semibold text-accent-deep underline underline-offset-4 hover:text-accent">查看行动记录<ArrowRight size={15} aria-hidden /></Link>
              </>
            )}
          </Card>
        </div>
      </section>
      {classes.error && <div className="mt-5"><ErrorState message={classes.error} onRetry={classes.reload} /></div>}
    </Page>
  );
}
