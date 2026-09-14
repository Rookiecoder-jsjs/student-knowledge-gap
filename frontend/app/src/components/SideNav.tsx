import { motion, useReducedMotion } from "framer-motion";
import { Link } from "react-router-dom";
import type { ComponentType, ReactNode } from "react";
import { EASE } from "../lib/motion-tokens";
import { BrandMark } from "./BrandMark";

/**
 * 教师/管理端侧栏骨架（side-nav-redesign 2026-09-11；P2 响应式修订 saas-redesign §6）：
 * 分区由调用方分组（教师端=班级/工具/全局管理）。学生门户与班级选择页不走此壳、
 * 仍用 TopBar（端边界见 docs/side-nav-redesign.md §2）。
 *
 * 布局三态（saas-redesign §6，64px rail 随抽屉升级退役）：<md 固定抽屉
 * （off-canvas，mobileOpen 控制进出，遮罩与移动端菜单条在 Shell）+ md+ sticky
 * 全宽 240px。激活态沿用「颜色即位置」——模块色胶囊。
 */

export interface SideNavItem {
  /** 稳定唯一键：跨 cid 解析不变的原始路由。to 会随班级解析变化（classes 未加载
   * 时 cid=0 → 全部解析为 "/"，重复 key → React 协调留孤儿 → 导航链接翻倍的实锤
   * 根因），key 绝不能用解析后的 to——纪律沿自 TopBarNavItem。 */
  id?: string;
  to: string;
  label: string;
  icon: ComponentType<{
    size?: number;
    weight?: "regular" | "fill" | "bold";
    className?: string;
  }>;
  /** 激活胶囊底色（模块色，如 ACCENTS.dashboard）。 */
  accent: string;
  active?: boolean;
  /** 尾缀节点（待签发角标等）：自行绝对定位于项右上角，DOM 序在胶囊之后保证盖在其上。 */
  trailing?: ReactNode;
}

export interface SideNavGroup {
  /** 分区名（班级/工具/全局管理…）。 */
  label: string;
  items: SideNavItem[];
}

/**
 * 侧栏壳。品牌点击落 brandTo（教师端=班级选择页）；context 为语境块
 * （教师端=班级切换器）；footer 沉底（账号簇）。<md 抽屉由 mobileOpen/
 * onMobileClose 控制（导航点击即收起），md+ 常驻无感。
 */
export function Sidebar({
  title,
  subtitle,
  brandTo = "/",
  context,
  groups,
  navLabel,
  layoutId,
  footer,
  mobileOpen = false,
  onMobileClose,
}: {
  title: ReactNode;
  /** 副标题（端名），缺省不显示。 */
  subtitle?: ReactNode;
  brandTo?: string;
  /** 语境块（班级切换器等）。 */
  context?: ReactNode;
  groups: SideNavGroup[];
  navLabel: string;
  /** framer-motion 布局动画组 id：每端独立（shell-side-nav）。 */
  layoutId: string;
  /** 沉底簇（账号/返回链）；缺省无。 */
  footer?: ReactNode;
  /** <md 抽屉开合；md+ 恒常驻。 */
  mobileOpen?: boolean;
  /** 抽屉收起回调（导航点击/遮罩点击）。 */
  onMobileClose?: () => void;
}) {
  const reduce = useReducedMotion();
  return (
    <aside
      className={`fixed inset-y-0 left-0 z-50 flex h-[100dvh] w-64 shrink-0 flex-col border-r border-line bg-surface/95 shadow-[8px_0_32px_-28px_rgba(33,42,36,.45)] backdrop-blur transition-transform duration-200 md:sticky md:top-0 md:self-start md:translate-x-0 ${
        mobileOpen ? "translate-x-0 shadow-float" : "-translate-x-full"
      }`}
    >
      {/* 品牌 */}
      <Link
        to={brandTo}
        onClick={onMobileClose}
        className="relative flex shrink-0 items-center gap-3 border-b border-line/70 px-5 py-5 transition-colors hover:bg-surface-2/60"
      >
        <BrandMark size={36} />
        <span className="min-w-0 leading-tight">
          <span className="block truncate font-display text-[15px] font-bold tracking-tight">{title}</span>
          {subtitle && (
            <span className="block truncate text-[11px] text-ink-faint">{subtitle}</span>
          )}
        </span>
      </Link>

      {/* 语境块（班级切换器） */}
      {context && <div className="flex shrink-0 flex-col gap-2 px-4 pb-3 pt-4">{context}</div>}

      {/* 分组导航：纵向胶囊，激活项模块色 */}
      <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-2" aria-label={navLabel}>
        {groups.map((g) => (
          <div key={g.label} className="mb-4 last:mb-0">
            <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
              {g.label}
            </p>
            <div className="flex flex-col gap-1">
              {g.items.map(({ id, to, label, icon: Icon, accent, active, trailing }) => (
                <Link
                  key={id ?? label}
                  to={to}
                  onClick={onMobileClose}
                  aria-current={active ? "page" : undefined}
                  className={`relative flex min-h-10 items-center gap-2.5 rounded-xl px-3 py-2 text-sm font-medium transition-colors ${
                    active ? "text-white shadow-soft" : "text-ink-soft hover:bg-surface-2 hover:text-ink"
                  }`}
                >
                  {active && !reduce && (
                    <motion.span
                      layoutId={layoutId}
                      className="absolute inset-0 rounded-xl"
                      style={{ background: accent }}
                      transition={{ type: "spring", stiffness: 320, damping: 30, ease: EASE }}
                      aria-hidden
                    />
                  )}
                  {active && reduce && (
                    <span
                      className="absolute inset-0 rounded-xl"
                      style={{ background: accent }}
                      aria-hidden
                    />
                  )}
                  <Icon size={17} weight={active ? "fill" : "regular"} className="relative shrink-0" />
                  <span className="relative min-w-0 truncate">{label}</span>
                  {trailing}
                </Link>
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* 沉底簇：账号/返回链 */}
      {footer && <div className="shrink-0 border-t border-line/70 bg-surface-2/35 px-4 py-3">{footer}</div>}
    </aside>
  );
}
