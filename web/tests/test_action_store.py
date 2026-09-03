from __future__ import annotations

from pathlib import Path

import pytest

from web.action_store import ActionStore


RESOURCES = Path("/Users/apple/Documents/VideoMake/ref/pose/pose.md")


def test_action_store_parses_pose_library_and_local_images(tmp_path):
    if not RESOURCES.is_file():
        pytest.skip("本机未安装 VideoMake 的 pose.md")
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
    assert (tmp_path / "prompt" / "actions.json").is_file()


def test_action_store_moves_legacy_cache_into_prompt_directory(tmp_path):
    legacy = tmp_path / "prompt-actions.json"
    legacy.write_text('{"version": 2, "actions": []}', encoding="utf-8")

    store = ActionStore(tmp_path, source_path=tmp_path / "missing-Resources.md")

    assert not legacy.exists()
    assert store.path == tmp_path / "prompt" / "actions.json"
    assert store._read()["actions"] == []


def test_action_store_does_not_serve_an_unlisted_path(tmp_path):
    if not RESOURCES.is_file():
        pytest.skip("本机未安装 VideoMake 的 pose.md")
    store = ActionStore(tmp_path, source_path=RESOURCES)

    assert store.image_path("missing-action") is None
    assert store.image_path("missing-action", "depth") is None


def test_action_store_reports_missing_and_mismatched_pairs(tmp_path):
    source = tmp_path / "Resources.md"
    color_root = tmp_path / "pose" / "color"
    depth_root = tmp_path / "pose" / "depth"
    color_root.mkdir(parents=True)
    depth_root.mkdir(parents=True)
    (color_root / "paired.jpg").write_bytes(b"color")
    (depth_root / "paired_depth.png").write_bytes(b"depth")
    (color_root / "missing-depth.jpg").write_bytes(b"color")
    (color_root / "mismatched.jpg").write_bytes(b"color")
    (depth_root / "other_depth.png").write_bytes(b"depth")
    (depth_root / "depth-only_depth.png").write_bytes(b"depth")
    source.write_text(
        """## pose

### 一、站立

#### paired.jpg
tags: 站立, 侧倾
![200](pose/color/paired.jpg)![200](pose/depth/paired_depth.png)

> Paired prompt.

#### missing-depth.jpg
![200](pose/color/missing-depth.jpg)

> Missing depth prompt.

#### mismatched.jpg
![200](pose/color/mismatched.jpg)![200](pose/depth/other_depth.png)

> Mismatched prompt.

#### depth-only.jpg（仅深度图，待补彩色原图）
![200](pose/depth/depth-only_depth.png)

> Depth-only prompt.
""",
        encoding="utf-8",
    )

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


def test_action_store_reindexes_when_resources_content_changes(tmp_path):
    source = tmp_path / "Resources.md"
    color_root = tmp_path / "pose" / "color"
    color_root.mkdir(parents=True)
    (color_root / "stable.jpg").write_bytes(b"color")
    source.write_text(
        """## pose

### 一、站立

#### stable.jpg
![200](pose/color/stable.jpg)

> First prompt.
""",
        encoding="utf-8",
    )
    store = ActionStore(tmp_path / "data", source_path=source)
    first = store.public_actions()[0]
    cache = store._read()

    source.write_text(
        source.read_text(encoding="utf-8").replace("First prompt", "Updated prompt"),
        encoding="utf-8",
    )
    store.refresh()
    second = store.public_actions()[0]

    assert first["id"] == second["id"]
    assert second["text"] == "Updated prompt."
    assert store._read()["source_sha256"] != cache["source_sha256"]
