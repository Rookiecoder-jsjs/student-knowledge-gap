import { CheckCircle, XCircle } from "@phosphor-icons/react";
import { useState } from "react";
import { Badge, Button, Card, Page, PageHeader, Skeleton } from "../components/ui";
import { ACCENTS } from "../lib/theme";
import {
  inboxList,
  issueReport,
  rejectReport,
  reportFull,
  type InboxItem,
  type ReportFull,
} from "../lib/api";

/**
 * 审批收件箱（agent-product-design §4.3/§5.3，Phase 2 批次B）。
 *
 * 列表项：草稿类型 / 班级或学生主体 / 时间 / 差异预览；行内签发或打回。
 * 打回必须附理由（后端强校验）；签发为终态——再起草走新建报告。
 */

export default function Inbox() {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [full, setFull] = useState<ReportFull | null>(null);

  const reload = async () => {
    try {
      const data = await inboxList("draft");
      setItems(data.items);
      setTotal(data.total);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // 首次加载（避免 useAsync 依赖签发后的刷新逻辑）
  const [loaded, setLoaded] = useState(false);
  if (!loaded) {
    setLoaded(true);
    void reload();
  }

  const doIssue = async (id: number) => {
    setBusyId(id);
    setError(null);
    try {
      await issueReport(id);
      if (full?.report_id === id) setFull(null);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const doReject = async (it: InboxItem) => {
    const note = window.prompt(`打回「${it.type_label}」请附理由（将随草稿留档）：`);
    if (!note || !note.trim()) return;
    setBusyId(it.report_id);
    setError(null);
    try {
      await rejectReport(it.report_id, note.trim());
      if (full?.report_id === it.report_id) setFull(null);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Page accent={ACCENTS.dashboard}>
      <PageHeader
        title="待签发"
        desc="Agent 起草的报告在此等待教师确认；签发即生效，打回需附理由"
      />

      {error && (
        <Card className="mb-4 border-danger/30 bg-danger/5 p-3 text-sm text-danger">
          {error}
        </Card>
      )}

      {!items ? (
        <Skeleton rows={4} />
      ) : items.length === 0 ? (
        <Card className="p-10 text-center text-sm text-ink-faint">
          暂无待签发的草稿。AI 起草的班级诊断单、改进意见会出现在这里。
        </Card>
      ) : (
        <div className="space-y-4">
          <p className="text-xs text-ink-faint">共 {total} 份草稿待处理</p>

          {/* 全文视图（点开某份草稿时） */}
          {full && (
            <Card className="border-accent/40 p-5">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Badge tone="accent">{full.type_label}</Badge>
                  <span className="text-xs text-ink-faint">
                    {full.generated_at?.replace("T", " ").slice(0, 16)}
                  </span>
                </div>
                <Button variant="secondary" onClick={() => setFull(null)}>
                  收起
                </Button>
              </div>
              <pre className="max-h-[420px] overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed text-ink">
                {full.markdown}
              </pre>
            </Card>
          )}

          {items.map((it) => (
            <Card key={it.report_id} className="p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Badge tone="accent">{it.type_label}</Badge>
                  {it.subject && (
                    <span className="text-sm font-medium text-ink">{it.subject}</span>
                  )}
                  <span className="text-xs text-ink-faint">
                    {it.generated_at?.replace("T", " ").slice(0, 16)}
                  </span>
                  {it.writer?.model && (
                    <Badge tone="neutral">AI 起草 · {it.writer.model}</Badge>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="secondary"
                    onClick={async () => {
                      setError(null);
                      try {
                        setFull(await reportFull(it.report_id));
                        window.scrollTo({ top: 0, behavior: "smooth" });
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    查看全文
                  </Button>
                  <Button
                    onClick={() => doIssue(it.report_id)}
                    disabled={busyId === it.report_id}
                  >
                    <CheckCircle size={15} weight="fill" />
                    签发
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => doReject(it)}
                    disabled={busyId === it.report_id}
                  >
                    <XCircle size={15} />
                    打回
                  </Button>
                </div>
              </div>
              <p className="mt-3 line-clamp-2 text-sm leading-relaxed text-ink-soft">
                {it.preview}
              </p>
            </Card>
          ))}
        </div>
      )}
    </Page>
  );
}
