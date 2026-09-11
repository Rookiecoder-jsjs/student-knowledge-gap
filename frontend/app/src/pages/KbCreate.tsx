import { CaretRight, Plus, Trash } from "@phosphor-icons/react";
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Button, Card, Field, Input, Page, PageHeader, StatusDot } from "../components/ui";
import { createKbVersion, createKp } from "../lib/api";
import { getBackTarget, roleFlags, setBackTarget } from "../lib/portal";
import { useAuth } from "../lib/AuthContext";
import { ACCENTS } from "../lib/theme";

/**
 * 图形化新建知识库向导（2026-09-11，frontend-ends-design §知识库）。
 *
 * 四步：基本信息（学科/教材/版本号/年级）→ 章节规划（批量粘贴，一行一个）→
 * 知识点录入（逐章；支持批量粘贴「编码 名称 | 描述」）→ 确认创建（先建空白
 * 草稿版本，再逐条写入；失败可重试）。建完跳回 /kb 并落在新版本上。
 * 写权门槛与 /kb 一致（非 kb_content_editable 见只读提示；后端 require_kb_editor 兜底）。
 */

interface KpDraft {
  chapter: string;
  code: string;
  name: string;
  description: string;
  importance: string;
}

const STEPS = ["基本信息", "章节规划", "知识点录入", "确认创建"];

const COMMON_SUBJECTS = ["数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理", "道德与法治"];
const COMMON_EDITIONS = ["人教版", "北师大版", "苏教版", "沪教版", "浙教版", "外研版", "译林版"];

/** 批量粘贴行解析：「编码 名称」或「编码 名称 | 描述」（竖线后为描述）。 */
function parseKpLine(line: string, chapter: string): KpDraft | null {
  const [head, ...descParts] = line.split("|");
  const tokens = head.trim().split(/\s+/);
  if (tokens.length < 2 || !tokens[0] || !tokens[1]) return null;
  return {
    chapter,
    code: tokens[0],
    name: tokens.slice(1).join(" "),
    description: descParts.join("|").trim(),
    importance: "核心",
  };
}

function parseLines(text: string): string[] {
  return [...new Set(text.split("\n").map((l) => l.trim()).filter(Boolean))];
}

export default function KbCreate() {
  const editable = roleFlags(useAuth().session).kbContentEditable;
  const nav = useNavigate();
  // 总面板「新建」预填（rbac-scopes-design §6）：从 /admin/kb 带学科/年级进来；
  // 返回链走 sessionStorage 面包屑（getBackTarget——location.state 在本 app 不可靠）
  const prefill = (useLocation().state as { kbSubject?: string; kbGrade?: number } | null) ?? null;
  const backTo = getBackTarget("/kb/new", "/kb");
  const backLabel = backTo === "/admin/kb" ? "返回知识库总览" : "返回知识库";

  const [step, setStep] = useState(1);
  const [subject, setSubject] = useState(prefill?.kbSubject ?? "");
  const [edition, setEdition] = useState("人教版");
  const [version, setVersion] = useState("0.1.0");
  const [grade, setGrade] = useState(prefill?.kbGrade ?? 7);
  const [chapters, setChapters] = useState<string[]>([]);
  const [kps, setKps] = useState<KpDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [failed, setFailed] = useState<KpDraft[]>([]);
  const [createdId, setCreatedId] = useState<number | null>(null);

  // 步骤 3 的录入缓冲（批量粘贴框按章节记忆）
  const [pasteFor, setPasteFor] = useState<string | null>(null);
  const [pasteText, setPasteText] = useState("");
  const [newKp, setNewKp] = useState<{ chapter: string; code: string; name: string; description: string }>({
    chapter: "",
    code: "",
    name: "",
    description: "",
  });

  if (!editable) {
    return (
      <Page accent={ACCENTS.knowledge}>
        <Link to={backTo} className="mb-4 inline-flex items-center gap-1 text-sm text-ink-soft transition-colors hover:text-accent">
          ← {backLabel}
        </Link>
        <Card className="p-8 text-center text-sm text-ink-soft">
          知识库创建需要内容编辑权限（管理员或被授权的 kb_editor 教师）。
        </Card>
      </Page>
    );
  }

  const codeSeen = new Set<string>();
  const duplicateCodes = kps.filter((k) => {
    if (codeSeen.has(k.code)) return true;
    codeSeen.add(k.code);
    return false;
  });
  const invalidKps = kps.filter((k) => !k.code.trim() || !k.name.trim());

  function step1Ok() {
    return subject.trim().length > 0 && edition.trim().length > 0 && version.trim().length > 0;
  }
  function step3Problem(): string | null {
    if (kps.length === 0) return "至少录入一个知识点";
    if (invalidKps.length > 0) return `${invalidKps.length} 个知识点缺编码或名称`;
    if (duplicateCodes.length > 0) return `编码重复：${duplicateCodes.map((k) => k.code).join("、")}`;
    return null;
  }

  function addKpsFromText(chapter: string, text: string) {
    const parsed = parseLines(text)
      .map((l) => parseKpLine(l, chapter))
      .filter((k): k is KpDraft => k !== null);
    setKps((ks) => [...ks, ...parsed.filter((p) => !ks.some((k) => k.code === p.code))]);
  }

  async function doCreate(list: KpDraft[]) {
    setBusy(true);
    setErr(null);
    try {
      let kbId = createdId;
      if (kbId == null) {
        const r = await createKbVersion({
          subject: subject.trim(),
          grade,
          textbook_edition: edition.trim(),
          version: version.trim(),
        });
        kbId = r.id;
        setCreatedId(r.id);
      }
      const failedLocal: KpDraft[] = [];
      let done = 0;
      for (const kp of list) {
        try {
          await createKp({
            code: kp.code,
            name: kp.name,
            grade,
            chapter: kp.chapter,
            description: kp.description || undefined,
            importance: kp.importance,
            kb_version_id: kbId,
          });
        } catch {
          failedLocal.push(kp);
        }
        done += 1;
        setProgress({ done, total: list.length });
      }
      setFailed(failedLocal);
      if (failedLocal.length === 0 && kbId != null) {
        // 落在工作台并链式保留返回来源；从 /kb 进来的清掉（避免返回指向自身）
        setBackTarget("/kb", backTo === "/kb" ? null : backTo);
        nav("/kb", { state: { kbVersionId: kbId } });
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page accent={ACCENTS.knowledge}>
      <Link
        to={backTo}
        className="mb-4 inline-flex items-center gap-1 text-sm text-ink-soft transition-colors hover:text-accent"
      >
        ← {backLabel}
      </Link>
      <PageHeader title="图形化新建知识库" desc="不用写 YAML：填基本信息 → 规划章节 → 录入知识点 → 一键创建草稿版本" />

      {/* 步骤指示 */}
      <div className="mb-5 flex flex-wrap items-center gap-4">
        {STEPS.map((label, i) => {
          const n = i + 1;
          return (
            <button
              key={label}
              onClick={() => n < step && setStep(n)}
              disabled={n > step}
              className={`flex items-center gap-1.5 text-sm ${n === step ? "font-semibold text-ink" : n < step ? "text-ink-soft hover:text-accent" : "text-ink-faint"}`}
            >
              <StatusDot state={n < step ? "done" : n === step ? "active" : "todo"} />
              {label}
            </button>
          );
        })}
      </div>

      {err && <p className="mb-3 text-xs text-danger">{err}</p>}

      {/* 步骤 1：基本信息 */}
      {step === 1 && (
        <Card className="max-w-xl space-y-4 p-6">
          <Field label="学科" hint="同一学科内做版本治理；分析按班级学科取对应知识库">
            <Input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              list="common-subjects"
              placeholder="如 数学"
            />
            <datalist id="common-subjects">
              {COMMON_SUBJECTS.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </Field>
          <Field label="教材版本">
            <Input
              value={edition}
              onChange={(e) => setEdition(e.target.value)}
              list="common-editions"
            />
            <datalist id="common-editions">
              {COMMON_EDITIONS.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="版本号" hint="草稿，之后可启用">
              <Input value={version} onChange={(e) => setVersion(e.target.value)} />
            </Field>
            <Field label="年级（数字，如 7 = 初一）">
              <Input
                type="number"
                value={grade}
                onChange={(e) => setGrade(Number(e.target.value))}
              />
            </Field>
          </div>
          <div className="flex justify-end">
            <Button variant="primary" disabled={!step1Ok()} onClick={() => setStep(2)}>
              下一步：章节规划
            </Button>
          </div>
        </Card>
      )}

      {/* 步骤 2：章节规划 */}
      {step === 2 && (
        <Card className="max-w-xl space-y-4 p-6">
          <Field label="批量粘贴章节（一行一个）" hint="也可在下方逐个添加；顺序即教材目录顺序">
            <textarea
              rows={6}
              value={chapters.join("\n")}
              onChange={(e) => setChapters(parseLines(e.target.value))}
              placeholder={"第一章 有理数\n第二章 整式的加减\n第三章 一元一次方程"}
              className="w-full rounded-md border border-line-strong bg-surface px-3 py-2 text-sm transition-colors focus:border-accent"
            />
          </Field>
          <p className="text-xs text-ink-faint">已规划 {chapters.length} 章</p>
          <div className="flex justify-between">
            <Button variant="ghost" onClick={() => setStep(1)}>
              上一步
            </Button>
            <Button
              variant="primary"
              disabled={chapters.length === 0}
              onClick={() => setStep(3)}
            >
              下一步：知识点录入
            </Button>
          </div>
        </Card>
      )}

      {/* 步骤 3：知识点录入（逐章） */}
      {step === 3 && (
        <div className="space-y-4">
          {step3Problem() && (
            <p className="text-xs text-warn">{step3Problem()}</p>
          )}
          {chapters.map((ch) => {
            const list = kps.filter((k) => k.chapter === ch);
            return (
              <Card key={ch} className="p-5">
                <div className="mb-2 flex items-center justify-between">
                  <p className="text-sm font-semibold text-ink">
                    {ch}
                    <span className="ml-2 text-xs font-normal text-ink-faint">（{list.length}）</span>
                  </p>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setPasteFor(pasteFor === ch ? null : ch);
                      setPasteText("");
                    }}
                  >
                    <CaretRight size={13} />
                    批量粘贴
                  </Button>
                </div>
                {pasteFor === ch && (
                  <div className="mb-3 space-y-2 rounded-lg bg-surface-2/60 p-3">
                    <textarea
                      rows={5}
                      value={pasteText}
                      onChange={(e) => setPasteText(e.target.value)}
                      placeholder={"一行一个：编码 名称 | 描述（描述可省）\nM7A-501 对顶角 | 两直线相交形成的相对的角\nM7A-502 邻补角"}
                      className="w-full rounded-md border border-line-strong bg-surface px-3 py-2 font-mono text-xs transition-colors focus:border-accent"
                    />
                    <div className="flex justify-end">
                      <Button
                        variant="secondary"
                        onClick={() => {
                          addKpsFromText(ch, pasteText);
                          setPasteText("");
                          setPasteFor(null);
                        }}
                        disabled={!pasteText.trim()}
                      >
                        解析并加入
                      </Button>
                    </div>
                  </div>
                )}
                {list.length > 0 && (
                  <ul className="mb-3 space-y-1">
                    {list.map((k) => (
                      <li
                        key={k.code}
                        className="flex items-center gap-2 rounded-lg bg-surface px-2.5 py-1.5 text-sm"
                      >
                        <span className="font-mono text-xs text-ink-faint">{k.code}</span>
                        <span className="min-w-0 flex-1 truncate">{k.name}</span>
                        {k.description && (
                          <span className="hidden max-w-[24rem] truncate text-xs text-ink-faint md:inline">
                            {k.description}
                          </span>
                        )}
                        <button
                          onClick={() => setKps((ks) => ks.filter((x) => x.code !== k.code))}
                          className="text-ink-faint transition-colors hover:text-danger"
                          aria-label={`移除 ${k.name}`}
                        >
                          <Trash size={13} />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    value={newKp.chapter === ch ? newKp.code : ""}
                    onChange={(e) => setNewKp({ chapter: ch, code: e.target.value, name: "", description: "" })}
                    placeholder="编码"
                    className="w-32"
                  />
                  <Input
                    value={newKp.chapter === ch ? newKp.name : ""}
                    onChange={(e) => setNewKp((p) => ({ ...p, name: e.target.value }))}
                    placeholder="名称"
                    className="w-48"
                  />
                  <Button
                    variant="secondary"
                    onClick={() => {
                      if (!newKp.code.trim() || !newKp.name.trim()) return;
                      if (kps.some((k) => k.code === newKp.code.trim())) return;
                      setKps((ks) => [
                        ...ks,
                        {
                          chapter: ch,
                          code: newKp.code.trim(),
                          name: newKp.name.trim(),
                          description: newKp.description.trim(),
                          importance: "核心",
                        },
                      ]);
                      setNewKp({ chapter: ch, code: "", name: "", description: "" });
                    }}
                  >
                    <Plus size={14} /> 添加
                  </Button>
                </div>
              </Card>
            );
          })}
          <div className="flex justify-between">
            <Button variant="ghost" onClick={() => setStep(2)}>
              上一步
            </Button>
            <Button variant="primary" disabled={kps.length === 0} onClick={() => setStep(4)}>
              下一步：确认创建
            </Button>
          </div>
        </div>
      )}

      {/* 步骤 4：确认创建 */}
      {step === 4 && (
        <Card className="max-w-2xl space-y-4 p-6">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
            <p className="text-ink-faint">
              学科<span className="ml-2 font-medium text-ink">{subject}</span>
            </p>
            <p className="text-ink-faint">
              教材<span className="ml-2 font-medium text-ink">{edition}</span>
            </p>
            <p className="text-ink-faint">
              版本号<span className="ml-2 font-medium text-ink tabular-nums">v{version}（草稿）</span>
            </p>
            <p className="text-ink-faint">
              年级<span className="ml-2 font-medium text-ink tabular-nums">{grade}</span>
            </p>
            <p className="text-ink-faint">
              章节<span className="ml-2 font-medium text-ink tabular-nums">{chapters.length}</span>
            </p>
            <p className="text-ink-faint">
              知识点<span className="ml-2 font-medium text-ink tabular-nums">{kps.length}</span>
            </p>
          </div>
          <p className="text-xs text-ink-faint">
            创建后即为本学科的一个草稿版本：可在知识库页继续精修，教研确认后「启用本版（全校）」。
          </p>
          {progress && (
            <p className="text-xs text-accent-deep">
              写入中… {progress.done}/{progress.total}
              {failed.length > 0 && `（失败 ${failed.length}）`}
            </p>
          )}
          {failed.length > 0 && (
            <div className="rounded-lg border border-warn/25 bg-warn-soft p-3 text-xs">
              <p className="font-semibold text-warn">
                {failed.length} 个知识点写入失败（编码冲突或校验拒绝）
              </p>
              <p className="mt-1 text-ink-soft">{failed.map((k) => k.code).join("、")}</p>
              <div className="mt-2">
                <Button variant="secondary" disabled={busy} onClick={() => doCreate(failed)}>
                  重试失败项
                </Button>
              </div>
            </div>
          )}
          <div className="flex justify-between">
            <Button variant="ghost" onClick={() => setStep(3)} disabled={busy}>
              上一步
            </Button>
            <Button
              variant="primary"
              disabled={busy || step3Problem() !== null}
              onClick={() => doCreate(kps)}
            >
              {busy ? "创建中…" : `创建（${kps.length} 个知识点）`}
            </Button>
          </div>
        </Card>
      )}
    </Page>
  );
}
