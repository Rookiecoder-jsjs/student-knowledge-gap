import {
  ArrowLeft,
  BookOpen,
  ChartLineUp,
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
  { to: "/exams", label: "考试录入", icon: Exam },
  { to: "/students", label: "学生诊断", icon: Student },
  { to: "/quality", label: "质量分析", icon: ChartLineUp },
];

/** 有机缓动（更柔的 ease-out）。 */
const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1];

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

  return (
    <div className="flex min-h-[100dvh]">
      <aside className="fixed inset-y-0 left-0 z-10 flex w-56 flex-col border-r border-line bg-surface/80 backdrop-blur-sm">
        <Link
          to="/"
          className="flex items-center gap-2.5 px-5 pb-6 pt-6 transition-opacity hover:opacity-80"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent text-white shadow-soft">
            <TreeStructure size={18} weight="bold" />
          </span>
          <div>
            <p className="text-sm font-semibold leading-tight tracking-tight">薄弱点分析</p>
            <p className="text-xs text-ink-faint">教师工作台</p>
          </div>
        </Link>
        <nav className="flex flex-col gap-1 px-3">
          {NAV.map(({ to, end, label, icon: Icon }) => (
            <NavLink key={to} to={`${base}${to}`} end={end} className="relative">
              {({ isActive }) => (
                <span
                  className={`relative z-10 flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors ${
                    isActive ? "text-accent-deep" : "text-ink-soft hover:bg-surface-2 hover:text-ink"
                  }`}
                >
                  {isActive && !reduce && (
                    <motion.span
                      layoutId="nav-active"
                      className="absolute inset-0 rounded-xl bg-accent-soft"
                      transition={{ type: "spring", stiffness: 240, damping: 26, ease: EASE }}
                    />
                  )}
                  {isActive && reduce && (
                    <span className="absolute inset-0 rounded-xl bg-accent-soft" />
                  )}
                  <Icon size={17} weight={isActive ? "fill" : "regular"} />
                  <span className="relative">{label}</span>
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-5 pb-5">
          <p className="text-xs leading-relaxed text-ink-faint">
            所有结论均可查看依据；
            <br />
            教师拥有最终否决权。
          </p>
        </div>
      </aside>
      <main className="ml-56 min-w-0 flex-1 px-9 py-8">
        <div className="mx-auto max-w-[1200px]">
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
            <nav className="flex items-center gap-2 text-sm text-ink-soft" aria-label="面包屑">
              <Link
                to="/"
                className="inline-flex items-center gap-1 transition-colors hover:text-accent"
              >
                <ArrowLeft size={15} />
                所有班级
              </Link>
              {onStudentDetail && (
                <>
                  <span className="text-ink-faint">/</span>
                  <Link
                    to={`${base}/students`}
                    className="transition-colors hover:text-accent"
                  >
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
                  className="rounded-xl border border-line bg-surface px-2.5 py-1.5 text-sm transition-colors focus:border-accent"
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
