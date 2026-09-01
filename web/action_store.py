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


VERSION = 2
DEFAULT_RESOURCES_PATH = Path("/Users/apple/Documents/VideoMake/ref/Resources.md")


def _action_id(image_path: str, title: str) -> str:
    source = image_path or title
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    return f"pose-{digest}"


def _pair_key(path: str, kind: str) -> str:
    """Return the stable basename used to pair a color image and its depth map."""
    stem = Path(path).stem
    if kind == "depth" and stem.endswith("_depth"):
        stem = stem[:-6]
    return stem


def _category(value: str) -> str:
    return re.sub(r"^[一二三四五六七八九十]+、", "", value.strip())


def _normalise_action(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    action_id = str(value.get("id") or "").strip()
    title = str(value.get("title") or "").strip()
    text = str(value.get("text") or "").strip()
    color_image_path = str(value.get("color_image_path") or value.get("image_path") or "").strip()
    depth_image_path = str(value.get("depth_image_path") or "").strip()
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
        # image_path remains as a compatibility alias for older callers/cache files.
        "image_path": color_image_path,
        "color_image_path": color_image_path,
        "depth_image_path": depth_image_path,
        "pair_key": str(value.get("pair_key") or _pair_key(color_image_path or depth_image_path, "color")).strip(),
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
            color_image_path = current["color_image_path"]
            depth_image_path = current["depth_image_path"]
            image_path = color_image_path or depth_image_path
            tags = ["pose"]
            if current_category:
                tags.insert(0, current_category)
            actions.append({
                "id": _action_id(image_path, title),
                "tags": tags,
                "title": title,
                "text": prompt,
                "image_path": image_path,
                "color_image_path": color_image_path,
                "depth_image_path": depth_image_path,
                "pair_key": _pair_key(color_image_path or depth_image_path, "color"),
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
                current = {
                    "title": title,
                    "color_image_path": "",
                    "depth_image_path": "",
                    "prompt_lines": [],
                }
                continue
            if not current:
                continue
            for image_match in re.finditer(r"!\[[^\]]*\]\((pose/(color|depth)/[^)]+)\)", line):
                image_path, kind = image_match.groups()
                if kind == "color" and not current["color_image_path"]:
                    current["color_image_path"] = image_path
                elif kind == "depth" and not current["depth_image_path"]:
                    current["depth_image_path"] = image_path
            if raw_line.lstrip().startswith(">"):
                prompt_line = raw_line.lstrip()[1:].strip()
                current["prompt_lines"].append(prompt_line)
            elif not line and current["prompt_lines"] and current["prompt_lines"][-1] != "":
                current["prompt_lines"].append("")
        finish()
        return actions

    def _source_signature(self) -> tuple[int, str]:
        if not self.source_path.is_file():
            return 0, ""
        digest = hashlib.sha256()
        with self.source_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return self.source_path.stat().st_mtime_ns, digest.hexdigest()

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            source_mtime, source_sha256 = self._source_signature()
            document = self._read()
            if not force and source_sha256 and document.get("source_sha256") == source_sha256 and isinstance(document.get("actions"), list):
                actions = [_normalise_action(value) for value in document["actions"]]
                self._actions = [value for value in actions if value]
                return
            if self.source_path.is_file():
                self._actions = self._parse_source()
                self._write({
                    "version": VERSION,
                    "source": str(self.source_path),
                    "source_mtime_ns": source_mtime,
                    "source_sha256": source_sha256,
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

    def image_path(self, action_id: str, kind: str = "color") -> Path | None:
        with self._lock:
            action = self._find(action_id)
            if not action:
                return None
            if kind not in {"color", "depth"}:
                return None
            raw_path = action.get("color_image_path") or action.get("image_path") if kind == "color" else action.get("depth_image_path")
            relative = Path(str(raw_path or ""))
            source_root = self.source_path.parent.resolve()
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                return None
            path = (source_root / relative).resolve()
            if source_root not in path.parents or not path.is_file():
                return None
            return path

    def _pair_status(self, action: dict[str, Any]) -> tuple[str, str, bool, bool]:
        color_path = self.image_path(action["id"], "color")
        depth_path = self.image_path(action["id"], "depth")
        color_exists = color_path is not None
        depth_exists = depth_path is not None
        color_key = _pair_key(str(action.get("color_image_path") or action.get("image_path") or ""), "color")
        depth_key = _pair_key(str(action.get("depth_image_path") or ""), "depth")
        if not color_exists and not depth_exists:
            return "missing_both", "原图和深度图都不存在", False, False
        if not color_exists:
            return "missing_color", "缺少原图", False, depth_exists
        if not depth_exists:
            return "missing_depth", "缺少深度图", True, False
        if color_key != depth_key:
            return "mismatched", "原图与深度图文件名不匹配", True, True
        return "paired", "原图与深度图已配对", True, True

    def public_actions(self) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for action in self._actions:
                item = copy.deepcopy(action)
                status, message, color_available, depth_available = self._pair_status(action)
                item.pop("image_path", None)
                item.pop("color_image_path", None)
                item.pop("depth_image_path", None)
                item["image_available"] = color_available
                item["image_url"] = f"/api/prompt/actions/{quote(action['id'], safe='')}/image" if color_available else ""
                item["color_image_available"] = color_available
                item["color_image_url"] = item["image_url"]
                item["depth_image_available"] = depth_available
                item["depth_image_url"] = f"/api/prompt/actions/{quote(action['id'], safe='')}/depth" if depth_available else ""
                item["pair_status"] = status
                item["pair_message"] = message
                result.append(item)
            return result

    def source_status(self) -> dict[str, Any]:
        with self._lock:
            _, source_sha256 = self._source_signature()
            public_actions = self.public_actions()
            paired = sum(1 for action in public_actions if action["pair_status"] == "paired")
            issues = [
                {"id": action["id"], "title": action["title"], "status": action["pair_status"], "message": action["pair_message"]}
                for action in public_actions
                if action["pair_status"] != "paired"
            ]
            return {
                "source": self.source_path.name,
                "source_sha256": source_sha256,
                "action_count": len(public_actions),
                "paired_count": paired,
                "issue_count": len(issues),
                "issues": issues,
            }
