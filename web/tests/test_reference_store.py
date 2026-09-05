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
    def write(kind: str, references: list[dict]) -> None:
        (root / kind / f"{kind}.json").write_text(
            json.dumps({"version": VERSION, "kind": kind, "references": references}, ensure_ascii=False),
            encoding="utf-8",
        )
    write("character", [{"id": "character-hero", "category": "二次元", "tags": ["人物", "主角"], "source_tags": [], "title": "主角 · 正脸", "text": "主角脸部参考。", "image_path": "hero.png", "audio_path": ""}])
    write("audio", [{"id": "audio-hero", "category": "音色", "tags": ["音频"], "source_tags": [], "title": "主角音色", "text": "主角的对白音色参考。", "image_path": "", "audio_path": "voice.mp3"}])
    write("background", [{"id": "background-room", "category": "室内", "tags": ["背景"], "source_tags": [], "title": "房间", "text": "室内场景参考。", "image_path": "room.jpg", "audio_path": ""}])
    write("clothes", [{"id": "clothes-uniform", "category": "制服", "tags": ["服装"], "source_tags": [], "title": "学院制服", "text": "学院制服参考。", "image_path": "uniform.webp", "audio_path": ""}])
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

    assert not (tmp_path / "data" / "prompt" / "references.json").exists()


def test_reference_store_keeps_legacy_nested_character_variants_readable(tmp_path):
    root = _reference_root(tmp_path)
    character = root / "character" / "character.json"
    character.write_text(json.dumps({"version": VERSION, "references": [{"id": "character-variant", "category": "二次元", "tags": ["人物", "主角"], "title": "主角 · 三视图", "text": "主角三视图。", "image_path": "hero.png", "audio_path": ""}]}, ensure_ascii=False), encoding="utf-8")
    store = ReferenceStore(tmp_path / "data", root)

    item = next(item for item in store.references() if item["kind"] == "character")
    assert item["title"] == "主角 · 三视图"
    assert item["category"] == "二次元"
    assert item["tags"] == ["人物", "主角"]


def test_reference_store_preserves_explicit_entry_tags(tmp_path):
    root = _reference_root(tmp_path)
    character = root / "character" / "character.json"
    character.write_text(json.dumps({"version": VERSION, "references": [{"id": "character-hero", "category": "二次元", "tags": ["人物", "头像", "二次元", "3D", "主角"], "source_tags": ["头像", "二次元", "3D"], "title": "主角 · 正脸", "text": "主角脸部参考。", "image_path": "hero.png", "audio_path": ""}]}, ensure_ascii=False), encoding="utf-8")
    store = ReferenceStore(tmp_path / "data", root)

    item = next(item for item in store.references() if item["kind"] == "character")
    assert item["tags"] == ["人物", "头像", "二次元", "3D", "主角"]


def test_reference_store_refreshes_from_json_source_changes(tmp_path):
    root = _reference_root(tmp_path)
    character = root / "character" / "character.json"
    store = ReferenceStore(tmp_path / "data", root)
    document = json.loads(character.read_text(encoding="utf-8"))
    document["references"][0]["text"] = "已更新的人物参考。"
    character.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    store.refresh()

    item = next(item for item in store.references() if item["kind"] == "character")
    assert item["text"] == "已更新的人物参考。"


def test_reference_store_unwraps_angle_bracket_paths_and_keeps_same_titles_distinct(tmp_path):
    root = _reference_root(tmp_path)
    (root / "character" / "千夏").mkdir()
    (root / "character" / "樱井宁宁").mkdir()
    (root / "character" / "千夏" / "4.jpg").write_bytes(b"first")
    (root / "character" / "樱井宁宁" / "4.jpg").write_bytes(b"second")
    (root / "character" / "character.json").write_text(json.dumps({"version": VERSION, "references": [
        {"id": "character-one", "category": "千夏", "tags": ["人物"], "title": "4.jpg", "text": "千夏参考。", "image_path": "千夏/4.jpg", "audio_path": ""},
        {"id": "character-two", "category": "樱井宁宁", "tags": ["人物"], "title": "4.jpg", "text": "樱井宁宁参考。", "image_path": "樱井宁宁/4.jpg", "audio_path": ""},
    ]}, ensure_ascii=False), encoding="utf-8")
    store = ReferenceStore(tmp_path / "data", root)

    items = [item for item in store.references() if item["kind"] == "character"]
    assert len(items) == 2
    assert len({item["id"] for item in items}) == 2
    assert {item["image_path"] for item in items} == {"千夏/4.jpg", "樱井宁宁/4.jpg"}
    assert {store.media_path(item["id"], "image").parent.name for item in items} == {"千夏", "樱井宁宁"}


def test_reference_api_exposes_library_and_local_image(tmp_path, monkeypatch):
    source_root = Path("/Users/apple/Documents/VideoMake/ref")
    source_file = source_root / "background" / "background.json"
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
        assert payload["kind_counts"] == {"character": 157, "audio": 6, "background": 14, "clothes": 6}
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


def test_reference_store_uses_per_kind_configured_json_paths(tmp_path):
    root = _reference_root(tmp_path)
    alternate = tmp_path / "alternate-character.json"
    alternate.write_text(json.dumps({"version": VERSION, "references": [{"id": "character-alt", "category": "写实", "tags": ["人物"], "title": "另一个人物", "text": "另一个人物参考。"}]}, ensure_ascii=False), encoding="utf-8")
    store = ReferenceStore(
        tmp_path / "data",
        root,
        {"character": alternate},
    )

    assert store.kind_counts() == {"character": 1, "audio": 1, "background": 1, "clothes": 1}
    assert store.references()[0]["title"] == "另一个人物"
    assert store.source_paths()["character"] == str(alternate.resolve())
    assert store.source_status()["source_paths"]["character"] == str(alternate.resolve())


def test_reference_store_writes_tags_and_new_entries_back_to_kind_json(tmp_path):
    root = _reference_root(tmp_path)
    store = ReferenceStore(tmp_path / "data", root)
    character = next(item for item in store.references() if item["kind"] == "character")

    updated = store.update_reference(character["id"], {"tags": ["头像", "主角"], "text": "已更新的人物文本"})
    source = root / "character" / "character.json"
    content = json.loads(source.read_text(encoding="utf-8"))
    assert updated["source_tags"] == ["头像", "主角"]
    saved = next(item for item in content["references"] if item["id"] == character["id"])
    assert saved["tags"] == ["人物", "头像", "主角"]
    assert saved["text"] == "已更新的人物文本"

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
    assert any(item["title"] == "新人物" for item in json.loads(source.read_text(encoding="utf-8"))["references"])


def test_reference_store_renames_target_image_when_title_changes(tmp_path):
    root = _reference_root(tmp_path)
    store = ReferenceStore(tmp_path / "data", root)
    character = next(item for item in store.references() if item["kind"] == "character")

    updated = store.update_reference(character["id"], {"title": "新人物名"})

    assert updated["image_path"] == "新人物名.png"
    assert (root / "character" / "新人物名.png").read_bytes() == b"png"
    assert not (root / "character" / "hero.png").exists()
    content = json.loads((root / "character" / "character.json").read_text(encoding="utf-8"))
    renamed = next(item for item in content["references"] if item["id"] == character["id"])
    assert renamed["title"] == "新人物名"
    assert renamed["image_path"] == "新人物名.png"


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
        saved = json.loads((root / "background" / "background.json").read_text(encoding="utf-8"))
        assert any(item["text"] == "已更新" for item in saved["references"])

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
