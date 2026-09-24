import { Component, Fragment, lazy, Suspense, useCallback, useEffect, useState, type ErrorInfo, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { ExamWorkspace } from "./components/ExamWorkspace";
import { Shell } from "./components/Shell";
import { ApiError, listClasses } from "./lib/api";
import { useAuth } from "./lib/AuthContext";
import { watchSystemTheme } from "./lib/theme-mode";
import { landingFor } from "./lib/portal";
import { Guard, useRoleFlags } from "./components/PortalGuard";
const ClassPicker = lazy(() => import("./pages/ClassPicker"));
const Assistant = lazy(() => import("./pages/Assistant"));
const Collect = lazy(() => import("./pages/Collect"));
const CommitView = lazy(() => import("./pages/CommitView"));
const Diagnosis = lazy(() => import("./pages/Diagnosis"));
const ExamNew = lazy(() => import("./pages/ExamNew"));
const Exams = lazy(() => import("./pages/Exams"));
const Inbox = lazy(() => import("./pages/Inbox"));
const Kb = lazy(() => import("./pages/Kb"));
const Login = lazy(() => import("./pages/Login"));
const Mastery = lazy(() => import("./pages/Mastery"));
const Overview = lazy(() => import("./pages/Overview"));
const Quality = lazy(() => import("./pages/Quality"));
const ExamBrief = lazy(() => import("./pages/ExamBrief"));
const Review = lazy(() => import("./pages/Review"));
const StudentPortal = lazy(() => import("./pages/StudentPortal"));
const Students = lazy(() => import("./pages/Students"));
const TemplateView = lazy(() => import("./pages/TemplateView"));
const Usage = lazy(() => import("./pages/Usage"));
const Wizard = lazy(() => import("./pages/Wizard"));
const KbNew = lazy(() => import("./pages/KbNew"));
const Accounts = lazy(() => import("./pages/admin/Accounts"));
const KbPanel = lazy(() => import("./pages/admin/KbPanel"));

function AppFallback() {
  return (
    <div className="flex min-h-[100dvh] items-center justify-center" role="status">
      <span className="h-8 w-8 animate-spin rounded-full border-2 border-accent/20 border-t-accent" />
      <span className="sr-only">页面加载中</span>
    </div>
  );
}

interface AppErrorBoundaryState {
  error: Error | null;
}

/** 路由页渲染异常兜底：避免单个组件错误把整站变成白屏，允许原地重试。 */
class AppErrorBoundary extends Component<{ children: ReactNode }, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 保留堆栈供本地开发定位；用户只看到可操作的错误态。
    console.error("页面渲染失败", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex min-h-[100dvh] items-center justify-center px-6">
        <div className="max-w-md text-center" role="alert">
          <p className="text-sm font-semibold text-ink">页面暂时无法显示</p>
          <p className="mt-2 text-xs leading-relaxed text-ink-faint">
            页面遇到意外错误，请重试；如果问题持续，请联系管理员。
          </p>
          <button
            className="mt-4 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-deep active:scale-[0.98]"
            onClick={() => this.setState({ error: null })}
          >
            重试
          </button>
        </div>
      </div>
    );
  }
}

/** 页面代码首次按需加载时只占位内容区，避免 Shell/侧栏被 Suspense 替换。 */
function Deferred({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={(
        <div className="flex min-h-[45vh] items-center justify-center" role="status">
          <span className="h-7 w-7 animate-spin rounded-full border-2 border-accent/20 border-t-accent" />
          <span className="sr-only">页面加载中</span>
        </div>
      )}
    >
      {children}
    </Suspense>
  );
}

/** 包豪斯静帧：保留页面随路径重置的语义，侧栏保持挂载。 */
function Animated({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return <Fragment key={pathname}>{children}</Fragment>;
}

/**
 * 学生门户树（frontend-ends-design 学生端）：/portal 子路径，
 * 教师/管理路由一律不挂载——学生敲教师 URL → 兜底重定向门户首页。
 */
function StudentRoutes() {
  return (
    <Routes>
      <Route path="/portal" element={<StudentPortal source={{ kind: "self" }} />} />
      <Route path="/portal/study" element={<StudentPortal source={{ kind: "self" }} />} />
      <Route path="/portal/mastery" element={<StudentPortal source={{ kind: "self" }} />} />
      <Route path="/portal/reports" element={<StudentPortal source={{ kind: "self" }} />} />
      <Route path="/portal/plan" element={<StudentPortal source={{ kind: "self" }} />} />
      <Route path="*" element={<StudentFallback />} />
    </Routes>
  );
}

/**
 * 兜底重定向（会话感知，端进出修订竞态修复）：`*` 不盲目回首页，而是按
 * landingFor 计算落地区。背景：登录后 session（紧急更新）先于 location
 * （startTransition）渲染——中间渲染「新会话 + 旧 location」时，盲目回 `/`
 * 的 `*` 会把 Login 的落地导航顶掉（实测：登出停在 /portal 后登录纯校管，
 * 应落 /admin 却落了 /）。兜底与新会话自洽后，中间渲染自然落到正确地区。
 */
function StaffFallback() {
  const session = useAuth().session;
  const location = useLocation();
  return <Navigate to={landingFor(session ?? null, location.pathname) ?? "/"} replace />;
}

function StudentFallback() {
  const session = useAuth().session;
  const location = useLocation();
  return <Navigate to={landingFor(session ?? null, location.pathname) ?? "/portal"} replace />;
}

/**
 * 管理员学生门户预览（超级账号）：从学生列表进入，读 `/admin/students/{id}/portal/*`
 * 只读镜像（与 /me 同源同形状）。不是切换身份——admin 始终是 admin。
 */
function StudentPortalPreview() {
  const { classId, studentId } = useParams();
  return (
    <StudentPortal
      source={{
        kind: "preview",
        studentId: Number(studentId),
        base: `/c/${classId}/students/${studentId}/portal`,
      }}
    />
  );
}

/** 全局管理落地（side-nav §5）：admin 登录 → 账号管理；其余弹回班级概览。
 * 原独立校务台已并入教学壳侧栏（仅超管可见），开放模式不再有校级入口。 */
function AdminHome() {
  const flags = useRoleFlags();
  if (flags.adminLogin) return <Navigate to="/admin/accounts" replace />;
  return <Navigate to="/" replace />;
}

/**
 * 教师/管理端路由树（frontend-ends-design 教师端 + 校务台独立壳）。
 * 开放模式匿名也走此树（bootstrap 信任域，旗标见 lib/portal roleFlags）。
 */
function TeacherRoutes() {
  const flags = useRoleFlags();
  return (
    // 侧栏切换不整树重挂（2026-09-12 设计反馈排查）：此前外包的
    // <AnimatePresence mode="wait"> + <Routes key={pathname}> 会让 Shell（侧栏）
    // 随每次导航销毁重建——layoutId 激活胶囊永远无法滑动、班级列表/待签发角标
    // 重拉闪变，主区先淡出到全空再淡入（闪烁主体，总过渡时长翻倍）。Shell 现已
    // 跨路由持久化，页面内容由 Animated 静态直出。
    <Routes>
      <Route path="/" element={<Animated><ClassPicker /></Animated>} />
      <Route path="/wizard" element={<Animated><Wizard /></Animated>} />
      {/* 知识库两页套 Shell（导航一致性 2026-09-11）：侧栏「知识库」项与
          待签发/AI 教研员同为侧栏导航目的地，须保持侧栏 + 激活胶囊连续，
          不再整页换壳；页内返回链（从哪进去回哪）保留 */}
      <Route path="/kb" element={<Shell><Deferred><Animated><Kb /></Animated></Deferred></Shell>} />
      <Route path="/kb/new" element={<Shell><Deferred><Animated><KbNew /></Animated></Deferred></Shell>} />

      <Route path="/c/:classId" element={<Shell><Deferred><Animated><Overview /></Animated></Deferred></Shell>} />
      <Route path="/inbox" element={<Shell><Deferred><Animated><Inbox /></Animated></Deferred></Shell>} />
      {/* Guard 必须在 Shell 内层（2026-09-12 工具组瞬跳排查）：Guard 可见时虽是
          Fragment 透传，但 React 协调按元素类型比较——<Guard> ≠ <Shell>，套在
          外层会让 Shell 每次进出 /assistant 整体重挂（侧栏瞬跳+角标重拉） */}
      <Route path="/assistant" element={
        <Shell>
          <Deferred>
            <Guard show={flags.assistantVisible}>
              <Animated><Assistant /></Animated>
            </Guard>
          </Deferred>
        </Shell>
      } />

      <Route path="/c/:classId/exams" element={<Shell><Deferred><Animated><Exams /></Animated></Deferred></Shell>} />
      <Route path="/c/:classId/exams/new" element={<Shell><Deferred><Animated><ExamNew /></Animated></Deferred></Shell>} />

      {/* 考试工作区：5 阶流水线。路由元素根类型必须是 <Shell> 本体（2026-09-12 同类
          排查）：包一层 ExamStage 组件会让根类型 ≠ Shell，从考试列表进工作区时侧栏
          整体重挂（胶囊瞬跳+角标重拉）。Shell/Animated/ExamWorkspace 就地内联。 */}
      <Route path="/c/:classId/exams/:examId" element={<Shell><Deferred><Animated><ExamWorkspace stage={1}><TemplateView /></ExamWorkspace></Animated></Deferred></Shell>} />
      <Route path="/c/:classId/exams/:examId/review" element={<Shell><Deferred><Animated><ExamWorkspace stage={2}><Review /></ExamWorkspace></Animated></Deferred></Shell>} />
      <Route path="/c/:classId/exams/:examId/collect" element={<Shell><Deferred><Animated><ExamWorkspace stage={3}><Collect /></ExamWorkspace></Animated></Deferred></Shell>} />
      <Route path="/c/:classId/exams/:examId/commit" element={<Shell><Deferred><Animated><ExamWorkspace stage={4}><CommitView /></ExamWorkspace></Animated></Deferred></Shell>} />
      <Route path="/c/:classId/exams/:examId/report" element={<Shell><Deferred><Animated><ExamWorkspace stage={5}><ExamBrief /></ExamWorkspace></Animated></Deferred></Shell>} />

      {/* 质量分析直达入口（不在工作区内，自带考试选择器） */}
      <Route path="/c/:classId/quality" element={<Shell><Deferred><Animated><Quality /></Animated></Deferred></Shell>} />

      <Route path="/c/:classId/students" element={<Shell><Deferred><Animated><Students /></Animated></Deferred></Shell>} />
      <Route path="/c/:classId/students/:studentId/diagnosis" element={<Shell><Deferred><Animated><Diagnosis /></Animated></Deferred></Shell>} />
      <Route path="/c/:classId/students/:studentId/mastery" element={<Shell><Deferred><Animated><Mastery /></Animated></Deferred></Shell>} />

      {/* 全局管理（side-nav §5，2026-09-12 设计反馈）：原独立校务台三页并入
          教学 Shell——侧栏「全局管理」组直达，仅超管登录挂载（adminLogin）；
          学科管理员的 KB 编辑走 /kb 自页，不再有校级面板入口 */}
      {/* /admin 仅是落地重定向（→ /admin/accounts）：同样套 Shell（2026-09-12 同类
          排查）——重定向帧保住侧栏而非整屏空白，且根类型同为 Shell，跨跳转持久 */}
      {flags.adminLogin && (
        <Route path="/admin" element={<Shell><Animated><AdminHome /></Animated></Shell>} />
      )}
      {flags.adminLogin && (
        <Route path="/admin/usage" element={<Shell><Deferred><Animated><Usage /></Animated></Deferred></Shell>} />
      )}
      {flags.adminLogin && (
        <Route path="/admin/accounts" element={<Shell><Deferred><Animated><Accounts /></Animated></Deferred></Shell>} />
      )}
      {flags.adminLogin && (
        <Route path="/admin/kb" element={<Shell><Deferred><Animated><KbPanel /></Animated></Deferred></Shell>} />
      )}

      {/* 学生门户预览（超级账号，admin-only）：挂教学树学生语境（独立壳，不套 Shell） */}
      {flags.adminLogin && (
        <>
          <Route path="/c/:classId/students/:studentId/portal" element={<StudentPortalPreview />} />
          <Route path="/c/:classId/students/:studentId/portal/study" element={<StudentPortalPreview />} />
          <Route path="/c/:classId/students/:studentId/portal/mastery" element={<StudentPortalPreview />} />
          <Route path="/c/:classId/students/:studentId/portal/reports" element={<StudentPortalPreview />} />
          <Route path="/c/:classId/students/:studentId/portal/plan" element={<StudentPortalPreview />} />
        </>
      )}

      {/* 未匹配：教师/管理端敲未知路径或学生路径 → 按 landingFor 弹回落地区 */}
      <Route path="*" element={<StaffFallback />} />
    </Routes>
  );
}

/** 认证模式探测：登录态 null 时判断是「安全模式需登录」还是「开放模式匿名可用」。 */
function usePortalGate() {
  const { session } = useAuth();
  const [mode, setMode] = useState<"loading" | "open" | "secure" | "error">("loading");
  const [probe, setProbe] = useState(0);

  useEffect(() => {
    // 会话恢复完成前不要探测开放模式；否则 Shell 与页面挂载时会各自触发
    // 一次 /classes，产生无意义的 401 并延迟首屏。
    if (session === undefined) return;
    if (session !== null) {
      setMode("secure");
      return;
    }
    let alive = true;
    setMode("loading");
    listClasses()
      .then(() => alive && setMode("open"))
      .catch((e: ApiError) => alive && setMode(e.status === 401 ? "secure" : "error"));
    return () => {
      alive = false;
    };
  }, [session, probe]);

  return { mode, retry: useCallback(() => setProbe((p) => p + 1), []) };
}

function AppContent() {
  const { session, rebootstrap } = useAuth();
  const { mode, retry } = usePortalGate();

  // 主题跟随系统（saas-redesign §5）：首绘由 index.html boot 脚本定，
  // 此处只挂系统偏好变化监听（仅 system 模式生效）
  useEffect(() => watchSystemTheme(), []);

  if (session === undefined || mode === "loading") {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-accent/20 border-t-accent" aria-label="加载中" />
      </div>
    );
  }
  if (session === null && mode === "error") {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center px-6">
        <div className="max-w-md text-center">
          <p className="text-sm font-medium text-ink-soft">无法连接后端服务</p>
          <p className="mt-1 text-xs text-ink-faint">请确认 uvicorn 已启动后再试。</p>
          {/* 重试同时重跑连通探测与会话恢复（端进出修订）：瞬断后持有效 token
              的用户点一下即恢复身份，不必被迫重登 */}
          <button
            onClick={() => {
              retry();
              rebootstrap();
            }}
            className="mt-4 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
          >
            重试
          </button>
        </div>
      </div>
    );
  }
  if (session === null && mode === "secure") {
    return <Suspense fallback={<AppFallback />}><Login /></Suspense>;
  }
  // 学生 → 独立门户树；教师/admin 登录态、或开放模式匿名 → 教师/管理树
  return (
    <Suspense fallback={<AppFallback />}>
      {session?.role === "student" ? <StudentRoutes /> : <TeacherRoutes />}
    </Suspense>
  );
}

export default function App() {
  return (
    <AppErrorBoundary>
      <AppContent />
    </AppErrorBoundary>
  );
}
