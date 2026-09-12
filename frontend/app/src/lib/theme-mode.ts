/**
 * 主题三态（跟随系统/亮/暗）——saas-redesign §5。
 * 首绘由 index.html 的 boot 脚本设 `data-theme`（防 FOUC，KEY 与此处 sc.theme
 * 约定一致）；本模块只负责运行时切换、持久化与系统偏好跟随。
 */

export type ThemeMode = "system" | "light" | "dark";

const KEY = "sc.theme";

/** 解析当前模式（存储缺失/非法 → 跟随系统）。 */
export function getThemeMode(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    /* 隐私模式等：跟随系统 */
    return "system";
  }
}

/** 应用到 `<html data-theme>`；system 按 prefers-color-scheme 解析。 */
export function applyTheme(mode: ThemeMode): void {
  const dark =
    mode === "dark" ||
    (mode === "system" &&
      typeof matchMedia === "function" &&
      matchMedia("(prefers-color-scheme: dark)").matches);
  if (dark) {
    document.documentElement.dataset.theme = "dark";
  } else {
    delete document.documentElement.dataset.theme;
  }
}

/** 切换并持久化（隐私模式只本轮生效，不报错）。 */
export function setThemeMode(mode: ThemeMode): void {
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* 隐私模式：不持久化 */
  }
  applyTheme(mode);
}

/** 系统偏好变化时跟随（仅 system 模式有意义）。App 挂载时调用一次，返回解绑函数。 */
export function watchSystemTheme(): () => void {
  if (typeof matchMedia !== "function") return () => {};
  const mq = matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    if (getThemeMode() === "system") applyTheme("system");
  };
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
