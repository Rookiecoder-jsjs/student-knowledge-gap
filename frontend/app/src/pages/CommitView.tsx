import { CheckCircle, PaperPlaneTilt, WarningCircle } from "@phosphor-icons/react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, ErrorState, Modal, SectionTitle, Skeleton, StatTile } from "../components/ui";
import { commitExam, examResponses } from "../lib/api";
import { useAsync } from "../lib/hooks";

/** 阶段 4·提交：就绪检查 + 二次确认 + 提交结果（从 Collect 抽出的落闸动作）。 */
export default function CommitView() {
  const { classId, examId } = useParams();
  const cid = Number(classId);
  const eid = Number(examId);
  const matrix = useAsync(() => examResponses(eid), [eid]);

  const summary = matrix.data?.summary;
  const pending = summary?.["待审核"] ?? 0;
  const submitted = summary?.["已提交"] ?? 0;
  const uncollected = summary?.["未采集"] ?? 0;
  const committed = submitted > 0;
  const lowConf = (matrix.data?.responses ?? []).reduce((s, r) => s + r.low_confidence_count, 0);

  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<{
    committed_responses: number;
    evidence_events: number;
    quality_report?: boolean;
    diagnoses?: number;
    interventions?: number;
    skipped: string[];
  } | null>(null);

  const doCommit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await commitExam(eid);
      setResult(r);
      setConfirm(false);
      matrix.reload();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (matrix.loading) return <Skeleton rows={4} />;
  if (matrix.error) return <ErrorState message={matrix.error} onRetry={matrix.reload} />;

  // 已提交：落闸完成态
  if (committed && !result) {
    return (
      <Card className="flex flex-col items-center gap-3 py-12 text-center">
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft">
          <CheckCircle size={26} className="text-accent" weight="fill" />
        </span>
        <p className="text-sm font-semibold">本场考试已提交并生成分析依据</p>
        <p className="max-w-[48ch] text-xs text-ink-faint">
          作答与标注已锁定，如需更正请以补录考试处理。
        </p>
        <Link to={`/c/${cid}/exams?tab=diagnosis`}>
          <Button>查看班级诊断单</Button>
        </Link>
      </Card>
    );
  }

  return (
    <div>
      <SectionTitle>提交就绪检查</SectionTitle>

      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile value={pending} label="待提交作答" tone={pending > 0 ? "accent" : "neutral"} />
        <StatTile value={submitted} label="已提交" />
        <StatTile value={uncollected} label="未采集" tone={uncollected > 0 ? "warn" : "neutral"} />
        <StatTile value={lowConf} label="把握低待核对" tone={lowConf > 0 ? "warn" : "neutral"} />
      </div>

      {err && (
        <div className="mb-4 rounded-lg border border-danger/20 bg-danger-soft px-4 py-2.5 text-sm text-danger" role="alert">
          {err}
        </div>
      )}

      {/* 提交结果摘要 */}
      {result && (
        <Card className="mb-5 flex flex-col items-center gap-3 py-10 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft">
            <CheckCircle size={26} className="text-accent" weight="fill" />
          </span>
          <p className="text-sm font-semibold">
            已提交 {result.committed_responses} 份作答，生成 {result.evidence_events} 条分析依据
          </p>
          {result.quality_report && (
            <p className="text-xs text-ink-soft">
              已自动生成班级质量报告、班级改进意见 + {result.diagnoses ?? 0} 份学生诊断单与改进单
              {result.interventions ? `，${result.interventions} 条行动建议待确认` : ""}
              ，一次生成、随时查看。
            </p>
          )}
          {result.skipped.length > 0 && (
            <p className="max-w-[52ch] text-xs text-warn">
              跳过 {result.skipped.length} 条：{result.skipped.slice(0, 3).join("；")}
            </p>
          )}
          <div className="mt-1 flex gap-2">
            <Link to={`/c/${cid}/exams?tab=diagnosis`}>
              <Button>查看班级诊断单</Button>
            </Link>
            <Link to={`/c/${cid}/exams/${eid}/report`}>
              <Button variant="secondary">本场概况</Button>
            </Link>
          </div>
        </Card>
      )}

      {!result && (
        <Card className="p-5">
          {pending === 0 ? (
            <div className="flex items-center gap-2 text-sm text-ink-soft">
              <WarningCircle size={16} className="text-warn" />
              没有可提交的作答（待提交为 0）。请先在「采集」阶段录入学生卷。
            </div>
          ) : (
            <>
              <div className="mb-3 flex items-start gap-2 text-sm text-ink-soft">
                <WarningCircle size={16} className="mt-0.5 shrink-0 text-warn" />
                <p>
                  提交后本场考试的作答与标注将<strong className="text-ink">锁定</strong>，进入分析并生成证据。后续仅可通过补录考试追加。
                </p>
              </div>
              {lowConf > 0 && (
                <p className="mb-3 text-xs text-warn">
                  仍有 {lowConf} 处把握低的得分未核对，建议先回「采集」确认。
                </p>
              )}
              <div className="flex justify-end">
                <Button onClick={() => setConfirm(true)} disabled={busy}>
                  <PaperPlaneTilt size={15} />
                  提交本场考试（{pending} 份）
                </Button>
              </div>
            </>
          )}
        </Card>
      )}

      <Modal
        open={confirm}
        onClose={() => setConfirm(false)}
        title="确认提交"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(false)} disabled={busy}>
              取消
            </Button>
            <Button variant="danger" onClick={doCommit} disabled={busy}>
              {busy ? "提交中，正在生成报告…" : "确认提交"}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-soft">
          将提交 <strong className="text-ink">{pending}</strong> 份作答进入分析，作答与标注提交后锁定。
        </p>
        <div className="flex items-center gap-2">
          <Badge tone="warn">不可撤销</Badge>
          <span className="text-xs text-ink-faint">如需更正请以补录考试处理</span>
        </div>
      </Modal>
    </div>
  );
}
