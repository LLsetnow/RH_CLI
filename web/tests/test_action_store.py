from __future__ import annotations

from pathlib import Path

import pytest

from web.action_store import ActionStore


RESOURCES = Path("/Users/apple/Documents/VideoMake/ref/Resources.md")


def test_action_store_parses_pose_library_and_local_images(tmp_path):
    if not RESOURCES.is_file():
        pytest.skip("本机未安装 VideoMake 的 Resources.md")
    store = ActionStore(tmp_path, source_path=RESOURCES)

    actions = store.actions()
    public_actions = store.public_actions()

    assert len(actions) == 58
    assert len(public_actions) == len(actions)
    assert actions[0]["title"]
    assert actions[0]["text"]
    assert actions[0]["tags"]
    assert all(item["image_available"] for item in public_actions)
    assert all(item["image_url"].startswith("/api/prompt/actions/") for item in public_actions)
    assert (tmp_path / "prompt-actions.json").is_file()


def test_action_store_does_not_serve_an_unlisted_path(tmp_path):
    if not RESOURCES.is_file():
        pytest.skip("本机未安装 VideoMake 的 Resources.md")
    store = ActionStore(tmp_path, source_path=RESOURCES)

    assert store.image_path("missing-action") is None
