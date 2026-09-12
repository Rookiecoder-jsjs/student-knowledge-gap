import { SignOut, TreeStructure } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "framer-motion";
import { Link } from "react-router-dom";
import type { ComponentType, ReactNode } from "react";
import { useAuth } from "../lib/AuthContext";
import type { Session } from "../lib/auth";
import { EASE } from "../lib/motion-tokens";
import { ThemeToggle } from "./ThemeToggle";

/**
 * 学生门户/班级选择页统一顶栏骨架（UI 位置统一 2026-09-10）：品牌左 · 导航中 ·
 * 工具/账号右。side-nav-redesign（2026-09-11）后教师工作台/校务台已迁侧栏
 * （SideNav.tsx），学生端 5 tab 顶栏保留现状、班级选择页仍为转场 hub。
 * 导航胶囊激活态 = 模块色（颜色即位置），动效参数与侧栏一致。
 */

/** 顶栏工具/账号链接统一款式（教师 Shell、班级选择页、校务台右侧共用）。
 * 窄屏 icon-only 态收窄内边距，给中段主导航让位。 */
export const TOOL_LINK =
  "inline-flex items-center gap-1.5 rounded-full px-2 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink sm:px-3";

export interface TopBarNavItem {
  /** 稳定唯一键（可选）：跨 cid 解析不变的原始路由。to 会随班级解析变化
   * （classes 未加载时 cid=0 → 全部解析为 "/"，产生重复 key，React 协调
   * 会留孤儿节点——导航链接翻倍的实锤根因），key 绝不能用解析后的 to。 */
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
}

/** 顶栏容器。 */
export function TopBar({
  title,
  subtitle,
  brandTo = "/",
  brandAccent,
  nav,
  right,
}: {
  /** 主标题（可含徽标等节点）。 */
  title: ReactNode;
  /** 副标题（端名/语境），缺省不显示。 */
  subtitle?: ReactNode;
  /** 品牌点击落点；学生门户预览指向返回教师端。 */
  brandTo?: string;
  /** 品牌图标块底色；缺省用主题 accent（教师/校务端），学生端传 ACCENTS.student。 */
  brandAccent?: string;
  /** 中段导航（<TopBarNav/> 或自定义节点）；缺省无导航。 */
  nav?: ReactNode;
  /** 右侧工具/账号簇。 */
  right?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-40 border-b border-line/70 bg-surface/80 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1200px] items-center justify-between gap-4 px-6">
        <Link
          to={brandTo}
          className="flex shrink-0 items-center gap-2.5 transition-opacity hover:opacity-80"
        >
          <span
            className={`flex h-9 w-9 items-center justify-center rounded-xl text-white ${
              brandAccent ? "" : "bg-accent shadow-[0_4px_14px_-2px] shadow-accent/50"
            }`}
            style={brandAccent ? { background: brandAccent } : undefined}
          >
            <TreeStructure size={18} weight="bold" />
          </span>
          <div className="leading-tight">
            <p className="flex items-center gap-2 text-sm font-semibold tracking-tight">{title}</p>
            {subtitle && <p className="text-[11px] text-ink-faint">{subtitle}</p>}
          </div>
        </Link>

        {nav}

        <div className="flex shrink-0 items-center gap-1.5">{right}</div>
      </div>
    </header>
  );
}

/** 顶栏导航胶囊组：激活项渲染模块色胶囊；窄屏中段横向滚动（min-w-0 让位两侧）。 */
export function TopBarNav({
  items,
  navLabel,
  layoutId = "topbar-nav",
}: {
  items: TopBarNavItem[];
  navLabel: string;
  /** 同屏只有一个导航组；各端传不同 id 隔离 framer-motion 布局动画。 */
  layoutId?: string;
}) {
  const reduce = useReducedMotion();
  return (
    <nav className="flex min-w-0 items-center gap-1 overflow-x-auto" aria-label={navLabel}>
      {items.map(({ id, to, label, icon: Icon, accent, active }) => (
        <Link
          key={id ?? label}
          to={to}
          aria-current={active ? "page" : undefined}
          className={`relative flex shrink-0 items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition-colors ${
            active ? "text-white" : "text-ink-soft hover:text-ink"
          }`}
        >
          {active && !reduce && (
            <motion.span
              layoutId={layoutId}
              className="absolute inset-0 rounded-full"
              style={{ background: accent }}
              transition={{ type: "spring", stiffness: 320, damping: 30, ease: EASE }}
              aria-hidden
            />
          )}
          {active && reduce && (
            <span className="absolute inset-0 rounded-full" style={{ background: accent }} aria-hidden />
          )}
          <Icon size={16} weight={active ? "fill" : "regular"} className="relative" />
          <span className="relative">{label}</span>
        </Link>
      ))}
    </nav>
  );
}

/** 右上账号簇：姓名+管理员徽标（窄屏隐藏）+ 主题切换（saas-redesign §5.3，
 * 教师端/学生端一处接入全端生效）+ 登出钮（全宽可用，端进出修订语义）。
 * 教师端/校务台侧栏化后此簇同时沉入侧栏底部复用（SideNav footer）。 */
export function AccountCluster({ session, name }: { session: Session; name?: string }) {
  const { logout } = useAuth();
  return (
    <>
      {name && (
        <span className="ml-1 hidden items-center gap-1.5 md:inline-flex">
          <span className="text-sm font-semibold text-ink">{name}</span>
          {session.role === "admin" && (
            <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] font-medium text-accent-deep">
              管理员
            </span>
          )}
        </span>
      )}
      <ThemeToggle />
      <button
        onClick={logout}
        aria-label="退出登录"
        title="退出登录"
        className="inline-flex items-center gap-1 rounded-full px-2 py-1.5 text-[13px] font-medium text-ink-soft transition-colors hover:bg-danger-soft hover:text-danger"
      >
        <SignOut size={15} />
      </button>
    </>
  );
}
