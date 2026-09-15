"""backend 与 gateway JSON formatter 的安全边界同步校验。

gateway 镜像自包含（不 COPY backend），脱敏清单 ``_SENSITIVE_KEY_PARTS`` 与
LogRecord 标准属性白名单 ``_STDLIB_ATTRS`` 是同一契约的两份拷贝。本测试解析
两侧源码的常量字面量并强制相等——一侧新增敏感字段而另一侧漏改时，这里红。
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SYNCED_CONSTANTS = ("_SENSITIVE_KEY_PARTS", "_STDLIB_ATTRS")


def _module_constants(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants: dict[str, object] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id in _SYNCED_CONSTANTS):
            continue
        value = node.value
        # 两侧 _STDLIB_ATTRS 写作 frozenset({...})，literal_eval 不支持该调用——
        # 取其首个参数（set 字面量）求值后按集合比较。
        if isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        constants[target.id] = ast.literal_eval(value)
    missing = [name for name in _SYNCED_CONSTANTS if name not in constants]
    assert not missing, f"{path} 缺少常量定义: {missing}"
    return constants


def test_sensitive_key_parts_and_stdlib_attrs_stay_in_sync():
    backend = _module_constants(_REPO_ROOT / "backend" / "app" / "observability.py")
    gateway = _module_constants(_REPO_ROOT / "gateway" / "observability.py")
    for name in _SYNCED_CONSTANTS:
        assert set(backend[name]) == set(gateway[name]), (
            f"{name} 在 backend 与 gateway 两侧不一致——"
            "脱敏清单是安全边界，任何一侧改动必须同步另一侧"
        )
