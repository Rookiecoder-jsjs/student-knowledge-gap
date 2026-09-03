"""CODEX_HOME 首启播种(装车批第 3 步起;第 5 步后 sc MCP 为远程 url 形)。

背景:config.toml(deepseek provider + [mcp_servers.sc])渲染方自 gateway 启动时
自举(幂等)。装车批第 5 步后 sc MCP server 迁入 backend 容器(streamable-http 挂
/mcp),[mcp_servers.sc] 是远程 `url = http://backend:8000/mcp` + **条件**
`bearer_token_env_var`:SC_AUTH_SECRET 配置(安全模式)才渲染该键行——gateway 把
按教师签的 token(SC_SCHOOL_AUTH_TOKEN)注入子进程 env,codex 以 Authorization
头逐请求发往 backend,backend 验签后按教师过滤;SC_AUTH_SECRET 未配(开放模式)
→ 键整行省略 = codex 匿名不发头。省略是匿名**唯一**形态:codex 的 MCP client
对「引用到但 env 未设的 bearer 键」fail-closed 拒启整 server(0.149 容器实测),
留空/带键在 open 模式下都会让 sc MCP 起不来。gateway 容器不再挂 sc-data 卷,
agent 物理不可达 sc.db。

幂等纪律:仅当 $CODEX_HOME/config.toml **缺失**时写入——管理员手写/后续改装的
配置永不覆盖。例外:检测到**旧 stdio 形**(含 school-authz-mcp 引用,第 3/4 批
形态)的既有配置,旋转为 `.pre-mcp-remote.bak` 后重渲染(否则 codex 会去 spawn
一个已不再 stage/播种的 shim)。**模式翻转不自愈**:SC_AUTH_SECRET 在网关侧开关
后,已播种 config 的 bearer 键形态不变(幂等不覆盖),需删 `t<tid>/config.toml`
让下次 spawn 重播种。

装车批第 6 批:播种由网关**按驱动 home 惰性调用**——`main.py Bridge.spawn` 前
`_seed_driver_home(teacher_id)` 以 `CODEX_HOME/t<teacher_id>/` 为 codex_home 调本
模块(幂等);不再是启动时对单根目录播种一次(根仅为卷挂载点)。本模块 API 不变
(Path 参数化),天然适配每驱动一个子目录。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)


def _is_stale_stdio(text: str) -> bool:
    """旧 stdio 形 config.toml 判定：存在 `command = …school-authz-mcp` 配置行。

    装车批第 6 批前 marker 是裸子串 `"school-authz-mcp"`——但分发模板的**头注释**
    本身就含该词（说明 shim 退役），渲染出的新 config 在下次播种时被误判为旧形、
    旋转 .bak 重渲染（启动/每次 spawn 反复 churn）。收窄为只匹配实际配置行
    （`command` 键引用 shim），注释/说明文本不再命中。
    """
    return re.search(r"^\s*command\s*=.*school-authz-mcp", text, re.MULTILINE) is not None


def render_config_toml(
    template: str,
    *,
    codex_home: str,
    api_key: str,
    mcp_bearer_line: str = "",
) -> str:
    """把模板占位符替换为运行时定值(缺占位符原样通过,不改模板语义)。

    mcp_bearer_line: 渲染进 [mcp_servers.sc] 的 bearer 键行——安全模式传
    `bearer_token_env_var = "SC_SCHOOL_AUTH_TOKEN"`;默认空串 = 整键省略(codex
    匿名)。模式判定在 seed_codex_home(读 env SC_AUTH_SECRET)。
    """
    replacements = {
        "CODEX_HOME": codex_home,
        "DEEPSEEK_API_KEY": api_key,
        "MCP_BEARER_LINE": mcp_bearer_line,
    }
    for key, value in replacements.items():
        template = template.replace("{{" + key + "}}", value)
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
    或重播种)。bearer 键行是否渲染由 env SC_AUTH_SECRET 决定(与网关侧
    `_teacher_identity_env` 同源):配置→安全模式带键;未配→整键省略(开放匿名)。
    """
    env = os.environ if env is None else env
    config_path = codex_home / "config.toml"
    if config_path.exists():
        try:
            existing = config_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        if _is_stale_stdio(existing):
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
    secured = bool(env.get("SC_AUTH_SECRET"))
    mcp_bearer_line = (
        'bearer_token_env_var = "SC_SCHOOL_AUTH_TOKEN"' if secured else ""
    )
    codex_home.mkdir(parents=True, exist_ok=True)
    rendered = render_config_toml(
        template_path.read_text(encoding="utf-8"),
        codex_home=str(codex_home),
        api_key=api_key,
        mcp_bearer_line=mcp_bearer_line,
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
    if not secured:
        logger.warning(
            "[codex-home] SC_AUTH_SECRET 未配置——[mcp_servers.sc] 已省略 "
            "bearer_token_env_var,sc MCP 以匿名(无 Authorization 头)访问 backend;"
            "若 backend 端反而配置了 SC_AUTH_SECRET,域工具将被 401 拒绝。生产请 "
            "配置 SC_AUTH_SECRET 并重启网关"
        )
    return True
