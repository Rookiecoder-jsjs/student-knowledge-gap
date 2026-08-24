import { useParams, useSearchParams } from "react-router-dom";
import { Card, EmptyState, ErrorState, Page, SectionTitle, Skeleton } from "../components/ui";
import { Reveal } from "../components/motion";
import { ReportMarkdown, ReportTOC } from "../components/Markdown";
import { listExams, qualityReport } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { ACCENTS } from "../lib/theme";

/**
 * 单场考试报告（存档查看器，diagnosis-sheet-redesign §1.4/F4）。
 * 语义从「班级质量分析与行动方向」降级为「单场考试报告」：
 * 行动面板与摘要条移入班级诊断单；本页只保留 考试选择器 + markdown + TOC + 打印。
 * 报告本身在提交时已自动生成（get-or-generate：无则补算），无需手动触发生成。
 */
export default function Quality() {
  const { classId, examId: routeExamId } = useParams();
  const cid = Number(classId);
  const presetExamId = routeExamId ? Number(routeExamId) : null;
  const [params] = useSearchParams();

  const exams = useAsync(() => listExams(cid), [cid]);
  // 工作区入口带 exam 参数时预设本场；直达入口默认最近一场
  const initialExam =
    presetExamId ??
    (params.get("exam") ? Number(params.get("exam")) : null);
  const effectiveExamId = initialExam ?? exams.data?.exams[0]?.exam_id ?? null;

  // get-or-generate：有已存报告直接返回（提交时已生成），无则现算——不提供手动开关
  const report = useAsync(
    () => (effectiveExamId ? qualityReport(cid, effectiveExamId, false) : Promise.resolve(null)),
    [cid, effectiveExamId]
  );

  const selectedExam = exams.data?.exams.find((e) => e.exam_id === effectiveExamId);

  return (
    <Page accent={ACCENTS.exam}>
      <SectionTitle>单场考试报告{selectedExam ? ` · ${selectedExam.name}` : ""}</SectionTitle>

      {!presetExamId && (
        <Card className="mb-5 flex flex-wrap items-end gap-4 p-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-faint">选择考试</span>
            <select
              value={effectiveExamId ?? ""}
              onChange={(e) => {
                window.location.href = `/c/${cid}/quality?exam=${e.target.value}`;
              }}
              className="min-w-[220px] rounded-lg border border-line bg-surface px-3 py-2 text-sm transition-colors focus:border-accent"
            >
              <option value="" disabled>
                {exams.data && exams.data.exams.length > 0 ? "请选择…" : "暂无考试"}
              </option>
              {(exams.data?.exams ?? []).map((e) => (
                <option key={e.exam_id} value={e.exam_id}>
                  {e.name}（{e.exam_date}）
                </option>
              ))}
            </select>
          </label>
          <p className="ml-auto max-w-sm text-xs text-ink-faint">
            报告随考试提交自动生成并存档。班级的最新状态与改进意见请见
            <a href={`/c/${cid}/exams?tab=diagnosis`} className="ml-1 font-medium text-accent underline underline-offset-2">
              班级诊断单
            </a>
            。
          </p>
        </Card>
      )}

      {report.loading && <Skeleton rows={8} />}
      {report.error && <ErrorState message={report.error} onRetry={report.reload} />}

      {report.data === null && !report.loading && !report.error && !presetExamId && (
        <Card>
          <EmptyState title="选择一场考试" hint="选择后显示该场考试的完整质量报告。" />
        </Card>
      )}

      {report.data && !report.loading && (
        <Reveal>
          <div className="grid gap-5 lg:grid-cols-[200px_1fr]">
            <aside className="hidden lg:block">
              <div className="sticky top-4 rounded-lg border border-line bg-surface p-4 shadow-soft">
                <ReportTOC content={report.data.markdown} />
                <button
                  onClick={() => window.print()}
                  className="mt-3 w-full rounded-md border border-line px-2 py-1 text-xs text-ink-soft transition-colors hover:border-accent hover:text-accent"
                >
                  打印 / 导出 PDF
                </button>
              </div>
            </aside>
            <Card className="p-6">
              <ReportMarkdown content={report.data.markdown} />
            </Card>
          </div>
        </Reveal>
      )}
    </Page>
  );
}
