"""Infer prompt groups for the workflows registered in the local library.

The migration is deliberately conservative: a library block is inserted only
when its complete text occurs in a workflow prompt (ignoring whitespace). Any
text that is not matched is retained as an original, direct-output text item.
This keeps the generated group useful in the prompt workbench without
rewriting the workflow's prompt semantics.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEFAULT_DATA_ROOT = Path(__file__).resolve().parent / "data"
DEFAULT_LIBRARY_PATH = Path.home() / "Documents" / "VideoMake" / "ref" / "prompt" / "library.json"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def _read_workflow_registry(data_root: Path) -> list[dict[str, Any]]:
    """Read both the split registry and the previous inline format."""
    registry_path = data_root / "workflow-registry.json"
    document = _read_json(registry_path, {"workflows": []})
    raw_records = document.get("workflows", [])
    if not isinstance(raw_records, list):
        return []

    entries_root = (data_root / "workflow-registry").resolve()
    data_root_resolved = data_root.resolve()
    records: list[dict[str, Any]] = []
    for item in raw_records:
        if not isinstance(item, dict):
            continue
        workflow_id = str(item.get("id") or "").strip()
        if not workflow_id:
            continue
        reference = str(item.get("file") or item.get("path") or "").strip()
        if reference:
            reference_path = Path(reference).expanduser()
            candidates = (
                [reference_path.resolve()]
                if reference_path.is_absolute()
                else [
                    (registry_path.parent / reference_path).resolve(),
                    (entries_root / reference_path).resolve(),
                ]
            )
            entry_path = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.is_file()
                    and (candidate.is_relative_to(data_root_resolved) or candidate.is_relative_to(entries_root))
                ),
                None,
            )
            if entry_path is not None:
                entry = _read_json(entry_path, {})
                if entry:
                    entry["id"] = workflow_id
                    entry.update(
                        {
                            key: value
                            for key, value in item.items()
                            if key not in {"file", "path"} and key not in entry
                        }
                    )
                    records.append(entry)
                    continue
        # Legacy inline records remain supported for old checkouts and test
        # fixtures while the next application write migrates them.
        records.append(dict(item))
    return records


def _normalised_with_map(value: str) -> tuple[str, list[tuple[int, int]]]:
    """Collapse whitespace and retain the source span of every output char."""
    result: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        if value[index].isspace():
            start = index
            while index < len(value) and value[index].isspace():
                index += 1
            if result and index < len(value):
                result.append(" ")
                spans.append((start, index))
            continue
        result.append(value[index])
        spans.append((index, index + 1))
        index += 1
    return "".join(result), spans


def _matched_block_spans(prompt: str, blocks: list[dict[str, Any]]) -> list[tuple[int, int, dict[str, Any]]]:
    """Find non-overlapping complete block occurrences in prompt order."""
    normalised, spans = _normalised_with_map(prompt)
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for block in blocks:
        needle, _ = _normalised_with_map(str(block.get("text") or ""))
        if not needle:
            continue
        start = 0
        while True:
            start = normalised.find(needle, start)
            if start < 0:
                break
            candidates.append((start, start + len(needle), block))
            start += max(1, len(needle))

    # Prefer the most specific (longest) block when a structural heading and a
    # longer block such as "首帧对齐" cover the same characters.
    candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0], str(item[2].get("id") or "")))
    selected: list[tuple[int, int, dict[str, Any]]] = []
    for start, end, block in candidates:
        if any(start < selected_end and end > selected_start for selected_start, selected_end, _ in selected):
            continue
        selected.append((start, end, block))
    selected.sort(key=lambda item: item[0])

    mapped: list[tuple[int, int, dict[str, Any]]] = []
    for start, end, block in selected:
        if not spans or start >= len(spans) or end <= start:
            continue
        mapped.append((spans[start][0], spans[end - 1][1], block))
    return mapped


def infer_prompt_items(workflow_id: str, prompt: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build workbench items while preserving all unmatched prompt text."""
    matches = _matched_block_spans(prompt, blocks)
    items: list[dict[str, Any]] = []
    cursor = 0
    item_index = 0
    for start, end, block in matches:
        text = prompt[cursor:start].strip()
        if text:
            items.append(
                {
                    "instance_id": f"text-inferred-{workflow_id}-{item_index}",
                    "kind": "text",
                    "text": text,
                    "translated_text": text,
                    "translation_disabled": True,
                    "generated_type": "workflow-inferred",
                }
            )
            item_index += 1
        items.append(
            {
                "instance_id": f"fixed-inferred-{workflow_id}-{item_index}",
                "kind": "fixed",
                "block_id": str(block.get("id") or ""),
                "snapshot": {
                    "category": str(block.get("category") or "未分类"),
                    "tags": list(block.get("tags") or []),
                    "title": str(block.get("title") or ""),
                    "text": str(block.get("text") or ""),
                },
            }
        )
        item_index += 1
        cursor = end

    text = prompt[cursor:].strip()
    if text:
        items.append(
            {
                "instance_id": f"text-inferred-{workflow_id}-{item_index}",
                "kind": "text",
                "text": text,
                "translated_text": text,
                "translation_disabled": True,
                "generated_type": "workflow-inferred",
            }
        )
    return items


def _workflow_prompts(workflow: dict[str, Any]) -> list[str]:
    prompts: list[str] = []
    for node in workflow.values():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        for field, value in node["inputs"].items():
            if str(field).lower() in {"text", "prompt", "positive", "negative", "caption", "instruction"}:
                if isinstance(value, str) and value.strip():
                    prompts.append(value)
    return prompts


def _atomic_write(path: Path, value: dict[str, Any], mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.inferred.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if mode is not None:
            os.chmod(temporary, mode)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _mode_for(path: Path, fallback: int = 0o644) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return fallback


def _write_workflow_registry(data_root: Path, records: list[dict[str, Any]]) -> None:
    """Persist the package manifests and their compact global index."""
    registry_path = data_root / "workflow-registry.json"
    package_root = data_root / "workflow"
    package_layout = package_root.is_dir()
    legacy_entries_root = data_root / "workflow-registry"
    index_records: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        workflow_id = str(record.get("id") or "").strip()
        if not workflow_id or Path(workflow_id).name != workflow_id:
            continue
        record = dict(record)
        if package_layout:
            record["workflow_file"] = f"workflow/{workflow_id}/workflow_api.json"
            record["prompt_group_file"] = f"workflow/{workflow_id}/prompt_group.json"
            record["manifest_file"] = f"workflow/{workflow_id}/manifest.json"
            record["package_dir"] = f"workflow/{workflow_id}"
            entry_path = package_root / workflow_id / "manifest.json"
        else:
            record["workflow_file"] = f"workflows/{workflow_id}.json"
            if str(record.get("prompt_group_id") or "").strip():
                record["prompt_group_file"] = f"workflows/{workflow_id}.prompt_group.json"
            else:
                record.pop("prompt_group_file", None)
            entry_path = legacy_entries_root / f"{workflow_id}.json"
        _atomic_write(entry_path, record, _mode_for(entry_path, 0o600))
        index_records.append(
            {
                "id": workflow_id,
                "file": str(entry_path.relative_to(data_root)),
            }
        )
    _atomic_write(
        registry_path,
        {"version": 2, "workflows": index_records},
        _mode_for(registry_path, 0o600),
    )


def _group_name(workflow_name: str) -> str:
    stem = Path(workflow_name).stem.strip() or "未命名工作流"
    return f"{stem} · 反推提示词组"


def _group_file_path(groups_path: Path, group_id: str) -> Path:
    """Return the split-content path used by PromptStore."""
    clean_id = str(group_id or "").strip()
    return groups_path.parent / "groups" / f"{quote(clean_id, safe='-_.~')}.json"


def _group_metadata(groups_path: Path, group: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a group document to the current groups.json index shape."""
    group_id = str(group.get("id") or "").strip()
    name = str(group.get("name") or "").strip()
    if not group_id or not name:
        return None
    metadata = {
        "id": group_id,
        "name": name,
        "updated_at": int(group.get("updated_at") or 0),
        "file": f"groups/{quote(group_id, safe='-_.~')}.json",
    }
    folder_id = str(group.get("folder_id") or "").strip()
    if folder_id:
        metadata["folder_id"] = folder_id
    return metadata


def _group_document(group: dict[str, Any]) -> dict[str, Any]:
    """Build the standalone group file consumed by PromptStore."""
    document = {
        "version": 1,
        "id": str(group.get("id") or "").strip(),
        "name": str(group.get("name") or "").strip(),
        "updated_at": int(group.get("updated_at") or 0),
        "items": group.get("items") if isinstance(group.get("items"), list) else [],
    }
    folder_id = str(group.get("folder_id") or "").strip()
    if folder_id:
        document["folder_id"] = folder_id
    return document


def _sidecar_group(data_root: Path, workflow_id: str, fallback: dict[str, Any]) -> dict[str, Any] | None:
    """Read a workflow-associated group snapshot when it contains items."""
    package_sidecar = data_root / "workflow" / workflow_id / "prompt_group.json"
    legacy_sidecar = data_root / "workflows" / f"{workflow_id}.prompt_group.json"
    sidecar = package_sidecar if package_sidecar.is_file() else legacy_sidecar
    value = _read_json(sidecar, {})
    group_id = str(value.get("id") or fallback.get("id") or "").strip()
    name = str(value.get("name") or fallback.get("name") or "").strip()
    items = value.get("items")
    if not group_id or not name or not isinstance(items, list) or not items:
        return None
    return {
        "id": group_id,
        "name": name,
        "updated_at": int(value.get("updated_at") or fallback.get("updated_at") or 0),
        "items": items,
        **({"folder_id": fallback["folder_id"]} if str(fallback.get("folder_id") or "").strip() else {}),
    }


def build_plan(data_root: Path, library_path: Path) -> dict[str, Any]:
    registry_path = data_root / "workflow-registry.json"
    groups_path = data_root / "prompt" / "groups.json"
    groups_document = _read_json(groups_path, {"version": 1, "groups": []})
    records = _read_workflow_registry(data_root)
    library_document = _read_json(library_path, {"blocks": []})
    blocks = [item for item in library_document.get("blocks", []) if isinstance(item, dict)]
    groups = [item for item in groups_document.get("groups", []) if isinstance(item, dict)]
    folders = groups_document.get("folders") if isinstance(groups_document.get("folders"), list) else []
    existing_group_ids = {str(item.get("id") or "").strip() for item in groups}
    existing_ids = {
        str(item.get("id") or "").strip()
        for item in records
        if str(item.get("prompt_group_id") or "").strip()
    }

    plan_records: list[dict[str, Any]] = []
    plan_groups: list[dict[str, Any]] = []
    repair_groups: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    now = int(time.time() * 1000)
    for record in records:
        workflow_id = str(record.get("id") or "").strip()
        if not workflow_id:
            continue
        if workflow_id in existing_ids:
            group_id = str(record.get("prompt_group_id") or "").strip()
            existing_group = next((item for item in groups if str(item.get("id") or "").strip() == group_id), {})
            target = _group_file_path(groups_path, group_id) if group_id else None
            target_document = _read_json(target, {}) if target else {}
            recovered = _sidecar_group(data_root, workflow_id, {**existing_group, "id": group_id})
            if recovered and (
                not isinstance(target_document.get("items"), list)
                or not target_document.get("items")
            ):
                repair_groups.append(recovered)
            skipped.append({"id": workflow_id, "reason": "已有提示词组关联"})
            continue
        stable_workflow_file = data_root / "workflow" / workflow_id / "workflow_api.json"
        if stable_workflow_file.is_file():
            workflow_files = [stable_workflow_file]
        else:
            # Read the previous flat library during migration, but never
            # write the old name-based convention back to the registry.
            legacy_root = data_root / "workflows"
            legacy_stable = legacy_root / f"{workflow_id}.json"
            workflow_files = [legacy_stable] if legacy_stable.is_file() else list(legacy_root.glob(f"{workflow_id}_*.json"))
        if len(workflow_files) != 1:
            skipped.append({"id": workflow_id, "reason": f"工作流文件数量异常：{len(workflow_files)}"})
            continue
        workflow = _read_json(workflow_files[0], {})
        prompt = "\n\n".join(_workflow_prompts(workflow))
        group_id = f"group-{workflow_id}-inferred"
        if group_id in existing_group_ids:
            skipped.append({"id": workflow_id, "reason": f"提示词组 ID 已存在：{group_id}"})
            continue
        group = {
            "id": group_id,
            "name": _group_name(str(record.get("name") or workflow_files[0].name)),
            "updated_at": now,
            "items": infer_prompt_items(workflow_id, prompt, blocks),
        }
        updated_record = dict(record)
        updated_record["prompt_group_id"] = group_id
        updated_record["prompt_group_name"] = group["name"]
        plan_records.append(updated_record)
        plan_groups.append(group)

    return {
        "registry_path": registry_path,
        "groups_path": groups_path,
        "records": records,
        "plan_records": plan_records,
        "plan_groups": plan_groups,
        "repair_groups": repair_groups,
        "existing_groups": groups,
        "folders": folders,
        "skipped": skipped,
        "block_count": len(blocks),
        "package_layout": (data_root / "workflow").is_dir(),
    }


def apply_plan(plan: dict[str, Any]) -> None:
    # Write sidecars first. If a later registry write fails, the next run can
    # safely rebuild the same sidecars because the registry still has no link.
    for record, group in zip(plan["plan_records"], plan["plan_groups"]):
        package_root = plan["groups_path"].parent.parent / "workflow"
        sidecar = (
            package_root / str(record["id"]) / "prompt_group.json"
            if plan.get("package_layout")
            else plan["groups_path"].parent.parent / "workflows" / f"{record['id']}.prompt_group.json"
        )
        _atomic_write(sidecar, group, 0o600)

    # PromptStore uses a split format: groups.json is an index and each
    # group's items live in prompt/groups/<group-id>.json. Keep the inferred
    # sidecar and the prompt-group store in sync, including old empty files.
    groups_to_write = {group["id"]: group for group in plan["plan_groups"] + plan["repair_groups"]}
    for group in groups_to_write.values():
        group_path = _group_file_path(plan["groups_path"], str(group["id"]))
        _atomic_write(group_path, _group_document(group), _mode_for(group_path, 0o600))

    records_by_id = {
        str(item.get("id") or ""): item
        for item in plan["records"]
        if str(item.get("id") or "")
    }
    for updated in plan["plan_records"]:
        records_by_id[str(updated["id"])] = updated
    _write_workflow_registry(plan["registry_path"].parent, list(records_by_id.values()))

    groups = {
        str(item.get("id") or ""): _group_metadata(plan["groups_path"], item)
        for item in plan["existing_groups"]
        if str(item.get("id") or "")
    }
    groups = {group_id: metadata for group_id, metadata in groups.items() if metadata}
    for group in groups_to_write.values():
        metadata = _group_metadata(plan["groups_path"], group)
        if metadata:
            previous = groups.get(str(group["id"])) or {}
            if previous.get("folder_id") and not metadata.get("folder_id"):
                metadata["folder_id"] = previous["folder_id"]
            groups[str(group["id"])] = metadata
    groups_document = {"version": 1, "folders": plan.get("folders", []), "groups": list(groups.values())}
    _atomic_write(plan["groups_path"], groups_document, _mode_for(plan["groups_path"], 0o644))


def summary(plan: dict[str, Any]) -> dict[str, Any]:
    groups = plan["plan_groups"]
    fixed = sum(sum(1 for item in group["items"] if item.get("kind") == "fixed") for group in groups)
    text = sum(sum(1 for item in group["items"] if item.get("kind") == "text") for group in groups)
    empty = sum(1 for group in groups if not group["items"])
    return {
        "workflows_to_associate": len(groups),
        "groups_to_generate": len(groups),
        "groups_repaired": len(plan.get("repair_groups", [])),
        "matched_library_blocks": fixed,
        "original_text_items": text,
        "empty_prompt_groups": empty,
        "skipped": plan["skipped"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="为本地工作流库反推并关联提示词组")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY_PATH)
    parser.add_argument("--apply", action="store_true", help="写入 groups.json、工作流提示词组 sidecar 和 registry")
    args = parser.parse_args()
    data_root = args.data_root.expanduser().resolve()
    library_path = args.library.expanduser().resolve()
    if not library_path.is_file():
        parser.error(f"积木库不存在：{library_path}")
    plan = build_plan(data_root, library_path)
    result = summary(plan)
    if args.apply:
        apply_plan(plan)
        result["applied"] = True
    else:
        result["applied"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
