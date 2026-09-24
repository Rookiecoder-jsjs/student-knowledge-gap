import { Link } from "react-router-dom";
import { btnPrimary, btnSm } from "../../lib/buttons";
import { SITE } from "../../lib/site";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

/** 顶栏：与教师端共用粗黑结构线和工作台红动作色。 */
export function Nav() {
  return (
    <header className="sticky top-0 z-50 border-b-[3px] border-ink bg-paper">
      <div className="mx-auto flex h-[72px] w-full max-w-[1240px] items-center justify-between px-4 md:px-6">
        <Link to="/" aria-label={`${SITE.name} 首页`}>
          <Logo compact />
        </Link>

        <div className="flex items-center gap-2">
          <ThemeToggle />
          <a href={SITE.loginUrl} className="px-3 py-2 text-sm font-semibold text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink">
            登录
          </a>
          <Link to="/#cta" className={`${btnPrimary} ${btnSm}`}>
            预约演示
          </Link>
        </div>
      </div>
    </header>
  );
}
