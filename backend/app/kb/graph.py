"""知识图谱的内存遍历（networkx，DESIGN §11：百级节点无需图数据库）。

边方向约定：prerequisite 边 from→to 表示「from 是 to 的前置」。
归因追溯 = 沿反向边做有界 BFS（≤ PREREQ_MAX_DEPTH 层）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgePoint, KpRelation

try:
    import networkx as nx
except ImportError:  # pragma: no cover
    nx = None


class KpGraph:
    """某个 kb_version 的前置依赖图。"""

    def __init__(self, session: Session, kb_version_id: int):
        self.kb_version_id = kb_version_id
        self._session = session
        self._kp: dict[int, KnowledgePoint] = {}
        self._code_to_id: dict[str, int] = {}
        self._prereq: dict[int, list[tuple[int, float]]] = {}   # kp_id → [(prereq_id, weight)]
        self._succ: dict[int, list[tuple[int, float]]] = {}     # kp_id → [(successor_id, weight)]（前向影响）
        self._confusable: dict[int, list[int]] = {}             # kp_id → [易混伙伴 kp_id]（双向）

        for kp in session.scalars(
            select(KnowledgePoint).where(KnowledgePoint.kb_version_id == kb_version_id)
        ):
            self._kp[kp.id] = kp
            self._code_to_id[kp.code] = kp.id
            self._prereq.setdefault(kp.id, [])
            self._succ.setdefault(kp.id, [])
            self._confusable.setdefault(kp.id, [])

        kp_ids = set(self._kp)
        for rel in session.scalars(
            select(KpRelation).where(KpRelation.type == "prerequisite")
        ):
            if rel.from_kp_id in kp_ids and rel.to_kp_id in kp_ids:
                self._prereq[rel.to_kp_id].append((rel.from_kp_id, rel.weight))
                self._succ[rel.from_kp_id].append((rel.to_kp_id, rel.weight))

        # 易混关系是双向语义：a 与 b 易混 ⇒ b 与 a 易混（kb-improvement-design K1）
        for rel in session.scalars(
            select(KpRelation).where(KpRelation.type == "confusable")
        ):
            if rel.from_kp_id in kp_ids and rel.to_kp_id in kp_ids:
                if rel.to_kp_id not in self._confusable[rel.from_kp_id]:
                    self._confusable[rel.from_kp_id].append(rel.to_kp_id)
                if rel.from_kp_id not in self._confusable[rel.to_kp_id]:
                    self._confusable[rel.to_kp_id].append(rel.from_kp_id)

        # networkx 视图（供可视化/未来扩展；核心遍历用上面的 dict）
        if nx is not None:
            self.nx_graph = nx.DiGraph()
            for kp_id in self._kp:
                self.nx_graph.add_node(kp_id)
            for to_id, edges in self._prereq.items():
                for from_id, w in edges:
                    self.nx_graph.add_edge(from_id, to_id, weight=w)

    # -- 查询 ---------------------------------------------------------------

    def kp(self, kp_id: int) -> KnowledgePoint:
        """按 id 取知识点；跨版本数据兜底按主键回查（名称跨版本稳定）。"""
        if kp_id in self._kp:
            return self._kp[kp_id]
        kp = self._session.get(KnowledgePoint, kp_id)
        if kp is None:
            raise KeyError(kp_id)
        return kp

    def code(self, kp_code: str) -> int:
        return self._code_to_id[kp_code]

    def is_container(self, kp_id: int) -> bool:
        return self._kp[kp_id].code.startswith("C")

    def direct_prerequisites(self, kp_id: int) -> list[tuple[int, float]]:
        return list(self._prereq.get(kp_id, []))

    def prerequisite_chain(
        self, kp_id: int, max_depth: int
    ) -> list[tuple[int, int, float]]:
        """有界 BFS：返回 [(ancestor_kp_id, depth, edge_weight), ...]。

        depth 从 1 开始；同一祖先只保留最先到达（最浅）的一次。
        """
        seen: dict[int, tuple[int, float]] = {}
        frontier = [(kp_id, 0)]
        while frontier:
            current, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for pre_id, w in self._prereq.get(current, []):
                if pre_id not in seen:
                    seen[pre_id] = (depth + 1, w)
                    frontier.append((pre_id, depth + 1))
        return [(aid, d, w) for aid, (d, w) in sorted(seen.items(), key=lambda kv: kv[1][0])]

    def descendants(
        self, kp_id: int, max_depth: int
    ) -> list[tuple[int, int, float]]:
        """前向影响：kp_id 薄弱会波及的后代 [(descendant_id, depth, edge_weight), ...]。

        depth 从 1 开始（直接后继）；同一后代只保留最先到达（最浅）的一次。
        与 prerequisite_chain 对称，供报告「可能波及下游」预警（kb-improvement-design K4）。
        """
        seen: dict[int, tuple[int, float]] = {}
        frontier = [(kp_id, 0)]
        while frontier:
            current, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for to_id, w in self._succ.get(current, []):
                if to_id not in seen:
                    seen[to_id] = (depth + 1, w)
                    frontier.append((to_id, depth + 1))
        return [(did, d, w) for did, (d, w) in sorted(seen.items(), key=lambda kv: kv[1][0])]

    def confusable_partners(self, kp_id: int) -> list[int]:
        """易混伙伴（kb-improvement-design K1）：与 kp_id 有 confusable 关系的 kp 列表。"""
        return list(self._confusable.get(kp_id, []))

    def grade7_kp_ids(self) -> list[int]:
        """本年级（grade==图谱主年级）非容器知识点，排除小学衔接与已归档（分析层不纳入 archived）。"""
        grades = {kp.grade for kp in self._kp.values()}
        target = max(grades)  # 主年级 = 图谱中最高年级
        return [
            kp.id
            for kp in self._kp.values()
            if kp.grade == target
            and not kp.code.startswith("C")
            and not getattr(kp, "archived", False)
        ]

    def suspect_edges(
        self,
        class_id: int,
        as_of: datetime,
        min_samples: int = 8,
        corr_threshold: float = 0.3,
    ) -> list[dict]:
        """可疑前置边反查（improvement-plan §2.2）。

        对每条 prerequisite 边 (from->to)，取班级学生在两端 kp 的掌握度，
        计算相关性。前置关系成立时两端应正相关；低相关且样本足够 -> 可疑
        （该边可能不成立，或被横切因素稀释）。仅统计两端均达
        MIN_EVIDENCE_COUNT 证据门槛的学生（与薄弱判定同纪律）。

        返回按 |corr| 升序（越接近 0 越可疑）的可疑边列表。
        """
        from app.config import MIN_EVIDENCE_COUNT
        from app.models import Student
        from app.pipeline.mastery import evidence_summary, mastery_at

        students = list(
            self._session.scalars(
                select(Student).where(Student.class_id == class_id)
            )
        )

        suspects: list[dict] = []
        for to_id, pres in self._prereq.items():
            for from_id, weight in pres:
                xs: list[float] = []
                ys: list[float] = []
                for stu in students:
                    sf = evidence_summary(self._session, stu.id, from_id, as_of)
                    if sf.count < MIN_EVIDENCE_COUNT:
                        continue
                    st = evidence_summary(self._session, stu.id, to_id, as_of)
                    if st.count < MIN_EVIDENCE_COUNT:
                        continue
                    m_from = mastery_at(self._session, stu.id, from_id, as_of)
                    m_to = mastery_at(self._session, stu.id, to_id, as_of)
                    if m_from is None or m_to is None:
                        continue
                    xs.append(m_from)
                    ys.append(m_to)
                if len(xs) < min_samples:
                    continue
                corr = _pearson(xs, ys)
                if corr is None:
                    continue
                if abs(corr) < corr_threshold:
                    suspects.append(
                        {
                            "from_code": self.kp(from_id).code,
                            "from_name": self.kp(from_id).name,
                            "to_code": self.kp(to_id).code,
                            "to_name": self.kp(to_id).name,
                            "weight": weight,
                            "n": len(xs),
                            "corr": round(corr, 3),
                        }
                    )
        suspects.sort(key=lambda e: abs(e["corr"]))
        return suspects


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """皮尔逊相关系数；样本不足或零方差返回 None。"""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / ((sx * sy) ** 0.5)
