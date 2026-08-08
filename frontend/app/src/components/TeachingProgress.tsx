import { Plus, Trash } from "@phosphor-icons/react";
import { useState } from "react";
import { Badge, Button, Card, ErrorState, Skeleton } from "./ui";
import { StaggerItem, StaggerList } from "./motion";
import { deleteProgress, getProgress, listKps, patchProgress, updateProgress } from "../lib/api";
import { useAsync } from "../lib/hooks";

/** 班级教学进度卡片：已教清单 + 添加/删除/改日期（kb-edit §4.2/§7.3）。 */
export function TeachingProgressCard({ classId }: { classId: number }) {
  const progress = useAsync(() => getProgress(classId), [classId]);
  const kps = useAsync(() => listKps(), []);
  const [adding, setAdding] = useState(false);
  const [selCode, setSelCode] = useState("");
  const [taughtAt, setTaughtAt] = useState(() => new Date().toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const taughtCodes = new Set(progress.data?.progress.map((p) => p.code) ?? []);
  const available = (kps.data?.kps ?? []).filter(
    (k) => !k.archived && !k.code.startsWith("C") && !taughtCodes.has(k.code)
  );

  async function add() {
    if (!selCode) return;
    setBusy(true);
    setErr(null);
    try {
      await updateProgress(classId, [selCode], taughtAt);
      setAdding(false);
      setSelCode("");
      progress.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "添加失败");
    } finally {
      setBusy(false);
    }
  }

  async function remove(kpId: number) {
    setBusy(true);
    setErr(null);
    try {
      await deleteProgress(classId, kpId);
      progress.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  async function changeDate(kpId: number, d: string) {
    setErr(null);
    try {
      await patchProgress(classId, kpId, d);
      progress.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "修改失败");
    }
  }

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs text-ink-faint">
          已教 {progress.data?.progress.length ?? 0} 个知识点
        </span>
        {!adding && (
          <button
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1 text-xs font-medium text-accent transition-colors hover:text-accent-deep"
          >
            <Plus size={14} /> 添加已教
          </button>
        )}
      </div>

      {err && <p className="mb-2 text-xs text-danger">{err}</p>}

      {progress.loading && <Skeleton rows={2} />}
      {progress.error && <ErrorState message={progress.error} onRetry={progress.reload} />}

      {progress.data && progress.data.progress.length === 0 && !adding && (
        <p className="text-sm leading-relaxed text-ink-faint">
          尚未标记教学进度。未教知识点显示为「未学到」，不影响待加强判定。
        </p>
      )}

      {progress.data && progress.data.progress.length > 0 && (
        <StaggerList className="space-y-1.5">
          {progress.data.progress.map((p) => (
            <StaggerItem
              key={p.kp_id}
              className={`flex items-center gap-2 text-sm ${p.archived ? "opacity-50" : ""}`}
            >
              <span className="min-w-0 flex-1 truncate">
                <span className="font-mono text-xs text-ink-faint">{p.code}</span> {p.name}
                {p.archived && <Badge tone="warn">已停用</Badge>}
              </span>
              <input
                type="date"
                value={p.taught_at}
                disabled={busy}
                onChange={(e) => changeDate(p.kp_id, e.target.value)}
                className="rounded-lg border border-line bg-surface px-2 py-1 text-xs text-ink-soft transition-colors focus:border-accent"
              />
              <button
                onClick={() => remove(p.kp_id)}
                disabled={busy}
                className="text-ink-faint transition-colors hover:text-danger disabled:opacity-50"
                aria-label={`删除${p.name}的教学进度`}
              >
                <Trash size={14} />
              </button>
            </StaggerItem>
          ))}
        </StaggerList>
      )}

      {adding && (
        <div className="mt-3 space-y-2 rounded-xl border border-line bg-surface-2 p-3">
          {available.length === 0 ? (
            <p className="text-xs text-ink-faint">所有未停用的知识点均已标记教学进度。</p>
          ) : (
            <select
              value={selCode}
              onChange={(e) => setSelCode(e.target.value)}
              className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm transition-colors focus:border-accent"
            >
              <option value="">选择知识点…</option>
              {available.map((k) => (
                <option key={k.code} value={k.code}>
                  {k.code} · {k.name}
                </option>
              ))}
            </select>
          )}
          <input
            type="date"
            value={taughtAt}
            onChange={(e) => setTaughtAt(e.target.value)}
            className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm transition-colors focus:border-accent"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setAdding(false)} disabled={busy}>
              取消
            </Button>
            <Button variant="primary" onClick={add} disabled={busy || !selCode}>
              添加
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
