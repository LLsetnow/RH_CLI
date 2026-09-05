from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from rh_cli.errors import RhCliError

from .library_rating import normalize_library_rating, replace_rating_tag


VERSION = 1


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _json_source_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.suffix.lower() in {".md", ".markdown"}:
        path = path.with_suffix(".json")
    return path.resolve()


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


def _media_kind(value: Any, path: str = "") -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("image/") or raw == "image":
        return "image"
    if raw.startswith("audio/") or raw == "audio":
        return "audio"
    if raw.startswith("video/") or raw == "video":
        return "video"
    suffix = Path(path).suffix.lower()
    if suffix in {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}:
        return "image"
    if suffix in {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}:
        return "audio"
    if suffix in {".avi", ".flv", ".mkv", ".mov", ".mp4", ".m4v", ".webm", ".wmv"}:
        return "video"
    return ""


def _block(value: Any, fallback_id: str | None = None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    block_id = str(value.get("id") or fallback_id or _new_id("block")).strip()
    category = str(value.get("category") or "未分类").strip() or "未分类"
    title = str(value.get("title") or "").strip()
    text = str(value.get("text") or "").strip()
    if not block_id or not title or not text:
        return None
    return {"id": block_id, "category": category, "tags": _tags(value.get("tags")), "title": title, "text": text}


def _segments(value: Any) -> list[dict[str, Any]]:
    """Keep the structured prompt-editor segments while discarding unknown fields."""
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    allowed_snapshot_keys = {
        "title",
        "text",
        "tags",
        "category",
        "color_image_url",
        "depth_image_url",
        "pair_status",
        "color_image_path",
        "depth_image_path",
        "image_url",
        "image_path",
        "audio_url",
        "audio_path",
        "media_type",
        "reference_kind",
    }
    for segment in value:
        if not isinstance(segment, dict):
            continue
        segment_type = str(segment.get("type") or "").strip()
        if segment_type == "text":
            result.append({"type": "text", "text": str(segment.get("text") or "")})
            continue
        if segment_type != "reference":
            continue
        source_type = str(segment.get("source_type") or segment.get("sourceType") or "").strip()
        source_id = str(segment.get("source_id") or segment.get("sourceId") or "").strip()
        if source_type not in {"block", "action", "reference"} or not source_id:
            continue
        normalized: dict[str, Any] = {
            "type": "reference",
            "source_type": source_type,
            "source_id": source_id,
            "label": str(segment.get("label") or "").strip(),
        }
        snapshot = segment.get("snapshot")
        if isinstance(snapshot, dict):
            safe_snapshot: dict[str, Any] = {}
            for key in allowed_snapshot_keys:
                if key not in snapshot:
                    continue
                safe_snapshot[key] = _tags(snapshot[key]) if key == "tags" else str(snapshot[key] or "").strip()
            if safe_snapshot:
                normalized["snapshot"] = safe_snapshot
        result.append(normalized)
    return result


def _item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    instance_id = str(value.get("instance_id") or value.get("instanceId") or _new_id("item")).strip()
    kind = str(value.get("kind") or "").strip()
    if not instance_id or kind not in {"fixed", "action", "reference", "media", "text"}:
        return None
    if kind == "text":
        result = {"instance_id": instance_id, "kind": "text", "text": str(value.get("text") or "")}
        if "translated_text" in value or "translatedText" in value:
            result["translated_text"] = str(value.get("translated_text") or value.get("translatedText") or "")
        if value.get("translation_disabled") or value.get("translationDisabled"):
            result["translation_disabled"] = True
        generated_type = str(value.get("generated_type") or value.get("generatedType") or "").strip()
        if generated_type:
            result["generated_type"] = generated_type[:64]
        if isinstance(value.get("segments"), list):
            result["segments"] = _segments(value.get("segments"))
        return result
    if kind == "media":
        media_path = str(value.get("media_path") or value.get("mediaPath") or value.get("path") or "").strip()
        media_name = str(value.get("media_name") or value.get("mediaName") or Path(media_path).name or "媒体积木").strip()
        media_mime = str(value.get("media_mime") or value.get("mediaMime") or value.get("mime") or "").strip().lower()
        media_kind = _media_kind(value.get("media_kind") or value.get("mediaKind") or value.get("media_type") or value.get("mediaType"), media_path)
        if not media_path:
            return {
                "instance_id": instance_id,
                "kind": "media",
                "media_path": "",
                "media_name": media_name[:240] or "媒体积木",
                "media_kind": media_kind,
                "media_mime": media_mime[:120],
            }
        if not media_kind:
            return None
        return {
            "instance_id": instance_id,
            "kind": "media",
            "media_path": media_path,
            "media_name": media_name[:240] or "媒体文件",
            "media_kind": media_kind,
            "media_mime": media_mime[:120],
        }
    if kind == "action":
        reference_id = value.get("action_id") or value.get("actionId") or value.get("block_id") or value.get("blockId") or value.get("sourceId")
    elif kind == "reference":
        reference_id = value.get("reference_id") or value.get("referenceId") or value.get("source_id") or value.get("sourceId")
    else:
        reference_id = value.get("block_id") or value.get("blockId") or value.get("sourceId")
    reference_id = str(reference_id or "").strip()
    snapshot_value = value.get("snapshot")
    if not isinstance(snapshot_value, dict):
        snapshot_value = value
    if kind == "reference":
        title = str(snapshot_value.get("title") or value.get("title") or "").strip()
        text = str(snapshot_value.get("text") or value.get("text") or "").strip()
        if not reference_id or not title:
            return None
        result = {
            "instance_id": instance_id,
            "kind": "reference",
            "reference_id": reference_id,
            "reference_kind": str(value.get("reference_kind") or value.get("referenceKind") or "").strip(),
            "snapshot": {
                "tags": _tags(snapshot_value.get("tags")),
                "title": title,
                "text": text,
            },
        }
        for key in ("image_url", "image_path", "audio_url", "audio_path", "media_type"):
            if key in snapshot_value:
                result["snapshot"][key] = str(snapshot_value.get(key) or "").strip()
        return result
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
    """JSON-backed prompt block library with JSON state/group persistence."""

    def __init__(self, data_root: str | Path, library_path: str | Path | None = None) -> None:
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.prompt_root = self.root / "prompt"
        self.prompt_root.mkdir(parents=True, exist_ok=True)
        self.default_library_path = (self.prompt_root / "library.json").resolve()
        self.library_path = (
            _json_source_path(library_path)
            if library_path is not None and str(library_path).strip()
            else self.default_library_path
        )
        self.state_path = self.prompt_root / "state.json"
        self.groups_path = self.prompt_root / "groups.json"
        self.group_files_path = self.prompt_root / "groups"
        self.group_files_path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._lock:
            self._migrate_legacy_files()
            self._ensure(self.library_path, {"version": VERSION, "blocks": []})
            self._ensure(self.state_path, {"version": VERSION, "items": []})
            self._migrate_group_files()
            self._ensure(self.groups_path, {"version": VERSION, "folders": [], "groups": []})

    def _migrate_legacy_files(self) -> None:
        """Move legacy flat state files into the structured prompt directory."""
        migrated_files = {
            self.root / "prompt-state.json": self.state_path,
            self.root / "prompt-groups.json": self.groups_path,
        }
        for legacy_path, target_path in migrated_files.items():
            if target_path.exists() or not legacy_path.is_file():
                continue
            legacy_path.replace(target_path)
        if self.library_path == self.default_library_path and not self.library_path.exists():
            legacy_library = self.root / "prompt-library.json"
            if legacy_library.is_file():
                document = self._read(legacy_library, {"version": VERSION, "blocks": []})
                self._write_library({"version": VERSION, "blocks": document.get("blocks", [])})

    def set_library_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        if path.suffix.lower() != ".json":
            raise RhCliError("INVALID_PROMPT_LIBRARY_PATH", f"基础积木必须使用 JSON 文件：{path}")
        if not path.is_file():
            raise RhCliError("INVALID_PROMPT_LIBRARY_PATH", f"基础积木 JSON 文件不存在：{path}")
        document = self._read(path, {"version": VERSION, "blocks": []})
        if not isinstance(document.get("blocks"), list):
            raise RhCliError("INVALID_PROMPT_LIBRARY_PATH", "基础积木 JSON 必须是包含 blocks 数组的对象。")
        with self._lock:
            self.library_path = path
        return path

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

    @staticmethod
    def _normalise_blocks(values: Any) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        ids: set[str] = set()
        for value in values if isinstance(values, list) else []:
            block = _block(value)
            if block and block["id"] not in ids:
                blocks.append(block)
                ids.add(block["id"])
        return blocks

    def _write_library(self, document: dict[str, Any]) -> None:
        document = dict(document)
        document["version"] = VERSION
        document["blocks"] = self._normalise_blocks(document.get("blocks"))
        self._write(self.library_path, document)

    def _library(self) -> dict[str, Any]:
        raw = self._read(self.library_path, {"version": VERSION, "blocks": []})
        return {"version": VERSION, "blocks": self._normalise_blocks(raw.get("blocks"))}

    def _state(self) -> dict[str, Any]:
        raw = self._read(self.state_path, {"version": VERSION, "items": []})
        return {"version": VERSION, "items": _items(raw.get("items"))}

    @staticmethod
    def _normalise_group_folders(values: Any) -> list[dict[str, Any]]:
        folders: list[dict[str, Any]] = []
        ids: set[str] = set()
        for value in values if isinstance(values, list) else []:
            if not isinstance(value, dict):
                continue
            folder_id = str(value.get("id") or "").strip()
            name = str(value.get("name") or "").strip()
            if not folder_id or not name or folder_id in ids:
                continue
            folders.append({
                "id": folder_id,
                "name": name,
                "created_at": _timestamp(value.get("created_at")),
                "updated_at": _timestamp(value.get("updated_at")),
            })
            ids.add(folder_id)
        return folders

    def _group_file_path(self, group_id: str) -> Path:
        clean_id = str(group_id or "").strip()
        if not clean_id:
            raise RhCliError("INVALID_GROUP", "组状态必须包含 ID。")
        # Quote the ID so even legacy IDs cannot escape the prompt-groups directory.
        return self.group_files_path / f"{quote(clean_id, safe='-_.~')}.json"

    def _group_file_reference(self, group_id: str) -> str:
        return f"{self.group_files_path.name}/{self._group_file_path(group_id).name}"

    def _group_metadata(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        group_id = str(value.get("id") or "").strip()
        name = str(value.get("name") or "").strip()
        if not group_id or not name:
            return None
        metadata = {
            "id": group_id,
            "name": name,
            "updated_at": _timestamp(value.get("updated_at")),
            "file": self._group_file_reference(group_id),
        }
        folder_id = str(value.get("folder_id") or "").strip()
        if folder_id:
            metadata["folder_id"] = folder_id
        return metadata

    def _group_index(self) -> dict[str, Any]:
        raw = self._read(self.groups_path, {"version": VERSION, "folders": [], "groups": []})
        groups: list[dict[str, Any]] = []
        ids: set[str] = set()
        for value in raw.get("groups", []):
            metadata = self._group_metadata(value)
            if not metadata or metadata["id"] in ids:
                continue
            groups.append(metadata)
            ids.add(metadata["id"])
        return {
            "version": VERSION,
            "folders": self._normalise_group_folders(raw.get("folders")),
            "groups": groups,
        }

    def _write_group_file(self, group: dict[str, Any]) -> None:
        document = {
            "version": VERSION,
            "id": str(group.get("id") or "").strip(),
            "name": str(group.get("name") or "").strip(),
            "updated_at": _timestamp(group.get("updated_at")),
            "items": _items(group.get("items")),
        }
        folder_id = str(group.get("folder_id") or "").strip()
        if folder_id:
            document["folder_id"] = folder_id
        self._write(self._group_file_path(document["id"]), document)

    def _write_groups(self, document: dict[str, Any]) -> None:
        """Persist group contents separately, then atomically publish the index."""
        folders = self._normalise_group_folders(document.get("folders"))
        groups: list[dict[str, Any]] = []
        ids: set[str] = set()
        for value in document.get("groups", []):
            metadata = self._group_metadata(value)
            if not metadata or metadata["id"] in ids:
                continue
            self._write_group_file({**value, **metadata})
            groups.append(metadata)
            ids.add(metadata["id"])
        self._write(self.groups_path, {"version": VERSION, "folders": folders, "groups": groups})

    def _migrate_group_files(self) -> None:
        """Split the old groups.json document into an index and one file per group."""
        raw = self._read(self.groups_path, {"version": VERSION, "folders": [], "groups": []})
        values = raw.get("groups")
        if not isinstance(values, list) or not any(isinstance(value, dict) and "items" in value for value in values):
            return

        document = {"version": VERSION, "folders": self._normalise_group_folders(raw.get("folders")), "groups": []}
        ids: set[str] = set()
        for value in values:
            metadata = self._group_metadata(value)
            if not metadata or metadata["id"] in ids:
                continue
            group = {
                **metadata,
                "items": _items(value.get("items")),
            }
            self._write_group_file(group)
            document["groups"].append(metadata)
            ids.add(metadata["id"])
        self._write(self.groups_path, document)

    def _groups(self) -> dict[str, Any]:
        index = self._group_index()
        groups: list[dict[str, Any]] = []
        for metadata in index["groups"]:
            raw_group = self._read(self._group_file_path(metadata["id"]), {"items": []})
            group = {
                "id": metadata["id"],
                "name": metadata["name"],
                "updated_at": metadata["updated_at"],
                "items": _items(raw_group.get("items")),
            }
            if metadata.get("folder_id"):
                group["folder_id"] = metadata["folder_id"]
            groups.append(group)
        return {"version": VERSION, "folders": index["folders"], "groups": groups}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"library": self._library(), "state": self._state(), "groups": self._groups()}

    def groups(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._groups()["groups"])

    def prompt_group_folders(self) -> list[dict[str, Any]]:
        with self._lock:
            document = self._group_index()
            counts: dict[str, int] = {}
            for group in document["groups"]:
                folder_id = str(group.get("folder_id") or "").strip()
                if folder_id:
                    counts[folder_id] = counts.get(folder_id, 0) + 1
            return [
                {
                    **folder,
                    "group_count": counts.get(folder["id"], 0),
                }
                for folder in document["folders"]
            ]

    @staticmethod
    def _clean_group_folder_name(name: str) -> str:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise RhCliError("INVALID_PROMPT_GROUP_FOLDER", "文件夹名称不能为空。")
        if len(clean_name) > 80:
            raise RhCliError("INVALID_PROMPT_GROUP_FOLDER", "文件夹名称不能超过 80 个字符。")
        if any(char in clean_name for char in "/\\\0"):
            raise RhCliError("INVALID_PROMPT_GROUP_FOLDER", "文件夹名称不能包含路径分隔符。")
        return clean_name

    def create_prompt_group_folder(self, name: str) -> dict[str, Any]:
        clean_name = self._clean_group_folder_name(name)
        with self._lock:
            document = self._groups()
            if any(item["name"].casefold() == clean_name.casefold() for item in document["folders"]):
                raise RhCliError("PROMPT_GROUP_FOLDER_EXISTS", f"文件夹已存在：{clean_name}")
            timestamp = time.time_ns() // 1_000_000
            folder = {
                "id": _new_id("pgf"),
                "name": clean_name,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            document["folders"].append(folder)
            self._write_groups(document)
            return {**folder, "group_count": 0}

    def rename_prompt_group_folder(self, folder_id: str, name: str) -> dict[str, Any]:
        clean_id = str(folder_id or "").strip()
        clean_name = self._clean_group_folder_name(name)
        with self._lock:
            document = self._groups()
            target = next((item for item in document["folders"] if item["id"] == clean_id), None)
            if target is None:
                raise RhCliError("PROMPT_GROUP_FOLDER_NOT_FOUND", f"找不到文件夹：{clean_id}")
            if any(item["id"] != clean_id and item["name"].casefold() == clean_name.casefold() for item in document["folders"]):
                raise RhCliError("PROMPT_GROUP_FOLDER_EXISTS", f"文件夹已存在：{clean_name}")
            target["name"] = clean_name
            target["updated_at"] = time.time_ns() // 1_000_000
            self._write_groups(document)
            return next(item for item in self.prompt_group_folders() if item["id"] == clean_id)

    def delete_prompt_group_folder(self, folder_id: str) -> None:
        clean_id = str(folder_id or "").strip()
        with self._lock:
            document = self._groups()
            if not any(item["id"] == clean_id for item in document["folders"]):
                raise RhCliError("PROMPT_GROUP_FOLDER_NOT_FOUND", f"找不到文件夹：{clean_id}")
            document["folders"] = [item for item in document["folders"] if item["id"] != clean_id]
            for group in document["groups"]:
                if str(group.get("folder_id") or "") == clean_id:
                    group.pop("folder_id", None)
            self._write_groups(document)

    def _group_folder_id(self, folder_id: Any) -> str:
        clean_id = str(folder_id or "").strip()
        if not clean_id:
            return ""
        if not any(item["id"] == clean_id for item in self._groups()["folders"]):
            raise RhCliError("PROMPT_GROUP_FOLDER_NOT_FOUND", "找不到要归类的提示词组文件夹。")
        return clean_id

    def get_group(self, group_id: str) -> dict[str, Any] | None:
        clean_id = str(group_id or "").strip()
        if not clean_id:
            return None
        with self._lock:
            group = next((item for item in self._groups()["groups"] if item["id"] == clean_id), None)
            if group is None:
                raise RhCliError("GROUP_NOT_FOUND", "找不到要关联的提示词组。")
            return copy.deepcopy(group)

    def add_block(self, value: dict[str, Any]) -> dict[str, Any]:
        block = _block(value)
        if not block:
            raise RhCliError("INVALID_BLOCK", "积木必须包含标题和文本内容。")
        with self._lock:
            document = self._library()
            if any(item["id"] == block["id"] for item in document["blocks"]):
                raise RhCliError("BLOCK_EXISTS", "积木 ID 已存在。")
            document["blocks"].append(block)
            self._write_library(document)
        return block

    def update_block(self, block_id: str, value: dict[str, Any]) -> dict[str, Any]:
        payload = dict(value)
        payload["id"] = block_id
        with self._lock:
            library = self._library()
            index = next((index for index, item in enumerate(library["blocks"]) if item["id"] == block_id), None)
            if index is None:
                raise RhCliError("BLOCK_NOT_FOUND", "找不到这块积木。")
            if "category" not in payload:
                payload["category"] = library["blocks"][index].get("category") or "未分类"
            block = _block(payload)
            if not block:
                raise RhCliError("INVALID_BLOCK", "积木必须包含标题和文本内容。")
            library["blocks"][index] = block
            state = self._state()
            state["items"] = _refresh_item_snapshots(state["items"], block_id, block)
            groups = self._groups()
            for group in groups["groups"]:
                group["items"] = _refresh_item_snapshots(group["items"], block_id, block)
            self._write_library(library)
            self._write(self.state_path, state)
            self._write_groups(groups)
        return block

    def update_block_rating(self, block_id: str, rating: Any) -> dict[str, Any]:
        score = normalize_library_rating(rating)
        with self._lock:
            library = self._library()
            current = next((item for item in library["blocks"] if item["id"] == block_id), None)
            if current is None:
                raise RhCliError("BLOCK_NOT_FOUND", "找不到这块积木。")
            payload = dict(current)
            payload["tags"] = replace_rating_tag(list(current.get("tags") or []), score)
        return self.update_block(block_id, payload)

    def delete_block(self, block_id: str) -> None:
        with self._lock:
            document = self._library()
            blocks = [item for item in document["blocks"] if item["id"] != block_id]
            if len(blocks) == len(document["blocks"]):
                raise RhCliError("BLOCK_NOT_FOUND", "找不到这块积木。")
            document["blocks"] = blocks
            self._write_library(document)

    def save_state(self, values: Any) -> dict[str, Any]:
        document = {"version": VERSION, "items": _items(values)}
        with self._lock:
            self._write(self.state_path, document)
        return document

    def task_group_snapshot(self) -> dict[str, Any]:
        """Return the current workbench in the same shape as a saved group state."""
        with self._lock:
            return {
                "id": _new_id("task-group"),
                "name": "任务提交时组装台",
                "updated_at": time.time_ns() // 1_000_000,
                "items": self._state()["items"],
            }

    def save_group(self, name: str, values: Any, group_id: str | None = None, folder_id: str | None = None) -> dict[str, Any]:
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
                if folder_id is not None:
                    clean_folder_id = self._group_folder_id(folder_id)
                    if clean_folder_id:
                        group["folder_id"] = clean_folder_id
                    else:
                        group.pop("folder_id", None)
            else:
                group = {"id": _new_id("group"), "name": clean_name, "updated_at": time.time_ns() // 1_000_000, "items": items}
                if folder_id is not None:
                    clean_folder_id = self._group_folder_id(folder_id)
                    if clean_folder_id:
                        group["folder_id"] = clean_folder_id
                document["groups"].append(group)
            self._write_groups(document)
            return copy.deepcopy(group)

    def delete_group(self, group_id: str) -> None:
        with self._lock:
            document = self._groups()
            groups = [item for item in document["groups"] if item["id"] != group_id]
            if len(groups) == len(document["groups"]):
                raise RhCliError("GROUP_NOT_FOUND", "找不到这个组状态。")
            document["groups"] = groups
            self._write_groups(document)
            group_file = self._group_file_path(group_id)
            if group_file.exists():
                group_file.unlink()

    def migrate(self, custom_blocks: Any, stage: Any) -> dict[str, Any]:
        with self._lock:
            library = self._library()
            existing = {item["id"] for item in library["blocks"]}
            for value in custom_blocks if isinstance(custom_blocks, list) else []:
                block = _block(value)
                if block and block["id"] not in existing:
                    library["blocks"].append(block)
                    existing.add(block["id"])
            self._write_library(library)
            state = self.save_state(stage)
            return {"library": library, "state": state, "groups": self._groups()}
