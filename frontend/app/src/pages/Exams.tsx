import { ArrowRight, Plus } from "@phosphor-icons/react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Page, PageHeader, Skeleton, StatusDot } from "../components/ui";
import { StaggerItem, StaggerList } from "../components/motion";
import { ReportMarkdown } from "../components/Markdown";
import { classDiagnosisSheet, listClasses, listExams } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { ACCENTS } from "../lib/theme";
import type { ExamSummary } from "../lib/types";

const STAGE_LABELS = ["建卷", "审核", "采集", "提交", "概况"];

/** 由考试摘要推算当前所处阶段与下一动作。 */
function examStage(e: ExamSummary): { current: number; label: string; to: string } {
  const committed = (e.response_counts["已提交"] ?? 0) > 0;
  if (e.unreviewed_tags > 0) return { current: 2, label: "去审核", to: "review" };
  if (!committed) return { current: 3, label: "去采集", to: "collect" };
  return { current: 5, label: "看概况", to: "report" };
}

/** 各阶完成态。 */
function stageDone(e: ExamSummary): Record<number, boolean> {
  const committed = (e.response_counts["已提交"] ?? 0) > 0;
  const reviewed = e.unreviewed_tags === 0;
  return { 1: true, 2: reviewed, 3: committed, 4: committed, 5: committed };
}

/**
 * 考试模块双 tab（diagnosis-sheet-redesign §1.2/F3）：
 * 「考试」= 流水线卡片列表；「班级诊断单」= 滚动的班级持续状态
 * （现状 + 改进意见 + 行动明细[干预闭环后接] + 存档网格）。
 */
export default function Exams() {
  const { classId } = useParams();
  const cid = Number(classId);
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "diagnosis" ? "diagnosis" : "exams";
  const { data, loading, error, reload } = useAsync(() => listExams(cid), [cid]);
  const classes = useAsync(() => listClasses(), []);

  return (
    <Page accent={ACCENTS.exam}>
      <PageHeader
        title="考试"
        desc="每场考试是一条流水线：建卷 → 审核 → 采集 → 提交 → 概况"
        actions={
          <span className="flex items-center gap-2">
            <select
              name="class-switch"
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

      {/* 双 tab：考试 | 班级诊断单 */}
      <div className="mb-5 inline-flex rounded-full border border-line bg-surface p-1" role="tablist" aria-label="考试模块视图">
        {(
          [
            ["exams", "考试"],
            ["diagnosis", "班级诊断单"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setParams(key === "exams" ? {} : { tab: key })}
            className={`rounded-full px-4 py-1.5 text-sm transition-colors ${
              tab === key ? "bg-accent font-semibold text-white" : "text-ink-soft hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "exams" ? (
        <ExamList data={data} loading={loading} error={error} reload={reload} cid={cid} />
      ) : (
        <ClassDiagnosisTab cid={cid} />
      )}
    </Page>
  );
}

/** tab 1：流水线卡片列表（原 Exams 列表体）。 */
function ExamList({
  data,
  loading,
  error,
  reload,
  cid,
}: {
  data: { exams: ExamSummary[] } | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
  cid: number;
}) {
  return (
    <>
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
    </>
  );
}

/** tab 2：班级诊断单——滚动状态（§1.2 五区块；行动/闭环区块待 intervention-loop 接入）。 */
function ClassDiagnosisTab({ cid }: { cid: number }) {
  const sheet = useAsync(() => classDiagnosisSheet(cid), [cid]);
  const s = sheet.data;

  if (sheet.loading) return <Skeleton rows={6} />;
  if (sheet.error) return <ErrorState message={sheet.error} onRetry={sheet.reload} />;
  if (!s) return null;

  const st = s.status;

  return (
    <div className="space-y-5">
      {/* 区块一：班级现状（滚动统计） */}
      <Card className="p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm font-semibold">班级现状</p>
          <p className="text-xs text-ink-faint tabular-nums">
            数据截至 {st.data_as_of ?? "—"} · 全班 {st.student_count} 人 · 已考 {st.exam_count} 场
          </p>
        </div>
        <div className="mt-3 grid gap-4 sm:grid-cols-3">
          <div>
            <p className="text-xs text-ink-faint">待加强知识点</p>
            <p className="text-xl font-semibold tabular-nums">{st.weak_kp_total}</p>
          </div>
          <div className="sm:col-span-2">
            <p className="text-xs text-ink-faint">班级共性待加强 Top 3</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {st.common_weak.length === 0 && (
                <span className="text-sm text-ink-soft">暂无达到共性阈值的点</span>
              )}
              {st.common_weak.slice(0, 3).map((d) => (
                <Badge key={d.kp} tone="warn">
                  {d.kp} · {d.weak_share_pct}%
                </Badge>
              ))}
            </div>
            {st.trend.prev_exam !== null && (st.trend.entered.length > 0 || st.trend.exited.length > 0) && (
              <p className="mt-2 text-xs text-ink-soft">
                较「{st.trend.prev_exam}」：
                {st.trend.entered.length > 0 && <>新进入共性榜 {st.trend.entered.join("、")}</>}
                {st.trend.entered.length > 0 && st.trend.exited.length > 0 && <>；</>}
                {st.trend.exited.length > 0 && <>退出 {st.trend.exited.join("、")}</>}
              </p>
            )}
          </div>
        </div>
      </Card>

      {/* 区块二：班级改进意见（最新一份，LLM/模板） */}
      {s.improvement_advice ? (
        <Card className="p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-sm font-semibold">班级改进意见</p>
            <p className="flex items-center gap-2 text-xs text-ink-faint">
              {s.improvement_advice.writer ? (
                <Badge tone="accent">AI 起草</Badge>
              ) : null}
              {s.improvement_advice.generated_at?.slice(0, 10)}
            </p>
          </div>
          <div className="prose-sm mt-3 max-w-none [&_h1]:hidden">
            <ReportMarkdown content={s.improvement_advice.markdown} />
          </div>
        </Card>
      ) : (
        <Card>
          <EmptyState
            title="暂无改进意见"
            hint="提交一场考试后，系统会基于班级数据自动生成改进意见。"
          />
        </Card>
      )}

      {/* 区块三/四：行动明细 + 闭环条 —— intervention-loop-design 落地后接入（D11 压后） */}

      {/* 区块五：往期考试报告存档 */}
      {s.past_exams.length > 0 && (
        <Card className="p-5">
          <p className="text-sm font-semibold">往期考试报告</p>
          <StaggerList className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {s.past_exams.map((e) => (
              <StaggerItem key={e.exam_id}>
                <Link to={`/c/${cid}/quality?exam=${e.exam_id}`} className="block">
                  <Card interactive className="p-4">
                    <p className="truncate text-sm font-medium text-ink">{e.name}</p>
                    <p className="mt-0.5 text-xs text-ink-faint tabular-nums">
                      {e.exam_date} · {e.type}
                    </p>
                  </Card>
                </Link>
              </StaggerItem>
            ))}
          </StaggerList>
        </Card>
      )}
    </div>
  );
}
