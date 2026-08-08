"""知识库 YAML 导入器（DESIGN §4 构建流程第 3 步）。

校验规则：编码唯一、关系端点存在、type 合法、weight ∈ [0,1]。
内容真正变化才生成新 kb_version（版本化原则：历史数据可追溯）；
同内容重复导入幂等返回既有版本 —— 否则旧考试的标注/证据会指向
旧版本 id，而 _active_kb 取最新版本，导致分析断链（KeyError）。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KbVersion, KnowledgePoint, KpRelation

RELATION_TYPES = {"prerequisite", "contains", "confusable", "spiral"}


class KbImportError(ValueError):
    """知识库 YAML 校验失败。"""


def import_kb(session: Session, yaml_path: str | Path) -> KbVersion:
    """解析并校验 YAML，写入数据库，返回 kb_version。"""
    data = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    meta = data.get("meta", {})
    points = data.get("knowledge_points", [])
    relations = data.get("relations", [])

    # ---- 校验：编码唯一 ----
    codes = [p["code"] for p in points]
    dup = {c for c in codes if codes.count(c) > 1}
    if dup:
        raise KbImportError(f"知识点编码重复: {sorted(dup)}")

    # ---- 幂等：同版本同内容直接返回既有 kb_version ----
    candidates = session.scalars(
        select(KbVersion).where(
            KbVersion.subject == meta.get("subject", "数学"),
            KbVersion.textbook_edition == meta.get("textbook_edition", "未知"),
            KbVersion.version == meta.get("version", "0.1.0"),
        )
    )
    want = set(codes)
    for kb in candidates:
        have = {
            row[0]
            for row in session.execute(
                select(KnowledgePoint.code).where(
                    KnowledgePoint.kb_version_id == kb.id
                )
            )
        }
        if have == want:
            return kb

    kb = KbVersion(
        subject=meta.get("subject", "数学"),
        textbook_edition=meta.get("textbook_edition", "未知"),
        version=meta.get("version", "0.1.0"),
        status=meta.get("status", "draft"),
    )
    session.add(kb)
    session.flush()  # 取得 kb.id

    code_to_id: dict[str, int] = {}
    for p in points:
        _validate_point(p)
        kp = KnowledgePoint(
            kb_version_id=kb.id,
            code=p["code"],
            name=p["name"],
            description=p.get("description", ""),
            grade=p["grade"],
            semester=p.get("semester", 0),
            chapter=p.get("chapter", ""),
            cog_levels_expected=p.get("cog_levels_expected", []),
            difficulty_prior=p.get("difficulty_prior", 0.5),
            mastery_floor=p.get("mastery_floor", 0.6),
            importance=p.get("importance", "核心"),
            archived=p.get("archived", False),
        )
        session.add(kp)
        session.flush()
        code_to_id[p["code"]] = kp.id

    for r in relations:
        if r["type"] not in RELATION_TYPES:
            raise KbImportError(f"非法关系类型: {r['type']}")
        if r["from"] not in code_to_id or r["to"] not in code_to_id:
            raise KbImportError(f"关系端点不存在: {r['from']} → {r['to']}")
        weight = r.get("weight", 1.0)
        if not 0.0 <= weight <= 1.0:
            raise KbImportError(f"关系权重越界: {r['from']}→{r['to']} weight={weight}")
        session.add(
            KpRelation(
                from_kp_id=code_to_id[r["from"]],
                to_kp_id=code_to_id[r["to"]],
                type=r["type"],
                weight=weight,
                audit_status="draft",
            )
        )

    session.flush()
    return kb


def _validate_point(p: dict) -> None:
    for field in ("code", "name", "grade"):
        if field not in p:
            raise KbImportError(f"知识点缺少必填字段 {field}: {p}")
    for level in p.get("cog_levels_expected", []):
        if level not in ("识记", "理解", "应用", "综合"):
            raise KbImportError(f"非法认知层级 {level}（{p['code']}）")
    importance = p.get("importance", "核心")
    if importance not in ("基础", "核心", "拓展"):
        raise KbImportError(f"非法重要度 {importance}（{p['code']}）：基础/核心/拓展")
