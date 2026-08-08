import { Plus } from "@phosphor-icons/react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, Skeleton } from "../components/ui";
import { Reveal } from "../components/motion";
import { listClasses, listExams } from "../lib/api";
import { useAsync } from "../lib/hooks";

/** 考试录入页：顶部班级筛选 + 考试列表（含审核/采集状态角标）+ 新建入口。 */
export default function Exams() {
  const { classId } = useParams();
  const cid = Number(classId);
  const nav = useNavigate();
  const { data, loading, error, reload } = useAsync(() => listExams(cid), [cid]);
  const classes = useAsync(() => listClasses(), []);

  return (
    <div>
      <PageHeader
        title="考试录入"
        desc="拍照或 Excel 录入 -> 审核 -> 提交，提交后自动生成分析依据"
        actions={
          <span className="flex items-center gap-2">
            <select
              value={cid}
              onChange={(e) => nav(`/c/${e.target.value}/exams`)}
              className="rounded-xl border border-line bg-surface px-3 py-2 text-sm transition-colors focus:border-accent"
              aria-label="切换班级"
            >
              {(classes.data?.classes ?? []).map((c) => (
                <option key={c.class_id} value={c.class_id}>
                  {c.name}
                </option>
              ))}
            </select>
            <Link to={`/c/${cid}/exams/new`}>
              <Button>
                <Plus size={15} />
                新建考试
              </Button>
            </Link>
          </span>
        }
      />

      {loading && <Skeleton rows={4} />}
      {error && <ErrorState message={error} onRetry={reload} />}

      {data && data.exams.length === 0 && (
        <Card>
          <EmptyState
            title="暂无考试"
            hint="点击右上角「新建考试」，推荐拍照上传空白试卷，AI 会自动解析题目并初步标注知识点。"
          />
        </Card>
      )}

      {data && data.exams.length > 0 && (
        <Reveal>
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-ink-faint">
                    <th className="px-5 py-3 font-medium">考试</th>
                    <th className="px-5 py-3 font-medium">日期 / 类型</th>
                    <th className="px-5 py-3 font-medium">采集进度</th>
                    <th className="px-5 py-3 font-medium">状态</th>
                    <th className="px-5 py-3" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {data.exams.map((e) => {
                    const submitted = e.response_counts["已提交"] ?? 0;
                    const pending = e.response_counts["待审核"] ?? 0;
                    return (
                      <tr key={e.exam_id} className="transition-colors hover:bg-surface-2/50">
                        <td className="px-5 py-3.5 font-medium">{e.name}</td>
                        <td className="px-5 py-3.5 text-ink-soft">
                          {e.exam_date} · {e.type}
                        </td>
                        <td className="px-5 py-3.5 text-ink-soft">
                          {submitted} 已提交{pending > 0 ? ` / ${pending} 待审核` : ""}
                        </td>
                        <td className="px-5 py-3.5">
                          <span className="flex flex-wrap gap-1.5">
                            {e.unreviewed_tags > 0 && (
                              <Badge tone="warn">{e.unreviewed_tags} 标注待审核</Badge>
                            )}
                            {submitted > 0 && <Badge tone="accent">已生成分析</Badge>}
                          </span>
                        </td>
                        <td className="px-5 py-3.5 text-right">
                          <span className="flex justify-end gap-2">
                            <Link to={`/c/${cid}/exams/${e.exam_id}/review`}>
                              <Button variant="secondary">审核台</Button>
                            </Link>
                            <Link to={`/c/${cid}/exams/${e.exam_id}/collect`}>
                              <Button variant="secondary">采集 / 提交</Button>
                            </Link>
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </Reveal>
      )}
    </div>
  );
}
