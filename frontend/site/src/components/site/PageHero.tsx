import { Container } from "./Container";
import { Reveal } from "./Reveal";

/** 内页统一页头：kicker + 大标题 + 引言。 */
export function PageHero({
  kicker,
  title,
  lead,
}: {
  kicker: string;
  title: string;
  lead?: string;
}) {
  return (
    <section className="relative overflow-hidden border-b-[3px] border-ink bg-primary py-16 text-white md:py-20">
      <div className="paper-grid pointer-events-none absolute inset-0 opacity-15" aria-hidden />
      <span className="pointer-events-none absolute right-[9%] top-8 h-20 w-20 rounded-full bg-bh-yellow" aria-hidden />
      <span className="pointer-events-none absolute right-[19%] top-24 h-12 w-12 bg-bh-blue" aria-hidden />
      <Container className="relative">
        <Reveal>
          <p className="text-xs font-semibold tracking-[0.2em] text-bh-yellow uppercase">{kicker}</p>
          <h1 className="mt-3 max-w-[22ch] font-display text-3xl leading-tight font-bold tracking-[-0.03em] text-white text-balance md:text-5xl">
            {title}
          </h1>
          {lead && <p className="mt-5 max-w-[62ch] text-base leading-8 text-white/80 md:text-lg">{lead}</p>}
        </Reveal>
      </Container>
    </section>
  );
}
