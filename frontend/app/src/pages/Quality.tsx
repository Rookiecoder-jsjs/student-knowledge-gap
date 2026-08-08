import { FileText } from "@phosphor-icons/react";
import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Button, Card, EmptyState, ErrorState, PageHeader, Skeleton } from "../components/ui";
import { Reveal } from "../components/motion";
import { ReportMarkdown } from "../components/Markdown";
import { listExams, qualityReport } from "../lib/api";
import { useAsync } from "../lib/hooks";

/** 班级质量分析：选考试 -> 生成报告（可开关 AI 解读）。 */
export default function Quality() {
  const { classId } = useParams();
  const cid = Number(classId);
  const [params] = useSearchParams();

  const exams = useAsync(() => listExams(cid), [cid]);
  const [examId, setExamId] = useState<number | null>(
    params.get("exam") ? Number(params.get("exam")) : null
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

  return (
    <div>
      <PageHeader title="班级质量分析" desc="共性待加强点与讲评建议；数字全部来自系统计算，可直接用于讲评课" />

      <Card className="mb-6 flex flex-wrap items-end gap-4 p-5">
        <label className="flex flex-col gap-2">
          <span className="text-sm font-medium">选择考试</span>
          <select
            value={examId ?? ""}
            onChange={(e) => {
              setExamId(Number(e.target.value));
              setReport(null);
            }}
            className="min-w-[220px] rounded-xl border border-line bg-surface px-3 py-2.5 text-sm transition-colors focus:border-accent"
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
          {loading ? "生成中…" : report ? "重新生成" : "生成报告"}
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
          <Card className="p-7">
            <ReportMarkdown content={report} />
          </Card>
        </Reveal>
      )}
    </div>
  );
}
