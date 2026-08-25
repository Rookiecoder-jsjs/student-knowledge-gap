"""生成前端标签映射（单一真源 -> TS）。

单一真源：``app/labels_source.py``。改动标签后运行：

    cd backend && python -m scripts.gen_labels_ts

再提交生成的 ``frontend/app/src/lib/labels.ts``。测试 ``tests/test_labels_sync.py``
断言产物与真源一致，防漂移。纯 Python、无新依赖。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.labels_source import (
    ATTR_LABEL,
    BAND_LABEL,
    CRITERION_LABEL,
    EFFECT_LABEL,
    INTERVENTION_STATUS_LABEL,
    KIND_LABEL,
    TRAJ_LABEL,
    VERSION_STATUS_LABEL,
)

HEADER = """/**
 * 枚举值 -> 口语标签映射（自动生成，勿手改）。
 * 单一真源：backend/app/labels_source.py；改动后运行
 *   cd backend && python -m scripts.gen_labels_ts
 * 对应后端：app/pipeline/attribution.py 的 ATTR_*、app/pipeline/weakness.py 的
 * TRAJ_* / weak_criterion、app/ingestion/photo.py 的 band、app/models.py 的 kb status。
 */
"""

FOOTER = """
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
export const kindLabel = (k: string) => translate(KIND_LABEL, k);
export const interventionStatusLabel = (s: string) => translate(INTERVENTION_STATUS_LABEL, s);
export const effectLabel = (e: string) => translate(EFFECT_LABEL, e);
"""


def _ts_map(name: str, mapping: dict[str, str]) -> str:
    """dict -> `const NAME: Record<string, string> = { "k": "v", ... };`（键值均 JSON 转义）。"""
    body = ",\n".join(
        f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}"
        for k, v in mapping.items()
    )
    return f"const {name}: Record<string, string> = {{\n{body},\n}};"


def build_labels_ts() -> str:
    parts = [HEADER.strip(), _ts_map("ATTR_LABEL", ATTR_LABEL), _ts_map("TRAJ_LABEL", TRAJ_LABEL)]
    parts.append(_ts_map("CRITERION_LABEL", CRITERION_LABEL))
    parts.append(_ts_map("BAND_LABEL", BAND_LABEL))
    parts.append(_ts_map("VERSION_STATUS_LABEL", VERSION_STATUS_LABEL))
    parts.append(_ts_map("KIND_LABEL", KIND_LABEL))
    parts.append(_ts_map("INTERVENTION_STATUS_LABEL", INTERVENTION_STATUS_LABEL))
    parts.append(_ts_map("EFFECT_LABEL", EFFECT_LABEL))
    return "\n\n".join(parts) + FOOTER


def main() -> None:
    out = Path(__file__).resolve().parent.parent.parent / "frontend" / "app" / "src" / "lib" / "labels.ts"
    out.write_text(build_labels_ts(), encoding="utf-8")
    print(f"已生成 {out}")


if __name__ == "__main__":
    main()
