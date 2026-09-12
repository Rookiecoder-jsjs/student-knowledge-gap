import type { MpChapter, MpKind, MpTree } from "./mindmap-model";

/**
 * 三列树布局（kb-mindmap-create §1）：章列 x=0 → 节列 → 知识点列；后序算子树高，
 * 前序定 y（父卡相对子树块垂直居中）。纯函数，树/选中态变化整体重排。
 * 卡高与 MindNodeCards 的实际渲染高对齐（选中态含动作行）。
 */

const X: Record<MpKind, number> = { chapter: 0, section: 280, kp: 540 };
const kpH = (selected: boolean) => (selected ? 180 : 56);
const chapterH = (selected: boolean) => (selected ? 96 : 64);
const sectionH = (selected: boolean) => (selected ? 80 : 48);
const GAP_Y = 14;

interface Sub {
  id: string;
  kind: MpKind;
  h: number;        // 自身卡高
  subtreeH: number; // 含子树
  children: Sub[];
}

function chapterSub(c: MpChapter, selectedId: string | null): Sub {
  const kpSub = (id: string): Sub => {
    const h = kpH(selectedId === id);
    return { id, kind: "kp", h, subtreeH: h, children: [] };
  };
  const children: Sub[] = [
    ...c.sections.map((s) => ({
      id: s.id,
      kind: "section" as const,
      h: sectionH(selectedId === s.id),
      subtreeH: 0,
      children: s.kps.map((k) => kpSub(k.id)),
    })),
    ...c.kps.map((k) => kpSub(k.id)),
  ];
  for (const s of children) {
    s.subtreeH = Math.max(
      s.h,
      s.children.reduce((n, k) => n + k.subtreeH, 0) + GAP_Y * Math.max(0, s.children.length - 1)
    );
  }
  const kidsH = children.reduce((n, k) => n + k.subtreeH, 0) + GAP_Y * Math.max(0, children.length - 1);
  return { id: c.id, kind: "chapter", h: chapterH(selectedId === c.id), subtreeH: Math.max(chapterH(selectedId === c.id), kidsH), children };
}

export function layoutTree(tree: MpTree, selectedId: string | null): Map<string, { x: number; y: number }> {
  const pos = new Map<string, { x: number; y: number }>();
  const subs = tree.chapters.map((c) => chapterSub(c, selectedId));
  const place = (sub: Sub, x: number, top: number) => {
    pos.set(sub.id, { x, y: top + Math.max(0, (sub.subtreeH - sub.h) / 2) });
    let cy = top;
    for (const child of sub.children) {
      place(child, X[child.kind], cy);
      cy += child.subtreeH + GAP_Y;
    }
  };
  let cursor = 0;
  for (const root of subs) {
    place(root, X.chapter, cursor);
    cursor += root.subtreeH + GAP_Y * 2;
  }
  return pos;
}
