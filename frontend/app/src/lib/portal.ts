import { LAST_CLASS_KEY, type Session, type SubjectScope } from "./auth";

/**
 * 门户角色旗标（frontend-ends-design §一）：端边界规则的唯一出处。
 *
 * - 开放模式 = 无会话匿名（仅当后端开放模式时 App 才会把 null 会话放进教师树）；
 * - teacher/admin = 登录教师（admin 布尔即管理员，含授课兼管理）；
 * - student 永不进入本模块（App 顶层已分流到学生门户树）。
 *
 * Shell 导航、页面组件（Kb 只读等）与路由守卫共用同一旗标集，避免散点漂移。
 */
export function roleFlags(s: Session | null | undefined) {
  const open = s === null; // 教师树内 null 只会是开放模式（undefined=加载中不渲染树）
  const role = s?.role;
  const isStaff = open || role === "teacher" || role === "admin"; // 待签发等教师语义
  const adminLogin = role === "admin";
  const adminOrOpen = open || role === "admin"; // 演示信任域（开放模式=初始化语义）
  // 两层 KB 写权（存量全局语义）：内容层（录 kp/关系、fork 草稿、draft→reviewed）
  // = admin 或 kb_editor；版本治理（设为正式版=全校口径切换）= 仅 admin。
  // RBAC 范围体系（rbac-scopes-design §9）按 (subject, grade) 细化：kbWrite/kbGovern。
  const scopes: SubjectScope[] = s?.subject_scopes ?? [];
  const homeroomIds = s?.homeroom_class_ids ?? [];
  const kbContentEditable = adminOrOpen || s?.teacher?.kb_editor === true;
  const kbVersionEditable = adminOrOpen;
  const assistantVisible = role === "teacher" || role === "admin"; // 需 gateway token，匿名无
  /** 内容写权（按版本学科×年级）：admin/开放 ∨ kb_editor ∨ 范围授权。 */
  const kbWrite = (subject?: string | null, grade?: number | null): boolean => {
    if (kbContentEditable) return true;
    if (!subject) return false;
    return scopes.some(
      (x) => x.subject === subject && (grade == null || x.grade === grade)
    );
  };
  /** 治理权（启用/停用版本）：admin/开放 ∨ 范围授权（学科管理员，rbac-scopes-design §2）。 */
  const kbGovern = (subject?: string | null, grade?: number | null): boolean => {
    if (kbVersionEditable) return true;
    if (!subject) return false;
    return scopes.some(
      (x) => x.subject === subject && (grade == null || x.grade === grade)
    );
  };
  // KB 总面板（校务台）：admin/开放 ∨ 有学科授权 ∨ kb_editor（rbac-scopes-design §6）
  const kbPanelVisible =
    adminOrOpen || scopes.length > 0 || s?.teacher?.kb_editor === true;
  const isHomeroom = (classId: number): boolean => homeroomIds.includes(classId);
  /** 学生账号开通/重置（rbac-scopes-design §8）：admin/开放 ∨ 本班班主任。 */
  const canEnableStudent = (classId: number): boolean =>
    adminOrOpen || homeroomIds.includes(classId);
  return {
    open, role, isStaff, adminLogin, adminOrOpen, kbContentEditable, kbVersionEditable,
    assistantVisible, scopes, homeroomIds, kbWrite, kbGovern, kbPanelVisible,
    isHomeroom, canEnableStudent,
  };
}

export type RoleFlags = ReturnType<typeof roleFlags>;

/**
 * 返回链「从哪进去回哪」（返回逻辑审计 2026-09-11）。
 *
 * 用 sessionStorage 面包屑而非 location.state：实测 react-router 7.18 +
 * AnimatePresence(mode="wait") 组合下，部分 SPA push 后组件读
 * useLocation().state 得到 undefined（history.state.usr 却完好）——kbVersionId
 * 落版不受影响，返回链这种「必须可靠」的信息不走 state。键按目标路径；
 * 值 null = 显式清除（目标页回落默认）。隐私模式读写失败静默退化默认值。
 */
const BACK_TARGET_KEY = "sc.backTargets";

export function setBackTarget(dest: string, from: string | null): void {
  try {
    const raw = sessionStorage.getItem(BACK_TARGET_KEY);
    const map = raw ? (JSON.parse(raw) as Record<string, string | null>) : {};
    map[dest] = from;
    sessionStorage.setItem(BACK_TARGET_KEY, JSON.stringify(map));
  } catch {
    /* 读不到 sessionStorage 就没有返回链记忆，回落默认 */
  }
}

export function getBackTarget(dest: string, fallback: string): string {
  try {
    const raw = sessionStorage.getItem(BACK_TARGET_KEY);
    const map = raw ? (JSON.parse(raw) as Record<string, string | null>) : {};
    return map[dest] ?? fallback;
  } catch {
    return fallback;
  }
}

/**
 * 登录落地规则（端进出逻辑修订 2026-09-10）：登录成功后应去的路径；
 * null = 停留当前路径——深链保持靠「URL 不动 + App 按会话重渲染」。
 *
 * - 学生：非 /portal 路径回门户首页（教师 URL 不残留）；
 * - 教学树深链（/c/*，含学生视角预览）一律原地保持：/c/ 非法 id 由 Shell
 *   守卫兜底；admin 全校可见，浏览器实测渲染 /c 深链无碍；
 * - /portal/* 是学生命名空间：教师/校管 URL 残留一律弹回自己的落地区；
 * - admin（含带班超管）：无深链 → 直落校务台——管理员主身份语义，不再按
 *   「有没有班」区分纯校管/带班（校务台进出修订 2026-09-12）；
 * - 教师：无深链（/）→ 直落上次班级（sc.lastClassId 在授权列表内才生效；
 *   显式退出会清该键，401 过期重登保留，见 AuthContext.logout）；
 * - 教师撞 /admin/*（路由未挂载）→ 一步到位回 /，不让 `*` 守卫再弹一次。
 */
export function landingFor(s: Session | null, pathname: string): string | null {
  if (!s) return null;
  if (s.role === "student") return pathname.startsWith("/portal") ? null : "/portal";
  const classes = s.classes ?? [];
  if (pathname.startsWith("/c/")) return null;
  if (s.role === "admin") return "/admin";
  if (pathname.startsWith("/portal")) return "/";
  if (s.role === "teacher" && pathname.startsWith("/admin")) {
    // 学科管理员 / kb_editor 可达校务台知识库面板（rbac-scopes-design §6）；
    // 其余 /admin/*（账号/用量）教师一步弹回教学端
    const kbPanelOk =
      pathname.startsWith("/admin/kb") &&
      ((s.subject_scopes?.length ?? 0) > 0 || s.teacher?.kb_editor === true);
    return kbPanelOk ? null : "/";
  }
  if (pathname === "/" || pathname === "") {
    let remembered = 0;
    try {
      remembered = Number(localStorage.getItem(LAST_CLASS_KEY));
    } catch {
      /* 隐私模式等：读不到就不直落 */
    }
    if (
      Number.isInteger(remembered) &&
      remembered > 0 &&
      classes.some((c) => c.class_id === remembered)
    ) {
      return `/c/${remembered}`;
    }
  }
  return null;
}
