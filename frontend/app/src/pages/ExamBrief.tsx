import { ArrowRight, FileText } from "@phosphor-icons/react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Page, SectionTitle, Skeleton } from "../components/ui";
import { qualityReport } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { ACCENTS } from "../lib/theme";

/**
 * 考试概况（diagnosis-sheet-redesign §1.1/F1）：第 5 阶一屏轻页。
 * 只回答「这场考试考得怎么样」——提交率/均分/共性 top3/逐题得分率；
 * 行动与诊断内容一律不进此页（出口指向班级诊断单与本场完整报告存档）。
 */
export default function ExamBrief() {
  const { classId, examId: routeExamId } = useParams();
  const cid = Number(classId);
  const eid = Number(routeExamId);

  // 概况数据源 = 质量报告 snapshot（B2 已补字段），get-or-generate 语义不变
  const report = useAsync(() => qualityReport(cid, eid, false), [cid, eid]);
  const snap = report.data?.snapshot ?? null;
  // 平均得分率：满分加权（逐题 rate 按分值加权平均）；无 rates 时退化为 —
  const meanRate = snap && snap.question_rates.length > 0
    ? (() => {
        let num = 0, den = 0;
        for (const q of snap.question_rates) {
          if (q.rate != null) {
            num += q.rate * q.full_score;
            den += q.full_score;
          }
        }
        return den > 0 ? num / den : null;
      })()
    : null;

  return (
    <Page accent={ACCENTS.exam}>
      <SectionTitle>本场概况</SectionTitle>

      {report.loading && <Skeleton rows={6} />}
      {report.error && <ErrorState message={report.error} onRetry={report.reload} />}

      {report.data && !snap && (
        <Card>
          <EmptyState
            title="暂无概况数据"
            hint="报告生成后此处显示提交率、平均得分率与共性待加强点。"
          />
        </Card>
      )}

      {snap && (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Card className="p-5">
              <p className="text-xs text-ink-faint">提交 / 全班</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">
                {snap.committed}
                <span className="text-sm font-normal text-ink-faint"> / {snap.committed + snap.pending}</span>
              </p>
            </Card>
            <Card className="p-5">
              <p className="text-xs text-ink-faint">平均得分率</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">
                {meanRate != null ? `${Math.round(meanRate * 100)}%` : "—"}
              </p>
              <p className="mt-0.5 text-xs text-ink-faint tabular-nums">
                均分 {snap.stats.mean ?? "—"} · 最高 {snap.stats.max ?? "—"} · 最低 {snap.stats.min ?? "—"}
              </p>
            </Card>
            <Card className="p-5">
              <p className="text-xs text-ink-faint">共性待加强 Top 3</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {snap.common_weak.length === 0 && (
                  <span className="text-sm text-ink-soft">暂无达到共性阈值的点</span>
                )}
                {snap.common_weak.slice(0, 3).map((d) => (
                  <Badge key={d.code} tone="warn">
                    {d.name} · {Math.round(d.weak_share * 100)}%
                  </Badge>
                ))}
              </div>
            </Card>
          </div>

          {/* 各题得分率条 */}
          <Card className="mt-4 p-5">
            <p className="text-sm font-semibold">各题得分率</p>
            <div className="mt-3 space-y-2">
              {snap.question_rates.map((q) => (
                <div key={q.idx} className="flex items-center gap-3 text-xs tabular-nums">
                  <span className="w-12 shrink-0 text-ink-soft">第 {q.idx} 题</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-2">
                    <div
                      className={`h-full rounded-full ${q.low ? "bg-warn" : "bg-accent"}`}
                      style={{ width: `${Math.round((q.rate ?? 0) * 100)}%` }}
                    />
                  </div>
                  <span className={`w-10 text-right ${q.low ? "font-semibold text-warn" : "text-ink-soft"}`}>
                    {q.rate != null ? `${Math.round(q.rate * 100)}%` : "—"}
                  </span>
                  <span className="hidden w-40 truncate text-ink-faint sm:block">{q.kps}</span>
                </div>
              ))}
              {snap.question_rates.length === 0 && (
                <p className="text-xs text-ink-faint">暂无题目数据。</p>
              )}
            </div>
          </Card>

          {/* 两个出口：班级诊断单 / 本场完整报告存档 */}
          <div className="mt-4 flex flex-wrap gap-3">
            <Link to={`/c/${cid}/exams?tab=diagnosis`}>
              <Button>
                查看班级诊断单
                <ArrowRight size={15} />
              </Button>
            </Link>
            <Link to={`/c/${cid}/quality?exam=${eid}`}>
              <Button variant="secondary">
                <FileText size={15} />
                本场完整报告
              </Button>
            </Link>
          </div>
        </>
      )}
    </Page>
  );
}
