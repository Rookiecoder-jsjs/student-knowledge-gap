"""知识库域路由：导入 / 浏览 / 编辑 / 版本管理 / 导出（候选2 拆分）。

领域逻辑在 ``kb.edit`` / ``kb.versioning`` / ``kb.compatibility`` / ``kb.loader``，
本文件只做参数解析、依赖解析与异常翻译（_kb_http_error：404/409/400）。
"""

from __future__ import annotations

import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth as _auth
from app.api.deps import _active_kb, _graph, get_db, guard_class, require_kb_editor, require_teacher
from app.kb import edit as kb_edit
from app.kb import versioning as kb_ver
from app.kb.compatibility import compatibility
from app.kb.edit import KbEditError, KbNotFoundError
from app.kb.graph import KpGraph
from app.kb.resolver import active_kb
from app.ingestion.templates import suggest_question_tags
from app.kb.loader import KbImportError, import_kb
from app.models import Class, KbVersion, KnowledgePoint, KpRelation
from app.queries import kb as query_kb
from app.schemas import (
    KbImportRequest,
    KbVersionCreateRequest,
    KbVersionPatchRequest,
    KpCreateRequest,
    KpUpdateRequest,
    RelationCreateRequest,
    RelationUpdateRequest,
    SuggestQuestionRequest,
)
from app.upload_limits import MAX_FILE_BYTES, UploadTooLargeError, read_upload

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


def _kb_write_guard(db: Session, ctx, kb: KbVersion, *, govern: bool = False) -> None:
    """范围写权裁决（rbac-scopes-design §4）：403 翻译。govern=True 用治理权。"""
    ok = (
        _auth.can_govern_kb(db, ctx, kb.subject, kb.grade)
        if govern
        else _auth.can_write_kb(db, ctx, kb.subject, kb.grade)
    )
    if not ok:
        grade_txt = f"年级{kb.grade}" if kb.grade is not None else "全年级"
        raise HTTPException(
            403,
            f"知识库写权不含 {kb.subject}（{grade_txt}）：{kb.textbook_edition} v{kb.version}",
        )


def _kb_read_guard(db: Session, ctx, kb: KbVersion) -> None:
    """范围教师的 KB 读取裁决（普通教师/admin 保持全校可读语义）。"""
    if not _auth.can_read_kb(db, ctx, kb.subject, kb.grade):
        grade_txt = f"年级{kb.grade}" if kb.grade is not None else "全年级"
        raise HTTPException(
            403,
            f"知识库读取范围不含 {kb.subject}（{grade_txt}）："
            f"{kb.textbook_edition} v{kb.version}",
        )


def _kp_version_guard(db: Session, ctx, kp_id: int) -> None:
    """按知识点定位所属版本并做内容写权裁决（update/delete 等按 id 直改的入口）。"""
    kp = db.get(KnowledgePoint, kp_id)
    if kp is None:
        raise HTTPException(404, "知识点不存在")
    kb = db.get(KbVersion, kp.kb_version_id)
    if kb is None:
        raise HTTPException(404, "知识点所属版本不存在")
    _kb_write_guard(db, ctx, kb)


def _relation_version_guard(db: Session, ctx, rel_id: int) -> None:
    """按关系定位所属版本（from 端点 kp 的版本）并做内容写权裁决。"""
    rel = db.get(KpRelation, rel_id)
    if rel is None:
        raise HTTPException(404, "关系不存在")
    kp = db.get(KnowledgePoint, rel.from_kp_id)
    if kp is None:
        raise HTTPException(404, "关系所属知识点不存在")
    kb = db.get(KbVersion, kp.kb_version_id)
    if kb is None:
        raise HTTPException(404, "知识点所属版本不存在")
    _kb_write_guard(db, ctx, kb)


# ---------------------------------------------------------------------------
# 导入 / 上传
# ---------------------------------------------------------------------------


@router.post("/kb/import")
def kb_import(
    req: KbImportRequest,
    db: Session = Depends(get_db),
    ctx=Depends(require_kb_editor),
):
    try:
        kb = import_kb(db, req.yaml_path)
    except (KbImportError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    _kb_write_guard(db, ctx, kb)  # 越权导入 → 403（get_db 回滚已建版本）
    return {"kb_version_id": kb.id, "status": kb.status, "version": kb.version}


@router.post("/kb/upload")
async def kb_upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    ctx=Depends(require_kb_editor),
):
    """浏览器直接上传知识库 YAML（无需服务器文件系统访问）。"""
    try:
        raw = await read_upload(file, max_bytes=MAX_FILE_BYTES, label="知识库文件")
    except UploadTooLargeError as e:
        raise HTTPException(413, str(e)) from e
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        kb = import_kb(db, tmp_path)
    except Exception as e:
        raise HTTPException(400, f"知识库导入失败: {e}")
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    _kb_write_guard(db, ctx, kb)  # 越权导入 → 403（get_db 回滚已建版本）
    return {"kb_version_id": kb.id, "status": kb.status, "version": kb.version}


# ---------------------------------------------------------------------------
# 浏览
# ---------------------------------------------------------------------------


@router.get("/kb/versions")
def list_kb_versions(
    db: Session = Depends(get_db),
    ctx=Depends(require_teacher),
):
    """列知识库版本（kb-edit §4.1）。聚合在 queries.kb（N+1 → 一次 group_by）。

    RBAC 读取面（rbac-scopes-design §5）：持有学科×年级授权者只看授权学科；
    admin / kb_editor / 普通教师 / 开放模式不受限（KB 读=全校，既有语义）。
    """
    versions = query_kb.kb_versions_list(db)
    scopes = _auth.subject_scopes(db, ctx)
    if scopes:
        versions = [
            v for v in versions
            if _auth.can_read_kb(db, ctx, v.get("subject"), v.get("grade"))
        ]
    return {"versions": versions}


@router.get("/kb/kps")
def list_kps(
    kb_version_id: int | None = None,
    class_id: int | None = None,
    offset: int = 0,
    limit: int = 200,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """知识库全部知识点（完整字段）。缺省取 active；?kb_version_id= 查指定版本。

    供向导进度勾选 / 审核台闭集选择器 / 知识库浏览页使用。
    """
    if class_id is not None:
        clazz = db.get(Class, class_id)
        if clazz is None:
            raise HTTPException(404, "班级不存在")
        guard_class(class_id, db, ctx)
        if kb_version_id is not None:
            kb = db.get(KbVersion, kb_version_id)
            if kb is None:
                raise HTTPException(404, "知识库版本不存在")
            expected = _auth.class_subject(db, ctx, clazz)
            if kb.subject != expected:
                raise HTTPException(400, "知识库版本与班级学科不匹配")
            if kb.grade is not None and kb.grade != clazz.grade:
                raise HTTPException(400, "知识库版本与班级年级不匹配")
        else:
            kb = _active_kb(db, _auth.class_subject(db, ctx, clazz))
    elif kb_version_id is not None:
        kb = db.get(KbVersion, kb_version_id)
        if kb is None:
            raise HTTPException(404, "知识库版本不存在")
    else:
        kb = _active_kb(db)
    _kb_read_guard(db, ctx, kb)
    if offset < 0:
        raise HTTPException(400, "offset 不能小于 0")
    if not 1 <= limit <= 200:
        raise HTTPException(400, "limit 必须在 1 到 200 之间")
    total = db.scalar(
        select(func.count(KnowledgePoint.id)).where(
            KnowledgePoint.kb_version_id == kb.id
        )
    ) or 0
    rows = db.scalars(
        select(KnowledgePoint)
        .where(KnowledgePoint.kb_version_id == kb.id)
        .order_by(KnowledgePoint.code)
        .offset(offset)
        .limit(limit)
    )
    kps = [_kp_brief(k) for k in rows]
    return {
        "kb_version_id": kb.id,
        "kps": kps,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(kps) < total,
    }


@router.get("/kb/kps/{kp_id}")
def kp_detail(
    kp_id: int,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """单知识点详情：属性 + 前置链 + 直接前置 + 后继 + contains 关系（kb-edit §4.1）。

    聚合实现在 ``app.mcp_tools.get_kp_detail``（Agent 工具面共用一份，不复制）；
    本端点只做 id 定位与 HTTP 异常翻译。图按知识点「所属版本」构建——
    draft 版本的知识点此前因 active 建图读不出关系（思维导图建库验收发现）。
    """
    from app.mcp_tools import get_kp_detail as kp_detail_impl

    kp = db.get(KnowledgePoint, kp_id)
    if kp is None:
        raise HTTPException(404, f"知识点 {kp_id} 不存在")
    kb = db.get(KbVersion, kp.kb_version_id)
    if kb is None:
        raise HTTPException(404, "知识点所属版本不存在")
    _kb_read_guard(db, ctx, kb)
    try:
        return kp_detail_impl(db, _graph(db, kp.kb_version_id), kp_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.get("/kb/relations")
def list_relations(
    kb_version_id: int | None = None,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """关系列表，按端点 kp 归属版本过滤（隐式版本隔离，kb-edit §4.1/§6.3）。"""
    if kb_version_id is not None:
        kb = db.get(KbVersion, kb_version_id)
        if kb is None:
            raise HTTPException(404, "知识库版本不存在")
    else:
        kb = _active_kb(db)
    _kb_read_guard(db, ctx, kb)
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
def create_kp(
    req: KpCreateRequest,
    db: Session = Depends(get_db),
    ctx=Depends(require_kb_editor),
):
    """新建知识点。缺省落 active 版本（旧行为）；显式 kb_version_id 可写入
    draft/reviewed 版本（图形化建库向导走这条路）——active 版本拒绝直写，
    治理上改启用版须走 fork→改→审→启用。code 同版本唯一（uq_kb_code +
    IntegrityError 兜底）。"""
    if req.kb_version_id is not None:
        kb = db.get(KbVersion, req.kb_version_id)
        if kb is None:
            raise HTTPException(404, "知识库版本不存在")
        if kb.status == "active":
            raise HTTPException(
                400, "不能直接写入启用中的版本；请先「基于当前版修订」出草稿"
            )
    else:
        kb = _active_kb(db)
    _kb_write_guard(db, ctx, kb)
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
    ctx=Depends(require_kb_editor),
):
    """改属性（不允许改 code）。〔v0.2〕改 mastery_floor/difficulty_prior 支持 ?preview=true 影响预览。"""
    _kp_version_guard(db, ctx, kp_id)
    by = ctx.teacher.name if ctx.teacher is not None else "开放模式"
    try:
        kp, impact, previewed = kb_edit.update_kp(
            db,
            kp_id,
            by=by,
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
    kp_id: int,
    force: bool = False,
    confirm: bool = False,
    db: Session = Depends(get_db),
    ctx=Depends(require_kb_editor),
):
    """软归档（默认）/ 硬删（force=true）。引用预检见 kb-edit §5。"""
    _kp_version_guard(db, ctx, kp_id)
    by = ctx.teacher.name if ctx.teacher is not None else "开放模式"
    try:
        return kb_edit.delete_kp(db, kp_id, force=force, confirm=confirm, by=by)
    except KbEditError as e:
        raise _kb_http_error(e)


@router.post("/kb/relations")
def create_relation(
    req: RelationCreateRequest,
    db: Session = Depends(get_db),
    ctx=Depends(require_kb_editor),
):
    """新建关系：校验 type/weight/同版本/非自环（kb-edit §4.4/§6.3）。

    显式 kb_version_id 可写入 draft/reviewed 版本（思维导图建库在草稿内连线，
    与 create_kp 的向导路径同语义）；缺省落 active；active 版本拒绝直写。
    """
    if req.kb_version_id is not None:
        kb = db.get(KbVersion, req.kb_version_id)
        if kb is None:
            raise HTTPException(404, "知识库版本不存在")
        if kb.status == "active":
            raise HTTPException(
                400, "不能直接写入启用中的版本；请先「基于当前版修订」出草稿"
            )
    else:
        kb = _active_kb(db)
    _kb_write_guard(db, ctx, kb)
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
def update_relation(
    rel_id: int,
    req: RelationUpdateRequest,
    db: Session = Depends(get_db),
    ctx=Depends(require_kb_editor),
):
    _relation_version_guard(db, ctx, rel_id)
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
def delete_relation(
    rel_id: int,
    db: Session = Depends(get_db),
    ctx=Depends(require_kb_editor),
):
    _relation_version_guard(db, ctx, rel_id)
    try:
        kb_edit.delete_relation(db, rel_id)
    except KbEditError as e:
        raise _kb_http_error(e)
    return {"deleted": rel_id}


# ---------------------------------------------------------------------------
# 版本管理 + 导出（kb-edit §4.5/§4.6）
# ---------------------------------------------------------------------------


@router.post("/kb/versions")
def fork_kb_version(
    source_version_id: int | None = None,
    db: Session = Depends(get_db),
    ctx=Depends(require_kb_editor),
):
    """fork 为草稿新版本：复制其 kp（含 archived）+ 关系（kb-edit §4.5/§6.3）。

    缺省源 = 全局 active（旧行为）；多学科页显式传 source_version_id=当前浏览
    版本——fork 源跟学科走，不串。
    """
    if source_version_id is not None:
        src = db.get(KbVersion, source_version_id)
        if src is None:
            raise HTTPException(404, "版本不存在")
    else:
        src = _active_kb(db)
    _kb_write_guard(db, ctx, src)
    new = kb_ver.fork_kb_version(db, src)
    return {"id": new.id, "status": new.status, "forked_from": src.id}


@router.post("/kb/versions/create")
def create_kb_version(
    req: KbVersionCreateRequest,
    db: Session = Depends(get_db),
    ctx=Depends(require_kb_editor),
):
    """图形化建库第一步：创建空白草稿版本（多学科：subject 显式指定）。

    与 fork（POST /kb/versions）路径区分；内容经 POST /kb/kps?kb_version_id=
    逐条写入，或 YAML 导入走既有通道。
    """
    if not _auth.can_write_kb(db, ctx, req.subject.strip(), req.grade):
        raise HTTPException(403, f"知识库写权不含学科「{req.subject.strip()}」")
    kb = KbVersion(
        subject=req.subject.strip(),
        grade=req.grade,
        textbook_edition=req.textbook_edition.strip(),
        version=req.version.strip(),
        status="draft",
    )
    db.add(kb)
    db.flush()
    return {
        "id": kb.id,
        "subject": kb.subject,
        "grade": kb.grade,
        "version": kb.version,
        "status": kb.status,
    }


@router.get("/kb/versions/{version_id}/compatibility")
def kb_compatibility(
    version_id: int,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """与**同学科**当前 active 的 code 差集 + 〔v0.2〕属性 diff（切换前预览）。

    多学科口径：全局 active 会跨学科比错对象；同学科无 active（首激活）→
    空差集（没有可失联的旧证据）。
    """
    target = db.get(KbVersion, version_id)
    if target is None:
        raise HTTPException(404, "版本不存在")
    _kb_read_guard(db, ctx, target)
    active = active_kb(db, target.subject)
    if active is None or active is target:
        return {
            "active_version_id": active.id if active is not None else None,
            "target_version_id": target.id,
            "missing_codes": [],
            "attribute_changes": [],
        }
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
    ctx=Depends(require_kb_editor),
):
    """改 status：draft->reviewed->active。切 active 做超集 + 〔v0.2〕属性 diff 校验（§6.1/§6.2/§6.5）。

    RBAC 范围（rbac-scopes-design §4）：draft→reviewed=内容层（can_write_kb）；
    active=治理层（can_govern_kb——学科管理员可启用本学科版本；开放模式匿名
    放行——bootstrap 向导依赖）。
    """
    target = db.get(KbVersion, version_id)
    if target is None:
        raise HTTPException(404, "版本不存在")
    if req.status not in ("draft", "reviewed", "active"):
        raise HTTPException(400, "非法 status")
    _kb_write_guard(db, ctx, target, govern=(req.status == "active"))

    if req.status != "active":
        target.status = req.status
        db.flush()
        return {"id": target.id, "status": target.status}

    # 多学科口径：被替代的旧 active 按**同学科**解析（全局解析会把别的学科降级）；
    # 同学科无 active（含目标即学科内最新版）→ None = 首激活语义（跳过兼容对照）
    active = active_kb(db, target.subject)
    if active is target:
        active = None
    try:
        return kb_ver.activate_kb_version(
            db, target, active, force=force, confirm=confirm
        )
    except KbEditError as e:
        raise _kb_http_error(e)


@router.get("/kb/export")
def export_kb(
    kb_version_id: int | None = None,
    ctx=Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """从 DB 现状生成 YAML（对齐 loader 可读回，kb-edit §4.6）。"""
    if kb_version_id is not None:
        kb = db.get(KbVersion, kb_version_id)
        if kb is None:
            raise HTTPException(404, "版本不存在")
    else:
        kb = _active_kb(db)
    _kb_read_guard(db, ctx, kb)
    yaml_text = kb_ver.export_kb_yaml(db, kb)
    return Response(
        content=yaml_text,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="kb-v{kb.id}.yaml"'},
    )
