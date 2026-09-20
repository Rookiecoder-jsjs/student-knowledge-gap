import type { ReactNode } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { ExamWorkspace } from "./components/ExamWorkspace";
import { Shell } from "./components/Shell";
import ClassPicker from "./pages/ClassPicker";
import Assistant from "./pages/Assistant";
import Collect from "./pages/Collect";
import CommitView from "./pages/CommitView";
import Diagnosis from "./pages/Diagnosis";
import ExamNew from "./pages/ExamNew";
import Exams from "./pages/Exams";
import Inbox from "./pages/Inbox";
import Kb from "./pages/Kb";
import Mastery from "./pages/Mastery";
import Overview from "./pages/Overview";
import Quality from "./pages/Quality";
import ExamBrief from "./pages/ExamBrief";
import Review from "./pages/Review";
import Students from "./pages/Students";
import TemplateView from "./pages/TemplateView";
import Usage from "./pages/Usage";
import Wizard from "./pages/Wizard";

/** 包豪斯静帧（方案 3 motion=still）：路由切换不再做进场/退场动画，保留挂载结构。 */
function Animated({ children }: { children: ReactNode }) {
  return <>{children}</>;
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

export default function App() {
  const location = useLocation();
  return (
    <Routes location={location} key={location.pathname}>
        <Route path="/" element={<Animated><ClassPicker /></Animated>} />
        <Route path="/wizard" element={<Animated><Wizard /></Animated>} />
        <Route path="/kb" element={<Animated><Kb /></Animated>} />

        <Route path="/c/:classId" element={<Shell><Animated><Overview /></Animated></Shell>} />
        <Route path="/assistant" element={<Shell><Animated><Assistant /></Animated></Shell>} />
        <Route path="/inbox" element={<Shell><Animated><Inbox /></Animated></Shell>} />
        <Route path="/usage" element={<Shell><Animated><Usage /></Animated></Shell>} />
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
      </Routes>
  );
}
