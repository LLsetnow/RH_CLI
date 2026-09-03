from __future__ import annotations

import json
import re
from typing import Any

import httpx

from rh_cli.errors import RhCliError


ALIYUN_VISION_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
ALIYUN_VISION_DEFAULT_MODEL = "qwen-vl-max"
ALIYUN_VISION_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_JSON_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _kind_label(kind: str) -> str:
    return {
        "action": "动作",
        "character": "人物",
        "background": "背景",
        "clothes": "服装",
    }.get(str(kind or "").strip(), "参考素材")


def _recognition_prompt(kind: str) -> str:
    if str(kind or "").strip() == "action":
        return (
            "你是视频工作流动作库的编辑助手。请识别这张动作参考图，并只返回一个合法 JSON 对象，"
            "不要返回 Markdown、代码块或任何额外说明。JSON 必须包含以下字段："
            '{"title":"简短的动作或姿势标题","text":"只描述人物动作和肢体空间关系的中文内容","tags":["动作标签"]}。'
            "识别重点只能是人物的动作、姿势、朝向、重心、肢体关系、身体部位位置和遮挡关系。"
            "请尽量说明头部、躯干、肩膀、手臂、手、髋部、腿和脚的相对位置；"
            "使用画面空间描述，例如头部位于画面左上方、大腿占据画面右下方、双手位于胸前等，"
            "并说明弯曲、伸展、抬起、交叉、支撑、行走或其他可见动作。"
            "title 用不超过 32 个中文字符概括动作或姿势；text 控制在 1000 字以内。"
            "tags 只填写 2 到 8 个动作、姿势、身体部位或空间位置相关的简洁中文标签，不要重复。"
            "严禁描述人物的服装、头发、发型、发饰、饰品、背景、环境、灯光、色彩、画面风格、人物身份或外貌；"
            "一级分类不要填写，由用户自行选择或新建。只能根据图片中可见的动作和姿势作答，不要臆测不可见内容。"
        )
    return (
        "你是视频工作流素材库的编辑助手。请识别这张图片，并只返回一个合法 JSON 对象，"
        "不要返回 Markdown、代码块或任何额外说明。JSON 必须包含以下字段："
        '{"title":"简短且准确的卡片标题","text":"适合视频工作流使用的中文文本描述","tags":["二级标签"]}。'
        f"这是一张{_kind_label(kind)}素材。title 用不超过 32 个中文字符概括主体和特点；"
        "text 写清楚主体、外观、姿态/动作、构图、环境或适合的使用场景，控制在 1000 字以内；"
        "tags 只填写 2 到 8 个简洁的中文二级标签，不要填写一级分类，不要包含重复标签。"
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {None, "text"}:
                parts.append(str(item.get("text") or ""))
        return "".join(parts).strip()
    return ""


def _parse_result(payload: Any) -> dict[str, Any]:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RhCliError("VISION_INVALID_RESPONSE", "阿里云视觉服务返回了无效响应。") from exc
    raw = _JSON_CODE_FENCE.sub("", _content_text(content)).strip()
    try:
        result = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise RhCliError("VISION_INVALID_RESPONSE", "阿里云视觉服务没有返回可识别的 JSON。")
        try:
            result = json.loads(match.group(0))
        except (ValueError, json.JSONDecodeError) as exc:
            raise RhCliError("VISION_INVALID_RESPONSE", "阿里云视觉服务返回的 JSON 无法解析。") from exc
    if not isinstance(result, dict):
        raise RhCliError("VISION_INVALID_RESPONSE", "阿里云视觉服务返回的数据格式不正确。")
    title = str(result.get("title") or "").strip()[:120]
    text = str(result.get("text") or "").strip()[:3000]
    raw_tags = result.get("tags")
    if isinstance(raw_tags, str):
        tags = [item.strip() for item in re.split(r"[,，、\s]+", raw_tags) if item.strip()]
    elif isinstance(raw_tags, list):
        tags = [str(item).strip() for item in raw_tags if str(item).strip()]
    else:
        tags = []
    deduplicated = []
    for tag in tags:
        if tag not in deduplicated:
            deduplicated.append(tag)
    if not title and not text:
        raise RhCliError("VISION_INVALID_RESPONSE", "阿里云视觉服务没有返回有效的标题或文本。")
    return {"title": title, "text": text, "tags": deduplicated[:12]}


class AliyunVisionClient:
    def __init__(self, api_key: str, model: str = ALIYUN_VISION_DEFAULT_MODEL) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or ALIYUN_VISION_DEFAULT_MODEL).strip()

    def _chat(self, body: dict[str, Any]) -> Any:
        if not self.api_key:
            raise RhCliError("VISION_NOT_CONFIGURED", "请先在设置中配置阿里云百炼 API Key。")
        try:
            response = httpx.post(
                ALIYUN_VISION_ENDPOINT,
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                timeout=90.0,
            )
        except httpx.TimeoutException as exc:
            raise RhCliError("VISION_TIMEOUT", "阿里云识图请求超时，请检查网络后重试。") from exc
        except httpx.HTTPError as exc:
            raise RhCliError("VISION_NETWORK_ERROR", "无法连接阿里云视觉服务，请检查网络。") from exc
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RhCliError("VISION_INVALID_RESPONSE", "阿里云视觉服务返回了无效响应。") from exc
        if response.status_code >= 400:
            raise RhCliError("VISION_REQUEST_FAILED", "阿里云视觉服务请求失败，请检查 API Key 和模型权限。")
        return payload

    def recognize(self, image_data_url: str, kind: str) -> dict[str, Any]:
        image_data_url = str(image_data_url or "").strip()
        if not self.api_key:
            raise RhCliError("VISION_NOT_CONFIGURED", "请先在设置中配置阿里云百炼 API Key。")
        if not image_data_url.startswith("data:image/") or ";base64," not in image_data_url:
            raise RhCliError("VISION_IMAGE_INVALID", "识图只支持 Base64 图片数据。")
        encoded = image_data_url.split(",", 1)[1]
        if len(encoded) > ALIYUN_VISION_MAX_IMAGE_BYTES * 4 // 3 + 1024:
            raise RhCliError("VISION_IMAGE_TOO_LARGE", "用于识图的图片不能超过 10 MB。")
        body = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "text", "text": _recognition_prompt(kind)},
                ],
            }],
            "temperature": 0.2,
        }
        return _parse_result(self._chat(body))
