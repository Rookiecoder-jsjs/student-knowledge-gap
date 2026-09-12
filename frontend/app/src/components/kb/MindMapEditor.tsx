import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
} from "@xyflow/react";
import type { Connection, Edge, EdgeTypes, Node, NodeTypes } from "@xyflow/react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { REL_CHIP_CLASS, REL_EDGE_CLASS, REL_LABEL } from "../../lib/relation-types";
import type { RelationType } from "../../lib/relation-types";
import { layoutTree } from "../../lib/mindmap-layout";
import {
  addChapterKp,
  addChild,
  addSibling,
  cleanEdges,
  edgeError,
  kpIdSet,
  locate,
  moveWithin,
  nid,
  promote,
  removeNode,
  updateNode,
} from "../../lib/mindmap-model";
import type { MpEdge, MpKp, MpTree } from "../../lib/mindmap-model";
import { useToast } from "../../lib/toast";
import { ChapterCard, KpCard, RelationEdge, SectionCard } from "./MindNodeCards";
import type { CardActions, ChapterData, KpCardData, SectionData } from "./MindNodeCards";

/**
 * 导图画布（kb-mindmap-create §5）：受控组件——树/边状态在上层，键盘（Enter 兄弟 /
 * Tab 子级 / Shift+Tab 升级 / Delete 删除）与拖线建关系在此截获；布局永远自动。
 */

const NODE_TYPES: NodeTypes = { chapter: ChapterCard, section: SectionCard, kp: KpCard };
const EDGE_TYPES: EdgeTypes = { relation: RelationEdge };

export interface MindMapEditorProps {
  tree: MpTree;
  edges: MpEdge[];
  codePrefix: string;
  busy: boolean;
  /** 支持函数式更新——结构操作与字段提交可连续组合（setTree/setEdges 直接兼容）。 */
  onTreeChange: (t: MpTree | ((prev: MpTree) => MpTree)) => void;
  onEdgesChange: (e: MpEdge[] | ((prev: MpEdge[]) => MpEdge[])) => void;
}

interface PendingConn {
  from: string;
  to: string;
  x: number;
  y: number;
}

export default function MindMapEditor({ tree, edges, codePrefix, busy, onTreeChange, onEdgesChange }: MindMapEditorProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingConn | null>(null);
  const connRef = useRef<Connection | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const removedRef = useRef<string[]>([]);
  const toast = useToast();

  // 焦点请求消费：按 data-id 直查节点卡名称输入并全选。两层时序对抗：
  // ① rAF 轮询等 RF 把新节点挂上 DOM；② 挂载期 RF 测量/fitView 的重渲染会
  // 吞掉刚落的焦点（2026-09-12 实证）——60/120/240ms 断言重落，直至稳住。
  useEffect(() => {
    if (!focusId) return;
    let raf = 0;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tryFocus = () => {
      const input = wrapRef.current?.querySelector<HTMLInputElement>(
        `.react-flow__node[data-id="${focusId}"] input[aria-label='章名'], .react-flow__node[data-id="${focusId}"] input[aria-label='节名'], .react-flow__node[data-id="${focusId}"] input[aria-label='知识点名称']`
      );
      if (!input) {
        if (tries++ < 30) raf = requestAnimationFrame(tryFocus);
        return;
      }
      input.focus();
      input.select();
      let n = 0;
      const assert = () => {
        if (document.activeElement === input) {
          setFocusId(null);
          return;
        }
        if (n++ < 3) {
          input.focus();
          input.select();
          timer = setTimeout(assert, 60 * 2 ** n);
        } else {
          setFocusId(null); // 放弃：用户可能主动点了别处
        }
      };
      timer = setTimeout(assert, 60);
    };
    raf = requestAnimationFrame(tryFocus);
    return () => {
      cancelAnimationFrame(raf);
      if (timer) clearTimeout(timer);
    };
  }, [focusId]);

  // 空树首挂：光标直接落在首章名称上——教师进页即可打字（易用性主路径）
  const emptyOnMount = useRef(
    tree.chapters.length === 1 &&
      !tree.chapters[0].name &&
      !tree.chapters[0].sections.length &&
      !tree.chapters[0].kps.length
  );
  useEffect(() => {
    if (emptyOnMount.current) {
      emptyOnMount.current = false;
      setSelectedId(tree.chapters[0].id);
      setFocusId(tree.chapters[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const kpIds = useMemo(() => kpIdSet(tree), [tree]);
  const nameOf = useCallback(
    (id: string) => {
      const hit = locate(tree, id);
      return hit?.kind === "kp" ? hit.kp.name || hit.kp.code || "未命名" : "未知节点";
    },
    [tree]
  );

  const deleteEdge = useCallback(
    (id: string) => onEdgesChange(edges.filter((e) => e.id !== id)),
    [edges, onEdgesChange]
  );

  const act: CardActions = useMemo(
    () => ({
      onRename: (id, name) => onTreeChange((prev) => updateNode(prev, id, { name })),
      onPatchKp: (id, patch: Partial<MpKp>) => onTreeChange((prev) => updateNode(prev, id, patch)),
      onAddSibling: (id) => {
        const nid2 = nid();
        // `?? prev`：selectedId 意外指向已删节点时静默降级，绝不白屏
        onTreeChange((prev) => addSibling(prev, id, { codePrefix, newId: nid2 })?.tree ?? prev);
        setSelectedId(nid2);
        setFocusId(nid2);
      },
      onAddChild: (id) => {
        // kp 无下级；kind 不随任何操作改变，快照判定安全
        if (locate(tree, id)?.kind === "kp") {
          toast("知识点没有下级");
          return;
        }
        const nid2 = nid();
        onTreeChange((prev) => addChild(prev, id, { codePrefix, newId: nid2 })?.tree ?? prev);
        setSelectedId(nid2);
        setFocusId(nid2);
      },
      onAddChapterKp: (id) => {
        const nid2 = nid();
        onTreeChange((prev) => addChapterKp(prev, id, codePrefix, nid2)?.tree ?? prev);
        setSelectedId(nid2);
        setFocusId(nid2);
      },
      onPromote: (id) => {
        onTreeChange((prev) => promote(prev, id).tree);
        setFocusId(id);
      },
      onMove: (id, dir) => onTreeChange((prev) => moveWithin(prev, id, dir)),
      onRemove: (id) => {
        // 两个 updater 同批按序执行：树更新器先记下被删 kp id，边更新器随后清理。
        // 删光唯一的章时预生成补种 id：补一个空首章并重落光标，从零再录
        // （removeNode 内部以「删后 chapters 为空」为准，此处同谓词仅决定是否聚焦）
        const reseedId = tree.chapters.length === 1 && tree.chapters[0].id === id ? nid() : null;
        onTreeChange((prev) => {
          const r = removeNode(prev, id, reseedId ?? undefined);
          removedRef.current = r.removed;
          return r.tree;
        });
        onEdgesChange((prev) => cleanEdges(prev, removedRef.current));
        setSelectedId(reseedId);
        if (reseedId) setFocusId(reseedId);
      },
    }),
    [tree, codePrefix, onTreeChange, onEdgesChange, toast]
  );

  const { nodes, rfEdges } = useMemo(() => {
    const positions = layoutTree(tree, selectedId);
    const nodes: Node[] = [];
    const kpNode = (k: MpKp) => {
      const data: KpCardData = { id: k.id, kp: k, selected: selectedId === k.id, act };
      nodes.push({ id: k.id, type: "kp", position: positions.get(k.id)!, draggable: false, data });
    };
    for (const c of tree.chapters) {
      const cd: ChapterData = {
        id: c.id, name: c.name, sections: c.sections.length, kps: c.kps.length,
        selected: selectedId === c.id, act,
      };
      nodes.push({ id: c.id, type: "chapter", position: positions.get(c.id)!, draggable: false, data: cd });
      for (const s of c.sections) {
        const sd: SectionData = { id: s.id, name: s.name, kps: s.kps.length, selected: selectedId === s.id, act };
        nodes.push({ id: s.id, type: "section", position: positions.get(s.id)!, draggable: false, data: sd });
        for (const k of s.kps) kpNode(k);
      }
      for (const k of c.kps) kpNode(k);
    }

    const rfEdges: Edge[] = [];
    const treeEdge = (source: string, target: string) =>
      rfEdges.push({ id: `t-${source}-${target}`, source, target, type: "smoothstep", className: "kb-edge-tree" });
    for (const c of tree.chapters) {
      for (const s of c.sections) {
        treeEdge(c.id, s.id);
        for (const k of s.kps) treeEdge(s.id, k.id);
      }
      for (const k of c.kps) treeEdge(c.id, k.id);
    }
    for (const e of edges) {
      rfEdges.push({
        id: e.id,
        source: e.from,
        target: e.to,
        type: "relation",
        zIndex: 10,
        data: {
          label: REL_LABEL[e.type],
          cls: REL_EDGE_CLASS[e.type],
          chipCls: REL_CHIP_CLASS[e.type],
          onDelete: deleteEdge,
        },
      });
    }
    return { nodes, rfEdges };
  }, [tree, edges, selectedId, act, deleteEdge]);

  /** 把焦点所在字段提交进树（非受控输入的统一落账）；不在本画布字段内则忽略。 */
  const commitFocusedField = () => {
    const el = document.activeElement;
    if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)) return;
    if (!wrapRef.current?.contains(el)) return;
    const nodeId = el.closest(".react-flow__node")?.getAttribute("data-id");
    if (!nodeId) return;
    const field = el.getAttribute("aria-label");
    if (field === "章名" || field === "节名" || field === "知识点名称") {
      onTreeChange((prev) => updateNode(prev, nodeId, { name: el.value }));
    } else if (field === "知识点编码") {
      onTreeChange((prev) => updateNode(prev, nodeId, { code: el.value }));
    } else if (field === "知识点描述") {
      onTreeChange((prev) => updateNode(prev, nodeId, { description: el.value }));
    }
  };

  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (busy) return;
    if (e.key === "Escape") {
      setPending(null);
      return;
    }
    if (!selectedId) return;
    const target = e.target as HTMLElement;
    const inField = target.tagName === "INPUT" || target.tagName === "TEXTAREA";
    if (e.key === "Enter") {
      if (target.tagName === "TEXTAREA") return; // 描述里 Enter = 换行
      e.preventDefault();
      commitFocusedField();
      act.onAddSibling(selectedId);
    } else if (e.key === "Tab") {
      e.preventDefault();
      commitFocusedField();
      if (e.shiftKey) act.onPromote(selectedId);
      else act.onAddChild(selectedId);
    } else if (e.key === "Delete" || e.key === "Backspace") {
      if (inField) return; // 输入框内正常编辑文本
      e.preventDefault();
      act.onRemove(selectedId);
    }
  };

  const commitPending = (type: RelationType) => {
    if (!pending) return;
    const err = edgeError(tree, edges, pending.from, pending.to, type);
    if (err) {
      toast(err);
      setPending(null);
      return;
    }
    onEdgesChange([...edges, { id: nid(), from: pending.from, to: pending.to, type }]);
    toast(`已连「${nameOf(pending.from)}」${REL_LABEL[type]} → 「${nameOf(pending.to)}」`);
    setPending(null);
  };

  return (
    <div
      ref={wrapRef}
      className="h-[min(68vh,700px)] min-h-[440px] overflow-hidden rounded-xl border border-line bg-canvas outline-none"
      tabIndex={0}
      onKeyDown={onKeyDown}
    >
      <ReactFlow
        nodes={nodes}
        edges={rfEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        nodesDraggable={false}
        onNodesChange={() => {}} // 选择/拖拽由本层状态接管，屏蔽 RF 内部变更
        deleteKeyCode={null} // 删除由本层键盘统一处理（级联清边）
        minZoom={0.2}
        maxZoom={1.5}
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
        onPaneClick={() => setSelectedId(null)}
        onNodeClick={(_, node) => setSelectedId(node.id)}
        isValidConnection={(c) => kpIds.has(c.source) && kpIds.has(c.target) && c.source !== c.target}
        onConnect={(c) => {
          connRef.current = c;
        }}
        onConnectEnd={(evt) => {
          const c = connRef.current;
          connRef.current = null;
          if (!c?.source || !c?.target) return;
          const pt =
            "clientX" in evt
              ? { x: evt.clientX, y: evt.clientY }
              : { x: evt.changedTouches[0].clientX, y: evt.changedTouches[0].clientY };
          setPending({ from: c.source, to: c.target, ...pt });
        }}
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1.2} />
        <Controls position="bottom-right" showInteractive={false} />
        <MiniMap position="bottom-left" pannable zoomable nodeColor={() => "#94a3b8"} className="kb-minimap" />
      </ReactFlow>

      {/* 关系类型选择（拖线落点弹出，kb-mindmap-create §1 交互定案） */}
      {pending && (
        <div className="fixed inset-0 z-50" onClick={() => setPending(null)}>
          <div
            className="absolute w-56 rounded-xl border border-line-strong bg-surface p-3 shadow-float"
            style={{ left: pending.x, top: pending.y, transform: "translate(-50%, 8px)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <p className="mb-2 text-xs text-ink-soft">
              <span className="font-medium text-ink">{nameOf(pending.from)}</span>
              <span className="mx-1 text-ink-faint">→</span>
              <span className="font-medium text-ink">{nameOf(pending.to)}</span>
            </p>
            <div className="grid grid-cols-2 gap-1.5">
              {(Object.keys(REL_LABEL) as RelationType[]).map((t) => (
                <button
                  key={t}
                  onClick={() => commitPending(t)}
                  className={`cursor-pointer rounded-lg border px-2 py-1.5 text-xs font-medium transition-colors hover:bg-surface-2 ${REL_CHIP_CLASS[t]}`}
                >
                  {REL_LABEL[t]}
                </button>
              ))}
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
              方向：前者 → 后者。树层级不会生成「包含」关系；创建时随知识点一并写入。
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
