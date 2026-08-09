import { Plus, Trash } from "@phosphor-icons/react";
import { useState } from "react";
import { Button, Card, ErrorState, Modal, Skeleton } from "./ui";
import { deleteProgress, getProgress, listKps, patchProgress, updateProgress } from "../lib/api";
import { useAsync } from "../lib/hooks";

/**
 * 班级教学进度：仪表盘紧凑摘要 + 「管理」弹窗。
 * 卡片只展示计数/进度条/最近几项，完整增删改在弹窗内（避免长列表撑高页面）。
 */
export function TeachingProgressCard({ classId }: { classId: number }) {
  const progress = useAsync(() => getProgress(classId), [classId]);
  const kps = useAsync(() => listKps(), []);
  const [manageOpen, setManageOpen] = useState(false);

  const taught = progress.data?.progress ?? [];
  const total = (kps.data?.kps ?? []).filter((k) => !k.archived).length;
  const pct = total > 0 ? Math.round((taught.length / total) * 100) : 0;

  // 按章节聚合已教/总数，呈现进度分布（高密度、有界高度）
  const taughtSet = new Set(taught.map((p) => p.code));
  const chapterMap = new Map<string, { taught: number; total: number }>();
  for (const k of kps.data?.kps ?? []) {
    if (k.archived || k.code.startsWith("C")) continue;
    const ch = k.chapter || "未分组";
    const e = chapterMap.get(ch) ?? { taught: 0, total: 0 };
    e.total++;
    if (taughtSet.has(k.code)) e.taught++;
    chapterMap.set(ch, e);
  }
  const chapters = [...chapterMap.entries()].map(([name, c]) => ({ name, ...c }));

  return (
    <Card className="p-4">
      <div className="mb-2.5 flex items-center justify-between">
        <span className="text-xs font-medium text-ink-faint tabular-nums">
          已教 {taught.length}
          {total > 0 ? ` / ${total}` : ""}
        </span>
        <button
          onClick={() => setManageOpen(true)}
          className="text-xs font-medium text-accent transition-colors hover:text-accent-deep"
        >
          管理
        </button>
      </div>

      {/* 进度条 */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
        <div
          className="h-full rounded-full bg-accent transition-all duration-500"
          style={{ width: `${Math.max(3, pct)}%` }}
        />
      </div>

      {progress.loading && <Skeleton rows={1} />}
      {progress.error && <ErrorState message={progress.error} onRetry={progress.reload} />}

      {!progress.loading && taught.length === 0 && (
        <p className="mt-3 text-xs leading-relaxed text-ink-faint">
          尚未标记教学进度。未教知识点显示为「未学到」，不影响待加强判定。
        </p>
      )}

      {chapters.length > 0 && (
        <div className="mt-3">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
            按章节
          </p>
          <div className="max-h-[208px] space-y-2 overflow-y-auto pr-1">
            {chapters.map((c) => {
              const cpct = c.total > 0 ? Math.round((c.taught / c.total) * 100) : 0;
              return (
                <div key={c.name}>
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="truncate text-ink-soft">{c.name}</span>
                    <span className="ml-2 shrink-0 tabular-nums text-ink-faint">
                      {c.taught}/{c.total}
                    </span>
                  </div>
                  <div className="h-1 w-full overflow-hidden rounded-full bg-surface-2">
                    <div
                      className="h-full rounded-full bg-accent/70 transition-all duration-500"
                      style={{ width: `${Math.max(2, cpct)}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {manageOpen && (
        <ManageTeachingProgress
          classId={classId}
          onClose={() => {
            setManageOpen(false);
            progress.reload();
          }}
        />
      )}
    </Card>
  );
}

/** 管理弹窗：完整已教清单 + 增删改日期（kb-edit §4.2/§7.3）。 */
function ManageTeachingProgress({
  classId,
  onClose,
}: {
  classId: number;
  onClose: () => void;
}) {
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
    <Modal
      open
      onClose={onClose}
      title="管理教学进度"
      size="lg"
      footer={<Button onClick={onClose}>完成</Button>}
    >
      {err && <p className="text-xs text-danger">{err}</p>}

      {progress.loading && <Skeleton rows={3} />}
      {progress.error && <ErrorState message={progress.error} onRetry={progress.reload} />}

      {progress.data && (
        <>
          <div className="flex items-center justify-between">
            <span className="text-xs text-ink-faint tabular-nums">
              已教 {progress.data.progress.length} 个知识点
            </span>
            {!adding && (
              <Button variant="secondary" onClick={() => setAdding(true)}>
                <Plus size={14} /> 添加已教
              </Button>
            )}
          </div>

          {progress.data.progress.length === 0 && !adding && (
            <p className="py-6 text-center text-sm text-ink-faint">
              尚未标记教学进度，点击右上角「添加已教」开始。
            </p>
          )}

          {progress.data.progress.length > 0 && (
            <div className="max-h-[44vh] space-y-1 overflow-y-auto pr-1">
              {progress.data.progress.map((p) => (
                <div
                  key={p.kp_id}
                  className={`flex items-center gap-2 rounded-md px-1 py-1 text-sm hover:bg-surface-2 ${
                    p.archived ? "opacity-50" : ""
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate">
                    <span className="font-mono text-xs text-ink-faint">{p.code}</span> {p.name}
                  </span>
                  <input
                    type="date"
                    value={p.taught_at}
                    disabled={busy}
                    onChange={(e) => changeDate(p.kp_id, e.target.value)}
                    className="rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink-soft transition-colors focus:border-accent"
                  />
                  <button
                    onClick={() => remove(p.kp_id)}
                    disabled={busy}
                    className="text-ink-faint transition-colors hover:text-danger disabled:opacity-50"
                    aria-label={`删除${p.name}的教学进度`}
                  >
                    <Trash size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {adding && (
            <div className="mt-2 space-y-2 rounded-lg border border-line bg-surface-2 p-3">
              {available.length === 0 ? (
                <p className="text-xs text-ink-faint">所有未停用的知识点均已标记教学进度。</p>
              ) : (
                <select
                  value={selCode}
                  onChange={(e) => setSelCode(e.target.value)}
                  className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm transition-colors focus:border-accent"
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
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm transition-colors focus:border-accent"
              />
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setAdding(false)} disabled={busy}>
                  取消
                </Button>
                <Button onClick={add} disabled={busy || !selCode}>
                  添加
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
