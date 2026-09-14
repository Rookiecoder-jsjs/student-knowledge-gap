import { SITE } from "../../lib/site";
import { BrandMark } from "./BrandMark";

/** 官网品牌锁：与教师端、登录页、favicon 共用同一品牌标。 */
export function Logo({ dark = false, compact = false }: { dark?: boolean; compact?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <BrandMark size={30} />
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
