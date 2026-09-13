/**
 * Hero 视觉：教学改进路径示意卡。
 * 用一条从发现问题到验证改变的路径，表达产品带来的结果，而不是展示实现细节。
 */
export function GraphCard({ className = "" }: { className?: string }) {
  const steps = [
    { x: 72, label: "看见问题", detail: "共性与差异", active: true },
    { x: 210, label: "找准重点", detail: "决定先改什么", active: true },
    { x: 348, label: "安排行动", detail: "每个人有下一步", active: false },
    { x: 486, label: "验证改变", detail: "有效做法留下", active: false },
  ];

  return (
    <div className={`rounded-2xl border border-white/10 bg-night-2/80 p-5 shadow-[0_32px_80px_-32px_rgba(0,0,0,0.55)] backdrop-blur ${className}`}>
      <div className="flex items-center justify-between border-b border-white/10 pb-3">
        <p className="text-xs font-semibold tracking-[0.18em] text-white/50 uppercase">改进路径 · 示例</p>
        <p className="text-[10px] text-white/30">从发现到改变</p>
      </div>
      <svg viewBox="0 0 560 250" className="mt-5 w-full" role="img" aria-label="教学改进路径示意：看见问题、找准重点、安排行动、验证改变">
        <defs>
          <linearGradient id="path-line" x1="0" x2="1">
            <stop offset="0" stopColor="#5eead4" stopOpacity="0.9" />
            <stop offset="1" stopColor="#f5b04d" stopOpacity="0.55" />
          </linearGradient>
        </defs>
        <path d="M72 92 H486" stroke="rgba(255,255,255,0.14)" strokeWidth="4" strokeLinecap="round" />
        <path d="M72 92 H348" stroke="url(#path-line)" strokeWidth="4" strokeLinecap="round" />
        {steps.map((step, index) => (
          <g key={step.label}>
            <circle cx={step.x} cy="92" r="22" fill={step.active ? "#5eead4" : "#183b37"} stroke={step.active ? "#5eead4" : "rgba(255,255,255,0.28)"} strokeWidth="2" />
            <text x={step.x} y="98" textAnchor="middle" fill={step.active ? "#0a1f1d" : "rgba(255,255,255,0.6)"} fontSize="12" fontWeight="700">{String(index + 1).padStart(2, "0")}</text>
            <text x={step.x} y="145" textAnchor="middle" fill="rgba(255,255,255,0.82)" fontSize="13" fontWeight="600">{step.label}</text>
            <text x={step.x} y="166" textAnchor="middle" fill="rgba(255,255,255,0.42)" fontSize="10">{step.detail}</text>
          </g>
        ))}
        <rect x="152" y="204" width="256" height="28" rx="14" fill="rgba(94,234,212,0.12)" />
        <text x="280" y="222" textAnchor="middle" fill="#9ee9dc" fontSize="11">让每一次考试，都带走一个更好的下一步</text>
      </svg>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-white/10 pt-3 text-[11px] text-white/45">
        <span className="flex items-center gap-1.5"><i className="h-1.5 w-1.5 rounded-full bg-mint" />已经看见</span>
        <span className="flex items-center gap-1.5"><i className="h-1.5 w-1.5 rounded-full bg-annot" />准备行动</span>
        <span className="ml-auto">每一轮都有回响</span>
      </div>
    </div>
  );
}
