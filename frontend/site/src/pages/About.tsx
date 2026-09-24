import { ArrowRight, ChatsCircle, Desktop, GraduationCap } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { btnPrimary } from "../lib/buttons";
import { CTABand } from "../components/site/CTABand";
import { Container } from "../components/site/Container";
import { PageHero } from "../components/site/PageHero";
import { Reveal } from "../components/site/Reveal";
import { SectionHeading } from "../components/site/SectionHeading";
import { usePageMeta } from "../lib/page";
import { SITE } from "../lib/site";

/** 三端一体面板（示意化，替代产品截图位）。 */
const ENDS = [
  { icon: Desktop, t: "学校管理", d: "看见全校变化，找到最值得投入的地方" },
  { icon: ChatsCircle, t: "教师协作", d: "围绕共同重点备课、讲评与跟进" },
  { icon: GraduationCap, t: "学生行动", d: "知道自己要加强什么，从下一步开始" },
] as const;

const BELIEFS = [
  {
    t: "考试的意义不在排名，在于下一步",
    d: "一场考试的价值，是让学生知道自己下一步改什么、让老师知道下一步讲什么。排名只是副产品，改进才是目的。",
  },
  {
    t: "改进要融入每天的教学",
    d: "不替老师做决定，只把需要的重点放到顺手的位置：哪里先讲、谁需要跟进、下一次如何验证，都有清晰依据。",
  },
  {
    t: "每个学生都应该被看见",
    d: "学生的成长记录属于学校，也属于每一次真实的教学判断。让老师看见差异，让学生获得适合自己的支持。",
  },
] as const;

export function AboutSection() {
  return (
    <div id="about" className="scroll-mt-24">
      <section className="py-20 md:py-24">
        <Container>
          <SectionHeading kicker="产品理念" title="我们相信三件事" />
          <div className="mt-12 space-y-10">
            {BELIEFS.map((b, i) => (
              <Reveal key={b.t}>
                <div className="grid gap-3 border-l-2 border-primary/25 pl-5 md:grid-cols-[16rem_1fr] md:gap-8 md:pl-8">
                  <h2 className="font-display text-xl leading-snug font-bold tracking-tight text-ink text-balance">
                    <span className="mr-2 font-display text-annot">{String(i + 1).padStart(2, "0")}</span>
                    {b.t}
                  </h2>
                  <p className="max-w-[62ch] text-base leading-relaxed text-ink-soft">{b.d}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </Container>
      </section>

      <section className="border-t border-line bg-surface py-20 md:py-24">
        <Container className="grid items-center gap-10 lg:grid-cols-[1fr_1.2fr]">
          <Reveal>
            <SectionHeading
              kicker="产品形态"
              title="从管理到课堂，每个人都能接上"
              lead="学校管理看变化，老师定重点，学生做行动；每个角色都能从同一份结果出发，把改进接力下去。"
            />
            <div className="mt-6 flex flex-wrap gap-3">
              <a href="#cta" className={btnPrimary}>
                预约演示 <ArrowRight size={15} />
              </a>
              <Link
                to="/#product"
                className="inline-flex items-center gap-1 px-2 py-2.5 text-sm font-medium text-primary underline-offset-4 hover:underline"
              >
                先看产品 <ArrowRight size={14} />
              </Link>
            </div>
          </Reveal>
          <Reveal delay={0.1}>
            <div className="border-2 border-ink bg-surface p-6 shadow-lift">
              <p className="text-xs font-semibold tracking-[0.2em] text-primary uppercase">一起把改变做起来</p>
              <ul className="mt-4 divide-y divide-line">
                {ENDS.map((e) => (
                  <li key={e.t} className="flex items-start gap-3 py-3.5">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center border-2 border-ink bg-primary text-white shadow-soft">
                      <e.icon size={20} weight="bold" />
                    </span>
                    <div>
                      <p className="font-display text-sm font-bold text-ink">{e.t}</p>
                      <p className="mt-0.5 text-sm leading-relaxed text-ink-soft">{e.d}</p>
                    </div>
                  </li>
                ))}
              </ul>
              <p className="mt-3 border-t border-line pt-3 text-xs text-ink-faint">
                从全校目标到个人行动，每一步都有清楚的承接
              </p>
            </div>
          </Reveal>
        </Container>
      </section>

    </div>
  );
}

export default function About() {
  usePageMeta(
    "关于 — 知行教研 · 教学质量分析平台",
    "知行教研的产品理念：考试为了改进、改进融入教学、每个学生都被看见。"
  );

  return (
    <>
      <PageHero
        kicker="关于"
        title="让每一次考试，都产生改进"
        lead={`${SITE.name}帮助学校把考试结果变成教学行动：管理者看见变化，老师找到重点，学生从清晰的下一步开始。`}
      />
      <AboutSection />
      <CTABand />
    </>
  );
}
