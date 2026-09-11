import { Check, PaperPlaneTilt, X } from "@phosphor-icons/react";
import { useState } from "react";
import {
  confirmIntervention,
  skipIntervention,
} from "../lib/api";
import {
  effectLabel,
  interventionStatusLabel,
  kindLabel,
  loopStateLabel,
} from "../lib/labels";
import type { InterventionRow, InterventionSummary } from "../lib/types";
import { Badge, Button, EmptyState } from "./ui";

/**
 * 干预闭环共享组件（intervention-loop-design §6 视觉规格，三张单架构收敛后
 * 唯一全量版面 = 班级诊断单行动明细区块；工作台只有链接卡）。
 */

/** 效果状态 chip：不只靠颜色区分（内含文字），复用既有 Badge tone。 */
export function EffectChip({ effect }: { effect: string }) {
  const tone =
    effect === "improved"
      ? "accent"
      : effect === "declined"
        ? "danger"
        : "neutral";
  return <Badge tone={tone}>{effectLabel(effect) || effect}</Badge>;
}

/** 状态 chip：建议中=warn · 已执行=accent · 已跳过=neutral。 */
export function StatusChip({ status }: { status: string }) {
  const tone =
    status === "suggested" ? "warn" : status === "done" ? "accent" : "neutral";
  return <Badge tone={tone}>{interventionStatusLabel(status)}</Badge>;
}

/** 进度生命周期 chip（闭环一期 P1 + study-loop-design）：自报待检验/待复测=warn ·
 * 已闭合=accent · 未闭合=danger · 其余 neutral。只认折叠函数产出的封闭状态集。 */
export function LoopStateChip({ state }: { state: string | null | undefined }) {
  if (!state) return null;
  const tone =
    state === "已闭合"
      ? "accent"
      : state === "未闭合"
        ? "danger"
        : state === "待复测" || state === "持平复评" || state === "自报待检验"
          ? "warn"
          : "neutral";
  return <Badge tone={tone}>{loopStateLabel(state)}</Badge>;
}

/** scope 标签：全班/小组(N 人)/个体。 */
function ScopeTag({ row }: { row: InterventionRow }) {
  if (row.scope === "class") return <span className="text-ink-faint">全班</span>;
  if (row.scope === "group")
    return (
      <span className="text-ink-faint">
        小组（{row.group_size ?? "?"} 人同根源）
      </span>
    );
  return <span className="text-ink-faint">{row.alias ?? `学生 #${row.student_id}`}</span>;
}

/**
 * 行动明细面板（三层杠杆序由后端保证）：行内一键派发/确认/跳过。
 * rows 是后端裁剪好的**待办队列**（≤10 条：仅挂起、覆盖抑制、小组按组
 * 一行）——这里是纯渲染，不自行分页/展开；完整事实走学生页干预记录。
 * 小组代表行按组批量落事实（操作层一次、事实层逐行）。
 * 集体行（班级/小组）额外提供「派发AI方案」：一键把学习安排转为学生门户
 * 自学（study-loop-design 混合派发），学生各自按归因生成方案、各自自报。
 */
const DISPATCH_NOTE = "已派发AI学习方案（学生门户自学）";

export function ActionPlanPanel({
  rows,
  onChanged,
  emptyHint = "暂无行动建议——提交考试后系统会基于班级数据生成。",
}: {
  rows: InterventionRow[];
  onChanged?: () => void;
  emptyHint?: string;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = async (
    row: InterventionRow,
    op: "confirm" | "skip" | "dispatch"
  ) => {
    setBusyId(row.id);
    setError(null);
    try {
      const batch = row.scope === "group";
      if (op === "skip") await skipIntervention(row.id, batch);
      else if (op === "dispatch")
        await confirmIntervention(row.id, batch, DISPATCH_NOTE);
      else await confirmIntervention(row.id, batch);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  if (rows.length === 0) {
    return <EmptyState title="暂无行动建议" hint={emptyHint} />;
  }

  return (
    <div className="space-y-2">
      {error && (
        <p className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger" role="alert">
          {error}
        </p>
      )}
      {rows.map((row) => (
        <div
          key={row.id}
          className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-line bg-surface px-3.5 py-2.5"
        >
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-ink">{row.kp_name}</span>
              <Badge tone="neutral">{kindLabel(row.kind)}</Badge>
              <StatusChip status={row.status} />
              {/* 已建议 与状态 chip 同义，不重复渲染 */}
              {row.loop_state && row.loop_state !== "已建议" && (
                <LoopStateChip state={row.loop_state} />
              )}
            </p>
            <p className="mt-0.5 text-xs text-ink-faint">
              <ScopeTag row={row} />
              {row.note ? `　·　${row.note}` : ""}
              {row.done_at ? `　·　执行于 ${row.done_at.slice(0, 10)}` : ""}
            </p>
          </div>
          {row.status === "suggested" && (
            <div className="flex shrink-0 flex-wrap items-center gap-1.5">
              <Button
                variant="secondary"
                disabled={busyId === row.id}
                onClick={() => act(row, "skip")}
                aria-label={`跳过「${row.kp_name}」的${kindLabel(row.kind)}建议`}
                className="px-2.5 py-1.5 text-xs"
              >
                <X size={13} />
                跳过
              </Button>
              <Button
                variant="secondary"
                disabled={busyId === row.id}
                onClick={() => act(row, "confirm")}
                aria-label={`确认已线下执行「${row.kp_name}」的${kindLabel(row.kind)}建议`}
                className="px-2.5 py-1.5 text-xs"
              >
                <Check size={13} />
                线下已讲
              </Button>
              {(row.scope === "class" || row.scope === "group") && (
                <Button
                  disabled={busyId === row.id}
                  onClick={() => act(row, "dispatch")}
                  aria-label={`派发「${row.kp_name}」的AI学习方案，学生门户自学`}
                  className="px-2.5 py-1.5 text-xs"
                >
                  <PaperPlaneTilt size={13} />
                  派发AI方案
                </Button>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** 闭环摘要条：待确认 N · 采纳率 · 干预提升率 · 待验证 M（措辞成长框架）。 */
export function InterventionSummaryStrip({
  summary,
}: {
  summary: InterventionSummary | null;
}) {
  if (!summary || summary.total === 0) return null;
  const pct = (v: number | null) =>
    v === null ? "—" : `${Math.round(v * 100)}%`;
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-xl bg-accent/8 px-4 py-2.5 text-xs text-ink-soft">
      <span>
        待确认{" "}
        <b className="tabular-nums text-ink">
          {summary.by_status.suggested}
        </b>{" "}
        条
      </span>
      <span>
        采纳率 <b className="tabular-nums text-ink">{pct(summary.adoption_rate)}</b>
      </span>
      <span>
        干预提升率{" "}
        <b className="tabular-nums text-accent-deep">
          {summary.intervention_lift_rate === null
            ? "待考试验证"
            : pct(summary.intervention_lift_rate)}
        </b>
      </span>
      <span>
        待验证 <b className="tabular-nums text-ink">{summary.effects.awaiting_retest}</b> 项
      </span>
      {summary.self_reported != null && summary.self_reported > 0 && (
        <span>
          自报待检验{" "}
          <b className="tabular-nums text-ink">{summary.self_reported}</b> 项
        </span>
      )}
    </div>
  );
}
