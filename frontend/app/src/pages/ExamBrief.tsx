import { ArrowRight, FileText } from "@phosphor-icons/react";
import { Link, useParams } from "react-router-dom";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Page,
  SectionTitle,
  Skeleton,
  Table,
  THead,
  TR,
  Td,
  Th,
} from "../components/ui";
import { qualityReport } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { setBackTarget } from "../lib/portal";
import { ACCENTS } from "../lib/theme";

/**
 * 考试概况（diagnosis-sheet-redesign §1.1/F1）：第 5 阶一屏轻页。
 * 只回答「这场考试考得怎么样」——提交率/均分/共性 top3/逐题得分率；
 * 行动与诊断内容一律不进此页（出口指向班级诊断单与本场完整报告存档）。
 */
export default function ExamBrief() {
  const { classId, examId: routeExamId } = useParams();
  const cid = Number(classId);
  const eid = Number(routeExamId);

  // 概况数据源 = 质量报告 snapshot（B2 已补字段），get-or-generate 语义不变
  const report = useAsync(() => qualityReport(cid, eid, false), [cid, eid]);
  const snap = report.data?.snapshot ?? null;
  // 平均得分率：满分加权（逐题 rate 按分值加权平均）；无 rates 时退化为 —
  const meanRate = snap && snap.question_rates.length > 0
    ? (() => {
        let num = 0, den = 0;
        for (const q of snap.question_rates) {
          if (q.rate != null) {
            num += q.rate * q.full_score;
            den += q.full_score;
          }
        }
        return den > 0 ? num / den : null;
      })()
    : null;

  return (
    <Page accent={ACCENTS.exam}>
      <SectionTitle>本场概况</SectionTitle>

      {report.loading && <Skeleton rows={6} />}
      {report.error && <ErrorState message={report.error} onRetry={report.reload} />}

      {report.data && !snap && (
        <Card>
          <EmptyState
            title="暂无概况数据"
            hint="报告生成后此处显示提交率、平均得分率与共性待加强点。"
          />
        </Card>
      )}

      {snap && (
        <>
          {/* 关键指标表：将概况数字集中在同一行，便于班级间/考试间对照。 */}
          <Card className="p-0">
            <div className="flex items-baseline justify-between gap-3 border-b border-line px-4 py-3">
              <p className="text-sm font-semibold">考试数据总览</p>
              <p className="text-xs text-ink-faint">{snap.exam_date}</p>
            </div>
            <Table className="[&_table]:min-w-[640px]">
              <THead>
                <tr>
                  <Th>提交人数</Th>
                  <Th>待提交</Th>
                  <Th>平均得分率</Th>
                  <Th>平均分</Th>
                  <Th>最高分</Th>
                  <Th>最低分</Th>
                </tr>
              </THead>
              <tbody>
                <TR>
                  <Td className="text-base font-semibold text-ink">{snap.committed}</Td>
                  <Td className={snap.pending > 0 ? "font-semibold text-warn" : "text-ink-soft"}>
                    {snap.pending}
                  </Td>
                  <Td className="text-base font-semibold text-ink">
                    {meanRate != null ? `${Math.round(meanRate * 100)}%` : "—"}
                  </Td>
                  <Td>{snap.stats.mean ?? "—"}</Td>
                  <Td>{snap.stats.max ?? "—"}</Td>
                  <Td>{snap.stats.min ?? "—"}</Td>
                </TR>
              </tbody>
            </Table>
          </Card>

          {/* 共性薄弱点表：完整保留后端返回的排名依据，不再只展示 Top 3 标签。 */}
          <Card className="mt-4 p-0">
            <div className="flex items-baseline justify-between gap-3 border-b border-line px-4 py-3">
              <p className="text-sm font-semibold">共性待加强知识点</p>
              <p className="text-xs text-ink-faint">按班级平均掌握度升序</p>
            </div>
            {snap.common_weak.length > 0 ? (
              <Table className="[&_table]:min-w-[720px]">
                <THead>
                  <tr>
                    <Th>排名</Th>
                    <Th>知识点</Th>
                    <Th>编码</Th>
                    <Th>班级平均掌握度</Th>
                    <Th>薄弱占比</Th>
                    <Th>有效样本</Th>
                  </tr>
                </THead>
                <tbody>
                  {snap.common_weak.map((d, index) => (
                    <TR key={d.code}>
                      <Td className="text-ink-faint">{index + 1}</Td>
                      <Td className="font-medium text-ink">{d.name}</Td>
                      <Td className="text-ink-soft">{d.code}</Td>
                      <Td>{Math.round(d.class_avg * 100)}%</Td>
                      <Td>
                        <Badge tone="warn">{Math.round(d.weak_share * 100)}%</Badge>
                      </Td>
                      <Td>{d.n}</Td>
                    </TR>
                  ))}
                </tbody>
              </Table>
            ) : (
              <p className="px-4 py-5 text-sm text-ink-soft">暂无达到共性阈值的知识点。</p>
            )}
          </Card>

          {/* 逐题得分率表：在窄屏由 Table 原语负责横向滚动。 */}
          <Card className="mt-4 p-4">
            <p className="mb-3 text-sm font-semibold">逐题得分率</p>
            {snap.question_rates.length > 0 ? (
              <Table className="[&_table]:min-w-[760px]">
                <THead>
                  <tr>
                    <Th>题号</Th>
                    <Th>题型</Th>
                    <Th>满分</Th>
                    <Th>得分率</Th>
                    <Th>关联知识点</Th>
                    <Th>状态</Th>
                  </tr>
                </THead>
                <tbody>
                  {snap.question_rates.map((q) => (
                    <TR key={q.idx}>
                      <Td className="font-medium text-ink">第 {q.idx} 题</Td>
                      <Td>{q.q_type || "—"}</Td>
                      <Td>{q.full_score}</Td>
                      <Td className={q.low ? "font-semibold text-warn" : "font-semibold text-ink"}>
                        {q.rate != null ? `${Math.round(q.rate * 100)}%` : "—"}
                      </Td>
                      <Td className="max-w-[260px] truncate text-ink-soft" title={q.kps || undefined}>
                        {q.kps || "未标注"}
                      </Td>
                      <Td>
                        <Badge tone={q.low ? "warn" : "success"}>{q.low ? "待加强" : "正常"}</Badge>
                      </Td>
                    </TR>
                  ))}
                </tbody>
              </Table>
            ) : (
              <p className="text-xs text-ink-faint">暂无题目数据。</p>
            )}
          </Card>

          {/* 两个出口：班级诊断单 / 本场完整报告存档 */}
          <div className="mt-4 flex flex-wrap gap-3">
            <Link to={`/c/${cid}/exams?tab=diagnosis`}>
              <Button>
                查看班级诊断单
                <ArrowRight size={15} />
              </Button>
            </Link>
            <Link
              to={`/c/${cid}/quality?exam=${eid}`}
              onClick={() => setBackTarget(`/c/${cid}/quality`, `/c/${cid}/exams/${eid}/report`)}
            >
              <Button variant="secondary">
                <FileText size={15} />
                本场完整报告
              </Button>
            </Link>
          </div>
        </>
      )}
    </Page>
  );
}
