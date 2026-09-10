"""Local character TTS discovery and GPT-SoVITS API access."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import httpx

from rh_cli.errors import RhCliError


TTS_ROOT = Path("/Users/apple/Documents/VideoMake/ref/tts")
TTS_API_URL = "http://127.0.0.1:9889"
TTS_TEXT_LIMIT = 20_000


def _asset(directory: Path, pattern: str) -> Path | None:
    matches = sorted(path for path in directory.glob(pattern) if path.is_file())
    return matches[0] if matches else None


def _voice_record(directory: Path) -> dict[str, Any] | None:
    reference_dir = directory / "reference"
    gpt_path = _asset(directory, "*.ckpt")
    sovits_path = _asset(directory, "*.pth")
    reference_path = _asset(reference_dir, "*.wav")
    prompt_path = reference_dir / "参考文本.txt"
    if not gpt_path or not sovits_path or not reference_path or not prompt_path.is_file():
        return None
    try:
        prompt_text = prompt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RhCliError("TTS_REFERENCE_TEXT_UNREADABLE", f"无法读取人物“{directory.name}”的参考文本。") from exc
    if not prompt_text:
        return None
    return {
        "id": directory.name,
        "name": directory.name,
        "gpt_model_path": str(gpt_path.resolve()),
        "sovits_model_path": str(sovits_path.resolve()),
        "reference_path": str(reference_path.resolve()),
        "reference_name": reference_path.name,
        "prompt_text": prompt_text,
    }


def discover_tts_voices(root: str | Path = TTS_ROOT) -> list[dict[str, Any]]:
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise RhCliError("TTS_ASSET_ROOT_MISSING", f"找不到角色 TTS 目录：{root_path}")
    voices = []
    for directory in sorted(root_path.iterdir(), key=lambda item: item.name):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        voice = _voice_record(directory)
        if voice:
            voices.append(voice)
    if not voices:
        raise RhCliError("TTS_VOICES_MISSING", f"{root_path} 下没有找到完整的角色 TTS 资产。")
    return voices


def public_tts_voices(root: str | Path = TTS_ROOT) -> list[dict[str, str]]:
    return [
        {
            "id": str(voice["id"]),
            "name": str(voice["name"]),
            "reference_path": str(voice["reference_path"]),
            "reference_name": str(voice["reference_name"]),
        }
        for voice in discover_tts_voices(root)
    ]


class TtsClient:
    """Serialize model switching and synthesis requests to the local API."""

    def __init__(self, root: str | Path = TTS_ROOT, api_url: str | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.api_url = str(api_url or os.environ.get("RH_TTS_API_URL") or TTS_API_URL).rstrip("/")
        self._lock = threading.RLock()
        self._loaded_voice_id = ""

    def voice(self, voice_id: str) -> dict[str, Any]:
        clean_id = str(voice_id or "").strip()
        for voice in discover_tts_voices(self.root):
            if str(voice["id"]) == clean_id:
                return voice
        raise RhCliError("TTS_VOICE_NOT_FOUND", f"找不到角色 TTS：{clean_id or '未选择'}")

    @staticmethod
    def _response_error(response: httpx.Response, fallback: str) -> str:
        try:
            payload = response.json()
        except (ValueError, TypeError):
            return fallback
        if isinstance(payload, dict):
            detail = payload.get("message") or payload.get("detail")
            if detail:
                return str(detail)
        return fallback

    def _set_model(self, voice: dict[str, Any]) -> None:
        try:
            response = httpx.post(
                self.api_url + "/set_model",
                json={
                    "gpt_model_path": voice["gpt_model_path"],
                    "sovits_model_path": voice["sovits_model_path"],
                },
                timeout=httpx.Timeout(connect=15.0, read=900.0, write=15.0, pool=15.0),
            )
        except httpx.HTTPError as exc:
            raise RhCliError("TTS_SERVICE_UNAVAILABLE", "本地 GPT-SoVITS 服务不可用，请确认 9889 端口服务已启动。") from exc
        if response.status_code >= 400:
            message = self._response_error(response, "角色 TTS 模型加载失败。")
            raise RhCliError("TTS_MODEL_LOAD_FAILED", message)
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise RhCliError("TTS_MODEL_LOAD_FAILED", "角色 TTS 模型加载返回无效结果。") from exc
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            raise RhCliError("TTS_MODEL_LOAD_FAILED", str(payload.get("message") or "角色 TTS 模型加载失败。"))

    def synthesize(self, voice_id: str, text: str) -> tuple[bytes, dict[str, Any]]:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise RhCliError("TTS_TEXT_MISSING", "请输入语音内容。")
        if len(clean_text) > TTS_TEXT_LIMIT:
            raise RhCliError("TTS_TEXT_TOO_LONG", f"语音内容不能超过 {TTS_TEXT_LIMIT} 个字符。")
        voice = self.voice(voice_id)
        with self._lock:
            try:
                if self._loaded_voice_id != voice["id"]:
                    self._set_model(voice)
                    self._loaded_voice_id = str(voice["id"])
                response = httpx.post(
                    self.api_url + "/",
                    json={
                        "refer_wav_path": voice["reference_path"],
                        "prompt_text": voice["prompt_text"],
                        "prompt_language": "中文",
                        "text": clean_text,
                        "text_language": "中文",
                        "sample_steps": 8,
                        "top_p": 1.0,
                        "temperature": 1.0,
                    },
                    timeout=httpx.Timeout(connect=15.0, read=1200.0, write=15.0, pool=15.0),
                )
            except httpx.HTTPError as exc:
                raise RhCliError("TTS_SERVICE_UNAVAILABLE", "本地 GPT-SoVITS 服务请求失败。") from exc
            if response.status_code >= 400:
                message = self._response_error(response, "语音合成失败。")
                raise RhCliError("TTS_SYNTHESIS_FAILED", message)
            content_type = str(response.headers.get("content-type") or "").lower()
            audio = response.content
            if "json" in content_type:
                raise RhCliError("TTS_SYNTHESIS_FAILED", self._response_error(response, "语音合成失败。"))
            if not audio:
                raise RhCliError("TTS_SYNTHESIS_FAILED", "语音合成返回空音频。")
            return audio, voice
