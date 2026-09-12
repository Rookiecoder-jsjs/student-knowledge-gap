/** 知识点关系类型——与后端 app/kb/loader.py RELATION_TYPES 对齐；导图画布与 KpDetailEditor 共用（kb-mindmap-create §4）。 */

export const RELATION_TYPES = ["prerequisite", "contains", "confusable", "spiral"] as const;
export type RelationType = (typeof RELATION_TYPES)[number];

export const REL_LABEL: Record<RelationType, string> = {
  prerequisite: "前置",
  contains: "包含",
  confusable: "易混",
  spiral: "螺旋上升",
};

/** 关系边配色类（信号色随令牌双主题，色值定义在 index.css 的 reactflow 覆盖段）。 */
export const REL_EDGE_CLASS: Record<RelationType, string> = {
  prerequisite: "kb-edge-prerequisite",
  contains: "kb-edge-contains",
  confusable: "kb-edge-confusable",
  spiral: "kb-edge-spiral",
};

/** 关系标签 chip 的令牌类（语义色文字 + 中性底）。 */
export const REL_CHIP_CLASS: Record<RelationType, string> = {
  prerequisite: "border-accent/30 text-accent-deep",
  contains: "border-info/30 text-info",
  confusable: "border-warn/30 text-warn",
  spiral: "border-success/30 text-success",
};

export function isRelationType(v: string): v is RelationType {
  return (RELATION_TYPES as readonly string[]).includes(v);
}

/** 宽容标签：未知类型原样返回（历史数据兜底）。 */
export function relLabel(t: string): string {
  return isRelationType(t) ? REL_LABEL[t] : t;
}
