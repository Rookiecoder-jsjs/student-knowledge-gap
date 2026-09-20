import {
  ArrowRight,
  BookOpen,
  ClipboardText,
  PlusCircle,
  TreeStructure,
} from "@phosphor-icons/react";
import { Link, useNavigate } from "react-router-dom";
import { ErrorState, Skeleton } from "../components/ui";
import { StaggerItem, StaggerList } from "../components/motion";
import { listClassesOverview } from "../lib/api";
import { useAsync } from "../lib/hooks";
import type { ClassOverview } from "../lib/types";

/** 一级页面·班级概览：横向对比所有班级的待办 / 最近考试 / 教学进度，点击进入单班工作台。 */
export default function ClassPicker() {
  const nav = useNavigate();
  const { data, loading, error, reload } = useAsync(() => listClassesOverview(), []);

  return (
    <div className="mx-auto flex min-h-[100dvh] max-w-[1200px] flex-col px-6 py-12">
      {/* 品牌 hero：包豪斯红块 + 原色几何构成，落地页视觉锚点 */}
      <header className="relative mb-10 overflow-hidden border-2 border-ink bg-accent px-8 py-9 text-white shadow-lift">
        {/* 原色几何构成（纯装饰）：圆 = 班级，方 = 考试，条 = 待办 */}
        <span className="pointer-events-none absolute right-16 top-6 h-16 w-16 rounded-full bg-bh-yellow" aria-hidden />
        <span className="pointer-events-none absolute right-40 top-12 h-10 w-10 bg-bh-blue" aria-hidden />
        <span className="pointer-events-none absolute bottom-5 right-8 h-3 w-24 bg-white/90" aria-hidden />
        <div className="relative flex items-center gap-4">
          <span className="flex h-12 w-12 items-center justify-center bg-white text-accent">
            <TreeStructure size={24} weight="bold" />
          </span>
          <div>
            <h1 className="font-display text-3xl font-bold tracking-tight">班级概览</h1>
            <p className="mt-1 text-sm text-white/85">
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
        <StaggerList className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {data.classes.map((c) => (
            <StaggerItem key={c.class_id}>
              <ClassCard c={c} onClick={() => nav(`/c/${c.class_id}`)} />
            </StaggerItem>
          ))}
        </StaggerList>
      )}

      <div className="mt-10 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <Link
          to="/kb"
          className="inline-flex items-center gap-1.5 font-medium text-accent transition-colors hover:text-accent-deep"
        >
          <BookOpen size={15} />
          知识库管理
        </Link>
        {data && data.classes.length > 0 && (
          <span className="text-ink-faint">
            需要新建班级？{" "}
            <Link to="/wizard" className="font-medium text-accent hover:text-accent-deep">
              进入初始化向导
            </Link>
          </span>
        )}
      </div>
    </div>
  );
}

function ClassCard({ c, onClick }: { c: ClassOverview; onClick: () => void }) {
  const { taught, total } = c.progress;
  const pct = total > 0 ? Math.round((taught / total) * 100) : 0;
  return (
    <button
      onClick={onClick}
      className="group flex w-full flex-col border-2 border-ink bg-surface p-6 text-left transition-all duration-200 hover:-translate-y-1 hover:shadow-lift active:translate-x-px active:translate-y-px"
    >
      <div className="flex items-start justify-between">
        <p className="text-lg font-semibold tracking-tight">{c.name}</p>
        <ArrowRight
          size={18}
          className="text-ink-faint transition-all group-hover:translate-x-0.5 group-hover:text-accent"
        />
      </div>
      <p className="mt-1 text-xs text-ink-faint">
        {c.grade} 年级 · {c.subject}
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2.5">
        <span className="text-sm text-ink-soft">
          <b className="font-semibold text-ink">{c.student_count}</b> 学生
        </span>
        <span className="text-sm text-ink-soft">
          <b className="font-semibold text-ink">{c.exam_count}</b> 考试
        </span>
        {c.todo_count > 0 && (
          <span className="inline-flex items-center gap-1 rounded-lg border border-warn/20 bg-warn-soft px-2 py-0.5 text-xs font-medium text-warn">
            <ClipboardText size={12} />
            {c.todo_count} 项待办
          </span>
        )}
      </div>

      <div className="mt-4 border-t border-line pt-3 text-xs text-ink-faint">
        {c.latest_exam ? (
          <p className="truncate">
            最近：{c.latest_exam.name} · {c.latest_exam.exam_date}
            <span className="ml-1 text-ink-soft">
              （{c.latest_exam.submitted} 已提交
              {c.latest_exam.pending > 0 ? ` / ${c.latest_exam.pending} 待提交` : ""}）
            </span>
          </p>
        ) : (
          <p>暂无考试</p>
        )}
      </div>

      <div className="mt-3">
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="text-ink-faint">教学进度</span>
          <span className="font-medium text-ink-soft">
            {total > 0 ? `${taught}/${total} · ${pct}%` : "未导入知识库"}
          </span>
        </div>
        {total > 0 && (
          <div className="h-2 w-full overflow-hidden bg-surface-2 border border-ink/30">
            <div
              className="h-full bg-accent"
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
      </div>
    </button>
  );
}
