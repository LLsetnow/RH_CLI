from __future__ import annotations

import asyncio
from types import SimpleNamespace

from textual.widgets import Select, Static

from rh_cli import gui as gui_mod
from rh_cli.gui import ConfigApp, next_default_after_remove, validate_key_value


def test_validate_key_value_accepts_32_char():
    key = "a1b2c3d4e5f60718293a4b5c6d7e8f90"  # 32 chars
    assert validate_key_value(key) == key


def test_validate_key_value_rejects_short_long_placeholder():
    assert validate_key_value("short") is None
    assert validate_key_value("x" * 40) is None
    assert validate_key_value("your_api_key_here") is None
    assert validate_key_value("   ") is None
    assert validate_key_value("") is None


def test_next_default_keeps_default_when_other_removed():
    keys = {"a": "x" * 32, "b": "y" * 32}
    assert next_default_after_remove(keys, "a", "b") == "b"


def test_next_default_falls_back_when_default_removed():
    keys = {"a": "x" * 32, "b": "y" * 32, "c": "z" * 32}
    assert next_default_after_remove(keys, "a", "a") == "b"


def test_next_default_empty_when_all_removed():
    assert next_default_after_remove({}, "a", "a") == ""


# ---------------------------------------------------------------------------
# TUI 启动测试（headless run_test，monkeypatch 隔离真实配置）
# ---------------------------------------------------------------------------

def _patch_config(monkeypatch):
    key = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    monkeypatch.setattr(gui_mod, "read_default_site", lambda: "ai")
    monkeypatch.setattr(gui_mod, "list_keys", lambda: {"cn-rh": key, "ai-wallet": key})
    monkeypatch.setattr(gui_mod, "get_default_key_name", lambda: "ai-wallet")
    monkeypatch.setattr(gui_mod, "resolve_api_key",
                        lambda: SimpleNamespace(value=key, source="keys.ai-wallet"))
    monkeypatch.setattr(gui_mod, "save_default_site", lambda site: None)
    monkeypatch.setattr(gui_mod, "save_keys", lambda keys, default: None)


def test_config_app_mounts_and_shows_state(monkeypatch):
    _patch_config(monkeypatch)

    async def main():
        async with ConfigApp().run_test() as pilot:
            app = pilot.app
            assert "ai" in str(app.query_one("#site-current", Static).render())
            assert app.query_one("#key-select", Select).value == "ai-wallet"
            key_label = str(app.query_one("#key-current", Static).render())
            assert "来源：keys.ai-wallet" in key_label
            assert "a1b2****" in key_label  # 脱敏显示
    asyncio.run(main())


def test_config_app_radio_change_saves_site(monkeypatch):
    _patch_config(monkeypatch)
    saved: list[str] = []
    monkeypatch.setattr(gui_mod, "save_default_site", lambda site: saved.append(site))

    async def main():
        async with ConfigApp().run_test() as pilot:
            radios = list(pilot.app.query("RadioButton"))
            radios[1].value = True  # 模拟用户选中 cn，触发 RadioSet.Changed
            await pilot.pause()
            assert saved == ["cn"]
    asyncio.run(main())
