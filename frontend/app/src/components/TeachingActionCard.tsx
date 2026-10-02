import { useState } from "react";
import { confirmIntervention, teachingCard } from "../lib/api";
import { useAsync } from "../lib/hooks";
import type { InterventionRow } from "../lib/types";
import { Button, ErrorState, Input, Modal, Skeleton } from "./ui";

export function TeachingActionCard({ row, onClose, onChanged }: {
  row: InterventionRow; onClose: () => void; onChanged?: () => void;
}) {
  const card = useAsync(() => teachingCard(row.id), [row.id]);
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = async () => {
    setBusy(true); setError(null);
    try {
      await confirmIntervention(row.id, row.scope === "group", undefined, due || undefined);
      onChanged?.(); onClose();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <Modal open onClose={() => { if (!busy) onClose(); }} title="十五分钟教学行动卡" size="lg" footer={<>
    <Button variant="ghost" onClick={onClose} disabled={busy}>关闭</Button>
    {row.status === "suggested" && <Button onClick={confirm} disabled={busy || card.loading || !!card.error}>{busy ? "保存中…" : "已执行，安排复测"}</Button>}
  </>}>
    {card.loading && <Skeleton rows={5} />}
    {card.error && <ErrorState message={card.error} onRetry={card.reload} />}
    {!card.loading && !card.error && card.data && <div className="space-y-4 text-sm">
      <p className="font-semibold">{card.data.kp_name} · {card.data.duration_minutes} 分钟</p>
      <p className="text-ink-soft">{card.data.goal}</p>
      {card.data.root_kp_name && <p>先补基础：{card.data.root_kp_name}</p>}
      <p className="text-xs text-ink-faint">准备：{card.data.preparation}</p>
      <ol className="space-y-2">
        {card.data.steps.map((step, index) => <li key={step.title} className="rounded-lg bg-surface-2 p-3">
          <b>{index + 1}. {step.title} · {step.minutes} 分钟</b>
          <p className="mt-1 text-ink-soft">{step.instruction}</p>
        </li>)}
      </ol>
      <div>
        <p className="font-medium">可参考的原题</p>
        {card.data.examples.length === 0 ? <p className="mt-1 text-xs text-ink-faint">本场暂无关联原题，请教师选择同知识点例题。</p> : card.data.examples.map(q =>
          <p key={`${q.exam_id}:${q.question_idx}`} className="mt-1 break-words text-xs text-ink-soft">{q.exam_name} · 第 {q.question_idx} 题：{q.stem || "请查原试卷"}</p>
        )}
      </div>
      <p className="text-xs text-ink-faint">{card.data.verification}</p>
      {row.status === "suggested" && <label className="flex flex-wrap items-center gap-2 text-xs text-ink-soft">
        复测日期（留空默认 7 天后）
        <Input type="date" aria-label="行动卡复测日期" value={due} onChange={e => setDue(e.target.value)} />
      </label>}
      {error && <p className="text-danger" role="alert">{error}</p>}
    </div>}
  </Modal>;
}
