import { Robot } from "@phosphor-icons/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 与后端 app/reports/narrative.py SECTION_HEADER 保持一致。 */
const AI_HEADER = "## AI 解读（模型生成，数字以上文系统计算为准）";

/** 报告 Markdown 渲染：AI 解读段单独成块并带「模型生成」标注。 */
export function ReportMarkdown({ content }: { content: string }) {
  const aiIdx = content.indexOf(AI_HEADER);
  const main = aiIdx >= 0 ? content.slice(0, aiIdx) : content;
  const ai = aiIdx >= 0 ? content.slice(aiIdx + AI_HEADER.length) : null;

  return (
    <div>
      <div className="report-md text-ink">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{main}</ReactMarkdown>
      </div>
      {ai && (
        <div className="mt-6 rounded-2xl border border-accent/20 bg-accent-soft/70 p-5 shadow-soft">
          <div className="mb-3 flex items-center gap-2 border-b border-accent/15 pb-3">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent/15">
              <Robot size={16} className="text-accent-deep" />
            </span>
            <span className="text-sm font-semibold text-accent-deep">AI 解读</span>
            <span className="rounded-lg border border-accent/25 bg-surface px-2 py-0.5 text-xs font-medium text-accent-deep">
              模型生成 · 数字以系统计算为准
            </span>
          </div>
          <div className="report-md text-ink">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{ai}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}
