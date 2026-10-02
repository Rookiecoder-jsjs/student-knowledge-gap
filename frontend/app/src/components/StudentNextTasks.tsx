import { Link } from "react-router-dom";
import { meNextTasks, portalNextTasks } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { Badge, Card, ErrorState, Skeleton } from "./ui";

export function StudentNextTasks({ previewStudentId, studyBase }: { previewStudentId?: number; studyBase: string }) {
  const tasks = useAsync(() => previewStudentId ? portalNextTasks(previewStudentId) : meNextTasks(), [previewStudentId]);
  return <section className="mb-5 space-y-3" aria-label="学生下一步任务卡">
    <h2 className="text-sm font-semibold">今天先做这一步</h2>
    <p className="text-xs text-ink-faint">最多展示 3 个重点，先完成一项再继续。自报学习完成后，还需要用复测作答验证。</p>
    {tasks.loading && <Skeleton rows={2} />}
    {tasks.error && <ErrorState message={tasks.error} onRetry={tasks.reload} />}
    {!tasks.loading && !tasks.error && tasks.data && <>
      {tasks.data.items.length === 0 && <Card className="p-4 text-sm text-ink-soft">目前没有需要优先处理的学习任务。</Card>}
      {tasks.data.items.map(task => <Card key={task.kp_code} className="space-y-3 p-4">
        <p className="flex flex-wrap items-center gap-2 font-semibold">{task.kp_name}
          <Badge tone={task.status === "study" ? "warn" : "neutral"}>{task.status === "study" ? "建议学习" : task.self_marked ? "已自报，待复测" : "老师已执行，待复测"}</Badge></p>
        <p className="text-sm text-ink-soft">{task.reason} · 约 {task.minutes} 分钟</p>
        <ol className="list-inside list-decimal space-y-1 text-sm">{task.steps.map(step => <li key={step}>{step}</li>)}</ol>
        <p className="text-xs text-ink-soft">完成标准：{task.completion}</p>
        {task.retest_due_date && <p className="text-xs text-ink-faint">老师安排的复测日期：{task.retest_due_date}</p>}
        {(!previewStudentId || task.has_plan) ? <Link className="inline-block text-sm font-semibold text-accent-deep underline" to={`${studyBase}?kp_code=${encodeURIComponent(task.kp_code)}`}>{task.has_plan ? "查看学习方案与进度" : "开始学习"} →</Link> :
          <p className="text-xs text-ink-faint">该生尚未生成学习方案；管理员预览保持只读。</p>}
      </Card>)}
      {tasks.data.remaining > 0 && <p className="text-xs text-ink-faint">还有 {tasks.data.remaining} 个关注点，可在下方薄弱环节中查看。</p>}
    </>}
  </section>;
}
