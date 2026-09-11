import {
  ArrowLeft,
  BookOpen,
  ChartLine,
  ClipboardText,
  Student,
  Warning,
} from "@phosphor-icons/react";
import { useMemo, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { AccountCluster, TopBar, TopBarNav } from "../components/TopBar";
import { LoopStateChip } from "../components/ActionPlan";
import { Badge, Button, Card, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { ReportMarkdown } from "../components/Markdown";
import {
  meActionPlan,
  meMastery,
  meProfile,
  meReportFull,
  meReports,
  meSelfMark,
  meStudyPlan,
  meStudyRecords,
  meWeaknesses,
  portalActionPlan,
  portalMastery,
  portalProfile,
  portalReportFull,
  portalReports,
  portalStudyPlan,
  portalStudyRecords,
  portalWeaknesses,
} from "../lib/api";
import type { StudyRecordListItem } from "../lib/api";
import { useAuth } from "../lib/AuthContext";
import { useAsync } from "../lib/hooks";
import type { MasteryItem, WeakItem } from "../lib/types";
import { ACCENTS } from "../lib/theme";

/**
 * 学生自服务门户（auth-roles-design §6 + frontend-ends-design 学生端）：/me 面——
 * 我的薄弱/掌握/已签发报告/改进单/我的学习。按 URL 路由驱动（/portal 系列），报告可深链。
 *
 * 两种主体来源共用同一套渲染（超级账号设计）：
 * - ``self``：学生本人，读 /me/*（学习 tab 含唯一可写操作：方案生成 + 自报）；
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

type Tab = "weak" | "mastery" | "reports" | "plan" | "study";

const TABS: { key: Tab; label: string; icon: typeof Warning; seg: string }[] = [
  { key: "weak", label: "我的薄弱点", icon: Warning, seg: "" },
  { key: "study", label: "我的学习", icon: BookOpen, seg: "/study" },
  { key: "mastery", label: "我的掌握度", icon: ChartLine, seg: "/mastery" },
  { key: "reports", label: "我的报告", icon: ClipboardText, seg: "/reports" },
  { key: "plan", label: "我的改进单", icon: Student, seg: "/plan" },
];

function tabFromPath(path: string, base: string): Tab {
  const rest = path.startsWith(base) ? path.slice(base.length) : "";
  if (rest.startsWith("/study")) return "study";
  if (rest.startsWith("/mastery")) return "mastery";
  if (rest.startsWith("/reports")) return "reports";
  if (rest.startsWith("/plan")) return "plan";
  return "weak";
}

/** 学习 tab 路由基座（薄弱卡「开始学习」深链用）：self → /portal/study，preview → 教学树。 */
function studyBase(source: PortalSource): string {
  return source.kind === "preview" ? `${source.base ?? ""}/study` : "/portal/study";
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
          {tab === "study" && <StudyTab source={source} />}
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
                {/* 干预进度（闭环一期 P1）：发现薄弱 → 老师已安排 → 待复测 → 已见效 */}
                {w.loop_state && <LoopStateChip state={w.loop_state} />}
              </div>
              <p className="mt-1 text-xs text-ink-faint">{w.criterion}</p>
            </div>
            {w.mastery != null && <Pct value={w.mastery} />}
          </div>
          {/* 自学入口（study-loop-design）：修复段学生自驱——AI 按该生错因生成方案 */}
          <div className="mt-3 flex items-center justify-between gap-3 border-t border-line pt-3">
            <span className="text-[11px] text-ink-faint">
              生成针对你的讲解与变式练习，学完自行标记进度
            </span>
            <Link
              to={`${studyBase(source)}?kp_code=${encodeURIComponent(w.code)}`}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent-deep transition-colors hover:bg-accent/20"
            >
              <BookOpen size={13} />
              开始学习
            </Link>
          </div>
        </Card>
      ))}
    </div>
  );
}

/**
 * 我的学习（study-loop-design）：方案列表 + 方案详情（?kp_code= 深链）。
 * self 可生成（首次查看 get-or-generate）与自报「我学会了」；preview 严格只读
 * （无记录显示空态，绝不触发生成；无自报按钮）。
 */
function StudyTab({ source }: { source: PortalSource }) {
  const [sp] = useSearchParams();
  const kpCode = sp.get("kp_code");
  return kpCode ? (
    <StudyPlanDetail source={source} kpCode={kpCode} />
  ) : (
    <StudyList source={source} />
  );
}

function StudyList({ source }: { source: PortalSource }) {
  const { data, loading, error, reload } = useAsync(
    () => portalCall(source, meStudyRecords, portalStudyRecords),
    [source.kind, source.studentId]
  );
  const records = data?.records ?? [];
  const doing = records.filter((r: StudyRecordListItem) => !r.self_marked_at);
  const marked = records.filter((r: StudyRecordListItem) => r.self_marked_at);

  const row = (r: StudyRecordListItem) => (
    <Card key={r.id} className="p-0">
      <Link
        to={`${studyBase(source)}?kp_code=${encodeURIComponent(r.kp_code)}`}
        className="flex items-center justify-between gap-4 px-5 py-4 transition-colors hover:bg-surface-2/60"
      >
        <div>
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            {r.kp_name}
            <Badge>{r.kp_code}</Badge>
            {r.loop_state && <LoopStateChip state={r.loop_state} />}
          </p>
          <p className="mt-0.5 text-[11px] text-ink-faint">
            方案生成于 {r.generated_at?.replace("T", " ").slice(0, 16)}
            {r.self_marked_at
              ? ` · 自报于 ${r.self_marked_at.replace("T", " ").slice(0, 16)}`
              : ""}
          </p>
        </div>
        <span className="inline-flex items-center gap-1 text-xs text-accent">
          <BookOpen size={13} />
          查看
        </span>
      </Link>
    </Card>
  );

  return (
    <div className="space-y-3">
      <SectionTitle>我的学习</SectionTitle>
      {loading && <Skeleton rows={3} />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {data && records.length === 0 && (
        <EmptyState
          title="还没有学习方案"
          hint="去「我的薄弱点」挑一个知识点点「开始学习」，AI 会生成针对你的讲解与练习。"
        />
      )}
      {doing.length > 0 && (
        <>
          <p className="text-xs font-medium text-ink-soft">学习中（学完记得自报进度）</p>
          {doing.map(row)}
        </>
      )}
      {marked.length > 0 && (
        <>
          <p className="mt-4 text-xs font-medium text-ink-soft">
            已自报 · 待下一场考试检验
          </p>
          {marked.map(row)}
        </>
      )}
    </div>
  );
}

function StudyPlanDetail({
  source,
  kpCode,
}: {
  source: PortalSource;
  kpCode: string;
}) {
  const isPreview = source.kind === "preview";
  const plan = useAsync(
    () =>
      portalCall(
        source,
        () => meStudyPlan(kpCode),
        (sid) => portalStudyPlan(sid, kpCode)
      ),
    [source.kind, source.studentId, kpCode]
  );
  const [busy, setBusy] = useState(false);
  const [markError, setMarkError] = useState<string | null>(null);
  // 预览 404（学生尚未生成方案，后端 LookupError 文案）转为空态而非错误
  const notFound = isPreview && (plan.error ?? "").includes("还没有生成过学习方案");

  const doMark = async () => {
    if (!plan.data || plan.data.self_marked_at) return;
    setBusy(true);
    setMarkError(null);
    try {
      await meSelfMark(plan.data.id);
      await plan.reload();
    } catch (e) {
      setMarkError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <SectionTitle>学习方案{plan.data ? ` · ${plan.data.kp_name}` : ""}</SectionTitle>
        <Link
          to={studyBase(source)}
          className="shrink-0 text-xs text-accent hover:underline"
        >
          ← 返回列表
        </Link>
      </div>
      {plan.loading && <Skeleton rows={6} />}
      {plan.error && !notFound && <ErrorState message={plan.error} onRetry={plan.reload} />}
      {notFound && (
        <EmptyState
          title="该知识点还没有学习方案"
          hint="学生在本人门户打开该知识点的「开始学习」后，这里会显示同一份方案（只读）。"
        />
      )}
      {plan.data && (
        <Card className="p-6">
          {plan.data.plan_writer && !plan.data.plan_writer.template && (
            <p className="mb-3 text-[11px] text-ink-faint">
              AI 生成{plan.data.plan_writer.model ? ` · ${plan.data.plan_writer.model}` : ""}
              ，数字与判定以系统计算为准
            </p>
          )}
          <ReportMarkdown content={plan.data.plan_markdown} />
          <div className="mt-5 border-t border-line pt-4">
            {plan.data.self_marked_at ? (
              <p className="flex flex-wrap items-center gap-2 text-xs text-ink-soft">
                {plan.data.loop_state && <LoopStateChip state={plan.data.loop_state} />}
                <span>
                  自报于 {plan.data.self_marked_at.replace("T", " ").slice(0, 16)} ·
                  下一场考试该点的表现会自动验证
                </span>
              </p>
            ) : isPreview ? (
              <p className="text-xs text-ink-faint">
                管理员预览只读：自报操作请以学生本人登录后进行。
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-3">
                <Button disabled={busy} onClick={doMark}>
                  <BookOpen size={14} />
                  我学会了
                </Button>
                <span className="text-xs text-ink-faint">
                  请先完成上面的练习再自报——下一场考试见真章
                </span>
              </div>
            )}
            {markError && (
              <p className="mt-2 rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger" role="alert">
                {markError}
              </p>
            )}
          </div>
        </Card>
      )}
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
