import { Camera, Sparkle, Table, WarningCircle } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Card, Field, Input, PageHeader } from "../components/ui";
import { createExam, listKps, photoTemplate, suggestQuestionTags } from "../lib/api";
import type { QuestionCreate } from "../lib/types";

const TYPES = ["单元", "期中", "期末", "练习", "补录", "诊断"];

/** 新建考试：拍照解析（推荐）或手工建卷。 */
export default function ExamNew() {
  const { classId } = useParams();
  const cid = Number(classId);
  const nav = useNavigate();
  const [tab, setTab] = useState<"photo" | "manual">("photo");
  const fileRef = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [type, setType] = useState("单元");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  // 拍照
  const [file, setFile] = useState<File | null>(null);

  // 手工建卷（5 列：题号|题型|满分|题干|知识点；题干/知识点可空，兼容旧 4 列）
  const [rows, setRows] = useState(
    "1|选择|5|下列关于绝对值的说法正确的是|M7A-105\n2|解答|10|计算：(-3)+5|M7A-111"
  );

  const submitPhoto = async () => {
    if (!file) return setError("请先选择试卷照片");
    if (!name.trim()) return setError("请填写考试名称");
    setBusy(true);
    setError(null);
    try {
      const r = await photoTemplate(file, {
        class_id: cid,
        name: name.trim(),
        exam_date: date,
        type,
      });
      if (r.warnings.length > 0) {
        setWarnings(r.warnings);
        // 停留展示警告，由教师确认后进入审核台
        setTimeout(() => nav(`/c/${cid}/exams/${r.exam_id}/review`), 2600);
      } else {
        nav(`/c/${cid}/exams/${r.exam_id}/review`);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const recommendTags = async () => {
    const items: { idx: number; stem: string; q_type: string }[] = [];
    for (const line of rows.split("\n")) {
      const t = line.trim();
      if (!t) continue;
      const p = t.split("|").map((s) => s?.trim());
      const idx = Number(p[0]);
      const stem = p.length >= 5 ? p[3] ?? "" : "";
      if (idx && stem) items.push({ idx, stem, q_type: p[1] || "解答" });
    }
    if (items.length === 0)
      return setError("没有含题干的题目可推荐（5 列格式：题号|题型|满分|题干|知识点）");
    setBusy(true);
    setError(null);
    try {
      const r = await suggestQuestionTags(items);
      const byIdx = new Map(r.suggestions.map((s) => [s.idx, s.kps]));
      const newRows = rows.split("\n").map((line) => {
        const t = line.trim();
        if (!t) return line;
        const p = t.split("|").map((s) => s?.trim());
        const idx = Number(p[0]);
        const kps = byIdx.get(idx);
        if (!kps || kps.length === 0) return line;
        const kpStr = kps.map((k) => k.code).join(" ");
        const stem = p.length >= 5 ? p[3] ?? "" : "";
        return `${p[0]}|${p[1] || "解答"}|${p[2] || "10"}|${stem}|${kpStr}`;
      });
      setRows(newRows.join("\n"));
      setWarnings(r.warnings);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submitManual = async () => {
    if (!name.trim()) return setError("请填写考试名称");
    const questions: QuestionCreate[] = [];
    for (const line of rows.split("\n")) {
      const t = line.trim();
      if (!t) continue;
      const p = t.split("|").map((s) => s?.trim());
      const idx = p[0];
      const qType = p[1];
      const score = p[2];
      if (!idx || !score) throw new Error(`行格式错误：${t}`);
      const stem = p.length >= 5 ? p[3] ?? "" : "";
      const kpsStr = p.length >= 5 ? p[4] ?? "" : p[3] ?? "";
      questions.push({
        idx: Number(idx),
        stem,
        q_type: qType || "解答",
        full_score: Number(score),
        kps: kpsStr
          .split(/[,，\s]+/)
          .filter(Boolean)
          .map((code) => ({ code, weight: 1.0 })),
      });
    }
    if (questions.length === 0) return setError("请至少添加一道题");
    setBusy(true);
    setError(null);
    try {
      const kb = await listKps();
      const r = await createExam({
        kb_version_id: kb.kb_version_id,
        class_id: cid,
        name: name.trim(),
        exam_date: date,
        type,
        questions,
      });
      nav(`/c/${cid}/exams/${r.exam_id}/collect`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const tabs: { key: "photo" | "manual"; label: string; Icon: typeof Camera }[] = [
    { key: "photo", label: "拍照解析（推荐）", Icon: Camera },
    { key: "manual", label: "手工建卷", Icon: Table },
  ];

  return (
    <div>
      <PageHeader title="新建考试" desc="拍照路径由 AI 解析题目并初步标注知识点，建卷后需在审核台确认" />

      {/* 分段控件：tab 语义（修 P1-2） */}
      <div
        className="mb-5 inline-flex gap-1 rounded-xl border border-line bg-surface p-1"
        role="tablist"
        aria-label="录入方式"
      >
        {tabs.map(({ key, label, Icon }) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
              tab === key ? "bg-accent-soft text-accent-deep" : "text-ink-soft hover:text-ink"
            }`}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
      </div>

      {warnings.length > 0 && (
        <div className="mb-4 rounded-xl border border-warn/20 bg-warn-soft p-4" role="status">
          <p className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-warn">
            <WarningCircle size={16} />
            解析警告（即将进入审核台）
          </p>
          <ul className="list-disc pl-5 text-sm text-warn">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {error && (
        <div className="mb-4 rounded-xl border border-danger/20 bg-danger-soft px-4 py-3 text-sm text-danger" role="alert">
          {error}
        </div>
      )}

      <Card className="max-w-[720px] space-y-5 p-7">
        <div className="grid grid-cols-[1.5fr_1fr_1fr] gap-4">
          <Field label="考试名称">
            <Input placeholder="如：10月月考" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="考试日期">
            <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label="类型">
            <select
              value={type}
              onChange={(e) => setType(e.target.value)}
              className="rounded-xl border border-line bg-surface px-3 py-2.5 text-sm transition-colors focus:border-accent"
            >
              {TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </Field>
        </div>

        {tab === "photo" ? (
          <div role="tabpanel" className="space-y-4">
            {/* 可见按钮触发可聚焦隐藏 input（键盘可达，修 P0-3） */}
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="flex w-full cursor-pointer flex-col items-center gap-2 rounded-2xl border border-dashed border-line bg-surface-2/60 px-6 py-10 text-center transition-colors hover:border-accent/50 hover:bg-surface-2"
            >
              <span className="flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft">
                <Camera size={24} className="text-accent" weight="thin" />
              </span>
              <span className="text-sm font-medium">{file ? file.name : "上传空白试卷照片"}</span>
              <span className="text-xs text-ink-faint">
                上传前会自动遮盖姓名栏；解析约需十几秒
              </span>
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="sr-only"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <Button onClick={submitPhoto} disabled={busy || !file} className="w-full justify-center">
              {busy ? "AI 解析中，请稍候…" : "开始解析"}
            </Button>
          </div>
        ) : (
          <div role="tabpanel" className="space-y-4">
            <Field label="题目（每行一题：题号|题型|满分|题干|知识点编码，多个编码用空格分隔；题干/知识点可空）">
              <textarea
                rows={8}
                value={rows}
                onChange={(e) => setRows(e.target.value)}
                className="rounded-xl border border-line bg-surface px-3.5 py-2.5 font-mono text-sm transition-colors focus:border-accent"
              />
            </Field>
            <div className="flex items-center gap-2 text-xs text-ink-faint">
              <Sparkle size={14} />
              填了题干但没标知识点的题，可一键让 AI 从知识库中推荐，审核后再建卷。
            </div>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={recommendTags} disabled={busy} className="justify-center">
                <Sparkle size={15} />
                AI 推荐标注
              </Button>
              <Button onClick={submitManual} disabled={busy} className="flex-1 justify-center">
                {busy ? "创建中…" : "创建考试模板"}
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
