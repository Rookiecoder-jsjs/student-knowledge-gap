import { CaretDown, CaretRight, HandPalm } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Input, Page, PageHeader, SectionTitle, Skeleton } from "../components/ui";
import { StaggerItem, StaggerList } from "../components/motion";
import { ReportMarkdown } from "../components/Markdown";
import {
  diagnosisReport,
  getWeaknesses,
  listStudents,
  overrideAttribution,
  runAttributions,
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

/** 学生诊断单：报告正文 + 结构化薄弱卡片 + 归因假设（可否决）。 */
export default function Diagnosis() {
  const { classId, studentId } = useParams();
  const cid = Number(classId);
  const sid = Number(studentId);

  const students = useAsync(() => listStudents(cid), [cid]);
  // 右侧弱项面板与报告同 as_of：默认跟随报告快照日期；显式选日期时按所选现算
  const [asOf, setAsOf] = useState("");
  const [reportAsOf, setReportAsOf] = useState<string | null>(null);
  const weak = useAsync(
    () => getWeaknesses(sid, asOf || reportAsOf || undefined),
    [sid, asOf, reportAsOf]
  );
  const student = students.data?.students.find((s) => s.student_id === sid);

  const [attributions, setAttributions] = useState<AttributionView[] | null>(null);
  const [attrError, setAttrError] = useState<string | null>(null);
  const [narrative, setNarrative] = useState(false);
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
      const r = await diagnosisReport(sid, narrative, asOf || undefined);
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
  }, [sid, narrative, asOf]);

  return (
    <Page accent={ACCENTS.student}>
      <PageHeader
        title={student ? `${student.name_or_alias} 的诊断单` : "学生诊断单"}
        desc="先看进步，再看待加强项；每条结论可展开依据"
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-ink-soft">
              截至
              <Input
                type="date"
                value={asOf}
                onChange={(e) => setAsOf(e.target.value)}
              />
            </label>
            <Button
              variant={narrative ? "primary" : "secondary"}
              aria-pressed={narrative}
              onClick={() => setNarrative((n) => !n)}
            >
              {narrative ? "已附加 AI 解读" : "附加 AI 解读"}
            </Button>
          </div>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        {/* 左：诊断报告 */}
        <section>
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
        </section>

        {/* 右：结构化薄弱点与归因 */}
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
