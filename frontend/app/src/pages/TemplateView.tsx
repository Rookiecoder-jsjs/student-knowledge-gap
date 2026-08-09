import { ArrowRight, SealCheck } from "@phosphor-icons/react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, ErrorState, SectionTitle, Skeleton } from "../components/ui";
import { examDetail, listExams } from "../lib/api";
import { useAsync } from "../lib/hooks";

/** 阶段 1·建卷：试卷结构概览（题目、满分、知识点标注），只读。 */
export default function TemplateView() {
  const { classId, examId } = useParams();
  const cid = Number(classId);
  const eid = Number(examId);
  const detail = useAsync(() => examDetail(eid), [eid]);
  const exams = useAsync(() => listExams(cid), [cid]);
  const unreviewed = exams.data?.exams.find((e) => e.exam_id === eid)?.unreviewed_tags ?? 0;

  const totalScore = detail.data?.questions.reduce((s, q) => s + q.full_score, 0) ?? 0;
  const kpSet = new Set<string>();
  detail.data?.questions.forEach((q) => q.kps.forEach((k) => kpSet.add(k.code)));

  return (
    <div>
      <SectionTitle
        count={detail.data?.questions.length}
        right={
          detail.data && (
            <span className="text-xs text-ink-faint tabular-nums">
              总分 {totalScore} · 覆盖 {kpSet.size} 个知识点
            </span>
          )
        }
      >
        试卷结构
      </SectionTitle>

      {detail.loading && <Skeleton rows={4} />}
      {detail.error && <ErrorState message={detail.error} onRetry={detail.reload} />}

      {detail.data && (
        <Card className="divide-y divide-line">
          {detail.data.questions.map((q) => (
            <div key={q.question_id} className="px-5 py-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-semibold">
                    题{q.idx}
                    <span className="ml-2 font-normal text-ink-faint">
                      {q.q_type} · 满分 {q.full_score} · {q.cog_level}
                    </span>
                  </p>
                  {q.stem && <p className="mt-1 line-clamp-2 text-xs text-ink-soft">{q.stem}</p>}
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {q.kps.length === 0 && (
                  <span className="text-xs text-danger">未标注 - 未标注题目不进入分析</span>
                )}
                {q.kps.map((k) => (
                  <Badge key={k.tag_id} tone={k.reviewed ? "accent" : "warn"}>
                    {k.code} {k.name}
                  </Badge>
                ))}
              </div>
            </div>
          ))}
        </Card>
      )}

      {detail.data && (
        <div className="mt-5 flex justify-end">
          <Link to={`/c/${cid}/exams/${eid}/${unreviewed > 0 ? "review" : "collect"}`}>
            <Button>
              {unreviewed > 0 ? (
                <>
                  <SealCheck size={15} />
                  去审核标注（{unreviewed}）
                </>
              ) : (
                <>
                  去采集学生卷
                  <ArrowRight size={15} />
                </>
              )}
            </Button>
          </Link>
        </div>
      )}
    </div>
  );
}
