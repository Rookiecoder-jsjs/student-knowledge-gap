"""评测集验收演示（agent-product-design §10.1 Phase 4 批次A）。

与 tests/test_evalset.py 跑同一组用例（app/evalset.py），出人类可读的对账
报告 → output/agent-evalset-report.md。试点校装机验收可直接演示：
「Agent 会说的每一句结论，都能对上确定性管线的输出」。

用法（backend/ 下）：
  ../.venv/bin/python scripts/run_agent_evalset.py
退出码：全绿 0，任一失败 1（CI 可直接用作门禁）。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base  # noqa: E402
from app.evalset import EVALSET_VERSION, run_all  # noqa: E402

OUT_PATH = ROOT / "output" / "agent-evalset-report.md"


def main() -> int:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    with S() as session:
        rows = run_all(session)

    total = len(rows)
    passed = sum(1 for r in rows if r["ok"])
    check_total = sum(len(r["checks"]) for r in rows)
    check_passed = sum(1 for r in rows for c in r["checks"] if c["ok"])

    lines = [
        "# Agent 评测集对账报告",
        "",
        f"- 版本：`{EVALSET_VERSION}`",
        "- 运行时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        f"- 用例：**{passed}/{total}** 通过；断言：**{check_passed}/{check_total}** 通过",
        "",
        "| 用例 | 教师的问题 | 工具 | 结果 | 断言明细 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        detail = "；".join(
            f"{c['name']}{'✓' if c['ok'] else '✗ ' + c.get('error', c['expect'])}"
            for c in r["checks"]
        )
        lines.append(
            f"| {r['id']} | {r['question']} | `{r['tool']}` "
            f"| {'✅' if r['ok'] else '❌'} | {detail} |"
        )
    lines += ["", "---", "",
              "对账纪律：身份类结论精确相等；数值类只断区间（对时间衰减微漂移鲁棒）。",
              "任一失败 = 确定性管线行为漂移或工具聚合缺陷，先修管线再谈 Agent。"]

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n")
    print(f"[evalset] {passed}/{total} cases, {check_passed}/{check_total} checks -> {OUT_PATH}")

    for r in rows:
        mark = "PASS" if r["ok"] else "FAIL"
        bad = [c["name"] for c in r["checks"] if not c["ok"]]
        suffix = f"  ✗ {bad}" if bad else ""
        print(f"  [{mark}] {r['id']:<12} {r['tool']}{suffix}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
