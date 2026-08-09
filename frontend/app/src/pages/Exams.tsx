import { ArrowRight, Plus } from "@phosphor-icons/react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, Skeleton, StatusDot } from "../components/ui";
import { StaggerItem, StaggerList } from "../components/motion";
import { listClasses, listExams } from "../lib/api";
import { useAsync } from "../lib/hooks";
import type { ExamSummary } from "../lib/types";

const STAGE_LABELS = ["建卷", "审核", "采集", "提交", "报告"];

/** 由考试摘要推算当前所处阶段与下一动作。 */
function examStage(e: ExamSummary): { current: number; label: string; to: string } {
  const committed = (e.response_counts["已提交"] ?? 0) > 0;
  if (e.unreviewed_tags > 0) return { current: 2, label: "去审核", to: "review" };
  if (!committed) return { current: 3, label: "去采集", to: "collect" };
  return { current: 5, label: "查看报告", to: "report" };
}

/** 各阶完成态。 */
function stageDone(e: ExamSummary): Record<number, boolean> {
  const committed = (e.response_counts["已提交"] ?? 0) > 0;
  const reviewed = e.unreviewed_tags === 0;
  return { 1: true, 2: reviewed, 3: committed, 4: committed, 5: committed };
}

/** 考试列表：流水线卡片，每场考试显示阶段进度 + 下一动作。 */
export default function Exams() {
  const { classId } = useParams();
  const cid = Number(classId);
  const nav = useNavigate();
  const { data, loading, error, reload } = useAsync(() => listExams(cid), [cid]);
  const classes = useAsync(() => listClasses(), []);

  return (
    <div>
      <PageHeader
        title="考试"
        desc="每场考试是一条流水线：建卷 → 审核 → 采集 → 提交 → 报告"
        actions={
          <span className="flex items-center gap-2">
            <select
              value={cid}
              onChange={(e) => nav(`/c/${e.target.value}/exams`)}
              className="rounded-lg border border-line bg-surface px-3 py-2 text-sm transition-colors focus:border-accent"
              aria-label="切换班级"
            >
              {(classes.data?.classes ?? []).map((c) => (
                <option key={c.class_id} value={c.class_id}>
                  {c.name}
                </option>
              ))}
            </select>
            <Link to={`/c/${cid}/exams/new`}>
              <Button>
                <Plus size={15} />
                新建考试
              </Button>
            </Link>
          </span>
        }
      />

      {loading && <Skeleton rows={4} />}
      {error && <ErrorState message={error} onRetry={reload} />}

      {data && data.exams.length === 0 && (
        <Card>
          <EmptyState
            title="暂无考试"
            hint="点击右上角「新建考试」，推荐拍照上传空白试卷，AI 会自动解析题目并初步标注知识点。"
          />
        </Card>
      )}

      {data && data.exams.length > 0 && (
        <StaggerList className="grid gap-4 sm:grid-cols-2">
          {data.exams.map((e) => {
            const stage = examStage(e);
            const done = stageDone(e);
            const submitted = e.response_counts["已提交"] ?? 0;
            const pending = e.response_counts["待审核"] ?? 0;
            const uncollected = e.response_counts["未采集"] ?? 0;
            return (
              <StaggerItem key={e.exam_id}>
                <Link to={`/c/${cid}/exams/${e.exam_id}/${stage.to}`} className="block">
                  <Card interactive className="h-full p-5">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-ink">{e.name}</p>
                        <p className="mt-0.5 text-xs text-ink-faint tabular-nums">
                          {e.exam_date} · {e.type} · {e.question_count} 题
                        </p>
                      </div>
                      <Badge tone={stage.current === 5 ? "accent" : "warn"}>{stage.label}</Badge>
                    </div>

                    {/* 阶段进度点 */}
                    <div className="mt-4 flex items-center gap-1.5">
                      {STAGE_LABELS.map((label, i) => {
                        const n = i + 1;
                        const isDone = done[n];
                        const isCurrent = n === stage.current;
                        return (
                          <div key={label} className="flex items-center gap-1.5">
                            <span
                              className="flex items-center gap-1 text-[11px]"
                              title={`第 ${n} 阶·${label}`}
                            >
                              <StatusDot state={isDone ? "done" : isCurrent ? "active" : "todo"} />
                              <span
                                className={
                                  isCurrent
                                    ? "font-semibold text-accent-deep"
                                    : isDone
                                      ? "text-ink-soft"
                                      : "text-ink-faint"
                                }
                              >
                                {label}
                              </span>
                            </span>
                            {i < STAGE_LABELS.length - 1 && (
                              <span
                                className={`h-px w-3 ${isDone ? "bg-accent/40" : "bg-line"}`}
                                aria-hidden
                              />
                            )}
                          </div>
                        );
                      })}
                    </div>

                    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-3 text-xs text-ink-faint tabular-nums">
                      <span>已提交 {submitted}</span>
                      {pending > 0 && <span className="text-warn">待审核 {pending}</span>}
                      {uncollected > 0 && <span>未采集 {uncollected}</span>}
                      {e.unreviewed_tags > 0 && (
                        <span className="text-warn">{e.unreviewed_tags} 标注待审</span>
                      )}
                      <ArrowRight size={13} className="ml-auto text-ink-faint" />
                    </div>
                  </Card>
                </Link>
              </StaggerItem>
            );
          })}
        </StaggerList>
      )}
    </div>
  );
}
