from __future__ import annotations

import json

from web.prompt_store import PromptStore


def test_prompt_store_creates_markdown_library_and_json_state_documents(tmp_path):
    store = PromptStore(tmp_path)
    block = store.add_block({"title": "主体", "text": "清晰主体", "tags": ["画面", "画面", ""]})
    state = store.save_state([
        {"instanceId": "item-1", "kind": "fixed", "sourceId": block["id"], "title": block["title"], "text": block["text"], "tags": block["tags"]},
        {"instanceId": "item-2", "kind": "text", "text": "补充条件"},
    ])
    group = store.save_group("电影镜头", state["items"])

    assert {path.name for path in (tmp_path / "prompt").glob("*.json")} == {"state.json", "groups.json"}
    library_text = (tmp_path / "prompt" / "library.md").read_text()
    assert "#### 主体" in library_text
    assert f"id: {block['id']}" in library_text
    assert "tags: 画面" in library_text
    assert not list(tmp_path.glob("prompt-*.json"))
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


def test_prompt_store_moves_legacy_files_into_prompt_directory(tmp_path):
    legacy_library = tmp_path / "prompt-library.json"
    legacy_library.write_text(json.dumps({"version": 1, "blocks": [{"id": "legacy", "title": "旧", "text": "旧文本", "tags": []}]}))

    store = PromptStore(tmp_path)

    assert legacy_library.exists()
    assert (tmp_path / "prompt" / "library.md").is_file()
    assert store.snapshot()["library"]["blocks"][0]["id"] == "legacy"


def test_prompt_store_parses_markdown_blocks_with_stable_metadata(tmp_path):
    library = tmp_path / "library.md"
    library.write_text(
        "# 基础积木\n\n## blocks\n\n### 镜头\n\n"
        "#### 摄影机运动\n"
        "id: camera-motion\n"
        "tags: 摄影机, 运镜\n"
        "> The camera pushes in slowly.\n"
        "> Keep the subject centered.\n",
        encoding="utf-8",
    )

    store = PromptStore(tmp_path / "data", library_path=library)

    assert store.snapshot()["library"]["blocks"] == [{
        "id": "camera-motion",
        "tags": ["摄影机", "运镜"],
        "title": "摄影机运动",
        "text": "The camera pushes in slowly.\nKeep the subject centered.",
    }]


def test_prompt_store_supports_a_configured_library_path(tmp_path):
    library = tmp_path / "sources" / "blocks.json"
    library.parent.mkdir()
    library.write_text(json.dumps({"version": 1, "blocks": []}), encoding="utf-8")

    store = PromptStore(tmp_path / "data", library_path=library)
    block = store.add_block({"title": "外部积木", "text": "从设置的 JSON 文件读取", "tags": ["测试"]})

    assert store.library_path == library.resolve()
    assert json.loads(library.read_text(encoding="utf-8"))["blocks"] == [block]
    assert not (tmp_path / "data" / "prompt" / "library.json").exists()


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


def test_reference_items_keep_resource_identity_media_snapshot_and_order(tmp_path):
    store = PromptStore(tmp_path)
    items = [
        {
            "instanceId": "reference-item-1",
            "kind": "reference",
            "referenceId": "character-hero",
            "referenceKind": "character",
            "snapshot": {
                "title": "主角 · 正脸",
                "text": "主角脸部参考",
                "tags": ["人物", "二次元"],
                "image_url": "/api/prompt/references/character-hero/image",
                "media_type": "image",
            },
        },
        {"instanceId": "text-item", "kind": "text", "text": "补充镜头"},
    ]

    state = store.save_state(items)
    store.save_group("人物顺序", state["items"])
    reloaded = PromptStore(tmp_path).snapshot()

    assert [item["kind"] for item in reloaded["state"]["items"]] == ["reference", "text"]
    reference = reloaded["state"]["items"][0]
    assert reference["reference_id"] == "character-hero"
    assert reference["reference_kind"] == "character"
    assert reference["snapshot"]["image_url"].endswith("/image")
    assert reloaded["groups"]["groups"][0]["items"][0]["reference_id"] == "character-hero"
