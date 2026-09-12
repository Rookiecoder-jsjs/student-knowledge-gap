import { Trash } from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createKbLibrary } from "../../lib/kb-create";
import type { KbBasic, KpDraft, KpRelationDraft } from "../../lib/kb-create";
import { countKps, emptyTree, flattenTree } from "../../lib/mindmap-model";
import type { MpEdge, MpTree } from "../../lib/mindmap-model";
import { setBackTarget } from "../../lib/portal";
import { Button, Card, PageHeader } from "../ui";
import BasicInfoForm from "./BasicInfoForm";
import MindMapEditor from "./MindMapEditor";

const DRAFT_KEY = "sc.kb-mindmap-draft";

interface Draft {
  tree: MpTree;
  edges: MpEdge[];
  savedAt: number;
}

function outlineText(tree: MpTree): string {
  const lines: string[] = [];
  for (const c of tree.chapters) {
    lines.push(c.name);
    for (const s of c.sections) {
      lines.push(`  ${s.name}`);
      for (const k of s.kps) lines.push(`    ${[k.code, k.name].filter(Boolean).join(" ")}`);
    }
    for (const k of c.kps) lines.push(`  ${[k.code, k.name].filter(Boolean).join(" ")}`);
  }
  return lines.join("\n");
}

function readDraft(): Draft | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw) as Draft;
    return d?.tree?.chapters?.length ? d : null;
  } catch {
    return null;
  }
}

function writeDraft(d: Draft) {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
  } catch {
    /* 存储不可用则跳过（隐私模式） */
  }
}

/**
 * 思维导图建库模式（kb-mindmap-create §4）：基本信息（共用）+ 画布 + 大纲粘贴 +
 * localStorage 草稿 + 共享创建管道（知识点与关系两阶段）。<md 画布只读提示。
 */
export default function MindMapMode({
  basic,
  onBasicChange,
  backTo,
}: {
  basic: KbBasic;
  onBasicChange: (b: KbBasic) => void;
  backTo: string;
}) {
  const nav = useNavigate();
  const [tree, setTree] = useState<MpTree>(() => emptyTree());
  const [edges, setEdges] = useState<MpEdge[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ phase: "kp" | "relation"; done: number; total: number } | null>(null);
  const [failedKps, setFailedKps] = useState<KpDraft[]>([]);
  const [failedRelations, setFailedRelations] = useState<KpRelationDraft[]>([]);
  const [kbId, setKbId] = useState<number | null>(null);
  const [draftOffer, setDraftOffer] = useState<Draft | null>(null);

  // 进画布时探测草稿；编辑防抖 3s 自动存
  useEffect(() => {
    setDraftOffer(readDraft());
  }, []);
  useEffect(() => {
    const t = setTimeout(() => writeDraft({ tree, edges, savedAt: Date.now() }), 3000);
    return () => clearTimeout(t);
  }, [tree, edges]);

  const flat = useMemo(() => flattenTree(tree, edges), [tree, edges]);
  const kpTotal = useMemo(
    () => tree.chapters.reduce((n, c) => n + countKps(c), 0),
    [tree]
  );
  // 基本信息校验与树校验合并（学科为空也能创建的缺口，2026-09-12 验收发现）
  const problems = useMemo(() => {
    const basicProblems: string[] = [];
    if (!basic.subject.trim()) basicProblems.push("缺学科");
    if (!basic.edition.trim()) basicProblems.push("缺教材版本");
    if (!basic.version.trim()) basicProblems.push("缺版本号");
    return [...basicProblems, ...flat.problems];
  }, [basic, flat.problems]);

  function restoreDraft() {
    if (!draftOffer) return;
    setTree(draftOffer.tree);
    setEdges(draftOffer.edges ?? []);
    setDraftOffer(null);
  }

  function discardDraft() {
    try {
      localStorage.removeItem(DRAFT_KEY);
    } catch {
      /* 忽略 */
    }
    setDraftOffer(null);
  }

  async function doCreate(kps: KpDraft[], relations: KpRelationDraft[]) {
    if (!basic.subject.trim() || !basic.edition.trim() || !basic.version.trim()) return; // 兜底（按钮已禁用）
    setBusy(true);
    setErr(null);
    setProgress(null);
    try {
      const r = await createKbLibrary(basic, kps, relations, setProgress, kbId ?? undefined);
      setKbId(r.kbId);
      setFailedKps(r.failedKps);
      setFailedRelations(r.failedRelations);
      if (r.failedKps.length === 0 && r.failedRelations.length === 0) {
        try {
          localStorage.removeItem(DRAFT_KEY);
        } catch {
          /* 忽略 */
        }
        // 落在知识库工作台并链式保留返回来源（与向导一致）
        setBackTarget("/kb", backTo === "/kb" ? null : backTo);
        nav("/kb", { state: { kbVersionId: r.kbId } });
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="思维导图建库" desc="像画导图一样搭教材结构：章 → 节 → 知识点，知识点间拖线标关系，一键创建草稿版本" />

      {err && <p className="mb-3 text-xs text-danger">{err}</p>}

      <div className="grid gap-4 lg:grid-cols-[minmax(20rem,24rem)_1fr]">
        <Card className="h-fit p-4">
          <BasicInfoForm value={basic} onChange={onBasicChange} showPrefix />
        </Card>

        <div className="min-w-0 space-y-3">
          {draftOffer && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-info/25 bg-info-soft px-3 py-2 text-xs">
              <span className="text-ink-soft">
                检测到 {new Date(draftOffer.savedAt).toLocaleString()} 保存的草稿
              </span>
              <span className="flex gap-2">
                <Button size="sm" variant="secondary" onClick={restoreDraft}>
                  恢复草稿
                </Button>
                <Button size="sm" variant="ghost" onClick={discardDraft}>
                  <Trash size={13} />
                  丢弃
                </Button>
              </span>
            </div>
          )}

          {problems.length > 0 && (
            <p className="text-xs text-warn">{problems[0]}{problems.length > 1 && `（等 ${problems.length} 个问题）`}</p>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-ink-faint">
              打字起名后按 Enter 建同级、Tab 建下级；悬浮卡片右侧＋建下级、下方＋建同级；拖知识点侧边圆点连线标关系
            </p>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="primary"
                disabled={busy || problems.length > 0}
                onClick={() => doCreate(flat.kps, flat.relations)}
              >
                {busy ? "创建中…" : `创建（${kpTotal} 点 ${edges.length} 关系）`}
              </Button>
            </div>
          </div>

          <div className="hidden md:block">
            <MindMapEditor
              tree={tree}
              edges={edges}
              codePrefix={basic.codePrefix ?? ""}
              busy={busy}
              onTreeChange={setTree}
              onEdgesChange={setEdges}
            />
          </div>
          <div className="rounded-xl border border-line bg-surface p-4 text-sm md:hidden">
            <p className="font-medium text-ink">导图编辑需在桌面端使用</p>
            <p className="mt-1 text-xs text-ink-soft">窄屏下可查看当前结构；请在 ≥768px 的窗口中编辑。</p>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-canvas p-3 text-xs text-ink-soft">
              {outlineText(tree)}
            </pre>
          </div>

          {progress && (
            <p className="text-xs text-accent-deep" aria-live="polite">
              {progress.phase === "kp"
                ? `写入知识点… ${progress.done}/${progress.total}`
                : `写入关系… ${progress.done}/${progress.total}`}
              {(failedKps.length > 0 || failedRelations.length > 0) && `（失败 ${failedKps.length + failedRelations.length}）`}
            </p>
          )}
          {(failedKps.length > 0 || failedRelations.length > 0) && (
            <div className="rounded-lg border border-warn/25 bg-warn-soft p-3 text-xs">
              <p className="font-semibold text-warn">部分写入失败（编码冲突或校验拒绝）</p>
              {failedKps.length > 0 && (
                <p className="mt-1 text-ink-soft">知识点：{failedKps.map((k) => k.code).join("、")}</p>
              )}
              {failedRelations.length > 0 && (
                <p className="mt-1 text-ink-soft">
                  关系：{failedRelations.map((r) => `${r.from_code}→${r.to_code}`).join("、")}
                </p>
              )}
              <div className="mt-2">
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => doCreate(failedKps, failedRelations)}>
                  重试失败项
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
