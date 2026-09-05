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

from .library_rating import normalize_library_rating, replace_rating_tag
from .media_rename import rename_media_files, safe_media_stem


VERSION = 6
DEFAULT_POSE_RESOURCES_PATH = Path("/Users/apple/Documents/VideoMake/ref/pose/pose.json")


def _action_id(image_path: str, title: str) -> str:
    source = image_path or title
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    return f"pose-{digest}"


def _pair_key(path: str, kind: str) -> str:
    """Return the stable basename used to pair action auxiliary images."""
    stem = Path(path).stem
    suffixes = {"depth": "_depth", "skeleton": "_skeleton"}
    suffix = suffixes.get(kind, "")
    if suffix and stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    return stem


def _category(value: str) -> str:
    return re.sub(r"^[一二三四五六七八九十]+、", "", value.strip())


def _tags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.replace("，", ",").replace("、", ",").split(",")
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for raw in value:
        tag = str(raw or "").strip()
        if tag and tag not in result:
            result.append(tag)
    return result


def _tags_without_category(value: Any, category: str) -> list[str]:
    return [tag for tag in _tags(value) if tag != category]


def _normalise_action(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    action_id = str(value.get("id") or "").strip()
    title = str(value.get("title") or "").strip()
    text = str(value.get("text") or "").strip()
    color_image_path = str(value.get("color_image_path") or value.get("image_path") or "").strip()
    depth_image_path = str(value.get("depth_image_path") or "").strip()
    skeleton_image_path = str(value.get("skeleton_image_path") or "").strip()
    if not action_id or not title or not text:
        return None
    category = _category(str(value.get("category") or "")) or "未分类"
    return {
        "id": action_id,
        "category": category,
        "tags": _tags_without_category(value.get("tags", []), category),
        "title": title,
        "text": text,
        # image_path remains as a compatibility alias for older callers/cache files.
        "image_path": color_image_path,
        "color_image_path": color_image_path,
        "depth_image_path": depth_image_path,
        "skeleton_image_path": skeleton_image_path,
        "pair_key": str(value.get("pair_key") or _pair_key(color_image_path or depth_image_path, "color")).strip(),
    }


def _relative_path(value: Any, root: Path) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"媒体路径必须位于媒体库根目录内：{path}") from exc
    if ".." in path.parts:
        raise ValueError("媒体路径不能跳出媒体库根目录。")
    return str(path)


def _json_source_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.suffix.lower() in {".md", ".markdown"}:
        path = path.with_suffix(".json")
    return path.resolve()


class ActionStore:
    """Build and serve a local action library from a JSON source file."""

    def __init__(
        self,
        data_root: str | Path,
        source_path: str | Path | None = None,
        source_root: str | Path | None = None,
    ) -> None:
        self.root = Path(data_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_path = (
            _json_source_path(source_path)
            if source_path is not None
            else (Path(source_root).expanduser().resolve() / "pose" / "pose.json" if source_root is not None else self._resolve_source_path())
        )
        self.source_root = (
            Path(source_root).expanduser().resolve()
            if source_root is not None
            else self.source_path.parent.parent.resolve() if self.source_path.parent.name == "pose" else self.source_path.parent.resolve()
        )
        self._lock = threading.RLock()
        self._actions: list[dict[str, Any]] = []
        self.refresh()

    @staticmethod
    def _resolve_source_path() -> Path:
        configured = os.environ.get("RH_PROMPT_RESOURCES_PATH", "").strip()
        candidates = [
            _json_source_path(configured) if configured else None,
            DEFAULT_POSE_RESOURCES_PATH,
            Path.home() / "Documents" / "VideoMake" / "ref" / "pose" / "pose.json",
            Path(__file__).resolve().parents[2] / "VideoMake" / "ref" / "pose" / "pose.json",
        ]
        for candidate in candidates:
            if candidate and candidate.is_file():
                return candidate.resolve()
        return _json_source_path(configured) if configured else DEFAULT_POSE_RESOURCES_PATH

    def set_source_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        if path.suffix.lower() != ".json":
            raise ValueError(f"动作库必须使用 JSON 文件：{path}")
        if not path.is_file():
            raise FileNotFoundError(f"动作库 JSON 文件不存在：{path}")
        with self._lock:
            self.source_path = path
            self.source_root = path.parent.parent.resolve() if path.parent.name == "pose" else path.parent.resolve()
            self.refresh(force=True)
        return path

    def set_source_root(self, value: str | Path) -> Path:
        root = Path(value).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"媒体库根目录不存在：{root}")
        path = root / "pose" / "pose.json"
        with self._lock:
            self.source_root = root
            self.source_path = path
            self.refresh(force=True)
        return root

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
            if not self.source_path.is_file():
                self._actions = []
                return
            try:
                document = json.loads(self.source_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                document = {}
            actions = [_normalise_action(value) for value in document.get("actions", [])] if isinstance(document, dict) else []
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
            if kind not in {"color", "depth", "skeleton"}:
                return None
            if kind == "color":
                raw_path = action.get("color_image_path")
                if raw_path is None:  # compatibility with older JSON entries
                    raw_path = action.get("image_path")
            elif kind == "depth":
                raw_path = action.get("depth_image_path")
            else:
                raw_path = action.get("skeleton_image_path")
            relative = Path(str(raw_path or ""))
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                return None
            source_roots = [self.source_path.parent.resolve()]
            # pose/pose.json stores links as pose/color/... relative to ref/.
            if source_roots[0].name == "pose":
                source_roots.append(source_roots[0].parent)
            for source_root in source_roots:
                path = (source_root / relative).resolve()
                if source_root in path.parents and path.is_file():
                    return path
            return None

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
                item["color_image_path"] = str(action.get("color_image_path") or action.get("image_path") or "")
                item["depth_image_path"] = str(action.get("depth_image_path") or "")
                item.pop("image_path", None)
                item["image_available"] = color_available
                item["image_url"] = f"/api/prompt/actions/{quote(action['id'], safe='')}/image" if color_available else ""
                item["color_image_available"] = color_available
                item["color_image_url"] = item["image_url"]
                item["depth_image_available"] = depth_available
                item["depth_image_url"] = f"/api/prompt/actions/{quote(action['id'], safe='')}/depth" if depth_available else ""
                skeleton_path = self.image_path(action["id"], "skeleton")
                skeleton_available = skeleton_path is not None
                item["skeleton_image_path"] = str(action.get("skeleton_image_path") or "")
                item["skeleton_image_available"] = skeleton_available
                item["skeleton_image_url"] = f"/api/prompt/actions/{quote(action['id'], safe='')}/skeleton" if skeleton_available else ""
                item["pair_status"] = status
                item["pair_message"] = message
                result.append(item)
            return result

    def _write_source(self, actions: list[dict[str, Any]]) -> None:
        self.source_path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "version": VERSION,
            "actions": [_normalise_action(action) for action in actions],
        }
        document["actions"] = [action for action in document["actions"] if action]
        temporary = self.source_path.with_name(f".{self.source_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.source_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def add_action(self, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("动作内容必须是对象。")
        payload = dict(value)
        payload["color_image_path"] = _relative_path(payload.get("color_image_path") or payload.get("image_path"), self.source_root)
        payload["depth_image_path"] = _relative_path(payload.get("depth_image_path"), self.source_root)
        payload["skeleton_image_path"] = _relative_path(payload.get("skeleton_image_path"), self.source_root)
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("动作名称不能为空。")
        payload["id"] = str(payload.get("id") or _action_id(payload["color_image_path"] or payload["depth_image_path"], title)).strip()
        if any(item["id"] == payload["id"] for item in self._actions):
            raise ValueError("动作 ID 已存在。")
        payload["text"] = str(payload.get("text") or "").strip()
        if not payload["text"]:
            raise ValueError("动作提示词不能为空。")
        normalised = _normalise_action(payload)
        if not normalised:
            raise ValueError("动作内容不完整。")
        with self._lock:
            self._actions.append(normalised)
            self._write_source(self._actions)
            self.refresh(force=True)
            return copy.deepcopy(self._find(normalised["id"]))

    def update_action(self, action_id: str, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self._find(action_id)
            if not current:
                raise KeyError(f"找不到动作：{action_id}")
            payload = dict(current)
            payload.update(value if isinstance(value, dict) else {})
            payload["id"] = action_id
            payload["color_image_path"] = _relative_path(payload.get("color_image_path") or payload.get("image_path"), self.source_root)
            payload["depth_image_path"] = _relative_path(payload.get("depth_image_path"), self.source_root)
            payload["skeleton_image_path"] = _relative_path(payload.get("skeleton_image_path"), self.source_root)
            normalised = _normalise_action(payload)
            if not normalised:
                raise ValueError("动作内容不完整。")
            if normalised["title"] != current["title"]:
                stem = safe_media_stem(normalised["title"], "action")
                rename_entries: dict[str, tuple[str, str]] = {}
                for role, field, marker in (
                    ("color", "color_image_path", ""),
                    ("depth", "depth_image_path", "_depth"),
                    ("skeleton", "skeleton_image_path", "_skeleton"),
                ):
                    path = str(payload.get(field) or "").strip()
                    if path:
                        rename_entries[role] = (path, f"{stem}{marker}{Path(path).suffix}")
                renamed = rename_media_files(self.source_root, rename_entries) if rename_entries else {}
                if "color" in renamed:
                    payload["color_image_path"] = renamed["color"]
                    payload["image_path"] = renamed["color"]
                if "depth" in renamed:
                    payload["depth_image_path"] = renamed["depth"]
                if "skeleton" in renamed:
                    payload["skeleton_image_path"] = renamed["skeleton"]
                payload["pair_key"] = _pair_key(
                    str(payload.get("color_image_path") or payload.get("image_path") or payload.get("depth_image_path") or ""),
                    "color",
                )
                normalised = _normalise_action(payload)
                if not normalised:
                    raise ValueError("动作内容不完整。")
            index = self._actions.index(current)
            self._actions[index] = normalised
            self._write_source(self._actions)
            self.refresh(force=True)
            return copy.deepcopy(self._find(action_id))

    def update_action_rating(self, action_id: str, rating: Any) -> dict[str, Any]:
        score = normalize_library_rating(rating)
        with self._lock:
            current = self._find(action_id)
            if not current:
                raise KeyError(f"找不到动作：{action_id}")
            tags = replace_rating_tag(list(current.get("tags") or []), score)
        return self.update_action(action_id, {"tags": tags})

    def delete_action(self, action_id: str) -> None:
        with self._lock:
            before = len(self._actions)
            self._actions = [item for item in self._actions if item["id"] != action_id]
            if len(self._actions) == before:
                raise KeyError(f"找不到动作：{action_id}")
            self._write_source(self._actions)
            self.refresh(force=True)

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
