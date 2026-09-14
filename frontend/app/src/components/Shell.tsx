import {
  BookOpen,
  ChartBar,
  ChatCircleDots,
  Exam,
  House,
  List,
  Student,
  Tray,
  TreeStructure,
  UserGear,
} from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation, useNavigate, useParams } from "react-router-dom";
import { inboxSummary, listClasses, type InboxSummary as InboxSummaryData } from "../lib/api";
import { LAST_CLASS_KEY } from "../lib/auth";
import { useAuth } from "../lib/AuthContext";
import { useAsync } from "../lib/hooks";
import { roleFlags } from "../lib/portal";
import { PRODUCT_NAME } from "../lib/site";
import { ACCENTS } from "../lib/theme";
import { Select } from "./ui";
import { AccountCluster } from "./TopBar";
import { Sidebar, type SideNavGroup, type SideNavItem } from "./SideNav";

/** 主导航三项（班级作用域）：to 为解析前原始路由，渲染时经 scopeTo 解析。 */
const NAV = [
  { to: "", label: "工作台", icon: House, accent: ACCENTS.dashboard },
  { to: "/exams", label: "考试", icon: Exam, accent: ACCENTS.exam },
  { to: "/students", label: "学生", icon: Student, accent: ACCENTS.student },
];

/**
 * 教师工作台壳（side-nav-redesign 2026-09-11）：顶栏 tab 迁左成侧栏菜单，
 * 三组分区——班级（工作台/考试/学生）· 工具（待签发/AI 教研员/知识库）·
 * 管理（校务台）。品牌+班级切换器在侧栏顶部，账号簇沉底，无顶栏、页面全高。
 * 激活态颜色即位置：胶囊色 = 落点页 Page accent。
 */
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
  const [draftError, setDraftError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setDraftError(null);
    inboxSummary()
      .then((s: InboxSummaryData) => {
        if (alive) setDraftCount(s.draft);
      })
      .catch((e: unknown) => {
        if (alive) setDraftError(e instanceof Error ? e.message : "加载失败");
      });
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

  // <md 抽屉开合（saas-redesign §6）：菜单条打开，导航点击/遮罩/路由切换收起
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

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

  // 侧栏语境块：班级切换器（Select 原语，design-style §4 禁原生 select）
  const hasClasses = (classes.data?.classes ?? []).length > 0;
  const classContext = hasClasses ? (
    <Select
      name="class-switch"
      value={cid}
      onChange={(e) => nav(`/c/${e.target.value}`)}
      className="w-full"
      aria-label="切换班级"
    >
      {classes.data?.classes.map((c) => (
        <option key={c.class_id} value={c.class_id}>
          {c.name}
        </option>
      ))}
    </Select>
  ) : null;

  // 待签发角标：绝对定位于项右上角（rail 下盖在图标角上）
  const inboxBadge =
    (draftCount ?? 0) > 0 ? (
      <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-semibold text-white">
        {draftCount}
      </span>
    ) : draftError ? (
      <span
        className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-warn px-1 text-[10px] font-semibold text-white"
        title="待签发数量加载失败"
        aria-label="待签发数量加载失败"
      >
        !
      </span>
    ) : undefined;

  // 侧栏分组（side-nav-redesign §1 + §5）：激活项颜色即位置。
  // 组可见性由角色旗标派生：isStaff/assistantVisible（工具组）、
  // adminLogin（全局管理组——仅超管登录）。
  const groups: SideNavGroup[] = [
    {
      label: "班级",
      items: NAV.map(({ to, label, icon, accent }) => ({
        // id=解析前的原始路由：跨 cid 恒定，避免 classes 未加载时 key 全为
        // "/"（重复 key → React 协调留孤儿 → 导航链接翻倍，2026-09-11 实锤）
        id: to,
        to: scopeTo(to),
        label,
        icon,
        accent,
        active: isActiveFor(to),
      })),
    },
    {
      label: "工具",
      items: (
        [
          ...(flags.isStaff
            ? [
                {
                  id: "/inbox",
                  to: "/inbox",
                  label: "待签发",
                  icon: Tray,
                  accent: ACCENTS.dashboard,
                  active: path.startsWith("/inbox"),
                  trailing: inboxBadge,
                },
              ]
            : []),
          ...(flags.assistantVisible
            ? [
                {
                  id: "/assistant",
                  to: "/assistant",
                  label: "AI 教研员",
                  icon: ChatCircleDots,
                  accent: ACCENTS.knowledge,
                  active: path.startsWith("/assistant"),
                },
              ]
            : []),
          {
            id: "/kb",
            to: "/kb",
            label: "知识库",
            icon: BookOpen,
            accent: ACCENTS.knowledge,
            active: path.startsWith("/kb"),
          },
        ] satisfies SideNavItem[]
      ),
    },
    // 全局管理（side-nav §5，2026-09-12 设计反馈）：原独立校务台（AdminShell）
    // 并入侧栏，仅超管登录可见——开放模式与学科管理员不再有校级入口。
    ...(flags.adminLogin
      ? [
          {
            label: "全局管理",
            items: [
              {
                id: "/admin/usage",
                to: "/admin/usage",
                label: "用量",
                icon: ChartBar,
                accent: ACCENTS.dashboard,
                active: path.startsWith("/admin/usage"),
              },
              {
                id: "/admin/accounts",
                to: "/admin/accounts",
                label: "账号管理",
                icon: UserGear,
                accent: ACCENTS.dashboard,
                active: path.startsWith("/admin/accounts"),
              },
              {
                id: "/admin/kb",
                to: "/admin/kb",
                label: "知识点管理",
                icon: TreeStructure,
                accent: ACCENTS.knowledge,
                active: path.startsWith("/admin/kb"),
              },
            ] satisfies SideNavItem[],
          },
        ]
      : []),
  ];

  return (
    <div className="flex min-h-[100dvh]">
      <Sidebar
        title={PRODUCT_NAME}
        subtitle="教师工作台"
        context={classContext}
        navLabel="主导航"
        layoutId="shell-side-nav"
        groups={groups}
        footer={
          session ? (
            <AccountCluster session={session} name={accountName || roleLabel} />
          ) : undefined
        }
        mobileOpen={navOpen}
        onMobileClose={() => setNavOpen(false)}
      />
      <div className="flex min-w-0 flex-1 flex-col [--shell-top:48px] md:[--shell-top:0px]">
        {/* 移动端顶条（<md）：菜单钮开抽屉；md+ 由侧栏接管 */}
        <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center gap-2 border-b border-line bg-surface/85 px-3 backdrop-blur md:hidden">
          <button
            onClick={() => setNavOpen(true)}
            aria-label="打开导航"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <List size={18} />
          </button>
          <span className="text-sm font-semibold tracking-tight">{PRODUCT_NAME}</span>
        </header>
        <main className="min-w-0 flex-1">
          {/* 全幅工作台（saas-redesign §6）：撤 max-w 居中，数据视图自然铺满；
              --shell-top 供 PageHeader 吸顶偏移（移动端让位顶条 48px） */}
          <div className="px-4 py-6 md:px-8 md:py-7">{children}</div>
        </main>
      </div>
      {/* 抽屉遮罩（<md） */}
      {navOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 md:hidden"
          onClick={() => setNavOpen(false)}
          aria-hidden
        />
      )}
    </div>
  );
}
