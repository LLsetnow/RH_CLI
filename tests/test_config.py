from __future__ import annotations

from rh_cli.config import resolve_api_key


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
