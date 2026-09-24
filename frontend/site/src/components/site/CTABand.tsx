import { CheckCircle } from "@phosphor-icons/react";
import { useState } from "react";
import type { FormEvent } from "react";
import { btnPrimary } from "../../lib/buttons";
import { SITE } from "../../lib/site";
import { Reveal } from "./Reveal";

const ROLES = ["校长 / 校领导", "教务 / 年级管理员", "教研员 / 教研组长", "班主任 / 任课老师", "其他"];
const STAGES = ["小学", "初中", "高中", "九年一贯制", "其他"];

const inputCls =
  "w-full border-2 border-ink bg-paper px-3 py-2.5 text-sm text-ink outline-none transition-[border-color,box-shadow] focus:border-primary";

/**
 * 预约演示（全站唯一转化动作）：三字段起，mailto 提交。
 * 邮箱为上线前必填项（lib/site.ts TODO）——提交前有校验提示。
 */
export function CTABand() {
  const [school, setSchool] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<string>(ROLES[0]);
  const [stage, setStage] = useState<string>(STAGES[1]);
  const [err, setErr] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!school.trim() || !name.trim()) {
      setErr("请填写学校名称与您的称呼");
      return;
    }
    setErr(null);
    const subject = `预约演示 — ${school.trim()}`;
    const body = `学校：${school.trim()}\n称呼：${name.trim()}\n角色：${role}\n学段：${stage}`;
    window.location.href = `mailto:${SITE.email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    setSent(true);
  }

  return (
    <section id="cta" className="scroll-mt-20 border-y-[3px] border-ink bg-night py-20 md:py-24">
      <div className="mx-auto grid w-full max-w-[1200px] items-center gap-10 px-4 md:px-6 lg:grid-cols-[1fr_1.1fr]">
        <Reveal>
          <p className="text-xs font-semibold tracking-[0.2em] text-bh-yellow uppercase">预约演示</p>
          <h2 className="mt-3 font-display text-3xl leading-tight font-bold tracking-tight text-white md:text-4xl">
            用一次真实的教学场景，
            <br />
            看问题如何变成行动。
          </h2>
          <p className="mt-4 max-w-[52ch] text-base leading-relaxed text-white/60">
            留下联系方式，我们安排线上或到校演示：结合你们最近的考试，展示如何找到重点、安排跟进并看见变化。
          </p>
          <ul className="mt-6 space-y-2.5 text-sm text-white/70">
            <li className="flex items-center gap-2">
              <CheckCircle size={16} className="shrink-0 text-bh-yellow" weight="fill" /> 演示约 40 分钟，含答疑
            </li>
            <li className="flex items-center gap-2">
              <CheckCircle size={16} className="shrink-0 text-bh-yellow" weight="fill" /> 可从一个年级或学科开始，逐步推广
            </li>
            <li className="flex items-center gap-2">
              <CheckCircle size={16} className="shrink-0 text-bh-yellow" weight="fill" /> 围绕学校真实场景，结果清晰可验证
            </li>
          </ul>
        </Reveal>

        <Reveal delay={0.1}>
          <form onSubmit={submit} noValidate className="border-2 border-ink bg-surface p-6 shadow-float md:p-7">
            <div className="grid gap-4 sm:grid-cols-2">
              <label htmlFor="demo-school" className="block">
                <span className="text-sm font-medium text-ink">学校名称</span>
                <input id="demo-school" name="school" required aria-required="true" aria-invalid={Boolean(err && !school.trim())} aria-describedby={err ? "demo-error" : undefined} autoComplete="organization" value={school} onChange={(e) => setSchool(e.target.value)} className={`mt-2 ${inputCls}`} placeholder="如：XX 市第一中学" />
              </label>
              <label htmlFor="demo-name" className="block">
                <span className="text-sm font-medium text-ink">您的称呼</span>
                <input id="demo-name" name="name" required aria-required="true" aria-invalid={Boolean(err && !name.trim())} aria-describedby={err ? "demo-error" : undefined} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} className={`mt-2 ${inputCls}`} placeholder="如：李老师" />
              </label>
              <label htmlFor="demo-role" className="block">
                <span className="text-sm font-medium text-ink">您的角色</span>
                <select id="demo-role" name="role" autoComplete="off" value={role} onChange={(e) => setRole(e.target.value)} className={`mt-2 ${inputCls}`}>
                  {ROLES.map((r) => <option key={r}>{r}</option>)}
                </select>
              </label>
              <label htmlFor="demo-stage" className="block">
                <span className="text-sm font-medium text-ink">学段</span>
                <select id="demo-stage" name="stage" autoComplete="off" value={stage} onChange={(e) => setStage(e.target.value)} className={`mt-2 ${inputCls}`}>
                  {STAGES.map((s) => <option key={s}>{s}</option>)}
                </select>
              </label>
            </div>
            {err && <p id="demo-error" role="alert" className="mt-3 border-2 border-red-700 bg-red-50 px-3 py-2 text-xs text-red-700">{err}</p>}
            {sent && (
              <p aria-live="polite" className="mt-3 text-xs text-primary-deep">
                已打开邮件客户端；若未弹出，请直接致邮 {SITE.email}
              </p>
            )}
            <button type="submit" className={`${btnPrimary} mt-5 w-full`}>
              提交预约
            </button>
            <p className="mt-3 text-center text-xs text-ink-faint">
              或直接致邮 <a className="underline underline-offset-4 hover:text-ink-soft" href={`mailto:${SITE.email}`}>{SITE.email}</a>
            </p>
          </form>
        </Reveal>
      </div>
    </section>
  );
}
