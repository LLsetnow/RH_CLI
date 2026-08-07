from __future__ import annotations

from rh_cli.config import read_default_site, resolve_api_key, save_default_site
from rh_cli.errors import RhCliError


def test_resolve_api_key_prefers_cli(monkeypatch):
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "env-key")
    resolved = resolve_api_key("cli-key")
    assert resolved.value == "cli-key"
    assert resolved.source == "cli"


def test_resolve_api_key_uses_env(monkeypatch):
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "env-key")
    resolved = resolve_api_key(None)
    assert resolved.value == "env-key"
    assert resolved.source == "env"


def test_resolve_api_key_ignores_placeholder(monkeypatch):
    monkeypatch.delenv("RUNNINGHUB_API_KEY", raising=False)
    resolved = resolve_api_key("RUNNINGHUB_API_KEY")
    assert resolved.value is None
    assert resolved.source == "none"


def _use_tmp_config(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"
    monkeypatch.setattr("rh_cli.config.config_path", lambda: target)
    return target


def test_read_default_site_defaults_to_cn(tmp_path, monkeypatch):
    _use_tmp_config(tmp_path, monkeypatch)
    assert read_default_site() == "cn"


def test_save_and_read_default_site(tmp_path, monkeypatch):
    target = _use_tmp_config(tmp_path, monkeypatch)
    path = save_default_site("ai")
    assert path == target
    assert read_default_site() == "ai"


def test_read_default_site_normalizes_case(tmp_path, monkeypatch):
    target = _use_tmp_config(tmp_path, monkeypatch)
    target.write_text('default_site = "AI"\n', encoding="utf-8")
    assert read_default_site() == "ai"


def test_save_default_site_invalid_raises(tmp_path, monkeypatch):
    _use_tmp_config(tmp_path, monkeypatch)
    try:
        save_default_site("jp")
    except RhCliError as exc:
        assert exc.code == "INVALID_SITE"
    else:
        raise AssertionError("expected INVALID_SITE error")
