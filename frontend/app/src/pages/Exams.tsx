import { ArrowRight, Plus } from "@phosphor-icons/react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ActionPlanPanel,
  InterventionSummaryStrip,
} from "../components/ActionPlan";
import { Badge, Button, Card, EmptyState, ErrorState, Page, PageHeader, Pagination, Select, Skeleton, StatusDot, Tabs } from "../components/ui";
import { StaggerItem, StaggerList } from "../components/motion";
import { ReportMarkdown } from "../components/Markdown";
import { classDiagnosisSheet, classKnowledgeGraph, listClasses, listExams } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { setBackTarget } from "../lib/portal";
import { ACCENTS } from "../lib/theme";
import type { ExamSummary, KnowledgeGraphNode } from "../lib/types";

const KnowledgeGraph2D = lazy(() => import("../components/graph/KnowledgeGraph2D"));

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
  const PAGE_SIZE = 12;
  const [page, setPage] = useState(1);
  const { data, loading, error, reload } = useAsync(
    () => listExams(cid, { offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE }),
    [cid, page],
  );
  useEffect(() => setPage(1), [cid]);
  useEffect(() => {
    if (data && data.exams.length === 0 && data.total && page > 1) setPage((p) => p - 1);
  }, [data, page]);
  const classes = useAsync(() => listClasses(), []);

  return (
    <Page accent={ACCENTS.exam}>
      <PageHeader
        title="考试"
        desc="每场考试是一条流水线：建卷 → 审核 → 采集 → 提交 → 概况"
        actions={
          <span className="flex items-center gap-2">
            <Select
              name="class-switch"
              value={cid}
              onChange={(e) => nav(`/c/${e.target.value}/exams`)}
              className="w-36"
              aria-label="切换班级"
            >
              {(classes.data?.classes ?? []).map((c) => (
                <option key={c.class_id} value={c.class_id}>
                  {c.name}
                </option>
              ))}
            </Select>
            <Link to={`/c/${cid}/exams/new`}>
              <Button>
                <Plus size={15} />
                新建考试
              </Button>
            </Link>
          </span>
        }
      />

      {/* 双 tab：考试 | 班级诊断单（Tabs 原语，design-style §4 禁页内手写 tab） */}
      <Tabs
        ariaLabel="考试模块视图"
        layoutId="exams-view-tab"
        className="mb-5"
        tabs={[
          { key: "exams", label: "考试" },
          { key: "diagnosis", label: "班级诊断单" },
        ] as const}
        value={tab}
        onChange={(k) => setParams(k === "exams" ? {} : { tab: k })}
      />

      {tab === "exams" ? (
        <ExamList data={data} loading={loading} error={error} reload={reload} cid={cid} page={page} pageSize={PAGE_SIZE} onPageChange={setPage} />
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
  page,
  pageSize,
  onPageChange,
}: {
  data: { exams: ExamSummary[]; total?: number; has_more?: boolean } | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
  cid: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
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
                  <Card interactive className="h-full p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="flex flex-wrap items-center gap-2 truncate text-sm font-semibold text-ink">
                          {e.name}
                          {e.subject && <Badge tone="neutral">{e.subject}</Badge>}
                        </p>
                        <p className="mt-0.5 text-xs text-ink-faint tabular-nums">
                          {e.exam_date} · {e.type} · {e.question_count} 题
                        </p>
                      </div>
                      <Badge tone={stage.current === 5 ? "accent" : "warn"}>{stage.label}</Badge>
                    </div>

                    {/* 阶段进度点 */}
                    <div className="mt-4 flex min-w-0 items-center gap-1.5 overflow-x-auto pb-1">
                      {STAGE_LABELS.map((label, i) => {
                        const n = i + 1;
                        const isDone = done[n];
                        const isCurrent = n === stage.current;
                        return (
                          <div key={label} className="flex min-w-max items-center gap-1.5">
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
      {data && data.exams.length > 0 && (
        <Pagination
          className="mt-5"
          page={page}
          pageSize={pageSize}
          total={data.total ?? data.exams.length}
          hasMore={data.has_more}
          onPageChange={onPageChange}
          disabled={loading}
        />
      )}
    </>
  );
}

/** tab 2：班级诊断单——滚动状态（§1.2 五区块；行动/闭环区块待 intervention-loop 接入）。 */
function ClassDiagnosisTab({ cid }: { cid: number }) {
  const sheet = useAsync(() => classDiagnosisSheet(cid), [cid]);
  const graph = useAsync(() => classKnowledgeGraph(cid), [cid]);
  const [graphFilter, setGraphFilter] = useState<"all" | "weak" | "prerequisite" | "confusable">("all");
  const [selectedGraphNode, setSelectedGraphNode] = useState<number | null>(null);
  const s = sheet.data;
  const graphView = useMemo(() => {
    const payload = graph.data;
    if (!payload) return null;
    if (graphFilter === "all") return payload;
    if (graphFilter === "weak") {
      const nodes = payload.nodes.filter((node) => (node.weak_share ?? 0) > 0 || (node.mastery ?? 1) < 0.6);
      const ids = new Set(nodes.map((node) => node.id));
      return { ...payload, nodes, edges: payload.edges.filter((edge) => ids.has(edge.from) && ids.has(edge.to)) };
    }
    const edges = payload.edges.filter((edge) => edge.type === graphFilter);
    const ids = new Set(edges.flatMap((edge) => [edge.from, edge.to]));
    return { ...payload, nodes: payload.nodes.filter((node) => ids.has(node.id)), edges };
  }, [graph.data, graphFilter]);
  const selectedNode = graph.data?.nodes.find((node) => node.id === selectedGraphNode) ?? null;

  if (sheet.loading) return <Skeleton rows={6} />;
  if (sheet.error) return <ErrorState message={sheet.error} onRetry={sheet.reload} />;
  if (!s) return null;

  const st = s.status;

  return (
    <div className="space-y-5">
      {/* 区块一：班级现状（滚动统计） */}
      <Card className="p-4">
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

      {/* 区块一补充：知识结构 2D 探索图，不替换诊断正文。 */}
      <section>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-semibold">知识结构</p>
            <p className="mt-0.5 text-xs text-ink-faint">按班级掌握度定位共性薄弱点与前置关系</p>
          </div>
          <Select
            name="knowledge-graph-filter"
            size="sm"
            value={graphFilter}
            onChange={(event) => {
              setGraphFilter(event.target.value as typeof graphFilter);
              setSelectedGraphNode(null);
            }}
            className="w-32"
            aria-label="知识结构筛选"
          >
            <option value="all">全部关系</option>
            <option value="weak">只看薄弱点</option>
            <option value="prerequisite">只看前置</option>
            <option value="confusable">只看易混</option>
          </Select>
        </div>
        {graph.loading && <Skeleton rows={5} />}
        {graph.error && <ErrorState message={graph.error} onRetry={graph.reload} />}
        {graphView && graphView.nodes.length > 0 && (
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_16rem]">
            <Suspense fallback={<Card className="p-4"><Skeleton rows={5} /></Card>}>
              <KnowledgeGraph2D
                nodes={graphView.nodes}
                edges={graphView.edges}
                selectedId={selectedGraphNode}
                onSelect={(node) => setSelectedGraphNode(node?.id ?? null)}
              />
            </Suspense>
            <GraphNodeInspector node={selectedNode} />
          </div>
        )}
        {graphView && graphView.nodes.length === 0 && (
          <Card className="p-5 text-sm text-ink-faint">当前筛选没有可展示的知识点。</Card>
        )}
      </section>

      {/* 区块二：班级改进意见（最新一份，LLM/模板） */}
      {s.improvement_advice ? (
        <Card className="p-4">
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

      {/* 区块三：行动明细（待办队列——后端折叠排序，仅挂起建议 ≤10 条；
          行内一键确认/跳过，小组代表行按组批量落事实） */}
      <Card className="p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm font-semibold">行动明细</p>
          <p className="text-xs text-ink-faint">
            {s.actions.pending_confirm > 0
              ? s.actions.pending_confirm > s.actions.rows.length
                ? `待确认 ${s.actions.pending_confirm} 条 · 系统已按杠杆排序，先做这 ${s.actions.rows.length} 条`
                : `${s.actions.pending_confirm} 条待确认`
              : "暂无待确认建议"}
          </p>
        </div>
        <div className="mt-3">
          <ActionPlanPanel rows={s.actions.rows} onChanged={sheet.reload} />
        </div>
      </Card>

      {/* 区块四：闭环条（采纳率 · 干预提升率 · 待验证）——定向复测入口已随复测小卷软退役移除 */}
      <InterventionSummaryStrip summary={s.intervention_summary} />

      {/* 区块五：往期考试报告存档 */}
      {s.past_exams.length > 0 && (
        <Card className="p-4">
          <p className="text-sm font-semibold">往期考试报告</p>
          <StaggerList className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {s.past_exams.map((e) => (
              <StaggerItem key={e.exam_id}>
                <Link
                  to={`/c/${cid}/quality?exam=${e.exam_id}`}
                  onClick={() =>
                    setBackTarget(`/c/${cid}/quality`, `/c/${cid}/exams?tab=diagnosis`)
                  }
                  className="block"
                >
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

function GraphNodeInspector({ node }: { node: KnowledgeGraphNode | null }) {
  if (!node) {
    return (
      <Card className="h-fit p-4 text-sm text-ink-faint">
        点击图中的知识点查看班级指标。
      </Card>
    );
  }
  return (
    <Card className="h-fit p-4">
      <p className="text-sm font-semibold">{node.name}</p>
      <p className="mt-0.5 font-mono text-xs text-ink-faint">{node.code}</p>
      <dl className="mt-4 space-y-2.5 text-xs">
        <div className="flex justify-between gap-3"><dt className="text-ink-faint">章节</dt><dd className="text-right text-ink-soft">{node.chapter}</dd></div>
        <div className="flex justify-between gap-3"><dt className="text-ink-faint">班级平均掌握度</dt><dd className="font-semibold text-ink">{node.mastery == null ? "暂无" : `${Math.round(node.mastery * 100)}%`}</dd></div>
        <div className="flex justify-between gap-3"><dt className="text-ink-faint">薄弱占比</dt><dd className="font-semibold text-ink">{node.weak_share == null ? "暂无" : `${Math.round(node.weak_share * 100)}%`}</dd></div>
        <div className="flex justify-between gap-3"><dt className="text-ink-faint">有效学生数</dt><dd className="font-semibold text-ink">{node.student_count}</dd></div>
        <div className="flex justify-between gap-3"><dt className="text-ink-faint">证据数</dt><dd className="font-semibold text-ink">{node.evidence_count}</dd></div>
      </dl>
    </Card>
  );
}
