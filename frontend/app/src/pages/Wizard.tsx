import { ArrowLeft, ArrowRight, CheckCircle, Upload } from "@phosphor-icons/react";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, Field, Input, Page } from "../components/ui";
import {
  createClass,
  createSchool,
  kbImport,
  kbUpload,
  listKps,
  updateProgress,
} from "../lib/api";
import { ACCENTS } from "../lib/theme";
import type { KpNode } from "../lib/api";

const STEPS = ["导入知识库", "建班与名单", "教学进度"];

export default function Wizard() {
  const nav = useNavigate();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // step 2
  const [schoolName, setSchoolName] = useState("我的学校");
  const [className, setClassName] = useState("");
  const [grade, setGrade] = useState(7);
  const [roster, setRoster] = useState("");
  const [classId, setClassId] = useState<number | null>(null);
  // step 3
  const [kps, setKps] = useState<KpNode[]>([]);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [taughtAt, setTaughtAt] = useState(new Date().toISOString().slice(0, 10));

  const chapters = useMemo(() => {
    const m = new Map<string, KpNode[]>();
    for (const k of kps) {
      const key = k.chapter || "未分章";
      m.set(key, [...(m.get(key) ?? []), k]);
    }
    return [...m.entries()];
  }, [kps]);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const step1 = async (file: File | null, path: string) =>
    run(async () => {
      await (file ? kbUpload(file) : kbImport(path));
      setStep(1);
    });

  const step2 = async () =>
    run(async () => {
      const aliases = roster
        .split(/[\n,，、;；\s]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (!className.trim()) throw new Error("请填写班级名称");
      if (aliases.length === 0) throw new Error("请粘贴学生名单（每行一个姓名）");
      const school = await createSchool(schoolName.trim());
      const cls = await createClass(school.school_id, {
        name: className.trim(),
        grade,
        student_aliases: aliases,
      });
      setClassId(cls.class_id);
      const list = await listKps();
      setKps(list.kps);
      setStep(2);
    });

  const step3 = async () =>
    run(async () => {
      if (classId === null) return;
      if (checked.size > 0) await updateProgress(classId, [...checked], taughtAt);
      nav(`/c/${classId}`);
    });

  const toggle = (code: string) =>
    setChecked((s) => {
      const n = new Set(s);
      if (n.has(code)) n.delete(code);
      else n.add(code);
      return n;
    });

  const toggleChapter = (nodes: KpNode[]) =>
    setChecked((s) => {
      const n = new Set(s);
      const all = nodes.every((k) => n.has(k.code));
      nodes.forEach((k) => (all ? n.delete(k.code) : n.add(k.code)));
      return n;
    });

  return (
    <Page
      accent={ACCENTS.knowledge}
      className="mx-auto min-h-[100dvh] max-w-[760px] px-6 py-12"
    >
      <header className="mb-10">
        <h1 className="font-display text-3xl font-bold tracking-tight">初始化设置</h1>
        <div className="mt-5 flex items-center gap-2">
          {STEPS.map((s, i) => (
            <div key={s} className="flex items-center gap-2">
              <span
                className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold transition-colors ${
                  i < step
                    ? "bg-accent text-white"
                    : i === step
                      ? "bg-accent-soft text-accent-deep ring-2 ring-accent/30"
                      : "bg-surface-2 text-ink-faint"
                }`}
              >
                {i < step ? <CheckCircle size={14} weight="fill" /> : i + 1}
              </span>
              <span className={`text-sm ${i === step ? "font-semibold" : "text-ink-faint"}`}>
                {s}
              </span>
              {i < STEPS.length - 1 && <span className="mx-1 h-px w-8 bg-line" />}
            </div>
          ))}
        </div>
      </header>

      {error && (
        <div
          className="mb-4 rounded-lg border border-danger/20 bg-danger-soft px-4 py-3 text-sm text-danger"
          role="alert"
        >
          {error}
        </div>
      )}

      {step === 0 && (
        <Card className="p-7">
          <StepOne onSubmit={step1} busy={busy} />
        </Card>
      )}

      {step === 1 && (
        <Card className="space-y-5 p-7">
          <Field label="学校名称">
            <Input value={schoolName} onChange={(e) => setSchoolName(e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="班级名称">
              <Input
                placeholder="如：七(3)班"
                value={className}
                onChange={(e) => setClassName(e.target.value)}
              />
            </Field>
            <Field label="年级">
              <Input
                type="number"
                value={grade}
                onChange={(e) => setGrade(Number(e.target.value))}
              />
            </Field>
          </div>
          <Field label="学生名单（每行一个姓名，支持从 Excel 直接粘贴）">
            <textarea
              rows={8}
              value={roster}
              onChange={(e) => setRoster(e.target.value)}
              placeholder={"张三\n李四\n王五"}
              className="rounded-md border border-line-strong bg-surface px-3.5 py-2.5 text-sm transition-colors focus:border-accent"
            />
          </Field>
          <p className="text-xs leading-relaxed text-ink-faint">
            姓名仅用于匹配试卷，可用化名；系统不采集其他个人信息。
          </p>
          <div className="flex justify-between">
            <Button variant="ghost" onClick={() => setStep(0)}>
              <ArrowLeft size={15} />
              上一步
            </Button>
            <Button onClick={step2} disabled={busy}>
              下一步
              <ArrowRight size={15} />
            </Button>
          </div>
        </Card>
      )}

      {step === 2 && (
        <Card className="p-7">
          <div className="mb-5 flex items-center justify-between gap-4">
            <Field label="已教内容的教授日期">
              <Input type="date" value={taughtAt} onChange={(e) => setTaughtAt(e.target.value)} />
            </Field>
            <p className="max-w-[34ch] text-xs leading-relaxed text-ink-faint">
              勾选「已教」后，未教知识点只会显示为「未学到」，不会被误判为待加强。
            </p>
          </div>
          <div className="max-h-[46vh] space-y-3 overflow-y-auto pr-2">
            {chapters.map(([chap, nodes]) => (
              <div key={chap} className="rounded-md border border-line-strong p-4">
                <label className="flex items-center gap-2 text-sm font-semibold">
                  <input
                    type="checkbox"
                    className="accent-accent"
                    checked={nodes.every((k) => checked.has(k.code))}
                    onChange={() => toggleChapter(nodes)}
                  />
                  {chap}
                  <span className="font-normal text-ink-faint">（{nodes.length} 个知识点）</span>
                </label>
                <div className="mt-2.5 grid grid-cols-1 gap-x-4 gap-y-1.5 pl-6 sm:grid-cols-2">
                  {nodes.map((k) => (
                    <label key={k.code} className="flex items-center gap-2 text-sm text-ink-soft">
                      <input
                        type="checkbox"
                        className="accent-accent"
                        checked={checked.has(k.code)}
                        onChange={() => toggle(k.code)}
                      />
                      <span className="font-mono text-xs text-ink-faint">{k.code}</span>
                      {k.name}
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-6 flex justify-between">
            <Button variant="ghost" onClick={() => setStep(1)}>
              <ArrowLeft size={15} />
              上一步
            </Button>
            <Button onClick={step3} disabled={busy}>
              完成设置
              <CheckCircle size={15} />
            </Button>
          </div>
          <p className="mt-3 text-xs text-ink-faint">
            也可以先跳过（直接完成），之后再补教学进度。
          </p>
        </Card>
      )}
    </Page>
  );
}

function StepOne({
  onSubmit,
  busy,
}: {
  onSubmit: (file: File | null, path: string) => void;
  busy: boolean;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [path, setPath] = useState("kb/math/grade7/kb.yaml");
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm font-semibold">上传知识库文件（推荐）</p>
        {/* 可见按钮触发隐藏但可聚焦的 input（键盘可达，修 P0-3） */}
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          className="mt-2 flex w-full cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed border-line bg-surface-2/60 px-6 py-9 text-center transition-colors hover:border-accent/50 hover:bg-surface-2"
        >
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft">
            <Upload size={22} className="text-accent" weight="thin" />
          </span>
          <span className="text-sm text-ink-soft">
            {file ? file.name : "点击选择 kb.yaml（人教版七上已内置于后端仓库）"}
          </span>
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".yaml,.yml"
          className="sr-only"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      </div>
      <div className="flex items-center gap-3 text-xs text-ink-faint">
        <span className="h-px flex-1 bg-line" />
        或使用服务器上已有的文件
        <span className="h-px flex-1 bg-line" />
      </div>
      <Field label="服务器路径">
        <Input value={path} onChange={(e) => setPath(e.target.value)} />
      </Field>
      <Button
        onClick={() => onSubmit(file, path)}
        disabled={busy}
        className="w-full justify-center"
      >
        导入知识库
      </Button>
    </div>
  );
}
