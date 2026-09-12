import type { KpDraft, KpRelationDraft } from "./kb-create";
import { isRelationType } from "./relation-types";
import type { RelationType } from "./relation-types";

/**
 * 导图树状态模型（kb-mindmap-create §3/§4）：全部操作不可变（返回新树）。
 * 层级 kind 与深度耦合：chapter(0) → section(1) → kp(2)；kp 亦可直属章下。
 * 树结构落 chapter 字符串（「章 > 节」），不自动生成 contains 边。
 */

export interface MpKp {
  id: string;
  code: string;
  name: string;
  description: string;
  importance: string; // 基础/核心/拓展
}

export interface MpSection {
  id: string;
  name: string;
  kps: MpKp[];
}

export interface MpChapter {
  id: string;
  name: string;
  sections: MpSection[];
  /** 直属章下（未挂节）的知识点 */
  kps: MpKp[];
}

export interface MpEdge {
  id: string;
  from: string; // kp id
  to: string;   // kp id
  type: RelationType;
}

export interface MpTree {
  chapters: MpChapter[];
}

export type MpKind = "chapter" | "section" | "kp";

/** 画布内唯一 id（节点/边共用）。 */
export const nid = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `mp-${Date.now()}-${Math.random().toString(36).slice(2)}`;

/** 空白章（emptyTree 首章 / addSibling 新章 / 删光后补种共用一个形态）。 */
export function emptyChapter(id: string): MpChapter {
  return { id, name: "", sections: [], kps: [] };
}

export function emptyTree(): MpTree {
  // 首章名留空：进页光标即落在此处，教师直接打字起名（易用性主路径）
  return { chapters: [emptyChapter(nid())] };
}

/* ---------------- 定位 ---------------- */

export type MpHit =
  | { kind: "chapter"; chapter: MpChapter; ci: number }
  | { kind: "section"; chapter: MpChapter; section: MpSection; ci: number; si: number }
  | { kind: "kp"; chapter: MpChapter; section: MpSection | null; kp: MpKp; ci: number; si: number | null; ki: number };

export function locate(tree: MpTree, id: string): MpHit | null {
  for (let ci = 0; ci < tree.chapters.length; ci++) {
    const chapter = tree.chapters[ci];
    if (chapter.id === id) return { kind: "chapter", chapter, ci };
    for (let si = 0; si < chapter.sections.length; si++) {
      const section = chapter.sections[si];
      if (section.id === id) return { kind: "section", chapter, section, ci, si };
      for (let ki = 0; ki < section.kps.length; ki++) {
        if (section.kps[ki].id === id)
          return { kind: "kp", chapter, section, kp: section.kps[ki], ci, si, ki };
      }
    }
    for (let ki = 0; ki < chapter.kps.length; ki++) {
      if (chapter.kps[ki].id === id)
        return { kind: "kp", chapter, section: null, kp: chapter.kps[ki], ci, si: null, ki };
    }
  }
  return null;
}

/* ---------------- 节点编辑 ---------------- */

type ChapterFn = (c: MpChapter) => MpChapter;

function mapChapters(tree: MpTree, ciTarget: number | null, fn: ChapterFn): MpTree {
  return {
    chapters: tree.chapters.map((c, ci) => (ciTarget == null || ci === ciTarget ? fn(c) : c)),
  };
}

export function updateNode(tree: MpTree, id: string, patch: Partial<MpKp> & { name?: string }): MpTree {
  const hit = locate(tree, id);
  if (!hit) return tree;
  if (hit.kind === "chapter") {
    return mapChapters(tree, hit.ci, (c) => ({ ...c, name: patch.name ?? c.name }));
  }
  if (hit.kind === "section") {
    return mapChapters(tree, hit.ci, (c) => ({
      ...c,
      sections: c.sections.map((s) => (s.id === id ? { ...s, name: patch.name ?? s.name } : s)),
    }));
  }
  const kpFn = (k: MpKp): MpKp => (k.id === id ? { ...k, ...patch } : k);
  return mapChapters(tree, hit.ci, (c) => ({
    ...c,
    sections: c.sections.map((s) => ({ ...s, kps: s.kps.map(kpFn) })),
    kps: c.kps.map(kpFn),
  }));
}

export function newKpNode(code = "", id?: string): MpKp {
  return { id: id ?? nid(), code, name: "", description: "", importance: "核心" };
}

/** 章内下一个可用编码：{前缀}-{章序}{流水:02d}（对标 M7A-105；全树查重）。 */
/** 全树已用编码（自动编码查重用）。 */
export function allCodes(tree: MpTree): string[] {
  const out: string[] = [];
  const collect = (kps: MpKp[]) => {
    for (const k of kps) if (k.code) out.push(k.code);
  };
  for (const c of tree.chapters) {
    for (const s of c.sections) collect(s.kps);
    collect(c.kps);
  }
  return out;
}

export function nextCode(tree: MpTree, prefix: string, chapterIndex: number): string {
  if (!prefix.trim()) return "";
  const taken = new Set(allCodes(tree));
  const ch = chapterIndex + 1;
  for (let seq = 1; seq < 100; seq++) {
    const code = `${prefix.trim()}-${ch}${String(seq).padStart(2, "0")}`;
    if (!taken.has(code)) return code;
  }
  return "";
}

/* ---------------- 结构操作（均返回 {tree, newId?}，newId 供聚焦） ---------------- */

export interface AddOpts {
  codePrefix?: string;
  /** 预生成 id（函数式更新下由调用方在 updater 外生成，保证纯更新）。 */
  newId?: string;
}

export function addSibling(tree: MpTree, refId: string, opts?: AddOpts): { tree: MpTree; newId: string } | null {
  const hit = locate(tree, refId);
  if (!hit) return null;
  if (hit.kind === "chapter") {
    const node = emptyChapter(opts?.newId ?? nid());
    const chapters = [...tree.chapters];
    chapters.splice(hit.ci + 1, 0, node);
    return { tree: { chapters }, newId: node.id };
  }
  if (hit.kind === "section") {
    const node = { id: opts?.newId ?? nid(), name: "", kps: [] };
    return {
      tree: mapChapters(tree, hit.ci, (c) => {
        const sections = [...c.sections];
        sections.splice(hit.si + 1, 0, node);
        return { ...c, sections };
      }),
      newId: node.id,
    };
  }
  const node = newKpNode(opts?.codePrefix ? nextCode(tree, opts.codePrefix, hit.ci) : "", opts?.newId);
  return {
    tree: mapChapters(tree, hit.ci, (c) => {
      if (hit.section) {
        const kps = [...hit.section.kps];
        kps.splice(hit.ki + 1, 0, node);
        return { ...c, sections: c.sections.map((s) => (s.id === hit.section!.id ? { ...s, kps } : s)) };
      }
      const kps = [...c.kps];
      kps.splice(hit.ki + 1, 0, node);
      return { ...c, kps };
    }),
    newId: node.id,
  };
}

/** Tab：chapter→加节，section→加点；kp 无下级（返回 null 提示）。 */
export function addChild(tree: MpTree, parentId: string, opts?: AddOpts): { tree: MpTree; newId: string } | null {
  const hit = locate(tree, parentId);
  if (!hit) return null;
  if (hit.kind === "chapter") {
    const node = { id: opts?.newId ?? nid(), name: "", kps: [] };
    return {
      tree: mapChapters(tree, hit.ci, (c) => ({ ...c, sections: [...c.sections, node] })),
      newId: node.id,
    };
  }
  if (hit.kind === "section") {
    const node = newKpNode(opts?.codePrefix ? nextCode(tree, opts.codePrefix, hit.ci) : "", opts?.newId);
    return {
      tree: mapChapters(tree, hit.ci, (c) => ({
        ...c,
        sections: c.sections.map((s) => (s.id === hit.section.id ? { ...s, kps: [...s.kps, node] } : s)),
      })),
      newId: node.id,
    };
  }
  return null;
}

/** 章卡「+知识点」：直属章下（无节）。 */
export function addChapterKp(tree: MpTree, chapterId: string, codePrefix?: string, newId?: string): { tree: MpTree; newId: string } | null {
  const hit = locate(tree, chapterId);
  if (!hit || hit.kind !== "chapter") return null;
  const node = newKpNode(codePrefix ? nextCode(tree, codePrefix, hit.ci) : "", newId);
  return {
    tree: mapChapters(tree, hit.ci, (c) => ({ ...c, kps: [...c.kps, node] })),
    newId: node.id,
  };
}

/** Shift+Tab：kp（节下→章下直属）；section（→上一章的节）；其余 no-op。 */
export function promote(tree: MpTree, id: string): { tree: MpTree; newId?: string } {
  const hit = locate(tree, id);
  if (!hit) return { tree };
  if (hit.kind === "kp" && hit.section) {
    const kp = hit.kp;
    return {
      tree: mapChapters(tree, hit.ci, (c) => ({
        ...c,
        sections: c.sections.map((s) => (s.id === hit.section!.id ? { ...s, kps: s.kps.filter((k) => k.id !== id) } : s)),
        kps: [...c.kps, kp],
      })),
      newId: id,
    };
  }
  if (hit.kind === "section" && hit.ci > 0) {
    const section = hit.section;
    return {
      tree: {
        chapters: tree.chapters.map((c, ci) => {
          if (ci === hit.ci) return { ...c, sections: c.sections.filter((s) => s.id !== id) };
          if (ci === hit.ci - 1) return { ...c, sections: [...c.sections, section] };
          return c;
        }),
      },
      newId: id,
    };
  }
  return { tree };
}

/** 收集删除的 kp id（含级联），供边清理。 */
function removedKpIds(nodes: { kps: MpKp[] }[]): string[] {
  return nodes.flatMap((n) => n.kps.map((k) => k.id));
}

export function removeNode(tree: MpTree, id: string, reseedId?: string): { tree: MpTree; removed: string[] } {
  const hit = locate(tree, id);
  if (!hit) return { tree, removed: [] };
  if (hit.kind === "chapter") {
    const chapters = tree.chapters.filter((c) => c.id !== id);
    // 删光最后一个章 → 立即补一个空首章（reseedId 由调用方预生成保持纯更新），
    // 否则画布零节点、无法再从零录入（2026-09-12 用户实测缺口）
    if (reseedId && chapters.length === 0) chapters.push(emptyChapter(reseedId));
    return {
      tree: { chapters },
      removed: [id, ...removedKpIds(hit.chapter.sections), ...removedKpIds([hit.chapter])],
    };
  }
  if (hit.kind === "section") {
    return {
      tree: mapChapters(tree, hit.ci, (c) => ({ ...c, sections: c.sections.filter((s) => s.id !== id) })),
      removed: [id, ...removedKpIds([hit.section])],
    };
  }
  return {
    tree: mapChapters(tree, hit.ci, (c) => ({
      ...c,
      sections: c.sections.map((s) => ({ ...s, kps: s.kps.filter((k) => k.id !== id) })),
      kps: c.kps.filter((k) => k.id !== id),
    })),
    removed: [id],
  };
}

export function moveWithin(tree: MpTree, id: string, dir: -1 | 1): MpTree {
  const hit = locate(tree, id);
  if (!hit) return tree;
  const swap = <T,>(arr: T[], i: number): T[] => {
    const j = i + dir;
    if (j < 0 || j >= arr.length) return arr;
    const next = [...arr];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  };
  if (hit.kind === "chapter") return { chapters: swap(tree.chapters, hit.ci) };
  if (hit.kind === "section")
    return mapChapters(tree, hit.ci, (c) => ({ ...c, sections: swap(c.sections, hit.si) }));
  return mapChapters(tree, hit.ci, (c) =>
    hit.section
      ? {
          ...c,
          sections: c.sections.map((s) => (s.id === hit.section!.id ? { ...s, kps: swap(s.kps, hit.ki) } : s)),
        }
      : { ...c, kps: swap(c.kps, hit.ki) }
  );
}

/* ---------------- 关系边 ---------------- */

export function kpIdSet(tree: MpTree): Set<string> {
  const out = new Set<string>();
  for (const c of tree.chapters) {
    for (const s of c.sections) for (const k of s.kps) out.add(k.id);
    for (const k of c.kps) out.add(k.id);
  }
  return out;
}

/** 建边校验：返回错误文案；合法返回 null。 */
export function edgeError(tree: MpTree, edges: MpEdge[], from: string, to: string, type: RelationType): string | null {
  const ids = kpIdSet(tree);
  if (!ids.has(from) || !ids.has(to)) return "关系端点必须是知识点";
  if (from === to) return "不能连自己";
  if (edges.some((e) => e.from === from && e.to === to && e.type === type)) return "已存在相同关系";
  return null;
}

export function cleanEdges(edges: MpEdge[], removed: string[]): MpEdge[] {
  const gone = new Set(removed);
  return edges.filter((e) => !gone.has(e.from) && !gone.has(e.to));
}

/* ---------------- 统计 / 拍平 / 校验 ---------------- */

export function countKps(chapter: MpChapter): number {
  return chapter.kps.length + chapter.sections.reduce((n, s) => n + s.kps.length, 0);
}

export function chapterString(chapterName: string, sectionName: string | null): string {
  return sectionName ? `${chapterName} > ${sectionName}` : chapterName;
}

export interface FlattenResult {
  kps: KpDraft[];
  relations: KpRelationDraft[];
  problems: string[];
}

/** 拍平为创建管道产物；relations 端点转编码。 */
export function flattenTree(tree: MpTree, edges: MpEdge[]): FlattenResult {
  const kps: KpDraft[] = [];
  const problems: string[] = [];
  const idToCode = new Map<string, string>();
  const seenCodes = new Map<string, string>();

  const pushKp = (kp: MpKp, chapter: string, where: string) => {
    if (!kp.name.trim()) problems.push(`${where}：知识点缺名称`);
    if (!kp.code.trim()) problems.push(`${where}「${kp.name || "未命名"}」缺编码`);
    else if (seenCodes.has(kp.code)) {
      const first = seenCodes.get(kp.code)!;
      problems.push(`编码重复：${kp.code}（${first} 与 ${where}）`);
    } else seenCodes.set(kp.code, where);
    if (chapter.length > 100) problems.push(`${where}：章节路径超 100 字符（${chapter}）`);
    idToCode.set(kp.id, kp.code);
    kps.push({ chapter, code: kp.code.trim(), name: kp.name.trim(), description: kp.description.trim(), importance: kp.importance });
  };

  for (const c of tree.chapters) {
    // 完全空的章（占位）静默跳过；有内容才要求命名
    if (!c.name.trim() && c.sections.length === 0 && c.kps.length === 0) continue;
    if (!c.name.trim()) problems.push("存在缺名称的章");
    for (const s of c.sections) {
      if (!s.name.trim()) problems.push(`「${c.name}」存在缺名称的节`);
      for (const k of s.kps) pushKp(k, chapterString(c.name, s.name), `${c.name} > ${s.name}`);
    }
    for (const k of c.kps) pushKp(k, c.name, c.name);
  }
  if (kps.length === 0) problems.push("至少录入一个知识点");

  const relations: KpRelationDraft[] = [];
  for (const e of edges) {
    const from = idToCode.get(e.from);
    const to = idToCode.get(e.to);
    if (!from || !to || !isRelationType(e.type)) continue; // 删除级联后理论上不出现
    relations.push({ from_code: from, to_code: to, type: e.type });
  }
  return { kps, relations, problems };
}
