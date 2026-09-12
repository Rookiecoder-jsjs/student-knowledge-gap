import { lazy, Suspense, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";
import { defaultBasic } from "../lib/kb-create";
import type { KbBasic } from "../lib/kb-create";
import { getBackTarget, roleFlags } from "../lib/portal";
import { ACCENTS } from "../lib/theme";
import { Card, Page, PageHeader, Skeleton, Tabs } from "../components/ui";
import KbWizard from "../components/kb/KbWizard";

/** 导图画布较重（@xyflow/react），路由分包按需加载。 */
const MindMapMode = lazy(() => import("../components/kb/MindMapMode"));

/**
 * 图形化新建知识库（kb-mindmap-create §4）：Tabs 双模式入口——
 * 分步向导（原 KbCreate 流程）与思维导图；步骤 1 基本信息两模式共用状态。
 * 写权门槛与 /kb 一致（非 kb_content_editable 见只读提示；后端 require_kb_editor 兜底）。
 */
export default function KbNew() {
  const editable = roleFlags(useAuth().session).kbContentEditable;
  const prefill = (useLocation().state as { kbSubject?: string; kbGrade?: number } | null) ?? null;
  const backTo = getBackTarget("/kb/new", "/kb");
  const backLabel = backTo === "/admin/kb" ? "返回知识库总览" : "返回知识库";
  const [basic, setBasic] = useState<KbBasic>(() => defaultBasic(prefill));
  const [mode, setMode] = useState<"wizard" | "mindmap">("wizard");

  if (!editable) {
    return (
      <Page accent={ACCENTS.knowledge}>
        <Link
          to={backTo}
          className="mb-4 inline-flex items-center gap-1 text-sm text-ink-soft transition-colors hover:text-accent"
        >
          ← {backLabel}
        </Link>
        <Card className="p-8 text-center text-sm text-ink-soft">
          知识库创建需要内容编辑权限（管理员或被授权的 kb_editor 教师）。
        </Card>
      </Page>
    );
  }

  return (
    <Page accent={ACCENTS.knowledge}>
      <Link
        to={backTo}
        className="mb-4 inline-flex items-center gap-1 text-sm text-ink-soft transition-colors hover:text-accent"
      >
        ← {backLabel}
      </Link>
      <PageHeader
        title="图形化新建知识库"
        desc="两种方式创建草稿版本：分步向导（文本批量粘贴）或思维导图（可视化搭结构、拖线标关系）"
      />
      <Tabs
        ariaLabel="建库方式"
        layoutId="kbnew-mode"
        className="mb-5"
        tabs={[
          { key: "wizard", label: "分步向导" },
          { key: "mindmap", label: "思维导图" },
        ] as const}
        value={mode}
        onChange={setMode}
      />
      {mode === "wizard" ? (
        <KbWizard basic={basic} onBasicChange={setBasic} backTo={backTo} />
      ) : (
        <Suspense fallback={<Card className="p-4"><Skeleton rows={5} /></Card>}>
          <MindMapMode basic={basic} onBasicChange={setBasic} backTo={backTo} />
        </Suspense>
      )}
    </Page>
  );
}
