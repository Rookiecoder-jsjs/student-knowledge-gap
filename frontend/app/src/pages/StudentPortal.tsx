import {
  ArrowLeft,
  ChartLine,
  ClipboardText,
  Student,
  Warning,
} from "@phosphor-icons/react";
import { useMemo, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { AccountCluster, TopBar, TopBarNav } from "../components/TopBar";
import { Badge, Card, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { ReportMarkdown } from "../components/Markdown";
import {
  meActionPlan,
  meMastery,
  meProfile,
  meReportFull,
  meReports,
  meWeaknesses,
  portalActionPlan,
  portalMastery,
  portalProfile,
  portalReportFull,
  portalReports,
  portalWeaknesses,
} from "../lib/api";
import { useAuth } from "../lib/AuthContext";
import { useAsync } from "../lib/hooks";
import type { MasteryItem, WeakItem } from "../lib/types";
import { ACCENTS } from "../lib/theme";

/**
 * 学生自服务门户（auth-roles-design §6 + frontend-ends-design 学生端）：只读 /me 面——
 * 我的薄弱/掌握/已签发报告/改进单。按 URL 路由驱动（/portal 系列），报告可深链。
 *
 * 两种主体来源共用同一套渲染（超级账号设计）：
 * - ``self``：学生本人，读 /me/*；
 * - ``preview``：admin 预览指定学生，读 /admin/students/{id}/portal/*——后端镜像
 *   端点与 /me 同源同形状（同样只读、同样只发 issued），是端边界内的「查看」而非
 *   切换身份。
 */

export interface PortalSource {
  kind: "self" | "preview";
  /** preview 必填：被查看的学生。 */
  studentId?: number;
  /** preview 必填：预览路由基座（教学树 /c/:cid/students/:sid/portal）——tab 深链用。 */
  base?: string;
  /** preview 可选：列表页带过来的姓名，避免首帧空白。 */
  label?: string;
}

/** 按 source 选端点（self ↔ preview 形状一致）。 */
function portalCall<T>(
  source: PortalSource,
  own: () => Promise<T>,
  as: (studentId: number) => Promise<T>
): Promise<T> {
  return source.kind === "preview"
    ? as(source.studentId ?? 0)
    : own();
}

type Tab = "weak" | "mastery" | "reports" | "plan";

const TABS: { key: Tab; label: string; icon: typeof Warning; seg: string }[] = [
  { key: "weak", label: "我的薄弱点", icon: Warning, seg: "" },
  { key: "mastery", label: "我的掌握度", icon: ChartLine, seg: "/mastery" },
  { key: "reports", label: "我的报告", icon: ClipboardText, seg: "/reports" },
  { key: "plan", label: "我的改进单", icon: Student, seg: "/plan" },
];

function tabFromPath(path: string, base: string): Tab {
  const rest = path.startsWith(base) ? path.slice(base.length) : "";
  if (rest.startsWith("/mastery")) return "mastery";
  if (rest.startsWith("/reports")) return "reports";
  if (rest.startsWith("/plan")) return "plan";
  return "weak";
}

export default function StudentPortal({ source }: { source: PortalSource }) {
  const { session } = useAuth();
  const isPreview = source.kind === "preview";
  const sid = source.studentId ?? 0;
  // 预览挂教学树（分区重设计 2026-09-10）：基座由 App 路由组件传入
  const base = isPreview ? (source.base ?? `/admin/portal/${sid}`) : "/portal";

  const profile = useAsync(
    () => portalCall(source, meProfile, portalProfile),
    [source.kind, sid]
  );
  const location = useLocation();
  const [sp] = useSearchParams();
  // tab 纯由 URL 派生（路由驱动）：链接点击、前进后退、深链同一出处
  const tab = tabFromPath(location.pathname, base);

  const classId = profile.data?.student.class_id;
  const backTo = isPreview && classId ? `/c/${classId}/students` : null;
  const label =
    source.label ??
    session?.student?.name_or_alias ??
    profile.data?.student.name_or_alias ??
    (isPreview ? `学生 #${sid}` : "我");

  return (
    <div className="flex min-h-[100dvh] flex-col">
      {/* 统一顶栏骨架（UI 位置统一）：原第二行吸顶 tab 收进顶栏中段，tab 由 URL 派生 */}
      <TopBar
        brandTo={backTo ?? "/"}
        brandAccent={ACCENTS.student}
        title={
          <>
            {isPreview ? "学生门户预览" : "我的薄弱点分析"}
            {isPreview && <Badge tone="neutral">管理员只读</Badge>}
          </>
        }
        subtitle={
          <>
            {label}
            {profile.data?.student.class_name ? ` · ${profile.data.student.class_name}` : ""}
          </>
        }
        nav={
          <TopBarNav
            navLabel="门户导航"
            layoutId="portal-nav"
            items={TABS.map(({ key, label: l, icon, seg }) => ({
              to: `${base}${seg}${
                key === "reports" && sp.get("report_id") ? `?report_id=${sp.get("report_id")}` : ""
              }`,
              label: l,
              icon,
              accent: ACCENTS.student,
              active: tab === key,
            }))}
          />
        }
        right={
          isPreview ? (
            backTo && (
              <Link
                to={backTo}
                className="inline-flex items-center gap-1.5 rounded-full px-3 py-2 text-[13px] font-medium text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink"
              >
                <ArrowLeft size={15} />
                返回教师端
              </Link>
            )
          ) : (
            session && <AccountCluster session={session} name="" />
          )
        }
      />

      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-[900px] px-6 py-7">
          {isPreview && (
            <p className="mb-4 rounded-lg border border-line bg-surface-2/50 px-4 py-2.5 text-xs text-ink-faint">
              管理员预览：此处为学生登录后所见内容，与自服务页面同源同形状（只读、仅已签发）。
            </p>
          )}
          {tab === "weak" && <WeakTab source={source} />}
          {tab === "mastery" && <MasteryTab source={source} />}
          {tab === "reports" && (
            <ReportsTab source={source} deepReportId={Number(sp.get("report_id")) || null} />
          )}
          {tab === "plan" && <PlanTab source={source} />}
        </div>
      </main>
    </div>
  );
}

function Pct({ value }: { value: number }) {
  const v = Math.round(value * 100);
  const color = v >= 80 ? "#10b981" : v >= 60 ? "#f59e0b" : "#ef4444";
  return (
    <div className="min-w-[110px]">
      <div className="mb-1 flex justify-between text-[11px] text-ink-faint">
        <span style={{ color }}>{v}%</span>
        <span className="normal-case">{v >= 80 ? "达标" : v >= 60 ? "偏低" : "薄弱"}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
        <div className="h-full rounded-full" style={{ width: `${v}%`, background: color }} />
      </div>
    </div>
  );
}

function WeakTab({ source }: { source: PortalSource }) {
  const { data, loading, error, reload } = useAsync(
    () => portalCall(source, meWeaknesses, portalWeaknesses),
    [source.kind, source.studentId]
  );
  return (
    <div className="space-y-3">
      <SectionTitle>可能薄弱的环节</SectionTitle>
      {loading && <Skeleton rows={4} />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && data.weak.length === 0 && (
        <EmptyState title="暂无薄弱环节" hint="当前未发现低于掌握底线的知识点。可查看「我的掌握度」了解各环节水平。" />
      )}
      {data?.weak.map((w: WeakItem) => (
        <Card key={w.code} className="p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{w.name}</span>
                <Badge>{w.code}</Badge>
                {w.class_common && <Badge tone="warn">班级共性</Badge>}
                {w.stale && <Badge tone="danger">久未更新</Badge>}
              </div>
              <p className="mt-1 text-xs text-ink-faint">{w.criterion}</p>
            </div>
            {w.mastery != null && <Pct value={w.mastery} />}
          </div>
        </Card>
      ))}
    </div>
  );
}

function MasteryTab({ source }: { source: PortalSource }) {
  const { data, loading, error, reload } = useAsync(
    () => portalCall(source, meMastery, portalMastery),
    [source.kind, source.studentId]
  );
  const sorted = useMemo(
    () => (data?.mastery ?? []).slice().sort((a, b) => a.mastery - b.mastery),
    [data]
  );
  return (
    <div className="space-y-3">
      <SectionTitle>各知识点掌握度</SectionTitle>
      {loading && <Skeleton rows={4} />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && data.mastery.length === 0 && (
        <EmptyState title="暂无掌握度数据" hint="产生考试或练习证据后，这里会按知识点推导掌握度。" />
      )}
      {sorted.map((m: MasteryItem) => (
        <Card key={m.code} className="flex items-center justify-between gap-4 p-4">
          <div className="flex items-center gap-2">
            <span className="font-medium">{m.name}</span>
            <span className="text-[11px] text-ink-faint">{m.code}</span>
          </div>
          <Pct value={m.mastery} />
        </Card>
      ))}
    </div>
  );
}

interface ReportRow {
  report_id: number;
  type: string;
  type_label?: string;
  exam_id: number | null;
  generated_at: string | null;
}

function ReportsTab({
  source,
  deepReportId = null,
}: {
  source: PortalSource;
  deepReportId?: number | null;
}) {
  const { data, loading, error, reload } = useAsync(
    () => portalCall(source, meReports, portalReports),
    [source.kind, source.studentId]
  );
  const [openId, setOpenId] = useState<number | null>(deepReportId);
  const detail = useAsync(
    () =>
      openId
        ? portalCall(
            source,
            () => meReportFull(openId),
            (sid) => portalReportFull(sid, openId)
          )
        : Promise.reject(new Error("无")),
    [source.kind, source.studentId, openId]
  );
  const rows = (data?.reports ?? []) as ReportRow[];

  return (
    <div className="space-y-3">
      <SectionTitle>已签发的报告</SectionTitle>
      {loading && <Skeleton rows={3} />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && rows.length === 0 && (
        <EmptyState title="暂无已签发报告" hint="教师签发后的诊断单与改进单会出现在这里。" />
      )}
      {rows.map((r) => (
        <Card key={r.report_id} className="p-0">
          <button
            onClick={() => setOpenId(openId === r.report_id ? null : r.report_id)}
            className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left transition-colors hover:bg-surface-2/60"
          >
            <div>
              <p className="text-sm font-semibold">{r.type_label ?? r.type}</p>
              <p className="text-[11px] text-ink-faint">
                {r.generated_at?.replace("T", " ").slice(0, 16)}
                {r.exam_id ? ` · 关联考试 #${r.exam_id}` : ""}
              </p>
            </div>
            <span className="text-xs text-accent">{openId === r.report_id ? "收起" : "查看"}</span>
          </button>
          {openId === r.report_id && (
            <div className="border-t border-line px-5 py-4">
              {detail.loading && <Skeleton rows={2} />}
              {detail.error && <p className="text-xs text-danger">{detail.error}</p>}
              {detail.data && <ReportMarkdown content={detail.data.markdown} />}
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}

function PlanTab({ source }: { source: PortalSource }) {
  const { data, loading, error, reload } = useAsync(
    () => portalCall(source, meActionPlan, portalActionPlan),
    [source.kind, source.studentId]
  );
  return (
    <div className="space-y-3">
      <SectionTitle>我的改进单</SectionTitle>
      {loading && <Skeleton rows={3} />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && !data.markdown && (
        <EmptyState title="暂无改进单" hint="教师签发改进单后，会与诊断单一起在这里展示。" />
      )}
      {data?.markdown && (
        <Card className="p-6">
          <ReportMarkdown content={data.markdown} />
        </Card>
      )}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
      <span className="h-4 w-1 rounded-full" style={{ background: ACCENTS.student }} />
      {children}
    </h2>
  );
}
