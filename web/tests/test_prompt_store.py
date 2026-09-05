from __future__ import annotations

import json

from web.prompt_store import PromptStore


def test_prompt_store_creates_json_library_and_json_state_documents(tmp_path):
    store = PromptStore(tmp_path)
    block = store.add_block({"title": "主体", "text": "清晰主体", "tags": ["画面", "画面", ""]})
    state = store.save_state([
        {"instanceId": "item-1", "kind": "fixed", "sourceId": block["id"], "title": block["title"], "text": block["text"], "tags": block["tags"]},
        {"instanceId": "item-2", "kind": "text", "text": "补充条件"},
    ])
    group = store.save_group("电影镜头", state["items"])

    assert {path.name for path in (tmp_path / "prompt").glob("*.json")} == {"library.json", "state.json", "groups.json"}
    library_document = json.loads((tmp_path / "prompt" / "library.json").read_text())
    group_index = json.loads((tmp_path / "prompt" / "groups.json").read_text())
    group_document = json.loads((tmp_path / "prompt" / "groups" / f"{group['id']}.json").read_text())
    assert library_document["blocks"] == [block]
    assert group_index["groups"] == [{
        "id": group["id"],
        "name": group["name"],
        "updated_at": group["updated_at"],
        "file": f"groups/{group['id']}.json",
    }]
    assert "items" not in group_index["groups"][0]
    assert group_document["items"] == group["items"]
    assert not list(tmp_path.glob("prompt-*.json"))
    reloaded = PromptStore(tmp_path).snapshot()
    assert reloaded["library"]["blocks"] == [block]
    assert reloaded["state"]["items"][0]["block_id"] == block["id"]
    assert reloaded["groups"]["groups"][0]["id"] == group["id"]
    assert len(reloaded["groups"]["groups"][0]["items"]) == 2


def test_prompt_store_migrates_legacy_combined_groups_file(tmp_path):
    prompt_root = tmp_path / "prompt"
    prompt_root.mkdir()
    legacy_group = {
        "id": "group-legacy",
        "name": "旧组状态",
        "updated_at": 123,
        "items": [{"instanceId": "item-1", "kind": "text", "text": "旧内容"}],
    }
    (prompt_root / "groups.json").write_text(
        json.dumps({"version": 1, "folders": [], "groups": [legacy_group]}, ensure_ascii=False),
        encoding="utf-8",
    )

    store = PromptStore(tmp_path)

    index = json.loads((prompt_root / "groups.json").read_text(encoding="utf-8"))
    group_file = json.loads((prompt_root / "groups" / "group-legacy.json").read_text(encoding="utf-8"))
    assert index["groups"] == [{
        "id": "group-legacy",
        "name": "旧组状态",
        "updated_at": 123,
        "file": "groups/group-legacy.json",
    }]
    assert group_file["items"] == [{"instance_id": "item-1", "kind": "text", "text": "旧内容"}]
    assert store.get_group("group-legacy")["items"] == group_file["items"]


def test_prompt_store_lists_and_gets_saved_groups(tmp_path):
    store = PromptStore(tmp_path)
    group = store.save_group("可复用镜头", [{"instanceId": "item-1", "kind": "text", "text": "慢慢推近"}])

    assert store.groups() == [group]
    assert store.get_group(group["id"]) == group
    assert store.get_group("") is None


def test_prompt_store_persists_translated_free_text(tmp_path):
    store = PromptStore(tmp_path)

    state = store.save_state([
        {
            "instanceId": "text-item",
            "kind": "text",
            "text": "一个电影感镜头",
            "translated_text": "A cinematic shot.",
        }
    ])

    assert state["items"] == [{
        "instance_id": "text-item",
        "kind": "text",
        "text": "一个电影感镜头",
        "translated_text": "A cinematic shot.",
    }]
    assert PromptStore(tmp_path).snapshot()["state"]["items"] == state["items"]


def test_prompt_store_exports_current_workbench_as_a_task_group_snapshot(tmp_path):
    store = PromptStore(tmp_path)
    state = store.save_state([{"instanceId": "item-1", "kind": "text", "text": "任务镜头"}])

    group = store.task_group_snapshot()

    assert group["name"] == "任务提交时组装台"
    assert group["items"] == state["items"]
    assert group["id"].startswith("task-group-")


def test_prompt_store_persists_media_stage_items_and_groups(tmp_path):
    store = PromptStore(tmp_path)
    media = {
        "instanceId": "media-item",
        "kind": "media",
        "mediaPath": "/tmp/reference.png",
        "mediaName": "reference.png",
        "mediaKind": "image",
        "mediaMime": "image/png",
    }

    state = store.save_state([media])
    group = store.save_group("媒体参考", state["items"])
    reloaded = PromptStore(tmp_path).snapshot()

    expected = {
        "instance_id": "media-item",
        "kind": "media",
        "media_path": "/tmp/reference.png",
        "media_name": "reference.png",
        "media_kind": "image",
        "media_mime": "image/png",
    }
    assert state["items"] == [expected]
    assert reloaded["state"]["items"] == [expected]
    assert group["items"] == [expected]


def test_prompt_store_persists_empty_media_stage_placeholder(tmp_path):
    store = PromptStore(tmp_path)

    state = store.save_state([{
        "instanceId": "media-placeholder",
        "kind": "media",
    }])

    assert state["items"] == [{
        "instance_id": "media-placeholder",
        "kind": "media",
        "media_path": "",
        "media_name": "媒体积木",
        "media_kind": "",
        "media_mime": "",
    }]


def test_prompt_store_persists_generated_subject_definitions_marker(tmp_path):
    store = PromptStore(tmp_path)

    state = store.save_state([{
        "instanceId": "subject-item",
        "kind": "text",
        "text": "subject_definitions:\n<Subject 1>: A person.",
        "translatedText": "subject_definitions:\n<Subject 1>: A person.",
        "translationDisabled": True,
        "generatedType": "subject_definitions",
        "segments": [{"type": "text", "text": "subject_definitions:\n<Subject 1>: A person."}],
    }])

    assert state["items"][0]["generated_type"] == "subject_definitions"
    assert state["items"][0]["translation_disabled"] is True
    assert PromptStore(tmp_path).snapshot()["state"]["items"][0]["generated_type"] == "subject_definitions"


def test_prompt_store_persists_structured_free_text_references(tmp_path):
    store = PromptStore(tmp_path)
    state = store.save_state([
        {
            "instanceId": "text-with-reference",
            "kind": "text",
            "text": "镜头跟随 @站立动作",
            "translatedText": "The camera follows __RH_REF_1__.",
            "segments": [
                {"type": "text", "text": "镜头跟随 "},
                {
                    "type": "reference",
                    "sourceType": "action",
                    "sourceId": "action-1",
                    "label": "站立动作",
                    "snapshot": {"title": "站立动作", "text": "A standing pose", "tags": ["pose"], "ignored": "drop me"},
                },
            ],
        }
    ])

    assert state["items"][0]["segments"] == [
        {"type": "text", "text": "镜头跟随 "},
        {
            "type": "reference",
            "source_type": "action",
            "source_id": "action-1",
            "label": "站立动作",
            "snapshot": {"title": "站立动作", "text": "A standing pose", "tags": ["pose"]},
        },
    ]
    assert state["items"][0]["translated_text"] == "The camera follows __RH_REF_1__."
    assert PromptStore(tmp_path).snapshot()["state"]["items"] == state["items"]


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
    assert updated == {"id": block["id"], "category": "未分类", **expected_snapshot}
    assert snapshot["state"]["items"][0]["snapshot"] == expected_snapshot
    assert snapshot["groups"]["groups"][0]["items"][0]["snapshot"] == expected_snapshot
    library_blocks = json.loads((tmp_path / "prompt" / "library.json").read_text())["blocks"]
    assert library_blocks == [updated]


def test_save_group_with_id_overwrites_items_without_creating_a_second_group(tmp_path):
    store = PromptStore(tmp_path)
    first = store.save_group("首帧恢复", [{"instanceId": "old", "kind": "text", "text": "旧顺序"}])

    updated = store.save_group("首帧恢复", [{"instanceId": "new", "kind": "text", "text": "新顺序"}], first["id"])
    snapshot = store.snapshot()

    assert updated["id"] == first["id"]
    assert updated["updated_at"] >= first["updated_at"]
    assert len(snapshot["groups"]["groups"]) == 1
    assert snapshot["groups"]["groups"][0]["items"][0]["text"] == "新顺序"


def test_prompt_group_folders_persist_membership_and_unclassify_on_delete(tmp_path):
    store = PromptStore(tmp_path)
    folder = store.create_prompt_group_folder("剧场动画")
    group = store.save_group(
        "绝区零开场",
        [{"instanceId": "item-1", "kind": "text", "text": "镜头缓慢推进"}],
        folder_id=folder["id"],
    )

    assert store.prompt_group_folders()[0]["group_count"] == 1
    assert store.get_group(group["id"])["folder_id"] == folder["id"]
    assert PromptStore(tmp_path).prompt_group_folders()[0]["name"] == "剧场动画"

    renamed = store.rename_prompt_group_folder(folder["id"], "绝区零剧场")
    assert renamed["name"] == "绝区零剧场"
    store.delete_prompt_group_folder(folder["id"])

    assert store.prompt_group_folders() == []
    assert "folder_id" not in store.get_group(group["id"])


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
    assert (tmp_path / "prompt" / "library.json").is_file()
    assert store.snapshot()["library"]["blocks"][0]["id"] == "legacy"


def test_prompt_store_reads_json_blocks_with_stable_metadata(tmp_path):
    library = tmp_path / "library.json"
    library.write_text(json.dumps({"version": 1, "blocks": [{
        "id": "camera-motion",
        "category": "镜头",
        "tags": ["摄影机", "运镜"],
        "title": "摄影机运动",
        "text": "The camera pushes in slowly.\nKeep the subject centered.",
    }]}, ensure_ascii=False), encoding="utf-8")

    store = PromptStore(tmp_path / "data", library_path=library)

    assert store.snapshot()["library"]["blocks"] == [{
        "id": "camera-motion",
        "category": "镜头",
        "tags": ["摄影机", "运镜"],
        "title": "摄影机运动",
        "text": "The camera pushes in slowly.\nKeep the subject centered.",
    }]


def test_prompt_store_preserves_json_categories_when_writing_library(tmp_path):
    library = tmp_path / "library.json"
    library.write_text(json.dumps({"version": 1, "blocks": [
        {"id": "fixed-camera", "category": "镜头", "tags": ["镜头"], "title": "固定镜头", "text": "The camera stays still."},
        {"id": "photoreal", "category": "风格", "tags": ["风格"], "title": "写实", "text": "Photorealistic."},
    ]}, ensure_ascii=False), encoding="utf-8")

    store = PromptStore(tmp_path / "data", library_path=library)
    store.delete_block("fixed-camera")
    rewritten = json.loads(library.read_text(encoding="utf-8"))

    assert [block["category"] for block in rewritten["blocks"]] == ["风格"]
    assert rewritten["blocks"][0]["title"] == "写实"


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
