from __future__ import annotations

import json
from typing import Any

import httpx

from rh_cli.errors import RhCliError


ALIYUN_PROMPT_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
ALIYUN_PROMPT_DEFAULT_MODEL = "qwen-plus"
AI_PROMPT_MAX_CONTEXT_CHARS = 60000
AI_PROMPT_MAX_QUESTION_CHARS = 12000


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


def _response_text(payload: Any) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RhCliError("PROMPT_AI_INVALID_RESPONSE", "阿里云提示词服务返回了无效响应。") from exc
    result = _content_text(content)
    if not result:
        raise RhCliError("PROMPT_AI_INVALID_RESPONSE", "阿里云提示词服务没有返回文本内容。")
    return result


class AliyunPromptWriter:
    def __init__(self, api_key: str, model: str = ALIYUN_PROMPT_DEFAULT_MODEL) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or ALIYUN_PROMPT_DEFAULT_MODEL).strip()

    def write(self, context: str, question: str) -> dict[str, str]:
        context = str(context or "").strip()
        question = str(question or "").strip()
        if not question:
            raise RhCliError("PROMPT_AI_QUESTION_EMPTY", "请输入想让 AI 改写的内容。")
        if len(context) > AI_PROMPT_MAX_CONTEXT_CHARS:
            raise RhCliError("PROMPT_AI_CONTEXT_TOO_LONG", "提示词工作台内容过长，请先减少部分积木后重试。")
        if len(question) > AI_PROMPT_MAX_QUESTION_CHARS:
            raise RhCliError("PROMPT_AI_QUESTION_TOO_LONG", "输入内容不能超过 12000 个字符。")
        if not self.api_key:
            raise RhCliError("PROMPT_AI_NOT_CONFIGURED", "请先在设置中配置阿里云百炼 API Key。")

        user_content = "当前提示词工作台上下文：\n" + (context or "（当前没有其他上下文）") + "\n\n输入内容（问题）：\n" + question
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是视频生成提示词编辑助手。请根据提示词工作台上下文，回答用户的输入内容。"
                        "输出必须是可以直接放回输入框的中文提示词正文，只输出最终内容，不要解释写作过程，"
                        "不要输出 Markdown 代码块、标题前缀、JSON 或英文翻译。"
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.7,
        }
        try:
            response = httpx.post(
                ALIYUN_PROMPT_ENDPOINT,
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                timeout=90.0,
            )
        except httpx.TimeoutException as exc:
            raise RhCliError("PROMPT_AI_TIMEOUT", "阿里云提示词请求超时，请检查网络后重试。") from exc
        except httpx.HTTPError as exc:
            raise RhCliError("PROMPT_AI_NETWORK_ERROR", "无法连接阿里云提示词服务，请检查网络。") from exc

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RhCliError("PROMPT_AI_INVALID_RESPONSE", "阿里云提示词服务返回了无效响应。") from exc
        if response.status_code >= 400:
            raise RhCliError("PROMPT_AI_REQUEST_FAILED", "阿里云提示词请求失败，请检查 API Key 和模型权限。")
        return {"text": _response_text(payload), "model": self.model}
