/** 登录会话（auth-roles-design §7）：token 持久化 + /auth/login + /auth/me 归一。 */

export type SessionRole = "admin" | "teacher" | "student";

export interface ClassRef {
  class_id: number;
  name: string;
}

/** 学科管理员授权（学科×年级；rbac-scopes-design §9）。 */
export interface SubjectScope {
  subject: string;
  grade: number;
}

export interface Session {
  role: SessionRole;
  teacher?: { id: number; name: string; admin: boolean; kb_editor?: boolean };
  classes?: ClassRef[];
  subject_scopes?: SubjectScope[];
  /** 担任班主任的班级 id 集。 */
  homeroom_class_ids?: number[];
  student?: {
    id: number;
    name_or_alias: string;
    class_id: number;
    class_name: string | null;
  };
}

const TOKEN_KEY = "sc.auth.token";
/** 上次访问班级（Shell 班级回落 + 登录落地）：显式退出时清除，避免跨账号残留。 */
export const LAST_CLASS_KEY = "sc.lastClassId";
/** 401 踢出一次性标记（sessionStorage，单标签页）：登录页读取后提示「登录已过期」。 */
export const EXPIRED_KEY = "sc.auth.expired";
const BASE = "/api";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* 隐私模式等：内存会话失效即可 */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* noop */
  }
}

function normalize(body: Record<string, unknown>): Session {
  if (body.role === "student") {
    return { role: "student", student: body.student as Session["student"] };
  }
  return {
    role: body.role === "admin" ? "admin" : "teacher",
    teacher: body.teacher as Session["teacher"],
    classes: (body.classes as ClassRef[]) ?? [],
    subject_scopes: (body.subject_scopes as Session["subject_scopes"]) ?? [],
    homeroom_class_ids: (body.homeroom_class_ids as number[]) ?? [],
  };
}

export async function apiLogin(username: string, password: string): Promise<Session> {
  const r = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const body = (await r.json().catch(() => ({}))) as Record<string, unknown>;
  if (!r.ok) throw new Error((body.detail as string) ?? `登录失败（HTTP ${r.status}）`);
  setToken(body.token as string);
  return normalize(body);
}

/** 会话恢复：无 token → null；token 失效（401）→ 清 token 返回 null。 */
export async function apiMe(): Promise<Session | null> {
  const token = getToken();
  if (!token) return null;
  const r = await fetch(`${BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (r.status === 401) {
    clearToken();
    return null;
  }
  if (!r.ok) return null;
  const body = (await r.json().catch(() => ({}))) as Record<string, unknown>;
  if (!body.authenticated) return null;
  return normalize(body);
}
