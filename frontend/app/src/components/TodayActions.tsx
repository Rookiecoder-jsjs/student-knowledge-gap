import { ArrowRight } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import type { ActionPlanView, ExamSummary } from "../lib/types";
import { Card } from "./ui";

/** 工作台只呈现能立即推进的事项；建议的完整内容留在班级诊断单。 */
export function TodayActions({ classId, exams, plan }: {
  classId: number;
  exams: ExamSummary[];
  plan: ActionPlanView;
}) {
  const base = `/c/${classId}`;
  const reviewTasks = exams
    .filter((exam) => exam.unreviewed_tags > 0 || (exam.response_counts["待审核"] ?? 0) > 0)
    .sort((a, b) => a.exam_date.localeCompare(b.exam_date))
    .map((exam) => {
      const reviewTags = exam.unreviewed_tags > 0;
      const responseCount = exam.response_counts["待审核"] ?? 0;
      return {
        id: `exam-${exam.exam_id}`,
        title: `${exam.name}待复核`,
        reason: [
          reviewTags ? `${exam.unreviewed_tags} 个知识点标注` : null,
          responseCount > 0 ? `${responseCount} 份作答` : null,
        ].filter(Boolean).join(" · "),
        action: reviewTags ? "复核标注" : "审核作答",
        to: `${base}/exams/${exam.exam_id}/${reviewTags ? "review" : "collect"}`,
      };
    });
  const actionTasks = plan.rows.map((row) => ({
    id: `action-${row.id}`,
    title: `${row.kp_name}的教学建议待确认`,
    reason: row.scope === "class" ? "面向全班" : row.scope === "group" ? `面向 ${row.group_size ?? 0} 人小组` : `面向 ${row.alias ?? "一名学生"}`,
    action: "查看依据并确认",
    to: `${base}/exams?tab=diagnosis`,
  }));
  const tasks = [...reviewTasks, ...actionTasks];

  return (
    <section aria-labelledby="workbench-tasks-title">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="workbench-tasks-title" className="text-lg font-bold text-ink">现在要处理</h2>
          <p className="mt-1 text-xs text-ink-faint">最近 6 场考试与当前行动队列 · 先复核数据，再确认建议</p>
        </div>
        <span className="text-xs tabular-nums text-ink-faint">显示前 {Math.min(tasks.length, 3)} 项</span>
      </div>
      <Card className="divide-y divide-line">
        {tasks.length === 0 ? (
          <p className="px-5 py-6 text-sm text-ink-soft">当前范围内没有需要处理的事项。</p>
        ) : tasks.slice(0, 3).map((task, index) => (
          <div key={task.id} className="flex flex-wrap items-center gap-4 px-5 py-4 sm:flex-nowrap">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center bg-accent-soft text-xs font-bold tabular-nums text-accent-deep">{index + 1}</span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-ink">{task.title}</p>
              <p className="mt-1 text-xs text-ink-soft">{task.reason}</p>
            </div>
            <Link to={task.to} className="inline-flex items-center gap-1 text-sm font-semibold text-accent-deep underline underline-offset-4 hover:text-accent">
              {task.action}<ArrowRight size={15} aria-hidden />
            </Link>
          </div>
        ))}
      </Card>
      {(reviewTasks.length > 0 || plan.pending_confirm > 0) && (
        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-xs">
          {reviewTasks.length > 0 && <Link className="text-accent-deep underline" to={`${base}/exams`}>查看全部考试</Link>}
          {plan.pending_confirm > 0 && <Link className="text-accent-deep underline" to={`${base}/exams?tab=diagnosis`}>查看全部待确认建议（{plan.pending_confirm}）</Link>}
        </div>
      )}
    </section>
  );
}
