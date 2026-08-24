import { Fragment, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { Page, StatusDot } from "./ui";
import { listExams } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { ACCENTS } from "../lib/theme";

/** 考试流水线 5 阶。to 相对于 /c/:cid/exams/:eid。第 5 阶=概况（diagnosis-sheet-redesign F2）。 */
const STAGES = [
  { n: 1, label: "建卷", to: "" },
  { n: 2, label: "审核", to: "/review" },
  { n: 3, label: "采集", to: "/collect" },
  { n: 4, label: "提交", to: "/commit" },
  { n: 5, label: "概况", to: "/report" },
] as const;

/**
 * 考试工作区：顶部常驻 5 阶 stepper，把 建卷->审核->采集->提交->报告 串成一条主线。
 * 用 listExams 一次取本场考试的 name/题数/未审核数/已提交数，驱动阶段完成态。
 */
export function ExamWorkspace({ stage, children }: { stage: number; children: ReactNode }) {
  const { classId, examId } = useParams();
  const cid = Number(classId);
  const eid = Number(examId);
  const base = `/c/${cid}/exams/${eid}`;
  const exams = useAsync(() => listExams(cid), [cid]);
  const exam = exams.data?.exams.find((e) => e.exam_id === eid);

  const committed = (exam?.response_counts["已提交"] ?? 0) > 0;
  const reviewed = (exam?.unreviewed_tags ?? 0) === 0;

  // 各阶完成条件
  const done: Record<number, boolean> = {
    1: true, // 建卷：考试已存在
    2: reviewed, // 审核：无未审核标注
    3: committed, // 采集：已提交（提交后采集即定稿）
    4: committed, // 提交：已提交
    5: committed, // 报告：提交后可生成
  };

  return (
    <Page accent={ACCENTS.exam}>
      {/* 考试上下文 + 流水线 stepper */}
      <div className="mb-6 rounded-2xl bg-surface px-5 py-4 shadow-soft">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h1 className="text-base font-semibold tracking-tight text-ink">
              {exam?.name ?? "考试"}
            </h1>
            {exam && (
              <span className="text-xs text-ink-faint">
                {exam.exam_date} · {exam.type} · {exam.question_count} 题
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs">
            {exam && exam.unreviewed_tags > 0 && (
              <span className="rounded-md bg-warn-soft px-2 py-0.5 font-medium text-warn">
                {exam.unreviewed_tags} 标注待审核
              </span>
            )}
            {committed ? (
              <span className="rounded-md bg-accent-soft px-2 py-0.5 font-medium text-accent-deep">
                已提交
              </span>
            ) : (
              <span className="rounded-md bg-surface-2 px-2 py-0.5 font-medium text-ink-soft">
                未提交
              </span>
            )}
          </div>
        </div>

        <ol role="list" className="flex flex-wrap items-center gap-1">
          {STAGES.map((s, i) => {
            const isDone = done[s.n];
            const isCurrent = s.n === stage;
            const state = isDone ? "done" : isCurrent ? "active" : "todo";
            return (
              <Fragment key={s.n}>
                <li>
                  <Link
                    to={`${base}${s.to}`}
                    aria-current={isCurrent ? "step" : undefined}
                    className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                      isCurrent
                        ? "bg-accent-soft text-accent-deep"
                        : isDone
                          ? "text-accent-deep hover:bg-surface-2"
                          : "text-ink-faint hover:bg-surface-2 hover:text-ink-soft"
                    }`}
                  >
                    <StatusDot state={state} />
                    <span>
                      <span className="tabular-nums text-ink-faint">{s.n}</span> {s.label}
                    </span>
                  </Link>
                </li>
                {i < STAGES.length - 1 && (
                  <li
                    className={`h-px w-4 ${isDone ? "bg-accent/40" : "bg-line"}`}
                    aria-hidden
                  />
                )}
              </Fragment>
            );
          })}
        </ol>
      </div>

      {children}
    </Page>
  );
}
