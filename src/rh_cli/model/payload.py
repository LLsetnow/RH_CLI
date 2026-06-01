from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rh_cli.errors import RhCliError
from rh_cli.http import RhHttpClient
from rh_cli.media import resolve_media


@dataclass(slots=True)
class ModelRunInput:
    prompt: str | None = None
    images: list[str] = field(default_factory=list)
    video: str | None = None
    audio: str | None = None
    params: list[str] = field(default_factory=list)


def parse_params(params: list[str] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in params or []:
        if "=" not in item:
            raise RhCliError("INVALID_PARAM", f"参数格式错误：{item}，应为 key=value。")
        key, value = item.split("=", 1)
        parsed[key] = value
    return parsed


def coerce_value(value: str, param_def: dict[str, Any] | None) -> Any:
    if not param_def:
        return value
    param_type = param_def.get("type")
    if param_type == "BOOLEAN":
        return value.lower() in ("true", "1", "yes", "y", "on")
    if param_type == "INT":
        try:
            return int(value)
        except ValueError:
            return value
    if param_type == "FLOAT":
        try:
            return float(value)
        except ValueError:
            return value
    return value


def build_payload(client: RhHttpClient, endpoint_def: dict[str, Any], run_input: ModelRunInput) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    params = endpoint_def.get("params", [])

    prompt_key = next((p.get("key") for p in params if p.get("key") in ("prompt", "text")), None)
    if run_input.prompt and prompt_key:
        payload[str(prompt_key)] = run_input.prompt
    elif run_input.prompt:
        payload["prompt"] = run_input.prompt

    media_params = [p for p in params if p.get("type") in ("IMAGE", "VIDEO", "AUDIO")]

    if run_input.images:
        image_params = [p for p in media_params if p.get("type") == "IMAGE"]
        if len(run_input.images) == 1 and image_params:
            param = image_params[0]
            key = str(param.get("key"))
            force_upload = endpoint_def.get("output_type") == "video"
            resolved = resolve_media(client, run_input.images[0], force_upload=force_upload)
            payload[key] = [resolved] if param.get("multiple") else resolved
        elif len(run_input.images) > 1:
            multi_param = next((p for p in image_params if p.get("multiple")), None)
            if multi_param:
                payload[str(multi_param.get("key"))] = [
                    resolve_media(client, image, force_upload=True) for image in run_input.images
                ]
            else:
                for image, param in zip(run_input.images, image_params):
                    payload[str(param.get("key"))] = resolve_media(client, image, force_upload=True)

    if run_input.video:
        video_param = next((p for p in media_params if p.get("type") == "VIDEO"), None)
        if video_param:
            payload[str(video_param.get("key"))] = resolve_media(client, run_input.video, force_upload=True)

    if run_input.audio:
        audio_param = next((p for p in media_params if p.get("type") == "AUDIO"), None)
        if audio_param:
            payload[str(audio_param.get("key"))] = resolve_media(client, run_input.audio, force_upload=True)

    extra_params = parse_params(run_input.params)
    for key, value in extra_params.items():
        param_def = next((p for p in params if p.get("key") == key), None)
        payload[key] = coerce_value(value, param_def)

    for param in params:
        key = str(param.get("key"))
        if key not in payload and param.get("required") and "default" in param:
            payload[key] = param["default"]

    return payload
