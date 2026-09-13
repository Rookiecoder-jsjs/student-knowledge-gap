import { ArrowsClockwise, ChatsCircle, Crosshair, Graph } from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { CTABand } from "../components/site/CTABand";
import { Container } from "../components/site/Container";
import { FeaturePanel } from "../components/site/FeaturePanel";
import { PageHero } from "../components/site/PageHero";
import { Reveal, Stagger, StaggerItem } from "../components/site/Reveal";
import { SectionHeading } from "../components/site/SectionHeading";
import { usePageMeta } from "../lib/page";

const RESULTS = [
  { label: "看见问题", detail: "班级与个人重点清晰" },
  { label: "找准重点", detail: "知道先从哪里开始" },
  { label: "安排行动", detail: "每个人都有下一步" },
  { label: "验证改变", detail: "有效做法能够留下" },
] as const;

interface CapBlock {
  icon: Icon;
  kicker: string;
  title: string;
  body: string;
  panelTitle: string;
  points: string[];
  flip?: boolean;
}

const BLOCKS: CapBlock[] = [
  {
    icon: Crosshair,
    kicker: "看见问题",
    title: "一场考试，先找到最值得讲的地方",
    body: "把复杂结果整理成清晰重点，先处理影响最大的共性问题，再照顾每个学生的差异，备课和讲评更有依据。",
    panelTitle: "老师得到什么",
    points: [
      "一眼看清班级共性问题",
      "知道哪些内容应优先讲解",
      "看到每位学生需要的支持",
      "讲评与备课有同一份依据",
    ],
  },
  {
    icon: Graph,
    kicker: "沉淀经验",
    title: "把好方法留在学校，也留给下一届",
    body: "将课程重点、常见误区和成熟做法整理在一起，团队有共同语言，新老师也能快速接上，让每次教研都留下积累。",
    panelTitle: "团队因此更顺畅",
    points: [
      "经验可复用，不必每次从零开始",
      "新老师快速了解学生难点",
      "跨班协作更容易对齐",
      "好的做法能够持续积累",
    ],
    flip: true,
  },
  {
    icon: ArrowsClockwise,
    kicker: "安排行动",
    title: "知道问题之后，马上知道下一步",
    body: "把改进建议按班级、小组和个人分层，老师确认后分发，学生拿到清楚、可执行的任务，改变从课堂延伸到课后。",
    panelTitle: "行动更容易发生",
    points: [
      "班级重点明确",
      "小组与个人任务清晰",
      "学生知道先做什么",
      "老师可随时查看进度",
    ],
  },
  {
    icon: ChatsCircle,
    kicker: "验证改变",
    title: "下一次考试，告诉你哪些做法有效",
    body: "把学生完成情况和后续表现放在一起回看，及时调整教学，把有效的改变变成可持续的节奏。",
    panelTitle: "每轮复盘都有收获",
    points: [
      "完成情况有人跟进",
      "复测结果及时对照",
      "有效做法沉淀下来",
      "下一轮行动更有把握",
    ],
    flip: true,
  },
];

const COMPARE = [
  ["备课重点", "翻卷子 + 凭经验", "一眼找到最值得讲的共性问题"],
  ["讲评课", "从头讲到尾", "围绕高频难点，把时间花在关键处"],
  ["课后跟进", "发完练习就结束", "每个人都有清楚的下一步"],
  ["复测", "只看分数涨跌", "知道哪些做法真正有效"],
  ["教研协作", "经验散落在个人手里", "共同沉淀，下一次直接复用"],
] as const;

export function ProductSection() {
  return (
    <div id="product" className="scroll-mt-24">
      <section className="border-b border-line py-16 md:py-20">
        <Container>
          <SectionHeading
            kicker="产品"
            title="把考试结果，变成教学改变"
            lead="从看见问题到验证改变，帮助学校把每一次考试都变成下一步更有把握的教学行动。"
          />
        </Container>
      </section>

      {/* 价值结果带：表达用户会得到的改变，不编造成效数据。 */}
      <section className="border-b border-line py-12">
        <Container>
          <Stagger className="grid grid-cols-2 gap-8 md:grid-cols-4">
            {RESULTS.map((result, index) => (
              <StaggerItem key={result.label}>
                <p className="font-display text-sm font-bold text-annot">{String(index + 1).padStart(2, "0")}</p>
                <p className="mt-2 font-display text-xl font-bold text-primary">{result.label}</p>
                <p className="mt-1.5 text-sm text-ink-soft">{result.detail}</p>
              </StaggerItem>
            ))}
          </Stagger>
        </Container>
      </section>

      {/* 四个关键结果，左右交替（价值说明 + 结果面板）。 */}
      <section className="space-y-20 py-20 md:space-y-28 md:py-28">
        {BLOCKS.map((b, i) => (
          <Container key={b.kicker}>
            <div className={`grid items-center gap-10 lg:grid-cols-2 ${b.flip ? "lg:[&>*:first-child]:order-2" : ""}`}>
              <Reveal>
                <p className="font-display text-sm font-bold text-annot">{String(i + 1).padStart(2, "0")}</p>
                <p className="mt-2 text-xs font-semibold tracking-[0.2em] text-primary uppercase">{b.kicker}</p>
                <h2 className="mt-3 font-display text-2xl leading-tight font-bold tracking-tight text-ink text-balance md:text-4xl">
                  {b.title}
                </h2>
                <p className="mt-4 text-base leading-relaxed text-ink-soft">{b.body}</p>
              </Reveal>
              <Reveal delay={0.1}>
                <FeaturePanel Icon={b.icon} title={b.panelTitle} points={b.points} />
              </Reveal>
            </div>
          </Container>
        ))}
      </section>

      {/* 常见做法 vs 更好的教学结果 */}
      <section className="border-y border-line bg-surface py-20 md:py-24">
        <Container className="max-w-[880px]">
          <SectionHeading center kicker="变化" title="从忙于整理，到专注改进" />
          <Reveal className="mt-10">
            <div className="overflow-x-auto rounded-2xl border border-line">
              <table className="w-full min-w-[560px] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-line bg-paper text-left">
                    <th className="px-5 py-3.5 font-medium text-ink-faint">环节</th>
                    <th className="px-5 py-3.5 font-medium text-ink-faint">常见做法</th>
                    <th className="px-5 py-3.5 font-medium text-primary">带来的改变</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {COMPARE.map(([k, a, b]) => (
                    <tr key={k}>
                      <td className="px-5 py-3.5 font-medium text-ink">{k}</td>
                      <td className="px-5 py-3.5 text-ink-faint">{a}</td>
                      <td className="px-5 py-3.5 text-ink-soft">{b}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Reveal>
          <Reveal className="mt-10 text-center">
            <Link to="/#solutions" className="text-sm font-medium text-primary underline-offset-4 hover:underline">
              看看不同角色如何把它用起来 →
            </Link>
          </Reveal>
        </Container>
      </section>
    </div>
  );
}

export default function Product() {
  usePageMeta(
    "产品 — 薄弱点分析 · 教学质量分析平台",
    "从发现教学重点到验证学习改变，帮助学校把每一次考试都变成更有把握的下一步。"
  );

  return (
    <>
      <PageHero
        kicker="产品"
        title="把考试结果，变成教学改变"
        lead="从看见问题到验证改变，帮助学校把每一次考试都变成下一步更有把握的教学行动。"
      />
      <ProductSection />
      <CTABand />
    </>
  );
}
