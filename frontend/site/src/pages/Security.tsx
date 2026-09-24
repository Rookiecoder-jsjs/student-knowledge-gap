import { Desktop, Lock, ShieldCheck, Users } from "@phosphor-icons/react";
import { CTABand } from "../components/site/CTABand";
import { Container } from "../components/site/Container";
import { PageHero } from "../components/site/PageHero";
import { Reveal, Stagger, StaggerItem } from "../components/site/Reveal";
import { SectionHeading } from "../components/site/SectionHeading";
import { usePageMeta } from "../lib/page";

const PILLARS = [
  {
    icon: Desktop,
    title: "学校掌握每一份记录",
    body: "成绩与学习记录由学校掌握，使用范围清晰，让教学判断建立在真实、完整的成长信息上。",
  },
  {
    icon: Lock,
    title: "安心的使用边界",
    body: "从日常查看到分享结果，每一步都围绕学校的管理要求设计，老师可以专注教学，学生可以安心学习。",
  },
  {
    icon: Users,
    title: "每个人看该看的",
    body: "不同角色看到与工作相关的内容，减少误读，也保护学生隐私，让协作更顺畅。",
  },
  {
    icon: ShieldCheck,
    title: "轻松开始，持续使用",
    body: "由学校统一安排使用方式，老师和学生无需额外注册或切换工具，日常教学可以自然接上。",
  },
] as const;

/** 使用关系示意：从学校目标到师生行动，再回到持续复盘。 */
function DeployDiagram() {
  return (
    <svg viewBox="0 0 560 300" className="w-full" role="img" aria-label="教学改进关系示意：学校目标连接教师重点与学生行动，持续复盘">
      <rect x="30" y="34" width="500" height="224" fill="#141414" />
      <text x="280" y="68" textAnchor="middle" fill="rgba(255,255,255,0.5)" fontSize="12">学校的教学现场</text>
      <rect x="62" y="100" width="124" height="72" fill="#d63a2f" stroke="#f2efe9" strokeWidth="2" />
      <text x="124" y="132" textAnchor="middle" fill="#ffffff" fontSize="14" fontWeight="700">学校目标</text>
      <text x="124" y="153" textAnchor="middle" fill="rgba(255,255,255,0.45)" fontSize="11">看见变化</text>
      <rect x="218" y="100" width="124" height="72" fill="#1f5fbf" stroke="#f2efe9" strokeWidth="2" />
      <text x="280" y="132" textAnchor="middle" fill="#ffffff" fontSize="14" fontWeight="700">教师重点</text>
      <text x="280" y="153" textAnchor="middle" fill="rgba(255,255,255,0.45)" fontSize="11">决定先改什么</text>
      <rect x="374" y="100" width="124" height="72" fill="#e2a93b" stroke="#f2efe9" strokeWidth="2" />
      <text x="436" y="132" textAnchor="middle" fill="#141414" fontSize="14" fontWeight="700">学生行动</text>
      <text x="436" y="153" textAnchor="middle" fill="rgba(255,255,255,0.45)" fontSize="11">从下一步开始</text>
      <path d="M186 136 H218 M342 136 H374" stroke="rgba(255,255,255,0.35)" strokeWidth="2" markerEnd="url(#arrow)" />
      <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="rgba(255,255,255,0.45)" /></marker></defs>
      <path d="M436 182 C436 218, 124 218, 124 182" fill="none" stroke="#d63a2f" strokeWidth="2" strokeDasharray="5 4" />
      <text x="280" y="242" textAnchor="middle" fill="rgba(255,255,255,0.55)" fontSize="11">下一轮复盘，让有效做法持续发生</text>
    </svg>
  );
}

export function SecuritySection() {
  return (
    <div id="security" className="scroll-mt-24">
      <section className="py-20 md:py-24">
        <Container>
          <SectionHeading
            kicker="安心使用"
            title="让学校放心，也让老师专注教学"
            lead="学生的成长记录属于学校，教学判断也应该留在真实的校园场景里。"
          />
          <div className="mt-12">
          <Stagger className="grid gap-5 md:grid-cols-2">
            {PILLARS.map((p) => (
              <StaggerItem key={p.title}>
                <article className="flex h-full gap-4 border-2 border-ink bg-surface p-6 shadow-soft">
                  <p.icon size={24} className="mt-0.5 shrink-0 text-primary" />
                  <div>
                    <h2 className="font-display text-lg font-bold tracking-tight text-ink">{p.title}</h2>
                    <p className="mt-2 text-sm leading-relaxed text-ink-soft">{p.body}</p>
                  </div>
                </article>
              </StaggerItem>
            ))}
          </Stagger>
          </div>
        </Container>
      </section>

      <section className="border-y border-line bg-surface py-20 md:py-24">
        <Container className="grid items-center gap-10 lg:grid-cols-[1fr_1.1fr]">
          <Reveal>
            <SectionHeading
              kicker="使用承诺"
              title="把安心留在每一次教学里"
              lead="学校看见目标，老师找到重点，学生拿到行动；每一轮结果都会回到下一次教学。"
            />
            <p className="mt-6 border-2 border-ink bg-paper p-4 text-sm leading-relaxed text-ink-soft shadow-soft">
              我们会结合学校的实际场景说明使用方式，让管理者、老师和学生都能从第一天顺利接上。
            </p>
          </Reveal>
          <Reveal delay={0.1} className="border-2 border-ink bg-paper p-4 shadow-lift md:p-6">
            <DeployDiagram />
          </Reveal>
        </Container>
      </section>

    </div>
  );
}

export default function Security() {
  usePageMeta(
    "安心使用 — 知行教研",
    "学校掌握成长记录，师生各得其所，让教学改进在真实校园场景里持续发生。"
  );

  return (
    <>
      <PageHero
        kicker="安心使用"
        title="让学校放心，也让老师专注教学"
        lead="学生的成长记录属于学校，教学判断也应该留在真实的校园场景里。"
      />
      <SecuritySection />
      <CTABand />
    </>
  );
}
