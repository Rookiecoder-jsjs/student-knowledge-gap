import { useEffect } from "react";

/** 为官网入口设置 document.title + meta description（单页站的 SEO 最低限）。 */
export function usePageMeta(title: string, desc: string) {
  useEffect(() => {
    document.title = title;
    let meta = document.querySelector('meta[name="description"]');
    if (!meta) {
      meta = document.createElement("meta");
      meta.setAttribute("name", "description");
      document.head.appendChild(meta);
    }
    meta.setAttribute("content", desc);
  }, [title, desc]);
}
