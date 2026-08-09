import { Camera, CheckCircle, Images, Keyboard } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Modal, Skeleton } from "../components/ui";
import {
  assignBatchItem,
  batchJob,
  discardBatchItem,
  examDetail,
  examResponses,
  manualEntry,
  photoBatch,
  photoResponse,
  retryBatchItem,
} from "../lib/api";
import { useAsync } from "../lib/hooks";
import type { BatchItemStatus, BatchJob, CollectStatus } from "../lib/types";

const STATUS_TONE: Record<CollectStatus, "neutral" | "warn" | "accent"> = {
  未采集: "neutral",
  待审核: "warn",
  已提交: "accent",
};

/** 学生卷采集：矩阵视图 + 拍照/手工录入 + 提交。 */
export default function Collect() {
  const { classId, examId } = useParams();
  const cid = Number(classId);
  const eid = Number(examId);
  const nav = useNavigate();

  const detail = useAsync(() => examDetail(eid), [eid]);
  const matrix = useAsync(() => examResponses(eid), [eid]);

  const [manualFor, setManualFor] = useState<number | null>(null);
  const [photoFor, setPhotoFor] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const committed = (matrix.data?.summary["已提交"] ?? 0) > 0;

  const uploadPhoto = async (file: File) => {
    if (photoFor === null) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await photoResponse(eid, photoFor, file);
      setNotice(
        `解析完成${r.warnings.length > 0 ? `（警告：${r.warnings.join("；")}）` : ""}，请留意把握低的题目。`
      );
      setPhotoFor(null);
      matrix.reload();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-ink">
          学生卷采集
          {matrix.data && (
            <span className="ml-2 font-normal text-ink-faint tabular-nums">
              未采集 {matrix.data.summary["未采集"]} · 待审核 {matrix.data.summary["待审核"]} · 已提交 {matrix.data.summary["已提交"]}
            </span>
          )}
        </p>
        {!committed && (
          <Button variant="secondary" onClick={() => nav(`/c/${cid}/exams/${eid}/commit`)}>
            下一步：去提交
          </Button>
        )}
      </div>

      {committed && (
        <div className="mb-4 flex items-center justify-between rounded-lg border border-accent/20 bg-accent-soft px-4 py-2.5 text-sm text-accent-deep">
          <span className="flex items-center gap-2">
            <CheckCircle size={16} weight="fill" />
            本场已提交并生成依据；如需更正请以补录考试处理。
          </span>
          <Button variant="secondary" onClick={() => nav(`/c/${cid}/exams/${eid}/report`)}>
            查看质量分析
          </Button>
        </div>
      )}
      {notice && (
        <div className="mb-4 rounded-lg border border-accent/20 bg-accent-soft px-4 py-2.5 text-sm text-accent-deep" role="status">
          {notice}
        </div>
      )}
      {err && (
        <div className="mb-4 rounded-lg border border-danger/20 bg-danger-soft px-4 py-2.5 text-sm text-danger" role="alert">
          {err}
        </div>
      )}

      <BatchCollect
        eid={eid}
        committed={committed}
        students={(matrix.data?.responses ?? []).map((r) => ({
          student_id: r.student_id,
          name_or_alias: r.name_or_alias,
        }))}
        onReloadMatrix={matrix.reload}
      />

      {matrix.loading && <Skeleton rows={6} />}
      {matrix.error && <ErrorState message={matrix.error} onRetry={matrix.reload} />}
      {matrix.data && matrix.data.responses.length === 0 && (
        <Card>
          <EmptyState title="班级名单为空" hint="请先在初始化向导中添加学生。" />
        </Card>
      )}

      {matrix.data && matrix.data.responses.length > 0 && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-ink-faint">
                  <th className="px-5 py-3 font-medium">学生</th>
                  <th className="px-5 py-3 font-medium">状态</th>
                  <th className="px-5 py-3 font-medium">总分</th>
                  <th className="px-5 py-3 font-medium">提醒</th>
                  <th className="px-5 py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {matrix.data.responses.map((r) => (
                  <tr key={r.student_id} className="transition-colors hover:bg-surface-2/50">
                    <td className="px-5 py-3 font-medium">{r.name_or_alias}</td>
                    <td className="px-5 py-3">
                      <Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge>
                    </td>
                    <td className="px-5 py-3 text-ink-soft">
                      {r.total_score !== null ? r.total_score : "-"}
                    </td>
                    <td className="px-5 py-3">
                      {r.low_confidence_count > 0 && (
                        <Badge tone="warn">{r.low_confidence_count} 题把握低</Badge>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      {!committed && r.status !== "已提交" && (
                        <span className="flex justify-end gap-2">
                          <input
                            ref={fileRef}
                            type="file"
                            accept="image/*"
                            className="sr-only"
                            onChange={(e) => {
                              const f = e.target.files?.[0];
                              if (f) uploadPhoto(f);
                              e.target.value = "";
                            }}
                          />
                          <Button
                            variant="secondary"
                            onClick={() => {
                              setPhotoFor(r.student_id);
                              fileRef.current?.click();
                            }}
                            disabled={busy}
                          >
                            <Camera size={14} />
                            拍照
                          </Button>
                          <Button
                            variant="secondary"
                            onClick={() => setManualFor(manualFor === r.student_id ? null : r.student_id)}
                          >
                            <Keyboard size={14} />
                            手工录入
                          </Button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {manualFor !== null && detail.data && (
        <ManualForm
          key={manualFor}
          examId={eid}
          studentId={manualFor}
          questions={detail.data.questions.map((q) => ({ idx: q.idx, full_score: q.full_score }))}
          onClose={() => setManualFor(null)}
          onDone={(total) => {
            setManualFor(null);
            setNotice(`录入成功，该生总分 ${total}`);
            matrix.reload();
          }}
          onError={(m) => setErr(m)}
        />
      )}
    </div>
  );
}

/** 手工录入弹窗：复用统一 Modal（焦点圈定 + Esc，修 P0-2）。 */
function ManualForm({
  examId,
  studentId,
  questions,
  onClose,
  onDone,
  onError,
}: {
  examId: number;
  studentId: number;
  questions: { idx: number; full_score: number }[];
  onClose: () => void;
  onDone: (total: number) => void;
  onError: (msg: string) => void;
}) {
  const [scores, setScores] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const payload: Record<string, number> = {};
    for (const q of questions) {
      const v = scores[q.idx];
      const n = v === undefined || v === "" ? 0 : Number(v);
      if (Number.isNaN(n) || n < 0 || n > q.full_score) {
        onError(`题${q.idx} 得分越界（满分 ${q.full_score}）`);
        return;
      }
      payload[String(q.idx)] = n;
    }
    setBusy(true);
    try {
      const r = await manualEntry(examId, studentId, payload);
      onDone(r.total_score);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="手工录入得分"
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? "保存中…" : "保存作答"}
          </Button>
        </>
      }
    >
      <div className="grid max-h-[50vh] grid-cols-2 gap-3 overflow-y-auto sm:grid-cols-3">
        {questions.map((q) => (
          <label key={q.idx} className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-soft">
              题{q.idx}（满分 {q.full_score}）
            </span>
            <input
              type="number"
              min={0}
              max={q.full_score}
              value={scores[q.idx] ?? ""}
              onChange={(e) => setScores((s) => ({ ...s, [q.idx]: e.target.value }))}
              className="rounded-xl border border-line bg-surface px-2.5 py-1.5 text-sm transition-colors focus:border-accent"
            />
          </label>
        ))}
      </div>
      <p className="text-xs text-ink-faint">留空按 0 分计；任一题越界将整体拒绝。</p>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// 批量拍照录入（DESIGN 批量录入 v0.3）
// ---------------------------------------------------------------------------

const BATCH_TONE: Record<BatchItemStatus, "neutral" | "accent" | "warn" | "danger"> = {
  queued: "neutral",
  parsing: "warn",
  matched: "accent",
  unmatched: "danger",
  failed: "danger",
  duplicate: "warn",
  discarded: "neutral",
};

const BATCH_LABEL: Record<BatchItemStatus, string> = {
  queued: "排队中",
  parsing: "识别中",
  matched: "已匹配",
  unmatched: "待指派",
  failed: "失败",
  duplicate: "重复上传",
  discarded: "已放弃",
};

/** 轮询批任务：job 全终态(done)即停；refresh() 用于指派/重试/丢弃后恢复轮询。 */
function useBatchJob(jobId: number | null) {
  const [job, setJob] = useState<BatchJob | null>(null);
  const [rev, setRev] = useState(0);
  useEffect(() => {
    if (jobId === null) return;
    let alive = true;
    let timer: ReturnType<typeof setInterval> | null = null;
    const poll = async () => {
      try {
        const j = await batchJob(jobId);
        if (!alive) return;
        setJob(j);
        if (j.status === "done" && timer) {
          clearInterval(timer);
          timer = null;
        }
      } catch {
        /* 轮询失败静默，下次重试 */
      }
    };
    poll();
    timer = setInterval(poll, 2500);
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [jobId, rev]);
  const refresh = useCallback(() => setRev((v) => v + 1), []);
  return { job, refresh };
}

function BatchCollect({
  eid,
  students,
  committed,
  onReloadMatrix,
}: {
  eid: number;
  students: { student_id: number; name_or_alias: string }[];
  committed: boolean;
  onReloadMatrix: () => void;
}) {
  const [jobId, setJobId] = useState<number | null>(null);
  const { job, refresh } = useBatchJob(jobId);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [assignFor, setAssignFor] = useState<number | null>(null);
  const [assignTarget, setAssignTarget] = useState<string>("");
  const fileRef = useRef<HTMLInputElement>(null);

  // job 转为全终态时联动矩阵，避免「8 matched」但矩阵仍「未采集」
  const prevDone = useRef(false);
  useEffect(() => {
    if (job && job.status === "done") {
      if (!prevDone.current) {
        prevDone.current = true;
        onReloadMatrix();
      }
    } else {
      prevDone.current = false;
    }
  }, [job, onReloadMatrix]);

  const upload = async (files: FileList) => {
    setErr(null);
    setBusy(true);
    try {
      const r = await photoBatch(eid, Array.from(files));
      setJobId(r.job_id);
      prevDone.current = false;
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doAssign = async (itemId: number) => {
    if (!assignTarget) return;
    setBusy(true);
    setErr(null);
    try {
      await assignBatchItem(itemId, Number(assignTarget));
      setAssignFor(null);
      setAssignTarget("");
      refresh();
      onReloadMatrix();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doRetry = async (itemId: number) => {
    setBusy(true);
    setErr(null);
    try {
      await retryBatchItem(itemId);
      prevDone.current = false;
      refresh();
      onReloadMatrix();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doDiscard = async (itemId: number) => {
    setBusy(true);
    setErr(null);
    try {
      await discardBatchItem(itemId);
      refresh();
      onReloadMatrix();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const counts: Record<string, number> = {};
  job?.items.forEach((it) => {
    counts[it.status] = (counts[it.status] ?? 0) + 1;
  });
  const success = (counts.matched ?? 0) + (counts.duplicate ?? 0);
  const total = job?.items.length ?? 0;
  const processed = total - (counts.queued ?? 0) - (counts.parsing ?? 0);
  const pct = total > 0 ? Math.round((processed / total) * 100) : 0;

  return (
    <Card className="mb-5 p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Images size={16} className="text-accent" />
          批量拍照录入
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          className="sr-only"
          onChange={(e) => {
            if (e.target.files && e.target.files.length) upload(e.target.files);
            e.target.value = "";
          }}
        />
        <Button
          variant="secondary"
          onClick={() => fileRef.current?.click()}
          disabled={busy || committed || students.length === 0}
        >
          <Camera size={14} />
          批量上传学生卷
        </Button>
      </div>

      {committed && (
        <div className="mb-3 text-xs text-ink-faint">本场考试已提交，批量录入已置只读。</div>
      )}
      {err && (
        <div className="mb-3 rounded-xl border border-danger/20 bg-danger-soft px-3 py-2 text-sm text-danger" role="alert">
          {err}
        </div>
      )}

      {job && (
        <>
          {/* 进度条（修 P1-6） */}
          {job.status !== "done" && (
            <div className="mb-3">
              <div className="mb-1 flex items-center justify-between text-xs text-ink-soft">
                <span>{counts.parsing ? "识别中…" : "处理中…"}</span>
                <span className="font-medium">{processed}/{total}</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2">
                <div
                  className="h-full rounded-full bg-accent transition-all duration-300"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          )}
          <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-soft">
            <span>成功 {success}</span>
            <span>待指派 {counts.unmatched ?? 0}</span>
            <span className="text-danger">失败 {counts.failed ?? 0}</span>
            <span>已放弃 {counts.discarded ?? 0}</span>
            <span className="text-ink-faint">总 {total}</span>
            {job.status === "done" && (
              <span className="text-accent">· 全部完成，请在下方矩阵核对后提交</span>
            )}
          </div>
          <ul className="divide-y divide-line">
            {job.items.map((it) => (
              <li key={it.id} className="flex flex-wrap items-center gap-2 py-2.5 text-sm">
                <span
                  className={`min-w-[10rem] flex-1 truncate ${
                    it.status === "discarded" ? "text-ink-faint line-through" : ""
                  }`}
                >
                  {it.file_name}
                </span>
                <span className="w-28 text-ink-soft">
                  {it.status === "queued" || it.status === "parsing"
                    ? "识别中…"
                    : it.detected_name
                    ? `卷面：${it.detected_name}`
                    : it.matched_student_name ?? "-"}
                </span>
                <Badge tone={BATCH_TONE[it.status]}>{BATCH_LABEL[it.status]}</Badge>
                {it.warnings.length > 0 && (
                  <span className="text-xs text-warn" title={it.warnings.join("；")}>
                    {it.warnings.length} 提醒
                  </span>
                )}
                {!committed && it.status === "unmatched" && (
                  <span className="flex items-center gap-1">
                    {assignFor === it.id ? (
                      <>
                        <select
                          value={assignTarget}
                          onChange={(e) => setAssignTarget(e.target.value)}
                          className="rounded-lg border border-line bg-surface px-2 py-1 text-xs transition-colors focus:border-accent"
                        >
                          <option value="">选择学生…</option>
                          {students.map((s) => (
                            <option key={s.student_id} value={s.student_id}>
                              {s.name_or_alias}
                            </option>
                          ))}
                        </select>
                        <Button variant="secondary" onClick={() => doAssign(it.id)} disabled={busy || !assignTarget}>
                          指派
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => {
                            setAssignFor(null);
                            setAssignTarget("");
                          }}
                        >
                          取消
                        </Button>
                      </>
                    ) : (
                      <>
                        <Button variant="secondary" onClick={() => setAssignFor(it.id)}>
                          指派
                        </Button>
                        <Button variant="ghost" onClick={() => doDiscard(it.id)} disabled={busy}>
                          丢弃
                        </Button>
                      </>
                    )}
                  </span>
                )}
                {!committed && it.status === "failed" && (
                  <span className="flex items-center gap-1">
                    <Button variant="secondary" onClick={() => doRetry(it.id)} disabled={busy}>
                      重试
                    </Button>
                    <Button variant="ghost" onClick={() => doDiscard(it.id)} disabled={busy}>
                      丢弃
                    </Button>
                  </span>
                )}
                {it.status === "duplicate" && (
                  <span className="text-xs text-ink-faint">该生已有作答</span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
