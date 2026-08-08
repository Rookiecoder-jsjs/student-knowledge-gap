import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { Shell } from "./components/Shell";
import ClassPicker from "./pages/ClassPicker";
import Collect from "./pages/Collect";
import Diagnosis from "./pages/Diagnosis";
import ExamNew from "./pages/ExamNew";
import Exams from "./pages/Exams";
import Kb from "./pages/Kb";
import Mastery from "./pages/Mastery";
import Overview from "./pages/Overview";
import Quality from "./pages/Quality";
import Review from "./pages/Review";
import Students from "./pages/Students";
import Wizard from "./pages/Wizard";

/** 有机减速曲线（更柔的 ease-out）。 */
const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1];

function Animated({ children }: { children: ReactNode }) {
  const reduce = useReducedMotion();
  if (reduce) return <>{children}</>;
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.26, ease: EASE }}
    >
      {children}
    </motion.div>
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
        <Route path="/c/:classId/exams/:examId/review" element={<Shell><Animated><Review /></Animated></Shell>} />
        <Route path="/c/:classId/exams/:examId/collect" element={<Shell><Animated><Collect /></Animated></Shell>} />
        <Route path="/c/:classId/quality" element={<Shell><Animated><Quality /></Animated></Shell>} />
        <Route path="/c/:classId/students" element={<Shell><Animated><Students /></Animated></Shell>} />
        <Route path="/c/:classId/students/:studentId/diagnosis" element={<Shell><Animated><Diagnosis /></Animated></Shell>} />
        <Route path="/c/:classId/students/:studentId/mastery" element={<Shell><Animated><Mastery /></Animated></Shell>} />
      </Routes>
    </AnimatePresence>
  );
}
