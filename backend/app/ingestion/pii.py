"""PII 剥离前置（DESIGN §5/§13）：学生卷送 LLM 前遮盖姓名/班级栏。

启发式：姓名栏几乎总在页面顶部条带，默认遮盖顶部 12%。
这是缓解而非保证——配合内部只流转 student_id、审核台可见原图。
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

DEFAULT_MASK_RATIO = 0.12


def mask_image(image_bytes: bytes, ratio: float = DEFAULT_MASK_RATIO) -> bytes:
    """遮盖图片顶部条带，返回 JPEG 字节。"""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w, int(h * ratio)], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
