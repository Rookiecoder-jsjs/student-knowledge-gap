import {
  CaretDown,
  CaretRight,
  Check,
  DotsThree,
  DownloadSimple,
  MagnifyingGlass,
  Plus,
  Upload,
} from "@phosphor-icons/react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { KpDetailEditor } from "../components/KpDetailEditor";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Modal,
  Page,
  PageHeader,
  Select,
  Skeleton,
  Tabs,
} from "../components/ui";
import { Reveal, StaggerItem, StaggerList } from "../components/motion";
import {
  createKp,
  downloadKbYaml,
  forkKbVersion,
  kbCompatibility,
  kbUpload,
  kpDetail,
  listAllKps,
  listKbVersions,
  listRelations,
  patchKbVersion,
  type KbCompatibility,
  type KpDetail,
  type KpNode,
} from "../lib/api";
import { useAsync } from "../lib/hooks";
import { getBackTarget, roleFlags, setBackTarget } from "../lib/portal";
import { useAuth } from "../lib/AuthContext";
import { versionStatusLabel } from "../lib/labels";
import { ACCENTS } from "../lib/theme";
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from "../lib/types";

const KnowledgeGraph2D = lazy(() => import("../components/graph/KnowledgeGraph2D"));

/**
 * 知识库浏览页（2026-09-11 按使用频率重构，frontend-ends-design §知识库）。
 *
 * 老师的四个真实任务与落位：
 * - 查知识点讲什么   → 首屏目录树（章节手风琴）+ 详情浮卡阅读态；
 * - 更正小错误       → 详情浮卡「编辑知识点」（常用四字段）；
 * - 补充新知识点     → 首屏「+ 补充知识点」；
 * - 换教材版本       → 「⋯ 版本与治理」菜单（导入/导出/修订/启用/版本切换）。
 * 两层写权 → RBAC 范围（rbac-scopes-design §4/§9）：内容层 = admin/开放 ∨
 * kb_editor ∨ 学科×年级授权（kbWrite 按当前版本学科×年级判定）；版本治理 =
 * admin/开放 ∨ 范围授权（kbGovern——学科管理员可启用本学科版本）。普通教师纯只读。
 */
export default function Kb() {
  const flags = roleFlags(useAuth().session);
  const location = useLocation();
  const nav = useNavigate();
  const versions = useAsync(() => listKbVersions(), []);
  // 向导建完跳回时经 location.state 直接落在新建版本；返回链走 sessionStorage
  // 面包屑（lib/portal getBackTarget——location.state 在本 app 不可靠）
  const kbState = location.state as { kbVersionId?: number } | null;
  const [versionId, setVersionId] = useState<number | null>(kbState?.kbVersionId ?? null);
  const [view, setView] = useState<"list" | "graph">("list");
  const kbBackTo = getBackTarget("/kb", "/");
  // 多学科（2026-09-11）：学科从版本表派生；缺省跟随全局 active 版本的学科。
  // 版本治理（切换/兼容对照）都在同学科内进行——后端按 subject 解析 active。
  const allVersions = versions.data?.versions ?? [];
  const subjects = useMemo(
    () => [...new Set((versions.data?.versions ?? []).map((v) => v.subject))],
    [versions.data]
  );
  const [subjectPick, setSubjectPick] = useState<string | null>(null);
  const subject =
    subjectPick ??
    // 显式选中的版本优先（向导建完跳回落在物理草稿，而非数学 active）
    (versionId != null ? allVersions.find((v) => v.id === versionId)?.subject : undefined) ??
    allVersions.find((v) => v.is_active)?.subject ??
    subjects[0] ??
    null;
  const subjectVersions = allVersions.filter((v) => v.subject === subject);
  const activeVersion =
    subjectVersions.find((v) => v.is_active) ?? subjectVersions[0];
  const currentVersionId = versionId ?? activeVersion?.id ?? null;
  const currentIs =
    allVersions.find((v) => v.id === currentVersionId) ?? undefined;
  // 范围写权按当前版本（学科×年级）判定（rbac-scopes-design §9）
  const editable = flags.kbWrite(subject, currentIs?.grade ?? null);
  const canActivate = flags.kbGovern(subject, currentIs?.grade ?? null);

  const kps = useAsync(
    () =>
      currentVersionId
        ? listAllKps(currentVersionId).then((kps) => ({ kb_version_id: currentVersionId, kps }))
        : Promise.resolve({ kb_version_id: 0, kps: [] as KpNode[] }),
    [currentVersionId]
  );
  const relations = useAsync(
    () => (view === "graph" && currentVersionId ? listRelations(currentVersionId) : Promise.resolve(null)),
    [currentVersionId, view],
  );

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const detail = useAsync(
    () => (selectedId ? kpDetail(selectedId) : Promise.resolve<KpDetail | null>(null)),
    [selectedId]
  );

  // 搜索过滤（修 P2-5）
  const [query, setQuery] = useState("");

  // 版本治理状态
  const [switchComp, setSwitchComp] = useState<KbCompatibility | null>(null);
  const [switchBusy, setSwitchBusy] = useState(false);
  const [switchErr, setSwitchErr] = useState<string | null>(null);
  const [forkBusy, setForkBusy] = useState(false);
  const [forkErr, setForkErr] = useState<string | null>(null);
  // 导入/导出：导入=上传 YAML 建新版本；导出=带鉴权 fetch 下载（window.open 裸
  // 请求无 Authorization 头，安全模式 401——2026-09-11 用户实报）
  const importFileRef = useRef<HTMLInputElement>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importErr, setImportErr] = useState<string | null>(null);
  const [exportErr, setExportErr] = useState<string | null>(null);

  // 补充知识点
  const [createOpen, setCreateOpen] = useState(false);
  const [createCode, setCreateCode] = useState("");
  const [createName, setCreateName] = useState("");
  const [createChapter, setCreateChapter] = useState("");
  const [createDesc, setCreateDesc] = useState("");
  const [createImportance, setCreateImportance] = useState("核心");
  const [createBusy, setCreateBusy] = useState(false);
  const [createErr, setCreateErr] = useState<string | null>(null);

  const chapters = useMemo(() => {
    const q = query.trim().toLowerCase();
    const map = new Map<string, KpNode[]>();
    for (const k of kps.data?.kps ?? []) {
      if (q && !k.code.toLowerCase().includes(q) && !k.name.toLowerCase().includes(q) && !(k.chapter || "").toLowerCase().includes(q)) {
        continue;
      }
      const ch = k.chapter || "未分组";
      if (!map.has(ch)) map.set(ch, []);
      map.get(ch)!.push(k);
    }
    return [...map.entries()];
  }, [kps.data, query]);

  const graphNodes = useMemo<KnowledgeGraphNode[]>(
    () => (kps.data?.kps ?? []).filter((node) => !node.archived).map((node) => ({
      ...node,
      chapter: node.chapter || "未分组",
      mastery: null,
      weak_share: null,
      evidence_count: 0,
      student_count: 0,
    })),
    [kps.data],
  );
  const graphEdges = useMemo<KnowledgeGraphEdge[]>(
    () => (relations.data?.relations ?? []).map((relation) => ({
      id: relation.id,
      from: relation.from.id,
      to: relation.to.id,
      type: relation.type,
      weight: relation.weight,
    })),
    [relations.data],
  );

  // 章节手风琴：首次数据到达默认展开第一章；搜索时全部展开（结果直接可见）
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const didInitExpand = useRef(false);
  useEffect(() => {
    if (!didInitExpand.current && chapters.length > 0) {
      didInitExpand.current = true;
      setExpanded(new Set([chapters[0][0]]));
    }
  }, [chapters]);
  const isOpen = (ch: string) => query.trim() !== "" || expanded.has(ch);
  const toggleChapter = (ch: string) =>
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(ch)) next.delete(ch);
      else next.add(ch);
      return next;
    });

  const switchSubject = (s: string) => {
    setSubjectPick(s);
    setVersionId(null);
    setSelectedId(null);
  };

  async function doExport() {
    if (!currentVersionId) return;
    setExportErr(null);
    try {
      const blob = await downloadKbYaml(currentVersionId);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `kb-v${currentVersionId}.yaml`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      setExportErr(e instanceof Error ? e.message : "导出失败");
    }
  }

  async function doImport(file: File) {
    setImportBusy(true);
    setImportErr(null);
    try {
      const r = await kbUpload(file);
      setVersionId(r.kb_version_id);
      setSelectedId(null);
      versions.reload();
    } catch (e) {
      setImportErr(e instanceof Error ? e.message : "导入失败");
    } finally {
      setImportBusy(false);
    }
  }

  async function doFork() {
    setForkBusy(true);
    setForkErr(null);
    try {
      // fork 源 = 当前浏览版本（多学科：源跟学科走，不串）
      await forkKbVersion(currentVersionId ?? undefined);
      versions.reload();
    } catch (e) {
      setForkErr(e instanceof Error ? e.message : "复制失败");
    } finally {
      setForkBusy(false);
    }
  }

  async function startSwitch() {
    if (!currentVersionId) return;
    setSwitchBusy(true);
    setSwitchErr(null);
    try {
      setSwitchComp(await kbCompatibility(currentVersionId));
    } catch (e) {
      setSwitchErr(e instanceof Error ? e.message : "加载兼容性失败");
    } finally {
      setSwitchBusy(false);
    }
  }

  async function confirmSwitch() {
    if (!switchComp || !currentVersionId) return;
    const needForce = switchComp.missing_codes.length > 0;
    const needConfirm = switchComp.attribute_changes.length > 0;
    setSwitchBusy(true);
    setSwitchErr(null);
    try {
      await patchKbVersion(currentVersionId, "active", {
        confirm: needConfirm,
        force: needForce,
      });
      setSwitchComp(null);
      setSelectedId(null);
      versions.reload();
    } catch (e) {
      setSwitchErr(e instanceof Error ? e.message : "切换失败");
    } finally {
      setSwitchBusy(false);
    }
  }

  async function doCreate() {
    if (!createCode.trim() || !createName.trim()) {
      setCreateErr("编码与名称必填（编码需唯一，如 M7A-5xx）");
      return;
    }
    setCreateBusy(true);
    setCreateErr(null);
    try {
      const kp = await createKp({
        code: createCode.trim(),
        name: createName.trim(),
        grade: kps.data?.kps[0]?.grade ?? 7,
        chapter: createChapter.trim() || undefined,
        description: createDesc.trim() || undefined,
        importance: createImportance,
        // 写入当前浏览的版本（草稿可写；active 版本后端拒绝直写）
        kb_version_id: currentVersionId ?? undefined,
      });
      setCreateOpen(false);
      kps.reload();
      setSelectedId(kp.id);
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCreateBusy(false);
    }
  }

  const openCreate = () => {
    setCreateCode("");
    setCreateName("");
    setCreateChapter("");
    setCreateDesc("");
    setCreateImportance("核心");
    setCreateErr(null);
    setCreateOpen(true);
  };

  const toolbarErr =
    forkErr ?? importErr ?? exportErr ?? null;

  return (
    <Page accent={ACCENTS.knowledge}>
      <Link
        to={kbBackTo}
        className="mb-4 inline-flex items-center gap-1 text-sm text-ink-soft transition-colors hover:text-accent"
      >
        ← {kbBackTo === "/admin/kb" ? "返回知识库总览" : "返回首页"}
      </Link>
      <PageHeader
        title="知识库"
        desc={
          activeVersion
            ? `${activeVersion.subject} · ${activeVersion.textbook_edition} · v${activeVersion.version}（${versionStatusLabel(activeVersion.status)}）`
            : undefined
        }
        actions={
          <>
            {editable && (
              <Button variant="primary" onClick={openCreate}>
                <Plus size={15} /> 补充知识点
              </Button>
            )}
            <VersionMenu
              versions={subjectVersions}
              currentVersionId={currentVersionId}
              currentIsActive={currentIs?.is_active ?? false}
              canActivate={canActivate}
              editable={editable}
              anyBusy={forkBusy || switchBusy || importBusy}
              onPick={(id) => {
                setVersionId(id);
                setSelectedId(null);
              }}
              onImport={() => importFileRef.current?.click()}
              onExport={doExport}
              onFork={doFork}
              onActivate={startSwitch}
              onCreate={() => {
                setBackTarget("/kb/new", "/kb");
                nav("/kb/new");
              }}
            />
          </>
        }
      />
      {/* 学科切换 chips：多学科治理的入口（版本/激活都在学科内） */}
      {versions.data && subjects.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2" role="tablist" aria-label="学科">
          {subjects.map((s) => (
            <button
              key={s}
              role="tab"
              aria-selected={s === subject}
              onClick={() => switchSubject(s)}
              className={`rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ${
                s === subject
                  ? "border-accent/30 bg-accent-soft text-accent-deep"
                  : "border-line bg-surface text-ink-soft hover:border-accent/40 hover:text-accent"
              }`}
            >
              {s}
              <span className="ml-1.5 text-xs font-normal text-ink-faint">
                {allVersions.filter((v) => v.subject === s).length}
              </span>
            </button>
          ))}
        </div>
      )}
      {/* 导入走隐藏 input（键盘可达，同向导 P0-3 修法） */}
      <input
        ref={importFileRef}
        type="file"
        id="kb-import-file"
        name="kb-import-file"
        aria-label="导入知识库 YAML 文件"
        accept=".yaml,.yml"
        className="sr-only"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) doImport(f);
          e.target.value = "";
        }}
      />
      {toolbarErr && <p className="mb-3 text-xs text-danger">{toolbarErr}</p>}

      {currentVersionId && (
        <Tabs
          ariaLabel="知识库视图"
          layoutId="kb-view"
          className="mb-4"
          tabs={[{ key: "list", label: "目录" }, { key: "graph", label: "关系图" }] as const}
          value={view}
          onChange={setView}
        />
      )}

      {versions.error && <ErrorState message={versions.error} onRetry={versions.reload} />}
      {versions.loading && <Skeleton rows={3} />}
      {versions.data && versions.data.versions.length === 0 && (
        <Card>
          <EmptyState title="尚未导入知识库" hint="可通过「⋯ 版本与治理 → 导入 YAML」导入，或使用初始化向导。" />
        </Card>
      )}

      {view === "list" && kps.data && kps.data.kps.length > 0 && (
        <Reveal>
          {/* 搜索框（修 P2-5） */}
          <div className="relative mb-3">
            <MagnifyingGlass
              size={15}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
            />
            <input
              id="kb-search"
              name="kb-search"
              aria-label="搜索知识点"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索编码 / 名称 / 章节…"
              className="w-full rounded-md border border-line-strong bg-surface py-2 pl-9 pr-3 text-sm transition-colors focus:border-accent"
            />
          </div>
          <p className="mb-2 text-xs text-ink-faint">
            知识点（{chapters.reduce((n, [, ns]) => n + ns.length, 0)}）· 点开章节浏览，点击知识点看详情
            {!editable && " · 只读，由授权教师与管理员维护"}
          </p>
          <Card className="max-h-[65vh] divide-y divide-line overflow-y-auto px-3 py-1">
            <StaggerList>
              {chapters.map(([ch, nodes]) => {
                const open = isOpen(ch);
                return (
                  <StaggerItem key={ch} className="py-0.5">
                    <button
                      type="button"
                      onClick={() => toggleChapter(ch)}
                      aria-expanded={open}
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-2.5 text-left transition-colors hover:bg-surface-2"
                    >
                      {open ? (
                        <CaretDown size={13} className="shrink-0 text-ink-faint" />
                      ) : (
                        <CaretRight size={13} className="shrink-0 text-ink-faint" />
                      )}
                      <span className="text-sm font-semibold text-ink">{ch}</span>
                      <span className="text-xs text-ink-faint">（{nodes.length}）</span>
                    </button>
                    {open && (
                      <div className="space-y-0.5 pb-2 pl-6">
                        {nodes.map((k) => (
                          <button
                            key={k.id}
                            onClick={() => setSelectedId(k.id)}
                            className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition-colors hover:bg-surface-2 ${
                              k.archived ? "opacity-50" : ""
                            }`}
                          >
                            <span className="font-mono text-xs text-ink-faint">{k.code}</span>
                            <span className="min-w-0 flex-1 truncate">{k.name}</span>
                            {k.code.startsWith("C") && <Badge tone="neutral">分类节点</Badge>}
                            {k.archived && <Badge tone="warn">已停用</Badge>}
                          </button>
                        ))}
                      </div>
                    )}
                  </StaggerItem>
                );
              })}
            </StaggerList>
          </Card>
        </Reveal>
      )}
      {view === "list" && kps.data && kps.data.kps.length > 0 && chapters.length === 0 && (
        <Card>
          <EmptyState title="没有匹配的知识点" hint="换个关键词，或清空搜索查看全部章节。" />
        </Card>
      )}

      {view === "graph" && currentVersionId && (
        <section className="space-y-3">
          {relations.loading && <Skeleton rows={5} />}
          {relations.error && <ErrorState message={relations.error} onRetry={relations.reload} />}
          {relations.data && graphNodes.length > 0 && (
            <Suspense fallback={<Card className="p-4"><Skeleton rows={5} /></Card>}>
              <KnowledgeGraph2D
                nodes={graphNodes}
                edges={graphEdges}
                selectedId={selectedId}
                onSelect={(node) => setSelectedId(node?.id ?? null)}
              />
            </Suspense>
          )}
          {relations.data && graphNodes.length === 0 && (
            <Card><EmptyState title="当前版本没有可展示的知识点" hint="请先在目录视图补充知识点。" /></Card>
          )}
        </section>
      )}

      {/* 详情浮卡：阅读态默认，编辑态经「编辑知识点」进入 */}
      <Modal
        open={selectedId !== null}
        onClose={() => setSelectedId(null)}
        title={detail.data?.name ?? "知识点详情"}
        size="lg"
      >
        {detail.loading && <Skeleton rows={4} />}
        {detail.error && <ErrorState message={detail.error} onRetry={detail.reload} />}
        {detail.data && (
          <KpDetailEditor
            detail={detail.data}
            kps={kps.data?.kps ?? []}
            onReload={() => {
              detail.reload();
              kps.reload();
            }}
            onSelect={setSelectedId}
            readOnly={!editable}
          />
        )}
      </Modal>

      {/* 补充知识点：高频入口（编码/名称必填，grade 沿用本版本现有知识点） */}
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="补充知识点"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)} disabled={createBusy}>
              取消
            </Button>
            <Button variant="primary" onClick={doCreate} disabled={createBusy}>
              {createBusy ? "创建中…" : "创建"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="编码（唯一标识，建议沿用教材编号风格）">
            <Input
              value={createCode}
              onChange={(e) => setCreateCode(e.target.value)}
              placeholder="如 M7A-5xx"
            />
          </Field>
          <Field label="名称">
            <Input
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              placeholder="如 相交线模型（猪蹄模型）"
            />
          </Field>
          <Field label="章节">
            <Input
              value={createChapter}
              onChange={(e) => setCreateChapter(e.target.value)}
              placeholder="如 第五章 相交线与平行线"
            />
          </Field>
          <Field label="描述（这个点讲什么、学到什么程度）" hint="诊断与报告会引用这段文字">
            <textarea
              value={createDesc}
              onChange={(e) => setCreateDesc(e.target.value)}
              rows={3}
              className="w-full rounded-md border border-line-strong bg-surface px-3 py-2 text-sm transition-colors focus:border-accent"
            />
          </Field>
          <Field label="重要度">
            <Select
              value={createImportance}
              onChange={(e) => setCreateImportance(e.target.value)}
              className="w-full"
            >
              <option value="基础">基础（地基性，优先补强）</option>
              <option value="核心">核心（章节主干）</option>
              <option value="拓展">拓展（独立/高阶）</option>
            </Select>
          </Field>
          {createErr && <p className="text-xs text-danger">{createErr}</p>}
        </div>
      </Modal>

      {/* 切换确认模态：统一 Modal（焦点圈定 + Esc，修 P0-2） */}
      <Modal
        open={switchComp !== null}
        onClose={() => {
          setSwitchComp(null);
          setSwitchErr(null);
        }}
        title="启用本版（全校）"
        size="lg"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setSwitchComp(null);
                setSwitchErr(null);
              }}
              disabled={switchBusy}
            >
              取消
            </Button>
            <Button variant="primary" onClick={confirmSwitch} disabled={switchBusy}>
              确认启用
            </Button>
          </>
        }
      >
        {switchComp && (
          <div className="space-y-3 text-sm">
            <p className="text-xs text-ink-faint">
              目标版本 #{switchComp.target_version_id} ← 当前 active #
              {switchComp.active_version_id}
            </p>

            {switchComp.missing_codes.length > 0 && (
              <div className="rounded-lg border border-danger/25 bg-danger-soft p-3 text-xs">
                <p className="font-semibold text-danger">
                  缺失 {switchComp.missing_codes.length} 个知识点
                </p>
                <p className="mt-1 text-ink-soft">
                  旧依据会从分析消失：{switchComp.missing_codes.join("、")}
                </p>
              </div>
            )}

            {switchComp.attribute_changes.length > 0 && (
              <div className="rounded-lg border border-warn/25 bg-warn-soft p-3 text-xs">
                <p className="font-semibold text-warn">
                  {switchComp.attribute_changes.length} 个知识点关键参数变化
                </p>
                <ul className="mt-1 space-y-0.5 text-ink-soft">
                  {switchComp.attribute_changes.map((c, i) => (
                    <li key={i}>
                      {c.code} · {c.field}：{String(c.old)} → {String(c.new)}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {switchComp.missing_codes.length === 0 &&
              switchComp.attribute_changes.length === 0 && (
                <p className="text-ink-soft">无缺失、无参数变化，可直接切换。</p>
              )}

            <p className="text-xs font-medium text-danger">
              ⚠ 切换后新产生的考试依据无法迁回旧版本。
            </p>
            {switchErr && <p className="text-xs text-danger">{switchErr}</p>}
          </div>
        )}
      </Modal>
    </Page>
  );
}

/** 「⋯ 版本与治理」菜单：版本切换 + 导入/导出/修订/启用——低频治理操作收纳处。 */
function VersionMenu({
  versions,
  currentVersionId,
  currentIsActive,
  canActivate,
  editable,
  anyBusy,
  onPick,
  onImport,
  onExport,
  onFork,
  onActivate,
  onCreate,
}: {
  versions: { id: number; version: string; status: string; is_active: boolean }[];
  currentVersionId: number | null;
  currentIsActive: boolean;
  canActivate: boolean;
  /** 内容层写权：导入/修订/新建是写操作，普通教师不可见（后端 require_kb_editor
   * 兜底）；导出与版本切换人人可用（教师可浏览与导出）。 */
  editable: boolean;
  anyBusy: boolean;
  onPick: (id: number) => void;
  onImport: () => void;
  onExport: () => void;
  onFork: () => void;
  onActivate: () => void;
  onCreate: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const itemCls =
    "flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm text-ink transition-colors hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40";

  return (
    <div className="relative" ref={ref}>
      <Button
        variant="secondary"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <DotsThree size={15} weight="bold" />
        版本与治理
      </Button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1.5 w-64 rounded-xl border border-line-strong bg-surface p-1.5 shadow-float"
        >
          <p className="px-2.5 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
            版本
          </p>
          {versions.map((v) => (
            <button
              key={v.id}
              role="menuitem"
              onClick={() => {
                onPick(v.id);
                setOpen(false);
              }}
              className={itemCls}
            >
              <span className="min-w-0 flex-1 truncate">
                v{v.version} · {versionStatusLabel(v.status)}
                {v.is_active ? "（当前启用）" : ""}
              </span>
              {v.id === currentVersionId && <Check size={13} className="shrink-0 text-accent" />}
            </button>
          ))}
          <div className="my-1 h-px bg-line" />
          {editable && (
            <>
              <button role="menuitem" onClick={() => { onCreate(); setOpen(false); }} className={itemCls}>
                <Plus size={14} className="text-ink-faint" /> 图形化新建知识库…
              </button>
              <button role="menuitem" onClick={() => { onImport(); setOpen(false); }} className={itemCls}>
                <Upload size={14} className="text-ink-faint" /> 导入 YAML（建新版本）
              </button>
            </>
          )}
          <button role="menuitem" onClick={() => { onExport(); setOpen(false); }} className={itemCls}>
            <DownloadSimple size={14} className="text-ink-faint" /> 导出当前版 YAML
          </button>
          {editable && (
            <button
              role="menuitem"
              onClick={() => { onFork(); setOpen(false); }}
              disabled={anyBusy}
              className={itemCls}
            >
              <Plus size={14} className="text-ink-faint" /> 基于当前版修订（复制为新版）
            </button>
          )}
          {canActivate && !currentIsActive && (
            <button
              role="menuitem"
              onClick={() => { onActivate(); setOpen(false); }}
              disabled={anyBusy}
              className={`${itemCls} font-medium text-accent-deep`}
            >
              <Check size={14} /> 启用本版（全校）
            </button>
          )}
        </div>
      )}
    </div>
  );
}
