import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, jobStatus, retryJob, type JobStatus } from "./api";

/** URL retains the task across reloads; the server rechecks access on every read. */
export function usePhotoJob(kind: "photo_template" | "photo_response", onSuccess: (result: Record<string, unknown>) => void) {
  const [params, setParams] = useSearchParams();
  const raw = params.get("photo_job");
  const id = raw && /^\d+$/.test(raw) && Number(raw) > 0 ? Number(raw) : null;
  const [job, setJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const success = useRef(onSuccess);
  useEffect(() => { success.current = onSuccess; }, [onSuccess]);

  useEffect(() => {
    setJob(null);
    setError(raw && id === null ? "任务编号无效" : null);
    if (id === null) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    let failures = 0;
    const poll = async () => {
      try {
        const current = await jobStatus(id, controller.signal);
        if (!alive) return;
        if (current.kind !== kind) {
          setError("此任务不属于当前录入流程");
          return;
        }
        failures = 0;
        setError(null);
        setJob(current);
        if (current.status === "succeeded") {
          if (!current.result) throw new Error("任务结果不完整，请联系管理员");
          success.current(current.result);
          return;
        }
        if (current.status === "failed") return;
      } catch (e) {
        if (!alive) return;
        const terminal = e instanceof ApiError && [401, 403, 404].includes(e.status);
        setError(terminal ? (e as Error).message : "暂时无法读取任务进度，正在重新连接…");
        if (terminal) return;
        failures += 1;
      }
      if (alive) timer = setTimeout(poll, Math.min(15000, 1500 * 2 ** Math.min(failures, 3)));
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); controller.abort(); };
  }, [id, raw, kind, revision]);

  const track = useCallback((jobId: number) => {
    setParams((previous) => { const next = new URLSearchParams(previous); next.set("photo_job", String(jobId)); return next; }, { replace: true });
    setRevision((v) => v + 1);
  }, [setParams]);
  const dismiss = () => setParams((previous) => {
    const next = new URLSearchParams(previous); next.delete("photo_job"); return next;
  }, { replace: true });
  const retry = async () => {
    if (id === null || retrying) return;
    setRetrying(true);
    try { await retryJob(id); setRevision((v) => v + 1); }
    catch (e) { setError((e as Error).message); }
    finally { setRetrying(false); }
  };
  return { id, job, error, track, dismiss, retry, retrying,
    active: id !== null && (!job || job.status === "queued" || job.status === "running") };
}
