import { Link } from "react-router-dom";
import type { ActionPlanView, ExamSummary, InterventionSummary } from "../lib/types";
import { Badge, Card, SectionTitle } from "./ui";

export function TodayActions({ classId, exams, plan, summary }: {
  classId: number; exams: ExamSummary[]; plan: ActionPlanView; summary: InterventionSummary | null;
}) {
  const base = `/c/${classId}`;
  const items = exams.filter((e) => e.unreviewed_tags > 0 || (e.response_counts["待审核"] ?? 0) > 0)
    .map((e) => ({ id: `exam-${e.exam_id}`, title: `复核 ${e.name}`,
      reason: e.unreviewed_tags > 0 ? `${e.unreviewed_tags} 个知识点标注待审核` : `${e.response_counts["待审核"]} 份作答待审核`,
      to: `${base}/exams/${e.exam_id}/${e.unreviewed_tags > 0 ? "review" : "collect"}`, action: "去复核" }));
  for (const row of plan.rows) items.push({ id: `action-${row.id}`, title: `${row.kp_name}：${row.kind}`,
    reason: `${row.scope === "class" ? "全班" : row.scope === "group" ? `${row.group_size ?? 0} 人小组` : row.alias ?? "个别学生"} · ${row.note || "待教师确认的教学建议"}`,
    to: `${base}/exams?tab=diagnosis`, action: "查看依据与建议" });
  return <section className="mb-6" aria-label="优先处理">
    <SectionTitle>优先处理</SectionTitle>
    <p className="mb-3 text-xs text-ink-faint">最近 6 场考试与当前行动队列 · 先复核数据，再确认教学行动</p>
    {items.length === 0 ? <Card className="p-4 text-sm text-ink-soft">当前范围内没有待处理事项，可查看全部考试与班级诊断单。</Card> :
      <div className="grid gap-3 lg:grid-cols-3">{items.slice(0, 3).map((item, i) => <Card key={item.id} className="flex flex-col gap-2 p-4">
        <Badge tone="warn">优先 {i + 1}</Badge><p className="font-semibold">{item.title}</p>
        <p className="text-sm text-ink-soft">{item.reason}</p>
        <Link className="mt-auto pt-2 text-sm font-semibold text-accent-deep underline underline-offset-4" to={item.to}>{item.action} →</Link>
      </Card>)}</div>}
    <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-sm">
      <Link className="text-accent-deep underline" to={`${base}/exams?tab=diagnosis`}>全部行动建议（{plan.pending_confirm}）</Link>
      {summary && <span className="text-ink-soft">复测结果：{summary.effects.improved} 项改善 / {summary.evaluable_count} 项可评估 · {summary.effects.awaiting_retest} 项待复测</span>}
    </div>
  </section>;
}
