"""Small, dependency-free Telegram Bot API client for completed workflow outputs."""

from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from uuid import uuid4

from rh_cli.errors import RhCliError


class TelegramDeliveryError(RhCliError):
    """An error safe to show in a local task stage log."""

    def __init__(self, message: str) -> None:
        super().__init__("TELEGRAM_DELIVERY_FAILED", message)


class TelegramNotifier:
    """Deliver saved task outputs to one Telegram chat without new dependencies."""

    RETRIES = 3
    CHUNK_SIZE = 1024 * 1024

    def __init__(self, store: Any) -> None:
        self.store = store

    @staticmethod
    def _is_true(value: Any) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def credentials(self) -> tuple[str, str]:
        data = self.store._read_json_file()
        local_token = str(data.get("telegram_bot_token") or "").strip()
        local_chat_id = str(data.get("telegram_chat_id") or "").strip()
        if local_token and local_chat_id:
            return local_token, local_chat_id
        return (
            str(os.environ.get("RH_TELEGRAM_BOT_TOKEN") or "").strip(),
            str(os.environ.get("RH_TELEGRAM_CHAT_ID") or "").strip(),
        )

    def settings(self) -> dict[str, Any]:
        data = self.store._read_json_file()
        token, chat_id = self.credentials()
        local_configured = bool(
            str(data.get("telegram_bot_token") or "").strip()
            and str(data.get("telegram_chat_id") or "").strip()
        )
        environment_configured = bool(
            str(os.environ.get("RH_TELEGRAM_BOT_TOKEN") or "").strip()
            and str(os.environ.get("RH_TELEGRAM_CHAT_ID") or "").strip()
        )
        enabled = bool(data.get("telegram_enabled")) if "telegram_enabled" in data else self._is_true(os.environ.get("RH_TELEGRAM_ENABLED"))
        return {
            "configured": bool(token and chat_id),
            "enabled": enabled,
            "bot_token_hint": self._mask(token),
            "chat_id": chat_id,
            "source": "local" if local_configured else "environment" if environment_configured else "",
        }

    @staticmethod
    def _mask(value: str) -> str:
        value = str(value or "")
        if len(value) <= 8:
            return "••••" if value else ""
        return f"{value[:4]}••••{value[-4:]}"

    @staticmethod
    def _caption(task: dict[str, Any], output: dict[str, Any]) -> str:
        workflow_name = str(task.get("workflow_name") or "RH Workflow Desk").strip()
        filename = str(output.get("name") or "成片").strip()
        return f"{workflow_name} · {filename}"[:1024]

    @staticmethod
    def _output_key(index: int, output: dict[str, Any]) -> str:
        raw = f"{index}:{output.get('kind', '')}:{output.get('path', '')}:{output.get('text', '')}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _media_method(output: dict[str, Any]) -> tuple[str, str]:
        mime = str(output.get("mime") or mimetypes.guess_type(str(output.get("name") or ""))[0] or "").lower()
        if mime.startswith("image/"):
            return "sendPhoto", "photo"
        if mime.startswith("video/"):
            return "sendVideo", "video"
        if mime.startswith("audio/"):
            return "sendAudio", "audio"
        return "sendDocument", "document"

    def _api_call(
        self,
        token: str,
        method: str,
        fields: dict[str, str],
        file_field: str = "",
        file_path: Path | None = None,
    ) -> dict[str, Any]:
        boundary = f"----RHWorkflowDesk{uuid4().hex}"
        boundary_bytes = boundary.encode("ascii")
        field_parts: list[bytes] = []
        content_length = 0
        for name, value in fields.items():
            part = (
                b"--" + boundary_bytes + b"\r\n"
                + f'Content-Disposition: form-data; name="{name}"'.encode("utf-8")
                + b"\r\n\r\n"
                + str(value).encode("utf-8")
                + b"\r\n"
            )
            field_parts.append(part)
            content_length += len(part)

        file_header = b""
        file_size = 0
        if file_path is not None:
            try:
                file_size = file_path.stat().st_size
            except OSError as exc:
                raise TelegramDeliveryError(f"找不到待发送的成片：{file_path.name}") from exc
            file_header = (
                b"--" + boundary_bytes + b"\r\n"
                + f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"'.encode("utf-8", "replace")
                + b"\r\n"
                + f"Content-Type: {mimetypes.guess_type(file_path.name)[0] or 'application/octet-stream'}".encode("ascii")
                + b"\r\n\r\n"
            )
            content_length += len(file_header) + file_size + 2
        closing = b"--" + boundary_bytes + b"--\r\n"
        content_length += len(closing)

        connection: http.client.HTTPSConnection | None = None
        try:
            connection = http.client.HTTPSConnection("api.telegram.org", timeout=60)
            connection.putrequest("POST", f"/bot{quote(token, safe=':_-')}/{method}")
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(content_length))
            connection.endheaders()
            for part in field_parts:
                connection.send(part)
            if file_path is not None:
                connection.send(file_header)
                with file_path.open("rb") as source:
                    while chunk := source.read(self.CHUNK_SIZE):
                        connection.send(chunk)
                connection.send(b"\r\n")
            connection.send(closing)
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024)
        except (OSError, http.client.HTTPException) as exc:
            raise TelegramDeliveryError("Telegram 网络请求失败，请检查网络或代理。") from exc
        finally:
            if connection is not None:
                connection.close()

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise TelegramDeliveryError("Telegram 返回了无法识别的响应。") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            description = str(payload.get("description") or "Telegram 接口拒绝了请求") if isinstance(payload, dict) else "Telegram 接口返回异常"
            raise TelegramDeliveryError(description[:300])
        return payload

    def _with_retries(self, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        last_error: TelegramDeliveryError | None = None
        for attempt in range(self.RETRIES):
            try:
                return action()
            except TelegramDeliveryError as exc:
                last_error = exc
                if attempt < self.RETRIES - 1:
                    time.sleep(1 + attempt * 2)
        raise last_error or TelegramDeliveryError("Telegram 发送失败")

    def test_connection(self) -> dict[str, Any]:
        token, chat_id = self.credentials()
        if not token or not chat_id:
            raise TelegramDeliveryError("请先配置 Telegram Bot Token 和 Chat ID。")
        self._with_retries(
            lambda: self._api_call(
                token,
                "sendMessage",
                {"chat_id": chat_id, "text": "RH Workflow Desk 连接测试成功。"},
            )
        )
        return {"ok": True, "message": "测试消息已发送到 Telegram。"}

    def _send_output(self, token: str, chat_id: str, task: dict[str, Any], output: dict[str, Any]) -> None:
        kind = str(output.get("kind") or "file")
        if kind == "text":
            text = str(output.get("text") or output.get("content") or "").strip()
            if text:
                self._with_retries(lambda: self._api_call(token, "sendMessage", {"chat_id": chat_id, "text": text[:4096]}))
            return
        path = Path(str(output.get("path") or "")).expanduser().resolve()
        if not path.is_file():
            raise TelegramDeliveryError(f"待发送的成片不存在：{path.name or '未知文件'}")
        method, file_field = self._media_method(output)
        fields = {"chat_id": chat_id, "caption": self._caption(task, output)}
        self._with_retries(lambda: self._api_call(token, method, fields, file_field, path))

    def notify_task(self, task_id: str, outputs: list[dict[str, Any]]) -> dict[str, int | str]:
        settings = self.settings()
        if not settings["enabled"]:
            return {"status": "disabled", "sent": 0, "failed": 0}
        token, chat_id = self.credentials()
        if not token or not chat_id:
            return {"status": "not_configured", "sent": 0, "failed": 0}
        task = self.store.task(task_id) or {"id": task_id, "workflow_name": "RH Workflow Desk"}
        sent = 0
        failed = 0
        for index, output in enumerate(outputs):
            if not isinstance(output, dict):
                continue
            delivery_key = self._output_key(index, output)
            if self.store.telegram_delivery_sent(task_id, delivery_key):
                continue
            try:
                self._send_output(token, chat_id, task, output)
            except TelegramDeliveryError:
                failed += 1
                continue
            self.store.mark_telegram_delivery_sent(task_id, delivery_key)
            sent += 1
        return {"status": "sent" if not failed else "partial", "sent": sent, "failed": failed}
