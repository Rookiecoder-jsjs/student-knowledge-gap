import { memo, type ReactNode } from "react";

/**
 * 包豪斯静帧（方案 3 · motion=still）：进场动画整体退役。
 * 保留组件签名（StaggerList/StaggerItem/Reveal 与 delay 属性），内部渲染静态容器，
 * 调用方零改动；reduced-motion 语义天然满足。
 */

/** 列表/网格容器：静态直出（原 stagger 容器）。 */
export const StaggerList = memo(function StaggerList({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  return <div className={className}>{children}</div>;
});

/** StaggerList 的子项：静态直出（原 fade+上移子项）。 */
export const StaggerItem = memo(function StaggerItem({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={className}>{children}</div>;
});

/** section 进场：静态直出（原 whileInView 进场）。 */
export const Reveal = memo(function Reveal({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  return <div className={className}>{children}</div>;
});
