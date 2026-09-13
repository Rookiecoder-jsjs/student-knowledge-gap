import { Reveal } from "./Reveal";

/** 区段标题：kicker（墨青小标）+ 大标题 + 可选引言。 */
export function SectionHeading({
  kicker,
  title,
  lead,
  dark = false,
  center = false,
}: {
  kicker: string;
  title: string;
  lead?: string;
  dark?: boolean;
  center?: boolean;
}) {
  return (
    <Reveal className={center ? "text-center" : ""}>
      <p className={`text-xs font-semibold tracking-[0.2em] uppercase ${dark ? "text-mint" : "text-primary"}`}>
        {kicker}
      </p>
      <h2
        className={`mt-3 font-display text-3xl leading-tight font-bold tracking-tight text-balance md:text-[2.6rem] ${
          dark ? "text-white" : "text-ink"
        }`}
      >
        {title}
      </h2>
      {lead && (
        <p className={`mt-4 max-w-[65ch] text-base leading-relaxed ${dark ? "text-white/60" : "text-ink-soft"} ${center ? "mx-auto" : ""}`}>
          {lead}
        </p>
      )}
    </Reveal>
  );
}
