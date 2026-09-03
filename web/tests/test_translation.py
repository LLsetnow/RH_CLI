from __future__ import annotations

import json

from web.app import LocalStore
from web.translation import AliyunTranslationClient


def test_aliyun_translation_client_signs_json_request_and_reads_result(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"Code": 200, "Data": {"Translated": "A cinematic shot."}, "DetectedLanguage": "zh"}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("web.translation.httpx.post", fake_post)

    result = AliyunTranslationClient("test-access-key", "test-access-secret").translate("一个电影感镜头")

    assert result == {"translated_text": "A cinematic shot.", "detected_language": "zh"}
    assert captured["url"].endswith("/api/translate/web/general")
    assert json.loads(captured["content"].decode("utf-8")) == {
        "FormatType": "text",
        "Scene": "general",
        "SourceLanguage": "auto",
        "SourceText": "一个电影感镜头",
        "TargetLanguage": "en",
    }
    assert captured["headers"]["Content-MD5"]
    assert captured["headers"]["Authorization"].startswith("acs test-access-key:")
    assert "test-access-secret" not in captured["headers"]["Authorization"]


def test_local_store_keeps_translation_secret_private_and_supports_environment(monkeypatch):
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "env-access-key")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "env-access-secret")
    # Verify the credential helper without touching the real local data directory.
    store = object.__new__(LocalStore)
    store._lock = __import__("threading").RLock()
    store._read_json_file = lambda: {}
    assert store.aliyun_translation_credentials() == ("env-access-key", "env-access-secret")
    public = store.aliyun_translation_settings()
    assert public["configured"] is True
    assert public["source"] == "environment"
    assert "env-access-secret" not in public
