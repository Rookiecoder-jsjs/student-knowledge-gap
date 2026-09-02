"""CODEX_HOME 首启播种(装车批第 3 步起;第 5 步后 sc MCP 为远程 url 形)。

背景:config.toml(deepseek provider + [mcp_servers.sc])渲染方自 gateway 启动时
自举(幂等)。装车批第 5 步后 sc MCP server 迁入 backend 容器(streamable-http 挂
/mcp),[mcp_servers.sc] 不再是「同容器 stdio 子进程 + school-authz-mcp shim」,
而是远程 `url = http://backend:8000/mcp` + `bearer_token_env_var`——codex 把网关
注入的教师 token(SC_SCHOOL_AUTH_TOKEN)以 Authorization 头逐请求发往 backend,
backend 验签后按教师过滤。gateway 容器不再挂 sc-data 卷,agent 物理不可达 sc.db。

幂等纪律:仅当 $CODEX_HOME/config.toml **缺失**时写入——管理员手写/后续改装的
配置永不覆盖。例外:检测到**旧 stdio 形**(含 school-authz-mcp 引用,第 3/4 批
形态)的既有配置,旋转为 `.pre-mcp-remote.bak` 后重渲染(否则 codex 会去 spawn
一个已不再 stage/播种的 shim)。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)

_REPLACEMENTS = ("CODEX_HOME", "DEEPSEEK_API_KEY")

# 旧 stdio 形 config.toml 的识别标记(装车批第 3/4 批 [mcp_servers.sc] command)
_STALE_STDIO_MARKER = "school-authz-mcp"


def render_config_toml(
    template: str,
    *,
    codex_home: str,
    api_key: str,
) -> str:
    """把模板占位符替换为运行时定值(缺占位符原样通过,不改模板语义)。"""
    for key, value in zip(_REPLACEMENTS, (codex_home, api_key), strict=True):
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


def seed_codex_home(
    codex_home: Path,
    assets_dir: Path,
    env: Mapping[str, str] | None = None,
) -> bool:
    """幂等播种:config.toml 缺失才渲染写入,models.json 缺失才拷入。

    返回 True=本次执行了播种(含旧形迁移);False=跳过(已有非旧形 config /
    资产缺失)。key 来源优先 SC_DEEPSEEK_API_KEY,回落 SC_LLM_API_KEY(与 backend
    共享的密钥),均缺则空 key 照渲染(壳可 initialize,首次真实 turn 前运维补 key
    或重播种)。
    """
    env = os.environ if env is None else env
    config_path = codex_home / "config.toml"
    if config_path.exists():
        try:
            existing = config_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        if _STALE_STDIO_MARKER in existing:
            bak = codex_home / "config.toml.pre-mcp-remote.bak"
            config_path.replace(bak)
            logger.warning(
                "[codex-home] 旧 stdio 形 config.toml(含 school-authz-mcp)已旋转为 "
                "%s,将按第 5 批 url 形重渲染", bak.name
            )
        else:
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
    if not env.get("SC_AUTH_SECRET"):
        logger.warning(
            "[codex-home] SC_AUTH_SECRET 未配置——sc MCP 无教师身份头(codex 不发 "
            "Authorization),开放模式匿名可读;安全模式(school-authz 收紧)下域工具 "
            "将被 backend 以 401 拒绝。生产请配置 SC_AUTH_SECRET 并重启网关"
        )
    return True
