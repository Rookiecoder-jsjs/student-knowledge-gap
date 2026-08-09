import { ArrowCounterClockwise, FolderOpen, WarningCircle, X } from "@phosphor-icons/react";
import { useEffect, useRef } from "react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

/* ---------------- Button ---------------- */

type Variant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  const styles: Record<Variant, string> = {
    primary:
      "bg-accent text-white shadow-soft hover:bg-accent-deep hover:shadow-lift disabled:bg-accent/40 disabled:shadow-none",
    secondary:
      "bg-surface text-ink border border-line hover:border-accent/50 hover:text-accent hover:bg-surface-2 disabled:opacity-40",
    ghost:
      "text-ink-soft hover:bg-accent-soft hover:text-accent disabled:opacity-40",
    danger:
      "bg-danger text-white shadow-soft hover:brightness-95 disabled:opacity-40",
  };
  return (
    <button
      className={`inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-3.5 py-2 text-sm font-medium transition-all duration-150 active:scale-[0.97] disabled:cursor-not-allowed ${styles[variant]} ${className}`}
      {...props}
    />
  );
}

/* ---------------- Badge ---------------- */

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "accent" | "warn" | "danger";
  children: ReactNode;
}) {
  const tones = {
    neutral: "bg-surface-2 text-ink-soft border-line",
    accent: "bg-accent-soft text-accent-deep border-accent/20",
    warn: "bg-warn-soft text-warn border-warn/20",
    danger: "bg-danger-soft text-danger border-danger/20",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/* ---------------- Card ---------------- */

export function Card({
  children,
  className = "",
  interactive = false,
}: {
  children: ReactNode;
  className?: string;
  interactive?: boolean;
}) {
  const base = "rounded-[10px] border border-line bg-surface shadow-soft";
  const hover = interactive
    ? "transition-all duration-150 hover:-translate-y-0.5 hover:shadow-lift hover:border-accent/30"
    : "";
  return <div className={`${base} ${hover} ${className}`}>{children}</div>;
}

/* ---------------- 三态：加载 / 空 / 错误 ---------------- */

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2.5" aria-live="polite" role="status">
      <span className="sr-only">加载中…</span>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-11 rounded-lg skeleton-shimmer"
          style={{ animationDelay: `${i * 120}ms` }}
        />
      ))}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center gap-3 py-12 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft">
        <FolderOpen size={24} className="text-accent" weight="thin" />
      </span>
      <p className="text-sm font-medium text-ink-soft">{title}</p>
      {hint && <p className="max-w-[46ch] text-xs leading-relaxed text-ink-faint">{hint}</p>}
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-3 py-12 text-center" role="alert">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-danger-soft">
        <WarningCircle size={24} className="text-danger" weight="thin" />
      </span>
      <p className="max-w-[52ch] text-sm text-ink-soft">{message}</p>
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          <ArrowCounterClockwise size={15} />
          重试
        </Button>
      )}
    </div>
  );
}

/* ---------------- 表单 ---------------- */

export function Field({
  label,
  error,
  hint,
  children,
}: {
  label: string;
  error?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-ink">{label}</span>
      {children}
      {hint && !error && <span className="text-xs text-ink-faint">{hint}</span>}
      {error && <span className="text-xs text-danger">{error}</span>}
    </label>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  const { className = "", ...rest } = props;
  return (
    <input
      className={`rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink tabular-nums placeholder:text-ink-faint transition-colors focus:border-accent ${className}`}
      {...rest}
    />
  );
}

/* ---------------- 区段标题 ---------------- */

/** 区段标题：小方块（accent）+ 标题 + 可选计数/副文。替代裸 <h2>。 */
export function SectionTitle({
  children,
  count,
  right,
}: {
  children: ReactNode;
  count?: number | string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-2.5 flex items-center justify-between gap-2">
      <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
        <span className="h-2 w-2 rounded-[2px] bg-accent" aria-hidden />
        {children}
        {count !== undefined && (
          <span className="font-normal normal-case tracking-normal text-ink-faint">（{count}）</span>
        )}
      </h2>
      {right}
    </div>
  );
}

/* ---------------- 页头 ---------------- */

export function PageHeader({
  title,
  desc,
  actions,
}: {
  title: string;
  desc?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {desc && <p className="mt-1 text-sm text-ink-soft">{desc}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ---------------- 模态（统一：焦点圈定 + Esc + 初始聚焦） ---------------- */

const MODAL_SIZE = {
  sm: "max-w-sm",
  md: "max-w-lg",
  lg: "max-w-2xl",
} as const;

/** 全站唯一弹窗：自动焦点圈定、Esc 关闭、初始聚焦、关闭后归还焦点。 */
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: keyof typeof MODAL_SIZE;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    const prevActive = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(
        panel?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        ) ?? []
      );
    // 初始聚焦首个可聚焦元素
    const items = focusables();
    items[0]?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "Tab" && panel) {
        const list = focusables();
        if (list.length === 0) return;
        const first = list[0];
        const last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      prevActive?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="absolute inset-0 bg-ink/40 backdrop-blur-[2px]"
        onClick={onClose}
        aria-hidden
      />
      <div
        ref={panelRef}
        className={`relative w-full ${MODAL_SIZE[size]} max-h-[85vh] overflow-auto rounded-[10px] border border-line bg-surface p-6 shadow-float`}
      >
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="text-base font-semibold tracking-tight text-ink">{title}</h3>
          <button
            onClick={onClose}
            aria-label="关闭"
            className="flex h-8 w-8 items-center justify-center rounded-md text-ink-faint transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <X size={18} />
          </button>
        </div>
        <div className="space-y-4">{children}</div>
        {footer && <div className="mt-5 flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

/* ---------------- 案头新增原语 ---------------- */

/** 流水线阶段状态点：● 完成 / ◐ 进行 / ○ 未开始。 */
export function StatusDot({ state }: { state: "done" | "active" | "todo" }) {
  const cls =
    state === "done"
      ? "bg-accent"
      : state === "active"
        ? "bg-accent ring-4 ring-accent/20"
        : "bg-line border border-ink-faint/40";
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${cls}`} aria-hidden />;
}

/** KPI 统计块：大号 tabular-nums 数字 + 标签 + 可选副文。 */
export function StatTile({
  value,
  label,
  hint,
  tone = "neutral",
}: {
  value: ReactNode;
  label: string;
  hint?: string;
  tone?: "neutral" | "accent" | "warn" | "danger";
}) {
  const val =
    tone === "accent"
      ? "text-accent"
      : tone === "warn"
        ? "text-warn"
        : tone === "danger"
          ? "text-danger"
          : "text-ink";
  return (
    <div className="rounded-[10px] border border-line bg-surface px-4 py-3 shadow-soft">
      <p className={`text-2xl font-semibold tabular-nums tracking-tight ${val}`}>{value}</p>
      <p className="mt-0.5 text-xs text-ink-faint">{label}</p>
      {hint && <p className="mt-0.5 text-[11px] text-ink-faint">{hint}</p>}
    </div>
  );
}
