import { ArrowLeft, BookOpen, ChartBar, UserGear } from "@phosphor-icons/react";
import { Link, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../lib/AuthContext";
import { getBackTarget } from "../lib/portal";
import { ACCENTS } from "../lib/theme";
import { AccountCluster } from "./TopBar";
import { Sidebar, type SideNavGroup } from "./SideNav";
import { useRoleFlags } from "./PortalGuard";

/**
 * 校务台壳（分区重设计 2026-09-10；side-nav-redesign 2026-09-11 迁侧栏）：
 * 账号/用量等校级运营从教学端撤出，落进 /admin 独立壳——与教学工作台平级、
 * 各自语境。挂载条件在 App.tsx：admin 登录或开放模式（演示信任域）；普通
 * 教师不可达。侧栏分组「校务」（账号/用量）+「知识」（知识库面板），
 * 返回教学工作台沉底（rail 收成图标）。
 */
export function AdminShell({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const flags = useRoleFlags();
  const location = useLocation();
  // 「← 教学工作台」从哪来回哪（校务台进出修订 2026-09-12）：Shell/ClassPicker
  // 挂载换页时持续记 /admin 的返回目标；无记录（登录直进/手敲 URL）回落班级概览。
  const backTo = getBackTarget("/admin", "/");

  // 账号页依赖 admin 登录（开放模式匿名无主体，仅用量可达）；
  // 知识库面板：admin/开放 ∨ 学科管理员 ∨ kb_editor（rbac-scopes-design §6）
  const groups: SideNavGroup[] = [
    {
      label: "校务",
      items: [
        ...(flags.adminLogin
          ? [
              {
                id: "/admin/accounts",
                to: "/admin/accounts",
                label: "账号",
                icon: UserGear,
                accent: ACCENTS.dashboard,
                active: location.pathname.startsWith("/admin/accounts"),
              },
            ]
          : []),
        {
          id: "/admin/usage",
          to: "/admin/usage",
          label: "用量",
          icon: ChartBar,
          accent: ACCENTS.dashboard,
          active: location.pathname.startsWith("/admin/usage"),
        },
      ],
    },
    ...(flags.kbPanelVisible
      ? [
          {
            label: "知识",
            items: [
              {
                id: "/admin/kb",
                to: "/admin/kb",
                label: "知识库",
                icon: BookOpen,
                accent: ACCENTS.knowledge,
                active: location.pathname.startsWith("/admin/kb"),
              },
            ],
          },
        ]
      : []),
  ];

  return (
    <div className="flex min-h-[100dvh]">
      <Sidebar
        title="薄弱点分析"
        subtitle="校务台"
        navLabel="校务台导航"
        layoutId="admin-side-nav"
        groups={groups}
        footer={
          <div className="flex flex-col items-center gap-1.5 md:items-stretch">
            {/* 返回链：rail 收成图标。注：标签不可写 `${...} hidden`——本项目
                Tailwind 排序里 .inline-flex 系在 .hidden 之后，裸 display 类会
                顶掉 hidden，必须用响应式变体（hidden md:inline）在外层收放。 */}
            <Link
              to={backTo}
              aria-label="返回教学工作台"
              title="返回教学工作台"
              className="flex items-center justify-center gap-1.5 rounded-lg px-2 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink md:justify-start"
            >
              <ArrowLeft size={15} className="shrink-0" />
              <span className="hidden md:inline">教学工作台</span>
            </Link>
            {session && <AccountCluster session={session} name={session.teacher?.name ?? ""} />}
          </div>
        }
      />
      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-[1200px] px-6 py-7">{children}</div>
      </main>
    </div>
  );
}
