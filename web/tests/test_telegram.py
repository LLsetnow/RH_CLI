from __future__ import annotations

from pathlib import Path

from web.telegram import TelegramNotifier


class FakeStore:
    def __init__(self, data: dict | None = None, task: dict | None = None) -> None:
        self.data = data or {}
        self._sent: set[tuple[str, str]] = set()
        self._task = task or {"workflow_name": "测试工作流"}

    def _read_json_file(self) -> dict:
        return dict(self.data)

    def task(self, task_id: str) -> dict:
        return {"id": task_id, **self._task}

    def telegram_delivery_sent(self, task_id: str, delivery_key: str) -> bool:
        return (task_id, delivery_key) in self._sent

    def mark_telegram_delivery_sent(self, task_id: str, delivery_key: str) -> None:
        self._sent.add((task_id, delivery_key))


def test_telegram_settings_never_exposes_bot_token():
    store = FakeStore({"telegram_bot_token": "123456:secret-token", "telegram_chat_id": "-1001", "telegram_enabled": True})

    settings = TelegramNotifier(store).settings()

    assert settings["configured"] is True
    assert settings["enabled"] is True
    assert settings["chat_id"] == "-1001"
    assert "123456:secret-token" not in str(settings)


def test_notify_task_routes_media_and_deduplicates(tmp_path: Path):
    output = tmp_path / "result.png"
    output.write_bytes(b"png")
    store = FakeStore(
        {"telegram_bot_token": "123456:secret-token", "telegram_chat_id": "-1001", "telegram_enabled": True}
    )
    notifier = TelegramNotifier(store)
    calls: list[tuple[str, dict, Path | None]] = []

    def fake_api_call(token, method, fields, file_field="", file_path=None):
        calls.append((method, {"file_field": file_field, **fields}, file_path))
        return {"ok": True}

    notifier._api_call = fake_api_call  # type: ignore[method-assign]
    saved = [{"kind": "file", "path": str(output), "name": "result.png", "mime": "image/png"}]

    assert notifier.notify_task("task-1", saved) == {"status": "sent", "sent": 1, "failed": 0}
    assert notifier.notify_task("task-1", saved) == {"status": "sent", "sent": 0, "failed": 0}
    assert calls[0][0] == "sendPhoto"
    assert calls[0][1]["file_field"] == "photo"
    assert calls[0][2] == output.resolve()


def test_disabled_telegram_does_not_send():
    store = FakeStore({"telegram_bot_token": "token", "telegram_chat_id": "chat", "telegram_enabled": False})
    notifier = TelegramNotifier(store)
    notifier._api_call = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not send"))  # type: ignore[method-assign]

    assert notifier.notify_task("task-1", []) == {"status": "disabled", "sent": 0, "failed": 0}
