type BrandMarkProps = {
  size?: number;
  className?: string;
};

/** SC 的统一品牌标：官网、教师端和 favicon 直接使用同一张生成图。 */
export function BrandMark({ size = 36, className }: BrandMarkProps) {
  return <img aria-hidden="true" alt="" className={className} height={size} src="/logo-mark.png" width={size} />;
}
