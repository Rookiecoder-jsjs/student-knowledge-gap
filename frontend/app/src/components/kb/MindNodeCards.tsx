import { BaseEdge, EdgeLabelRenderer, Handle, Position, getBezierPath } from "@xyflow/react";
import type { EdgeProps, NodeProps } from "@xyflow/react";
import { Plus } from "@phosphor-icons/react";
import { Select } from "../ui";
import type { MpKp } from "../../lib/mindmap-model";

/**
 * 导图节点卡与关系边（kb-mindmap-create §4/§5）。
 * 交互语言（2026-09-12 易用性裁决＋按钮方向修正）：悬浮节点浮现 ＋ 按钮，
 * 空间语义对齐三列布局——右侧＝下级（Tab 的效果，子级就在右边一列）、
 * 下方＝同级（Enter 的效果，同级就在下方）；知识点无下级，只有下方同级钮。
 * 教师主路径是「打字 + Enter/Tab」，不用先选中再找动作行；选中动作行只留
 * 升级/↑↓/删 结构微调。章/节的 handle 隐藏不可连——关系线只允许知识点之间。
 */

export interface CardActions {
  onRename: (id: string, name: string) => void;
  onPatchKp: (id: string, patch: Partial<MpKp>) => void;
  onAddSibling: (id: string) => void;
  onAddChild: (id: string) => void;
  onAddChapterKp: (id: string) => void;
  onPromote: (id: string) => void;
  onMove: (id: string, dir: -1 | 1) => void;
  onRemove: (id: string) => void;
}

export interface ChapterData extends Record<string, unknown> {
  id: string;
  name: string;
  sections: number;
  kps: number;
  selected: boolean;
  act: CardActions;
}

export interface SectionData extends Record<string, unknown> {
  id: string;
  name: string;
  kps: number;
  selected: boolean;
  act: CardActions;
}

export interface KpCardData extends Record<string, unknown> {
  id: string;
  kp: MpKp;
  selected: boolean;
  act: CardActions;
}

/** 悬浮浮现的 ＋ 按钮：卡片右侧＝同级，下方＝下级（XMind 手感；易用性主路径）。 */
function FloatAdd({ title, onClick, className }: { title: string; onClick: () => void; className: string }) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={(e) => {
        // 动作钮的 click 不冒泡到 RF 节点选中——否则 onNodeClick 会把 selectedId
        // 改回旧节点（删钮 → 死 id → 下一个 Enter 建同级即崩，2026-09-12 实测）
        e.stopPropagation();
        onClick();
      }}
      className={`nodrag absolute z-10 flex h-7 w-7 cursor-pointer items-center justify-center rounded-full border border-line-strong bg-surface text-accent shadow-soft transition-[opacity,border-color,background-color] opacity-0 hover:border-accent hover:bg-accent-soft group-hover:opacity-100 ${className}`}
    >
      <Plus size={15} weight="bold" />
    </button>
  );
}

function MicroBtn({
  label,
  title,
  onClick,
  danger = false,
}: {
  label: string;
  title: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={(e) => {
        e.stopPropagation(); // 同 FloatAdd：动作钮不改写选中态
        onClick();
      }}
      className={`nodrag cursor-pointer rounded px-1.5 py-0.5 text-[11px] transition-colors ${
        danger ? "text-ink-faint hover:bg-danger/10 hover:text-danger" : "text-ink-soft hover:bg-accent/10 hover:text-accent"
      }`}
    >
      {label}
    </button>
  );
}

const nameInputCls =
  "nodrag w-full rounded bg-transparent px-1 py-0.5 text-sm font-medium text-ink outline-none placeholder:text-ink-faint focus:bg-surface-2";

export function ChapterCard({ id, data }: NodeProps) {
  const d = data as unknown as ChapterData;
  return (
    <div
      className={`group relative w-60 rounded-xl border bg-surface p-2.5 shadow-soft transition-shadow ${
        d.selected ? "border-accent shadow-lift" : "border-line"
      }`}
    >
      <input
        defaultValue={d.name}
        onBlur={(e) => d.act.onRename(d.id, e.target.value)}
        placeholder="输入第一章名称"
        aria-label="章名"
        className={nameInputCls}
      />
      <p className="px-1 text-[11px] tabular-nums text-ink-faint">
        {d.sections} 节 · {d.kps} 点
      </p>
      {d.selected && (
        <div className="mt-1 flex flex-wrap items-center gap-0.5 border-t border-line pt-1.5">
          <MicroBtn label="+点" title="添加直属知识点" onClick={() => d.act.onAddChapterKp(id)} />
          <MicroBtn label="↑" title="上移" onClick={() => d.act.onMove(id, -1)} />
          <MicroBtn label="↓" title="下移" onClick={() => d.act.onMove(id, 1)} />
          <MicroBtn label="删" title="删除章（Delete）" danger onClick={() => d.act.onRemove(id)} />
        </div>
      )}
      <FloatAdd title="添加小节（Tab）" onClick={() => d.act.onAddChild(id)} className="-right-9 top-1/2 -translate-y-1/2" />
      <FloatAdd title="添加同级章（Enter）" onClick={() => d.act.onAddSibling(id)} className="-bottom-9 left-1/2 -translate-x-1/2" />
      {/* 章/节不可连关系线：handle 隐藏且禁连，仅供树边渲染 */}
      <Handle type="target" position={Position.Left} className="kb-handle-ghost" isConnectable={false} />
      <Handle type="source" position={Position.Right} className="kb-handle-ghost" isConnectable={false} />
    </div>
  );
}

export function SectionCard({ id, data }: NodeProps) {
  const d = data as unknown as SectionData;
  return (
    <div
      className={`group relative w-56 rounded-lg border p-2 transition-shadow ${
        d.selected ? "border-accent bg-surface shadow-lift" : "border-line bg-surface-2/70"
      }`}
    >
      <input
        defaultValue={d.name}
        onBlur={(e) => d.act.onRename(id, e.target.value)}
        placeholder="节名"
        aria-label="节名"
        className={nameInputCls}
      />
      <p className="px-1 text-[11px] tabular-nums text-ink-faint">{d.kps} 点</p>
      {d.selected && (
        <div className="mt-1 flex flex-wrap items-center gap-0.5 border-t border-line pt-1.5">
          <MicroBtn label="升级" title="挪到上一章（Shift+Tab）" onClick={() => d.act.onPromote(id)} />
          <MicroBtn label="↑" title="上移" onClick={() => d.act.onMove(id, -1)} />
          <MicroBtn label="↓" title="下移" onClick={() => d.act.onMove(id, 1)} />
          <MicroBtn label="删" title="删除节（Delete）" danger onClick={() => d.act.onRemove(id)} />
        </div>
      )}
      <FloatAdd title="添加知识点（Tab）" onClick={() => d.act.onAddChild(id)} className="-right-8 top-1/2 -translate-y-1/2" />
      <FloatAdd title="添加同级节（Enter）" onClick={() => d.act.onAddSibling(id)} className="-bottom-9 left-1/2 -translate-x-1/2" />
      <Handle type="target" position={Position.Left} className="kb-handle-ghost" isConnectable={false} />
      <Handle type="source" position={Position.Right} className="kb-handle-ghost" isConnectable={false} />
    </div>
  );
}

export function KpCard({ id, data }: NodeProps) {
  const d = data as unknown as KpCardData;
  const kp = d.kp;
  return (
    <div
      className={`group relative w-64 rounded-lg border bg-surface shadow-soft transition-shadow ${
        d.selected ? "border-accent shadow-lift" : "border-line"
      }`}
    >
      <div className="flex items-center gap-1 p-2">
        <input
          defaultValue={kp.code}
          onBlur={(e) => d.act.onPatchKp(id, { code: e.target.value })}
          placeholder="编码"
          aria-label="知识点编码"
          className="nodrag w-[84px] shrink-0 rounded bg-transparent px-1 py-0.5 font-mono text-[11px] text-ink-faint outline-none placeholder:text-ink-faint/60 focus:bg-surface-2"
        />
        <input
          defaultValue={kp.name}
          onBlur={(e) => d.act.onPatchKp(id, { name: e.target.value })}
          placeholder="知识点名称"
          aria-label="知识点名称"
          className="nodrag min-w-0 flex-1 rounded bg-transparent px-1 py-0.5 text-sm font-medium text-ink outline-none placeholder:text-ink-faint focus:bg-surface-2"
        />
      </div>
      {d.selected && (
        <div className="space-y-1.5 border-t border-line px-2 pb-2 pt-1.5">
          <textarea
            rows={2}
            defaultValue={kp.description}
            onBlur={(e) => d.act.onPatchKp(id, { description: e.target.value })}
            placeholder="描述（可选）"
            aria-label="知识点描述"
            className="nodrag w-full rounded border border-line-strong bg-canvas px-2 py-1 text-xs text-ink outline-none transition-colors focus:border-accent"
          />
          <div className="flex items-center justify-between gap-1">
            <Select
              size="sm"
              className="w-24"
              aria-label="重要度"
              value={kp.importance}
              onChange={(e) => d.act.onPatchKp(id, { importance: e.target.value })}
            >
              <option>基础</option>
              <option>核心</option>
              <option>拓展</option>
            </Select>
            <div className="flex items-center gap-0.5">
              <MicroBtn label="升级" title="挪到章直属（Shift+Tab）" onClick={() => d.act.onPromote(id)} />
              <MicroBtn label="↑" title="上移" onClick={() => d.act.onMove(id, -1)} />
              <MicroBtn label="↓" title="下移" onClick={() => d.act.onMove(id, 1)} />
              <MicroBtn label="删" title="删除知识点（Delete）" danger onClick={() => d.act.onRemove(id)} />
            </div>
          </div>
        </div>
      )}
      <FloatAdd title="添加同级知识点（Enter）" onClick={() => d.act.onAddSibling(id)} className="-bottom-8 left-1/2 -translate-x-1/2" />
      <Handle type="target" position={Position.Left} className="kb-handle" />
      <Handle type="source" position={Position.Right} className="kb-handle" />
    </div>
  );
}

export interface RelationEdgeData extends Record<string, unknown> {
  label: string;
  cls: string;
  chipCls: string;
  onDelete: (id: string) => void;
}

/** 关系边：信号色描边 + 类型小标签（hover 出删除钮）。 */
export function RelationEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data }: EdgeProps) {
  const d = data as unknown as RelationEdgeData;
  const [path] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  return (
    <>
      <BaseEdge id={id} path={path} className={d.cls} strokeWidth={1.5} />
      <EdgeLabelRenderer>
        <div
          className="nodrag pointer-events-none absolute"
          style={{
            transform: `translate(-50%, -50%) translate(${(sourceX + targetX) / 2}px, ${(sourceY + targetY) / 2}px)`,
          }}
        >
          <span
            className={`group pointer-events-auto inline-flex cursor-pointer items-center gap-0.5 rounded-full border bg-surface px-1.5 py-px text-[10px] font-medium ${d.chipCls}`}
          >
            {d.label}
            <button
              type="button"
              onClick={() => d.onDelete(id)}
              className="hidden opacity-60 transition-opacity hover:opacity-100 group-hover:inline"
              aria-label="删除关系"
            >
              ✕
            </button>
          </span>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
