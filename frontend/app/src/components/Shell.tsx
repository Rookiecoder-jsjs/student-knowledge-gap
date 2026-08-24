import { BookOpen, ChatCircleDots, Exam, House, Student, TreeStructure } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { listClasses } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { ACCENTS } from "../lib/theme";

/** 利落缓动（SaaS ease-out）。 */
const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

/** 顶部导航：3 个模块，激活态 = 各自模块色胶囊（颜色即位置）。 */
const NAV = [
  { to: "", label: "工作台", icon: House, accent: ACCENTS.dashboard },
  { to: "/exams", label: "考试", icon: Exam, accent: ACCENTS.exam },
  { to: "/students", label: "学生", icon: Student, accent: ACCENTS.student },
];

export function Shell({ children }: { children: ReactNode }) {
  const { classId } = useParams();
  const base = `/c/${classId}`;
  const cid = Number(classId) || 0;
  const nav = useNavigate();
  const location = useLocation();
  const reduce = useReducedMotion();
  const classes = useAsync(() => listClasses(), []);
  const currentName = classes.data?.classes.find((c) => c.class_id === cid)?.name;

  // 手动计算激活态：考试模块也涵盖 /quality 直达入口
  const path = location.pathname;
  const isActiveFor = (to: string) => {
    if (to === "") return path === base || path === `${base}/`;
    if (to === "/exams")
      return path.startsWith(`${base}/exams`) || path.startsWith(`${base}/quality`);
    return path.startsWith(`${base}${to}`);
  };

  return (
    <div className="flex min-h-[100dvh] flex-col">
      {/* 顶部导航：毛玻璃 + 模块色胶囊 */}
      <header className="sticky top-0 z-40 border-b border-line/70 bg-surface/80 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-[1200px] items-center justify-between gap-4 px-6">
          <Link
            to="/"
            className="flex shrink-0 items-center gap-2.5 transition-opacity hover:opacity-80"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent text-white shadow-[0_4px_14px_-2px] shadow-accent/50">
              <TreeStructure size={18} weight="bold" />
            </span>
            <div className="leading-tight">
              <p className="text-sm font-semibold tracking-tight">薄弱点分析</p>
              <p className="text-[11px] text-ink-faint">教师工作台</p>
            </div>
          </Link>

          <nav className="flex items-center gap-1" aria-label="主导航">
            {NAV.map(({ to, label, icon: Icon, accent }) => {
              const active = isActiveFor(to);
              const pill = (
                <span
                  className="absolute inset-0 rounded-full"
                  style={{ background: accent }}
                  aria-hidden
                />
              );
              return (
                <Link
                  key={to}
                  to={`${base}${to}`}
                  aria-current={active ? "page" : undefined}
                  className={`relative flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition-colors ${
                    active ? "text-white" : "text-ink-soft hover:text-ink"
                  }`}
                >
                  {active && !reduce && (
                    <motion.span
                      layoutId="nav-active"
                      className="absolute inset-0 rounded-full"
                      style={{ background: accent }}
                      transition={{ type: "spring", stiffness: 320, damping: 30, ease: EASE }}
                      aria-hidden
                    />
                  )}
                  {active && reduce && pill}
                  <Icon size={16} weight={active ? "fill" : "regular"} className="relative" />
                  <span className="relative">{label}</span>
                </Link>
              );
            })}
          </nav>

          <div className="flex shrink-0 items-center gap-3">
            <Link
              to="/assistant"
              className="inline-flex items-center gap-1.5 rounded-full px-3 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink"
            >
              <ChatCircleDots size={15} />
              AI 教研员
            </Link>
            <Link
              to="/kb"
              className="inline-flex items-center gap-1.5 rounded-full px-3 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink"
            >
              <BookOpen size={15} />
              知识库
            </Link>
            {(classes.data?.classes ?? []).length > 0 && (
              <select
                name="class-switch"
                value={cid}
                onChange={(e) => nav(`/c/${e.target.value}`)}
                className="rounded-full border border-line-strong bg-surface px-3 py-1.5 text-sm transition-colors focus:border-accent"
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
          </div>
        </div>
      </header>

      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-[1200px] px-6 py-7">{children}</div>
      </main>
    </div>
  );
}
