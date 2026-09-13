import type { Transition } from "framer-motion";

/** 官网统一缓动（docs/site-redesign.md §3）：平滑减速，不弹跳。 */
export const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

export const revealT: Transition = { duration: 0.6, ease: EASE };
