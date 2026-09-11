import {
  BookOpen,
  Buildings,
  ChatCircleDots,
  Exam,
  House,
  Student,
  Tray,
} from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams } from "react-router-dom";
import { inboxSummary, listClasses, type InboxSummary as InboxSummaryData } from "../lib/api";
import { LAST_CLASS_KEY } from "../lib/auth";
import { useAuth } from "../lib/AuthContext";
import { useAsync } from "../lib/hooks";
import { roleFlags } from "../lib/portal";
import { ACCENTS } from "../lib/theme";
import { AccountCluster, TOOL_LINK, TopBar, TopBarNav } from "./TopBar";

/** 顶部导航：3 个模块，激活态 = 各自模块色胶囊（颜色即位置）。 */
const NAV = [
  { to: "", label: "工作台", icon: House, accent: ACCENTS.dashboard },
  { to: "/exams", label: "考试", icon: Exam, accent: ACCENTS.exam },
  { to: "/students", label: "学生", icon: Student, accent: ACCENTS.student },
];

export function Shell({ children }: { children: ReactNode }) {
  const { classId } = useParams();
  const nav = useNavigate();
  const location = useLocation();
  const classes = useAsync(() => listClasses(), []);
  // 班级作用域：/c/:classId 页面取路由参数；全局页（待签发/AI 教研员/知识库/校务台）
  // 没有该参数——不回落的话导航会拼出 /c/undefined，落到班级页后 Number() 得 NaN，
  // 触发 /classes/NaN/* 的 422。回落顺序：路由参数 → 上次访问班级 → 列表首个 → 0。
  const routed = Number(classId);
  const routedOk = Number.isInteger(routed) && routed > 0;
  let remembered = 0;
  try {
    remembered = Number(localStorage.getItem(LAST_CLASS_KEY));
  } catch {
    /* 隐私模式等：读不到就走首班回落 */
  }
  const rememberedOk =
    Number.isInteger(remembered) &&
    remembered > 0 &&
    (classes.data?.classes.some((c) => c.class_id === remembered) ?? false);
  const cid = routedOk
    ? routed
    : rememberedOk
      ? remembered
      : (classes.data?.classes[0]?.class_id ?? 0);
  const base = `/c/${cid}`;
  const currentName = classes.data?.classes.find((c) => c.class_id === cid)?.name;

  // 端边界（frontend-ends-design §一）：角色由 session 派生，开放模式 null 会话
  // 视为 bootstrap 信任域（教师语义 + 用量可见）；admin 才见管理分区。
  const { session } = useAuth();
  const flags = roleFlags(session);
  const roleLabel = session
    ? session.role === "admin"
      ? "管理员"
      : session.role === "student"
        ? "学生"
        : "教师"
    : "开放模式";
  const accountName = session?.teacher?.name ?? "";

  // 待签发角标：进入页面与路由切换时刷新（§4.3 收件箱入口）
  const [draftCount, setDraftCount] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    inboxSummary()
      .then((s: InboxSummaryData) => {
        if (alive) setDraftCount(s.draft);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [location.pathname]);

  // 记住上次访问的班级（仅显式 /c/:classId 路由；回落值不覆盖）
  useEffect(() => {
    if (routedOk) {
      try {
        localStorage.setItem(LAST_CLASS_KEY, String(routed));
      } catch {
        /* 隐私模式等：不记忆即可 */
      }
    }
  }, [routed, routedOk]);

  // 手动计算激活态：考试模块也涵盖 /quality 直达入口
  const path = location.pathname;
  // 无可用班级时（cid=0）班级簇不指向 /c/0，退回班级选择页
  const scopeTo = (to: string) => (cid > 0 ? `${base}${to}` : "/");
  const isActiveFor = (to: string) => {
    if (cid <= 0) return false;
    if (to === "") return path === base || path === `${base}/`;
    if (to === "/exams")
      return path.startsWith(`${base}/exams`) || path.startsWith(`${base}/quality`);
    return path.startsWith(`${base}${to}`);
  };

  // 班级作用域守卫：URL 带 /c/ 前缀但班级 id 非法（手敲 /c/undefined、/c/1.5），
  // 直接回班级选择——不渲染子树，避免各班级页把 "NaN" 当路径参数发出去吃 422。
  // 全局页（待签发/AI 教研员/知识库/校务台）无 /c/ 前缀，不受影响。
  if (location.pathname.startsWith("/c/") && !routedOk) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="flex min-h-[100dvh] flex-col">
      {/* 统一顶栏骨架（TopBar）：品牌左 · 主导航中 · 工具/账号右。
          窄屏：品牌收成图标、班级下拉隐藏（经品牌→班级概览切换），给主导航让位 */}
      <TopBar
        title={<span className="hidden sm:inline">薄弱点分析</span>}
        subtitle={<span className="hidden sm:block">教师工作台</span>}
        nav={
          <TopBarNav
            navLabel="主导航"
            layoutId="shell-nav"
            items={NAV.map(({ to, label, icon, accent }) => ({
              to: scopeTo(to),
              label,
              icon,
              accent,
              active: isActiveFor(to),
            }))}
          />
        }
        right={
          <>
            {/* 全局工具簇：教师/管理员 + 开放模式；窄屏收成 icon-only（防溢出） */}
            {flags.isStaff && (
              <Link to="/inbox" className={`${TOOL_LINK} relative`}>
                <Tray size={15} />
                <span className="hidden sm:inline">待签发</span>
                {(draftCount ?? 0) > 0 && (
                  <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-semibold text-white">
                    {draftCount}
                  </span>
                )}
              </Link>
            )}
            {flags.assistantVisible && (
              <Link to="/assistant" className={TOOL_LINK}>
                <ChatCircleDots size={15} />
                <span className="hidden sm:inline">AI 教研员</span>
              </Link>
            )}
            <Link to="/kb" className={TOOL_LINK}>
              <BookOpen size={15} />
              <span className="hidden sm:inline">知识库</span>
            </Link>

            {/* 校务台入口（分区重设计）：admin 登录与开放模式可见，普通教师不见 */}
            {flags.adminOrOpen && (
              <Link to="/admin" className={TOOL_LINK}>
                <Buildings size={15} />
                <span className="hidden sm:inline">校务台</span>
              </Link>
            )}

            {(classes.data?.classes ?? []).length > 0 && (
              <select
                name="class-switch"
                value={cid}
                onChange={(e) => nav(`/c/${e.target.value}`)}
                className="ml-1 hidden rounded-full border border-line-strong bg-surface px-3 py-1.5 text-sm transition-colors focus:border-accent sm:block"
                aria-label="切换班级"
              >
                {classes.data?.classes.map((c) => (
                  <option key={c.class_id} value={c.class_id}>
                    {c.name}
                  </option>
                ))}
              </select>
            )}
            {currentName && !classes.loading && (
              <span className="hidden text-sm font-semibold text-ink lg:inline">
                {currentName}
              </span>
            )}
            {session && <AccountCluster session={session} name={accountName || roleLabel} />}
          </>
        }
      />

      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-[1200px] px-6 py-7">{children}</div>
      </main>
    </div>
  );
}
