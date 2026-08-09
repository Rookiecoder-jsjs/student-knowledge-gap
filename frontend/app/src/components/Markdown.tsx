import { Robot } from "@phosphor-icons/react";
import { useMemo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 与后端 app/reports/narrative.py SECTION_HEADER 保持一致。 */
const AI_HEADER = "## AI 解读（模型生成，数字以上文系统计算为准）";

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
  const aiIdx = content.indexOf(AI_HEADER);
  const main = aiIdx >= 0 ? content.slice(0, aiIdx) : content;
  const ai = aiIdx >= 0 ? content.slice(aiIdx + AI_HEADER.length) : null;

  return (
    <div>
      <div className="report-md text-ink">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={headingComponents}>
          {main}
        </ReactMarkdown>
      </div>
      {ai && (
        <div className="mt-6 rounded-[10px] border border-accent/20 bg-accent-soft/70 p-5 shadow-soft">
          <div className="mb-3 flex items-center gap-2 border-b border-accent/15 pb-3">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accent/15">
              <Robot size={16} className="text-accent-deep" />
            </span>
            <span className="text-sm font-semibold text-accent-deep">AI 解读</span>
            <span className="rounded-md border border-accent/25 bg-surface px-2 py-0.5 text-xs font-medium text-accent-deep">
              模型生成 · 数字以系统计算为准
            </span>
          </div>
          <div className="report-md text-ink">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={headingComponents}>
              {ai}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}
