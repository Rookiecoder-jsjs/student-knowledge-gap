import { Check, X } from "@phosphor-icons/react";
import { useState } from "react";
import {
  confirmIntervention,
  skipIntervention,
} from "../lib/api";
import {
  effectLabel,
  interventionStatusLabel,
  kindLabel,
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
 * 行动明细面板（三层杠杆序由后端保证）：行内一键确认/跳过。
 * 密度 compact 用于嵌入卡片；确认/跳过即时生效并回调刷新。
 */
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

  const act = async (id: number, op: "confirm" | "skip") => {
    setBusyId(id);
    setError(null);
    try {
      if (op === "confirm") await confirmIntervention(id);
      else await skipIntervention(id);
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
            </p>
            <p className="mt-0.5 text-xs text-ink-faint">
              <ScopeTag row={row} />
              {row.note ? `　·　${row.note}` : ""}
              {row.done_at ? `　·　执行于 ${row.done_at.slice(0, 10)}` : ""}
            </p>
          </div>
          {row.status === "suggested" && (
            <div className="flex shrink-0 items-center gap-1.5">
              <Button
                variant="secondary"
                disabled={busyId === row.id}
                onClick={() => act(row.id, "skip")}
                aria-label={`跳过「${row.kp_name}」的${kindLabel(row.kind)}建议`}
                className="px-2.5 py-1.5 text-xs"
              >
                <X size={13} />
                跳过
              </Button>
              <Button
                disabled={busyId === row.id}
                onClick={() => act(row.id, "confirm")}
                aria-label={`确认已执行「${row.kp_name}」的${kindLabel(row.kind)}建议`}
                className="px-2.5 py-1.5 text-xs"
              >
                <Check size={13} />
                已执行
              </Button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** 闭环摘要条：待确认 N · 采纳率 · 干预提升率 · 待复测 M（措辞成长框架）。 */
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
            ? "待复测验证"
            : pct(summary.intervention_lift_rate)}
        </b>
      </span>
      <span>
        等待复测 <b className="tabular-nums text-ink">{summary.effects.awaiting_retest}</b> 项
      </span>
    </div>
  );
}
