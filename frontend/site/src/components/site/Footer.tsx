import { SITE } from "../../lib/site";
import { Logo } from "./Logo";

/** 页脚：夜色收束。ICP 备案号待定稿后补（不渲染假号）。 */
export function Footer() {
  return (
    <footer className="relative overflow-hidden border-t-[3px] border-ink bg-night text-white">
      <span className="pointer-events-none absolute right-10 top-0 h-16 w-16 bg-primary" aria-hidden />
      <span className="pointer-events-none absolute right-28 top-16 h-10 w-10 bg-bh-blue" aria-hidden />
      <div className="relative mx-auto grid w-full max-w-[1200px] gap-10 px-4 py-14 md:grid-cols-[1.4fr_1fr_1fr] md:px-6">
        <div>
          <Logo dark />
          <p className="mt-5 max-w-[42ch] text-sm leading-7 text-white/55">
            {SITE.tagline}。帮助学校看见问题、安排行动、持续验证。
          </p>
        </div>
        <nav aria-label="页脚导航">
          <p className="text-xs font-semibold tracking-[0.2em] text-white/60 uppercase">页面</p>
          <ul className="mt-4 space-y-2.5 text-sm">
            <li><a className="text-white/70 transition-colors hover:text-white" href="/product">产品</a></li>
            <li><a className="text-white/70 transition-colors hover:text-white" href="/solutions">方案</a></li>
            <li><a className="text-white/70 transition-colors hover:text-white" href="/security">安心使用</a></li>
            <li><a className="text-white/70 transition-colors hover:text-white" href="/about">关于</a></li>
          </ul>
        </nav>
        <div>
          <p className="text-xs font-semibold tracking-[0.2em] text-white/60 uppercase">联系</p>
          <ul className="mt-4 space-y-2.5 text-sm text-white/70">
            <li>
              商务合作：<a className="underline decoration-white/30 underline-offset-4 transition-colors hover:text-white" href={`mailto:${SITE.email}`}>{SITE.email}</a>
            </li>
            <li>
              <a className="transition-colors hover:text-white" href={SITE.loginUrl}>已有账号？前往登录 →</a>
            </li>
          </ul>
        </div>
      </div>
      <div className="border-t-2 border-white/20">
        <div className="mx-auto flex w-full max-w-[1200px] flex-wrap items-center justify-between gap-2 px-4 py-5 text-xs text-white/60 md:px-6">
          <span>© 2026 {SITE.name}</span>
          <span>让教学改进持续发生</span>
        </div>
      </div>
    </footer>
  );
}
