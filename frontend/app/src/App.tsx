import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { ExamWorkspace } from "./components/ExamWorkspace";
import { Shell } from "./components/Shell";
import ClassPicker from "./pages/ClassPicker";
import Collect from "./pages/Collect";
import CommitView from "./pages/CommitView";
import Diagnosis from "./pages/Diagnosis";
import ExamNew from "./pages/ExamNew";
import Exams from "./pages/Exams";
import Kb from "./pages/Kb";
import Mastery from "./pages/Mastery";
import Overview from "./pages/Overview";
import Quality from "./pages/Quality";
import ExamBrief from "./pages/ExamBrief";
import Review from "./pages/Review";
import Students from "./pages/Students";
import TemplateView from "./pages/TemplateView";
import Wizard from "./pages/Wizard";

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

export default function App() {
  const location = useLocation();
  return (
    <AnimatePresence mode="wait">
      <Routes location={location} key={location.pathname}>
        <Route path="/" element={<Animated><ClassPicker /></Animated>} />
        <Route path="/wizard" element={<Animated><Wizard /></Animated>} />
        <Route path="/kb" element={<Animated><Kb /></Animated>} />

        <Route path="/c/:classId" element={<Shell><Animated><Overview /></Animated></Shell>} />
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
    </AnimatePresence>
  );
}
