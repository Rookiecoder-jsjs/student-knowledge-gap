import {
  ArrowLeft,
  BookOpen,
  Exam,
  House,
  Student,
  TreeStructure,
} from "@phosphor-icons/react";
import { motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate, useParams } from "react-router-dom";
import { listClasses } from "../lib/api";
import { useAsync } from "../lib/hooks";

const NAV = [
  { to: "", end: true, label: "工作台", icon: House },
  { to: "/exams", label: "考试", icon: Exam },
  { to: "/students", label: "学生", icon: Student },
];

/** 利落缓动（案头 ease-out）。 */
const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

export function Shell({ children }: { children: ReactNode }) {
  const { classId } = useParams();
  const base = `/c/${classId}`;
  const cid = Number(classId) || 0;
  const nav = useNavigate();
  const location = useLocation();
  const reduce = useReducedMotion();
  const classes = useAsync(() => listClasses(), []);
  const currentName = classes.data?.classes.find((c) => c.class_id === cid)?.name;

  // 学生详情页（诊断/掌握）时，面包屑补一层「学生列表」
  const onStudentDetail = /\/c\/\d+\/students\/\d+/.test(location.pathname);
  // 考试工作区内时，面包屑补一层「考试」
  const inExam = /\/c\/\d+\/exams\/\d+/.test(location.pathname);

  return (
    <div className="flex min-h-[100dvh]">
      <aside className="fixed inset-y-0 left-0 z-10 flex w-52 flex-col border-r border-line bg-surface/85 backdrop-blur-sm">
        <Link
          to="/"
          className="flex items-center gap-2.5 px-4 pb-5 pt-5 transition-opacity hover:opacity-80"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-white shadow-soft">
            <TreeStructure size={17} weight="bold" />
          </span>
          <div>
            <p className="text-[13px] font-semibold leading-tight tracking-tight">薄弱点分析</p>
            <p className="text-[11px] text-ink-faint">教师工作台</p>
          </div>
        </Link>
        <nav className="flex flex-col gap-0.5 px-2.5">
          {NAV.map(({ to, end, label, icon: Icon }) => (
            <NavLink key={to} to={`${base}${to}`} end={end} className="relative">
              {({ isActive }) => (
                <span
                  className={`relative z-10 flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors ${
                    isActive ? "text-accent-deep" : "text-ink-soft hover:bg-surface-2 hover:text-ink"
                  }`}
                >
                  {isActive && !reduce && (
                    <motion.span
                      layoutId="nav-active"
                      className="absolute inset-0 rounded-lg bg-accent-soft"
                      transition={{ type: "spring", stiffness: 320, damping: 30, ease: EASE }}
                    />
                  )}
                  {isActive && reduce && (
                    <span className="absolute inset-0 rounded-lg bg-accent-soft" />
                  )}
                  <Icon size={16} weight={isActive ? "fill" : "regular"} />
                  <span className="relative">{label}</span>
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-3 px-3 pb-4">
          <Link
            to="/kb"
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <BookOpen size={16} />
            知识库
          </Link>
          <p className="px-1 text-[11px] leading-relaxed text-ink-faint">
            所有结论均可查看依据；
            <br />
            教师拥有最终否决权。
          </p>
        </div>
      </aside>
      <main className="ml-52 min-w-0 flex-1 px-8 py-7">
        <div className="mx-auto max-w-[1200px]">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
            <nav className="flex items-center gap-2 text-sm text-ink-soft" aria-label="面包屑">
              <Link
                to="/"
                className="inline-flex items-center gap-1 transition-colors hover:text-accent"
              >
                <ArrowLeft size={15} />
                所有班级
              </Link>
              {inExam && (
                <>
                  <span className="text-ink-faint">/</span>
                  <Link to={`${base}/exams`} className="transition-colors hover:text-accent">
                    考试
                  </Link>
                </>
              )}
              {onStudentDetail && (
                <>
                  <span className="text-ink-faint">/</span>
                  <Link to={`${base}/students`} className="transition-colors hover:text-accent">
                    学生列表
                  </Link>
                </>
              )}
            </nav>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-ink">{currentName ?? "班级"}</span>
              {(classes.data?.classes ?? []).length > 0 && (
                <select
                  value={cid}
                  onChange={(e) => nav(`/c/${e.target.value}`)}
                  className="rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm transition-colors focus:border-accent"
                  aria-label="切换班级"
                >
                  {classes.data?.classes.map((c) => (
                    <option key={c.class_id} value={c.class_id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}

export function PageIcon() {
  return <BookOpen size={18} />;
}
