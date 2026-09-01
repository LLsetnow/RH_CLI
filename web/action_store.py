from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote


VERSION = 1
DEFAULT_RESOURCES_PATH = Path("/Users/apple/Documents/VideoMake/ref/Resources.md")


def _action_id(image_path: str, title: str) -> str:
    source = image_path or title
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    return f"pose-{digest}"


def _category(value: str) -> str:
    return re.sub(r"^[一二三四五六七八九十]+、", "", value.strip())


def _normalise_action(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    action_id = str(value.get("id") or "").strip()
    title = str(value.get("title") or "").strip()
    text = str(value.get("text") or "").strip()
    image_path = str(value.get("image_path") or "").strip()
    if not action_id or not title or not text:
        return None
    raw_tags = value.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    if not isinstance(raw_tags, list):
        raw_tags = []
    return {
        "id": action_id,
        "tags": [str(tag).strip() for tag in raw_tags if str(tag).strip()],
        "title": title,
        "text": text,
        "image_path": image_path,
    }


class ActionStore:
    """Build and serve a local action library from the pose section of Resources.md."""

    def __init__(self, data_root: str | Path, source_path: str | Path | None = None) -> None:
        self.root = Path(data_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "prompt-actions.json"
        self.source_path = (
            Path(source_path).expanduser().resolve()
            if source_path is not None
            else self._resolve_source_path()
        )
        self._lock = threading.RLock()
        self._actions: list[dict[str, Any]] = []
        self.refresh()

    @staticmethod
    def _resolve_source_path() -> Path:
        configured = os.environ.get("RH_PROMPT_RESOURCES_PATH", "").strip()
        candidates = [
            Path(configured).expanduser() if configured else None,
            DEFAULT_RESOURCES_PATH,
            Path.home() / "Documents" / "VideoMake" / "ref" / "Resources.md",
            Path(__file__).resolve().parents[2] / "VideoMake" / "ref" / "Resources.md",
        ]
        for candidate in candidates:
            if candidate and candidate.is_file():
                return candidate.resolve()
        return (Path(configured).expanduser() if configured else DEFAULT_RESOURCES_PATH).resolve()

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, Any]) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _parse_source(self) -> list[dict[str, Any]]:
        lines = self.source_path.read_text(encoding="utf-8").splitlines()
        actions: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        current_category = ""
        in_pose = False

        def finish() -> None:
            if not current:
                return
            prompt = "\n".join(current["prompt_lines"])
            prompt = re.sub(r"\n{3,}", "\n\n", prompt).strip()
            if not prompt:
                return
            title = current["title"]
            image_path = current["image_path"]
            tags = ["pose"]
            if current_category:
                tags.insert(0, current_category)
            actions.append({
                "id": _action_id(image_path, title),
                "tags": tags,
                "title": title,
                "text": prompt,
                "image_path": image_path,
            })

        for raw_line in lines:
            line = raw_line.strip()
            if line == "## pose":
                in_pose = True
                continue
            if in_pose and line.startswith("## "):
                break
            if not in_pose:
                continue
            if line.startswith("### "):
                current_category = _category(line[4:])
                continue
            if line.startswith("#### "):
                finish()
                title = re.sub(r"（[^）]*）$", "", line[5:]).strip()
                title = Path(title).stem
                current = {"title": title, "image_path": "", "prompt_lines": []}
                continue
            if not current:
                continue
            image_match = re.search(r"!\[[^\]]*\]\((pose/(?:color|depth)/[^)]+)\)", line)
            if image_match and not current["image_path"]:
                current["image_path"] = image_match.group(1)
            if raw_line.lstrip().startswith(">"):
                prompt_line = raw_line.lstrip()[1:].strip()
                current["prompt_lines"].append(prompt_line)
            elif not line and current["prompt_lines"] and current["prompt_lines"][-1] != "":
                current["prompt_lines"].append("")
        finish()
        return actions

    def refresh(self) -> None:
        with self._lock:
            source_mtime = self.source_path.stat().st_mtime_ns if self.source_path.is_file() else 0
            document = self._read()
            if source_mtime and document.get("source_mtime_ns") == source_mtime and isinstance(document.get("actions"), list):
                actions = [_normalise_action(value) for value in document["actions"]]
                self._actions = [value for value in actions if value]
                return
            if self.source_path.is_file():
                self._actions = self._parse_source()
                self._write({
                    "version": VERSION,
                    "source": str(self.source_path),
                    "source_mtime_ns": source_mtime,
                    "actions": self._actions,
                })
                return
            actions = [_normalise_action(value) for value in document.get("actions", [])]
            self._actions = [value for value in actions if value]

    def actions(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._actions)

    def _find(self, action_id: str) -> dict[str, Any] | None:
        return next((action for action in self._actions if action["id"] == action_id), None)

    def image_path(self, action_id: str) -> Path | None:
        with self._lock:
            action = self._find(action_id)
            if not action:
                return None
            relative = Path(action.get("image_path") or "")
            source_root = self.source_path.parent.resolve()
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                return None
            path = (source_root / relative).resolve()
            if source_root not in path.parents or not path.is_file():
                return None
            return path

    def public_actions(self) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for action in self._actions:
                item = copy.deepcopy(action)
                available = self.image_path(action["id"]) is not None
                item.pop("image_path", None)
                item["image_available"] = available
                item["image_url"] = f"/api/prompt/actions/{quote(action['id'], safe='')}/image" if available else ""
                result.append(item)
            return result
