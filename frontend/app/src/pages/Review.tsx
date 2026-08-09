import { Check, PencilLine, SealCheck } from "@phosphor-icons/react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "../components/ui";
import { approveTags, examDetail, listKps, patchAnswer, patchTags, reviewQueue } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { bandLabel } from "../lib/labels";

/** 审核台：LLM 标注逐题确认 + 低置信得分人工核对。 */
export default function Review() {
  const { classId, examId } = useParams();
  const cid = Number(classId);
  const eid = Number(examId);
  const nav = useNavigate();

  const detail = useAsync(() => examDetail(eid), [eid]);
  const queue = useAsync(() => reviewQueue(eid), [eid]);
  const kps = useAsync(() => listKps(), []);

  const [editingQ, setEditingQ] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [pickCode, setPickCode] = useState("");
  const [fixScore, setFixScore] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const kpCodes = (kps.data?.kps ?? []).map((k) => `${k.code} ${k.name}`);

  const startEdit = (qid: number, current: string) => {
    setEditingQ(qid);
    setDraft(current);
    setErr(null);
  };

  const saveTags = async (qid: number) => {
    const items = draft
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .map((l) => {
        const [code, w] = l.split(/[\s,，]+/);
        return { code, weight: w ? Number(w) : 1.0 };
      });
    if (items.length === 0) return setErr("至少保留一个知识点标注");
    setBusy(true);
    setErr(null);
    try {
      await patchTags(qid, items);
      setEditingQ(null);
      detail.reload();
      queue.reload();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const saveScore = async (answerId: number) => {
    const v = fixScore[answerId];
    if (v === undefined || v === "") return;
    setBusy(true);
    setErr(null);
    try {
      const r = await patchAnswer(answerId, { score: Number(v) });
      setNotice(`得分已更正，该生总分重新计算为 ${r.total_score}`);
      setFixScore((s) => ({ ...s, [answerId]: "" }));
      queue.reload();
      detail.reload();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const approveAll = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await approveTags(eid);
      setNotice(
        r.pending > 0
          ? `已批量批准 ${r.approved} 条标注，仍有 ${r.pending} 题需逐题复核`
          : `已批准 ${r.approved} 条标注`
      );
      detail.reload();
      queue.reload();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const unreviewedQids = new Set(queue.data?.unreviewed_tags.map((t) => t.question_id) ?? []);
  const reviewReasons = new Map(
    queue.data?.unreviewed_tags.map((t) => [t.question_id, t.review_reason]) ?? []
  );

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-ink">审核标注与低置信得分</p>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => nav(`/c/${cid}/exams/${eid}/collect`)}>
            下一步：采集学生卷
          </Button>
          <Button onClick={approveAll} disabled={busy || unreviewedQids.size === 0}>
            <SealCheck size={16} />
            批准全部待审标注{unreviewedQids.size > 0 ? `（${unreviewedQids.size} 题）` : ""}
          </Button>
        </div>
      </div>

      {notice && (
        <div className="mb-4 rounded-xl border border-accent/20 bg-accent-soft px-4 py-2.5 text-sm text-accent-deep" role="status">
          {notice}
        </div>
      )}
      {err && (
        <div className="mb-4 rounded-xl border border-danger/20 bg-danger-soft px-4 py-2.5 text-sm text-danger" role="alert">
          {err}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        {/* 左：题目与标注 */}
        <section>
          <SectionTitle
            count={unreviewedQids.size > 0 ? `${unreviewedQids.size} 题待确认` : undefined}
          >
            题目与知识点标注
          </SectionTitle>
          {detail.loading && <Skeleton rows={4} />}
          {detail.error && <ErrorState message={detail.error} onRetry={detail.reload} />}
          {detail.data && (
            <Card className="divide-y divide-line">
              {detail.data.questions.map((q) => {
                const needsReview = unreviewedQids.has(q.question_id);
                const isEditing = editingQ === q.question_id;
                return (
                  <div key={q.question_id} className="px-5 py-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold">
                          题{q.idx}
                          <span className="ml-2 font-normal text-ink-faint">
                            {q.q_type} · 满分 {q.full_score} · {q.cog_level}
                          </span>
                        </p>
                        {q.stem && (
                          <p className="mt-1 line-clamp-2 text-xs text-ink-soft">{q.stem}</p>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        {needsReview && (
                          <Badge tone="warn">{reviewReasons.get(q.question_id) ?? "待确认"}</Badge>
                        )}
                        {!isEditing && (
                          <Button
                            variant="ghost"
                            onClick={() =>
                              startEdit(
                                q.question_id,
                                q.kps.map((k) => `${k.code} ${k.weight}`).join("\n")
                              )
                            }
                          >
                            <PencilLine size={14} />
                            改标
                          </Button>
                        )}
                      </div>
                    </div>

                    {!isEditing ? (
                      <div className="mt-2.5 flex flex-wrap gap-1.5">
                        {q.kps.length === 0 && (
                          <span className="text-xs text-danger">未标注 - 未标注题目不进入分析</span>
                        )}
                        {q.kps.map((k) => (
                          <Badge key={k.tag_id} tone={k.reviewed ? "accent" : "warn"}>
                            {k.code} {k.name}
                            {!k.reviewed && ` · 把握 ${(k.confidence * 100).toFixed(0)}%`}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <div className="mt-3 space-y-2">
                        <textarea
                          rows={2}
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          placeholder="每行：知识点编码 权重（如 M7A-105 1.0）"
                          className="w-full rounded-xl border border-line bg-surface px-3 py-2 font-mono text-xs transition-colors focus:border-accent"
                        />
                        <div className="flex items-center gap-2">
                          <input
                            list="kp-codes"
                            value={pickCode}
                            onChange={(e) => setPickCode(e.target.value)}
                            placeholder="搜索知识点编码…"
                            className="w-56 rounded-xl border border-line bg-surface px-2.5 py-1.5 text-xs transition-colors focus:border-accent"
                          />
                          <Button
                            variant="secondary"
                            onClick={() => {
                              const code = pickCode.trim().split(/\s/)[0];
                              if (!code) return;
                              setDraft((d) => (d.trim() ? `${d.trim()}\n${code} 1.0` : `${code} 1.0`));
                              setPickCode("");
                            }}
                          >
                            加入标注
                          </Button>
                        </div>
                        <datalist id="kp-codes">
                          {kpCodes.map((c) => (
                            <option key={c} value={c.split(" ")[0]}>
                              {c}
                            </option>
                          ))}
                        </datalist>
                        <div className="flex gap-2">
                          <Button onClick={() => saveTags(q.question_id)} disabled={busy}>
                            <Check size={14} />
                            保存标注
                          </Button>
                          <Button variant="ghost" onClick={() => setEditingQ(null)}>
                            取消
                          </Button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </Card>
          )}
        </section>

        {/* 右：低置信得分队列 */}
        <section>
          <SectionTitle count={queue.data?.low_confidence_answers.length ?? 0}>需核对的得分</SectionTitle>
          {queue.loading && <Skeleton rows={3} />}
          {queue.error && <ErrorState message={queue.error} onRetry={queue.reload} />}
          {queue.data && queue.data.low_confidence_answers.length === 0 && (
            <Card>
              <EmptyState
                title="没有需要核对的得分"
                hint="把握低于 90% 的解析结果会出现在这里；必须人工核对的题目需逐条确认。"
              />
            </Card>
          )}
          {queue.data && queue.data.low_confidence_answers.length > 0 && (
            <Card className="divide-y divide-line">
              {queue.data.low_confidence_answers.map((a) => (
                <div key={a.answer_id} className="px-5 py-3.5">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium">
                      {a.student_name} · 题{a.question_idx}
                    </p>
                    <Badge tone={a.band === "强制人工" ? "danger" : "warn"}>{bandLabel(a.band)}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-ink-faint">
                    AI 解析得分 {a.score} / {a.full_score}（把握 {(a.confidence * 100).toFixed(0)}%）
                  </p>
                  <div className="mt-2.5 flex items-center gap-2">
                    <input
                      type="number"
                      min={0}
                      max={a.full_score}
                      placeholder="更正确得分"
                      value={fixScore[a.answer_id] ?? ""}
                      onChange={(e) =>
                        setFixScore((s) => ({ ...s, [a.answer_id]: e.target.value }))
                      }
                      className="w-28 rounded-xl border border-line bg-surface px-2.5 py-1.5 text-sm transition-colors focus:border-accent"
                    />
                    <Button
                      variant="secondary"
                      onClick={() => saveScore(a.answer_id)}
                      disabled={busy || fixScore[a.answer_id] === undefined || fixScore[a.answer_id] === ""}
                    >
                      更正
                    </Button>
                  </div>
                </div>
              ))}
            </Card>
          )}
        </section>
      </div>
    </div>
  );
}
