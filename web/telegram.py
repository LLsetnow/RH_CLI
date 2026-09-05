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
from urllib.parse import quote, urlencode
from uuid import uuid4

from rh_cli.errors import RhCliError


class TelegramDeliveryError(RhCliError):
    """An error safe to show in a local task stage log."""

    def __init__(self, message: str) -> None:
        super().__init__("TELEGRAM_DELIVERY_FAILED", message)


class TelegramRequestNotSentError(TelegramDeliveryError):
    """The request failed before any bytes could be sent to Telegram."""


class TelegramRequestOutcomeUnknownError(TelegramDeliveryError):
    """The request may have been accepted, but its response was not known."""


class TelegramNotifier:
    """Deliver saved task outputs to configured Telegram chats without new dependencies."""

    RETRIES = 3
    DELIVERY_CLAIM_LEASE_MS = 10 * 60 * 1000
    CHUNK_SIZE = 1024 * 1024
    MAX_INBOUND_IMAGE_BYTES = 20 * 1024 * 1024
    IMAGE_SUFFIXES = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}

    def __init__(self, store: Any) -> None:
        self.store = store

    @staticmethod
    def _is_true(value: Any) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def parse_chat_ids(value: Any) -> list[str]:
        """Parse a comma-separated Chat ID setting into unique, trimmed targets."""
        chat_ids: list[str] = []
        for raw_chat_id in str(value or "").replace("，", ",").split(","):
            chat_id = raw_chat_id.strip()
            if chat_id and chat_id not in chat_ids:
                chat_ids.append(chat_id)
        return chat_ids

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
        chat_ids = self.parse_chat_ids(chat_id)
        local_configured = bool(
            str(data.get("telegram_bot_token") or "").strip()
            and self.parse_chat_ids(data.get("telegram_chat_id"))
        )
        environment_configured = bool(
            str(os.environ.get("RH_TELEGRAM_BOT_TOKEN") or "").strip()
            and self.parse_chat_ids(os.environ.get("RH_TELEGRAM_CHAT_ID"))
        )
        enabled = bool(data.get("telegram_enabled")) if "telegram_enabled" in data else self._is_true(os.environ.get("RH_TELEGRAM_ENABLED"))
        inbound_mode = str(data.get("telegram_inbound_mode") or "fixed").strip().lower()
        if inbound_mode not in {"fixed", "folder_random"}:
            inbound_mode = "fixed"
        inbound_workflow_id = str(data.get("telegram_inbound_workflow_id") or "").strip()
        inbound_workflow_name = ""
        if inbound_workflow_id:
            try:
                inbound_workflow_name = str(self.store.workflow_record(inbound_workflow_id).get("name") or "").strip()
            except Exception:
                inbound_workflow_name = "工作流已删除"
        inbound_folder_id = str(data.get("telegram_inbound_folder_id") or "").strip()
        inbound_folder_name = ""
        if inbound_folder_id:
            try:
                inbound_folder_name = next(
                    (
                        str(item.get("name") or "").strip()
                        for item in self.store.workflow_folders()
                        if str(item.get("id") or "").strip() == inbound_folder_id
                    ),
                    "",
                )
            except Exception:
                inbound_folder_name = ""
        video_inbound_workflow_id = str(data.get("telegram_video_inbound_workflow_id") or "").strip()
        video_inbound_workflow_name = ""
        if video_inbound_workflow_id:
            try:
                video_inbound_workflow_name = str(self.store.workflow_record(video_inbound_workflow_id).get("name") or "").strip()
            except Exception:
                video_inbound_workflow_name = "工作流已删除"
        return {
            "configured": bool(token and chat_ids),
            "enabled": enabled,
            "bot_token_hint": self._mask(token),
            "chat_id": chat_id,
            "chat_ids": chat_ids,
            "source": "local" if local_configured else "environment" if environment_configured else "",
            "inbound_enabled": bool(data.get("telegram_inbound_enabled")),
            "inbound_mode": inbound_mode,
            "inbound_workflow_id": inbound_workflow_id,
            "inbound_workflow_name": inbound_workflow_name,
            "inbound_folder_id": inbound_folder_id,
            "inbound_folder_name": inbound_folder_name,
            "inbound_file_input_id": str(data.get("telegram_inbound_file_input_id") or "").strip(),
            "video_inbound_enabled": bool(data.get("telegram_video_inbound_enabled")),
            "video_inbound_workflow_id": video_inbound_workflow_id,
            "video_inbound_workflow_name": video_inbound_workflow_name,
            "video_inbound_file_input_id": str(data.get("telegram_video_inbound_file_input_id") or "").strip(),
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
        task_id = str(task.get("id") or "").strip() or "unknown-task"
        return f"{workflow_name} · {task_id}"[:1024]

    @staticmethod
    def _output_key(index: int, output: dict[str, Any]) -> str:
        raw = f"{index}:{output.get('kind', '')}:{output.get('path', '')}:{output.get('text', '')}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _delivery_key(output_key: str, chat_id: str, multiple_chats: bool) -> str:
        """Keep legacy keys for one target and isolate delivery state per target otherwise."""
        if not multiple_chats:
            return output_key
        chat_key = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:16]
        return f"{output_key}:{chat_key}"

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
        request_started = False
        try:
            connection = http.client.HTTPSConnection("api.telegram.org", timeout=60)
            connection.putrequest("POST", f"/bot{quote(token, safe=':_-')}/{method}")
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(content_length))
            # endheaders() flushes the HTTP request headers. From this point on,
            # a socket failure has an unknown outcome: Telegram may already have
            # received enough of the request to create the message.
            request_started = True
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
            if request_started:
                raise TelegramRequestOutcomeUnknownError(
                    "Telegram 请求结果未知，已停止自动重试以避免重复发送。"
                ) from exc
            raise TelegramRequestNotSentError("Telegram 请求尚未发出，请检查网络或代理。") from exc
        finally:
            if connection is not None:
                connection.close()

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise TelegramRequestOutcomeUnknownError(
                "Telegram 返回无法识别的响应，已停止自动重试以避免重复发送。"
            ) from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            description = str(payload.get("description") or "Telegram 接口拒绝了请求") if isinstance(payload, dict) else "Telegram 接口返回异常"
            raise TelegramDeliveryError(description[:300])
        return payload

    def _api_get(self, token: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
        path = f"/bot{quote(token, safe=':_-')}/{method}"
        if query:
            path += f"?{query}"
        connection: http.client.HTTPSConnection | None = None
        try:
            connection = http.client.HTTPSConnection("api.telegram.org", timeout=40)
            connection.request("GET", path)
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024)
        except (OSError, http.client.HTTPException) as exc:
            # GET polling has no message-send side effect, so it is safe to
            # retry even if the response outcome is not known.
            raise TelegramRequestNotSentError("Telegram 网络请求失败，请检查网络或代理。") from exc
        finally:
            if connection is not None:
                connection.close()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            # A malformed GET response is also safe to retry because polling
            # does not create a Telegram message.
            raise TelegramRequestNotSentError("Telegram 返回了无法识别的响应。") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            description = str(payload.get("description") or "Telegram 接口拒绝了请求") if isinstance(payload, dict) else "Telegram 接口返回异常"
            raise TelegramDeliveryError(description[:300])
        return payload

    def _with_retries(self, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        last_error: TelegramDeliveryError | None = None
        for attempt in range(self.RETRIES):
            try:
                return action()
            except TelegramRequestNotSentError as exc:
                last_error = exc
                if attempt < self.RETRIES - 1:
                    time.sleep(1 + attempt * 2)
            except TelegramDeliveryError as exc:
                # A known API rejection and an unknown POST outcome must not be
                # retried here. Retrying an unknown multipart upload can create
                # duplicate messages after Telegram already accepted it.
                raise exc
        raise last_error or TelegramDeliveryError("Telegram 发送失败")

    def test_connection(self) -> dict[str, Any]:
        token, chat_id = self.credentials()
        chat_ids = self.parse_chat_ids(chat_id)
        if not token or not chat_ids:
            raise TelegramDeliveryError("请先配置 Telegram Bot Token 和 Chat ID。")
        for target_chat_id in chat_ids:
            self._with_retries(
                lambda target_chat_id=target_chat_id: self._api_call(
                    token,
                    "sendMessage",
                    {"chat_id": target_chat_id, "text": "RH Workflow Desk 连接测试成功。"},
                )
            )
        return {"ok": True, "message": f"测试消息已发送到 {len(chat_ids)} 个 Telegram 聊天。"}

    def send_message(
        self,
        text: str,
        chat_id: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        token, configured_chat_id = self.credentials()
        configured_chat_ids = self.parse_chat_ids(configured_chat_id)
        requested_chat_id = str(chat_id or "").strip()
        target_chat_ids = [requested_chat_id] if requested_chat_id else configured_chat_ids
        if not token or not target_chat_ids:
            raise TelegramDeliveryError("请先配置 Telegram Bot Token 和 Chat ID。")
        message = str(text or "").strip()
        if not message:
            return
        for target_chat_id in target_chat_ids:
            fields = {"chat_id": target_chat_id, "text": message[:4096]}
            if reply_markup is not None:
                fields["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False, separators=(",", ":"))
            self._with_retries(
                lambda fields=fields: self._api_call(token, "sendMessage", fields)
            )

    def answer_callback_query(self, callback_query_id: str, text: str = "", show_alert: bool = False) -> None:
        token, _ = self.credentials()
        callback_id = str(callback_query_id or "").strip()
        if not token or not callback_id:
            raise TelegramDeliveryError("Telegram 回调信息不完整。")
        fields = {"callback_query_id": callback_id}
        if str(text or "").strip():
            fields["text"] = str(text).strip()[:200]
        if show_alert:
            fields["show_alert"] = "true"
        self._with_retries(lambda: self._api_call(token, "answerCallbackQuery", fields))

    def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        token, _ = self.credentials()
        target_chat_id = str(chat_id or "").strip()
        message = str(text or "").strip()
        if not token or not target_chat_id or not message:
            raise TelegramDeliveryError("Telegram 消息信息不完整。")
        fields = {"chat_id": target_chat_id, "message_id": str(int(message_id)), "text": message[:4096]}
        if reply_markup is not None:
            fields["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False, separators=(",", ":"))
        self._with_retries(lambda: self._api_call(token, "editMessageText", fields))

    def delete_message(self, chat_id: str, message_id: int) -> None:
        token, _ = self.credentials()
        target_chat_id = str(chat_id or "").strip()
        if not token or not target_chat_id:
            raise TelegramDeliveryError("Telegram 消息信息不完整。")
        fields = {"chat_id": target_chat_id, "message_id": str(int(message_id))}
        self._with_retries(lambda: self._api_call(token, "deleteMessage", fields))

    @staticmethod
    def message_text(update: dict[str, Any]) -> str:
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            return ""
        return str(message.get("text") or message.get("caption") or "").strip()

    @staticmethod
    def image_file_reference(update: dict[str, Any]) -> dict[str, str] | None:
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            return None
        photos = message.get("photo")
        if isinstance(photos, list):
            for item in reversed(photos):
                if isinstance(item, dict) and str(item.get("file_id") or "").strip():
                    return {
                        "file_id": str(item["file_id"]),
                        "name": "telegram-photo.jpg",
                        "mime": "image/jpeg",
                    }
        document = message.get("document")
        if isinstance(document, dict):
            mime = str(document.get("mime_type") or "").strip().lower()
            name = str(document.get("file_name") or "telegram-image").strip() or "telegram-image"
            suffix = Path(name).suffix.lower()
            if (mime.startswith("image/") or suffix in TelegramNotifier.IMAGE_SUFFIXES) and str(document.get("file_id") or "").strip():
                return {"file_id": str(document["file_id"]), "name": name, "mime": mime or "image/*"}
        return None

    def poll_updates(self, offset: int | None = None) -> list[dict[str, Any]]:
        token, _ = self.credentials()
        if not token:
            return []
        params: dict[str, Any] = {
            "timeout": 25,
            "limit": 100,
            "allowed_updates": json.dumps(["message", "callback_query"], separators=(",", ":")),
        }
        if offset is not None:
            params["offset"] = offset
        payload = self._with_retries(lambda: self._api_get(token, "getUpdates", params))
        updates = payload.get("result")
        return [item for item in updates if isinstance(item, dict)] if isinstance(updates, list) else []

    def download_image(self, update_id: int, reference: dict[str, str], target_dir: Path) -> Path:
        token, _ = self.credentials()
        file_id = str(reference.get("file_id") or "").strip()
        if not token or not file_id:
            raise TelegramDeliveryError("Telegram 图片信息不完整。")
        payload = self._with_retries(lambda: self._api_get(token, "getFile", {"file_id": file_id}))
        result = payload.get("result")
        file_path = str(result.get("file_path") or "").strip() if isinstance(result, dict) else ""
        if not file_path:
            raise TelegramDeliveryError("Telegram 没有返回图片文件路径。")
        suffix = Path(file_path).suffix.lower()
        if suffix not in self.IMAGE_SUFFIXES:
            suffix = Path(str(reference.get("name") or "")).suffix.lower()
        if suffix not in self.IMAGE_SUFFIXES:
            suffix = ".jpg"
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"telegram_{int(update_id)}_{uuid4().hex[:12]}{suffix}"
        target = (target_dir / filename).resolve()
        if target.parent != target_dir.resolve():
            raise TelegramDeliveryError("Telegram 图片保存路径无效。")
        temporary = target.with_name(f".{target.name}.part")
        connection: http.client.HTTPSConnection | None = None
        total = 0
        try:
            path = f"/file/bot{quote(token, safe=':_-')}/{quote(file_path.lstrip('/'), safe='/._-') }"
            connection = http.client.HTTPSConnection("api.telegram.org", timeout=60)
            connection.request("GET", path)
            response = connection.getresponse()
            if response.status >= 400:
                response.read(1024 * 1024)
                raise TelegramDeliveryError("Telegram 图片下载失败。")
            content_length = response.getheader("Content-Length")
            try:
                if content_length and int(content_length) > self.MAX_INBOUND_IMAGE_BYTES:
                    raise TelegramDeliveryError("Telegram 图片不能超过 20MB。")
            except ValueError:
                pass
            with temporary.open("wb") as destination:
                while chunk := response.read(self.CHUNK_SIZE):
                    total += len(chunk)
                    if total > self.MAX_INBOUND_IMAGE_BYTES:
                        raise TelegramDeliveryError("Telegram 图片不能超过 20MB。")
                    destination.write(chunk)
            temporary.replace(target)
            return target
        except TelegramDeliveryError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise TelegramDeliveryError("Telegram 图片下载失败，请检查网络或代理。") from exc
        finally:
            if connection is not None:
                connection.close()
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

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

    def notify_task(
        self,
        task_id: str,
        outputs: list[dict[str, Any]],
        *,
        force: bool = False,
        output_indices: list[int] | None = None,
    ) -> dict[str, int | str]:
        settings = self.settings()
        task = self.store.task(task_id) or {"id": task_id, "workflow_name": "RH Workflow Desk"}
        is_telegram_inbound = str(task.get("submission_source") or "").strip().lower() == "telegram"
        if not force and not is_telegram_inbound and not settings["enabled"]:
            return {"status": "disabled", "sent": 0, "failed": 0}
        token, chat_id = self.credentials()
        chat_ids = self.parse_chat_ids(chat_id)
        if not token or not chat_ids:
            return {"status": "not_configured", "sent": 0, "failed": 0}
        sent = 0
        failed = 0
        for position, output in enumerate(outputs):
            if not isinstance(output, dict):
                continue
            index = output_indices[position] if output_indices and position < len(output_indices) else position
            output_key = self._output_key(index, output)
            for target_chat_id in chat_ids:
                delivery_key = self._delivery_key(output_key, target_chat_id, len(chat_ids) > 1)
                claim_id = uuid4().hex
                if not self.store.claim_telegram_delivery(
                    task_id,
                    delivery_key,
                    claim_id,
                    lease_ms=self.DELIVERY_CLAIM_LEASE_MS,
                    allow_unknown=force,
                ):
                    continue
                try:
                    self._send_output(token, target_chat_id, task, output)
                except TelegramRequestOutcomeUnknownError as exc:
                    self.store.finish_telegram_delivery(
                        task_id, delivery_key, claim_id, "unknown", str(exc)
                    )
                    failed += 1
                    continue
                except TelegramDeliveryError as exc:
                    self.store.finish_telegram_delivery(
                        task_id, delivery_key, claim_id, "retryable", str(exc)
                    )
                    failed += 1
                    continue
                self.store.finish_telegram_delivery(task_id, delivery_key, claim_id, "sent", "")
                sent += 1
        return {"status": "sent" if not failed else "partial", "sent": sent, "failed": failed}
