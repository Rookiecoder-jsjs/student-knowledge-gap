import type { usePhotoJob } from "../lib/usePhotoJob";
import { Button, Card } from "./ui";

export function JobProgress({ task }: { task: ReturnType<typeof usePhotoJob> }) {
  if (task.id === null && !task.error) return null;
  const labels = { queued: "排队中", running: "正在解析", succeeded: "解析完成", failed: "解析失败" };
  return <Card className="mb-4 p-4" >
    <p role="status" aria-live="polite" className="font-semibold">
      {task.job ? labels[task.job.status] : "正在读取任务进度"}
    </p>
    {task.active && <p className="mt-1 text-sm text-ink-soft">可以刷新页面，处理进度会自动恢复。</p>}
    {(task.error || task.job?.error) && <p role="alert" className="mt-2 text-sm text-danger">{task.error || task.job?.error}</p>}
    <div className="mt-3 flex gap-2">
      {task.job?.status === "failed" && <Button onClick={() => void task.retry()} disabled={task.retrying}>重试解析</Button>}
      <Button variant="ghost" onClick={task.dismiss}>{task.active ? "隐藏进度" : "关闭进度"}</Button>
    </div>
  </Card>;
}
