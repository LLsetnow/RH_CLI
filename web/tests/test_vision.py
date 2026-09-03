from __future__ import annotations

import json
import threading

from web.app import LocalStore
from web.server import prompt_image_data_url
from web.vision import AliyunVisionClient


def test_aliyun_vision_client_sends_image_and_parses_card_fields(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            content = "```json\n" + json.dumps(
                {"title": "夜间街道", "text": "雨夜街道上的人物侧身行走。", "tags": ["夜景", "行走", "夜景"]},
                ensure_ascii=False,
            ) + "\n```"
            return {
                "choices": [{"message": {"content": content}}]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("web.vision.httpx.post", fake_post)
    image = "data:image/png;base64," + ("a" * 24)

    result = AliyunVisionClient("sk-test-key").recognize(image, "action")

    assert result == {"title": "夜间街道", "text": "雨夜街道上的人物侧身行走。", "tags": ["夜景", "行走"]}
    assert captured["url"].endswith("/compatible-mode/v1/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-test-key"
    body = captured["json"]
    assert body["model"] == "qwen-vl-max"
    assert body["messages"][0]["content"][0]["image_url"]["url"] == image
    action_prompt = body["messages"][0]["content"][1]["text"]
    assert "一级分类" in action_prompt
    assert "头部位于画面左上方" in action_prompt
    assert "严禁描述人物的服装、头发、发型、发饰、饰品、背景" in action_prompt
    assert "sk-test-key" not in json.dumps(body, ensure_ascii=False)


def test_aliyun_vision_settings_only_exposes_a_hint(monkeypatch):
    store = object.__new__(LocalStore)
    store._lock = threading.RLock()
    store._read_json_file = lambda: {"aliyun_vision_api_key": "sk-local-secret"}
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("ALIYUN_VISION_API_KEY", raising=False)

    public = store.aliyun_vision_settings()

    assert public == {
        "configured": True,
        "api_key_hint": "sk-l••••cret",
        "model": "qwen-vl-max",
        "source": "local",
    }
    assert "sk-local-secret" not in json.dumps(public)


def test_prompt_image_data_url_stays_inside_the_media_root(tmp_path):
    image = tmp_path / "character" / "one.png"
    image.parent.mkdir()
    image.write_bytes(b"png-bytes")

    data_url = prompt_image_data_url("character/one.png", [tmp_path])

    assert data_url == "data:image/png;base64,cG5nLWJ5dGVz"

    try:
        prompt_image_data_url("../outside.png", [tmp_path])
    except Exception as error:
        assert getattr(error, "code", "") == "VISION_IMAGE_INVALID"
    else:
        raise AssertionError("path traversal should be rejected")
