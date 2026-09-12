/**
 * 全站唯一缓动令牌（design-style §5，saas-redesign §7）。
 * 独立成 lib 文件：组件文件只导出组件（fast-refresh 纪律），常量放这里。
 */
export const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];
