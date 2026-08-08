import { ArrowsLeftRight, DownloadSimple, MagnifyingGlass, Plus } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { useMemo, useState } from "react";
import { KpDetailEditor } from "../components/KpDetailEditor";
import { Badge, Button, Card, EmptyState, ErrorState, Modal, PageHeader, Skeleton } from "../components/ui";
import { Reveal, StaggerItem, StaggerList } from "../components/motion";
import {
  exportKbUrl,
  forkKbVersion,
  kbCompatibility,
  kpDetail,
  listKbVersions,
  listKps,
  patchKbVersion,
  type KbCompatibility,
  type KpDetail,
  type KpNode,
} from "../lib/api";
import { useAsync } from "../lib/hooks";
import { versionStatusLabel } from "../lib/labels";

/** 知识库浏览与编辑页（全局，不绑班级；kb-edit §4.1/§7.2）。 */
export default function Kb() {
  const versions = useAsync(() => listKbVersions(), []);
  const [versionId, setVersionId] = useState<number | null>(null);
  const activeVersion =
    versions.data?.versions.find((v) => v.is_active) ?? versions.data?.versions[0];
  const currentVersionId = versionId ?? activeVersion?.id ?? null;
  const currentIs =
    versions.data?.versions.find((v) => v.id === currentVersionId) ?? undefined;

  const kps = useAsync(
    () =>
      currentVersionId
        ? listKps(currentVersionId)
        : Promise.resolve({ kb_version_id: 0, kps: [] as KpNode[] }),
    [currentVersionId]
  );

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const detail = useAsync(
    () => (selectedId ? kpDetail(selectedId) : Promise.resolve<KpDetail | null>(null)),
    [selectedId]
  );

  // 搜索过滤（修 P2-5）
  const [query, setQuery] = useState("");

  // 版本管理状态
  const [switchComp, setSwitchComp] = useState<KbCompatibility | null>(null);
  const [switchBusy, setSwitchBusy] = useState(false);
  const [switchErr, setSwitchErr] = useState<string | null>(null);
  const [forkBusy, setForkBusy] = useState(false);
  const [forkErr, setForkErr] = useState<string | null>(null);

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

  async function doFork() {
    setForkBusy(true);
    setForkErr(null);
    try {
      await forkKbVersion();
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

  return (
    <div className="mx-auto min-h-[100dvh] max-w-[1200px] px-6 py-10">
      <Link
        to="/"
        className="mb-4 inline-flex items-center gap-1 text-sm text-ink-soft transition-colors hover:text-accent"
      >
        ← 返回首页
      </Link>
      <PageHeader
        title="知识库管理"
        desc={
          activeVersion
            ? `${activeVersion.subject} · ${activeVersion.textbook_edition} · v${activeVersion.version}（${versionStatusLabel(activeVersion.status)}）`
            : undefined
        }
        actions={
          versions.data && versions.data.versions.length > 1 ? (
            <select
              value={currentVersionId ?? ""}
              onChange={(e) => {
                setVersionId(Number(e.target.value));
                setSelectedId(null);
              }}
              className="rounded-xl border border-line bg-surface px-3 py-2 text-sm transition-colors focus:border-accent"
              aria-label="切换版本"
            >
              {versions.data.versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version} · {versionStatusLabel(v.status)}
                  {v.is_active ? "（当前）" : ""}
                </option>
              ))}
            </select>
          ) : undefined
        }
      />

      {/* 版本工具栏 */}
      {versions.data && currentVersionId && (
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={doFork} disabled={forkBusy}>
            <Plus size={15} /> 复制为新版本
          </Button>
          <Button
            variant="secondary"
            onClick={() => window.open(exportKbUrl(currentVersionId), "_blank")}
          >
            <DownloadSimple size={15} /> 导出 YAML
          </Button>
          {currentIs && !currentIs.is_active && (
            <Button variant="primary" onClick={startSwitch} disabled={switchBusy}>
              <ArrowsLeftRight size={15} /> 设为正式版
            </Button>
          )}
          {forkErr && <span className="text-xs text-danger">{forkErr}</span>}
        </div>
      )}

      {versions.error && <ErrorState message={versions.error} onRetry={versions.reload} />}
      {versions.loading && <Skeleton rows={3} />}
      {versions.data && versions.data.versions.length === 0 && (
        <Card>
          <EmptyState title="尚未导入知识库" hint="请通过初始化向导导入知识库 YAML。" />
        </Card>
      )}

      {kps.data && kps.data.kps.length > 0 && (
        <Reveal className="grid gap-6 lg:grid-cols-[1fr_1.4fr]">
          <section>
            {/* 搜索框（修 P2-5） */}
            <div className="relative mb-3">
              <MagnifyingGlass
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
              />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索编码 / 名称 / 章节…"
                className="w-full rounded-xl border border-line bg-surface py-2 pl-9 pr-3 text-sm transition-colors focus:border-accent"
              />
            </div>
            <h2 className="mb-3 text-sm font-semibold text-ink-soft">
              知识点（{chapters.reduce((n, [, ns]) => n + ns.length, 0)}）
            </h2>
            <Card className="divide-y divide-line">
              <StaggerList>
              {chapters.map(([ch, nodes]) => (
                <StaggerItem key={ch} className="px-3 py-2.5">
                  <p className="px-1 pb-1 text-xs font-semibold text-ink-faint">{ch}</p>
                  <div className="space-y-0.5">
                    {nodes.map((k) => (
                      <button
                        key={k.id}
                        onClick={() => setSelectedId(k.id)}
                        className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition-colors ${
                          selectedId === k.id
                            ? "bg-accent-soft text-accent-deep"
                            : "hover:bg-surface-2"
                        } ${k.archived ? "opacity-50" : ""}`}
                      >
                        <span className="font-mono text-xs text-ink-faint">{k.code}</span>
                        <span className="min-w-0 flex-1 truncate">{k.name}</span>
                        {k.code.startsWith("C") && <Badge tone="neutral">分类节点</Badge>}
                        {k.archived && <Badge tone="warn">已停用</Badge>}
                      </button>
                    ))}
                  </div>
                </StaggerItem>
              ))}
              </StaggerList>
            </Card>
          </section>

          <section>
            {!selectedId && (
              <Card className="p-6">
                <EmptyState
                  title="选择左侧知识点查看与编辑"
                  hint="属性、前置链、后继与包含关系；可改属性、归档、增删关系。"
                />
              </Card>
            )}
            {selectedId && detail.loading && <Skeleton rows={4} />}
            {selectedId && detail.error && (
              <ErrorState message={detail.error} onRetry={detail.reload} />
            )}
            {selectedId && detail.data && (
              <KpDetailEditor
                detail={detail.data}
                kps={kps.data?.kps ?? []}
                onReload={detail.reload}
                onSelect={setSelectedId}
              />
            )}
          </section>
        </Reveal>
      )}

      {/* 切换确认模态：统一 Modal（焦点圈定 + Esc，修 P0-2） */}
      <Modal
        open={switchComp !== null}
        onClose={() => {
          setSwitchComp(null);
          setSwitchErr(null);
        }}
        title="设为正式版"
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
              确认切换
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
              <div className="rounded-xl border border-danger/25 bg-danger-soft p-3 text-xs">
                <p className="font-semibold text-danger">
                  缺失 {switchComp.missing_codes.length} 个知识点
                </p>
                <p className="mt-1 text-ink-soft">
                  旧依据会从分析消失：{switchComp.missing_codes.join("、")}
                </p>
              </div>
            )}

            {switchComp.attribute_changes.length > 0 && (
              <div className="rounded-xl border border-warn/25 bg-warn-soft p-3 text-xs">
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
    </div>
  );
}
