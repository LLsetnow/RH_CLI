from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from web.telegram import (
    TelegramNotifier,
    TelegramRequestNotSentError,
    TelegramRequestOutcomeUnknownError,
)


class FakeStore:
    def __init__(self, data: dict | None = None, task: dict | None = None) -> None:
        self.data = data or {}
        self._sent: set[tuple[str, str]] = set()
        self._deliveries: dict[tuple[str, str], dict[str, object]] = {}
        self._delivery_lock = threading.Lock()
        self._task = task or {"workflow_name": "测试工作流"}

    def _read_json_file(self) -> dict:
        return dict(self.data)

    def task(self, task_id: str) -> dict:
        return {"id": task_id, **self._task}

    def telegram_delivery_sent(self, task_id: str, delivery_key: str) -> bool:
        return (task_id, delivery_key) in self._sent

    def mark_telegram_delivery_sent(self, task_id: str, delivery_key: str) -> None:
        self._sent.add((task_id, delivery_key))

    def claim_telegram_delivery(self, task_id, delivery_key, claim_id, *, lease_ms, allow_unknown=False):
        key = (task_id, delivery_key)
        now = int(time.time() * 1000)
        with self._delivery_lock:
            current = self._deliveries.get(key)
            if current:
                status = current["status"]
                if status == "sent":
                    return False
                if status == "unknown" and not allow_unknown:
                    return False
                if status == "sending" and int(current["claim_until"]) > now:
                    return False
            self._deliveries[key] = {
                "status": "sending",
                "claimed_by": claim_id,
                "claim_until": now + int(lease_ms),
                "attempts": int(current["attempts"]) + 1 if current else 1,
            }
            return True

    def finish_telegram_delivery(self, task_id, delivery_key, claim_id, status, error=""):
        key = (task_id, delivery_key)
        with self._delivery_lock:
            current = self._deliveries.get(key)
            if current and current.get("status") == "sending" and current.get("claimed_by") == claim_id:
                self._deliveries[key] = {
                    **current,
                    "status": status,
                    "claimed_by": "",
                    "claim_until": 0,
                    "error": error,
                }


def test_telegram_settings_never_exposes_bot_token():
    store = FakeStore({"telegram_bot_token": "123456:secret-token", "telegram_chat_id": "-1001", "telegram_enabled": True})

    settings = TelegramNotifier(store).settings()

    assert settings["configured"] is True
    assert settings["enabled"] is True
    assert settings["chat_id"] == "-1001"
    assert "123456:secret-token" not in str(settings)


def test_parse_chat_ids_trims_deduplicates_and_accepts_chinese_comma():
    assert TelegramNotifier.parse_chat_ids(" -1001，-1002, -1001, @channel ") == ["-1001", "-1002", "@channel"]


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
    assert calls[0][1]["caption"] == "测试工作流 · task-1"


def test_notify_task_sends_each_output_to_each_chat_and_deduplicates(tmp_path: Path):
    output = tmp_path / "result.png"
    output.write_bytes(b"png")
    store = FakeStore(
        {
            "telegram_bot_token": "123456:secret-token",
            "telegram_chat_id": "-1001, -1002",
            "telegram_enabled": True,
        }
    )
    notifier = TelegramNotifier(store)
    calls: list[str] = []
    notifier._api_call = lambda token, method, fields, file_field="", file_path=None: (calls.append(fields["chat_id"]) or {"ok": True})  # type: ignore[method-assign]
    saved = [{"kind": "file", "path": str(output), "name": "result.png", "mime": "image/png"}]

    assert notifier.notify_task("task-1", saved) == {"status": "sent", "sent": 2, "failed": 0}
    assert notifier.notify_task("task-1", saved) == {"status": "sent", "sent": 0, "failed": 0}
    assert calls == ["-1001", "-1002"]


def test_test_connection_sends_to_each_configured_chat():
    store = FakeStore({"telegram_bot_token": "token", "telegram_chat_id": "-1001, -1002"})
    notifier = TelegramNotifier(store)
    calls: list[str] = []
    notifier._api_call = lambda token, method, fields, file_field="", file_path=None: (calls.append(fields["chat_id"]) or {"ok": True})  # type: ignore[method-assign]

    result = notifier.test_connection()

    assert result["ok"] is True
    assert "2 个" in result["message"]
    assert calls == ["-1001", "-1002"]


def test_disabled_telegram_does_not_send():
    store = FakeStore({"telegram_bot_token": "token", "telegram_chat_id": "chat", "telegram_enabled": False})
    notifier = TelegramNotifier(store)
    notifier._api_call = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not send"))  # type: ignore[method-assign]

    assert notifier.notify_task("task-1", []) == {"status": "disabled", "sent": 0, "failed": 0}


def test_telegram_inbound_output_sends_when_auto_push_is_disabled_and_deduplicates(tmp_path: Path):
    output = tmp_path / "result.mp4"
    output.write_bytes(b"mp4")
    store = FakeStore(
        {
            "telegram_bot_token": "123456:secret-token",
            "telegram_chat_id": "-1001",
            "telegram_enabled": False,
        },
        task={"workflow_name": "入站工作流", "submission_source": "telegram"},
    )
    notifier = TelegramNotifier(store)
    calls: list[str] = []
    notifier._api_call = lambda token, method, fields, file_field="", file_path=None: (calls.append(method) or {"ok": True})  # type: ignore[method-assign]
    saved = [{"kind": "file", "path": str(output), "name": "result.mp4", "mime": "video/mp4"}]

    assert notifier.notify_task("task-telegram", saved) == {"status": "sent", "sent": 1, "failed": 0}
    store.data["telegram_enabled"] = True
    assert notifier.notify_task("task-telegram", saved) == {"status": "sent", "sent": 0, "failed": 0}
    assert calls == ["sendVideo"]


def test_manual_telegram_upload_can_send_when_automatic_push_is_disabled(tmp_path: Path):
    output = tmp_path / "result.png"
    output.write_bytes(b"png")
    store = FakeStore(
        {"telegram_bot_token": "123456:secret-token", "telegram_chat_id": "-1001", "telegram_enabled": False}
    )
    notifier = TelegramNotifier(store)
    calls: list[str] = []
    notifier._api_call = lambda token, method, fields, file_field="", file_path=None: (calls.append(method) or {"ok": True})  # type: ignore[method-assign]

    saved = [{"kind": "file", "path": str(output), "name": "result.png", "mime": "image/png"}]

    assert notifier.notify_task("task-1", saved, force=True)["sent"] == 1
    assert calls == ["sendPhoto"]


def test_single_manual_output_keeps_original_output_index_for_deduplication(tmp_path: Path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    store = FakeStore({"telegram_bot_token": "123456:secret-token", "telegram_chat_id": "-1001"})
    notifier = TelegramNotifier(store)
    calls: list[Path | None] = []
    notifier._api_call = lambda token, method, fields, file_field="", file_path=None: (calls.append(file_path) or {"ok": True})  # type: ignore[method-assign]

    outputs = [
        {"kind": "file", "path": str(first), "name": "first.png", "mime": "image/png"},
        {"kind": "file", "path": str(second), "name": "second.png", "mime": "image/png"},
    ]

    assert notifier.notify_task("task-1", [outputs[1]], force=True, output_indices=[1])["sent"] == 1
    assert notifier.notify_task("task-1", outputs, force=True)["sent"] == 1
    assert calls == [second.resolve(), first.resolve()]


def test_concurrent_notifiers_claim_one_delivery_only(tmp_path: Path):
    output = tmp_path / "result.png"
    output.write_bytes(b"png")
    store = FakeStore({"telegram_bot_token": "token", "telegram_chat_id": "chat", "telegram_enabled": True})
    first = TelegramNotifier(store)
    second = TelegramNotifier(store)
    started = threading.Event()
    release = threading.Event()
    calls: list[Path | None] = []

    def fake_api(token, method, fields, file_field="", file_path=None):
        calls.append(file_path)
        started.set()
        assert release.wait(2)
        return {"ok": True}

    first._api_call = fake_api  # type: ignore[method-assign]
    second._api_call = fake_api  # type: ignore[method-assign]
    saved = [{"kind": "file", "path": str(output), "name": "result.png", "mime": "image/png"}]
    result: dict[str, dict] = {}

    worker = threading.Thread(target=lambda: result.setdefault("first", first.notify_task("task-1", saved)))
    worker.start()
    assert started.wait(1)
    result["second"] = second.notify_task("task-1", saved)
    release.set()
    worker.join(timeout=2)

    assert result["first"] == {"status": "sent", "sent": 1, "failed": 0}
    assert result["second"] == {"status": "sent", "sent": 0, "failed": 0}
    assert calls == [output.resolve()]


def test_not_sent_request_is_retried_but_unknown_outcome_is_not(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("web.telegram.time.sleep", lambda _seconds: None)
    output = tmp_path / "result.png"
    output.write_bytes(b"png")
    saved = [{"kind": "file", "path": str(output), "name": "result.png", "mime": "image/png"}]

    retry_store = FakeStore({"telegram_bot_token": "token", "telegram_chat_id": "chat", "telegram_enabled": True})
    retry_notifier = TelegramNotifier(retry_store)
    retry_calls = 0

    def retryable_api(*_args, **_kwargs):
        nonlocal retry_calls
        retry_calls += 1
        if retry_calls == 1:
            raise TelegramRequestNotSentError("not sent")
        return {"ok": True}

    retry_notifier._api_call = retryable_api  # type: ignore[method-assign]
    assert retry_notifier.notify_task("task-retry", saved) == {"status": "sent", "sent": 1, "failed": 0}
    assert retry_calls == 2

    unknown_store = FakeStore({"telegram_bot_token": "token", "telegram_chat_id": "chat", "telegram_enabled": True})
    unknown_notifier = TelegramNotifier(unknown_store)
    unknown_calls = 0

    def unknown_api(*_args, **_kwargs):
        nonlocal unknown_calls
        unknown_calls += 1
        raise TelegramRequestOutcomeUnknownError("unknown")

    unknown_notifier._api_call = unknown_api  # type: ignore[method-assign]
    assert unknown_notifier.notify_task("task-unknown", saved) == {"status": "partial", "sent": 0, "failed": 1}
    assert unknown_notifier.notify_task("task-unknown", saved)["sent"] == 0
    assert unknown_calls == 1


def test_image_file_reference_prefers_full_resolution_photo():
    reference = TelegramNotifier.image_file_reference(
        {
            "message": {
                "photo": [
                    {"file_id": "small", "width": 320, "height": 320},
                    {"file_id": "large", "width": 1280, "height": 1280},
                ]
            }
        }
    )

    assert reference == {"file_id": "large", "name": "telegram-photo.jpg", "mime": "image/jpeg"}


def test_image_file_reference_accepts_image_document_and_ignores_video():
    notifier = TelegramNotifier(FakeStore())

    assert notifier.image_file_reference({"message": {"document": {"file_id": "image", "file_name": "input.png"}}}) == {
        "file_id": "image",
        "name": "input.png",
        "mime": "image/*",
    }
    assert notifier.image_file_reference({"message": {"document": {"file_id": "video", "mime_type": "video/mp4"}}}) is None


def test_poll_updates_requests_messages_and_callback_queries():
    store = FakeStore({"telegram_bot_token": "token", "telegram_chat_id": "chat"})
    notifier = TelegramNotifier(store)
    calls = []

    def fake_api_get(token, method, params):
        calls.append(params)
        return {"ok": True, "result": []}

    notifier._api_get = fake_api_get  # type: ignore[method-assign]

    assert notifier.poll_updates(7) == []
    assert '"message","callback_query"' in calls[0]["allowed_updates"]
    assert calls[0]["offset"] == 7


def test_send_message_serializes_inline_keyboard():
    store = FakeStore({"telegram_bot_token": "token", "telegram_chat_id": "chat"})
    notifier = TelegramNotifier(store)
    calls = []
    notifier._api_call = lambda token, method, fields, file_field="", file_path=None: (calls.append((method, fields)) or {"ok": True})  # type: ignore[method-assign]

    notifier.send_message(
        "请选择",
        reply_markup={"inline_keyboard": [[{"text": "工作流 A", "callback_data": "rh_switch:wf_a"}]]},
    )

    assert calls[0][0] == "sendMessage"
    assert json.loads(calls[0][1]["reply_markup"])["inline_keyboard"][0][0]["callback_data"] == "rh_switch:wf_a"
