"""codex_home 首启播种单测:占位替换 / 幂等 / 旧形迁移 / key 解析 / 资产落盘。"""

from __future__ import annotations

from gateway import codex_home as ch

TEMPLATE = """\
model = "deepseek-v4-flash"
model_catalog_json = "{{CODEX_HOME}}/models.json"
[model_providers.deepseek]
experimental_bearer_token = "{{DEEPSEEK_API_KEY}}"
[mcp_servers.sc]
url = "http://backend:8000/mcp"
bearer_token_env_var = "SC_SCHOOL_AUTH_TOKEN"
"""

STALE_TEMPLATE = """\
[mcp_servers.sc]
command = "school-authz-mcp"
args = ["--", "{{SC_MCP_PYTHON}}", "{{SC_MCP_SERVER_SCRIPT}}"]
"""


def test_render_substitutes_all_placeholders():
    out = ch.render_config_toml(
        TEMPLATE,
        codex_home="/data/codex-home",
        api_key="secret-key",
    )
    assert 'model_catalog_json = "/data/codex-home/models.json"' in out
    assert 'experimental_bearer_token = "secret-key"' in out
    assert 'url = "http://backend:8000/mcp"' in out
    assert "bearer_token_env_var = \"SC_SCHOOL_AUTH_TOKEN\"" in out
    assert "{{" not in out


def test_render_preserves_unknown_text():
    out = ch.render_config_toml(
        TEMPLATE.replace("bearer_token_env_var = \"SC_SCHOOL_AUTH_TOKEN\"", "keep-me"),
        codex_home="/h",
        api_key="",
    )
    assert "keep-me" in out
    assert 'model_catalog_json = "/h/models.json"' in out


def _assets(tmp_path) -> object:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "config.toml.template").write_text(TEMPLATE, encoding="utf-8")
    (assets / "models.json").write_text('{"models":[]}', encoding="utf-8")
    return assets


def test_seed_writes_config_and_models(tmp_path):
    assets = _assets(tmp_path)
    home = tmp_path / "codex-home"

    seeded = ch.seed_codex_home(home, assets, env={"SC_LLM_API_KEY": "lk"})

    assert seeded is True
    assert 'experimental_bearer_token = "lk"' in (home / "config.toml").read_text(encoding="utf-8")
    assert (home / "models.json").read_text(encoding="utf-8") == '{"models":[]}'


def test_seed_idempotent_never_overwrites(tmp_path):
    assets = _assets(tmp_path)
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text("admin-managed", encoding="utf-8")
    (home / "models.json").write_text('{"v":"old"}', encoding="utf-8")

    seeded = ch.seed_codex_home(home, assets)

    assert seeded is False
    assert (home / "config.toml").read_text(encoding="utf-8") == "admin-managed"
    assert (home / "models.json").read_text(encoding="utf-8") == '{"v":"old"}'


def test_seed_migrates_stale_stdio_config(tmp_path):
    """旧 stdio 形(含 school-authz-mcp)既有配置 → 旋转 .bak + 重渲染 url 形。"""
    assets = _assets(tmp_path)
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(STALE_TEMPLATE, encoding="utf-8")

    seeded = ch.seed_codex_home(home, assets, env={"SC_LLM_API_KEY": "lk"})

    assert seeded is True
    assert (home / "config.toml.pre-mcp-remote.bak").read_text(encoding="utf-8") == STALE_TEMPLATE
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "school-authz-mcp" not in text
    assert 'url = "http://backend:8000/mcp"' in text


def test_seed_key_precedence_deepseek_over_llm(tmp_path):
    assets = _assets(tmp_path)
    home = tmp_path / "codex-home"

    ch.seed_codex_home(home, assets, env={"SC_DEEPSEEK_API_KEY": "dk", "SC_LLM_API_KEY": "lk"})
    assert 'experimental_bearer_token = "dk"' in (home / "config.toml").read_text(encoding="utf-8")


def test_seed_no_key_still_renders(tmp_path):
    assets = _assets(tmp_path)
    home = tmp_path / "codex-home"

    assert ch.seed_codex_home(home, assets, env={}) is True
    assert 'experimental_bearer_token = ""' in (home / "config.toml").read_text(encoding="utf-8")


def test_seed_missing_template_skips(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    home = tmp_path / "codex-home"

    assert ch.seed_codex_home(home, assets, env={}) is False
    assert not (home / "config.toml").exists()


# 分发模板的头注释本身含 "school-authz-mcp"（说明 shim 退役）——第 6 批前裸子串
# marker 会把自身渲染结果误判为旧形、反复旋转 .bak。真实模板形：
COMMENTED_TEMPLATE = """\
# [mcp_servers.sc] url + bearer_token_env_var;school-authz-mcp shim 第 5 批退役。
model = "deepseek-v4-flash"
model_catalog_json = "{{CODEX_HOME}}/models.json"
[mcp_servers.sc]
url = "http://backend:8000/mcp"
bearer_token_env_var = "SC_SCHOOL_AUTH_TOKEN"
"""


def test_is_stale_stdio_only_matches_command_line():
    assert ch._is_stale_stdio("comment about school-authz-mcp 退役\nurl = \"x\"") is False
    assert ch._is_stale_stdio('command = "school-authz-mcp"') is True
    assert ch._is_stale_stdio('args = ["school-authz-mcp"]') is False


def test_seed_idempotent_over_own_rendered_config(tmp_path):
    """装车批第 6 批回归：自身渲染结果（注释含 shim 字样）在下次播种不被旋转。"""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "config.toml.template").write_text(COMMENTED_TEMPLATE, encoding="utf-8")
    (assets / "models.json").write_text('{"models":[]}', encoding="utf-8")
    home = tmp_path / "codex-home"

    assert ch.seed_codex_home(home, assets, env={"SC_LLM_API_KEY": "lk"}) is True
    first = (home / "config.toml").read_text(encoding="utf-8")

    assert ch.seed_codex_home(home, assets, env={"SC_LLM_API_KEY": "lk"}) is False  # 幂等跳过
    assert (home / "config.toml").read_text(encoding="utf-8") == first
    assert not (home / "config.toml.pre-mcp-remote.bak").exists()
