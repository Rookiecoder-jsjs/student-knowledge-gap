"""codex_home 首启播种单测:占位替换 / 幂等 / key 解析 / 资产落盘。"""

from __future__ import annotations

from gateway import codex_home as ch

TEMPLATE = """\
model = "deepseek-v4-flash"
model_catalog_json = "{{CODEX_HOME}}/models.json"
[model_providers.deepseek]
experimental_bearer_token = "{{DEEPSEEK_API_KEY}}"
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
    assert 'args = ["--", "/usr/local/bin/python3", "/app/backend/app/mcp_server.py"]' in out
    assert "{{" not in out


def test_render_preserves_unknown_text():
    out = ch.render_config_toml(
        TEMPLATE.replace("{{SC_MCP_SERVER_SCRIPT}}", "keep-me"),
        codex_home="/h",
        api_key="",
    )
    assert "keep-me" in out
    assert 'model_catalog_json = "/h/models.json"' in out


def test_seed_writes_config_and_models(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "config.toml.template").write_text(TEMPLATE, encoding="utf-8")
    (assets / "models.json").write_text('{"models":[]}', encoding="utf-8")
    home = tmp_path / "codex-home"

    seeded = ch.seed_codex_home(home, assets, env={"SC_LLM_API_KEY": "lk"})

    assert seeded is True
    assert 'experimental_bearer_token = "lk"' in (home / "config.toml").read_text(encoding="utf-8")
    assert (home / "models.json").read_text(encoding="utf-8") == '{"models":[]}'


def test_seed_idempotent_never_overwrites(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "config.toml.template").write_text(TEMPLATE, encoding="utf-8")
    (assets / "models.json").write_text('{"v":"new"}', encoding="utf-8")
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text("admin-managed", encoding="utf-8")
    (home / "models.json").write_text('{"v":"old"}', encoding="utf-8")

    seeded = ch.seed_codex_home(home, assets)

    assert seeded is False
    assert (home / "config.toml").read_text(encoding="utf-8") == "admin-managed"
    assert (home / "models.json").read_text(encoding="utf-8") == '{"v":"old"}'


def test_seed_key_precedence_deepseek_over_llm(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "config.toml.template").write_text(TEMPLATE, encoding="utf-8")
    home = tmp_path / "codex-home"

    ch.seed_codex_home(home, assets, env={"SC_DEEPSEEK_API_KEY": "dk", "SC_LLM_API_KEY": "lk"})
    assert 'experimental_bearer_token = "dk"' in (home / "config.toml").read_text(encoding="utf-8")


def test_seed_no_key_still_renders(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "config.toml.template").write_text(TEMPLATE, encoding="utf-8")
    home = tmp_path / "codex-home"

    assert ch.seed_codex_home(home, assets, env={}) is True
    assert 'experimental_bearer_token = ""' in (home / "config.toml").read_text(encoding="utf-8")


def test_seed_missing_template_skips(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    home = tmp_path / "codex-home"

    assert ch.seed_codex_home(home, assets, env={}) is False
    assert not (home / "config.toml").exists()
