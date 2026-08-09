import { motion, useReducedMotion, type Variants } from "framer-motion";
import { Children, memo, type ReactNode } from "react";

/** 利落减速曲线（案头 ease-out，比绿洲更干脆）。 */
const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

/** 大列表降级阈值：超过则不再逐项 stagger，改为整体淡入，避免长列表 2s+ 迟滞。 */
const STAGGER_CAP = 16;

/**
 * 克制动效组件（案头，利落）。
 * 仅动画 opacity / transform（GPU 友好）；reduced-motion 下直接渲染静态内容。
 * 全部 React.memo 隔离，避免父组件重渲时重放进场动画。
 */

/** 列表/网格容器：子项依次进场（stagger）；>16 项降级为整体淡入。 */
export const StaggerList = memo(function StaggerList({
  children,
  className = "",
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;
  const count = Children.count(children);
  // 大列表：整体淡入，不逐项 stagger
  if (count > STAGGER_CAP) {
    return (
      <motion.div
        className={className}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.4, ease: EASE, delay }}
      >
        {children}
      </motion.div>
    );
  }
  return (
    <motion.div
      className={className}
      initial="hidden"
      animate="show"
      variants={{
        hidden: {},
        show: { transition: { staggerChildren: 0.04, delayChildren: delay } },
      }}
    >
      {children}
    </motion.div>
  );
});

/** StaggerList 的子项：fade + 微上移。 */
export const StaggerItem = memo(function StaggerItem({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;
  const variants: Variants = {
    hidden: { opacity: 0, y: 12 },
    show: { opacity: 1, y: 0, transition: { duration: 0.36, ease: EASE } },
  };
  return (
    <motion.div className={className} variants={variants}>
      {children}
    </motion.div>
  );
});

/** section 进场：进入视口时 fade + slide（once，不重复触发）。 */
export const Reveal = memo(function Reveal({
  children,
  className = "",
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 10 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-40px" }}
      transition={{ duration: 0.4, ease: EASE, delay }}
    >
      {children}
    </motion.div>
  );
});
