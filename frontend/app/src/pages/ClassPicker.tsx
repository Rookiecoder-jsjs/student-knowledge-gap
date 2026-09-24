import {
  ArrowRight,
  BookOpen,
  ClipboardText,
  PlusCircle,
} from "@phosphor-icons/react";
import { Link, useNavigate } from "react-router-dom";
import { useMemo } from "react";
import { AccountCluster, TOOL_LINK, TopBar } from "../components/TopBar";
import { ErrorState, Skeleton } from "../components/ui";
import { StaggerItem, StaggerList } from "../components/motion";
import { listClassesOverview } from "../lib/api";
import { useAuth } from "../lib/AuthContext";
import { useAsync } from "../lib/hooks";
import { PRODUCT_NAME } from "../lib/site";
import type { ClassOverview } from "../lib/types";
import { BrandMark } from "../components/BrandMark";

/** 一级页面·班级概览：横向对比所有班级的待办 / 最近考试 / 教学进度，点击进入单班工作台。
 * 顶栏为统一骨架（UI 位置统一 2026-09-10）：知识库/校务台/账号自页底上移进顶栏右侧。
 */
export default function ClassPicker() {
  const nav = useNavigate();
  const { session } = useAuth();
  const { data, loading, error, reload } = useAsync(() => listClassesOverview(), []);
  const groups = useMemo(() => {
    const map = new Map<string, ClassOverview[]>();
    for (const clazz of data?.classes ?? []) {
      const key = `${clazz.school_id}:${clazz.grade}:${clazz.name}`;
      const rows = map.get(key) ?? [];
      rows.push(clazz);
      map.set(key, rows);
    }
    return [...map.values()].sort((a, b) => {
      const grade = a[0].grade - b[0].grade;
      return grade || a[0].name.localeCompare(b[0].name, "zh-CN");
    });
  }, [data]);

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <TopBar
        title={PRODUCT_NAME}
        subtitle="教师工作台"
        right={
          <>
            {/* 全局管理（side-nav §5）已并入教学壳侧栏（仅超管），转场页不再放校级入口 */}
            <Link to="/kb" className={TOOL_LINK}>
              <BookOpen size={15} />
              <span className="hidden sm:inline">知识库</span>
            </Link>
            {session && <AccountCluster session={session} name={session.teacher?.name ?? ""} />}
          </>
        }
      />

      <div className="mx-auto w-full max-w-[1200px] flex-1 px-6 pb-12 pt-8">
        {/* 品牌 hero：原色几何构成，落地页视觉锚点 */}
        <header className="relative mb-10 overflow-hidden border-2 border-ink bg-accent px-7 py-8 text-white shadow-lift sm:px-9 sm:py-10">
          <span className="pointer-events-none absolute right-16 top-6 h-16 w-16 rounded-full bg-bh-yellow" aria-hidden />
          <span className="pointer-events-none absolute right-40 top-12 h-10 w-10 bg-bh-blue" aria-hidden />
          <span className="pointer-events-none absolute bottom-5 right-8 h-3 w-24 bg-white/90" aria-hidden />
          <div className="relative flex items-center gap-4">
            <BrandMark size={48} />
            <div>
              <h1 className="font-display text-3xl font-bold tracking-tight">班级概览</h1>
              <p className="mt-1 text-sm text-white/80">
                选择班级进入工作台，或创建新班级开始分析
              </p>
            </div>
          </div>
        </header>

        {loading && <Skeleton rows={3} />}
        {error && <ErrorState message={error} onRetry={reload} />}

        {data && data.classes.length === 0 && (
          <div className="flex flex-col items-center gap-4 rounded-2xl border border-dashed border-line bg-surface/70 px-10 py-16 text-center shadow-soft">
            <span className="flex h-16 w-16 items-center justify-center bg-accent text-white">
              <PlusCircle size={30} weight="bold" />
            </span>
            <div>
              <p className="text-lg font-semibold">还没有班级</p>
              <p className="mx-auto mt-2 max-w-[52ch] text-sm leading-relaxed text-ink-soft">
                首次使用需要三步：导入知识库、建立班级名单、标记教学进度。
                完成后即可录入考试并生成分析。
              </p>
            </div>
            <Link
              to="/wizard"
              className="mt-2 inline-flex items-center gap-2 border-2 border-ink bg-accent px-5 py-2.5 text-sm font-semibold text-white shadow-soft transition-colors hover:bg-accent-deep active:translate-x-px active:translate-y-px"
            >
              <PlusCircle size={17} />
              开始初始化（约 5 分钟）
            </Link>
          </div>
        )}

        {data && data.classes.length > 0 && (
          <>
            <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-ink-faint">
              <span className="rounded-full bg-surface-2 px-2.5 py-1">{groups.length} 个班级</span>
              <span className="rounded-full bg-surface-2 px-2.5 py-1">
                {[...new Set(data.classes.map((c) => c.grade))].length} 个年级
              </span>
              <span className="rounded-full bg-surface-2 px-2.5 py-1">
                {[...new Set(data.classes.map((c) => c.subject))].join(" / ")}
              </span>
              <span>同一班级按学科分别进入工作台</span>
            </div>
            <StaggerList className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {groups.map((rows) => (
                <StaggerItem key={`${rows[0].school_id}:${rows[0].grade}:${rows[0].name}`}>
                  <ClassGroupCard rows={rows} onClick={(classId) => nav(`/c/${classId}`)} />
                </StaggerItem>
              ))}
            </StaggerList>
          </>
        )}

        {data && data.classes.length > 0 && (
          <p className="mt-10 text-sm text-ink-faint">
            需要新建班级？{" "}
            <Link to="/wizard" className="font-medium text-accent hover:text-accent-deep">
              进入初始化向导
            </Link>
          </p>
        )}
      </div>
    </div>
  );
}

function ClassGroupCard({ rows, onClick }: { rows: ClassOverview[]; onClick: (classId: number) => void }) {
  const first = rows[0];
  return (
    <article className="border-2 border-ink bg-surface p-4 shadow-soft">
      <div className="flex items-start justify-between gap-3 px-1 pb-3">
        <div>
          <p className="text-lg font-semibold tracking-tight">{first.name}</p>
          <p className="mt-1 text-xs text-ink-faint">{first.grade} 年级 · {rows.length} 门学科</p>
        </div>
        <span className="rounded-full bg-accent-soft px-2 py-1 text-[11px] font-medium text-accent-deep">演示班级</span>
      </div>
      <div className="space-y-2 border-t border-line pt-3">
        {rows
          .slice()
          .sort((a, b) => a.subject.localeCompare(b.subject, "zh-CN"))
          .map((clazz) => (
            <SubjectCard key={clazz.class_id} c={clazz} onClick={() => onClick(clazz.class_id)} />
          ))}
      </div>
    </article>
  );
}

function SubjectCard({ c, onClick }: { c: ClassOverview; onClick: () => void }) {
  const { taught, total } = c.progress;
  const pct = total > 0 ? Math.round((taught / total) * 100) : 0;
  return (
    <button
      onClick={onClick}
      className="group flex w-full items-center gap-3 border-2 border-ink bg-canvas px-3 py-2.5 text-left shadow-soft transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-lift active:translate-x-px active:translate-y-px"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-ink">{c.subject}</span>
          <span className="text-[11px] text-ink-faint">{c.student_count} 人</span>
        </div>
        <div className="mt-1.5 flex items-center gap-2 text-[11px] text-ink-faint">
          <span>{c.exam_count} 场考试</span>
          <span>进度 {total > 0 ? `${pct}%` : "—"}</span>
          {c.todo_count > 0 && (
            <span className="inline-flex items-center gap-1 text-warn">
              <ClipboardText size={11} /> {c.todo_count} 待办
            </span>
          )}
        </div>
      </div>
      <ArrowRight size={16} className="shrink-0 text-ink-faint transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
    </button>
  );
}
