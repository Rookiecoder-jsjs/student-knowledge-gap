"""知识库域路由：导入 / 浏览 / 编辑 / 版本管理 / 导出（候选2 拆分）。

领域逻辑在 ``kb.edit`` / ``kb.versioning`` / ``kb.compatibility`` / ``kb.loader``，
本文件只做参数解析、依赖解析与异常翻译（_kb_http_error：404/409/400）。
"""

from __future__ import annotations

import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import _active_kb, _graph, get_db
from app.kb import edit as kb_edit
from app.kb import versioning as kb_ver
from app.kb.compatibility import compatibility
from app.kb.edit import KbEditError, KbNotFoundError
from app.kb.graph import KpGraph
from app.ingestion.templates import suggest_question_tags
from app.kb.loader import KbImportError, import_kb
from app.models import KbVersion, KnowledgePoint, KpRelation
from app.queries import kb as query_kb
from app.schemas import (
    KbImportRequest,
    KbVersionPatchRequest,
    KpCreateRequest,
    KpUpdateRequest,
    RelationCreateRequest,
    RelationUpdateRequest,
    SuggestQuestionRequest,
)

router = APIRouter()


def _kp_brief(k: KnowledgePoint) -> dict:
    """知识点完整字段（浏览 / 前端详情用）。"""
    return {
        "id": k.id,
        "code": k.code,
        "name": k.name,
        "description": k.description,
        "grade": k.grade,
        "semester": k.semester,
        "chapter": k.chapter,
        "cog_levels_expected": k.cog_levels_expected or [],
        "difficulty_prior": k.difficulty_prior,
        "mastery_floor": k.mastery_floor,
        "importance": k.importance,
        "archived": k.archived,
    }


def _kp_node(graph: KpGraph, kid: int) -> dict:
    """关系端点的极简节点视图（按主键回查，跨版本兜底）。"""
    k = graph.kp(kid)
    return {"id": kid, "code": k.code, "name": k.name}


def _kb_http_error(e: KbEditError) -> HTTPException:
    """领域异常 → HTTP 状态码：NotFound 404 / ConfirmRequired 409 / 其余 400。"""
    if isinstance(e, KbNotFoundError):
        return HTTPException(404, str(e))
    if isinstance(e, kb_edit.KbConfirmRequiredError):
        return HTTPException(409, str(e))
    return HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# 导入 / 上传
# ---------------------------------------------------------------------------


@router.post("/kb/import")
def kb_import(req: KbImportRequest, db: Session = Depends(get_db)):
    try:
        kb = import_kb(db, req.yaml_path)
    except (KbImportError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    return {"kb_version_id": kb.id, "status": kb.status, "version": kb.version}


@router.post("/kb/upload")
async def kb_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """浏览器直接上传知识库 YAML（无需服务器文件系统访问）。"""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        kb = import_kb(db, tmp_path)
    except Exception as e:
        raise HTTPException(400, f"知识库导入失败: {e}")
    return {"kb_version_id": kb.id, "status": kb.status, "version": kb.version}


# ---------------------------------------------------------------------------
# 浏览
# ---------------------------------------------------------------------------


@router.get("/kb/versions")
def list_kb_versions(db: Session = Depends(get_db)):
    """列全部知识库版本（kb-edit §4.1）。聚合在 queries.kb（N+1 → 一次 group_by）。"""
    return {"versions": query_kb.kb_versions_list(db)}


@router.get("/kb/kps")
def list_kps(kb_version_id: int | None = None, db: Session = Depends(get_db)):
    """知识库全部知识点（完整字段）。缺省取 active；?kb_version_id= 查指定版本。

    供向导进度勾选 / 审核台闭集选择器 / 知识库浏览页使用。
    """
    if kb_version_id is not None:
        kb = db.get(KbVersion, kb_version_id)
        if kb is None:
            raise HTTPException(404, "知识库版本不存在")
    else:
        kb = _active_kb(db)
    rows = db.scalars(
        select(KnowledgePoint)
        .where(KnowledgePoint.kb_version_id == kb.id)
        .order_by(KnowledgePoint.code)
    )
    return {"kb_version_id": kb.id, "kps": [_kp_brief(k) for k in rows]}


@router.get("/kb/kps/{kp_id}")
def kp_detail(kp_id: int, db: Session = Depends(get_db)):
    """单知识点详情：属性 + 前置链 + 直接前置 + 后继 + contains 关系（kb-edit §4.1）。

    聚合实现在 ``app.mcp_tools.get_kp_detail``（Agent 工具面共用一份，不复制）；
    本端点只做 id 定位与 HTTP 异常翻译。
    """
    from app.mcp_tools import get_kp_detail as kp_detail_impl

    try:
        return kp_detail_impl(db, _graph(db, _active_kb(db).id), kp_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.get("/kb/relations")
def list_relations(kb_version_id: int | None = None, db: Session = Depends(get_db)):
    """关系列表，按端点 kp 归属版本过滤（隐式版本隔离，kb-edit §4.1/§6.3）。"""
    if kb_version_id is not None:
        kb = db.get(KbVersion, kb_version_id)
        if kb is None:
            raise HTTPException(404, "知识库版本不存在")
    else:
        kb = _active_kb(db)
    graph = _graph(db, kb.id)
    version_kp_ids = graph.kp_ids()
    out = []
    for rel in db.scalars(select(KpRelation).order_by(KpRelation.id)):
        if rel.from_kp_id not in version_kp_ids or rel.to_kp_id not in version_kp_ids:
            continue
        out.append(
            {
                "id": rel.id,
                "from": _kp_node(graph, rel.from_kp_id),
                "to": _kp_node(graph, rel.to_kp_id),
                "type": rel.type,
                "weight": rel.weight,
            }
        )
    return {"kb_version_id": kb.id, "relations": out}


# ---------------------------------------------------------------------------
# 知识点 / 关系 CRUD（kb-edit §4.3/§4.4；领域逻辑在 kb.edit，本层只做异常翻译）
# ---------------------------------------------------------------------------


@router.post("/kb/suggest-question-tags")
def suggest_question_tags_endpoint(req: SuggestQuestionRequest, db: Session = Depends(get_db)):
    """题干 -> 闭集知识点推荐（improvement-plan §3.3）。

    纯文本 LLM 推荐，不落库；教师审核修改后再 createExam/create_template。
    """
    kb = _active_kb(db)
    return suggest_question_tags(db, kb.id, [q.model_dump() for q in req.questions])


@router.post("/kb/kps")
def create_kp(req: KpCreateRequest, db: Session = Depends(get_db)):
    """新建知识点（属 active kb）。code 同版本唯一（uq_kb_code + IntegrityError 兜底）。"""
    kb = _active_kb(db)
    try:
        kp = kb_edit.create_kp(
            db,
            kb_version_id=kb.id,
            code=req.code,
            name=req.name,
            grade=req.grade,
            chapter=req.chapter,
            semester=req.semester,
            description=req.description,
            cog_levels_expected=req.cog_levels_expected,
            difficulty_prior=req.difficulty_prior,
            mastery_floor=req.mastery_floor,
            importance=req.importance,
        )
    except KbEditError as e:
        raise HTTPException(400, str(e))
    return _kp_brief(kp)


@router.patch("/kb/kps/{kp_id}")
def update_kp(
    kp_id: int,
    req: KpUpdateRequest,
    preview: bool = False,
    db: Session = Depends(get_db),
):
    """改属性（不允许改 code）。〔v0.2〕改 mastery_floor/difficulty_prior 支持 ?preview=true 影响预览。"""
    try:
        kp, impact, previewed = kb_edit.update_kp(
            db,
            kp_id,
            by="teacher",
            preview=preview,
            name=req.name,
            description=req.description,
            chapter=req.chapter,
            semester=req.semester,
            cog_levels_expected=req.cog_levels_expected,
            difficulty_prior=req.difficulty_prior,
            mastery_floor=req.mastery_floor,
            importance=req.importance,
            archived=req.archived,
        )
    except KbEditError as e:
        raise _kb_http_error(e)
    if previewed:
        return {"preview": True, **impact}
    return {**_kp_brief(kp), "impact": impact}


@router.delete("/kb/kps/{kp_id}")
def delete_kp(
    kp_id: int, force: bool = False, confirm: bool = False, db: Session = Depends(get_db)
):
    """软归档（默认）/ 硬删（force=true）。引用预检见 kb-edit §5。"""
    try:
        return kb_edit.delete_kp(db, kp_id, force=force, confirm=confirm)
    except KbEditError as e:
        raise _kb_http_error(e)


@router.post("/kb/relations")
def create_relation(req: RelationCreateRequest, db: Session = Depends(get_db)):
    """新建关系：校验 type/weight/同版本/非自环（kb-edit §4.4/§6.3）。"""
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    try:
        rel = kb_edit.create_relation(
            db,
            kb_version_id=kb.id,
            from_kp_id=req.from_kp_id,
            to_kp_id=req.to_kp_id,
            type=req.type,
            weight=req.weight,
        )
    except KbEditError as e:
        raise HTTPException(400, str(e))
    return {
        "id": rel.id,
        "from": _kp_node(graph, rel.from_kp_id),
        "to": _kp_node(graph, rel.to_kp_id),
        "type": rel.type,
        "weight": rel.weight,
    }


@router.patch("/kb/relations/{rel_id}")
def update_relation(rel_id: int, req: RelationUpdateRequest, db: Session = Depends(get_db)):
    try:
        rel = kb_edit.update_relation(db, rel_id, type=req.type, weight=req.weight)
    except KbEditError as e:
        raise _kb_http_error(e)
    kb = _active_kb(db)
    graph = _graph(db, kb.id)
    return {
        "id": rel.id,
        "from": _kp_node(graph, rel.from_kp_id),
        "to": _kp_node(graph, rel.to_kp_id),
        "type": rel.type,
        "weight": rel.weight,
    }


@router.delete("/kb/relations/{rel_id}")
def delete_relation(rel_id: int, db: Session = Depends(get_db)):
    try:
        kb_edit.delete_relation(db, rel_id)
    except KbEditError as e:
        raise _kb_http_error(e)
    return {"deleted": rel_id}


# ---------------------------------------------------------------------------
# 版本管理 + 导出（kb-edit §4.5/§4.6）
# ---------------------------------------------------------------------------


@router.post("/kb/versions")
def fork_kb_version(db: Session = Depends(get_db)):
    """fork 当前 active：复制其 kp（含 archived）+ 关系为草稿新版本（kb-edit §4.5/§6.3）。"""
    src = _active_kb(db)
    new = kb_ver.fork_kb_version(db, src)
    return {"id": new.id, "status": new.status, "forked_from": src.id}


@router.get("/kb/versions/{version_id}/compatibility")
def kb_compatibility(version_id: int, db: Session = Depends(get_db)):
    """与当前 active 的 code 差集 + 〔v0.2〕属性 diff（切换前预览）。"""
    target = db.get(KbVersion, version_id)
    if target is None:
        raise HTTPException(404, "版本不存在")
    active = _active_kb(db)
    return {
        "active_version_id": active.id,
        "target_version_id": target.id,
        **compatibility(db, active, target),
    }


@router.patch("/kb/versions/{version_id}")
def patch_kb_version(
    version_id: int,
    req: KbVersionPatchRequest,
    confirm: bool = False,
    force: bool = False,
    db: Session = Depends(get_db),
):
    """改 status：draft->reviewed->active。切 active 做超集 + 〔v0.2〕属性 diff 校验（§6.1/§6.2/§6.5）。"""
    target = db.get(KbVersion, version_id)
    if target is None:
        raise HTTPException(404, "版本不存在")
    if req.status not in ("draft", "reviewed", "active"):
        raise HTTPException(400, "非法 status")

    if req.status != "active":
        target.status = req.status
        db.flush()
        return {"id": target.id, "status": target.status}

    active = _active_kb(db)
    try:
        return kb_ver.activate_kb_version(
            db, target, active, force=force, confirm=confirm
        )
    except KbEditError as e:
        raise _kb_http_error(e)


@router.get("/kb/export")
def export_kb(kb_version_id: int | None = None, db: Session = Depends(get_db)):
    """从 DB 现状生成 YAML（对齐 loader 可读回，kb-edit §4.6）。"""
    if kb_version_id is not None:
        kb = db.get(KbVersion, kb_version_id)
        if kb is None:
            raise HTTPException(404, "版本不存在")
    else:
        kb = _active_kb(db)
    yaml_text = kb_ver.export_kb_yaml(db, kb)
    return Response(
        content=yaml_text,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="kb-v{kb.id}.yaml"'},
    )