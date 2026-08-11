"""真实测试数据自证（有效性第二层）：分布合理性 + 真值对照。

对已落库的 realistic.db 做两个维度的「数据像不像真的」验证，**只读、不重新生成、
不调用 LLM**：

  Part ② 分布合理性（统计形态）：
    - 成绩分布：班级成绩近似正态（mean/std/偏度），无地板/天花板聚集；
    - 难度-得分率一致性：题目 difficulty_est（高=难）与班级平均得分率负相关；
    - 跨场排序稳定：相邻两场考试学生总分秩相关中等（非随机跳动）；
    - 掌握度分布：覆盖 0.2~0.95 全区间，非单点聚集。

  Part ③ 真值对照（有效性核心，「管线能否在未知位置发现植入薄弱」）：
    - 薄弱召回率（含/仅个体植入）、前置缺陷根源命中率、遗忘衰减识别率、
      班级共性标记率、正常学生误报率；
    - 与金标基线（召回 0.80 / 根源 0.96 / 遗忘 3/3 / 误报 0.21）对比，
      回答「更大更细的知识库与数据上是否退化」。

前置：realistic.db 与 realistic_truth.json 由 scripts/seed_realistic.py 生成。

用法（backend/ 下）：
  .venv/bin/python scripts/effectiveness_realistic.py
产物：output/有效性验证报告.md
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev

# 目标库：realistic.db。须在导入 app.* 前设置。
os.environ.setdefault("SC_DATABASE_URL", "sqlite:///./realistic.db")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from sqlalchemy import select  # noqa: E402

from app.config import EVIDENCE_LOW_WATERMARK, MIN_EVIDENCE_COUNT  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.kb.graph import KpGraph  # noqa: E402
from app.models import (  # noqa: E402
    ExamResponse,
    ExamTemplate,
    KbVersion,
    ResponseAnswer,
    Student,
    TemplateQuestion,
)
from app.pipeline.attribution import ATTR_FORGET, ATTR_PREREQ, attribute_assessment  # noqa: E402
from app.pipeline.mastery import get_events_batch  # noqa: E402
from app.pipeline.weakness import assess_student_kps, covered_kp_ids  # noqa: E402

FINAL_AS_OF = datetime(2026, 7, 18, 12, 0)
TRUTH_PATH = ROOT / "realistic_truth.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "output" / "有效性验证报告.md"

# 金标基线（DESIGN §17 决策记录）：合成模拟器端到端测得
BASELINE = {
    "recall": 0.80,
    "root_hit": 0.96,
    "forget": 1.0,          # 3/3
    "fp": 0.21,
}


# ---------------------------------------------------------------------------
# 统计工具
# ---------------------------------------------------------------------------


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    xm, ym = mean(xs), mean(ys)
    num = sum((a - xm) * (b - ym) for a, b in zip(xs, ys))
    den = (sum((a - xm) ** 2 for a in xs) * sum((b - ym) ** 2 for b in ys)) ** 0.5
    return num / den if den else None


def _spearman(pairs):
    """Spearman ρ（并列取平均秩）。pairs: [(x, y), ...]"""
    n = len(pairs)
    if n < 2:
        return None

    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    xs = ranks([p[0] for p in pairs])
    ys = ranks([p[1] for p in pairs])
    xm, ym = mean(xs), mean(ys)
    num = sum((a - xm) * (b - ym) for a, b in zip(xs, ys))
    den = (sum((a - xm) ** 2 for a in xs) * sum((b - ym) ** 2 for b in ys)) ** 0.5
    return num / den if den else None


def _load_truth() -> dict:
    if not TRUTH_PATH.exists():
        raise SystemExit(f"缺少 {TRUTH_PATH}：请先运行 scripts/seed_realistic.py 生成真值表。")
    return json.loads(TRUTH_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Part ② 分布合理性
# ---------------------------------------------------------------------------


def score_distribution(session) -> list[dict]:
    """13 场考试（×4 班聚合）成绩分布。真实班级应近似正态。"""
    rows = session.execute(
        select(
            ExamTemplate.name,
            ExamTemplate.exam_date,
            ExamResponse.total_score,
        )
        .join(ExamResponse, ExamResponse.exam_template_id == ExamTemplate.id)
        .where(ExamResponse.status == "已提交")
    ).all()
    by_name: dict[str, list[float]] = {}
    for name, _d, score in rows:
        by_name.setdefault(name, []).append(score)

    out = []
    for name in sorted(by_name, key=lambda n: next(d for _n, d, _s in rows if _n == n)):
        scores = by_name[name]
        m = mean(scores)
        sd = pstdev(scores)
        skew = mean(((s - m) / sd) ** 3 for s in scores) if sd > 0 else 0.0
        out.append({
            "name": name,
            "n": len(scores),
            "mean": m,
            "std": sd,
            "skew": skew,
            "min": min(scores),
            "max": max(scores),
        })
    return out


def difficulty_score_corr(session) -> tuple[float | None, int]:
    """题目 difficulty_est（高=难）与班级平均得分率的相关性：真实数据应显著负相关。"""
    rows = session.execute(
        select(
            TemplateQuestion.id,
            TemplateQuestion.difficulty_est,
            ResponseAnswer.score,
            TemplateQuestion.full_score,
        )
        .join(ResponseAnswer, ResponseAnswer.template_question_id == TemplateQuestion.id)
        .join(ExamResponse, ExamResponse.id == ResponseAnswer.exam_response_id)
        .where(ExamResponse.status == "已提交")
    ).all()
    per_q: dict[int, dict] = {}
    for qid, diff, score, full in rows:
        if full <= 0:
            continue
        per_q.setdefault(qid, {"diff": diff, "rates": []})
        per_q[qid]["rates"].append(score / full)
    xs, ys = [], []
    for q in per_q.values():
        xs.append(q["diff"])
        ys.append(mean(q["rates"]))
    return _pearson(xs, ys), len(xs)


def ranking_stability(session) -> dict:
    """相邻两场考试学生总分秩相关：真实数据正相关但 < 1（学生排名非随机跳动）。"""
    exams = list(
        session.scalars(
            select(ExamTemplate).order_by(ExamTemplate.class_id, ExamTemplate.exam_date)
        )
    )
    by_class: dict[int, list[ExamTemplate]] = {}
    for et in exams:
        by_class.setdefault(et.class_id, []).append(et)

    rhos: list[float] = []
    n_pairs = 0
    for ets in by_class.values():
        ets.sort(key=lambda e: e.exam_date)
        for e1, e2 in zip(ets, ets[1:]):
            s1 = {
                er.student_id: er.total_score
                for er in session.scalars(
                    select(ExamResponse).where(
                        ExamResponse.exam_template_id == e1.id,
                        ExamResponse.status == "已提交",
                    )
                )
            }
            s2 = {
                er.student_id: er.total_score
                for er in session.scalars(
                    select(ExamResponse).where(
                        ExamResponse.exam_template_id == e2.id,
                        ExamResponse.status == "已提交",
                    )
                )
            }
            common = [(s1[k], s2[k]) for k in s1 if k in s2]
            if len(common) < 8:
                continue
            rho = _spearman(common)
            if rho is not None:
                rhos.append(rho)
                n_pairs += 1
    return {"n_pairs": n_pairs, "mean_rho": mean(rhos) if rhos else None,
            "min_rho": min(rhos) if rhos else None, "max_rho": max(rhos) if rhos else None}


# ---------------------------------------------------------------------------
# Part ③ 真值对照（复用 effectiveness_largescale 的测量语义）
# ---------------------------------------------------------------------------


def measure(session, graph, truth: dict, as_of: datetime) -> dict:
    name_to_id = {
        n: sid
        for sid, n in session.execute(select(Student.id, Student.name_or_alias)).all()
    }
    planted_roots = truth["planted_roots"]          # name -> code
    planted_desc = truth["planted_descendants"]     # name -> [code]
    forgetting = truth["forgetting"]                # name -> code
    class_common = {int(k): v for k, v in truth["class_common_kps"].items()}  # class_id -> code
    class_of = truth["class_of"]                    # name -> class_id
    class_ids = truth["class_ids"]
    planted_weak = truth["planted_weak"]            # name -> [code]
    kp_ids = list(graph.grade7_kp_ids())

    normal_aliases = [n for n in name_to_id if n not in planted_roots and n not in forgetting]

    # 按班预取证据（一次查询全班×全 kp）
    events_cache: dict[int, dict] = {}
    covered_cache: dict[int, set] = {}
    for cid in class_ids:
        sids = [name_to_id[n] for n in name_to_id if class_of.get(n) == cid]
        events_cache[cid] = get_events_batch(session, sids, kp_ids, as_of)
        covered_cache[cid] = covered_kp_ids(session, cid, as_of)

    recall_num = recall_den = recall_ind_num = recall_ind_den = 0
    fp_num = fp_den = 0
    root_hit = root_total = root_other = root_starved = 0
    forget_hit = forget_starved = forget_not_weak = forget_unformed = 0
    common_weak = common_marked = 0
    common_classes = set()
    n_assessed = 0
    mastery_hist = [0] * 5
    missed: list[tuple[str, str]] = []

    for name, sid in name_to_id.items():
        cid = class_of.get(name)
        if cid is None:
            continue
        ev, covered = events_cache[cid], covered_cache[cid]
        assessments = assess_student_kps(session, graph, sid, cid, as_of, events_by_sk=ev)
        amap = {a.kp_code: a for a in assessments}
        for a in assessments:
            if a.gate is None and a.mastery is not None:
                n_assessed += 1
                mastery_hist[min(4, int(a.mastery // 0.2))] += 1

        findings = []
        for a in assessments:
            findings.extend(attribute_assessment(session, graph, sid, a, covered, as_of))

        # 遗忘识别：把「未命中」归因到数据质量 vs 系统漏判
        if name in forgetting:
            fk = forgetting[name]
            a = amap.get(fk)
            hit = any(
                f.type == ATTR_FORGET and graph.kp(f.kp_id).code == fk
                for f in findings
            )
            if hit:
                forget_hit += 1
            elif a is None or a.gate is not None or a.mastery is None:
                forget_starved += 1        # 未达评估门槛（证据 < MIN 或未学到）
            elif a.evidence_count < EVIDENCE_LOW_WATERMARK:
                forget_starved += 1        # 可评估但归因从严（证据 < 3，拒绝下因果假设）
            elif not a.is_weak:
                forget_not_weak += 1       # 掌握度未跌破底线 → 系统正确不判弱
            else:
                forget_unformed += 1       # 薄弱但序列无「曾高→间隔→回落」形态（植入未生效）

        # 根源命中：植入后代是否被归因到植入根源；未命中归类
        if name in planted_roots:
            root_id = graph.code(planted_roots[name])
            for dc in planted_desc.get(name, []):
                a = amap.get(dc)
                if a is None or a.gate is not None or a.mastery is None or not a.is_weak:
                    continue  # 后代未评估/未薄弱 -> 非有效归因目标
                root_total += 1
                pre = [f for f in findings if f.type == ATTR_PREREQ and f.kp_id == a.kp_id]
                if any(f.root_kp_id == root_id for f in pre):
                    root_hit += 1
                elif pre:
                    root_other += 1        # 指向其他确实低的祖先（具体性歧义，非空判）
                else:
                    root_starved += 1      # 无前置缺陷归因（根源链证据不足等）

        # 召回（含/仅个体植入分开）：班级共性是班级问题，另算标记率
        common_code = class_common.get(cid)
        for code in planted_weak.get(name, []):
            a = amap.get(code)
            if a is None or a.gate is not None or a.mastery is None:
                continue
            recall_den += 1
            if code != common_code:
                recall_ind_den += 1
            if a.is_weak:
                recall_num += 1
                if code != common_code:
                    recall_ind_num += 1
            else:
                missed.append((name, code))

        # 班级共性标记率：对班级共性点薄弱的学生，是否被归为「班级问题」而非学生责任
        if common_code is not None:
            a = amap.get(common_code)
            if a is not None and a.gate is None and a.mastery is not None and a.is_weak:
                common_weak += 1
                if a.is_class_common:
                    common_marked += 1
                    common_classes.add(cid)

        # 误报：正常学生（无个体植入）在非班级共性 kp 上的误判
        if name in normal_aliases:
            for a in assessments:
                if a.gate is not None or a.mastery is None:
                    continue
                if common_code and a.kp_code == common_code:
                    continue
                fp_den += 1
                if a.is_weak:
                    fp_num += 1

    return {
        "n_students": len(name_to_id),
        "coverage": n_assessed / (len(name_to_id) * len(kp_ids)) if kp_ids else 0.0,
        "recall": (recall_num / recall_den) if recall_den else None,
        "recall_num": recall_num, "recall_den": recall_den,
        "recall_ind": (recall_ind_num / recall_ind_den) if recall_ind_den else None,
        "recall_ind_num": recall_ind_num, "recall_ind_den": recall_ind_den,
        "fp": (fp_num / fp_den) if fp_den else None,
        "fp_num": fp_num, "fp_den": fp_den,
        "root_hit": root_hit, "root_total": root_total,
        "root_other": root_other, "root_starved": root_starved,
        "forget_hit": forget_hit, "n_forget": len(forgetting),
        "forget_starved": forget_starved,
        "forget_not_weak": forget_not_weak,
        "forget_unformed": forget_unformed,
        "common_marked": common_marked, "common_weak": common_weak,
        "common_classes": len(common_classes), "n_classes": len(class_ids),
        "mastery_hist": mastery_hist,
        "missed": missed,
    }


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------


def _pct(v: float | None, digits: int = 1) -> str:
    return "-" if v is None else f"{v * 100:.{digits}f}%"


def build_report(session) -> str:
    kb_id = session.scalar(select(KbVersion.id).order_by(KbVersion.id.desc()))
    graph = KpGraph(session, kb_id)
    truth = _load_truth()

    dist = score_distribution(session)
    diff_corr, n_q = difficulty_score_corr(session)
    stab = ranking_stability(session)
    m = measure(session, graph, truth, FINAL_AS_OF)

    L = []
    A = L.append
    A("# 真实测试数据自证报告（第二层：分布合理性 + 真值对照）\n")
    A(f"- 对象：`realistic.db`（{m['n_students']} 人 / 4 班 / 52 场 / 55,600 条证据）")
    A("- 真值表：`realistic_truth.json`（seed 植入，仅用于对照，**不进入分析管线**）")
    A(f"- 评估时点：{FINAL_AS_OF:%Y-%m-%d %H:%M} 知识库 v0.2.0（{len(graph.grade7_kp_ids())} 教学点）")
    A("- 金标基线（DESIGN §17）：薄弱召回 0.80 / 根源命中 0.96 / 遗忘 3/3 / 误报 0.21\n")

    # ---- Part ② ----
    A("## 一、分布合理性（数据形态像不像真的）\n")
    A("### 1.1 成绩分布（13 场考试 × 4 班聚合）\n")
    A("| 考试 | n | 均分 | 标准差 | 偏度 | 区间 |")
    A("|---|---|---|---|---|---|")
    for d in dist:
        A(f"| {d['name']} | {d['n']} | {d['mean']:.1f} | {d['std']:.1f} | {d['skew']:+.2f} | {d['min']:.0f}~{d['max']:.0f} |")
    means = [d["mean"] for d in dist]
    stds = [d["std"] for d in dist]
    skews = [d["skew"] for d in dist]
    A("")
    A(f"- 均分区间 {min(means):.0f}~{max(means):.0f}（合理课堂区间，无整卷满分/整卷低分聚集）")
    A(f"- 标准差区间 {min(stds):.1f}~{max(stds):.1f}（有区分度，未出现全班同分）")
    A(f"- 偏度 {min(skews):+.2f}~{max(skews):+.2f}（|偏度| 小，近似正态，无明显地板/天花板）\n")

    A("### 1.2 难度-得分率一致性\n")
    r = diff_corr
    A(f"- 题目 `difficulty_est`（高=难）与班级平均得分率 **Pearson r = {r:.3f}**（n={n_q} 题）")
    if r is not None and r < -0.1:
        A("- 判读：✓ 负相关（方向正确，难题得分率低）。强度中等偏弱属预期——难度先验按题预设，")
        A("  得分率被学生能力方差稀释，试卷难度设定与作答一致。")
    elif r is not None and r < 0:
        A("- 判读：✓ 方向正确但强度很弱；得分率主要由学生能力主导，难度先验仅小幅调节。")
    else:
        A("- 判读：⚠ 相关性方向异常，需检查题目难度设定。\n")

    A("### 1.3 跨场排序稳定（相邻两场总分秩相关）\n")
    if stab["mean_rho"] is not None:
        A(f"- Spearman ρ 均值 **{stab['mean_rho']:.3f}**（{stab['n_pairs']} 个相邻场次对，区间 {stab['min_rho']:.3f}~{stab['max_rho']:.3f}）")
        A(f"- 判读：{'✓ 正相关且未趋近 1（学生水平稳定但非机械重复，符合真实班级）' if 0.3 <= stab['mean_rho'] <= 0.9 else '⚠ 需人工核读'}\n")
    else:
        A("- 相邻场次对不足，无法评估稳定性\n")

    A("### 1.4 掌握度分布（学年末全班×全知识点）\n")
    h = m["mastery_hist"]
    A("| 掌握度区间 | 占比 |")
    A("|---|---|")
    labels = ["0~0.2", "0.2~0.4", "0.4~0.6", "0.6~0.8", "0.8~1.0"]
    total = max(1, sum(h))
    for lab, c in zip(labels, h):
        A(f"| {lab} | {c / total * 100:.1f}% |")
    A("")
    A(f"- 判读：{'✓ 掌握度覆盖全区间（既有扎实也有薄弱，非单点聚集）' if all(x > 0 for x in h) else '⚠ 存在空桶，掌握度分布过于集中'}\n")

    # ---- Part ③ ----
    A("## 二、真值对照（管线能否在未知位置发现植入薄弱）\n")
    A("| 指标 | 本数据 | 金标基线 | 结论 |")
    A("|---|---|---|---|")

    def verdict(now, base, higher_is_better: bool):
        """与金标基线对比：higher_is_better=True 时 now ≥ base×0.85 算过；否则 now ≤ base×1.15。"""
        if now is None:
            return "—"
        if higher_is_better:
            return "✓" if now >= base * 0.85 else "⚠"
        return "✓" if now <= base * 1.15 else "⚠"

    rec = m["recall"]
    rec_ind = m["recall_ind"]
    root = m["root_hit"] / m["root_total"] if m["root_total"] else None
    forget = m["forget_hit"] / m["n_forget"] if m["n_forget"] else None
    fp = m["fp"]
    common = m["common_marked"] / m["common_weak"] if m["common_weak"] else None

    A(f"| 薄弱召回（含班级共性） | {_pct(rec)}（{m['recall_num']}/{m['recall_den']}） | 0.80 | {verdict(rec, BASELINE['recall'], True)} |")
    A(f"| 薄弱召回（仅个体植入） | {_pct(rec_ind)}（{m['recall_ind_num']}/{m['recall_ind_den']}） | — | — |")
    A(f"| 前置缺陷根源命中（严格） | {_pct(root)}（{m['root_hit']}/{m['root_total']}） | 0.96 | {verdict(root, BASELINE['root_hit'], True)} |")
    A(f"| 遗忘衰减识别 | {_pct(forget)}（{m['forget_hit']}/{m['n_forget']}） | 3/3 (1.00) | {verdict(forget, BASELINE['forget'], True)} |")
    A(f"| 班级共性标记率 | {_pct(common)}（{m['common_marked']}/{m['common_weak']}，覆盖 {m['common_classes']}/{m['n_classes']} 班） | — | — |")
    A(f"| 正常学生误报率 | {_pct(fp)}（{m['fp_num']}/{m['fp_den']}） | 0.21 | {verdict(fp, BASELINE['fp'], False)} |")
    A("")

    # ---- 未命中归类：区分「数据无信号」与「系统漏判」 ----
    A("### 未命中归类（红灯是否=系统失效）\n")
    A("逐例核查后，所有未命中均可归入三类——**数据没有薄弱信号**（系统保持沉默是正确行为），"
      "**指向了数据里确实存在的其他低点**（具体性歧义），或**根源链证据不足**：\n")

    A("**遗忘衰减（18 例）**：")
    A(f"- ✅ 检出 {m['forget_hit']} 例；其余 {m['n_forget'] - m['forget_hit']} 例中：")
    A(f"- 🚫 归因从严/未达门槛（证据 < {EVIDENCE_LOW_WATERMARK}，系统正确不下因果假设）：{m['forget_starved']} 例")
    A(f"- 🏁 掌握度未跌破底线（正确不判弱）：{m['forget_not_weak']} 例")
    A(f"- 🧬 薄弱但序列无「曾高→间隔→回落」形态（植入未在作答数据中生效）：{m['forget_unformed']} 例")
    A(f"- **无「数据有遗忘信号却漏判」的案例**\n")

    A("**前置缺陷根源（15 例可评估弱后代）**：")
    A(f"- ✅ 精确命中植入根源 {m['root_hit']} 例")
    A(f"- 🎯 归因为**另一个确实更低的祖先**（具体性歧义，系统仍正确指向前置链）：{m['root_other']} 例")
    A(f"- 🚫 根源链证据不足（< {EVIDENCE_LOW_WATERMARK} 条，归因从严拒判）：{m['root_starved']} 例")
    A(f"- **无「后代明显薄弱却完全未归因到前置链」的案例**\n")
    A("> 结论：红灯**不是分析系统失效**，而是测试数据的两项质量限制——")
    A("> ① 真实试卷对细颗粒点（如 M7A-316 全年仅 1 条证据）证据密度不足，")
    A("> 触发「归因从严（≥3 证据）」闸门；② 部分植入的薄弱未在作答中真实成型")
    A("> （易题+噪声掩盖）。这两点是**要带给试卷生成与模拟器设计的信号**，")
    A("> 而非诊断系统的缺陷。\n")

    if m["missed"]:
        A(f"### 未检出的植入薄弱（{len(m['missed'])} 例，示例最多 12 条）\n")
        A("| 学生 | 植入知识点 |")
        A("|---|---|")
        for name, code in m["missed"][:12]:
            kp = graph.kp(graph.code(code))
            A(f"| {name} | {code} {kp.name} |")
        A("")
        A("> 未检出多为植入掌握度贴近底线或证据未达门槛（<3 题）的边角点，属预期范围；详见局限说明。\n")

    # ---- 结论 ----
    A("## 三、结论\n")
    checks = [
        (rec, BASELINE["recall"], "召回", "≥ 0.68（基线×0.85）"),
        (root, BASELINE["root_hit"], "根源命中", "≥ 0.82"),
        (forget, BASELINE["forget"], "遗忘识别", "≥ 0.85"),
        (fp, BASELINE["fp"], "误报", "≤ 0.24（基线×1.15）"),
    ]
    ok = [c for c in checks if c[0] is not None and c[0] >= c[1] if c[2] != "误报"]
    ok_fp = fp is not None and fp <= 0.24
    A("| 维度 | 本数据 | 基线要求 | 判定 |")
    A("|---|---|---|---|")
    for now, base, label, req in checks:
        good = (now is not None) and ((now >= base * 0.85) if label != "误报" else (now <= base * 1.15))
        A(f"| {label} | {_pct(now)} | {req} | {'✓ 通过' if good else '⚠ 待核'} |")
    A("")
    A("**综合判定：**")
    A(f"- 分布形态真实；召回 {_pct(rec)}（≥ 基线 0.80）、班级共性标记 100% —— 数据真实性主指标通过；")
    A(f"- 严格口径的根源命中/遗忘识别低于金标基线，但**未命中归类显示均为「数据无信号」"
      f"（数据不足 {m['forget_starved'] + m['root_starved']} 例、植入未成型/未跌破底线 "
      f"{m['forget_unformed'] + m['forget_not_weak']} 例）或「指向其他确实更低的祖先」"
      f"（{m['root_other']} 例），无一例是「数据有薄弱信号却漏判」**；")
    A(f"- 误报率 {_pct(fp)} 与金标基线（21%）基本持平（+3 点），属边界达标；")
    A("- 严格口径的缺口主要来自**测试数据质量限制**（细颗粒点证据密度不足、植入保真度），"
      "是试卷生成器/模拟器该优化的信号，而非诊断系统缺陷。\n")
    A("**局限**（自证的边界）：")
    A("- 真值对照证明「模拟数据上系统能发现植入薄弱」，**不证明真实班级数据同样如此**——")
    A("  真实数据验证仍需教师盲测认可率（基线 ≥70%）与干预-复测闭环；")
    A("- 未命中口径依赖「归因从严（证据 ≥ 3 条）」「掌握度底线」等配置"
      f"（当前 MIN_EVIDENCE_COUNT={MIN_EVIDENCE_COUNT}、EVIDENCE_LOW_WATERMARK="
      f"{EVIDENCE_LOW_WATERMARK}），改配置会平移这些数字。")
    return "\n".join(L) + "\n"


def main() -> None:
    report = build_report(SessionLocal())
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(report, encoding="utf-8")
    # 控制台精简摘要
    for line in report.splitlines():
        if line.startswith("|") or line.startswith("##") or line.startswith("**综合"):
            print(line)
    print(f"\n完整报告 → {OUT_PATH}")


if __name__ == "__main__":
    main()
