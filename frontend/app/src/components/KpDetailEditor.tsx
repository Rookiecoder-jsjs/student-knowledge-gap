import { ArrowClockwise, Plus, Trash } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Input, Modal } from "./ui";
import {
  ApiError,
  createRelation,
  deleteKp,
  deleteRelation,
  updateKp,
  type KpDetail,
  type KpNode,
  type KpPreviewImpact,
} from "../lib/api";

const RELATION_TYPES = ["prerequisite", "contains", "confusable", "spiral"];
const REL_LABEL: Record<string, string> = {
  prerequisite: "前置",
  contains: "包含",
  confusable: "易混",
  spiral: "螺旋上升",
};

const inputCls =
  "rounded-xl border border-line bg-surface px-3 py-2 text-sm transition-colors focus:border-accent";
const selectCls =
  "rounded-xl border border-line bg-surface px-2.5 py-1.5 text-xs transition-colors focus:border-accent";

/** 知识点详情编辑面板：属性 + 〔v0.2〕preview + 归档/恢复/硬删 + 关系增删。 */
export function KpDetailEditor({
  detail,
  kps,
  onReload,
  onSelect,
}: {
  detail: KpDetail;
  kps: KpNode[];
  onReload: () => void;
  onSelect: (id: number) => void;
}) {
  const [form, setForm] = useState({
    name: detail.name,
    description: detail.description,
    chapter: detail.chapter,
    semester: detail.semester,
    cog: detail.cog_levels_expected.join("、"),
    difficulty_prior: detail.difficulty_prior,
    mastery_floor: detail.mastery_floor,
    importance: detail.importance,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [preview, setPreview] = useState<KpPreviewImpact | null>(null);
  const [archiveConfirm, setArchiveConfirm] = useState(false);
  const [hardDeleteConfirm, setHardDeleteConfirm] = useState(false);

  // detail 变化（reload / 切换 kp）时重置表单
  useEffect(() => {
    setForm({
      name: detail.name,
      description: detail.description,
      chapter: detail.chapter,
      semester: detail.semester,
      cog: detail.cog_levels_expected.join("、"),
      difficulty_prior: detail.difficulty_prior,
      mastery_floor: detail.mastery_floor,
      importance: detail.importance,
    });
    setPreview(null);
    setMsg(null);
    setErr(null);
    setArchiveConfirm(false);
    setHardDeleteConfirm(false);
  }, [detail]);

  const isContainer = detail.code.startsWith("C");
  const hiLeverChanged =
    form.mastery_floor !== detail.mastery_floor ||
    form.difficulty_prior !== detail.difficulty_prior;

  function update<K extends keyof typeof form>(key: K, val: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: val }));
    setPreview(null);
  }

  function buildPayload() {
    const cog = form.cog.split(/[、,，\s]+/).filter(Boolean);
    const cogChanged = cog.join("、") !== detail.cog_levels_expected.join("、");
    return {
      name: form.name !== detail.name ? form.name : undefined,
      description: form.description !== detail.description ? form.description : undefined,
      chapter: form.chapter !== detail.chapter ? form.chapter : undefined,
      semester: form.semester !== detail.semester ? form.semester : undefined,
      cog_levels_expected: cogChanged ? cog : undefined,
      difficulty_prior: form.difficulty_prior !== detail.difficulty_prior ? form.difficulty_prior : undefined,
      mastery_floor: form.mastery_floor !== detail.mastery_floor ? form.mastery_floor : undefined,
      importance: form.importance !== detail.importance ? form.importance : undefined,
    };
  }

  async function doSave() {
    const payload = buildPayload();
    if (!Object.values(payload).some((v) => v !== undefined)) {
      setMsg("无变更");
      setErr(null);
      return;
    }
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      if (hiLeverChanged && !preview) {
        const res = await updateKp(detail.id, payload, true);
        if ("preview" in res) {
          setPreview(res);
          setMsg("请确认影响后再次点击「确认保存」");
        } else {
          onReload();
          setMsg("已保存");
        }
      } else {
        await updateKp(detail.id, payload, false);
        setPreview(null);
        onReload();
        setMsg("已保存");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function doArchive(confirm = false) {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const res = await deleteKp(detail.id, { confirm });
      setArchiveConfirm(false);
      onReload();
      setMsg(
        `已停用（依据 ${res.evidence_refs ?? 0} · 题 ${res.question_refs ?? 0} · 已清进度 ${res.progress_cleared ?? 0}）`
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setArchiveConfirm(true);
        setErr(e.message);
      } else {
        setErr(e instanceof Error ? e.message : "停用失败");
      }
    } finally {
      setBusy(false);
    }
  }

  async function doRestore() {
    setBusy(true);
    setErr(null);
    try {
      await updateKp(detail.id, { archived: false });
      onReload();
      setMsg("已恢复");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "恢复失败");
    } finally {
      setBusy(false);
    }
  }

  async function doHardDelete() {
    setBusy(true);
    setErr(null);
    try {
      await deleteKp(detail.id, { force: true });
      setHardDeleteConfirm(false);
      onReload();
      setMsg("已彻底删除");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "彻底删除失败");
    } finally {
      setBusy(false);
    }
  }

  async function doDeleteRel(relId: number) {
    setBusy(true);
    setErr(null);
    try {
      await deleteRelation(relId);
      onReload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "删除关系失败");
    } finally {
      setBusy(false);
    }
  }

  // 添加关系
  const [relTarget, setRelTarget] = useState("");
  const [relType, setRelType] = useState("prerequisite");
  const [relDir, setRelDir] = useState<"out" | "in">("out");

  async function doAddRel() {
    if (!relTarget) return;
    const targetId = Number(relTarget);
    const from = relDir === "out" ? detail.id : targetId;
    const to = relDir === "out" ? targetId : detail.id;
    setBusy(true);
    setErr(null);
    try {
      await createRelation({ from_kp_id: from, to_kp_id: to, type: relType });
      setRelTarget("");
      onReload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "添加关系失败");
    } finally {
      setBusy(false);
    }
  }

  const relTargets = kps.filter((k) => k.id !== detail.id && !k.archived);

  return (
    <Card className="space-y-4 p-6">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-ink-faint">{detail.code}</span>
          {detail.archived && <Badge tone="warn">已停用</Badge>}
          {isContainer && <Badge tone="neutral">分类节点</Badge>}
        </div>
        <div className="flex items-center gap-1.5">
          {!isContainer &&
            (detail.archived ? (
              <Button variant="ghost" onClick={doRestore} disabled={busy}>
                恢复
              </Button>
            ) : (
              <Button variant="ghost" onClick={() => doArchive(false)} disabled={busy}>
                停用
              </Button>
            ))}
          {!isContainer && !detail.archived && (
            <Button variant="danger" onClick={() => setHardDeleteConfirm(true)} disabled={busy}>
              彻底删除
            </Button>
          )}
        </div>
      </div>

      {err && <p className="text-xs text-danger">{err}</p>}
      {msg && <p className="text-xs text-accent-deep">{msg}</p>}

      {/* 归档二次确认（〔v0.2〕409） */}
      {archiveConfirm && (
        <div className="rounded-xl border border-warn/25 bg-warn-soft p-3 text-xs">
          <p className="text-warn">{err}</p>
          <p className="mt-1 text-ink-soft">
            停用后这些题目的知识点分析将缺失（题目标注保留但不计入）。确认请继续。
          </p>
          <div className="mt-2 flex gap-2">
            <Button variant="danger" onClick={() => doArchive(true)} disabled={busy}>
              确认停用
            </Button>
            <Button variant="ghost" onClick={() => setArchiveConfirm(false)} disabled={busy}>
              取消
            </Button>
          </div>
        </div>
      )}

      {/* 属性表单 */}
      <div className="grid grid-cols-2 gap-3">
        <label className="col-span-2 flex flex-col gap-1 text-sm">
          <span className="text-xs text-ink-faint">名称</span>
          <Input value={form.name} onChange={(e) => update("name", e.target.value)} disabled={busy} />
        </label>
        <label className="col-span-2 flex flex-col gap-1 text-sm">
          <span className="text-xs text-ink-faint">描述</span>
          <textarea
            value={form.description}
            onChange={(e) => update("description", e.target.value)}
            disabled={busy}
            rows={2}
            className={inputCls}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs text-ink-faint">章节</span>
          <Input value={form.chapter} onChange={(e) => update("chapter", e.target.value)} disabled={busy} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs text-ink-faint">学期（1上/2下/0不限）</span>
          <Input
            type="number"
            value={form.semester}
            onChange={(e) => update("semester", Number(e.target.value))}
            disabled={busy}
          />
        </label>
        <label className="col-span-2 flex flex-col gap-1 text-sm">
          <span className="text-xs text-ink-faint">认知层次（顿号分隔）</span>
          <Input value={form.cog} onChange={(e) => update("cog", e.target.value)} disabled={busy} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs text-ink-faint">预估难度</span>
          <Input
            type="number"
            step="0.05"
            value={form.difficulty_prior}
            onChange={(e) => update("difficulty_prior", Number(e.target.value))}
            disabled={busy}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs text-ink-faint">及格线</span>
          <Input
            type="number"
            step="0.05"
            value={form.mastery_floor}
            onChange={(e) => update("mastery_floor", Number(e.target.value))}
            disabled={busy}
          />
        </label>
        <label className="col-span-2 flex flex-col gap-1 text-sm">
          <span className="text-xs text-ink-faint">重要度（报告排序与全局薄弱加权依据）</span>
          <select
            value={form.importance}
            onChange={(e) => update("importance", e.target.value)}
            disabled={busy}
            className={inputCls}
          >
            <option value="基础">基础（地基性，优先补强）</option>
            <option value="核心">核心（章节主干）</option>
            <option value="拓展">拓展（独立/高阶）</option>
          </select>
        </label>
      </div>

      {/* 〔v0.2〕preview 影响数 */}
      {preview && (
        <div className="rounded-xl border border-accent/25 bg-accent-soft p-3 text-xs">
          <p className="font-semibold text-accent-deep">影响预览</p>
          <p className="mt-1">
            当前 {preview.current.weak_count} 人待加强（及格线 {preview.current.floor}）→ 改后{" "}
            {preview.projected.weak_count} 人（及格线 {preview.projected.floor}），Δ{" "}
            {preview.delta > 0 ? "+" : ""}
            {preview.delta}
          </p>
          {preview.note && <p className="mt-1 text-ink-faint">{preview.note}</p>}
        </div>
      )}

      <div className="flex items-center gap-2">
        <Button variant="primary" onClick={doSave} disabled={busy}>
          <ArrowClockwise size={15} />
          {preview ? "确认保存" : "保存"}
        </Button>
        <span className="text-xs text-ink-faint">
          {hiLeverChanged && !preview ? "关键参数改动将先预览影响" : "code 不可改（稳定标识）"}
        </span>
      </div>

      {/* 关系 */}
      <RelSection
        title="直接前置知识"
        items={detail.direct_prerequisites.map((p) => ({
          id: p.id,
          code: p.code,
          name: p.name,
          extra: `权重 ${p.weight.toFixed(2)}`,
        }))}
        onSelect={onSelect}
      />
      <RelSection
        title="后续知识（以本点为前置）"
        items={detail.successors.map((p) => ({
          id: p.id,
          code: p.code,
          name: p.name,
          extra: REL_LABEL[p.type] ?? p.type,
          relId: p.relation_id,
        }))}
        onSelect={onSelect}
        onDelete={doDeleteRel}
        busy={busy}
      />
      <RelSection
        title="所属分类"
        items={detail.containers.map((p) => ({
          id: p.id,
          code: p.code,
          name: p.name,
          extra: REL_LABEL[p.type] ?? p.type,
          relId: p.relation_id,
        }))}
        onSelect={onSelect}
        onDelete={doDeleteRel}
        busy={busy}
      />
      {detail.contained.length > 0 && (
        <RelSection
          title="包含的小节"
          items={detail.contained.map((p) => ({
            id: p.id,
            code: p.code,
            name: p.name,
            extra: REL_LABEL[p.type] ?? p.type,
            relId: p.relation_id,
          }))}
          onSelect={onSelect}
          onDelete={doDeleteRel}
          busy={busy}
        />
      )}

      {/* 添加关系 */}
      <div className="rounded-xl border border-line bg-surface-2 p-3">
        <p className="mb-2 text-xs font-semibold text-ink-soft">添加关系</p>
        <div className="flex flex-wrap items-center gap-2">
          <select value={relType} onChange={(e) => setRelType(e.target.value)} className={selectCls}>
            {RELATION_TYPES.map((t) => (
              <option key={t} value={t}>
                {REL_LABEL[t]}
              </option>
            ))}
          </select>
          <select
            value={relDir}
            onChange={(e) => setRelDir(e.target.value as "out" | "in")}
            className={selectCls}
          >
            <option value="out">本点 → 目标</option>
            <option value="in">目标 → 本点</option>
          </select>
          <select
            value={relTarget}
            onChange={(e) => setRelTarget(e.target.value)}
            className={`min-w-[12rem] flex-1 ${selectCls}`}
          >
            <option value="">选择知识点…</option>
            {relTargets.map((k) => (
              <option key={k.id} value={k.id}>
                {k.code} · {k.name}
              </option>
            ))}
          </select>
          <Button variant="secondary" onClick={doAddRel} disabled={busy || !relTarget}>
            <Plus size={14} /> 添加
          </Button>
        </div>
      </div>

      <p className="text-xs text-ink-faint">
        知识库版本 #{detail.kb_version_id}
      </p>

      {/* 硬删确认：统一 Modal（修 P2-3，替代 window.confirm） */}
      <Modal
        open={hardDeleteConfirm}
        onClose={() => setHardDeleteConfirm(false)}
        title="彻底删除知识点"
        footer={
          <>
            <Button variant="ghost" onClick={() => setHardDeleteConfirm(false)} disabled={busy}>
              取消
            </Button>
            <Button variant="danger" onClick={doHardDelete} disabled={busy}>
              {busy ? "删除中…" : "确认彻底删除"}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-soft">
          彻底删除不可恢复，会级联删除相关关系与教学进度。
        </p>
        <p className="text-xs text-ink-faint">
          若只想从分析中移除，建议改用「停用」（可恢复，且保留题目标注）。
        </p>
      </Modal>
    </Card>
  );
}

function RelSection({
  title,
  items,
  onSelect,
  onDelete,
  busy,
}: {
  title: string;
  items: { id: number; code: string; name: string; extra?: string; relId?: number }[];
  onSelect: (id: number) => void;
  onDelete?: (relId: number) => void;
  busy?: boolean;
}) {
  return (
    <div>
      <p className="mb-1.5 text-xs font-semibold text-ink-soft">
        {title}
        {items.length > 0 && `（${items.length}）`}
      </p>
      {items.length === 0 ? (
        <p className="text-xs text-ink-faint">无</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {items.map((it) => (
            <li key={`${it.id}-${it.relId ?? "g"}`}>
              <span className="inline-flex items-center gap-1 rounded-lg border border-line bg-surface px-2 py-1 text-xs">
                <button onClick={() => onSelect(it.id)} className="transition-colors hover:text-accent">
                  <span className="font-mono text-ink-faint">{it.code}</span>
                  {it.name}
                </button>
                {it.extra && <span className="text-ink-faint">· {it.extra}</span>}
                {onDelete && it.relId !== undefined && (
                  <button
                    onClick={() => onDelete(it.relId!)}
                    disabled={busy}
                    className="text-ink-faint transition-colors hover:text-danger disabled:opacity-50"
                    aria-label="删除关系"
                  >
                    <Trash size={12} />
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
