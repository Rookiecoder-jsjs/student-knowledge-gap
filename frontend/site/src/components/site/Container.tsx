import type { ReactNode } from "react";

/** 官网内容宽度：1200 上限，窄屏 16px 边距（禁止横向溢出）。 */
export function Container({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`mx-auto w-full max-w-[1200px] px-4 md:px-6 ${className}`}>{children}</div>;
}
