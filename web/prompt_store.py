from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from rh_cli.errors import RhCliError


VERSION = 1


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _tags(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.replace("，", ",").replace("、", ",").split(",")
    elif isinstance(value, list):
        values = value
    else:
        values = []
    result: list[str] = []
    for value in values:
        tag = str(value or "").strip()
        if tag and tag not in result:
            result.append(tag)
    return result


def _block(value: Any, fallback_id: str | None = None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    block_id = str(value.get("id") or fallback_id or _new_id("block")).strip()
    title = str(value.get("title") or "").strip()
    text = str(value.get("text") or "").strip()
    if not block_id or not title or not text:
        return None
    return {"id": block_id, "tags": _tags(value.get("tags")), "title": title, "text": text}


def _item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    instance_id = str(value.get("instance_id") or value.get("instanceId") or _new_id("item")).strip()
    kind = str(value.get("kind") or "").strip()
    if not instance_id or kind not in {"fixed", "action", "text"}:
        return None
    if kind == "text":
        return {"instance_id": instance_id, "kind": "text", "text": str(value.get("text") or "")}
    if kind == "action":
        reference_id = value.get("action_id") or value.get("actionId") or value.get("block_id") or value.get("blockId") or value.get("sourceId")
    else:
        reference_id = value.get("block_id") or value.get("blockId") or value.get("sourceId")
    reference_id = str(reference_id or "").strip()
    snapshot_value = value.get("snapshot")
    if not isinstance(snapshot_value, dict):
        snapshot_value = value
    snapshot = _block({
        "id": "snapshot",
        "title": snapshot_value.get("title"),
        "text": snapshot_value.get("text"),
        "tags": snapshot_value.get("tags"),
    })
    result: dict[str, Any] = {"instance_id": instance_id, "kind": kind}
    result["action_id" if kind == "action" else "block_id"] = reference_id
    if snapshot:
        result["snapshot"] = {key: snapshot[key] for key in ("tags", "title", "text")}
    return result


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        normalized = _item(item)
        if normalized:
            result.append(normalized)
    return result


def _snapshot(block: dict[str, Any]) -> dict[str, Any]:
    return {key: block[key] for key in ("tags", "title", "text")}


def _refresh_item_snapshots(values: Any, block_id: str, block: dict[str, Any]) -> list[dict[str, Any]]:
    items = _items(values)
    for item in items:
        if item["kind"] == "fixed" and item.get("block_id") == block_id:
            item["snapshot"] = _snapshot(block)
    return items


def _timestamp(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class PromptStore:
    """Atomic JSON persistence for the prompt block library and assembly states."""

    def __init__(self, data_root: str | Path) -> None:
        self.root = Path(data_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.library_path = self.root / "prompt-library.json"
        self.state_path = self.root / "prompt-state.json"
        self.groups_path = self.root / "prompt-groups.json"
        self._lock = threading.RLock()
        with self._lock:
            self._ensure(self.library_path, {"version": VERSION, "blocks": []})
            self._ensure(self.state_path, {"version": VERSION, "items": []})
            self._ensure(self.groups_path, {"version": VERSION, "groups": []})

    def _ensure(self, path: Path, default: dict[str, Any]) -> None:
        if not path.exists():
            self._write(path, default)

    def _read(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return copy.deepcopy(default)
        return value if isinstance(value, dict) else copy.deepcopy(default)

    def _write(self, path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _library(self) -> dict[str, Any]:
        raw = self._read(self.library_path, {"version": VERSION, "blocks": []})
        blocks: list[dict[str, Any]] = []
        ids: set[str] = set()
        for value in raw.get("blocks", []):
            block = _block(value)
            if block and block["id"] not in ids:
                blocks.append(block)
                ids.add(block["id"])
        return {"version": VERSION, "blocks": blocks}

    def _state(self) -> dict[str, Any]:
        raw = self._read(self.state_path, {"version": VERSION, "items": []})
        return {"version": VERSION, "items": _items(raw.get("items"))}

    def _groups(self) -> dict[str, Any]:
        raw = self._read(self.groups_path, {"version": VERSION, "groups": []})
        groups: list[dict[str, Any]] = []
        ids: set[str] = set()
        for value in raw.get("groups", []):
            if not isinstance(value, dict):
                continue
            group_id = str(value.get("id") or _new_id("group")).strip()
            name = str(value.get("name") or "").strip()
            if not group_id or not name or group_id in ids:
                continue
            groups.append({
                "id": group_id,
                "name": name,
                "updated_at": _timestamp(value.get("updated_at")),
                "items": _items(value.get("items")),
            })
            ids.add(group_id)
        return {"version": VERSION, "groups": groups}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"library": self._library(), "state": self._state(), "groups": self._groups()}

    def add_block(self, value: dict[str, Any]) -> dict[str, Any]:
        block = _block(value)
        if not block:
            raise RhCliError("INVALID_BLOCK", "积木必须包含标题和文本内容。")
        with self._lock:
            document = self._library()
            if any(item["id"] == block["id"] for item in document["blocks"]):
                raise RhCliError("BLOCK_EXISTS", "积木 ID 已存在。")
            document["blocks"].append(block)
            self._write(self.library_path, document)
        return block

    def update_block(self, block_id: str, value: dict[str, Any]) -> dict[str, Any]:
        payload = dict(value)
        payload["id"] = block_id
        block = _block(payload)
        if not block:
            raise RhCliError("INVALID_BLOCK", "积木必须包含标题和文本内容。")
        with self._lock:
            library = self._library()
            index = next((index for index, item in enumerate(library["blocks"]) if item["id"] == block_id), None)
            if index is None:
                raise RhCliError("BLOCK_NOT_FOUND", "找不到这块积木。")
            library["blocks"][index] = block
            state = self._state()
            state["items"] = _refresh_item_snapshots(state["items"], block_id, block)
            groups = self._groups()
            for group in groups["groups"]:
                group["items"] = _refresh_item_snapshots(group["items"], block_id, block)
            self._write(self.library_path, library)
            self._write(self.state_path, state)
            self._write(self.groups_path, groups)
        return block

    def delete_block(self, block_id: str) -> None:
        with self._lock:
            document = self._library()
            blocks = [item for item in document["blocks"] if item["id"] != block_id]
            if len(blocks) == len(document["blocks"]):
                raise RhCliError("BLOCK_NOT_FOUND", "找不到这块积木。")
            document["blocks"] = blocks
            self._write(self.library_path, document)

    def save_state(self, values: Any) -> dict[str, Any]:
        document = {"version": VERSION, "items": _items(values)}
        with self._lock:
            self._write(self.state_path, document)
        return document

    def save_group(self, name: str, values: Any, group_id: str | None = None) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise RhCliError("INVALID_GROUP", "组状态名称不能为空。")
        with self._lock:
            document = self._groups()
            items = _items(values)
            if group_id:
                group = next((item for item in document["groups"] if item["id"] == group_id), None)
                if group is None:
                    raise RhCliError("GROUP_NOT_FOUND", "找不到要覆盖的组状态。")
                group.update({"name": clean_name, "updated_at": time.time_ns() // 1_000_000, "items": items})
            else:
                group = {"id": _new_id("group"), "name": clean_name, "updated_at": time.time_ns() // 1_000_000, "items": items}
                document["groups"].append(group)
            self._write(self.groups_path, document)
            return copy.deepcopy(group)

    def delete_group(self, group_id: str) -> None:
        with self._lock:
            document = self._groups()
            groups = [item for item in document["groups"] if item["id"] != group_id]
            if len(groups) == len(document["groups"]):
                raise RhCliError("GROUP_NOT_FOUND", "找不到这个组状态。")
            document["groups"] = groups
            self._write(self.groups_path, document)

    def migrate(self, custom_blocks: Any, stage: Any) -> dict[str, Any]:
        with self._lock:
            library = self._library()
            existing = {item["id"] for item in library["blocks"]}
            for value in custom_blocks if isinstance(custom_blocks, list) else []:
                block = _block(value)
                if block and block["id"] not in existing:
                    library["blocks"].append(block)
                    existing.add(block["id"])
            self._write(self.library_path, library)
            state = self.save_state(stage)
            return {"library": library, "state": state, "groups": self._groups()}
