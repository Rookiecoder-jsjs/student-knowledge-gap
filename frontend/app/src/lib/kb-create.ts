import { createKbVersion, createKp, createRelation, listKps } from "./api";

/** 知识点草稿（向导与导图共用；kb-mindmap-create §3）。 */
export interface KpDraft {
  chapter: string;
  code: string;
  name: string;
  description: string;
  importance: string;
}

/** 关系草稿：端点记编码（建库时知识点尚无 id，落库时经 code→id 映射转正）。 */
export interface KpRelationDraft {
  from_code: string;
  to_code: string;
  type: string;
}

export interface KbBasic {
  subject: string;
  edition: string;
  version: string;
  grade: number;
  /** 自动编码前缀（仅导图模式用；默认按学科+年级推导，可改）。 */
  codePrefix?: string;
}

export const COMMON_SUBJECTS = ["数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理", "道德与法治"];
export const COMMON_EDITIONS = ["人教版", "北师大版", "苏教版", "沪教版", "浙教版", "外研版", "译林版"];

const SUBJECT_LETTER: Record<string, string> = {
  数学: "M",
  语文: "C",
  英语: "E",
  物理: "P",
  化学: "H",
  生物: "B",
  历史: "S",
  地理: "G",
  道德与法治: "D",
};

/** 编码前缀推导：学科字母 + 年级 + 上A（kb-mindmap-create §1；对标 M7A-105）。 */
export function derivePrefix(subject: string, grade: number): string {
  const letter = SUBJECT_LETTER[subject] ?? (subject ? subject.slice(0, 1).toUpperCase() : "K");
  return `${letter}${grade || 7}A`;
}

/** 步骤 1 缺省值（总面板「新建」预填：rbac-scopes-design §6）。 */
export function defaultBasic(prefill?: { kbSubject?: string; kbGrade?: number } | null): KbBasic {
  const subject = prefill?.kbSubject ?? "";
  const grade = prefill?.kbGrade ?? 7;
  return { subject, edition: "人教版", version: "0.1.0", grade, codePrefix: derivePrefix(subject, grade) };
}

export interface CreatePhase {
  phase: "kp" | "relation";
  done: number;
  total: number;
}

/** 批量粘贴行解析（原 KbCreate 内函数收编）：「编码 名称」或「编码 名称 | 描述」。 */
export function parseKpLine(line: string, chapter: string): KpDraft | null {
  const [head, ...descParts] = line.split("|");
  const tokens = head.trim().split(/\s+/);
  if (tokens.length < 2 || !tokens[0] || !tokens[1]) return null;
  return {
    chapter,
    code: tokens[0],
    name: tokens.slice(1).join(" "),
    description: descParts.join("|").trim(),
    importance: "核心",
  };
}

/**
 * 共享创建管道：版本 → 逐点写入（建 code→id 映射）→ 逐条关系（kb-mindmap-create §3）。
 * existingKbId 传入时跳过建版（失败重试不产生重复版本），并从库内重建映射以支持关系重试。
 */
export async function createKbLibrary(
  basic: KbBasic,
  kps: KpDraft[],
  relations: KpRelationDraft[],
  onProgress?: (p: CreatePhase) => void,
  existingKbId?: number
): Promise<{ kbId: number; failedKps: KpDraft[]; failedRelations: KpRelationDraft[] }> {
  const kbId = existingKbId ?? (await createKbVersion({
    subject: basic.subject.trim(),
    grade: basic.grade,
    textbook_edition: basic.edition.trim(),
    version: basic.version.trim(),
  })).id;

  const codeToId = new Map<string, number>();
  if (existingKbId != null) {
    const existing = await listKps(existingKbId);
    for (const k of existing.kps) codeToId.set(k.code, k.id);
  }

  const failedKps: KpDraft[] = [];
  for (let i = 0; i < kps.length; i++) {
    const kp = kps[i];
    try {
      const node = await createKp({
        code: kp.code,
        name: kp.name,
        grade: basic.grade,
        chapter: kp.chapter,
        description: kp.description || undefined,
        importance: kp.importance,
        kb_version_id: kbId,
      });
      codeToId.set(node.code, node.id);
    } catch {
      failedKps.push(kp);
    }
    onProgress?.({ phase: "kp", done: i + 1, total: kps.length });
  }

  // 端点未写入成功的边自动跳过（同样进失败列表，修好编码后可重试）
  const failedRelations: KpRelationDraft[] = [];
  for (let i = 0; i < relations.length; i++) {
    const r = relations[i];
    const fromId = codeToId.get(r.from_code);
    const toId = codeToId.get(r.to_code);
    if (fromId == null || toId == null) {
      failedRelations.push(r);
      continue;
    }
    try {
      await createRelation({ from_kp_id: fromId, to_kp_id: toId, type: r.type, kb_version_id: kbId });
    } catch {
      failedRelations.push(r);
    }
    onProgress?.({ phase: "relation", done: i + 1, total: relations.length });
  }

  return { kbId, failedKps, failedRelations };
}
