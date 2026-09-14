import { CheckCircle, ChatsCircle, Desktop, GraduationCap, SquaresFour, UsersThree } from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import { CTABand } from "../components/site/CTABand";
import { Container } from "../components/site/Container";
import { PageHero } from "../components/site/PageHero";
import { Reveal, Stagger, StaggerItem } from "../components/site/Reveal";
import { SectionHeading } from "../components/site/SectionHeading";
import { usePageMeta } from "../lib/page";

interface Role {
  icon: Icon;
  who: string;
  scene: string;
  points: string[];
  tools: string;
}

const ROLES: Role[] = [
  {
    icon: SquaresFour,
    who: "校长 / 校领导",
    scene: "随时看见各班的学习状态与变化，把资源投入最需要的地方，不必等到学期末才发现问题。",
    points: ["全校重点班级一目了然", "变化趋势有迹可循", "围绕问题安排支持"],
    tools: "全校概览 · 质量追踪",
  },
  {
    icon: UsersThree,
    who: "教务 / 年级管理员",
    scene: "把每场考试变成一次教学复盘，重点、进度和后续安排清清楚楚，教务工作更从容。",
    points: ["组织考试更省心", "结果快速转成教学重点", "跨班比较找到共性问题"],
    tools: "考试组织 · 质量复盘",
  },
  {
    icon: ChatsCircle,
    who: "教研员 / 教研组长",
    scene: "把团队的好方法沉淀为可共享的教学资产，教研讨论有共同依据，新老师也能快速接上。",
    points: ["共建学科重点与易错点", "新老教师更容易对齐", "教研讨论有共同依据"],
    tools: "教研共创 · 经验沉淀",
  },
  {
    icon: GraduationCap,
    who: "班主任 / 任课老师",
    scene: "让每个学生知道下一步做什么，也让老师知道该如何跟进；改变不再靠记忆和催促。",
    points: ["班级行动优先级清晰", "任务分到小组和个人", "完成情况有回应、有跟进"],
    tools: "班级行动 · 学生跟进",
  },
];

export function SolutionsSection() {
  return (
    <section id="solutions" className="scroll-mt-24 py-20 md:py-24">
      <Container>
        <SectionHeading
          kicker="方案"
          title="同一份结果，连接每个关键角色"
          lead="从管理决策到学生行动，每个人都围绕同一个改进目标协作，不再各自整理、各自判断。"
        />
        <div className="mt-12">
        <Stagger className="space-y-6">
            {ROLES.map((r) => (
              <StaggerItem key={r.who}>
                <article className="rounded-2xl border border-line bg-surface p-6 md:p-8">
                  <div className="flex items-center gap-3">
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                      <r.icon size={22} weight="duotone" />
                    </span>
                    <h2 className="font-display text-xl font-bold tracking-tight text-ink">{r.who}</h2>
                    <span className="ml-auto hidden rounded-full bg-paper px-3 py-1 text-xs text-ink-faint md:inline">
                      {r.tools}
                    </span>
                  </div>
                  <p className="mt-4 max-w-[70ch] text-base leading-relaxed text-ink-soft">{r.scene}</p>
                  <ul className="mt-5 grid gap-x-8 gap-y-2.5 md:grid-cols-3">
                    {r.points.map((p) => (
                      <li key={p} className="flex items-start gap-2 text-sm text-ink">
                        <CheckCircle size={15} weight="fill" className="mt-0.5 shrink-0 text-primary" />
                        {p}
                      </li>
                    ))}
                  </ul>
                </article>
              </StaggerItem>
            ))}
        </Stagger>
        <Reveal className="mt-10 text-center">
            <p className="inline-flex items-center gap-2 text-sm text-ink-faint">
              <Desktop size={16} className="text-primary" />
              从管理决策到学生行动，所有人围绕同一个改进目标协作
            </p>
          </Reveal>
        </div>
      </Container>
    </section>
  );
}

export default function Solutions() {
  usePageMeta(
    "方案 — 按角色使用知行教研",
    "为校长、教务、教研员和班主任提供清晰的决策重点，让教学改进从全校目标落到每个学生。"
  );

  return (
    <>
      <PageHero
        kicker="方案"
        title="同一份结果，连接每个关键角色"
        lead="从管理决策到学生行动，每个人都围绕同一个改进目标协作，不再各自整理、各自判断。"
      />
      <SolutionsSection />
      <CTABand />
    </>
  );
}
