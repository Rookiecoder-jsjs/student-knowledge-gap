import { TreeStructure } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "framer-motion";
import { Link } from "react-router-dom";
import type { ComponentType, ReactNode } from "react";

/**
 * 教师/校务端侧栏骨架（side-nav-redesign 2026-09-11）：顶栏 tab 迁左成菜单栏，
 * 功能分区由调用方分组（教师端=班级/工具/管理，校务台=校务/知识）。学生门户与
 * 班级选择页不走此壳、仍用 TopBar（端边界见 docs/side-nav-redesign.md §2）。
 *
 * 布局：sticky 侧栏（窄屏 rail 64px / md+ 全宽 240px）+ 主区独立滚动；
 * 激活态沿用「颜色即位置」——模块色胶囊，动效参数与 TopBarNav 一致。
 */

/** 利落缓动（SaaS ease-out），与 TopBar 同参。 */
const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

export interface SideNavItem {
  /** 稳定唯一键：跨 cid 解析不变的原始路由。to 会随班级解析变化（classes 未加载
   * 时 cid=0 → 全部解析为 "/"，重复 key → React 协调留孤儿 → 链接翻倍的实锤
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
  /** 分区名（班级/工具/管理…）；rail 下隐藏。 */
  label: string;
  items: SideNavItem[];
}

/**
 * 侧栏壳。品牌点击落 brandTo（教师端=班级选择页）；context 为 md+ 语境块
 * （教师端=班级切换器），rail（<md）不渲染；railContext 为 rail 下的语境
 * 替代钮（教师端=跳班级选择页的图标钮，沿旧窄屏「经品牌→班级概览切换」语义
 * 的显式化）。footer 沉底（账号簇/返回链）。
 */
export function Sidebar({
  title,
  subtitle,
  brandTo = "/",
  brandAccent,
  context,
  railContext,
  groups,
  navLabel,
  layoutId,
  footer,
}: {
  title: ReactNode;
  /** 副标题（端名），缺省不显示。 */
  subtitle?: ReactNode;
  brandTo?: string;
  /** 品牌图标块底色；缺省用主题 accent。 */
  brandAccent?: string;
  /** md+ 语境块（班级切换器等）；rail 不渲染。 */
  context?: ReactNode;
  /** rail 模式语境替代钮；rail 外不渲染。 */
  railContext?: ReactNode;
  groups: SideNavGroup[];
  navLabel: string;
  /** framer-motion 布局动画组 id：每端独立（shell-side-nav / admin-side-nav）。 */
  layoutId: string;
  /** 沉底簇（账号/返回链）；缺省无。 */
  footer?: ReactNode;
}) {
  const reduce = useReducedMotion();
  return (
    <aside className="sticky top-0 flex h-[100dvh] w-16 shrink-0 flex-col self-start border-r border-line bg-surface md:w-60">
      {/* 品牌：rail 只留图标块；标题/副标题 md+ 显示 */}
      <Link
        to={brandTo}
        className="flex shrink-0 items-center gap-2.5 px-3 py-4 transition-opacity hover:opacity-80"
      >
        <span
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white ${
            brandAccent ? "" : "bg-accent shadow-[0_4px_14px_-2px] shadow-accent/50"
          }`}
          style={brandAccent ? { background: brandAccent } : undefined}
        >
          <TreeStructure size={18} weight="bold" />
        </span>
        <span className="hidden min-w-0 leading-tight md:block">
          <span className="block truncate text-sm font-semibold tracking-tight">{title}</span>
          {subtitle && (
            <span className="block truncate text-[11px] text-ink-faint">{subtitle}</span>
          )}
        </span>
      </Link>

      {/* 语境块：md+ 全宽（班级切换器），rail 收成图标钮 */}
      {(context || railContext) && (
        <div className="flex shrink-0 flex-col items-center gap-2 px-3 pb-2 md:items-stretch md:px-4">
          {context && <span className="hidden w-full md:block">{context}</span>}
          {railContext && <span className="md:hidden">{railContext}</span>}
        </div>
      )}

      {/* 分组导航：纵向胶囊，激活项模块色；rail 只留图标（title 作悬浮提示） */}
      <nav
        className="min-h-0 flex-1 overflow-y-auto px-3 py-1 md:px-4"
        aria-label={navLabel}
      >
        {groups.map((g) => (
          <div key={g.label} className="mb-3 last:mb-0">
            <p className="mb-1 hidden px-2 text-[11px] font-medium uppercase tracking-wide text-ink-faint md:block">
              {g.label}
            </p>
            <div className="flex flex-col gap-0.5">
              {g.items.map(({ id, to, label, icon: Icon, accent, active, trailing }) => (
                <Link
                  key={id ?? label}
                  to={to}
                  aria-current={active ? "page" : undefined}
                  title={label}
                  className={`relative flex items-center justify-center gap-2.5 rounded-xl px-2 py-2 text-sm font-medium transition-colors md:justify-start md:px-2.5 ${
                    active ? "text-white" : "text-ink-soft hover:bg-surface-2 hover:text-ink"
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
                  <span className="relative hidden min-w-0 truncate md:inline">{label}</span>
                  {trailing}
                </Link>
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* 沉底簇：账号/返回链 */}
      {footer && (
        <div className="shrink-0 border-t border-line/70 px-3 py-3 md:px-4">{footer}</div>
      )}
    </aside>
  );
}
