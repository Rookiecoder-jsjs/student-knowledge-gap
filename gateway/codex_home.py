"""CODEX_HOME 首启播种(装车批第 3 步:runtime 魔改壳 + sc MCP 拓扑闭环)。

背景:config.toml(deepseek provider + [mcp_servers.sc])长期只存在于
assets/deepseek 模板,渲染方「装机向导」从未落库——换装前 sc 域工具实际
不可用。gateway 镜像把 runtime 壳二进制 + backend python 收进同一容器后,
本模块在 gateway 启动时自举一个可用的 CODEX_HOME:
从 deepseek 模板渲染 config.toml + 落 models.json,把容器内路径写成定值
(school-authz-mcp 在 PATH、python 在 /usr/local/bin、sc MCP server 脚本在
/app/backend/app/mcp_server.py)。

幂等纪律:仅当 $CODEX_HOME/config.toml **缺失**时写入——管理员手写/后续
改装的配置永不覆盖(与 accounts.example.json→accounts.json 的「模板兜底、
真实值优先」同调)。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)

# 容器内固定路径(镜像布局锁定;`python3` 与后端镜像同为 python:3.11-slim,
# mcp_server.py 以脚本路径直启,自身把 backend 父目录塞 sys.path 后 `import app`)
MCP_PYTHON = "/usr/local/bin/python3"
MCP_SERVER_SCRIPT = "/app/backend/app/mcp_server.py"

_REPLACEMENTS = (
    "CODEX_HOME",
    "DEEPSEEK_API_KEY",
    "SC_MCP_PYTHON",
    "SC_MCP_SERVER_SCRIPT",
)


def render_config_toml(
    template: str,
    *,
    codex_home: str,
    api_key: str,
    mcp_python: str = MCP_PYTHON,
    mcp_server_script: str = MCP_SERVER_SCRIPT,
) -> str:
    """把模板占位符替换为运行时定值(缺占位符原样通过,不改模板语义)。"""
    for key, value in zip(_REPLACEMENTS, (
        codex_home, api_key, mcp_python, mcp_server_script,
    ), strict=True):
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


def seed_codex_home(
    codex_home: Path,
    assets_dir: Path,
    env: Mapping[str, str] | None = None,
) -> bool:
    """幂等播种:config.toml 缺失才渲染写入,models.json 缺失才拷入。

    返回 True=本次执行了播种;False=跳过(已有 config / 资产缺失)。key 来源
    优先 SC_DEEPSEEK_API_KEY,回落 SC_LLM_API_KEY(与 backend 共享的密钥),
    均缺则空 key 照渲染(壳可 initialize,首次真实 turn 前运维补 key 或重播种)。
    """
    env = os.environ if env is None else env
    config_path = codex_home / "config.toml"
    if config_path.exists():
        return False

    template_path = assets_dir / "config.toml.template"
    if not template_path.exists():
        logger.warning("[codex-home] 模板缺失 %s,跳过播种", template_path)
        return False

    api_key = env.get("SC_DEEPSEEK_API_KEY") or env.get("SC_LLM_API_KEY") or ""
    codex_home.mkdir(parents=True, exist_ok=True)
    rendered = render_config_toml(
        template_path.read_text(encoding="utf-8"),
        codex_home=str(codex_home),
        api_key=api_key,
    )
    config_path.write_text(rendered, encoding="utf-8")
    models_src = assets_dir / "models.json"
    models_dst = codex_home / "models.json"
    if models_src.exists() and not models_dst.exists():
        shutil.copyfile(models_src, models_dst)
    if not api_key:
        logger.warning(
            "[codex-home] SC_DEEPSEEK_API_KEY/SC_LLM_API_KEY 均未配置,config.toml "
            "已用空 key 渲染——壳可启动,真实 turn 前请配置并重启网关"
        )
    return True
