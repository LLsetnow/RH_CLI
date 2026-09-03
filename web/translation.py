from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from email.utils import formatdate
from typing import Any

import httpx

from rh_cli.errors import RhCliError


ALIYUN_TRANSLATION_ENDPOINT = "https://mt.cn-hangzhou.aliyuncs.com/api/translate/web/general"
ALIYUN_TRANSLATION_CONTENT_TYPE = "application/json;charset=utf-8"
ALIYUN_TRANSLATION_VERSION = "2019-01-02"
ALIYUN_TRANSLATION_MAX_CHARS = 5000


def _hmac_sha1(value: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _content_md5(body: bytes) -> str:
    return base64.b64encode(hashlib.md5(body).digest()).decode("ascii")


def _aliyun_headers(body: bytes, access_key_id: str, access_key_secret: str, path: str = "/api/translate/web/general") -> dict[str, str]:
    """Build the ROA headers required by Aliyun's machine translation API."""
    accept = "application/json"
    content_type = ALIYUN_TRANSLATION_CONTENT_TYPE
    date = formatdate(usegmt=True)
    nonce = str(uuid.uuid4())
    body_md5 = _content_md5(body)
    string_to_sign = "\n".join(
        [
            "POST",
            accept,
            body_md5,
            content_type,
            date,
            "x-acs-signature-method:HMAC-SHA1",
            f"x-acs-signature-nonce:{nonce}",
            f"x-acs-version:{ALIYUN_TRANSLATION_VERSION}",
            path,
        ]
    )
    signature = _hmac_sha1(string_to_sign, access_key_secret)
    return {
        "Accept": accept,
        "Content-Type": content_type,
        "Content-MD5": body_md5,
        "Date": date,
        "Host": "mt.cn-hangzhou.aliyuncs.com",
        "Authorization": f"acs {access_key_id}:{signature}",
        "x-acs-signature-method": "HMAC-SHA1",
        "x-acs-signature-nonce": nonce,
        "x-acs-version": ALIYUN_TRANSLATION_VERSION,
    }


def _error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "阿里云翻译服务返回了无效响应。"
    response = payload.get("TranslateGeneralResponse")
    if isinstance(response, dict):
        payload = response
    message = str(payload.get("Message") or payload.get("errorMsg") or payload.get("message") or "").strip()
    return message[:240] if message else "阿里云翻译服务请求失败。"


class AliyunTranslationClient:
    def __init__(self, access_key_id: str, access_key_secret: str) -> None:
        self.access_key_id = str(access_key_id or "").strip()
        self.access_key_secret = str(access_key_secret or "").strip()

    def translate(self, source_text: str) -> dict[str, str]:
        source_text = str(source_text or "").strip()
        if not source_text:
            raise RhCliError("TRANSLATION_TEXT_EMPTY", "请输入需要翻译的自由文本。")
        if len(source_text) > ALIYUN_TRANSLATION_MAX_CHARS:
            raise RhCliError("TRANSLATION_TEXT_TOO_LONG", "单块自由文本不能超过 5000 个字符。")
        if not self.access_key_id or not self.access_key_secret:
            raise RhCliError("TRANSLATION_NOT_CONFIGURED", "请先在本地设置中配置阿里云翻译 AccessKey。")

        body = json.dumps(
            {
                "FormatType": "text",
                "Scene": "general",
                "SourceLanguage": "auto",
                "SourceText": source_text,
                "TargetLanguage": "en",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = _aliyun_headers(body, self.access_key_id, self.access_key_secret)
        try:
            response = httpx.post(ALIYUN_TRANSLATION_ENDPOINT, content=body, headers=headers, timeout=15.0)
        except httpx.TimeoutException as exc:
            raise RhCliError("TRANSLATION_TIMEOUT", "阿里云翻译请求超时，请检查网络后重试。") from exc
        except httpx.HTTPError as exc:
            raise RhCliError("TRANSLATION_NETWORK_ERROR", "无法连接阿里云翻译服务，请检查网络。") from exc

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RhCliError("TRANSLATION_INVALID_RESPONSE", "阿里云翻译服务返回了无效响应。") from exc

        if response.status_code >= 400:
            raise RhCliError("TRANSLATION_REQUEST_FAILED", _error_message(payload))
        result = payload.get("TranslateGeneralResponse") if isinstance(payload, dict) else None
        result = result if isinstance(result, dict) else payload
        if not isinstance(result, dict):
            raise RhCliError("TRANSLATION_INVALID_RESPONSE", "阿里云翻译服务返回了无效响应。")
        try:
            code = int(result.get("Code", 200))
        except (TypeError, ValueError):
            code = 500
        data = result.get("Data") if isinstance(result.get("Data"), dict) else {}
        translated = str(data.get("Translated") or result.get("Translated") or "").strip()
        if code != 200 or not translated:
            raise RhCliError("TRANSLATION_REQUEST_FAILED", _error_message(result))
        return {
            "translated_text": translated,
            "detected_language": str(result.get("DetectedLanguage") or "").strip(),
        }
