import { Monitor, Moon, Sun } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { applyTheme, getThemeMode, setThemeMode, type ThemeMode } from "../lib/theme-mode";

const ORDER: ThemeMode[] = ["system", "light", "dark"];

const META: Record<ThemeMode, { label: string; Icon: typeof Sun }> = {
  system: { label: "跟随系统", Icon: Monitor },
  light: { label: "亮色", Icon: Sun },
  dark: { label: "暗色", Icon: Moon },
};

/**
 * 主题三态循环切换钮（saas-redesign §5.3）：跟随系统 → 亮 → 暗 → 循环。
 * 教师端账号簇与学生端 TopBar 经 AccountCluster 共用，一处接入全端生效。
 */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const [mode, setMode] = useState<ThemeMode>(() => getThemeMode());

  // system 模式下系统偏好变化：应用新解析值，并把图标态刷新到位
  useEffect(() => {
    if (mode !== "system" || typeof matchMedia !== "function") return;
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [mode]);

  const { label, Icon } = META[mode];
  return (
    <button
      onClick={() => {
        const next = ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];
        setMode(next);
        setThemeMode(next);
      }}
      aria-label={`主题：${label}，点击切换`}
      title={`主题：${label}`}
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink ${className}`}
    >
      <Icon size={15} />
    </button>
  );
}
