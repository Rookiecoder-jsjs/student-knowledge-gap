import { FileText } from "@phosphor-icons/react";
import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Button, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "../components/ui";
import { Reveal } from "../components/motion";
import { ReportMarkdown, ReportTOC } from "../components/Markdown";
import { listExams, qualityReport } from "../lib/api";
import { useAsync } from "../lib/hooks";

/**
 * 班级质量分析（考试流水线第 5 阶，亦作 /c/:cid/quality 直达入口）。
 * 路由带 :examId 时为工作区阶段 5（预设本场，无选择器）；否则直达入口显示考试选择器。
 */
export default function Quality() {
  const { classId, examId: routeExamId } = useParams();
  const cid = Number(classId);
  const presetExamId = routeExamId ? Number(routeExamId) : null;
  const [params] = useSearchParams();

  const exams = useAsync(() => listExams(cid), [cid]);
  const [examId, setExamId] = useState<number | null>(
    presetExamId ?? (params.get("exam") ? Number(params.get("exam")) : null)
  );
  const [narrative, setNarrative] = useState(false);
  const [report, setReport] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    if (examId === null) return;
    setLoading(true);
    setError(null);
    try {
      const r = await qualityReport(cid, examId, narrative);
      setReport(r.markdown);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const selectedExam = exams.data?.exams.find((e) => e.exam_id === examId);

  return (
    <div>
      <SectionTitle>{presetExamId ? "班级质量分析" : "班级质量分析（选择考试）"}</SectionTitle>

      <Card className="mb-5 flex flex-wrap items-end gap-4 p-4">
        {presetExamId ? (
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-ink-faint">本场考试</span>
            <span className="min-w-[220px] text-sm font-semibold text-ink">
              {selectedExam?.name ?? "…"}
            </span>
          </div>
        ) : (
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-faint">选择考试</span>
            <select
              value={examId ?? ""}
              onChange={(e) => {
                setExamId(Number(e.target.value));
                setReport(null);
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
        )}
        <Button
          variant={narrative ? "primary" : "secondary"}
          aria-pressed={narrative}
          onClick={() => {
            setNarrative((n) => !n);
            setReport(null);
          }}
        >
          {narrative ? "已附加 AI 解读" : "附加 AI 解读"}
        </Button>
        <Button onClick={generate} disabled={loading || examId === null} className="ml-auto">
          <FileText size={15} />
          {loading
            ? narrative
              ? "解读生成中…"
              : "生成中…"
            : report
              ? "查看报告"
              : "生成报告"}
        </Button>
      </Card>

      {loading && <Skeleton rows={8} />}
      {error && <ErrorState message={error} onRetry={generate} />}
      {!loading && !report && !error && (
        <Card>
          <EmptyState
            title="选择考试并生成报告"
            hint="报告包含班级共性待加强点、各题得分率与讲评建议；开启 AI 解读会追加一段模型生成的文字说明（导出前请预览确认）。"
          />
        </Card>
      )}
      {report && !loading && (
        <Reveal>
          <div className="grid gap-5 lg:grid-cols-[200px_1fr]">
            <aside className="hidden lg:block">
              <div className="sticky top-4 rounded-[10px] border border-line bg-surface p-4 shadow-soft">
                <ReportTOC content={report} />
              </div>
            </aside>
            <Card className="p-6">
              <ReportMarkdown content={report} />
            </Card>
          </div>
        </Reveal>
      )}
    </div>
  );
}
