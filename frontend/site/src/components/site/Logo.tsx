import { SITE } from "../../lib/site";

/** 品牌标：知识点图谱三节点母题（呼应知识图谱能力），亮/暗双面。 */
export function Logo({ dark = false, compact = false }: { dark?: boolean; compact?: boolean }) {
  const node = dark ? "#5eead4" : "#0f766e";
  const edge = dark ? "rgba(255,255,255,0.28)" : "rgba(15,118,110,0.35)";
  return (
    <span className="flex items-center gap-2.5">
      <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">
        <path d="M8 21 L15 9 L22 19 M8 21 L22 19" stroke={edge} strokeWidth="1.6" fill="none" />
        <circle cx="15" cy="9" r="3.4" fill={node} />
        <circle cx="8" cy="21" r="2.6" fill={node} opacity="0.75" />
        <circle cx="22" cy="19" r="2.6" fill={node} opacity="0.75" />
      </svg>
      <span className="flex flex-col leading-none">
        <span className={`font-display text-[17px] font-bold tracking-tight ${dark ? "text-white" : "text-ink"}`}>
          {SITE.name}
        </span>
        {!compact && (
          <span className={`mt-1 text-[10px] ${dark ? "text-white/50" : "text-ink-faint"}`}>{SITE.suffix}</span>
        )}
      </span>
    </span>
  );
}
