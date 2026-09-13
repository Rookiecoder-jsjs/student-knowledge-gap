import { motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";
import { revealT } from "../../lib/motion";

/** 滚动浮现（动效档 4-5）：reduced-motion 下自动退化为纯淡入。 */
export function Reveal({
  children,
  delay = 0,
  className,
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  const reduced = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: reduced ? 0 : 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ ...revealT, delay }}
    >
      {children}
    </motion.div>
  );
}

/** 列表级联：70ms 步进。 */
export function Stagger({
  children,
  className,
  step = 0.07,
}: {
  children: ReactNode;
  className?: string;
  step?: number;
}) {
  return (
    <motion.div
      className={className}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: "-60px" }}
      variants={{ hidden: {}, show: { transition: { staggerChildren: step } } }}
    >
      {children}
    </motion.div>
  );
}

export function StaggerItem({ children, className }: { children: ReactNode; className?: string }) {
  const reduced = useReducedMotion();
  return (
    <motion.div
      className={className}
      variants={{
        hidden: { opacity: 0, y: reduced ? 0 : 18 },
        show: { opacity: 1, y: 0, transition: revealT },
      }}
    >
      {children}
    </motion.div>
  );
}
