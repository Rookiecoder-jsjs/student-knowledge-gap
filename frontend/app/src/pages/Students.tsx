import { ChartBar, Eye, FirstAidKit, Key, ListChecks } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, Modal, Page, PageHeader, Pagination, Skeleton } from "../components/ui";
import { Reveal } from "../components/motion";
import { enableStudentAccount, listInterventions, listStudents } from "../lib/api";
import type { StudentInfo } from "../lib/types";
import { useAuth } from "../lib/AuthContext";
import { useAsync } from "../lib/hooks";
import { roleFlags } from "../lib/portal";
import { ACCENTS } from "../lib/theme";

/** 学生列表（名单原序，不按任何分数排序）。 */
export default function Students() {
  const { classId } = useParams();
  const cid = Number(classId);
  const PAGE_SIZE = 20;
  const [page, setPage] = useState(1);
  const { data, loading, error, reload } = useAsync(
    () => listStudents(cid, { offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE }),
    [cid, page],
  );
  useEffect(() => setPage(1), [cid]);
  useEffect(() => {
    if (data && data.students.length === 0 && data.total && page > 1) setPage((p) => p - 1);
  }, [data, page]);
  const flags = roleFlags(useAuth().session);
  // 干预摘要（intervention-loop §6）：每行 chip「N 项建议 · M 已执行」——聚合一次
  const iv = useAsync(
    () => listInterventions({ class_id: cid, limit: 200 }),
    [cid]
  );
  const byStudent = new Map<number, { suggested: number; done: number; awaiting: number }>();
  for (const row of iv.data?.items ?? []) {
    if (row.student_id == null) continue;
    const slot = byStudent.get(row.student_id) ?? { suggested: 0, done: 0, awaiting: 0 };
    if (row.status === "suggested") slot.suggested += 1;
    if (row.status === "done") slot.done += 1;
    // 行级折叠状态（闭环一期 P1）：待复测/持平/未闭合都算「进行中」
    if (row.loop_state && row.loop_state !== "已建议" && row.loop_state !== "已跳过"
        && row.loop_state !== "已闭合" && row.loop_state !== "达标") slot.awaiting += 1;
    byStudent.set(row.student_id, slot);
  }

  // admin 开通/重置学生自服务账号（frontend-ends-design §D）
  const [enableFor, setEnableFor] = useState<StudentInfo | null>(null);
  const [enableErr, setEnableErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pw, setPw] = useState("");
  const [uname, setUname] = useState("");

  const doEnable = async () => {
    if (!enableFor) return;
    setBusy(true);
    setEnableErr(null);
    try {
      await enableStudentAccount(enableFor.student_id, pw, uname.trim() || undefined);
      setEnableFor(null); // 成功即关闭；行内徽标/按钮随 reload 更新
      reload();
    } catch (e) {
      setEnableErr(e instanceof Error ? e.message : "开通失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page accent={ACCENTS.student}>
      <PageHeader
        title="学生诊断"
        desc={`按名单原序展示；诊断单先看进步，再看待加强项${flags.isHomeroom(cid) ? " · 你是本班班主任（可开通本班学生自服务账号）" : ""}`}
      />

      {iv.error && (
        <Card className="mb-4 flex flex-wrap items-center justify-between gap-2 border-danger/30 bg-danger/5 p-3 text-sm text-danger">
          <span>干预摘要加载失败，学生名单仍可使用。</span>
          <Button size="sm" variant="secondary" onClick={iv.reload}>重试</Button>
        </Card>
      )}

      {loading && <Skeleton rows={5} />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && data.students.length === 0 && (
        <Card>
          <EmptyState title="暂无学生" hint="请在初始化向导中添加班级名单。" />
        </Card>
      )}

      {data && data.students.length > 0 && (
        <Reveal>
          <Card className="divide-y divide-line">
          {data.students.map((s) => {
            const stat = byStudent.get(s.student_id);
            return (
            <div key={s.student_id} className="flex items-center gap-3 px-5 py-3.5 transition-colors hover:bg-surface-2/50">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent-deep">
                {s.name_or_alias.slice(0, 1)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{s.name_or_alias}</p>
                <p className="flex flex-wrap items-center gap-2 text-xs text-ink-faint">
                  {s.external_code}
                  {s.has_account && <Badge tone="neutral">自服务已开通</Badge>}
                  {stat && (stat.suggested > 0 || stat.done > 0) && (
                    <Badge tone={stat.suggested > 0 ? "warn" : "neutral"}>
                      {stat.suggested > 0
                        ? `${stat.suggested} 条行动待确认`
                        : stat.awaiting > 0
                          ? `${stat.awaiting} 项干预进行中`
                          : `${stat.done} 项干预已执行`}
                    </Badge>
                  )}
                </p>
              </div>
              <span className="flex min-w-0 flex-wrap items-center justify-end gap-2">
                {flags.canEnableStudent(cid) && (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setEnableFor(s);
                      setEnableErr(null);
                      setPw("");
                      setUname(s.username ?? "");
                    }}
                    title={s.has_account ? "重置学生自服务账号口令" : "开通学生自服务账号（admin 或本班班主任）"}
                  >
                    <Key size={14} />
                    {s.has_account ? "重置口令" : "开通账号"}
                  </Button>
                )}
                {flags.adminLogin && (
                  <Link
                    to={`/c/${cid}/students/${s.student_id}/portal`}
                    title="以学生视角查看自服务门户（只读，与 /me 同源同形状）"
                    className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-3 py-2 text-sm font-medium text-ink-soft transition-colors hover:border-accent/50 hover:text-accent"
                  >
                    <Eye size={14} />
                    学生视角
                  </Link>
                )}
                <Link
                  to={`/c/${cid}/students/${s.student_id}/diagnosis`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-3 py-2 text-sm font-medium text-ink-soft transition-colors hover:border-accent/50 hover:text-accent"
                >
                  <FirstAidKit size={14} />
                  诊断单
                </Link>
                <Link
                  to={`/c/${cid}/students/${s.student_id}/diagnosis?view=plan`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-3 py-2 text-sm font-medium text-ink-soft transition-colors hover:border-accent/50 hover:text-accent"
                >
                  <ListChecks size={14} />
                  改进单
                </Link>
                <Link
                  to={`/c/${cid}/students/${s.student_id}/mastery`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-3 py-2 text-sm font-medium text-ink-soft transition-colors hover:border-accent/50 hover:text-accent"
                >
                  <ChartBar size={14} />
                  掌握程度
                </Link>
              </span>
            </div>
            );
          })}
          </Card>
          <Pagination
            className="mt-4"
            page={page}
            pageSize={PAGE_SIZE}
            total={data.total ?? data.students.length}
            hasMore={data.has_more}
            onPageChange={setPage}
            disabled={loading}
          />
        </Reveal>
      )}

      {/* 开通/重置学生自服务账号（frontend-ends-design §D + rbac-scopes-design §8：admin 或本班班主任） */}
      <Modal
        open={enableFor !== null}
        onClose={() => setEnableFor(null)}
        title={enableFor?.has_account ? `重置口令 · ${enableFor?.name_or_alias}` : `开通自服务账号 · ${enableFor?.name_or_alias ?? ""}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setEnableFor(null)} disabled={busy}>
              取消
            </Button>
            <Button variant="primary" onClick={doEnable} disabled={busy || pw.length < 6}>
              {busy ? "提交中…" : "保存"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-xs text-ink-faint">
            学生登录后查看自己的掌握度/薄弱点/已签发报告与改进单（只读自服务面）。
            {enableFor?.has_account
              ? "已开通账号将重置口令。"
              : "缺省登录名 = 学籍号（external_code），也可显式指定。"}
          </p>
          <Field label="登录名">
            <Input value={uname} onChange={(e) => setUname(e.target.value)} placeholder={enableFor?.external_code || "学籍号"} autoFocus />
          </Field>
          <Field label="口令（≥6 位）">
            <Input type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
          </Field>
          {enableErr && <p className="text-xs text-danger">{enableErr}</p>}
        </div>
      </Modal>
    </Page>
  );
}
