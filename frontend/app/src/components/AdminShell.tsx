import { ChartBar, UserGear } from "@phosphor-icons/react";
import { Link, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../lib/AuthContext";
import { ACCENTS } from "../lib/theme";
import { AccountCluster, TOOL_LINK, TopBar, TopBarNav } from "./TopBar";
import { useRoleFlags } from "./PortalGuard";

/** 校务台壳（分区重设计 2026-09-10）：账号/用量等校级运营从教师 header 撤出，
 * 落进 /admin 独立壳——与教学工作台（Shell）平级、各自语境。挂载条件在 App.tsx：
 * admin 登录或开放模式（演示信任域）；普通教师不可达。
 * 顶栏为统一骨架（TopBar）：品牌左 · 导航中 · 返回/账号右。
 */
export function AdminShell({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const flags = useRoleFlags();
  const location = useLocation();

  // 账号页依赖 admin 登录（开放模式匿名无主体，仅用量可达）
  const items = [
    ...(flags.adminLogin
      ? [{ to: "/admin/accounts", label: "账号", icon: UserGear }]
      : []),
    { to: "/admin/usage", label: "用量", icon: ChartBar },
  ].map(({ to, label, icon }) => ({
    to,
    label,
    icon,
    accent: ACCENTS.dashboard,
    active: location.pathname.startsWith(to),
  }));

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <TopBar
        title={<span className="hidden sm:inline">薄弱点分析</span>}
        subtitle={<span className="hidden sm:block">校务台</span>}
        nav={<TopBarNav navLabel="校务台导航" layoutId="admin-nav" items={items} />}
        right={
          <>
            {/* 窄屏隐藏返回链：品牌点击同为 /，给校务台导航让位。
                注：不可写 `${TOOL_LINK} hidden`——本项目 Tailwind 排序里
                .inline-flex 在 .hidden 之后，裸 inline-flex 会把 hidden 顶掉，
                必须用响应式变体包一层（hidden sm:inline-flex）在外层。 */}
            <span className="hidden sm:inline-flex">
              <Link to="/" className={TOOL_LINK}>
                ← 教学工作台
              </Link>
            </span>
            {session && <AccountCluster session={session} name={session.teacher?.name ?? ""} />}
          </>
        }
      />

      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-[1200px] px-6 py-7">{children}</div>
      </main>
    </div>
  );
}
