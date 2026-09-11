import { LAST_CLASS_KEY, type Session } from "./auth";

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
  // 两层 KB 写权：内容层（录 kp/关系、fork 草稿、draft→reviewed）= admin 或被授权
  // 教师；版本治理（设为正式版=全校口径切换）= 仅 admin。
  const kbContentEditable = adminOrOpen || s?.teacher?.kb_editor === true;
  const kbVersionEditable = adminOrOpen;
  const assistantVisible = role === "teacher" || role === "admin"; // 需 gateway token，匿名无
  return {
    open, role, isStaff, adminLogin, adminOrOpen, kbContentEditable, kbVersionEditable,
    assistantVisible,
  };
}

export type RoleFlags = ReturnType<typeof roleFlags>;

/**
 * 登录落地规则（端进出逻辑修订 2026-09-10）：登录成功后应去的路径；
 * null = 停留当前路径——深链保持靠「URL 不动 + App 按会话重渲染」。
 *
 * - 学生：非 /portal 路径回门户首页（教师 URL 不残留）；
 * - 教学树深链（/c/*，含学生视角预览）一律原地保持：/c/ 非法 id 由 Shell
 *   守卫兜底；admin 全校可见，浏览器实测纯校管渲染 /c 深链无碍；
 * - /portal/* 是学生命名空间：教师/校管 URL 残留一律弹回自己的落地区；
 * - 纯校管（admin 无班）：无深链 → 直落校务台；
 * - 教师 / 带班 admin：无深链（/）→ 直落上次班级（sc.lastClassId 在授权
 *   列表内才生效；显式退出会清该键，401 过期重登保留，见 AuthContext.logout）；
 * - 教师撞 /admin/*（路由未挂载）→ 一步到位回 /，不让 `*` 守卫再弹一次。
 */
export function landingFor(s: Session | null, pathname: string): string | null {
  if (!s) return null;
  if (s.role === "student") return pathname.startsWith("/portal") ? null : "/portal";
  const classes = s.classes ?? [];
  const pureAdmin = s.role === "admin" && classes.length === 0;
  if (pathname.startsWith("/c/")) return null;
  if (pathname.startsWith("/portal")) return pureAdmin ? "/admin" : "/";
  if (s.role === "teacher" && pathname.startsWith("/admin")) return "/";
  if (pathname === "/" || pathname === "") {
    if (pureAdmin) return "/admin";
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
