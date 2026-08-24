"""批量拍照上传的文件策略（架构修复 候选2：从 routes 抽出的上传校验模块）。

- 校验：数量 / 单文件大小 / 总大小 / 图片有效性（verify + 统一 JPEG）；
- 落盘：``sc_batch_`` 前缀 delete=False tempfile（孤儿文件可被 batch.gc_orphan_tempfiles
  统一清扫）；
- sync 策略：``effective_sync`` 判断 sync=true 是否真的同步执行
  （mock / 显式开关走同步；生产默认异步防阻塞，见 DESIGN 批量录入 v0.3）。

领域层只抛 ``ValueError`` 子类；HTTP 状态码由路由层按类型翻译：
- ``UploadLimitError`` → 413（大小超限）；其余（含数量超限）→ 400。
"""

from __future__ import annotations

import io
import os
import tempfile

from PIL import Image

from app.config import settings
from app.llm.audit import unwrap
from app.llm.client import LLMError, MockLLMClient, get_client

MAX_FILES = 50
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024


class BatchUploadError(ValueError):
    """上传策略校验失败（拒绝性错误 → HTTP 400）。"""


class UploadLimitError(BatchUploadError):
    """文件大小超限 → HTTP 413。"""


def effective_sync(sync: bool) -> bool:
    """sync=true 仅在 mock / 显式开关生效，避免生产同步阻塞请求线程数十分钟。"""
    if not sync:
        return False
    if os.environ.get("SC_LLM_PROVIDER", "mock").lower() == "mock":
        return True
    try:
        if isinstance(unwrap(get_client("vision")), MockLLMClient):
            return True
    except LLMError:
        pass
    return bool(settings.allow_sync_batch)


def _cleanup_saved(saved: list[tuple[str, str]]) -> None:
    for _, p in saved:
        try:
            os.remove(p)
        except OSError:
            pass


def validate_and_persist(uploads: list[tuple[str, bytes]]) -> list[tuple[str, str]]:
    """校验图片类型/大小/数量，落 delete=False tempfile。

    入参 ``[(file_name, raw_bytes)]``（路由层负责读 UploadFile）；
    返回 ``[(file_name, tmp_path)]``；任一校验失败时清理已落文件后抛异常。
    """
    if not uploads:
        raise BatchUploadError("未上传任何文件")
    if len(uploads) > MAX_FILES:
        raise BatchUploadError(f"单批最多 {MAX_FILES} 张，本次 {len(uploads)} 张")
    saved: list[tuple[str, str]] = []
    try:
        total = 0
        for i, (name, raw) in enumerate(uploads):
            if len(raw) > MAX_FILE_BYTES:
                raise UploadLimitError(f"文件 {name} 超过单文件 10MB 上限")
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise UploadLimitError("整批超过 100MB 上限")
            try:
                Image.open(io.BytesIO(raw)).verify()
            except Exception:
                raise BatchUploadError(f"文件 {name} 不是有效图片或已损坏") from None
            # verify 会重置流，重新打开并统一为 JPEG（匹配 LLM 客户端 image/jpeg 媒体类型）
            img = Image.open(io.BytesIO(raw))
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            # G6：统一 sc_batch_ 前缀，便于孤儿文件清扫（batch.gc_orphan_tempfiles）
            tmp = tempfile.NamedTemporaryFile(
                delete=False, prefix="sc_batch_", suffix=".jpg"
            )
            tmp.write(buf.getvalue())
            tmp.close()
            saved.append(((name or f"file_{i}.jpg")[:200], tmp.name))
        return saved
    except Exception:
        _cleanup_saved(saved)
        raise