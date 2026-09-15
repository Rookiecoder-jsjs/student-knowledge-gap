import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type Simulation, type SimulationLinkDatum } from "d3-force";
import { hierarchy, tree } from "d3-hierarchy";
import { select } from "d3-selection";
import { linkHorizontal } from "d3-shape";
import { zoom, zoomIdentity, zoomTransform, type ZoomBehavior } from "d3-zoom";
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

type NodeState = keyof typeof NODE_COLORS;

export interface KnowledgeGraph2DProps {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  selectedId?: number | null;
  onSelect?: (node: KnowledgeGraphNode | null) => void;
  className?: string;
}

interface Point {
  x: number;
  y: number;
}

interface Layout {
  positions: Map<number, Point>;
  chapters: string[];
  width: number;
  height: number;
  laneWidth: number;
  primaryEdgeIds: Set<number>;
  structureNodes: StructureNode[];
  structureLinks: StructureLink[];
}

interface TreeDatum {
  kind: "chapter" | "node";
  nodeId?: number;
  children?: TreeDatum[];
}

interface ForceNode extends KnowledgeGraphNode {
  kind: "node";
  x: number;
  y: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
  chapterIndex: number;
}

interface StructureNode {
  id: number;
  name: string;
  kind: "root" | "chapter";
  chapterIndex: number;
}

interface SimStructureNode extends StructureNode {
  x: number;
  y: number;
  fx?: number | null;
  fy?: number | null;
}

interface StructureLink {
  id: number;
  from: number;
  to: number;
}

type SimNode = ForceNode | SimStructureNode;

interface ForceLink extends SimulationLinkDatum<SimNode> {
  source: number | SimNode;
  target: number | SimNode;
  synthetic?: boolean;
}

const treeLink = linkHorizontal<{ source: Point; target: Point }, Point>()
  .x((point) => point.x)
  .y((point) => point.y);

/**
 * 只读知识结构 2D 探索图。使用 SVG 保持轻量、可缩放和可访问，编辑场景仍由
 * React Flow 承担。节点本身可点击、可聚焦，移动端则切换为同一数据的列表视图。
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
  const [showSecondaryRelations, setShowSecondaryRelations] = useState(false);
  const [query, setQuery] = useState("");
  const [zoomPercent, setZoomPercent] = useState(100);
  const [draggingId, setDraggingId] = useState<number | null>(null);
  const [forcePositions, setForcePositions] = useState<Map<number, Point>>(new Map());
  const svgRef = useRef<SVGSVGElement | null>(null);
  const zoomLayerRef = useRef<SVGGElement | null>(null);
  const zoomBehaviorRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const forceNodesRef = useRef<SimNode[]>([]);
  const forceSimulationRef = useRef<Simulation<SimNode, ForceLink> | null>(null);
  const activeId = selectedId ?? localSelectedId;

  // 图谱展示完整知识库；节点数量由后端筛选结果决定，不在前端静默截断。
  const visibleNodes = nodes;
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => edges.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to)),
    [edges, visibleIds],
  );
  const layout = useMemo(() => layoutNodes(visibleNodes, visibleEdges), [visibleEdges, visibleNodes]);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matchedIds = useMemo(() => {
    if (!normalizedQuery) return new Set<number>();
    return new Set(
      visibleNodes
        .filter((node) => `${node.name} ${node.code} ${node.chapter ?? ""}`.toLocaleLowerCase().includes(normalizedQuery))
        .map((node) => node.id),
    );
  }, [normalizedQuery, visibleNodes]);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const svgElement = svgRef.current;
    const layer = zoomLayerRef.current;
    if (!svgElement || !layer) return;
    const svgSelection = select(svgElement);
    const behavior = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.65, 2.8])
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

  useEffect(() => {
    if (mobile || visibleNodes.length === 0) {
      forceSimulationRef.current?.stop();
      forceSimulationRef.current = null;
      forceNodesRef.current = [];
      return;
    }

    const nodeForceNodes: ForceNode[] = visibleNodes.map((node) => {
      const initial = layout.positions.get(node.id) ?? {
        x: layout.width / 2,
        y: layout.height / 2,
      };
      return {
        ...node,
        kind: "node",
        x: initial.x,
        y: initial.y,
        chapterIndex: Math.max(0, layout.chapters.indexOf(node.chapter || "未分组")),
      } satisfies ForceNode;
    });
    const structureForceNodes: SimStructureNode[] = layout.structureNodes.map((node) => {
      const initial = layout.positions.get(node.id) ?? { x: layout.width / 2, y: 40 };
      return { ...node, x: initial.x, y: initial.y, fx: initial.x, fy: initial.y };
    });
    const forceNodes: SimNode[] = [...nodeForceNodes, ...structureForceNodes];
    const forceLinks: ForceLink[] = [
      ...layout.structureLinks.map((link) => ({ source: link.from, target: link.to, synthetic: true })),
      ...visibleEdges
        .filter((edge) => layout.primaryEdgeIds.has(edge.id))
        .map((edge) => ({ source: edge.from, target: edge.to })),
    ];
    const simulation = forceSimulation<SimNode, ForceLink>(forceNodes)
      .force(
        "link",
        forceLink<SimNode, ForceLink>(forceLinks)
          .id((node) => node.id)
          .distance((link) => (link.synthetic ? 58 : 52))
          .strength(0.85),
      )
      .force("charge", forceManyBody<SimNode>().strength((node) => (node.kind === "root" ? -140 : node.kind === "chapter" ? -90 : -72)).distanceMax(360))
      .force("collide", forceCollide<SimNode>().radius((node) => (node.kind === "root" ? 34 : node.kind === "chapter" ? 28 : 11)).strength(0.9))
      .force("center", forceCenter(layout.width / 2, layout.height / 2).strength(0.04))
      .force(
        "chapter",
        forceX<SimNode>((node) => {
          if (node.kind === "root") return layout.width / 2;
          return chapterX(node.chapterIndex, layout.chapters.length, layout.width, layout.laneWidth);
        }).strength((node) => (node.kind === "root" ? 0.5 : node.kind === "chapter" ? 0.32 : 0.14)),
      )
      .force(
        "row",
        forceY<SimNode>((node) => (node.kind === "root" ? 32 : node.kind === "chapter" ? 82 : layout.height * 0.58)).strength(
          (node) => (node.kind === "root" ? 0.7 : node.kind === "chapter" ? 0.4 : 0.035),
        ),
      )
      .alpha(0.9)
      .alphaDecay(0.035)
      .velocityDecay(0.35);

    forceNodesRef.current = forceNodes;
    forceSimulationRef.current = simulation;
    setForcePositions(new Map(forceNodes.map((node) => [node.id, { x: node.x, y: node.y }])));

    let frame = 0;
    simulation.on("tick", () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const next = new Map<number, Point>();
        forceNodes.forEach((node) => {
          node.x = clamp(node.x, 18, layout.width - 18);
          node.y = clamp(node.y, node.kind === "root" ? 24 : node.kind === "chapter" ? 58 : 112, layout.height - 24);
          next.set(node.id, { x: node.x, y: node.y });
        });
        setForcePositions(next);
      });
    });

    return () => {
      simulation.stop();
      if (frame) cancelAnimationFrame(frame);
      if (forceSimulationRef.current === simulation) forceSimulationRef.current = null;
    };
  }, [layout, mobile, visibleEdges, visibleNodes]);

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
  const startNodeDrag = (nodeId: number, event: ReactPointerEvent<SVGGElement>) => {
    const node = forceNodesRef.current.find((candidate) => candidate.id === nodeId);
    const simulation = forceSimulationRef.current;
    const svgElement = svgRef.current;
    if (!node || !simulation || !svgElement) return;
    event.preventDefault();
    event.stopPropagation();
    const point = pointerToGraph(event, svgElement);
    node.fx = point.x;
    node.fy = point.y;
    simulation.alphaTarget(0.25).restart();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDraggingId(nodeId);
  };
  const moveNodeDrag = (nodeId: number, event: ReactPointerEvent<SVGGElement>) => {
    if (draggingId !== nodeId) return;
    const node = forceNodesRef.current.find((candidate) => candidate.id === nodeId);
    const svgElement = svgRef.current;
    if (!node || !svgElement) return;
    const point = pointerToGraph(event, svgElement);
    node.fx = point.x;
    node.fy = point.y;
  };
  const endNodeDrag = (nodeId: number, event: ReactPointerEvent<SVGGElement>) => {
    if (draggingId !== nodeId) return;
    const node = forceNodesRef.current.find((candidate) => candidate.id === nodeId);
    if (node) {
      node.fx = null;
      node.fy = null;
    }
    forceSimulationRef.current?.alphaTarget(0);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setDraggingId(null);
  };
  const fallback = mobile;
  const pointFor = (nodeId: number) => forcePositions.get(nodeId) ?? layout.positions.get(nodeId);
  return (
    <div className={`overflow-hidden rounded-xl border border-line bg-canvas ${className}`}>
      {fallback ? (
        <FallbackList nodes={nodes} selectedId={activeId} onSelect={selectNode} />
      ) : (
        <div className="flex h-[min(62vh,560px)] min-h-[360px] w-full flex-col overflow-hidden p-2 sm:p-3">
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
            aria-label="知识结构二维关系图"
            style={{ touchAction: "none" }}
          >
            <rect x="0" y="0" width={layout.width} height={layout.height} rx="18" fill="currentColor" className="text-surface-2/30" />
            <g ref={zoomLayerRef} className="cursor-grab active:cursor-grabbing">
              {layout.chapters.map((chapter, index) => {
                const x = chapterX(index, layout.chapters.length, layout.width, layout.laneWidth);
                return (
                  <g key={chapter}>
                    <rect
                      x={x - layout.laneWidth / 2 + 8}
                      y="98"
                      width={layout.laneWidth - 16}
                      height={layout.height - 122}
                      rx="14"
                      fill="currentColor"
                      className="text-surface-2/20"
                    />
                  </g>
                );
              })}
              {layout.structureLinks.map((link) => {
                const from = pointFor(link.from);
                const to = pointFor(link.to);
                if (!from || !to) return null;
                return (
                  <line
                    key={`structure-${link.id}`}
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    stroke="#7dd3fc"
                    strokeOpacity="0.65"
                    strokeWidth="1.6"
                  />
                );
              })}
              {layout.structureNodes.map((node) => {
                const point = pointFor(node.id);
                if (!point) return null;
                if (node.kind === "root") {
                  return (
                    <g key={node.id} aria-label={`${node.name}知识体系`}>
                      <rect x={point.x - 54} y={point.y - 14} width="108" height="28" rx="14" fill="#0f766e" fillOpacity="0.9" />
                      <text x={point.x} y={point.y + 4} textAnchor="middle" className="fill-white text-[12px] font-semibold">
                        {node.name}
                      </text>
                    </g>
                  );
                }
                return (
                  <g key={node.id} aria-label={`${node.name}章节`}>
                    <rect x={point.x - layout.laneWidth / 2 + 16} y={point.y - 12} width={layout.laneWidth - 32} height="24" rx="12" fill="currentColor" className="text-surface-3" />
                    <text x={point.x} y={point.y + 4} textAnchor="middle" className="fill-ink-soft text-[10px] font-medium">
                      {node.name}
                    </text>
                  </g>
                );
              })}
              {visibleEdges.filter((edge) => layout.primaryEdgeIds.has(edge.id)).map((edge) => {
                const from = pointFor(edge.from);
                const to = pointFor(edge.to);
                if (!from || !to) return null;
                return (
                  <path
                    key={edge.id}
                    d={treeLink({ source: from, target: to }) ?? undefined}
                    fill="none"
                    stroke={EDGE_COLORS[edge.type] ?? "#5eead4"}
                    strokeOpacity="0.72"
                    strokeWidth="1.8"
                  />
                );
              })}
              {showSecondaryRelations && visibleEdges.filter((edge) => !layout.primaryEdgeIds.has(edge.id)).map((edge) => {
                const from = pointFor(edge.from);
                const to = pointFor(edge.to);
                if (!from || !to) return null;
                return (
                  <line
                    key={`secondary-${edge.id}`}
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    stroke={EDGE_COLORS[edge.type] ?? "#94a3b8"}
                    strokeOpacity="0.28"
                    strokeWidth="1.1"
                    strokeDasharray="4 4"
                  />
                );
              })}
              {visibleNodes.map((node) => {
                const point = pointFor(node.id);
                if (!point) return null;
                const isActive = activeId === node.id;
                const isHovered = hoveredId === node.id;
                const isMatch = !normalizedQuery || matchedIds.has(node.id);
                const radius = isActive || isHovered ? 10 : 7;
                const color = NODE_COLORS[node.state ?? nodeState(node)] ?? NODE_COLORS.no_data;
                return (
                  <g
                    key={node.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`${node.name}，${node.code}`}
                    aria-pressed={isActive}
                    aria-grabbed={draggingId === node.id}
                    onClick={() => selectNode(node)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        selectNode(node);
                      }
                    }}
                    onMouseEnter={() => setHoveredId(node.id)}
                    onMouseLeave={() => setHoveredId(null)}
                    onPointerDown={(event) => startNodeDrag(node.id, event)}
                    onPointerMove={(event) => moveNodeDrag(node.id, event)}
                    onPointerUp={(event) => endNodeDrag(node.id, event)}
                    onPointerCancel={(event) => endNodeDrag(node.id, event)}
                    className="cursor-pointer outline-none"
                    opacity={isMatch || isActive || isHovered ? 1 : 0.18}
                  >
                    <circle cx={point.x} cy={point.y} r={radius + 5} fill={color} fillOpacity={isActive ? 0.18 : 0} />
                    <circle cx={point.x} cy={point.y} r={radius} fill={color} stroke="currentColor" strokeWidth={isActive ? 2 : 1} className="text-canvas" />
                    {(visibleNodes.length <= 70 || isActive || isHovered || matchedIds.has(node.id)) && (
                      <text x={point.x + 14} y={point.y + 4} className="pointer-events-none fill-ink-soft text-[10px]">
                        {shorten(node.name)}
                      </text>
                    )}
                    <title>{`${node.name} · ${node.code}`}</title>
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
        <span className="inline-flex items-center gap-2">
          <i className="h-px w-5 bg-accent" />
          实线 = 前置树
        </span>
        <button
          type="button"
          aria-pressed={showSecondaryRelations}
          onClick={() => setShowSecondaryRelations((current) => !current)}
          className="rounded-md border border-line px-2 py-1 text-[11px] text-ink-soft transition-colors hover:bg-surface-2"
        >
          {showSecondaryRelations ? "隐藏辅助关系" : "显示辅助关系"}
        </button>
        <span className="ml-auto">点击节点查看详情 · 拖动节点调整布局 · 滚轮缩放、拖动平移 · Tab/Enter 可操作</span>
      </div>
      <div className="sr-only" aria-live="polite">
        {hoveredId != null ? `已聚焦 ${nodes.find((node) => node.id === hoveredId)?.name ?? "知识点"}` : ""}
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
  onSelect: (node: KnowledgeGraphNode) => void;
}) {
  return (
    <div className="max-h-[min(62vh,560px)] overflow-y-auto p-3">
      <p className="mb-2 text-xs text-ink-faint">窄屏下已切换为可操作列表，点击知识点查看详情。</p>
      <div className="grid gap-1.5 sm:grid-cols-2">
        {nodes.map((node) => (
          <button
            key={node.id}
            type="button"
            aria-pressed={selectedId === node.id}
            onClick={() => onSelect(node)}
            className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors hover:bg-surface-2 ${selectedId === node.id ? "border-accent/50 bg-accent-soft/40" : "border-line"}`}
          >
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: NODE_COLORS[node.state ?? nodeState(node)] ?? NODE_COLORS.no_data }} />
            <span className="min-w-0 flex-1 truncate">{node.name}</span>
            <span className="font-mono text-[10px] text-ink-faint">{node.code}</span>
          </button>
        ))}
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

function chapterX(index: number, total: number, width: number, laneWidth: number): number {
  const contentWidth = total * laneWidth;
  const left = Math.max(28, (width - contentWidth) / 2);
  return left + index * laneWidth + laneWidth / 2;
}

function layoutNodes(nodes: KnowledgeGraphNode[], edges: KnowledgeGraphEdge[]): Layout {
  const positions = new Map<number, Point>();
  const chapters = [...new Set(nodes.map((node) => node.chapter || "未分组"))];
  const visibleIds = new Set(nodes.map((node) => node.id));
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const parentByChild = new Map<number, number>();
  const primaryEdgeIds = new Set<number>();

  // 只把同章节的 contains / prerequisite 关系纳入主树，跨章节和多父节点关系
  // 保留为辅助关系。这样主视图始终是一组可扫描的树，而不是关系线的毛球。
  const candidates = edges
    .filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to))
    .filter((edge) => edge.type === "contains" || edge.type === "prerequisite")
    .filter((edge) => byId.get(edge.from)?.chapter === byId.get(edge.to)?.chapter)
    .sort((a, b) => {
      const typeRank = (type: string) => (type === "contains" ? 2 : 1);
      return typeRank(b.type) - typeRank(a.type) || b.weight - a.weight || a.id - b.id;
    });
  for (const edge of candidates) {
    if (parentByChild.has(edge.to) || edge.from === edge.to) continue;
    if (wouldCreateCycle(edge.to, edge.from, parentByChild)) continue;
    parentByChild.set(edge.to, edge.from);
    primaryEdgeIds.add(edge.id);
  }

  const childrenByParent = new Map<number, number[]>();
  for (const [child, parent] of parentByChild) {
    const children = childrenByParent.get(parent) ?? [];
    children.push(child);
    childrenByParent.set(parent, children);
  }
  for (const children of childrenByParent.values()) {
    children.sort((a, b) => (byId.get(a)?.code ?? "").localeCompare(byId.get(b)?.code ?? ""));
  }

  const rowGap = 34;
  const top = 122;
  let maxLeaves = 1;
  const depthByNode = new Map<number, number>();
  const depthOf = (nodeId: number, trail = new Set<number>()): number => {
    const cached = depthByNode.get(nodeId);
    if (cached != null) return cached;
    if (trail.has(nodeId)) return 0;
    const parent = parentByChild.get(nodeId);
    if (parent == null) {
      depthByNode.set(nodeId, 0);
      return 0;
    }
    const nextTrail = new Set(trail);
    nextTrail.add(nodeId);
    const depth = depthOf(parent, nextTrail) + 1;
    depthByNode.set(nodeId, depth);
    return depth;
  };
  nodes.forEach((node) => depthOf(node.id));
  const maxDepth = Math.max(0, ...depthByNode.values());
  const laneWidth = Math.max(148, maxDepth * 24 + 48);
  const width = Math.max(1000, chapters.length * laneWidth + 56);
  for (let chapterIndex = 0; chapterIndex < chapters.length; chapterIndex += 1) {
    const chapter = chapters[chapterIndex];
    const chapterNodes = nodes.filter((node) => (node.chapter || "未分组") === chapter);
    const roots = chapterNodes
      .filter((node) => !parentByChild.has(node.id))
      .sort((a, b) => a.code.localeCompare(b.code));
    const rootData: TreeDatum = {
      kind: "chapter",
      children: roots.map((root) => buildTreeDatum(root.id, childrenByParent)),
    };
    const treeRoot = tree<TreeDatum>()
      .nodeSize([rowGap, 1])
      .separation((a, b) => (a.parent === b.parent ? 1.2 : 1.8))(hierarchy(rootData));
    const graphNodes = treeRoot.descendants().filter((item) => item.data.nodeId != null);
    const minX = Math.min(0, ...graphNodes.map((item) => item.x));
    const maxX = Math.max(0, ...graphNodes.map((item) => item.x));
    const chapterStart = chapterX(chapterIndex, chapters.length, width, laneWidth) - laneWidth / 2;
    graphNodes.forEach((item) => {
      const nodeId = item.data.nodeId!;
      positions.set(nodeId, {
        x: chapterStart + 20 + Math.max(0, item.depth - 1) * 24,
        y: top + item.x - minX,
      });
    });
    maxLeaves = Math.max(maxLeaves, Math.ceil((maxX - minX) / rowGap) + 1);
  }
  // Defensive fallback: a malformed/cyclic input should still render every node.
  for (const node of nodes) {
    if (!positions.has(node.id)) {
      const index = nodes.indexOf(node);
      positions.set(node.id, {
        x: chapterX(Math.max(0, chapters.indexOf(node.chapter || "未分组")), chapters.length, width, laneWidth),
        y: top + (index % maxLeaves) * rowGap,
      });
    }
  }
  const rootName = gradeLabel(nodes);
  const rootId = -1;
  const structureNodes: StructureNode[] = [
    { id: rootId, name: rootName, kind: "root", chapterIndex: 0 },
    ...chapters.map((chapter, chapterIndex) => ({
      id: -1000 - chapterIndex,
      name: chapter,
      kind: "chapter" as const,
      chapterIndex,
    })),
  ];
  const structureLinks: StructureLink[] = chapters.flatMap((chapter, chapterIndex) => {
    const chapterId = -1000 - chapterIndex;
    const chapterNodes = nodes.filter((node) => (node.chapter || "未分组") === chapter);
    const roots = chapterNodes.filter((node) => !parentByChild.has(node.id)).sort((a, b) => a.code.localeCompare(b.code));
    return [
      { id: chapterId, from: rootId, to: chapterId },
      ...roots.map((node, rootIndex) => ({ id: chapterId * 1000 - rootIndex, from: chapterId, to: node.id })),
    ];
  });
  positions.set(rootId, { x: width / 2, y: 32 });
  structureNodes.slice(1).forEach((node) => {
    positions.set(node.id, {
      x: chapterX(node.chapterIndex, chapters.length, width, laneWidth),
      y: 82,
    });
  });
  const height = Math.max(560, top + maxLeaves * rowGap + 52);
  return { positions, chapters, width, height, laneWidth, primaryEdgeIds, structureNodes, structureLinks };
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

function wouldCreateCycle(child: number, parent: number, parentByChild: Map<number, number>): boolean {
  const seen = new Set<number>();
  let current: number | undefined = parent;
  while (current != null) {
    if (current === child || seen.has(current)) return true;
    seen.add(current);
    current = parentByChild.get(current);
  }
  return false;
}

function buildTreeDatum(nodeId: number, childrenByParent: Map<number, number[]>): TreeDatum {
  return {
    kind: "node",
    nodeId,
    children: (childrenByParent.get(nodeId) ?? []).map((childId) => buildTreeDatum(childId, childrenByParent)),
  };
}

function shorten(value: string): string {
  return value.length > 12 ? `${value.slice(0, 11)}…` : value;
}

function pointerToGraph(event: ReactPointerEvent<SVGGElement>, svgElement: SVGSVGElement): Point {
  const matrix = svgElement.getScreenCTM();
  if (!matrix) return { x: 0, y: 0 };
  const screenPoint = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
  const [x, y] = zoomTransform(svgElement).invert([screenPoint.x, screenPoint.y]);
  return { x, y };
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}
