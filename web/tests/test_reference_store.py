from __future__ import annotations

import json
import http.client
import threading
from pathlib import Path

import pytest

from web import app as web_app
from web import server as web_server
from web.reference_store import ReferenceStore


def _reference_root(tmp_path: Path) -> Path:
    root = tmp_path / "ref"
    (root / "character").mkdir(parents=True)
    (root / "audio").mkdir()
    (root / "background").mkdir()
    (root / "clothes").mkdir()
    (root / "character" / "hero.png").write_bytes(b"png")
    (root / "audio" / "voice.mp3").write_bytes(b"audio")
    (root / "background" / "room.jpg").write_bytes(b"jpg")
    (root / "clothes" / "uniform.webp").write_bytes(b"webp")
    (root / "character" / "character.md").write_text(
        "## character\n\n### 二次元\n\n#### 主角 · 正脸\n\n![200](hero.png)\n\n> 主角脸部参考。\n",
        encoding="utf-8",
    )
    (root / "audio" / "audio.md").write_text(
        "## audio\n\n### 音色\n\n#### 主角音色\n\n[音频](voice.mp3)\n\n> 主角的对白音色参考。\n",
        encoding="utf-8",
    )
    (root / "background" / "background.md").write_text(
        "## background\n\n### 室内\n\n#### 房间\n\n![200](room.jpg)\n\n> 室内场景参考。\n",
        encoding="utf-8",
    )
    (root / "clothes" / "clothes.md").write_text(
        "## clothes\n\n### 制服\n\n#### 学院制服\n\n![200](uniform.webp)\n\n> 学院制服参考。\n",
        encoding="utf-8",
    )
    return root


def test_reference_store_parses_all_resource_kinds_and_serves_media(tmp_path):
    root = _reference_root(tmp_path)
    store = ReferenceStore(tmp_path / "data", root)

    assert store.kind_counts() == {"character": 1, "audio": 1, "background": 1, "clothes": 1}
    public = store.public_references()
    assert {item["kind"] for item in public} == {"character", "audio", "background", "clothes"}
    character = next(item for item in public if item["kind"] == "character")
    audio = next(item for item in public if item["kind"] == "audio")
    assert character["tags"] == ["人物", "二次元", "主角"]
    assert character["image_url"].endswith("/image")
    assert store.media_path(character["id"], "image").name == "hero.png"
    assert audio["audio_url"].endswith("/audio")
    assert store.media_path(audio["id"], "audio").name == "voice.mp3"

    cached = json.loads((tmp_path / "data" / "prompt" / "references.json").read_text(encoding="utf-8"))
    assert cached["source_sha256"]
    assert len(cached["references"]) == 4


def test_reference_store_keeps_legacy_nested_character_variants_readable(tmp_path):
    root = _reference_root(tmp_path)
    character = root / "character" / "character.md"
    character.write_text(
        "## character\n\n### 二次元\n\n#### 主角\n\n##### 三视图\n\n![200](hero.png)\n\n> 主角三视图。\n",
        encoding="utf-8",
    )
    store = ReferenceStore(tmp_path / "data", root)

    item = next(item for item in store.references() if item["kind"] == "character")
    assert item["title"] == "主角 · 三视图"
    assert item["tags"] == ["人物", "二次元", "主角"]


def test_reference_api_exposes_library_and_local_image(tmp_path, monkeypatch):
    source_root = Path("/Users/apple/Documents/VideoMake/ref")
    source_file = source_root / "background" / "background.md"
    if not source_file.is_file():
        pytest.skip("本机未安装 VideoMake 的参考资源索引")
    data_root = tmp_path / "data"
    monkeypatch.setattr(web_app, "DATA_ROOT", data_root)
    monkeypatch.setattr(web_app, "WORKFLOW_ROOT", data_root / "workflows")
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", data_root / "outputs")
    monkeypatch.setattr(web_app, "KEYS_PATH", data_root / "keys.json")
    monkeypatch.setattr(web_app, "DB_PATH", data_root / "tasks.sqlite3")
    monkeypatch.setattr(web_app, "default_local_output_dir", lambda: data_root / "outputs")
    monkeypatch.setattr(web_server, "DATA_ROOT", data_root)

    server = web_server.AppServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        connection.request("GET", "/api/prompt/references")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["kind_counts"] == {"character": 72, "audio": 6, "background": 14, "clothes": 6}
        background = next(item for item in payload["references"] if item["kind"] == "background" and item["image_available"])

        connection.request("GET", background["image_url"])
        image_response = connection.getresponse()
        assert image_response.status == 200
        assert image_response.getheader("Content-Type", "").startswith("image/")
        assert image_response.read()

        connection.request("GET", background["image_url"].replace("/image", "/image-path"))
        path_response = connection.getresponse()
        path_payload = json.loads(path_response.read())
        assert path_response.status == 200
        assert Path(path_payload["path"]).is_file()
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_reference_store_uses_per_kind_configured_markdown_paths(tmp_path):
    root = _reference_root(tmp_path)
    alternate = tmp_path / "alternate-character.md"
    alternate.write_text(
        "## character\n\n### 写实\n\n#### 另一个人物\n\n> 另一个人物参考。\n",
        encoding="utf-8",
    )
    store = ReferenceStore(
        tmp_path / "data",
        root,
        {"character": alternate},
    )

    assert store.kind_counts() == {"character": 1, "audio": 1, "background": 1, "clothes": 1}
    assert store.references()[0]["title"] == "另一个人物"
    assert store.source_paths()["character"] == str(alternate.resolve())
    assert store.source_status()["source_paths"]["character"] == str(alternate.resolve())
