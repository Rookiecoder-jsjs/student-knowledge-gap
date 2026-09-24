import { SignOut } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { useEffect, useRef, type ComponentType, type ReactNode } from "react";
import { useAuth } from "../lib/AuthContext";
import type { Session } from "../lib/auth";
import { ThemeToggle } from "./ThemeToggle";
import { BrandMark } from "./BrandMark";

/**
 * 学生门户/班级选择页统一顶栏骨架（UI 位置统一 2026-09-10）：品牌左 · 导航中 ·
 * 工具/账号右。side-nav-redesign（2026-09-11）后教师工作台/校务台已迁侧栏
 * （SideNav.tsx），学生端 5 tab 顶栏保留现状、班级选择页仍为转场 hub。
 * 导航方块激活态 = 模块色（颜色即位置），动效参数与侧栏一致。
 */

/** 顶栏工具/账号链接统一款式（教师 Shell、班级选择页、校务台右侧共用）。
 * 窄屏 icon-only 态收窄内边距，给中段主导航让位。 */
export const TOOL_LINK =
  "inline-flex items-center gap-1.5 px-2 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink sm:px-3";

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
  /** 激活方块底色（模块色，如 ACCENTS.dashboard）。 */
  accent: string;
  active?: boolean;
}

/** 顶栏容器。 */
export function TopBar({
  title,
  subtitle,
  brandTo = "/",
  nav,
  right,
}: {
  /** 主标题（可含徽标等节点）。 */
  title: ReactNode;
  /** 副标题（端名/语境），缺省不显示。 */
  subtitle?: ReactNode;
  /** 品牌点击落点；学生门户预览指向返回教师端。 */
  brandTo?: string;
  /** 中段导航（<TopBarNav/> 或自定义节点）；缺省无导航。 */
  nav?: ReactNode;
  /** 右侧工具/账号簇。 */
  right?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-40 border-b-[3px] border-ink bg-canvas">
      <div className="mx-auto grid max-w-[1240px] grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-6 lg:flex lg:h-[68px] lg:justify-between lg:py-0">
        <Link
          to={brandTo}
          className="flex shrink-0 items-center gap-2.5 rounded-xl transition-opacity hover:opacity-80"
        >
          <BrandMark size={36} />
          <div className="leading-tight">
            <p className="flex items-center gap-2 font-display text-sm font-bold tracking-tight">{title}</p>
            {subtitle && <p className="text-[11px] text-ink-faint">{subtitle}</p>}
          </div>
        </Link>

        {nav && <div className="order-3 col-span-2 min-w-0 lg:order-none">{nav}</div>}

        <div className="flex shrink-0 items-center gap-1.5">{right}</div>
      </div>
    </header>
  );
}

/** 顶栏导航方块组：窄屏独占第二行，可横向滚动；当前入口自动保持可见。 */
export function TopBarNav({
  items,
  navLabel,
}: {
  items: TopBarNavItem[];
  navLabel: string;
  /** 保留旧调用接口；静帧主题不使用布局动画。 */
  layoutId?: string;
}) {
  const navRef = useRef<HTMLElement>(null);
  const activeKey = items.find((item) => item.active)?.to;
  useEffect(() => {
    navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeKey]);
  return (
    <nav ref={navRef} className="flex min-w-0 items-center gap-1 overflow-x-auto" aria-label={navLabel}>
      {items.map(({ id, to, label, icon: Icon, accent, active }) => (
        <Link
          key={id ?? label}
          to={to}
          aria-current={active ? "page" : undefined}
          className={`relative flex min-h-10 shrink-0 items-center gap-2 px-4 py-2 text-sm font-semibold transition-colors ${
            active ? "text-white shadow-soft" : "text-ink-soft hover:bg-surface-2/70 hover:text-ink"
          }`}
        >
          {active && (
            <span className="absolute inset-0" style={{ background: accent }} aria-hidden />
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
        className="inline-flex items-center gap-1 px-2 py-1.5 text-[13px] font-medium text-ink-soft transition-colors hover:bg-danger-soft hover:text-danger"
      >
        <SignOut size={15} />
      </button>
    </>
  );
}
