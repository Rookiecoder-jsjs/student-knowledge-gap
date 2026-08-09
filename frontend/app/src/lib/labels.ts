/**
 * 枚举值 -> 口语标签映射（自动生成，勿手改）。
 * 单一真源：backend/app/labels_source.py；改动后运行
 *   cd backend && python -m scripts.gen_labels_ts
 * 对应后端：app/pipeline/attribution.py 的 ATTR_*、app/pipeline/weakness.py 的
 * TRAJ_* / weak_criterion、app/ingestion/photo.py 的 band、app/models.py 的 kb status。
 */

const ATTR_LABEL: Record<string, string> = {
  "前置缺陷": "基础没打牢",
  "遗忘衰减": "学过但忘了",
  "数据不足": "数据不足",
  "易混淆": "概念混淆",
};

const TRAJ_LABEL: Record<string, string> = {
  "稳定": "稳定",
  "上升": "上升",
  "下滑": "下滑",
  "震荡": "时好时坏",
};

const CRITERION_LABEL: Record<string, string> = {
  "绝对底线": "低于及格线",
  "班级P25": "处于班级后段",
  "两者": "两条都中",
};

const BAND_LABEL: Record<string, string> = {
  "强制人工": "必须人工核对",
  "高亮提醒": "建议核对",
};

const VERSION_STATUS_LABEL: Record<string, string> = {
  "draft": "草稿",
  "reviewed": "已审核",
  "active": "正式",
};
/** 未知值原样返回，便于后续新增枚举不致报错。 */
function translate(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) return value ?? "";
  return map[value] ?? value;
}

export const attrLabel = (type: string) => translate(ATTR_LABEL, type);
export const trajLabel = (t: string | null | undefined) => translate(TRAJ_LABEL, t);
export const criterionLabel = (c: string | null | undefined) => translate(CRITERION_LABEL, c);
export const bandLabel = (b: string | null | undefined) => translate(BAND_LABEL, b);
export const versionStatusLabel = (s: string | null | undefined) => translate(VERSION_STATUS_LABEL, s);
