from __future__ import annotations

import base64
import json
import http.client
import threading
from pathlib import Path

import pytest

from web import app as web_app
from web import server as web_server
from web.reference_store import VERSION, ReferenceStore


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
    background = next(item for item in public if item["kind"] == "background")
    assert character["category"] == "二次元"
    assert character["tags"] == ["人物", "主角"]
    assert background["category"] == "室内"
    assert background["tags"] == ["背景"]
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
    assert item["category"] == "二次元"
    assert item["tags"] == ["人物", "主角"]


def test_reference_store_preserves_explicit_entry_tags(tmp_path):
    root = _reference_root(tmp_path)
    character = root / "character" / "character.md"
    character.write_text(
        "## character\n\n### 二次元\n\n#### 主角 · 正脸\n\n"
        "tags: 头像, 二次元, 3D\n\n![200](hero.png)\n\n> 主角脸部参考。\n",
        encoding="utf-8",
    )
    store = ReferenceStore(tmp_path / "data", root)

    item = next(item for item in store.references() if item["kind"] == "character")
    assert item["tags"] == ["人物", "头像", "二次元", "3D", "主角"]


def test_reference_store_rebuilds_cache_after_parser_version_changes(tmp_path):
    root = _reference_root(tmp_path)
    character = root / "character" / "character.md"
    character.write_text(
        "## character\n\n### 二次元\n\n#### 主角 · 正脸\n\n"
        "tags: 头像, 二次元, 3D\n\n![200](hero.png)\n\n> 主角脸部参考。\n",
        encoding="utf-8",
    )
    data_root = tmp_path / "data"
    ReferenceStore(data_root, root)
    cache_path = data_root / "prompt" / "references.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["version"] = VERSION - 1
    cached["references"][0]["tags"] = ["人物"]
    cache_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")

    store = ReferenceStore(data_root, root)

    item = next(item for item in store.references() if item["kind"] == "character")
    assert item["tags"] == ["人物", "头像", "二次元", "3D", "主角"]


def test_reference_store_unwraps_angle_bracket_paths_and_keeps_same_titles_distinct(tmp_path):
    root = _reference_root(tmp_path)
    (root / "character" / "千夏").mkdir()
    (root / "character" / "樱井宁宁").mkdir()
    (root / "character" / "千夏" / "4.jpg").write_bytes(b"first")
    (root / "character" / "樱井宁宁" / "4.jpg").write_bytes(b"second")
    (root / "character" / "character.md").write_text(
        "## character\n\n"
        "### 千夏\n\n#### 4.jpg\n\n![200](<千夏/4.jpg>)\n\n> 千夏参考。\n\n"
        "### 樱井宁宁\n\n#### 4.jpg\n\n![200](<樱井宁宁/4.jpg>)\n\n> 樱井宁宁参考。\n",
        encoding="utf-8",
    )
    store = ReferenceStore(tmp_path / "data", root)

    items = [item for item in store.references() if item["kind"] == "character"]
    assert len(items) == 2
    assert len({item["id"] for item in items}) == 2
    assert {item["image_path"] for item in items} == {"千夏/4.jpg", "樱井宁宁/4.jpg"}
    assert {store.media_path(item["id"], "image").parent.name for item in items} == {"千夏", "樱井宁宁"}


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
        assert payload["kind_counts"] == {"character": 162, "audio": 6, "background": 14, "clothes": 6}
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


def test_reference_store_writes_tags_and_new_entries_back_to_kind_markdown(tmp_path):
    root = _reference_root(tmp_path)
    store = ReferenceStore(tmp_path / "data", root)
    character = next(item for item in store.references() if item["kind"] == "character")

    updated = store.update_reference(character["id"], {"tags": ["头像", "主角"], "text": "已更新的人物文本"})
    source = root / "character" / "character.md"
    content = source.read_text(encoding="utf-8")
    assert updated["source_tags"] == ["头像", "主角"]
    assert f"id: {character['id']}" in content
    assert "tags: 头像, 主角" in content
    assert "> 已更新的人物文本" in content

    (root / "character" / "new.png").write_bytes(b"png")
    added = store.add_reference("character", {
        "category": "写实",
        "title": "新人物",
        "text": "新人物文本",
        "tags": ["测试"],
        "image_path": "character/new.png",
    })
    assert added["title"] == "新人物"
    assert added["image_path"] == "character/new.png"
    assert "#### 新人物" in source.read_text(encoding="utf-8")


def test_reference_api_can_add_and_update_a_card_in_the_configured_root(tmp_path, monkeypatch):
    root = _reference_root(tmp_path)
    data_root = tmp_path / "data"
    monkeypatch.setattr(web_app, "DATA_ROOT", data_root)
    monkeypatch.setattr(web_app, "WORKFLOW_ROOT", data_root / "workflows")
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", data_root / "outputs")
    monkeypatch.setattr(web_app, "KEYS_PATH", data_root / "keys.json")
    monkeypatch.setattr(web_app, "DB_PATH", data_root / "tasks.sqlite3")
    monkeypatch.setattr(web_app, "default_local_output_dir", lambda: data_root / "outputs")
    monkeypatch.setattr(web_server, "DATA_ROOT", data_root)

    settings_store = web_app.LocalStore()
    settings_store.set_media_library_root(str(root))
    settings_store._db.close()
    server = web_server.AppServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        body = json.dumps({
            "kind": "background",
            "category": "新增",
            "title": "API 新背景",
            "text": "API 背景文本",
            "tags": ["测试"],
            "image_path": "background/room.jpg",
        }, ensure_ascii=False)
        connection.request("POST", "/api/prompt/references", body=body.encode("utf-8"), headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 201
        reference_id = payload["reference"]["id"]

        update = json.dumps({"tags": ["更新"], "text": "已更新"})
        connection.request("PUT", f"/api/prompt/references/{reference_id}", body=update.encode("utf-8"), headers={"Content-Type": "application/json"})
        update_response = connection.getresponse()
        updated_payload = json.loads(update_response.read())
        assert update_response.status == 200
        assert updated_payload["reference"]["source_tags"] == ["更新"]
        assert "> 已更新" in (root / "background" / "background.md").read_text(encoding="utf-8")

        media_body = json.dumps({
            "kind": "background",
            "category": "新增",
            "title": "API 素材背景",
            "text": "从浏览器导入的背景",
            "media": [{
                "role": "image",
                "name": "browser-import.png",
                "mime": "image/png",
                "data_url": "data:image/png;base64," + base64.b64encode(b"browser-image").decode("ascii"),
            }],
        }, ensure_ascii=False)
        connection.request("POST", "/api/prompt/references", body=media_body.encode("utf-8"), headers={"Content-Type": "application/json"})
        media_response = connection.getresponse()
        media_payload = json.loads(media_response.read())
        assert media_response.status == 201
        assert media_payload["reference"]["image_path"] == "background/browser-import.png"
        assert (root / "background" / "browser-import.png").read_bytes() == b"browser-image"
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()
