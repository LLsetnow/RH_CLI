from __future__ import annotations

import copy
import hashlib
import json
import re
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


def _stable_block_id(title: str, category: str = "") -> str:
    source = f"{category}\n{title}".strip()
    return f"block-{hashlib.sha1(source.encode('utf-8')).hexdigest()[:12]}"


def _markdown_metadata(line: str, key: str) -> str | None:
    match = re.match(rf"^\s*(?:[-*]\s*)?{re.escape(key)}\s*:\s*(.*?)\s*$", line, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _parse_markdown_library(path: Path) -> list[dict[str, Any]]:
    """Parse the stable Markdown format used by the prompt block library."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    blocks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    current: dict[str, Any] | None = None
    current_category = ""

    def finish() -> None:
        nonlocal current
        if not current:
            return
        title = str(current.get("title") or "").strip()
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(current.get("text_lines") or [])).strip()
        if not title or not text:
            current = None
            return
        block_id = str(current.get("id") or _stable_block_id(title, str(current.get("category") or ""))).strip()
        if not block_id or block_id in seen_ids:
            current = None
            return
        tags = _tags(current.get("tags"))
        blocks.append({
            "id": block_id,
            "category": str(current.get("category") or "未分类").strip() or "未分类",
            "tags": tags,
            "title": title,
            "text": text,
        })
        seen_ids.add(block_id)
        current = None

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("## "):
            finish()
            current_category = ""
            continue
        if line.startswith("### "):
            finish()
            current_category = re.sub(r"^###\s*", "", line).strip()
            continue
        if not line.startswith("#### ") and not current:
            continue
        if line.startswith("#### "):
            finish()
            current = {
                "title": line[5:].strip(),
                "id": "",
                "tags": [],
                "category": current_category,
                "text_lines": [],
            }
            continue
        if current is None:
            continue
        metadata_id = _markdown_metadata(line, "id")
        if metadata_id is not None:
            current["id"] = metadata_id
            continue
        metadata_tags = _markdown_metadata(line, "tags")
        if metadata_tags is not None:
            current["tags"] = _tags(metadata_tags)
            continue
        if line.startswith("<!--") and line.endswith("-->"):
            continue
        if raw_line.lstrip().startswith(">"):
            prompt_line = raw_line.lstrip()[1:]
            current["text_lines"].append(prompt_line[1:] if prompt_line.startswith(" ") else prompt_line)
        elif not line:
            if current["text_lines"] and current["text_lines"][-1] != "":
                current["text_lines"].append("")
        else:
            # Plain lines are accepted too, so a user can write normal Markdown
            # prose without adding a quote marker to every line.
            current["text_lines"].append(raw_line.rstrip())
    finish()
    return blocks


def _markdown_library_text(blocks: list[dict[str, Any]]) -> str:
    lines = [
        "# MiniMax H3 基础提示词积木",
        "",
        "> 每个 ### 标题是一级分类，每个 #### 条目是一块可拖入提示词工作台的积木。字段和正文建议使用英文；对白与画面可见文字按 H3 规则保留原文。",
        "> 修改本文件后刷新提示词工坊即可重新抽取；id 用于保持组装台和组状态中的引用稳定。",
        "",
        "## blocks",
        "",
    ]
    categories: dict[str, list[dict[str, Any]]] = {}
    category_order: list[str] = []
    for block in blocks:
        category = str(block.get("category") or "未分类").strip() or "未分类"
        if category not in categories:
            categories[category] = []
            category_order.append(category)
        categories[category].append(block)
    for category in category_order:
        lines.extend([f"### {category}", ""])
        for block in categories[category]:
            lines.extend([
                f"#### {block['title']}",
                f"id: {block['id']}",
                f"tags: {', '.join(block.get('tags') or [])}",
            ])
            for text_line in str(block.get("text") or "").splitlines() or [""]:
                lines.append(f"> {text_line}" if text_line else ">")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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
    """Markdown-backed prompt block library with JSON state/group persistence."""

    def __init__(self, data_root: str | Path, library_path: str | Path | None = None) -> None:
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.prompt_root = self.root / "prompt"
        self.prompt_root.mkdir(parents=True, exist_ok=True)
        self.default_library_path = (self.prompt_root / "library.md").resolve()
        self.library_path = (
            Path(library_path).expanduser().resolve()
            if library_path is not None and str(library_path).strip()
            else self.default_library_path
        )
        self.state_path = self.prompt_root / "state.json"
        self.groups_path = self.prompt_root / "groups.json"
        self._lock = threading.RLock()
        with self._lock:
            self._migrate_legacy_files()
            self._ensure(self.library_path, {"version": VERSION, "blocks": []})
            self._ensure(self.state_path, {"version": VERSION, "items": []})
            self._ensure(self.groups_path, {"version": VERSION, "groups": []})

    def _migrate_legacy_files(self) -> None:
        """Move legacy flat state files and convert the old library JSON once."""
        migrated_files = {
            self.root / "prompt-state.json": self.state_path,
            self.root / "prompt-groups.json": self.groups_path,
        }
        for legacy_path, target_path in migrated_files.items():
            if target_path.exists() or not legacy_path.is_file():
                continue
            legacy_path.replace(target_path)
        if self.library_path == self.default_library_path and not self.library_path.exists():
            legacy_library_paths = (
                self.root / "prompt-library.json",
                self.prompt_root / "library.json",
            )
            for legacy_path in legacy_library_paths:
                if not legacy_path.is_file():
                    continue
                document = self._read(legacy_path, {"version": VERSION, "blocks": []})
                blocks = self._normalise_blocks(document.get("blocks"))
                if blocks:
                    self._write_library_markdown(blocks)
                break

    def set_library_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise RhCliError("INVALID_PROMPT_LIBRARY_PATH", f"基础积木 Markdown 文件不存在：{path}")
        with self._lock:
            self.library_path = path
        return path

    def _ensure(self, path: Path, default: dict[str, Any]) -> None:
        if not path.exists():
            if path == self.library_path and self._is_markdown_library():
                self._write_library_markdown([])
            else:
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

    def _is_markdown_library(self) -> bool:
        return self.library_path.suffix.lower() in {".md", ".markdown"}

    def _write_library_markdown(self, blocks: list[dict[str, Any]]) -> None:
        temporary = self.library_path.with_name(f".{self.library_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            self.library_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(_markdown_library_text(blocks), encoding="utf-8")
            temporary.replace(self.library_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _write_library(self, document: dict[str, Any]) -> None:
        if self._is_markdown_library():
            self._write_library_markdown(self._normalise_blocks(document.get("blocks")))
        else:
            self._write(self.library_path, document)

    def _library(self) -> dict[str, Any]:
        if self._is_markdown_library():
            return {"version": VERSION, "blocks": self._normalise_blocks(_parse_markdown_library(self.library_path))}
        raw = self._read(self.library_path, {"version": VERSION, "blocks": []})
        return {"version": VERSION, "blocks": self._normalise_blocks(raw.get("blocks"))}

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
            self._write(self.groups_path, groups)
        return block

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
            self._write_library(library)
            state = self.save_state(stage)
            return {"library": library, "state": state, "groups": self._groups()}
