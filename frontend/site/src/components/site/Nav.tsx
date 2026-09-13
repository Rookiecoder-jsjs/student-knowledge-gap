import { Link } from "react-router-dom";
import { btnPrimary, btnSm } from "../../lib/buttons";
import { SITE } from "../../lib/site";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

/** 顶栏：毛玻璃吸顶；只保留品牌、主题、登录与预约，不承担内容分区切换。 */
export function Nav() {
  return (
    <header className="sticky top-0 z-50 border-b border-line/80 bg-paper/88 shadow-[0_8px_24px_-24px_rgba(33,42,36,.5)] backdrop-blur-xl">
      <div className="mx-auto flex h-[68px] w-full max-w-[1240px] items-center justify-between px-4 md:px-6">
        <Link to="/" aria-label={`${SITE.name} 首页`}>
          <Logo compact />
        </Link>

        <div className="flex items-center gap-2">
          <ThemeToggle />
          <a href={SITE.loginUrl} className="rounded-md px-3 py-2 text-sm text-ink-soft transition-colors hover:text-ink">
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
