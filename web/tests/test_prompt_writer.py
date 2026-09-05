from __future__ import annotations

import json

import pytest

from rh_cli.errors import RhCliError
from web.prompt_writer import AliyunPromptWriter


def test_aliyun_prompt_writer_sends_text_only_and_reads_chinese_result(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "一个中文的视频生成提示词。"}}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("web.prompt_writer.httpx.post", fake_post)

    result = AliyunPromptWriter("sk-test-key").write(
        "【固定积木】\n提示词内容：电影感镜头\n\n【媒体积木】\n媒体类型：图片",
        "写一个温柔的开场镜头",
    )

    assert result == {"text": "一个中文的视频生成提示词。", "model": "qwen-plus"}
    assert captured["url"].endswith("/compatible-mode/v1/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-test-key"
    body = captured["json"]
    assert body["model"] == "qwen-plus"
    assert all(isinstance(message["content"], str) for message in body["messages"])
    assert "image_url" not in json.dumps(body, ensure_ascii=False)
    assert "mediaPath" not in json.dumps(body, ensure_ascii=False)
    assert "sk-test-key" not in json.dumps(body, ensure_ascii=False)


def test_aliyun_prompt_writer_requires_a_question():
    with pytest.raises(RhCliError) as error:
        AliyunPromptWriter("sk-test-key").write("上下文", "")

    assert getattr(error.value, "code", "") == "PROMPT_AI_QUESTION_EMPTY"
