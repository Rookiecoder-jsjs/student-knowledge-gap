"""Agent 评测集（agent-product-design §10.1 Phase 4；§4.6 对账纪律）。

有效性验证计划（effectiveness-validation-plan.md）思想在 Agent 层的延伸：
**Agent 的每句结论必须能与确定性管线对账，不一致即缺陷。**

做法三步：

1. **构造**：种入一组「已知真值」的历史考试（三人现象全部人工植入，见
   ``build_scenario``）——学生 W01 基础点持续低（植入根源）→ 中间点/应用点
   连带薄弱；独立点上 4/8 人低于底线（植入班级共性弱项）；教学进度与名册
   为确定事实。所有分数远离判定边界（0.6 底线、0.4 共性占比），断言不靠猜。
2. **标准问答对**：教师会问的 8 个标准问题，映射到全部七个只读工具
   （归因两问：缺陷链归因 + 孤立点诚实性）。
3. **对账**：工具输出 ⊕ 植入真值逐条断言——身份类结论（谁弱/根源是哪个点/
   共性弱项是谁）必须**精确相等**；数值类（掌握度/置信度）只断区间，对
   时间衰减微漂移鲁棒。as_of 固定在末场考试次日，避免「现在」漂移。

用途两条：
- 回归门禁：``tests/test_evalset.py`` 随每次提交跑（内存库，秒级）；
- 验收演示：``scripts/run_agent_evalset.py`` 跑同一组用例出对账报告
  （output/agent-evalset-report.md），试点校装机验收可直接演示。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.ingestion.commit import add_manual_response, commit_exam
from app.models import (
    Class,
    KbVersion,
    KnowledgePoint,
    KpRelation,
    School,
    Student,
)
from tests.conftest import add_progress, dt, make_exam

EVALSET_VERSION = "agent-evalset-v0.1.0"

AS_OF = date(2025, 11, 16)  # 末场考试次日：结论冻结时点

# 分数板（满分 10）。全部远离边界：强项 0.9、弱项 0.3~0.5（底线 0.6），
# 共性弱项占比 4/8=50%（阈值 40%）。
_S_STRONG = 9.0
_SCORES: dict[str, dict[str, float]] = {
    # alias: {kp_code: score}
    "W01": {"E1": 3.0, "E2": 5.0, "E3": 4.0, "EU": 9.0},  # 植入：根源在 E1 的连带薄弱
    "S02": {"E1": 9.0, "E2": 9.0, "E3": 9.0, "EU": 9.0},
    "S03": {"E1": 9.0, "E2": 9.0, "E3": 9.0, "EU": 4.0},  # 植入：孤立点共性弱项
    "S04": {"E1": 9.0, "E2": 9.0, "E3": 9.0, "EU": 4.0},
    "S05": {"E1": 9.0, "E2": 9.0, "E3": 9.0, "EU": 4.0},
    "S06": {"E1": 9.0, "E2": 9.0, "E3": 9.0, "EU": 4.0},
    "S07": {"E1": 9.0, "E2": 9.0, "E3": 9.0, "EU": 9.0},
    "S08": {"E1": 9.0, "E2": 9.0, "E3": 9.0, "EU": 9.0},
}
_ALIASES = list(_SCORES)
_WEAK_EU = ["S03", "S04", "S05", "S06"]

_EXAMS = [  # (name, date, type)
    ("九月月考", date(2025, 9, 20), "单元"),
    ("十月月考", date(2025, 10, 18), "单元"),
    ("期中考试", date(2025, 11, 15), "期中"),
]
_TAUGHT_AT = date(2025, 9, 1)


# ---------------------------------------------------------------------------
# 场景构造（真值来源）
# ---------------------------------------------------------------------------


def build_scenario(session: Session) -> dict[str, Any]:
    """种入知识库 + 班级 + 三场已提交考试，返回真值字典（对账基准）。"""
    kb = KbVersion(subject="数学", textbook_edition="评测版", version="eval-v1",
                   status="active")
    session.add(kb)
    session.flush()

    specs = [("E1", "基础点"), ("E2", "中间点"), ("E3", "应用点"), ("EU", "独立点")]
    kp_ids: dict[str, int] = {}
    for code, name in specs:
        kp = KnowledgePoint(
            kb_version_id=kb.id, code=code, name=name, grade=7, semester=1,
            chapter="评测章", cog_levels_expected=["应用"], difficulty_prior=0.5,
            mastery_floor=0.6,
        )
        session.add(kp)
        session.flush()
        kp_ids[code] = kp.id
    session.add(KpRelation(from_kp_id=kp_ids["E1"], to_kp_id=kp_ids["E2"],
                           type="prerequisite", weight=0.9))
    session.add(KpRelation(from_kp_id=kp_ids["E2"], to_kp_id=kp_ids["E3"],
                           type="prerequisite", weight=0.9))

    school = School(name="评测学校")
    session.add(school)
    session.flush()
    clazz = Class(school_id=school.id, name="初一（3）班", grade=7, subject="数学")
    session.add(clazz)
    session.flush()

    sid_by_alias: dict[str, int] = {}
    for alias in _ALIASES:
        stu = Student(school_id=school.id, class_id=clazz.id, name_or_alias=alias)
        session.add(stu)
        session.flush()
        sid_by_alias[alias] = stu.id

    add_progress(session, clazz.id, list(kp_ids.values()), taught_at=_TAUGHT_AT)

    questions = [
        (1, 10.0, "解答", "应用", [(kp_ids["E1"], 1.0)]),
        (2, 10.0, "解答", "应用", [(kp_ids["E2"], 1.0)]),
        (3, 10.0, "解答", "应用", [(kp_ids["E3"], 1.0)]),
        (4, 10.0, "解答", "应用", [(kp_ids["EU"], 1.0)]),
    ]
    exam_ids: dict[str, int] = {}
    for key, (name, exam_date, type_) in zip(("sep", "oct", "mid"), _EXAMS):
        tpl = make_exam(session, clazz.id, name, exam_date, type_, questions)
        for alias in _ALIASES:
            scores = _SCORES[alias]
            add_manual_response(
                session, tpl.id, sid_by_alias[alias],
                {idx: scores[code] for idx, code in
                 ((1, "E1"), (2, "E2"), (3, "E3"), (4, "EU"))},
            )
        commit_exam(session, tpl.id)
        exam_ids[key] = tpl.id
    session.flush()

    return {
        "kb_version_id": kb.id,
        "class_id": clazz.id,
        "kp_ids": kp_ids,
        "kp_names": {code: name for code, name in specs},
        "sid_by_alias": sid_by_alias,
        "alias_by_sid": {v: k for k, v in sid_by_alias.items()},
        "students": list(sid_by_alias.values()),
        "weak_eu_aliases": list(_WEAK_EU),
        "w01_sid": sid_by_alias["W01"],
        "s03_sid": sid_by_alias["S03"],
        "exam_ids": exam_ids,
        "latest_exam_id": exam_ids["mid"],
        "taught_date": str(_TAUGHT_AT),
    }


# ---------------------------------------------------------------------------
# 用例与断言
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    """一条对账断言：名字 + 真值描述 + 判定函数。"""

    name: str
    expect: str
    ok: Callable[[dict, dict], bool]


@dataclass(frozen=True)
class EvalCase:
    """一个标准问答对：教师的问题 → 工具调用 → 对账断言组。"""

    id: str
    question: str
    tool: str
    run: Callable[[Session, Any, dict], dict]  # (session, graph, truth) -> result
    checks: tuple[Check, ...]


def _run_summary(session, graph, truth):
    from app.mcp_tools import get_exam_summary

    return get_exam_summary(session, graph, truth["class_id"],
                            exam_id=truth["exam_ids"]["mid"])


def _run_mastery_eu(session, graph, truth):
    from app.mcp_tools import get_kp_mastery

    return get_kp_mastery(session, graph, truth["students"], [truth["kp_ids"]["EU"]],
                          as_of=AS_OF)


def _run_attr_w01(session, graph, truth):
    from app.mcp_tools import run_attribution

    return run_attribution(session, graph, truth["w01_sid"], as_of=AS_OF)


def _run_attr_s03(session, graph, truth):
    from app.mcp_tools import run_attribution

    return run_attribution(session, graph, truth["s03_sid"], as_of=AS_OF)


def _run_kp_detail(session, graph, truth):
    from app.mcp_tools import get_kp_detail

    return get_kp_detail(session, graph, "E2")


def _run_progress(session, graph, truth):
    from app.mcp_tools import get_teaching_progress

    return get_teaching_progress(session, truth["class_id"])


def _run_roster(session, graph, truth):
    from app.mcp_tools import list_students

    return list_students(session, truth["class_id"])


def _run_latest(session, graph, truth):
    from app.mcp_tools import latest_exam_id

    return {"latest_exam_id": latest_exam_id(session, truth["class_id"])}


def _attr_triples(result) -> set[tuple[str, str, str | None]]:
    return {
        (a["kp_code"], a["type"], a["root_kp_name"])
        for a in result["attributions"]
    }


CHECKS = {
    "summary": (
        Check("committed", "全班 8 人全部提交、无人缺交",
              lambda r, t: r["committed"] == 8 and r.get("pending") == 0),
        Check("common_weak", "共性弱项恰为「独立点」（4/8 人低于底线 ≥40% 阈值）",
              lambda r, t: [w["code"] for w in (r.get("common_weak") or [])] == ["EU"]),
        Check("no_false_common", "前置链三点不被误标为共性弱项（仅 1/8 人弱）",
              lambda r, t: "E1" not in [w["code"] for w in (r.get("common_weak") or [])]
              and "E3" not in [w["code"] for w in (r.get("common_weak") or [])]),
        Check("markdown", "面向教师的总结文本非空",
              lambda r, t: isinstance(r.get("summary_markdown"), str) and bool(r["summary_markdown"].strip())),
    ),
    "mastery_eu": (
        Check("weak_set", "低于底线的恰为植入的 4 人（S03~S06），无多报无漏报",
              lambda r, t: {t["alias_by_sid"][x["student_id"]]
                            for x in r["weak_first"]} == set(t["weak_eu_aliases"])
              and r["weak_total"] == 4),
        Check("below_floor_flag", "每条弱项行 below_floor=True 且按掌握度升序在前",
              lambda r, t: all(x["below_floor"] for x in r["weak_first"])
              and [x["mastery"] for x in r["weak_first"]]
              == sorted(x["mastery"] for x in r["weak_first"])),
        Check("mastered_clean", "达标样本与弱项集合无交集（S03~S06 不出现在达标侧）",
              lambda r, t: {t["alias_by_sid"][x["student_id"]]
                            for x in r["mastered_sample"]}.isdisjoint(t["weak_eu_aliases"])),
    ),
    "attr_w01": (
        Check("root_chain", "归因假设恰为两条前置缺陷：E2 与 E3 同指根源「基础点」",
              lambda r, t: _attr_triples(r) == {
                  ("E2", "前置缺陷", t["kp_names"]["E1"]),
                  ("E3", "前置缺陷", t["kp_names"]["E1"]),
              }),
        Check("honest_on_root", "基础点自身无归因条目（无前置可探时不编因果故事）",
              lambda r, t: all(a["kp_code"] != "E1" for a in r["attributions"])),
        Check("active_verdict", "全部为系统推导（未被裁决）状态",
              lambda r, t: all(a["verdict"] == "active" for a in r["attributions"])),
        Check("falsifiable", "每条假设附可证伪预测（含诊断题验证路径）",
              lambda r, t: all("诊断题" in a["prediction"]
                               for a in r["attributions"] if a["type"] == "前置缺陷")),
        Check("conf_range", "置信度落在 (0.5, 0.95] 合理区间",
              lambda r, t: all(0.5 < a["confidence"] <= 0.95 for a in r["attributions"])),
        Check("no_forget", "无遗忘衰减误判（分数平稳，无高→低轨迹）",
              lambda r, t: all(a["type"] != "遗忘衰减" for a in r["attributions"])),
    ),
    "attr_s03": (
        Check("no_false_causal", "孤立点薄弱的正常生不被编造前置缺陷（无前置可探）",
              lambda r, t: all(a["type"] != "前置缺陷" for a in r["attributions"])),
        Check("honest_silence_or_evidence", "无前置可探时要么沉默要么明说证据状态，绝不虚构根源名",
              lambda r, t: all(a["root_kp_name"] is None for a in r["attributions"])),
    ),
    "kp_detail": (
        Check("direct_prereq", "中间点的直接前置恰为基础点",
              lambda r, t: [p["name"] for p in r["direct_prerequisites"]]
              == [t["kp_names"]["E1"]]),
        Check("chain", "前置链：深度 1 处为基础点",
              lambda r, t: [(c["code"], c["depth"]) for c in r["prerequisite_chain"]]
              == [("E1", 1)]),
        Check("successors", "后继恰为应用点",
              lambda r, t: [s["name"] for s in r["successors"]] == [t["kp_names"]["E3"]]),
    ),
    "progress": (
        Check("taught_count", "本学期共教过 4 个知识点",
              lambda r, t: r["taught_count"] == 4),
        Check("eu_date", "独立点授课日期为 2025-09-01",
              lambda r, t: any(str(row.get("taught_at"))[:10] == t["taught_date"]
                               for row in r["progress"]
                               if row.get("kp_id") == t["kp_ids"]["EU"])),
    ),
    "roster": (
        Check("total", "名册共 8 人",
              lambda r, t: r.get("total") == 8),
        Check("alias_only", "名册只有别名——绝不含真名或分数字段（§9 prompt 最小化）",
              lambda r, t: r.get("students")
              and all("name_or_alias" in it
                      and not ({"real_name", "score", "total_score"} & set(it))
                      for it in r["students"])),
    ),
    "latest": (
        Check("latest_exam", "最近一场为期中考试（2025-11-15）",
              lambda r, t: r["latest_exam_id"] == t["exam_ids"]["mid"]),
        Check("not_earlier", "不取更早日历序的九月场（按 exam_date 降序）",
              lambda r, t: r["latest_exam_id"] != t["exam_ids"]["sep"]),
    ),
}

CASES: list[EvalCase] = [
    EvalCase("summary", "这次期中考试考得怎么样？", "get_exam_summary",
             _run_summary, CHECKS["summary"]),
    EvalCase("mastery_eu", "班里有哪些孩子在独立点上还没过关？", "get_kp_mastery",
             _run_mastery_eu, CHECKS["mastery_eu"]),
    EvalCase("attr_w01", "W01 的应用点为什么薄弱？", "run_attribution",
             _run_attr_w01, CHECKS["attr_w01"]),
    EvalCase("attr_s03", "S03 的薄弱是怎么回事？（孤立点，应无可归因前置）",
             "run_attribution", _run_attr_s03, CHECKS["attr_s03"]),
    EvalCase("kp_detail", "中间点的前置是什么？后面接什么？", "get_kp_detail",
             _run_kp_detail, CHECKS["kp_detail"]),
    EvalCase("progress", "这学期教到哪些点了？独立点什么时候教的？",
             "get_teaching_progress", _run_progress, CHECKS["progress"]),
    EvalCase("roster", "我们班现在有多少个学生？", "list_students",
             _run_roster, CHECKS["roster"]),
    EvalCase("latest", "最近一次考试是哪场？", "latest_exam_id",
             _run_latest, CHECKS["latest"]),
]

READ_TOOLS = {c.tool for c in CASES}


# ---------------------------------------------------------------------------
# 执行器（pytest 与 CLI 共用）
# ---------------------------------------------------------------------------


def run_all(session: Session) -> list[dict]:
    """跑全部用例，返回逐条行：{id, question, tool, ok, checks:[...]}。"""
    from app.mcp_tools import resolve_graph

    truth = build_scenario(session)  # 知识库随场景种入，须先于 graph 解析
    _, graph = resolve_graph(session)
    rows = []
    for case in CASES:
        try:
            result = case.run(session, graph, truth)
            checks = [
                {"name": c.name, "expect": c.expect, "ok": bool(c.ok(result, truth))}
                for c in case.checks
            ]
        except Exception as e:  # noqa: BLE001 —— 工具抛错本身就是对账失败，记录不中断
            checks = [{"name": "tool_raised", "expect": "工具正常返回",
                       "ok": False, "error": f"{type(e).__name__}: {e}"}]
        rows.append({
            "id": case.id,
            "question": case.question,
            "tool": case.tool,
            "ok": all(c["ok"] for c in checks),
            "checks": checks,
        })
    return rows
