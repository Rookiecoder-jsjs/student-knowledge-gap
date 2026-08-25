import { useState } from "react";
import { Card, Page, PageHeader, Skeleton } from "../components/ui";
import { ACCENTS } from "../lib/theme";
import { adminUsage, type UsageLedger } from "../lib/api";

/**
 * 管理端「本月消耗」（agent-product-design §5.9，Phase 2 批次D）。
 *
 * 学校自己付钱，就必须看得见花在哪：按 task/日 聚合的 token 与调用数。
 * 只覆盖 sc 直连 LLM 的调用；壳侧循环（agent_turn）记在网关侧，后续接入。
 */

const currentMonth = () => new Date().toISOString().slice(0, 7);

const fmt = (n: number) => (n >= 10000 ? `${(n / 1000).toFixed(1)}k` : String(n));

export default function Usage() {
  const [month, setMonth] = useState(currentMonth());
  const [data, setData] = useState<UsageLedger | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedFor, setLoadedFor] = useState("");

  if (loadedFor !== month) {
    setLoadedFor(month);
    adminUsage(month)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError((e as Error).message));
  }

  const tasks = data ? Object.entries(data.by_task) : [];

  return (
    <Page accent={ACCENTS.dashboard}>
      <PageHeader
        title="本月消耗"
        desc="LLM 用量台账——按任务与日聚合的 token 消耗与调用数"
        actions={
          <input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-sm focus:border-accent"
            aria-label="选择月份"
          />
        }
      />

      {error && (
        <Card className="mb-4 border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</Card>
      )}

      {!data ? (
        <Skeleton rows={5} />
      ) : (
        <div className="space-y-5">
          {/* 合计卡片 */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {[
              { label: "调用次数", value: fmt(data.total.calls) },
              { label: "输入 tokens", value: fmt(data.total.prompt_tokens) },
              { label: "输出 tokens", value: fmt(data.total.completion_tokens) },
              {
                label: "总 tokens",
                value: fmt(data.total.prompt_tokens + data.total.completion_tokens),
              },
            ].map((s) => (
              <Card key={s.label} className="p-4">
                <p className="text-xs text-ink-faint">{s.label}</p>
                <p className="mt-1 text-2xl font-semibold tracking-tight text-ink">{s.value}</p>
              </Card>
            ))}
          </div>

          {/* 任务小计 */}
          <Card className="overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-ink-faint">
                  <th className="px-4 py-3 font-medium">任务</th>
                  <th className="px-4 py-3 font-medium">调用</th>
                  <th className="px-4 py-3 font-medium">输入 tokens</th>
                  <th className="px-4 py-3 font-medium">输出 tokens</th>
                </tr>
              </thead>
              <tbody>
                {tasks.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-ink-faint">
                      本月暂无 LLM 调用
                    </td>
                  </tr>
                )}
                {tasks.map(([task, st]) => (
                  <tr key={task} className="border-b border-line/50 last:border-0">
                    <td className="px-4 py-2.5 font-medium text-ink">{task}</td>
                    <td className="px-4 py-2.5 text-ink-soft">{st.calls}</td>
                    <td className="px-4 py-2.5 text-ink-soft">{st.prompt_tokens}</td>
                    <td className="px-4 py-2.5 text-ink-soft">{st.completion_tokens}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          {/* 按日明细 */}
          {data.days.length > 0 && (
            <Card className="overflow-x-auto p-0">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-ink-faint">
                    <th className="px-4 py-3 font-medium">日期</th>
                    <th className="px-4 py-3 font-medium">调用</th>
                    <th className="px-4 py-3 font-medium">tokens 合计</th>
                    <th className="px-4 py-3 font-medium">按任务分布</th>
                  </tr>
                </thead>
                <tbody>
                  {data.days.map((d) => (
                    <tr key={d.date} className="border-b border-line/50 last:border-0">
                      <td className="px-4 py-2.5 font-medium text-ink">{d.date.slice(5)}</td>
                      <td className="px-4 py-2.5 text-ink-soft">{d.totals.calls}</td>
                      <td className="px-4 py-2.5 text-ink-soft">
                        {fmt(d.totals.prompt_tokens + d.totals.completion_tokens)}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-ink-faint">
                        {Object.entries(d.by_task)
                          .map(([t, s]) => `${t}×${s.calls}`)
                          .join(" · ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </div>
      )}
    </Page>
  );
}
