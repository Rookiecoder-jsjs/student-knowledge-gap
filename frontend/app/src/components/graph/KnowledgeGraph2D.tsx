import { useEffect, useMemo, useRef, useState } from "react";
import { hierarchy, pack, type HierarchyCircularNode } from "d3-hierarchy";
import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomBehavior } from "d3-zoom";
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from "../../lib/types";

const NODE_COLORS = {
  good: "#2dd4bf",
  watch: "#f5b04d",
  weak: "#f87171",
  no_data: "#94a3b8",
  not_learned: "#94a3b8",
} as const;

const EDGE_COLORS: Record<string, string> = {
  prerequisite: "#5eead4",
  contains: "#7dd3fc",
  confusable: "#f5b04d",
  spiral: "#a78bfa",
};

const CHAPTER_COLORS = ["#0f766e", "#2563eb", "#7c3aed", "#c2410c", "#be185d", "#0e7490"];
const PACK_SIZE = 760;
const MAX_RELATION_LINES = 220;

type NodeState = keyof typeof NODE_COLORS;

export interface KnowledgeGraph2DProps {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  selectedId?: number | null;
  onSelect?: (node: KnowledgeGraphNode | null) => void;
  className?: string;
}

interface PackDatum {
  id: number;
  name: string;
  kind: "root" | "chapter" | "node";
  value?: number;
  node?: KnowledgeGraphNode;
  children?: PackDatum[];
}

type PackNode = HierarchyCircularNode<PackDatum>;

interface PackLayout {
  root: PackNode;
  nodeById: Map<number, PackNode>;
  width: number;
  height: number;
}

/**
 * 只读知识结构 2D 圆包图。父圆表达年级/章节，叶圆表达知识点，圆面积按
 * 知识点重要度编码；掌握度仍由叶圆颜色表达。圆包布局是确定性的，不会像
 * 全局力导向图一样持续抖动，适合完整知识库的总览。
 */
export default function KnowledgeGraph2D({
  nodes,
  edges,
  selectedId,
  onSelect,
  className = "",
}: KnowledgeGraph2DProps) {
  const [localSelectedId, setLocalSelectedId] = useState<number | null>(selectedId ?? null);
  const [hoveredId, setHoveredId] = useState<number | null>(null);
  const [mobile, setMobile] = useState(false);
  const [showRelations, setShowRelations] = useState(false);
  const [query, setQuery] = useState("");
  const [zoomPercent, setZoomPercent] = useState(100);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const zoomLayerRef = useRef<SVGGElement | null>(null);
  const zoomBehaviorRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const activeId = selectedId ?? localSelectedId;

  const visibleNodes = nodes;
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => edges.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to)),
    [edges, visibleIds],
  );
  const layout = useMemo(() => buildPackLayout(visibleNodes), [visibleNodes]);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matchedIds = useMemo(() => {
    if (!normalizedQuery) return new Set<number>();
    return new Set(
      visibleNodes
        .filter((node) => `${node.name} ${node.code} ${node.chapter ?? ""}`.toLocaleLowerCase().includes(normalizedQuery))
        .map((node) => node.id),
    );
  }, [normalizedQuery, visibleNodes]);
  const matchedBranchIds = useMemo(() => {
    const branchIds = new Set<number>();
    if (!normalizedQuery) return branchIds;
    layout.root.each((node) => {
      if (node.data.kind === "node" && matchedIds.has(node.data.id)) {
        node.ancestors().forEach((ancestor) => branchIds.add(ancestor.data.id));
      }
    });
    return branchIds;
  }, [layout.root, matchedIds, normalizedQuery]);

  useEffect(() => {
    setLocalSelectedId(selectedId ?? null);
  }, [selectedId]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const svgElement = svgRef.current;
    const layer = zoomLayerRef.current;
    if (!svgElement || !layer) return;
    const svgSelection = select(svgElement);
    const behavior = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.65, 3.2])
      .on("zoom", ({ transform }) => {
        select(layer).attr("transform", transform.toString());
        setZoomPercent(Math.round(transform.k * 100));
      });
    svgSelection.call(behavior);
    zoomBehaviorRef.current = behavior;
    behavior.transform(svgSelection, zoomIdentity);
    return () => {
      svgSelection.on(".zoom", null);
      zoomBehaviorRef.current = null;
    };
  }, [layout.height, layout.width]);

  if (nodes.length === 0) {
    return <div className={`rounded-xl border border-line bg-canvas p-8 text-center text-sm text-ink-faint ${className}`}>暂无可展示的知识点</div>;
  }

  const selectNode = (node: KnowledgeGraphNode | null) => {
    setLocalSelectedId(node?.id ?? null);
    onSelect?.(node);
  };
  const adjustZoom = (factor: number) => {
    const svgElement = svgRef.current;
    const behavior = zoomBehaviorRef.current;
    if (!svgElement || !behavior) return;
    behavior.scaleBy(select(svgElement), factor);
  };
  const resetZoom = () => {
    const svgElement = svgRef.current;
    const behavior = zoomBehaviorRef.current;
    if (!svgElement || !behavior) return;
    behavior.transform(select(svgElement), zoomIdentity);
  };
  const fallback = mobile;
  const chapterNodes = layout.root.descendants().filter((node) => node.data.kind === "chapter");
  const leafNodes = layout.root.descendants().filter((node) => node.data.kind === "node");
  const root = layout.root;

  return (
    <div className={`overflow-hidden rounded-xl border border-line bg-canvas ${className}`}>
      {fallback ? (
        <FallbackList nodes={nodes} selectedId={activeId} onSelect={selectNode} />
      ) : (
        <div className="flex h-[clamp(520px,72vh,820px)] min-h-[520px] w-full flex-col overflow-hidden p-2 sm:p-3">
          <div className="mb-2 flex shrink-0 flex-wrap items-center gap-2" role="toolbar" aria-label="知识图谱工具">
            <label className="flex min-w-[220px] flex-1 items-center gap-2 text-xs text-ink-faint">
              <span className="shrink-0">查找知识点</span>
              <input
                type="search"
                id="knowledge-graph-search"
                name="knowledgeGraphSearch"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="名称、编码或章节"
                aria-label="查找知识点"
                className="min-w-0 flex-1 rounded-md border border-line bg-canvas px-2.5 py-1.5 text-xs text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent"
              />
            </label>
            <button
              type="button"
              onClick={() => adjustZoom(1.2)}
              aria-label="放大知识图谱"
              className="h-7 w-7 rounded-md border border-line text-base leading-none text-ink-soft transition-colors hover:bg-surface-2"
            >
              +
            </button>
            <span className="min-w-10 text-center text-[11px] tabular-nums text-ink-faint" aria-live="polite">
              {zoomPercent}%
            </span>
            <button
              type="button"
              onClick={() => adjustZoom(0.84)}
              aria-label="缩小知识图谱"
              className="h-7 w-7 rounded-md border border-line text-base leading-none text-ink-soft transition-colors hover:bg-surface-2"
            >
              −
            </button>
            <button
              type="button"
              onClick={resetZoom}
              className="rounded-md border border-line px-2.5 py-1.5 text-[11px] text-ink-soft transition-colors hover:bg-surface-2"
            >
              重置视图
            </button>
            <button
              type="button"
              aria-pressed={showRelations}
              onClick={() => setShowRelations((current) => !current)}
              className="rounded-md border border-line px-2.5 py-1.5 text-[11px] text-ink-soft transition-colors hover:bg-surface-2"
            >
              {showRelations ? "隐藏关系" : "显示关系"}
            </button>
            {normalizedQuery && (
              <span className="text-[11px] text-ink-faint">
                {matchedIds.size > 0 ? `匹配 ${matchedIds.size} 个` : "未找到匹配知识点"}
              </span>
            )}
          </div>
          <svg
            ref={svgRef}
            className="min-h-0 w-full flex-1"
            viewBox={`0 0 ${layout.width} ${layout.height}`}
            preserveAspectRatio="xMidYMid meet"
            role="group"
            aria-label="知识结构圆包图"
            style={{ touchAction: "none" }}
            onClick={() => selectNode(null)}
          >
            <rect x="0" y="0" width={layout.width} height={layout.height} rx="18" fill="currentColor" className="text-surface-2/30" />
            <g ref={zoomLayerRef}>
              <circle cx={root.x} cy={root.y} r={root.r} fill="currentColor" className="text-canvas" stroke="currentColor" strokeOpacity="0.24" strokeWidth="2" />
              <text
                x={root.x}
                y={root.y - root.r + 22}
                textAnchor="middle"
                className="fill-ink text-[15px] font-semibold"
                style={{ fontFamily: "var(--font-display)" }}
              >
                {root.data.name}
              </text>

              {chapterNodes.map((chapter, index) => {
                const active = !normalizedQuery || matchedBranchIds.has(chapter.data.id);
                const color = CHAPTER_COLORS[index % CHAPTER_COLORS.length];
                return (
                  <g key={chapter.data.id} opacity={active ? 1 : 0.16} pointerEvents="none">
                    <circle cx={chapter.x} cy={chapter.y} r={chapter.r} fill={color} fillOpacity="0.1" stroke={color} strokeOpacity="0.45" strokeWidth="1.5" />
                    {chapter.r > 32 && (
                      <text
                        x={chapter.x}
                        y={chapter.y - chapter.r + 18}
                        textAnchor="middle"
                        className="fill-ink-soft text-[12px] font-medium"
                        style={{ fontFamily: "var(--font-sans)" }}
                      >
                        {shorten(chapter.data.name, 14)}
                      </text>
                    )}
                  </g>
                );
              })}

              {showRelations && visibleEdges.slice(0, MAX_RELATION_LINES).map((edge) => {
                const from = layout.nodeById.get(edge.from);
                const to = layout.nodeById.get(edge.to);
                if (!from || !to) return null;
                return (
                  <line
                    key={`relation-${edge.id}`}
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    stroke={EDGE_COLORS[edge.type] ?? "#94a3b8"}
                    strokeOpacity="0.3"
                    strokeWidth="1.2"
                    strokeDasharray="4 4"
                    pointerEvents="none"
                  />
                );
              })}

              {leafNodes.map((node) => {
                const knowledgeNode = node.data.node;
                if (!knowledgeNode) return null;
                const isActive = activeId === knowledgeNode.id;
                const isHovered = hoveredId === knowledgeNode.id;
                const isMatch = !normalizedQuery || matchedIds.has(knowledgeNode.id);
                const radius = Math.max(3, node.r);
                const color = NODE_COLORS[knowledgeNode.state ?? nodeState(knowledgeNode)] ?? NODE_COLORS.no_data;
                const label = leafLabel(knowledgeNode.name, radius);
                const showLabel = radius > 11 && (visibleNodes.length <= 90 || isMatch || isActive || isHovered);
                return (
                  <g
                    key={knowledgeNode.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`${knowledgeNode.name}，${knowledgeNode.code}`}
                    aria-pressed={isActive}
                    onClick={(event) => {
                      event.stopPropagation();
                      selectNode(knowledgeNode);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        selectNode(knowledgeNode);
                      }
                    }}
                    onMouseEnter={() => setHoveredId(knowledgeNode.id)}
                    onMouseLeave={() => setHoveredId(null)}
                    className="cursor-pointer outline-none"
                    opacity={isMatch || isActive || isHovered ? 1 : 0.18}
                  >
                    {(isActive || isHovered) && <circle cx={node.x} cy={node.y} r={radius + 5} fill={color} fillOpacity="0.2" />}
                    <circle cx={node.x} cy={node.y} r={radius} fill={color} stroke="currentColor" strokeWidth={isActive ? 2.5 : 1} className="text-canvas" />
                    {showLabel && (
                      <text
                        x={node.x}
                        y={node.y}
                        textAnchor="middle"
                        dominantBaseline="middle"
                        paintOrder="stroke"
                        stroke="var(--color-canvas)"
                        strokeOpacity="0.9"
                        strokeWidth="2.5"
                        className="pointer-events-none fill-ink text-[10px]"
                        style={{ fontFamily: "var(--font-sans)", fontSize: `${label.fontSize}px`, fontWeight: isActive ? 600 : 500 }}
                      >
                        {label.text}
                      </text>
                    )}
                    <title>{`${node.ancestors().reverse().map((ancestor) => ancestor.data.name).join(" / ")}\n重要度：${knowledgeNode.importance || "核心"}`}</title>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-4 py-2.5 text-[11px] text-ink-faint">
        <LegendDot color={NODE_COLORS.good} label="掌握良好" />
        <LegendDot color={NODE_COLORS.watch} label="待巩固" />
        <LegendDot color={NODE_COLORS.weak} label="待加强" />
        <LegendDot color={NODE_COLORS.no_data} label="暂无数据" />
        <span>圆圈大小 = 重要度</span>
        <span className="inline-flex items-center gap-2">
          <i className="h-px w-5 border-t border-dashed border-ink-faint" />
          关系线（可选）
        </span>
      </div>
    </div>
  );
}

function FallbackList({
  nodes,
  selectedId,
  onSelect,
}: {
  nodes: KnowledgeGraphNode[];
  selectedId: number | null;
  onSelect: (node: KnowledgeGraphNode | null) => void;
}) {
  return (
    <div className="max-h-[62vh] min-h-[360px] overflow-y-auto p-3">
      <div className="mb-2 text-xs text-ink-faint">移动端以列表查看知识点，点击可打开详情。</div>
      <div className="space-y-1">
        {nodes.map((node) => {
          const color = NODE_COLORS[node.state ?? nodeState(node)] ?? NODE_COLORS.no_data;
          return (
            <button
              key={node.id}
              type="button"
              onClick={() => onSelect(node)}
              className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition-colors hover:bg-surface-2 ${selectedId === node.id ? "bg-surface-2" : ""}`}
            >
              <i className="h-2 w-2 shrink-0 rounded-full" style={{ background: color }} />
              <span className="min-w-0 flex-1 truncate text-sm text-ink">{node.name}</span>
              <span className="font-mono text-[11px] text-ink-faint">{node.code}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

function nodeState(node: KnowledgeGraphNode): NodeState {
  if (node.mastery == null) return "no_data";
  if (node.mastery < 0.6) return "weak";
  if (node.mastery < 0.8) return "watch";
  return "good";
}

function buildPackLayout(nodes: KnowledgeGraphNode[]): PackLayout {
  const chapterMap = new Map<string, KnowledgeGraphNode[]>();
  nodes.forEach((node) => {
    const chapter = node.chapter || "未分组";
    const items = chapterMap.get(chapter) ?? [];
    items.push(node);
    chapterMap.set(chapter, items);
  });

  const children: PackDatum[] = [...chapterMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([chapter, chapterNodes], chapterIndex) => ({
      id: -1000 - chapterIndex,
      name: chapter,
      kind: "chapter" as const,
      children: [...chapterNodes]
        .sort((a, b) => a.code.localeCompare(b.code))
        .map((node) => ({
          id: node.id,
          name: node.name,
          kind: "node" as const,
          value: importanceValue(node.importance),
          node,
        })),
    }));

  const data: PackDatum = {
    id: -1,
    name: gradeLabel(nodes),
    kind: "root",
    children,
  };
  const root = pack<PackDatum>()
    .size([PACK_SIZE, PACK_SIZE])
    .padding(7)(
      hierarchy(data)
        .sum((datum) => (datum.kind === "node" ? datum.value ?? 1 : 0))
        .sort((a, b) => (b.value ?? 0) - (a.value ?? 0) || a.data.name.localeCompare(b.data.name)),
    );
  const nodeById = new Map<number, PackNode>();
  root.descendants().forEach((node) => {
    if (node.data.kind === "node") nodeById.set(node.data.id, node);
  });
  return { root, nodeById, width: PACK_SIZE, height: PACK_SIZE };
}

function importanceValue(importance: string): number {
  return { 基础: 1.45, 核心: 1.25, 拓展: 1 }.hasOwnProperty(importance)
    ? ({ 基础: 1.45, 核心: 1.25, 拓展: 1 } as Record<string, number>)[importance]
    : 1.15;
}

function gradeLabel(nodes: KnowledgeGraphNode[]): string {
  const grades = [...new Set(nodes.map((node) => node.grade).filter((grade) => Number.isFinite(grade)))];
  if (grades.length > 0) {
    const grade = Math.max(...grades);
    const chinese = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"][grade];
    return `${chinese ?? grade}年级`;
  }
  return "知识体系";
}

function shorten(value: string, maxLength = 12): string {
  return value.length > maxLength ? `${value.slice(0, Math.max(1, maxLength - 1))}…` : value;
}

function leafLabel(value: string, radius: number): { text: string; fontSize: number } {
  const fontSize = Math.min(14, Math.max(10, radius * 0.38));
  const maxChars = Math.max(2, Math.floor((radius * 1.7) / fontSize));
  return { text: shorten(value, maxChars), fontSize };
}
