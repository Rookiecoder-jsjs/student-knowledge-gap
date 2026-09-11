import { ArrowRight, Plus } from "@phosphor-icons/react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Page,
  PageHeader,
  Skeleton,
} from "../../components/ui";
import { listKbVersions, patchKbVersion } from "../../lib/api";
import { useAsync } from "../../lib/hooks";
import { roleFlags, setBackTarget } from "../../lib/portal";
import { useAuth } from "../../lib/AuthContext";
import { ACCENTS } from "../../lib/theme";

/**
 * KB 总面板（rbac-scopes-design §6，校务台）：全校知识库总览——学科 → 年级 → 版本。
 *
 * - 数据面：GET /kb/versions 已按范围过滤（学科管理员只见授权学科；admin/kb_editor/
 *   开放模式全量），本页纯前端分组；
 * - 治理：启用本版 = 该学科全校口径切换（kbGovern 显隐——学科管理员可启用本学科）；
 * - draft→reviewed（备审）走内容写权（kbWrite）；
 * - 「进入工作台」带 kbVersionId 跳 /kb（版本治理/编辑仍在工作台做）。
 */

const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  reviewed: "备审",
  active: "正式",
};

export default function KbPanel() {
  const nav = useNavigate();
  const flags = roleFlags(useAuth().session);
  const versions = useAsync(() => listKbVersions(), []);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const subjects = useMemo(
    () => [...new Set((versions.data?.versions ?? []).map((v) => v.subject))],
    [versions.data]
  );
  const rows = versions.data?.versions ?? [];

  async function doPatch(v: { id: number; subject: string; grade: number | null }, status: string) {
    setBusyId(v.id);
    setErr(null);
    try {
      await patchKbVersion(v.id, status, status === "active" ? { confirm: true, force: true } : undefined);
      versions.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Page accent={ACCENTS.knowledge}>
      <PageHeader
        title="知识库总览"
        desc={
          flags.adminOrOpen
            ? "全校各学科知识库版本；「启用」= 该学科全校口径切换"
            : "你管理的学科知识库；「启用」= 该学科全校口径切换"
        }
        actions={
          <Button
            onClick={() => {
              setBackTarget("/kb/new", "/admin/kb");
              nav("/kb/new");
            }}
          >
            <Plus size={15} /> 图形化新建
          </Button>
        }
      />

      {versions.loading && <Skeleton rows={4} />}
      {versions.error && <ErrorState message={versions.error} onRetry={versions.reload} />}
      {!versions.loading && !versions.error && rows.length === 0 && (
        <Card>
          <EmptyState
            title="暂无知识库版本"
            hint="右上角「图形化新建」四步建库，或在工作台用 YAML 导入。"
          />
        </Card>
      )}

      {err && <p className="mb-3 text-xs text-danger">{err}</p>}

      {subjects.map((sub) => {
        const vs = rows.filter((v) => v.subject === sub);
        const grades = [...new Set(vs.map((v) => v.grade))].sort(
          (a, b) => (a ?? 99) - (b ?? 99)
        );
        return (
          <section key={sub} className="mb-7">
            <h2 className="mb-2 text-sm font-semibold text-ink">
              {sub}
              <span className="ml-2 text-xs font-normal text-ink-faint">
                {vs.length} 个版本 · {vs.filter((v) => v.status === "active").length > 0 ? "已启用" : "未启用"}
              </span>
            </h2>
            {grades.map((g) => (
              <div key={g ?? "na"} className="mb-3">
                <p className="mb-1.5 text-xs text-ink-faint">
                  {g == null ? "未标年级" : `${g} 年级`}
                </p>
                <Card className="divide-y divide-line">
                  {vs
                    .filter((v) => v.grade === g)
                    .map((v) => {
                      const govern = flags.kbGovern(v.subject, v.grade);
                      const canWrite = flags.kbWrite(v.subject, v.grade);
                      return (
                        <div key={v.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                          <div className="min-w-0 flex-1">
                            <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                              v{v.version}
                              <span className="font-normal text-ink-soft">{v.textbook_edition}</span>
                              <Badge tone={v.status === "active" ? "accent" : "neutral"}>
                                {STATUS_LABEL[v.status] ?? v.status}
                              </Badge>
                            </p>
                            <p className="text-xs text-ink-faint">
                              {v.kp_count} 个知识点 · 建于 {v.created_at?.slice(0, 10) ?? "—"}
                            </p>
                          </div>
                          {v.status === "draft" && canWrite && (
                            <Button
                              variant="ghost"
                              disabled={busyId === v.id}
                              onClick={() => doPatch(v, "reviewed")}
                            >
                              设为备审
                            </Button>
                          )}
                          {v.status !== "active" && govern && (
                            <Button
                              variant="secondary"
                              disabled={busyId === v.id}
                              onClick={() => doPatch(v, "active")}
                            >
                              启用本版
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            onClick={() => {
                              setBackTarget("/kb", "/admin/kb");
                              nav("/kb", { state: { kbVersionId: v.id } });
                            }}
                          >
                            进入工作台 <ArrowRight size={13} />
                          </Button>
                        </div>
                      );
                    })}
                </Card>
              </div>
            ))}
          </section>
        );
      })}
    </Page>
  );
}
