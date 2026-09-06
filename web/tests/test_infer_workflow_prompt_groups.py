from __future__ import annotations

import json

from web.infer_workflow_prompt_groups import infer_prompt_items
from web.infer_workflow_prompt_groups import apply_plan, build_plan
from web.prompt_store import PromptStore


def test_inference_prefers_complete_block_and_keeps_unmatched_text() -> None:
    blocks = [
        {
            "id": "heading",
            "category": "结构",
            "tags": [],
            "title": "细节描述",
            "text": "detailed_description:",
        },
        {
            "id": "alignment",
            "category": "结构",
            "tags": ["首尾帧"],
            "title": "首帧对齐",
            "text": "detailed_description:\n[Shot 1] begins from <Picture 1>.",
        },
    ]
    items = infer_prompt_items(
        "wf_test",
        "subject_definitions:\ncustom text\n\ndetailed_description:\n[Shot 1] begins from <Picture 1>.\n\nending",
        blocks,
    )

    assert [item["kind"] for item in items] == ["text", "fixed", "text"]
    assert items[0]["text"] == "subject_definitions:\ncustom text"
    assert items[1]["block_id"] == "alignment"
    assert items[2]["text"] == "ending"
    assert items[0]["translation_disabled"] is True


def test_inference_keeps_empty_prompt_empty() -> None:
    assert infer_prompt_items("wf_empty", "", []) == []


def _write_json(path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_apply_plan_writes_new_groups_in_prompt_store_split_format(tmp_path) -> None:
    data_root = tmp_path / "data"
    library_path = tmp_path / "library.json"
    _write_json(
        data_root / "workflow-registry.json",
        {"workflows": [{"id": "wf_new", "name": "新工作流.json"}]},
    )
    _write_json(
        data_root / "workflows" / "wf_new_新工作流.json",
        {"1": {"inputs": {"prompt": "summary:\ncustom prompt"}, "class_type": "TextEncode"}},
    )
    _write_json(
        data_root / "prompt" / "groups.json",
        {"version": 1, "folders": [{"id": "folder-1", "name": "保留"}], "groups": []},
    )
    _write_json(
        library_path,
        {"version": 1, "blocks": [{"id": "summary", "category": "结构", "title": "综述", "text": "summary:"}]},
    )

    plan = build_plan(data_root, library_path)
    apply_plan(plan)

    group_id = "group-wf_new-inferred"
    index = json.loads((data_root / "prompt" / "groups.json").read_text(encoding="utf-8"))
    group_file = json.loads((data_root / "prompt" / "groups" / f"{group_id}.json").read_text(encoding="utf-8"))
    assert index["folders"] == [{"id": "folder-1", "name": "保留"}]
    assert "items" not in index["groups"][0]
    assert len(group_file["items"]) == 2
    loaded = PromptStore(data_root, library_path=library_path).get_group(group_id)
    assert len(loaded["items"]) == len(group_file["items"])
    assert [item["kind"] for item in loaded["items"]] == ["fixed", "text"]
    workflow_index = json.loads((data_root / "workflow-registry.json").read_text(encoding="utf-8"))
    assert workflow_index["version"] == 2
    assert workflow_index["workflows"] == [{"id": "wf_new", "file": "workflow-registry/wf_new.json"}]
    workflow_entry = json.loads((data_root / "workflow-registry" / "wf_new.json").read_text(encoding="utf-8"))
    assert workflow_entry["prompt_group_id"] == group_id
    assert workflow_entry["workflow_file"] == "workflows/wf_new.json"
    assert workflow_entry["prompt_group_file"] == "workflows/wf_new.prompt_group.json"


def test_apply_plan_repairs_empty_group_file_from_workflow_sidecar(tmp_path) -> None:
    data_root = tmp_path / "data"
    library_path = tmp_path / "library.json"
    group_id = "group-wf_existing-inferred"
    _write_json(
        data_root / "workflow-registry.json",
        {"workflows": [{"id": "wf_existing", "name": "已有工作流.json", "prompt_group_id": group_id}]},
    )
    _write_json(
        data_root / "workflows" / "wf_existing_已有工作流.json",
        {"1": {"inputs": {"prompt": "原始提示词"}, "class_type": "TextEncode"}},
    )
    _write_json(
        data_root / "workflows" / "wf_existing.prompt_group.json",
        {"id": group_id, "name": "已有工作流 · 反推提示词组", "updated_at": 10, "items": [{"kind": "text", "text": "原始提示词"}]},
    )
    _write_json(
        data_root / "prompt" / "groups.json",
        {"version": 1, "folders": [], "groups": [{"id": group_id, "name": "已有工作流 · 反推提示词组", "updated_at": 10, "file": f"groups/{group_id}.json"}]},
    )
    _write_json(
        data_root / "prompt" / "groups" / f"{group_id}.json",
        {"version": 1, "id": group_id, "name": "已有工作流 · 反推提示词组", "updated_at": 10, "items": []},
    )
    _write_json(library_path, {"version": 1, "blocks": []})

    plan = build_plan(data_root, library_path)
    assert len(plan["repair_groups"]) == 1
    apply_plan(plan)

    repaired = PromptStore(data_root, library_path=library_path).get_group(group_id)
    assert len(repaired["items"]) == 1
    assert repaired["items"][0]["kind"] == "text"
    assert repaired["items"][0]["text"] == "原始提示词"
    assert repaired["items"][0]["instance_id"]
