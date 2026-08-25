import { CaretDown, CaretRight, HandPalm, Printer } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  ActionPlanPanel,
} from "../components/ActionPlan";
import { Badge, Button, Card, EmptyState, ErrorState, Input, Page, PageHeader, SectionTitle, Skeleton } from "../components/ui";
import { StaggerItem, StaggerList } from "../components/motion";
import { ReportMarkdown } from "../components/Markdown";
import {
  diagnosisReport,
  getWeaknesses,
  listInterventions,
  listStudents,
  overrideAttribution,
  runAttributions,
  studentActionPlan,
} from "../lib/api";
import { useAsync } from "../lib/hooks";
import { attrLabel, criterionLabel, trajLabel } from "../lib/labels";
import { ACCENTS } from "../lib/theme";
import type { AttributionView } from "../lib/types";

const ATTR_HINT: Record<string, string> = {
  前置缺陷: "基础没打牢，建议先补前置知识，再回看本题型",
  遗忘衰减: "学过但忘了，建议安排一次间隔复习",
  易混淆: "与相似知识点混了，建议对照辨析后针对性练习",
  数据不足: "依据不够，暂不判定，后续考试会继续观察",
};

/** 学生诊断单/改进单（intervention-loop §6）：?view=plan 切换；干预记录卡在右栏。 */
export default function Diagnosis() {
  const { classId, studentId } = useParams();
  const cid = Number(classId);
  const sid = Number(studentId);
  const [params, setParams] = useSearchParams();
  // 诊断单 | 改进单 切换（与 Quality ?exam= 同约定：query 参数不加新路由）
  const view = params.get("view") === "plan" ? "plan" : "diagnosis";

  const students = useAsync(() => listStudents(cid), [cid]);
  // 右侧弱项面板与报告同 as_of：默认跟随报告快照日期；显式选日期时按所选现算
  const [asOf, setAsOf] = useState("");
  const [reportAsOf, setReportAsOf] = useState<string | null>(null);
  const weak = useAsync(
    () => getWeaknesses(sid, asOf || reportAsOf || undefined),
    [sid, asOf, reportAsOf]
  );
  const student = students.data?.students.find((s) => s.student_id === sid);
  // 该生的干预记录（个体视角唯一版面：状态 + 效果 chip，链接回班级诊断单）
  const interventions = useAsync(
    () => listInterventions({ student_id: sid }).catch(() => ({ total: 0, items: [] })),
    [sid]
  );
  // 改进单视图数据
  const plan = useAsync(
    () => (view === "plan" ? studentActionPlan(sid) : Promise.resolve(null)),
    [view, sid]
  );

  const [attributions, setAttributions] = useState<AttributionView[] | null>(null);
  const [attrError, setAttrError] = useState<string | null>(null);
  // diagnosis-sheet-redesign F6：正文已由生成层（LLM/模板保底）落库，AI 解读 toggle 删除。
  // 后端 narrative 参数保留兼容，前端不再暴露。
  const [report, setReport] = useState<string | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

  const loadAttributions = () => {
    runAttributions(sid)
      // 「数据不足」不是方向性假设，报告正文已单列，此处不重复展示
      .then((r) => setAttributions(r.attributions.filter((a) => a.type !== "数据不足")))
      .catch((e: Error) => setAttrError(e.message));
  };
  useEffect(loadAttributions, [sid]);

  const generate = async () => {
    setReportLoading(true);
    setReportError(null);
    try {
      const r = await diagnosisReport(sid, false, asOf || undefined);
      setReport(r.markdown);
      setReportAsOf(r.as_of ?? null);
    } catch (e) {
      setReportError((e as Error).message);
    } finally {
      setReportLoading(false);
    }
  };

  useEffect(() => {
    generate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, asOf]);

  return (
    <Page accent={ACCENTS.student}>
      <PageHeader
        title={
          student
            ? `${student.name_or_alias} 的${view === "plan" ? "改进单" : "诊断单"}`
            : view === "plan"
              ? "学生改进单"
              : "学生诊断单"
        }
        desc={view === "plan" ? "给学生本人的行动卡，可打印后转发" : "先看进步，再看待加强项；每条结论可展开依据"}
        actions={
          <span className="flex items-center gap-3">
            {/* 诊断单 | 改进单 切换 */}
            <span className="inline-flex rounded-full border border-line bg-surface p-0.5" role="tablist" aria-label="学生报告视图">
              {(
                [
                  ["diagnosis", "诊断单"],
                  ["plan", "改进单"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  role="tab"
                  aria-selected={view === key}
                  onClick={() => setParams(key === "diagnosis" ? {} : { view: key })}
                  className={`rounded-full px-3 py-1 text-xs transition-colors ${
                    view === key ? "bg-accent font-semibold text-white" : "text-ink-soft hover:text-ink"
                  }`}
                >
                  {label}
                </button>
              ))}
            </span>
            <label className="flex items-center gap-2 text-sm text-ink-soft">
              截至
              <Input
                type="date"
                value={asOf}
                onChange={(e) => setAsOf(e.target.value)}
              />
            </label>
          </span>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        {/* 左：诊断单 / 改进单 正文 */}
        <section>
          {view === "plan" ? (
            <>
              {plan.loading && <Skeleton rows={8} />}
              {plan.error && <ErrorState message={plan.error} onRetry={plan.reload} />}
              {plan.data && (
                <>
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-xs text-ink-faint">
                      {plan.data.as_of ? `评估截至 ${plan.data.as_of}` : null}
                      {plan.data.writer ? " · AI 起草（教师可修改后转发）" : " · 模板版"}
                    </p>
                    <Button variant="secondary" onClick={() => window.print()} className="px-2.5 py-1.5 text-xs">
                      <Printer size={13} />
                      打印
                    </Button>
                  </div>
                  <Card className="p-7">
                    <ReportMarkdown content={plan.data.markdown} />
                  </Card>
                </>
              )}
            </>
          ) : (
            <>
              {reportLoading && <Skeleton rows={8} />}
              {reportError && <ErrorState message={reportError} onRetry={generate} />}
              {report && !reportLoading && (
                <>
                  {reportAsOf && (
                    <p className="mb-2 text-xs text-ink-faint">
                      诊断数据截至 {reportAsOf}
                      {!asOf && "（最近一场考试）"}
                    </p>
                  )}
                  <Card className="p-7">
                    <ReportMarkdown content={report} />
                  </Card>
                </>
              )}
            </>
          )}
        </section>

        {/* 右：结构化薄弱点与归因（诊断单视图） / 干预记录（两视图共用） */}
        <section className="space-y-6">
          <div>
            <SectionTitle count={weak.data?.weak.length ?? 0}>待加强知识点</SectionTitle>
            {weak.loading && <Skeleton rows={3} />}
            {weak.error && <ErrorState message={weak.error} onRetry={weak.reload} />}
            {weak.data && weak.data.weak.length === 0 && (
              <Card>
                <EmptyState
                  title="暂无判定为待加强的知识点"
                  hint={`另有 ${weak.data.gates["未学到"] ?? 0} 项未学到、${weak.data.gates["数据不足"] ?? 0} 项数据不足，均不计入待加强。`}
                />
              </Card>
            )}
            {weak.data && weak.data.weak.length > 0 && (
              <StaggerList className="space-y-3">
                {weak.data.weak.map((w) => (
                  <StaggerItem key={w.code}>
                    <Card interactive className="p-4">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-semibold">
                        {w.name}
                        <span className="ml-2 font-mono text-xs font-normal text-ink-faint">
                          {w.code}
                        </span>
                      </p>
                      <span className="text-sm font-semibold text-danger">
                        {w.mastery !== null ? `${Math.round(w.mastery * 100)}%` : "-"}
                      </span>
                    </div>
                    <p className="mt-1.5 flex flex-wrap gap-1.5 text-xs">
                      <Badge>判定依据：{criterionLabel(w.criterion)}</Badge>
                      <Badge>依据 {w.evidence_count} 题</Badge>
                      <Badge>变化趋势 {trajLabel(w.trajectory)}</Badge>
                      {w.class_common && <Badge tone="warn">班级共性</Badge>}
                      {w.stale && <Badge tone="warn">可能已变化</Badge>}
                    </p>
                    </Card>
                  </StaggerItem>
                ))}
                <p className="text-xs text-ink-faint">
                  未学到 {weak.data.gates["未学到"] ?? 0} 项 · 数据不足{" "}
                  {weak.data.gates["数据不足"] ?? 0} 项（均不计入待加强）
                </p>
              </StaggerList>
            )}
          </div>

          <div>
            <SectionTitle count={attributions?.length ?? 0}>可能的原因</SectionTitle>
            {attributions === null && !attrError && <Skeleton rows={2} />}
            {attrError && <ErrorState message={attrError} onRetry={loadAttributions} />}
            {attributions && attributions.length === 0 && (
              <Card>
                <EmptyState title="暂无可能的原因" hint="依据足够的待加强点才会分析可能的原因。" />
              </Card>
            )}
            {attributions && attributions.length > 0 && (
              <StaggerList className="space-y-3">
                {attributions.map((a) => (
                  <StaggerItem key={a.id}>
                    <AttributionCard
                      attribution={a}
                      onOverridden={() => loadAttributions()}
                    />
                  </StaggerItem>
                ))}
              </StaggerList>
            )}
          </div>

          {/* 该生的干预记录卡（intervention-loop §6）：状态 + 效果，链接回班级诊断单 */}
          <div>
            <SectionTitle count={interventions.data?.items.length ?? 0}>该生的干预记录</SectionTitle>
            {(interventions.data?.items.length ?? 0) === 0 ? (
              <Card className="p-4">
                <p className="text-sm text-ink-faint">
                  暂无干预记录。行动建议由系统在每次考试提交后生成。
                </p>
              </Card>
            ) : (
              <ActionPlanPanel
                rows={interventions.data!.items}
                onChanged={interventions.reload}
                emptyHint=""
              />
            )}
            <p className="mt-2 text-xs text-ink-faint">
              全班行动方向见{" "}
              <a href={`/c/${cid}/exams?tab=diagnosis`} className="text-accent hover:text-accent-deep">
                班级诊断单 →
              </a>
            </p>
          </div>
        </section>
      </div>
    </Page>
  );
}

function AttributionCard({
  attribution: a,
  onOverridden,
}: {
  attribution: AttributionView;
  onOverridden: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [overriding, setOverriding] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const doOverride = async () => {
    setBusy(true);
    setErr(null);
    try {
      await overrideAttribution(a.id, note);
      setOverriding(false);
      onOverridden();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card interactive className="p-4">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 text-left"
        aria-expanded={open}
      >
        {open ? (
          <CaretDown size={14} className="shrink-0 text-ink-faint" />
        ) : (
          <CaretRight size={14} className="shrink-0 text-ink-faint" />
        )}
        <span className="text-sm font-semibold">{a.kp}</span>
        <Badge tone={a.type === "数据不足" ? "neutral" : "accent"}>{attrLabel(a.type)}</Badge>
        <span className="ml-auto text-xs text-ink-faint">
          把握 {Math.round(a.confidence * 100)}%
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-2 border-t border-line pt-3 text-sm">
          {a.root_kp && (
            <p className="text-ink-soft">
              可能根源：<b className="font-semibold text-ink">{a.root_kp}</b>
            </p>
          )}
          {a.prediction && <p className="text-ink-soft">验证方式：{a.prediction}</p>}
          <p className="text-xs text-ink-faint">{ATTR_HINT[a.type] ?? ""}</p>

          {!overriding ? (
            <Button variant="ghost" onClick={() => setOverriding(true)}>
              <HandPalm size={14} />
              不认可这条原因？
            </Button>
          ) : (
            <div className="space-y-2 rounded-lg bg-surface-2 p-3">
              <textarea
                rows={2}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="备注（可选）：如该生课前已自学、近期状态特殊…"
                className="w-full rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-xs transition-colors focus:border-accent"
              />
              {err && <p className="text-xs text-danger">{err}</p>}
              <div className="flex gap-2">
                <Button variant="danger" onClick={doOverride} disabled={busy}>
                  标记这条不成立
                </Button>
                <Button variant="ghost" onClick={() => setOverriding(false)}>
                  取消
                </Button>
              </div>
              <p className="text-xs text-ink-faint">标记后重新分析时不会再出现这条。</p>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
