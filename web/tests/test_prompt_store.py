from __future__ import annotations

import json

from web.prompt_store import PromptStore


def test_prompt_store_creates_json_documents_and_round_trips(tmp_path):
    store = PromptStore(tmp_path)
    block = store.add_block({"title": "主体", "text": "清晰主体", "tags": ["画面", "画面", ""]})
    state = store.save_state([
        {"instanceId": "item-1", "kind": "fixed", "sourceId": block["id"], "title": block["title"], "text": block["text"], "tags": block["tags"]},
        {"instanceId": "item-2", "kind": "text", "text": "补充条件"},
    ])
    group = store.save_group("电影镜头", state["items"])

    assert {path.name for path in tmp_path.glob("prompt-*.json")} == {
        "prompt-library.json", "prompt-state.json", "prompt-groups.json"
    }
    assert json.loads((tmp_path / "prompt-library.json").read_text())["blocks"][0]["tags"] == ["画面"]
    reloaded = PromptStore(tmp_path).snapshot()
    assert reloaded["library"]["blocks"] == [block]
    assert reloaded["state"]["items"][0]["block_id"] == block["id"]
    assert reloaded["groups"]["groups"][0]["id"] == group["id"]
    assert len(reloaded["groups"]["groups"][0]["items"]) == 2


def test_deleted_library_block_keeps_group_order_and_snapshot(tmp_path):
    store = PromptStore(tmp_path)
    block = store.add_block({"title": "光线", "text": "柔和自然光", "tags": ["风格"]})
    item = {"instance_id": "item-1", "kind": "fixed", "block_id": block["id"], "snapshot": {"title": block["title"], "text": block["text"], "tags": block["tags"]}}
    store.save_state([item])
    store.save_group("保留历史", [item])

    store.delete_block(block["id"])
    snapshot = store.snapshot()
    assert snapshot["library"]["blocks"] == []
    assert snapshot["state"]["items"] == [item]
    assert snapshot["groups"]["groups"][0]["items"] == [item]


def test_update_library_block_preserves_id_and_refreshes_references(tmp_path):
    store = PromptStore(tmp_path)
    block = store.add_block({"title": "原标题", "text": "原文本", "tags": ["原标签"]})
    item = {"instance_id": "item-1", "kind": "fixed", "block_id": block["id"], "snapshot": block}
    store.save_state([item])
    store.save_group("待更新", [item])

    updated = store.update_block(block["id"], {"title": "新标题", "text": "新文本", "tags": ["新标签"]})
    snapshot = store.snapshot()
    expected_snapshot = {"title": "新标题", "text": "新文本", "tags": ["新标签"]}
    assert updated == {"id": block["id"], **expected_snapshot}
    assert snapshot["state"]["items"][0]["snapshot"] == expected_snapshot
    assert snapshot["groups"]["groups"][0]["items"][0]["snapshot"] == expected_snapshot


def test_migrate_legacy_browser_state_once(tmp_path):
    store = PromptStore(tmp_path)
    migrated = store.migrate(
        [{"id": "custom-old", "title": "旧积木", "text": "保留这段", "tags": ["旧"]}],
        [{"instanceId": "legacy-item", "kind": "fixed", "sourceId": "custom-old", "title": "旧积木", "text": "保留这段", "tags": ["旧"]}],
    )

    assert migrated["library"]["blocks"][0]["id"] == "custom-old"
    assert migrated["state"]["items"][0]["instance_id"] == "legacy-item"
    assert migrated["state"]["items"][0]["block_id"] == "custom-old"
    assert migrated["state"]["items"][0]["snapshot"]["title"] == "旧积木"


def test_action_items_keep_action_id_and_order(tmp_path):
    store = PromptStore(tmp_path)
    items = [
        {"instanceId": "action-item-1", "kind": "action", "actionId": "pose-one", "title": "动作一", "text": "第一段", "tags": ["pose"]},
        {"instanceId": "text-item", "kind": "text", "text": "补充镜头"},
        {"instanceId": "action-item-2", "kind": "action", "action_id": "pose-two", "snapshot": {"title": "动作二", "text": "第二段", "tags": ["pose"]}},
    ]

    state = store.save_state(items)
    group = store.save_group("动作顺序", state["items"])
    reloaded = PromptStore(tmp_path).snapshot()

    assert [item["kind"] for item in reloaded["state"]["items"]] == ["action", "text", "action"]
    assert [item["action_id"] for item in reloaded["groups"]["groups"][0]["items"] if item["kind"] == "action"] == ["pose-one", "pose-two"]
    assert reloaded["groups"]["groups"][0]["id"] == group["id"]
