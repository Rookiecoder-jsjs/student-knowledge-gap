import { Robot } from "@phosphor-icons/react";
import { useMemo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 兼容历史与当前后端文案；AI 解读始终在视觉上优先于系统报告。 */
const AI_HEADER_RE = /^## AI 解读[^\n]*$/m;

function splitReport(content: string): { main: string; ai: string | null } {
  const match = AI_HEADER_RE.exec(content);
  if (!match || match.index === undefined) return { main: content, ai: null };
  return {
    main: content.slice(0, match.index).trimEnd(),
    ai: content.slice(match.index + match[0].length).trim(),
  };
}

/** 把标题文本转成锚点 id（兼容中文）。 */
function slug(s: string): string {
  return s
    .replace(/[`*_]/g, "")
    .trim()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
}

/** 从 ReactMarkdown 标题子节点提取纯文本。 */
function nodeText(children: ReactNode): string {
  if (typeof children === "string") return children;
  if (Array.isArray(children)) return children.map(nodeText).join("");
  if (children && typeof children === "object" && "props" in children) {
    // @ts-expect-error react node shape
    return nodeText(children.props?.children);
  }
  return "";
}

/** 标题渲染器：注入锚点 id，供 TOC 跳转。 */
const headingComponents = {
  h1: ({ children }: { children?: ReactNode }) => <h1 id={slug(nodeText(children))}>{children}</h1>,
  h2: ({ children }: { children?: ReactNode }) => <h2 id={slug(nodeText(children))}>{children}</h2>,
  h3: ({ children }: { children?: ReactNode }) => <h3 id={slug(nodeText(children))}>{children}</h3>,
};

/** 报告目录：从 markdown 标题提取，sticky 侧栏。 */
export function ReportTOC({ content }: { content: string }) {
  const headings = useMemo(() => {
    const out: { level: number; text: string; id: string }[] = [];
    for (const line of content.split("\n")) {
      const m = /^(#{1,3})\s+(.+)$/.exec(line.trim());
      if (m) {
        const text = m[2].replace(/[`*_]/g, "").trim();
        if (text.startsWith("AI 解读")) continue;
        out.push({ level: m[1].length, text, id: slug(text) });
      }
    }
    return out;
  }, [content]);

  if (headings.length === 0) return null;
  return (
    <nav aria-label="报告目录" className="text-xs">
      <p className="mb-2 font-semibold uppercase tracking-wide text-ink-faint">目录</p>
      <ul className="space-y-1">
        {headings.map((h, i) => (
          <li key={i} className={h.level === 1 ? "" : h.level === 2 ? "pl-2" : "pl-4"}>
            <a
              href={`#${h.id}`}
              className="block truncate text-ink-soft transition-colors hover:text-accent"
            >
              {h.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** 报告 Markdown 渲染：AI 解读段单独成块并带「模型生成」标注。 */
export function ReportMarkdown({ content }: { content: string }) {
  const { main, ai } = splitReport(content);

  return (
    <div>
      {ai && (
        <section
          aria-labelledby="ai-insight-title"
          className="mb-6 rounded-[10px] border border-accent/25 bg-accent-soft/70 p-5 shadow-soft"
        >
          <div className="mb-4 flex flex-wrap items-center gap-2 border-b border-accent/15 pb-3">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-md bg-accent/15"
            >
              <Robot size={16} className="text-accent-deep" />
            </span>
            <h2 id="ai-insight-title" className="text-base font-semibold text-accent-deep">
              AI 教研解读
            </h2>
            <span className="rounded-md bg-accent px-2 py-0.5 text-xs font-semibold text-white">
              优先阅读
            </span>
            <span className="rounded-md border border-accent/25 bg-surface px-2 py-0.5 text-xs font-medium text-accent-deep">
              模型生成 · 数字以系统计算为准
            </span>
          </div>
          <div className="report-md text-ink">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={headingComponents}>
              {ai}
            </ReactMarkdown>
          </div>
        </section>
      )}
      <div className="report-md text-ink">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={headingComponents}>
          {main}
        </ReactMarkdown>
      </div>
    </div>
  );
}
