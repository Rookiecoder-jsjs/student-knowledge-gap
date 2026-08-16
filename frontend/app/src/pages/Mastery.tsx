import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { Card, EmptyState, ErrorState, Input, Page, PageHeader, SectionTitle, Skeleton, StatTile } from "../components/ui";
import { getMastery, listStudents } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { ACCENTS } from "../lib/theme";
import type { MasteryItem } from "../lib/types";

/** 掌握度画像：按章节分组的掌握度网格（仅表达个体变化，不做横向比较）。 */
export default function Mastery() {
  const { classId, studentId } = useParams();
  const cid = Number(classId);
  const sid = Number(studentId);
  const [asOf, setAsOf] = useState("");

  const students = useAsync(() => listStudents(cid), [cid]);
  const mastery = useAsync(() => getMastery(sid, asOf || undefined), [sid, asOf]);
  const student = students.data?.students.find((s) => s.student_id === sid);

  const groups = useMemo(() => {
    const m = new Map<string, MasteryItem[]>();
    for (const item of mastery.data?.mastery ?? []) {
      const chapter = item.code.split("-")[1]?.[0] ?? "?";
      const key = `第${chapter}章`;
      m.set(key, [...(m.get(key) ?? []), item]);
    }
    return [...m.entries()];
  }, [mastery.data]);

  return (
    <Page accent={ACCENTS.student}>
      <PageHeader
        title={student ? `${student.name_or_alias} 的掌握程度画像` : "掌握程度画像"}
        desc="掌握程度会随时间慢慢下降，只反映该生自身变化"
        actions={
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            截至
            <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          </label>
        }
      />

      {mastery.loading && <Skeleton rows={5} />}
      {mastery.error && <ErrorState message={mastery.error} onRetry={mastery.reload} />}
      {mastery.data && mastery.data.mastery.length === 0 && (
        <Card>
          <EmptyState
            title="尚无掌握程度数据"
            hint="录入并提交至少一场覆盖这些知识点的考试后，这里会显示掌握程度。"
          />
        </Card>
      )}

      {mastery.data && mastery.data.mastery.length > 0 && (
        <div className="mb-6 grid grid-cols-3 gap-3">
          <StatTile
            value={mastery.data.mastery.filter((k) => k.mastery >= 0.8).length}
            label="掌握较好（≥80%）"
            tone="accent"
          />
          <StatTile
            value={mastery.data.mastery.filter((k) => k.mastery >= 0.6 && k.mastery < 0.8).length}
            label="待巩固（60–80%）"
          />
          <StatTile
            value={mastery.data.mastery.filter((k) => k.mastery < 0.6).length}
            label="待加强（<60%）"
            tone="danger"
          />
        </div>
      )}

      <div className="space-y-7">
        {groups.map(([chapter, items]) => (
          <section key={chapter}>
            <SectionTitle count={items.length}>{chapter}</SectionTitle>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((k) => {
                const high = k.mastery >= 0.8;
                const mid = k.mastery >= 0.6;
                return (
                  <div
                    key={k.code}
                    className={`rounded-lg border p-4 transition-colors ${
                      high
                        ? "border-accent/25 bg-accent-soft/60"
                        : mid
                          ? "border-line bg-surface"
                          : "border-danger/20 bg-danger-soft/50"
                    }`}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <p className="truncate text-sm font-medium">{k.name}</p>
                      <span
                        className={`text-sm font-semibold ${
                          mid ? "text-accent-deep" : "text-danger"
                        }`}
                      >
                        {Math.round(k.mastery * 100)}%
                      </span>
                    </div>
                    <p className="mt-0.5 font-mono text-xs text-ink-faint">{k.code}</p>
                    <div className="mt-2.5 h-2 overflow-hidden rounded-full bg-surface-2">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${
                          mid ? "bg-accent" : "bg-danger"
                        }`}
                        style={{ width: `${Math.max(4, k.mastery * 100)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </Page>
  );
}
