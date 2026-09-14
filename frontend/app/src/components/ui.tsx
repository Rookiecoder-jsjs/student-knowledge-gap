import {
  ArrowCounterClockwise,
  CaretDown,
  FolderOpen,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { motion, useReducedMotion } from "framer-motion";
import { useEffect, useRef } from "react";
import type {
  ButtonHTMLAttributes,
  CSSProperties,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TdHTMLAttributes,
  ThHTMLAttributes,
} from "react";
import { EASE } from "../lib/motion-tokens";
import { PRODUCT_NAME } from "../lib/site";

/* ---------------- 页面模块色包裹器 ---------------- */

/**
 * 页面根节点包裹器：覆盖 --color-accent（运行时变量）。
 * accent-soft/deep 在 index.css 用 color-mix 派生，随此变量自动换色，
 * 因此整页按钮/徽章/装饰只需传一个主色即可整体换模块色。
 */
export function Page({
  accent,
  className = "",
  children,
}: {
  accent?: string;
  className?: string;
  children: ReactNode;
}) {
  const style = accent ? ({ "--color-accent": accent } as CSSProperties) : undefined;
  return (
    <div className={`page-surface ${className}`} style={style}>
      {children}
    </div>
  );
}

/** 彩色 icon 底座（教育风标志元素）：软色底 + 深色 icon，随页面 accent。 */
export function IconTile({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent/12 text-accent-deep ${className}`}
    >
      {children}
    </span>
  );
}

/* ---------------- Button ---------------- */

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

/** 高度三档（saas-redesign §4.4）：32 紧凑 / 36 默认 / 40 页头 CTA。 */
const SIZES: Record<Size, string> = {
  sm: "h-8 rounded-lg px-3 text-[13px]",
  md: "h-9 rounded-lg px-3.5 text-sm",
  lg: "h-10 rounded-lg px-4 text-sm",
};

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  const styles: Record<Variant, string> = {
    primary:
      "bg-accent text-white ring-1 ring-inset ring-black/5 shadow-[0_8px_18px_-10px] shadow-accent/70 hover:bg-accent-deep hover:shadow-lift disabled:bg-accent/40 disabled:shadow-none",
    secondary:
      "bg-surface text-ink border border-line-strong shadow-soft hover:border-accent/45 hover:text-accent-deep hover:bg-surface-2 disabled:opacity-40",
    ghost:
      "text-ink-soft hover:bg-accent/10 hover:text-accent disabled:opacity-40",
    danger:
      "bg-danger text-white hover:brightness-90 hover:shadow-lift disabled:opacity-40",
  };
  return (
    <button
      className={`inline-flex cursor-pointer items-center justify-center gap-1.5 font-semibold transition-[transform,background-color,border-color,color,box-shadow] duration-150 active:scale-[0.98] disabled:cursor-not-allowed ${SIZES[size]} ${styles[variant]} ${className}`}
      {...props}
    />
  );
}

/* ---------------- Badge ---------------- */

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "accent" | "warn" | "danger" | "success" | "info";
  children: ReactNode;
}) {
  const tones = {
    neutral: "bg-surface-2 text-ink-soft border-line",
    accent: "bg-accent-soft text-accent-deep border-accent/25",
    warn: "bg-warn-soft text-warn border-warn/25",
    danger: "bg-danger-soft text-danger border-danger/25",
    success: "bg-success-soft text-success border-success/25",
    info: "bg-info-soft text-info border-info/25",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-semibold ${tones[tone]}`}
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
  // 专业商务系（saas-redesign §4.4）：1px 细描边卡 + 轻阴影；interactive 才 lift
  //（仅 transform/阴影/border-color，GPU 友好）
  const base = "rounded-[14px] border border-line bg-surface shadow-soft";
  const hover = interactive
    ? "transition-[transform,border-color,box-shadow] duration-150 hover:-translate-y-px hover:border-accent/40 hover:shadow-lift"
    : "";
  return <div className={`${base} ${hover} ${className}`}>{children}</div>;
}

/* ---------------- Select（替代原生裸 select，design-style §4） ---------------- */

/** 下拉选择：样式与 Input 同族，自绘箭头；保留原生弹出（移动端友好）。
 * sm=h-8 紧凑（行内/工具条场景），md=h-9 默认（design-style §4）。 */
export function Select({
  className = "",
  size = "md",
  children,
  ...props
}: Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> & { size?: "sm" | "md" }) {
  const sizing =
    size === "sm"
      ? "h-8 rounded-lg pl-2.5 pr-7 text-[13px]"
      : "h-9 rounded-lg pl-3 pr-8 text-sm";
  return (
    <div className={`relative ${className}`}>
      <select
        className={`w-full appearance-none border border-line-strong bg-surface text-ink transition-colors focus:border-accent disabled:opacity-40 ${sizing}`}
        {...props}
      >
        {children}
      </select>
      <CaretDown
        size={size === "sm" ? 12 : 14}
        className={`pointer-events-none absolute top-1/2 -translate-y-1/2 text-ink-faint ${
          size === "sm" ? "right-2" : "right-2.5"
        }`}
        aria-hidden
      />
    </div>
  );
}

/* ---------------- Tabs（分段页签，design-style §4：禁页内手写 tab） ---------------- */

/** 胶囊分段页签：layoutId 滑动指示（active=模块色胶囊白字，与导航同语言）。 */
export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  ariaLabel,
  layoutId,
  className = "",
}: {
  tabs: readonly { key: T; label: string }[];
  value: T;
  onChange: (key: T) => void;
  ariaLabel: string;
  /** 同屏多组各自独立 id（framer 布局动画隔离）。 */
  layoutId: string;
  className?: string;
}) {
  const reduce = useReducedMotion();
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`inline-flex max-w-full overflow-x-auto rounded-xl border border-line bg-surface p-1 shadow-soft ${className}`}
    >
      {tabs.map(({ key, label }) => {
        const active = key === value;
        return (
          <button
            key={key}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(key)}
            className={`relative min-h-9 shrink-0 rounded-lg px-4 py-1.5 text-sm transition-colors ${
              active ? "font-semibold text-white" : "text-ink-soft hover:text-ink"
            }`}
          >
            {active && !reduce && (
              <motion.span
                layoutId={layoutId}
                className="absolute inset-0 rounded-lg bg-accent"
                transition={{ type: "spring", stiffness: 320, damping: 30, ease: EASE }}
                aria-hidden
              />
            )}
            {active && reduce && (
              <span className="absolute inset-0 rounded-lg bg-accent" aria-hidden />
            )}
            <span className="relative">{label}</span>
          </button>
        );
      })}
    </div>
  );
}

/* ---------------- Table（行高 40/13px/表头 surface-2，saas-redesign §4.4） ---------------- */

export function Table({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`overflow-x-auto ${className}`}>
      <table className="w-full border-collapse text-[13px] tabular-nums">{children}</table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return (
    <thead className="bg-surface-2/80 text-left text-xs font-semibold text-ink-soft">
      {children}
    </thead>
  );
}

export function TR({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <tr className={`transition-colors hover:bg-surface-2/50 ${className}`}>{children}</tr>;
}

export function Th({ className = "", ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={`whitespace-nowrap border-b border-line px-3 py-2 font-medium ${className}`}
      {...props}
    />
  );
}

export function Td({ className = "", ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={`border-b border-line px-3 py-2 align-middle ${className}`} {...props} />;
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
      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent/12">
        <FolderOpen size={22} className="text-accent-deep" weight="thin" />
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
      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-danger/12">
        <WarningCircle size={22} className="text-danger" weight="thin" />
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

/* ---------------- 分页 ---------------- */

/** 统一列表分页控件：页码从 1 开始，服务端列表可用 hasMore 覆盖 total 推导。 */
export function Pagination({
  page,
  pageSize,
  total,
  hasMore,
  onPageChange,
  disabled = false,
  className = "",
}: {
  page: number;
  pageSize: number;
  total?: number;
  hasMore?: boolean;
  onPageChange: (page: number) => void;
  disabled?: boolean;
  className?: string;
}) {
  const safePage = Math.max(1, page);
  const totalPages = total == null ? null : Math.max(1, Math.ceil(total / pageSize));
  const canPrev = safePage > 1;
  const canNext = hasMore ?? (totalPages != null ? safePage < totalPages : false);
  if (!canPrev && !canNext && (total ?? 0) <= pageSize) return null;

  return (
    <nav
      aria-label="列表分页"
      className={`flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3 ${className}`}
    >
      <p className="text-xs text-ink-faint">
        {totalPages != null ? `第 ${safePage} / ${totalPages} 页` : `第 ${safePage} 页`}
        {total != null ? ` · 共 ${total} 条` : ""}
      </p>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          disabled={disabled || !canPrev}
          onClick={() => onPageChange(safePage - 1)}
          aria-label="上一页"
        >
          上一页
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={disabled || !canNext}
          onClick={() => onPageChange(safePage + 1)}
          aria-label="下一页"
        >
          下一页
        </Button>
      </div>
    </nav>
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
      <span className="text-[13px] font-medium text-ink-soft">{label}</span>
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
      className={`min-h-9 rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink shadow-[inset_0_1px_0_rgb(255_255_255/.04)] tabular-nums placeholder:text-ink-faint transition-colors focus:border-accent ${className}`}
      {...rest}
    />
  );
}

/* ---------------- 区段标题 ---------------- */

/** 区段标题：细竖条（accent）+ 微标签字 + 可选计数/副文。替代裸 <h2>。 */
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
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.12em] text-ink-faint">
        <span className="h-3.5 w-[3px] rounded-full bg-accent" aria-hidden />
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
    // 吸顶（saas-redesign §6）：top 取 --shell-top（Shell 移动端顶条 48px 让位，
    // 缺省 0）；负外边距出血到主区内容边，底衬毛玻璃避免内容穿透。
    <div className="sticky top-[var(--shell-top,0px)] z-20 -mx-4 mb-6 flex flex-wrap items-center justify-between gap-4 border-b border-line/70 bg-canvas/92 px-4 py-4 shadow-[0_12px_24px_-28px_rgba(33,42,36,.7)] backdrop-blur-xl md:-mx-8 md:px-8">
      <div>
        <p className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-accent">{PRODUCT_NAME}</p>
        <h1 className="font-display text-[22px] font-bold leading-tight tracking-[-0.02em] text-ink">{title}</h1>
        {desc && <p className="mt-1 text-sm text-ink-soft">{desc}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center justify-end gap-2">{actions}</div>}
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
      {/* 遮罩：双主题恒为暗色纱（硬编码白名单，design-style §7——唯一 bg-black） */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
        onClick={onClose}
        aria-hidden
      />
      <div
        ref={panelRef}
        className={`relative w-full ${MODAL_SIZE[size]} max-h-[85vh] overflow-auto rounded-2xl border border-line-strong bg-surface p-6 shadow-float`}
      >
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h3>
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
        ? "bg-accent ring-4 ring-accent/15"
        : "bg-line border border-ink-faint/40";
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${cls}`} aria-hidden />;
}

/** KPI 统计块：彩色 icon 底座（可选）+ 大号 tabular-nums 数字 + 标签 + 可选副文。 */
export function StatTile({
  value,
  label,
  hint,
  tone = "neutral",
  icon,
}: {
  value: ReactNode;
  label: string;
  hint?: string;
  tone?: "neutral" | "accent" | "warn" | "danger";
  icon?: ReactNode;
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
    <div className="relative overflow-hidden rounded-[14px] border border-line bg-surface px-4 py-3.5 shadow-soft before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-accent/55">
      {icon && <IconTile className="mb-2.5">{icon}</IconTile>}
      <p className={`font-display text-[28px] font-bold leading-tight tabular-nums tracking-tight ${val}`}>
        {value}
      </p>
      <p className="mt-1 text-xs text-ink-faint">{label}</p>
      {hint && <p className="mt-0.5 text-[11px] text-ink-faint">{hint}</p>}
    </div>
  );
}
