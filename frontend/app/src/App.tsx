import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { ExamWorkspace } from "./components/ExamWorkspace";
import { AdminShell } from "./components/AdminShell";
import { Shell } from "./components/Shell";
import { ApiError, listClasses } from "./lib/api";
import { useAuth } from "./lib/AuthContext";
import { landingFor } from "./lib/portal";
import { Guard, useRoleFlags } from "./components/PortalGuard";
import ClassPicker from "./pages/ClassPicker";
import Assistant from "./pages/Assistant";
import Collect from "./pages/Collect";
import CommitView from "./pages/CommitView";
import Diagnosis from "./pages/Diagnosis";
import ExamNew from "./pages/ExamNew";
import Exams from "./pages/Exams";
import Inbox from "./pages/Inbox";
import Kb from "./pages/Kb";
import Login from "./pages/Login";
import Mastery from "./pages/Mastery";
import Overview from "./pages/Overview";
import Quality from "./pages/Quality";
import ExamBrief from "./pages/ExamBrief";
import Review from "./pages/Review";
import StudentPortal from "./pages/StudentPortal";
import Students from "./pages/Students";
import TemplateView from "./pages/TemplateView";
import Usage from "./pages/Usage";
import Wizard from "./pages/Wizard";
import KbCreate from "./pages/KbCreate";
import Accounts from "./pages/admin/Accounts";
import KbPanel from "./pages/admin/KbPanel";

/** 利落减速曲线（案头 ease-out）。 */
const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

function Animated({ children }: { children: ReactNode }) {
  const reduce = useReducedMotion();
  if (reduce) return <>{children}</>;
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18, ease: EASE }}
    >
      {children}
    </motion.div>
  );
}

/** 考试工作区阶段路由：stepper + 阶段面板。 */
function ExamStage({ stage, children }: { stage: number; children: ReactNode }) {
  return (
    <Shell>
      <Animated>
        <ExamWorkspace stage={stage}>{children}</ExamWorkspace>
      </Animated>
    </Shell>
  );
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

/** 校务台落地（分区重设计）：admin 登录 → 账号页；开放模式（匿名演示）→ 用量页。 */
function AdminHome() {
  const flags = useRoleFlags();
  if (flags.adminLogin) return <Navigate to="/admin/accounts" replace />;
  if (flags.adminOrOpen) return <Navigate to="/admin/usage" replace />;
  return <Navigate to="/" replace />;
}

/**
 * 教师/管理端路由树（frontend-ends-design 教师端 + 校务台独立壳）。
 * 开放模式匿名也走此树（bootstrap 信任域，旗标见 lib/portal roleFlags）。
 */
function TeacherRoutes() {
  const flags = useRoleFlags();
  const location = useLocation();
  return (
    <AnimatePresence mode="wait">
      <Routes location={location} key={location.pathname}>
        <Route path="/" element={<Animated><ClassPicker /></Animated>} />
        <Route path="/wizard" element={<Animated><Wizard /></Animated>} />
        {/* 知识库两页套 Shell（导航一致性 2026-09-11）：侧栏「知识库」项与
            待签发/AI 教研员同为侧栏导航目的地，须保持侧栏 + 激活胶囊连续，
            不再整页换壳；页内返回链（从哪进去回哪）保留 */}
        <Route path="/kb" element={<Shell><Animated><Kb /></Animated></Shell>} />
        <Route path="/kb/new" element={<Shell><Animated><KbCreate /></Animated></Shell>} />

        <Route path="/c/:classId" element={<Shell><Animated><Overview /></Animated></Shell>} />
        <Route path="/inbox" element={<Shell><Animated><Inbox /></Animated></Shell>} />
        <Route path="/assistant" element={
          <Guard show={flags.assistantVisible}>
            <Shell><Animated><Assistant /></Animated></Shell>
          </Guard>
        } />

        <Route path="/c/:classId/exams" element={<Shell><Animated><Exams /></Animated></Shell>} />
        <Route path="/c/:classId/exams/new" element={<Shell><Animated><ExamNew /></Animated></Shell>} />

        {/* 考试工作区：5 阶流水线 */}
        <Route path="/c/:classId/exams/:examId" element={<ExamStage stage={1}><TemplateView /></ExamStage>} />
        <Route path="/c/:classId/exams/:examId/review" element={<ExamStage stage={2}><Review /></ExamStage>} />
        <Route path="/c/:classId/exams/:examId/collect" element={<ExamStage stage={3}><Collect /></ExamStage>} />
        <Route path="/c/:classId/exams/:examId/commit" element={<ExamStage stage={4}><CommitView /></ExamStage>} />
        <Route path="/c/:classId/exams/:examId/report" element={<ExamStage stage={5}><ExamBrief /></ExamStage>} />

        {/* 质量分析直达入口（不在工作区内，自带考试选择器） */}
        <Route path="/c/:classId/quality" element={<Shell><Animated><Quality /></Animated></Shell>} />

        <Route path="/c/:classId/students" element={<Shell><Animated><Students /></Animated></Shell>} />
        <Route path="/c/:classId/students/:studentId/diagnosis" element={<Shell><Animated><Diagnosis /></Animated></Shell>} />
        <Route path="/c/:classId/students/:studentId/mastery" element={<Shell><Animated><Mastery /></Animated></Shell>} />

        {/* 校务台（分区重设计 2026-09-10）：独立壳，与教学 Shell 平级。
            admin 登录或开放模式（演示信任域）挂载；纯校管默认落这里 */}
        {flags.adminOrOpen && (
          <Route path="/admin" element={<AdminHome />} />
        )}
        {flags.adminLogin && (
          <Route path="/admin/accounts" element={<AdminShell><Animated><Accounts /></Animated></AdminShell>} />
        )}
        {/* KB 总面板（rbac-scopes-design §6）：admin/开放 ∨ 学科管理员 ∨ kb_editor */}
        {flags.kbPanelVisible && (
          <Route path="/admin/kb" element={<AdminShell><Animated><KbPanel /></Animated></AdminShell>} />
        )}
        {flags.adminOrOpen && (
          <Route path="/admin/usage" element={<AdminShell><Animated><Usage /></Animated></AdminShell>} />
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
    </AnimatePresence>
  );
}

/** 认证模式探测：登录态 null 时判断是「安全模式需登录」还是「开放模式匿名可用」。 */
function usePortalGate() {
  const { session } = useAuth();
  const [mode, setMode] = useState<"loading" | "open" | "secure" | "error">("loading");
  const [probe, setProbe] = useState(0);

  useEffect(() => {
    if (session !== undefined && session !== null) {
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

export default function App() {
  const { session, rebootstrap } = useAuth();
  const { mode, retry } = usePortalGate();

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
    return <Login />;
  }
  // 学生 → 独立门户树；教师/admin 登录态、或开放模式匿名 → 教师/管理树
  return session?.role === "student" ? <StudentRoutes /> : <TeacherRoutes />;
}
