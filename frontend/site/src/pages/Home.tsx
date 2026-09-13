import {
  ArrowRight,
  ArrowsClockwise,
  BookOpenText,
  ChartLineUp,
  Check,
  ChatsCircle,
  LockKey,
  Path,
  SealCheck,
  Student,
} from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { CTABand } from "../components/site/CTABand";
import { Container } from "../components/site/Container";
import { GraphCard } from "../components/site/GraphCard";
import { Reveal, Stagger, StaggerItem } from "../components/site/Reveal";
import { SectionHeading } from "../components/site/SectionHeading";
import { btnGhost, btnPrimary } from "../lib/buttons";
import { usePageMeta } from "../lib/page";

const WORKFLOW = [
  { n: "01", label: "看见问题", detail: "找到共性与差异" },
  { n: "02", label: "找准重点", detail: "决定先改什么" },
  { n: "03", label: "安排行动", detail: "让每个人有下一步" },
  { n: "04", label: "验证改变", detail: "把有效做法留下" },
] as const;

const CAPABILITIES = [
  {
    icon: ChartLineUp,
    eyebrow: "一场考试",
    title: "先看哪里真正值得讲",
    body: "把复杂结果整理成清晰重点，先处理影响最大的共性问题，再照顾每个学生的差异，备课和讲评更有依据。",
    link: "/#product",
  },
  {
    icon: BookOpenText,
    eyebrow: "一套教研经验",
    title: "把好方法留给下一届",
    body: "将课程重点、常见误区和成熟做法整理在一起，团队有共同语言，新老师也能快速接上。",
    link: "/#product",
  },
  {
    icon: Path,
    eyebrow: "一份行动清单",
    title: "从知道问题到安排行动",
    body: "为全班、小组和个人给出清楚的下一步建议，老师确认后即可分发，学生知道该练什么。",
    link: "/#solutions",
  },
  {
    icon: ArrowsClockwise,
    eyebrow: "一个完整闭环",
    title: "每次复测，都能看见改变",
    body: "记录学生完成与教师反馈，用后续表现验证教学调整，让有效方法留下来。",
    link: "/#solutions",
  },
] as const;

function ProductPreview() {
  return (
    <div className="relative rounded-[22px] border border-line bg-surface p-3 shadow-[0_32px_80px_-38px_rgba(13,42,39,.55)] sm:p-4">
      <div className="flex items-center justify-between border-b border-line px-2 pb-3">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-[#b33b31]/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-annot/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-primary/70" />
        </div>
        <span className="text-[10px] font-semibold tracking-[0.15em] text-ink-faint uppercase">一次教学复盘 · 示例</span>
      </div>
      <div className="grid gap-3 pt-3 sm:grid-cols-[1.1fr_.9fr]">
        <div className="rounded-2xl bg-night p-5 text-white">
          <p className="text-xs text-white/45">本周先关注</p>
          <p className="mt-2 font-display text-xl font-bold">函数的单调性与最值</p>
          <div className="mt-5 h-2 overflow-hidden rounded-full bg-white/10">
            <span className="block h-full w-[42%] rounded-full bg-annot" />
          </div>
          <div className="mt-2 flex items-center justify-between text-xs text-white/50">
            <span>班级掌握情况</span><b className="text-annot">需要关注</b>
          </div>
          <p className="mt-5 border-t border-white/10 pt-4 text-xs leading-relaxed text-white/60">
            多数同学在这部分卡住，建议先用一节课统一讲解，再给需要帮助的同学安排练习。
          </p>
        </div>
        <div className="space-y-3">
          {[
            ["全班重点", "优先讲解", "bg-primary"],
            ["小组辅导", "针对练习", "bg-[#356a8a]"],
            ["个别跟进", "持续支持", "bg-annot"],
          ].map(([label, value, color]) => (
            <div key={label} className="rounded-xl border border-line bg-paper px-4 py-3">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="flex items-center gap-2 text-ink-soft"><i className={`h-2 w-2 rounded-full ${color}`} />{label}</span>
                <b className="font-display text-ink">{value}</b>
              </div>
            </div>
          ))}
          <div className="rounded-xl border border-primary/20 bg-primary/8 px-4 py-3 text-xs leading-relaxed text-primary-deep">
            下次复习时，回看每项行动是否带来改善
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Home() {
  usePageMeta(
    "薄弱点分析 — 从一张试卷，到每个学生的提升路径",
    "把考试结果变成教学行动：看见问题、找准重点、安排跟进，再用后续表现验证改变。"
  );

  return (
    <>
      <section className="relative overflow-hidden border-b border-line py-16 sm:py-20 lg:py-24">
        <div className="paper-grid pointer-events-none absolute inset-0 opacity-65" aria-hidden />
        <Container className="relative grid items-center gap-12 lg:grid-cols-[.92fr_1.08fr] lg:gap-16">
          <Reveal>
            <p className="inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/8 px-3 py-1.5 text-xs font-semibold text-primary-deep">
              <SealCheck size={14} aria-hidden /> 看见问题 · 立即行动 · 持续改善
            </p>
            <h1 className="mt-6 max-w-[12ch] font-display text-[2.8rem] leading-[1.08] font-bold tracking-[-0.04em] text-ink text-balance sm:text-6xl">
              看清薄弱点，走完改进路
            </h1>
            <p className="mt-6 max-w-[54ch] text-base leading-8 text-ink-soft sm:text-lg">
              从一次考试出发，把结果变成清晰的教学重点，把重点变成每个人的下一步，再用后续表现确认改变。
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/#cta" className={btnPrimary}>预约演示 <ArrowRight size={16} aria-hidden /></Link>
              <Link to="/#product" className={btnGhost}>查看产品路径 <ArrowRight size={15} aria-hidden /></Link>
            </div>
            <p className="mt-7 flex items-center gap-2 text-xs text-ink-faint">
              <LockKey size={14} className="text-primary" aria-hidden /> 为学校日常教学而生 · 可从一个班级开始
            </p>
          </Reveal>
          <Reveal delay={0.08}><ProductPreview /></Reveal>
        </Container>
      </section>

      <section className="border-b border-line bg-surface/70">
        <Container>
          <ol className="grid divide-y divide-line py-2 sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-4 lg:divide-x">
            {WORKFLOW.map((step) => (
              <li key={step.n} className="flex items-center gap-3 px-4 py-5 first:pl-0 last:pr-0">
                <span className="font-display text-xs font-bold text-annot">{step.n}</span>
                <div><p className="text-sm font-bold text-ink">{step.label}</p><p className="text-xs text-ink-faint">{step.detail}</p></div>
              </li>
            ))}
          </ol>
        </Container>
      </section>

      <section id="product" className="scroll-mt-24 py-20 md:py-28">
        <Container>
          <SectionHeading kicker="不止看结果" title="诊断之后，真正重要的是下一步" lead="从看见问题到落实行动，帮助学校把改进做成日常，而不是停在一份报告里。" />
          <Stagger className="mt-12 grid gap-px overflow-hidden rounded-[20px] border border-line bg-line md:grid-cols-2">
            {CAPABILITIES.map(({ icon: Icon, eyebrow, title, body, link }) => (
              <StaggerItem key={title}>
                <Link to={link} className="group block h-full bg-surface p-6 transition-colors hover:bg-paper md:p-8">
                  <div className="flex items-start justify-between gap-4">
                    <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 text-primary"><Icon size={22} weight="duotone" aria-hidden /></span>
                    <ArrowRight size={16} className="text-ink-faint transition-transform group-hover:translate-x-1 group-hover:text-primary" aria-hidden />
                  </div>
                  <p className="mt-6 text-xs font-semibold tracking-[0.16em] text-annot uppercase">{eyebrow}</p>
                  <h2 className="mt-2 font-display text-xl font-bold tracking-tight text-ink">{title}</h2>
                  <p className="mt-3 max-w-[58ch] text-sm leading-7 text-ink-soft">{body}</p>
                </Link>
              </StaggerItem>
            ))}
          </Stagger>
        </Container>
      </section>

      <section className="bg-night py-20 text-white md:py-24">
        <Container className="grid items-center gap-12 lg:grid-cols-[.8fr_1.2fr]">
          <Reveal>
            <p className="text-xs font-semibold tracking-[0.2em] text-mint uppercase">从看见到改变</p>
            <h2 className="mt-3 font-display text-3xl leading-tight font-bold tracking-tight md:text-4xl">让每一份结果，都变成下一步</h2>
            <p className="mt-5 max-w-[48ch] text-base leading-8 text-white/60">把考试、课堂与学习行动连起来：老师快速找到重点，学生拿到具体建议，学校持续回顾哪些做法有效。</p>
            <ul className="mt-7 space-y-3 text-sm text-white/70">
              {["共性问题一眼看清", "教学重点有据可依", "学生行动清晰可执行"].map((item) => <li key={item} className="flex items-center gap-2"><Check size={15} className="text-mint" aria-hidden />{item}</li>)}
            </ul>
          </Reveal>
          <Reveal delay={0.08}><GraphCard /></Reveal>
        </Container>
      </section>

      <section id="solutions" className="scroll-mt-24 border-b border-line py-20 md:py-24">
        <Container className="grid gap-8 md:grid-cols-3">
          <Reveal className="md:col-span-3">
            <SectionHeading kicker="一起改进" title="从学校目标，到每个学生的下一步" lead="管理者看见变化，老师找到重点，学生拿到行动；每个人都围绕同一个改进目标协作。" />
          </Reveal>
          {[
            { icon: Student, title: "学校管理", body: "看见不同班级的变化，把资源投入最需要的地方。" },
            { icon: ChatsCircle, title: "教师协作", body: "让备课、讲评和跟进围绕同一个改进目标展开。" },
            { icon: LockKey, title: "学生成长", body: "知道自己哪里需要加强，也知道下一步从哪里开始。" },
          ].map(({ icon: Icon, title, body }) => (
            <Reveal key={title}>
              <div className="border-l-2 border-primary/25 pl-5">
                <Icon size={20} className="text-primary" aria-hidden />
                <h2 className="mt-4 font-display text-lg font-bold text-ink">{title}</h2>
                <p className="mt-2 text-sm leading-7 text-ink-soft">{body}</p>
              </div>
            </Reveal>
          ))}
        </Container>
      </section>

      <section id="security" className="scroll-mt-24 border-b border-line bg-surface py-16 md:py-20">
        <Container>
          <SectionHeading kicker="安心使用" title="让学校放心，也让老师专注教学" lead="成长记录由学校掌握，使用边界清晰；老师和学生可以把注意力放回真实的教学与学习。" />
          <div className="mt-10 grid gap-6 md:grid-cols-3">
            {[
              { icon: LockKey, title: "学校掌握", body: "每一份成长记录都服务于学校自己的教学判断。" },
              { icon: SealCheck, title: "边界清晰", body: "不同角色看与工作相关的重点，协作顺畅也保护隐私。" },
              { icon: ChatsCircle, title: "自然接上", body: "从一次考试开始，不必改变老师和学生熟悉的节奏。" },
            ].map(({ icon: Icon, title, body }) => (
              <Reveal key={title}>
                <div className="rounded-2xl border border-line bg-paper p-5">
                  <Icon size={21} className="text-primary" aria-hidden />
                  <h2 className="mt-4 font-display text-lg font-bold text-ink">{title}</h2>
                  <p className="mt-2 text-sm leading-7 text-ink-soft">{body}</p>
                </div>
              </Reveal>
            ))}
          </div>
          <div id="about" className="mt-12 border-t border-line pt-8 text-center">
            <p className="text-sm leading-7 text-ink-soft">我们相信，考试的意义不在排名，而在于让老师知道下一步讲什么，让学生知道下一步怎么学。</p>
          </div>
        </Container>
      </section>

      <CTABand />
    </>
  );
}
