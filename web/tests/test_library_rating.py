from __future__ import annotations

import json

from web.action_store import ActionStore
from web.prompt_store import PromptStore
from web.reference_store import ReferenceStore


def test_prompt_store_writes_and_clears_numeric_rating_tag(tmp_path):
    library = tmp_path / "library.json"
    library.write_text(json.dumps({"version": 1, "blocks": [{
        "id": "camera-push", "category": "镜头", "title": "推进", "text": "The camera pushes in.", "tags": ["运镜", "2"],
    }]}, ensure_ascii=False), encoding="utf-8")
    store = PromptStore(tmp_path / "data", library_path=library)

    rated = store.update_block_rating("camera-push", 5)
    assert rated["tags"] == ["运镜", "5"]
    assert json.loads(library.read_text(encoding="utf-8"))["blocks"][0]["tags"] == ["运镜", "5"]

    cleared = store.update_block_rating("camera-push", 0)
    assert cleared["tags"] == ["运镜"]
    assert json.loads(library.read_text(encoding="utf-8"))["blocks"][0]["tags"] == ["运镜"]


def test_action_store_writes_numeric_rating_to_pose_json(tmp_path):
    source = tmp_path / "pose.json"
    source.write_text(json.dumps({"version": 6, "actions": [{
        "id": "pose-push", "category": "站立", "title": "推进", "text": "The subject leans forward.", "tags": ["侧倾", "2"],
    }]}, ensure_ascii=False), encoding="utf-8")
    store = ActionStore(tmp_path / "data", source_path=source)

    rated = store.update_action_rating("pose-push", 4)
    assert rated["tags"] == ["侧倾", "4"]
    assert json.loads(source.read_text(encoding="utf-8"))["actions"][0]["tags"] == ["侧倾", "4"]

    store.update_action_rating("pose-push", 0)
    assert json.loads(source.read_text(encoding="utf-8"))["actions"][0]["tags"] == ["侧倾"]


def test_reference_store_writes_numeric_rating_without_persisting_derived_tags(tmp_path):
    root = tmp_path / "ref"
    character_dir = root / "character"
    character_dir.mkdir(parents=True)
    source = character_dir / "character.json"
    source.write_text(json.dumps({"version": 6, "references": [{
        "id": "character-hero", "category": "二次元", "tags": ["人物", "头像", "2"], "source_tags": ["头像", "2"],
        "title": "主角", "text": "Character reference.", "image_path": "hero.png",
    }]}, ensure_ascii=False), encoding="utf-8")
    store = ReferenceStore(tmp_path / "data", root)

    rated = store.update_reference_rating("character-hero", 3)
    assert rated["tags"] == ["人物", "头像", "3"]
    saved = json.loads(source.read_text(encoding="utf-8"))["references"][0]
    assert saved["source_tags"] == ["头像", "3"]
    assert saved["tags"] == ["人物", "头像", "3"]

    store.update_reference_rating("character-hero", 0)
    saved = json.loads(source.read_text(encoding="utf-8"))["references"][0]
    assert saved["source_tags"] == ["头像"]
    assert saved["tags"] == ["人物", "头像"]
