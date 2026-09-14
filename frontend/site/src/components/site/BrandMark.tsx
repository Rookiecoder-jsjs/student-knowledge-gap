type BrandMarkProps = {
  size?: number;
  className?: string;
};

/** 官网与教师端直接共用同一张生成品牌图。 */
export function BrandMark({ size = 36, className }: BrandMarkProps) {
  return <img aria-hidden="true" alt="" className={className} height={size} src="/logo-mark.png" width={size} />;
}
