import { Monitor, Moon, Sun } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { applyTheme, getThemeMode, setThemeMode, type ThemeMode } from "../../lib/theme-mode";

const modes: ThemeMode[] = ["system", "light", "dark"];
const meta = {
  system: { label: "跟随系统", Icon: Monitor },
  light: { label: "亮色", Icon: Sun },
  dark: { label: "暗色", Icon: Moon },
} as const;

export function ThemeToggle() {
  const [mode, setMode] = useState<ThemeMode>(() => getThemeMode());

  useEffect(() => {
    if (mode !== "system") return;
    const media = matchMedia("(prefers-color-scheme: dark)");
    const sync = () => applyTheme("system");
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [mode]);

  const { label, Icon } = meta[mode];
  return (
    <button
      type="button"
      aria-label={`主题：${label}，点击切换`}
      title={`主题：${label}`}
      onClick={() => {
        const next = modes[(modes.indexOf(mode) + 1) % modes.length];
        setMode(next);
        setThemeMode(next);
      }}
      className="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-full border border-line bg-surface text-ink-soft transition-colors hover:border-primary/45 hover:text-primary"
    >
      <Icon size={16} aria-hidden />
    </button>
  );
}
