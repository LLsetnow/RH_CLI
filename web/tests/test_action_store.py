from __future__ import annotations

import json
from pathlib import Path

import pytest

from web.action_store import ActionStore


RESOURCES = Path("/Users/apple/Documents/VideoMake/ref/pose/pose.json")


def _write_actions(path: Path, actions: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 6, "actions": actions}, ensure_ascii=False), encoding="utf-8")


def test_action_store_reads_pose_json_and_local_images(tmp_path):
    if not RESOURCES.is_file():
        pytest.skip("本机未安装 VideoMake 的 pose.json")
    store = ActionStore(tmp_path, source_path=RESOURCES)

    actions = store.actions()
    public_actions = store.public_actions()

    assert len(actions) == 57
    assert len(public_actions) == len(actions)
    assert actions[0]["title"]
    assert actions[0]["text"]
    assert actions[0]["category"] == "站立"
    assert actions[3]["tags"] == ["侧倾"]
    assert actions[3]["category"] == "站立"
    assert actions[4]["category"] == "坐姿"
    assert all(action["category"] not in action["tags"] for action in actions)
    assert all(item["depth_image_available"] for item in public_actions)
    assert all(item["image_available"] == item["color_image_available"] for item in public_actions)
    assert all(item["image_url"].startswith("/api/prompt/actions/") for item in public_actions if item["color_image_available"])
    assert all(item["pair_status"] == "paired" for item in public_actions)
    assert all(item["depth_image_url"].endswith("/depth") for item in public_actions)


def test_action_store_uses_json_source_without_a_derived_cache(tmp_path):
    source = tmp_path / "pose.json"
    _write_actions(source, [])

    store = ActionStore(tmp_path / "data", source_path=source)

    assert store.actions() == []
    assert not (tmp_path / "data" / "prompt" / "actions.json").exists()


def test_action_store_does_not_serve_an_unlisted_path(tmp_path):
    if not RESOURCES.is_file():
        pytest.skip("本机未安装 VideoMake 的 pose.json")
    store = ActionStore(tmp_path, source_path=RESOURCES)

    assert store.image_path("missing-action") is None
    assert store.image_path("missing-action", "depth") is None


def test_action_store_reports_missing_and_mismatched_pairs(tmp_path):
    root = tmp_path / "ref"
    color_root = root / "pose" / "color"
    depth_root = root / "pose" / "depth"
    color_root.mkdir(parents=True)
    depth_root.mkdir(parents=True)
    (color_root / "paired.jpg").write_bytes(b"color")
    (depth_root / "paired_depth.png").write_bytes(b"depth")
    (color_root / "missing-depth.jpg").write_bytes(b"color")
    (color_root / "mismatched.jpg").write_bytes(b"color")
    (depth_root / "other_depth.png").write_bytes(b"depth")
    (depth_root / "depth-only_depth.png").write_bytes(b"depth")
    source = root / "pose" / "pose.json"
    _write_actions(source, [
        {"id": "paired", "category": "站立", "tags": ["站立", "侧倾"], "title": "paired", "text": "Paired prompt.", "color_image_path": "pose/color/paired.jpg", "depth_image_path": "pose/depth/paired_depth.png"},
        {"id": "missing", "category": "站立", "title": "missing-depth", "text": "Missing depth prompt.", "color_image_path": "pose/color/missing-depth.jpg"},
        {"id": "mismatch", "category": "站立", "title": "mismatched", "text": "Mismatched prompt.", "color_image_path": "pose/color/mismatched.jpg", "depth_image_path": "pose/depth/other_depth.png"},
        {"id": "depth-only", "category": "站立", "title": "depth-only", "text": "Depth-only prompt.", "depth_image_path": "pose/depth/depth-only_depth.png"},
    ])

    store = ActionStore(tmp_path / "data", source_path=source)
    actions = {item["title"]: item for item in store.public_actions()}

    assert actions["paired"]["category"] == "站立"
    assert actions["paired"]["tags"] == ["侧倾"]
    assert actions["paired"]["pair_status"] == "paired"
    assert actions["missing-depth"]["pair_status"] == "missing_depth"
    assert actions["mismatched"]["pair_status"] == "mismatched"
    assert actions["depth-only"]["pair_status"] == "missing_color"
    assert actions["depth-only"]["color_image_available"] is False
    assert actions["depth-only"]["depth_image_available"] is True
    assert actions["paired"]["skeleton_image_available"] is False


def test_action_store_exposes_and_renames_skeleton_image(tmp_path):
    root = tmp_path / "ref"
    color_root = root / "pose" / "color"
    depth_root = root / "pose" / "depth"
    skeleton_root = root / "pose" / "skeleton"
    color_root.mkdir(parents=True)
    depth_root.mkdir(parents=True)
    skeleton_root.mkdir(parents=True)
    (color_root / "old-name.jpg").write_bytes(b"color")
    (depth_root / "old-name_depth.png").write_bytes(b"depth")
    (skeleton_root / "old-name_skeleton.png").write_bytes(b"skeleton")
    source = root / "pose" / "pose.json"
    _write_actions(source, [{
        "id": "pose-skeleton", "category": "站立", "title": "old-name", "text": "Prompt",
        "color_image_path": "pose/color/old-name.jpg", "depth_image_path": "pose/depth/old-name_depth.png",
        "skeleton_image_path": "pose/skeleton/old-name_skeleton.png",
    }])
    store = ActionStore(tmp_path / "data", source_root=root)

    public = store.public_actions()[0]
    assert public["skeleton_image_available"] is True
    assert public["skeleton_image_url"].endswith("/skeleton")
    assert store.image_path("pose-skeleton", "skeleton") == skeleton_root / "old-name_skeleton.png"

    updated = store.update_action("pose-skeleton", {"title": "新动作名"})
    assert updated["skeleton_image_path"] == "pose/skeleton/新动作名_skeleton.png"
    assert (skeleton_root / "新动作名_skeleton.png").read_bytes() == b"skeleton"
    assert not (skeleton_root / "old-name_skeleton.png").exists()


def test_action_store_reindexes_when_json_content_changes(tmp_path):
    source = tmp_path / "pose.json"
    action = {"id": "stable", "category": "站立", "title": "stable", "text": "First prompt."}
    _write_actions(source, [action])
    store = ActionStore(tmp_path / "data", source_path=source)
    first = store.public_actions()[0]

    action["text"] = "Updated prompt."
    _write_actions(source, [action])
    store.refresh()
    second = store.public_actions()[0]

    assert first["id"] == second["id"]
    assert second["text"] == "Updated prompt."


def test_action_store_writes_added_and_updated_entries_back_to_json(tmp_path):
    root = tmp_path / "ref"
    (root / "pose" / "color").mkdir(parents=True)
    (root / "pose" / "depth").mkdir(parents=True)
    (root / "pose" / "color" / "new.jpg").write_bytes(b"color")
    (root / "pose" / "depth" / "new_depth.png").write_bytes(b"depth")
    source = root / "pose" / "pose.json"
    _write_actions(source, [{
        "id": "pose-stable", "category": "站立", "title": "old", "text": "Old text",
        "color_image_path": "pose/color/new.jpg", "depth_image_path": "pose/depth/new_depth.png",
    }])
    store = ActionStore(tmp_path / "data", source_root=root)

    updated = store.update_action("pose-stable", {"title": "改名", "tags": ["侧倾"], "text": "Updated text"})
    assert updated["title"] == "改名"
    content = json.loads(source.read_text(encoding="utf-8"))
    assert content["actions"][0]["id"] == "pose-stable"
    assert content["actions"][0]["tags"] == ["侧倾"]
    assert content["actions"][0]["text"] == "Updated text"

    added = store.add_action({
        "category": "坐姿",
        "title": "新动作",
        "text": "New action",
        "tags": ["测试"],
        "color_image_path": "pose/color/new.jpg",
        "depth_image_path": "pose/depth/new_depth.png",
    })
    assert added["title"] == "新动作"
    assert any(item["title"] == "新动作" for item in json.loads(source.read_text(encoding="utf-8"))["actions"])
    assert any(item["id"] == added["id"] for item in store.actions())


def test_action_store_renames_color_and_depth_files_when_title_changes(tmp_path):
    root = tmp_path / "ref"
    color_root = root / "pose" / "color"
    depth_root = root / "pose" / "depth"
    color_root.mkdir(parents=True)
    depth_root.mkdir(parents=True)
    (color_root / "old-name.jpg").write_bytes(b"color")
    (depth_root / "old-name_depth.png").write_bytes(b"depth")
    source = root / "pose" / "pose.json"
    _write_actions(source, [{
        "id": "pose-rename", "category": "站立", "title": "old-name", "text": "Prompt",
        "color_image_path": "pose/color/old-name.jpg", "depth_image_path": "pose/depth/old-name_depth.png",
    }])
    store = ActionStore(tmp_path / "data", source_root=root)

    updated = store.update_action("pose-rename", {"title": "新动作名"})

    assert updated["color_image_path"] == "pose/color/新动作名.jpg"
    assert updated["depth_image_path"] == "pose/depth/新动作名_depth.png"
    assert (color_root / "新动作名.jpg").read_bytes() == b"color"
    assert (depth_root / "新动作名_depth.png").read_bytes() == b"depth"
    assert not (color_root / "old-name.jpg").exists()
    assert not (depth_root / "old-name_depth.png").exists()
    content = json.loads(source.read_text(encoding="utf-8"))["actions"][0]
    assert content["title"] == "新动作名"
    assert content["color_image_path"] == "pose/color/新动作名.jpg"
    assert content["depth_image_path"] == "pose/depth/新动作名_depth.png"


def test_action_store_does_not_overwrite_existing_target_pair(tmp_path):
    root = tmp_path / "ref"
    color_root = root / "pose" / "color"
    depth_root = root / "pose" / "depth"
    color_root.mkdir(parents=True)
    depth_root.mkdir(parents=True)
    (color_root / "old-name.jpg").write_bytes(b"old-color")
    (depth_root / "old-name_depth.png").write_bytes(b"old-depth")
    (color_root / "新动作.jpg").write_bytes(b"existing-color")
    (depth_root / "新动作_depth.png").write_bytes(b"existing-depth")
    source = root / "pose" / "pose.json"
    _write_actions(source, [{
        "id": "pose-collision", "category": "站立", "title": "old-name", "text": "Prompt",
        "color_image_path": "pose/color/old-name.jpg", "depth_image_path": "pose/depth/old-name_depth.png",
    }])
    store = ActionStore(tmp_path / "data", source_root=root)

    with pytest.raises(ValueError, match="目标文件已存在"):
        store.update_action("pose-collision", {"title": "新动作"})

    assert (color_root / "old-name.jpg").read_bytes() == b"old-color"
    assert (depth_root / "old-name_depth.png").read_bytes() == b"old-depth"
    assert (color_root / "新动作.jpg").read_bytes() == b"existing-color"
    assert (depth_root / "新动作_depth.png").read_bytes() == b"existing-depth"
