import { CheckCircle } from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import type { ReactNode } from "react";

/** 要点面板：产品/方案页的示意化右栏——图标 + 要点清单，不依赖产品截图。 */
export function FeaturePanel({
  Icon,
  title,
  points,
  footer,
  className = "",
}: {
  Icon: Icon;
  title: string;
  points: readonly string[];
  footer?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`border-2 border-ink bg-surface p-6 shadow-lift ${className}`}>
      <div className="flex items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center border-2 border-ink bg-primary text-white shadow-soft">
          <Icon size={20} weight="bold" />
        </span>
        <p className="font-display text-base font-bold tracking-tight text-ink">{title}</p>
      </div>
      <ul className="mt-4 divide-y divide-line">
        {points.map((p) => (
          <li key={p} className="flex items-start gap-2 py-2.5 text-sm leading-relaxed text-ink-soft">
            <CheckCircle size={15} weight="fill" className="mt-0.5 shrink-0 text-primary" />
            {p}
          </li>
        ))}
      </ul>
      {footer && <div className="mt-3 border-t border-line pt-3 text-xs text-ink-faint">{footer}</div>}
    </div>
  );
}
