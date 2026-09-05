from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .library_rating import normalize_library_rating, replace_rating_tag
from .media_rename import rename_media_files, safe_media_stem


VERSION = 6
DEFAULT_REFERENCE_ROOT = Path("/Users/apple/Documents/VideoMake/ref")

REFERENCE_DEFINITIONS = (
    ("character", "人物", "character/character.json"),
    ("audio", "音频", "audio/audio.json"),
    ("background", "背景", "background/background.json"),
    ("clothes", "服装", "clothes/clothes.json"),
)

IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".webm"}


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


def _reference_id(
    kind: str,
    source: str,
    title: str,
    *,
    category: str = "",
    image_path: str = "",
    audio_path: str = "",
) -> str:
    identity = "|".join((kind, source, category, title, image_path, audio_path))
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"{kind}-{digest}"


def _normalise_reference(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    reference_id = str(value.get("id") or "").strip()
    kind = str(value.get("kind") or "").strip()
    category = str(value.get("category") or "未分类").strip() or "未分类"
    title = str(value.get("title") or "").strip()
    text = str(value.get("text") or "").strip()
    image_path = str(value.get("image_path") or "").strip()
    audio_path = str(value.get("audio_path") or "").strip()
    if not reference_id or not kind or not title or (not text and not image_path and not audio_path):
        return None
    kind_label = str(value.get("kind_label") or kind).strip()
    tags = _tags(value.get("tags"))
    if kind_label and kind_label not in tags:
        tags.insert(0, kind_label)
    return {
        "id": reference_id,
        "kind": kind,
        "kind_label": kind_label,
        "category": category,
        "tags": tags,
        "source_tags": _tags(value.get("source_tags", value.get("tags"))),
        "title": title,
        "text": text,
        "image_path": image_path,
        "audio_path": audio_path,
        "source_path": str(value.get("source_path") or "").strip(),
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


class ReferenceStore:
    """Build and serve the non-pose reference libraries from JSON source files."""

    def __init__(
        self,
        data_root: str | Path,
        source_root: str | Path | None = None,
        source_paths: dict[str, str | Path] | None = None,
    ) -> None:
        self.root = Path(data_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_root = (
            Path(source_root).expanduser().resolve()
            if source_root is not None
            else self._resolve_source_root()
        )
        configured_paths = source_paths if isinstance(source_paths, dict) else {}
        self._configured_source_paths: dict[str, Path] = {}
        for kind, _, relative in REFERENCE_DEFINITIONS:
            value = configured_paths.get(kind)
            self._configured_source_paths[kind] = (
                _json_source_path(value)
                if value is not None and str(value).strip()
                else (self.source_root / relative).resolve()
            )
        self._lock = threading.RLock()
        self._references: list[dict[str, Any]] = []
        self._source_files: list[Path] = []
        self.refresh()

    @staticmethod
    def _resolve_source_root() -> Path:
        configured = os.environ.get("RH_PROMPT_REFERENCE_ROOT", "").strip()
        candidates = [
            Path(configured).expanduser() if configured else None,
            DEFAULT_REFERENCE_ROOT,
            Path.home() / "Documents" / "VideoMake" / "ref",
            Path(__file__).resolve().parents[2] / "VideoMake" / "ref",
        ]
        for candidate in candidates:
            if candidate and candidate.is_dir():
                return candidate.resolve()
        return (Path(configured).expanduser() if configured else DEFAULT_REFERENCE_ROOT).resolve()

    def _source_paths(self) -> list[Path]:
        return [self._configured_source_paths[kind] for kind, _, _ in REFERENCE_DEFINITIONS]

    def source_paths(self) -> dict[str, str]:
        with self._lock:
            return {kind: str(self._configured_source_paths[kind]) for kind, _, _ in REFERENCE_DEFINITIONS}

    def set_source_paths(self, values: dict[str, str | Path]) -> dict[str, str]:
        if not isinstance(values, dict):
            raise ValueError("参考资源路径必须是对象")
        allowed = {kind for kind, _, _ in REFERENCE_DEFINITIONS}
        next_paths = dict(self._configured_source_paths)
        for raw_kind, raw_path in values.items():
            kind = str(raw_kind or "").strip()
            if kind not in allowed:
                raise ValueError(f"未知的参考资源类型：{kind}")
            path = Path(str(raw_path or "")).expanduser().resolve()
            if path.suffix.lower() != ".json":
                raise ValueError(f"{kind} 资源库必须使用 JSON 文件：{path}")
            if not path.is_file():
                raise FileNotFoundError(f"{kind} 资源 JSON 文件不存在：{path}")
            next_paths[kind] = path
        with self._lock:
            self._configured_source_paths = next_paths
            self.refresh(force=True)
        return self.source_paths()

    def set_source_root(self, value: str | Path) -> Path:
        root = Path(value).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"媒体库根目录不存在：{root}")
        with self._lock:
            self.source_root = root
            self._configured_source_paths = {
                kind: (root / relative).resolve()
                for kind, _, relative in REFERENCE_DEFINITIONS
            }
            self.refresh(force=True)
        return root

    def _source_signature(self) -> tuple[dict[str, str], str]:
        signatures: dict[str, str] = {}
        digest = hashlib.sha256()
        for path in self._source_paths():
            relative = self._source_key(path)
            if path.is_file():
                file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
                signatures[relative] = file_digest
                digest.update(relative.encode("utf-8"))
                digest.update(file_digest.encode("ascii"))
            else:
                signatures[relative] = ""
                digest.update(relative.encode("utf-8"))
                digest.update(b"missing")
        return signatures, digest.hexdigest()

    def _source_key(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.source_root))
        except ValueError:
            return str(path)

    def _read_source(self, kind: str, kind_label: str, source_path: Path) -> list[dict[str, Any]]:
        if not source_path.is_file():
            return []
        try:
            document = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return []
        if not isinstance(document, dict) or not isinstance(document.get("references"), list):
            return []
        references: list[dict[str, Any]] = []
        for value in document["references"]:
            if not isinstance(value, dict):
                continue
            item = dict(value)
            item["kind"] = kind
            item["kind_label"] = kind_label
            item["source_path"] = str(source_path)
            normalised = _normalise_reference(item)
            if normalised:
                references.append(normalised)
        return references

    def _read_all(self) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        self._source_files = []
        for kind, kind_label, _ in REFERENCE_DEFINITIONS:
            path = self._configured_source_paths[kind]
            self._source_files.append(path)
            references.extend(self._read_source(kind, kind_label, path))
        return references

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            self._references = self._read_all()

    def references(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._references)

    def _find(self, reference_id: str) -> dict[str, Any] | None:
        return next((item for item in self._references if item["id"] == reference_id), None)

    def _resolve_media(self, reference: dict[str, Any], field: str) -> Path | None:
        raw_path = str(reference.get(field) or "").strip()
        if not raw_path:
            return None
        relative = Path(raw_path)
        if relative.is_absolute():
            return None
        source_path = Path(str(reference.get("source_path") or "")).expanduser().resolve()
        roots = [source_path.parent, self.source_root]
        for root in roots:
            candidate = (root / relative).resolve()
            if (root.resolve() in candidate.parents or candidate == root.resolve()) and candidate.is_file():
                return candidate
        return None

    def media_path(self, reference_id: str, kind: str) -> Path | None:
        with self._lock:
            reference = self._find(reference_id)
            if not reference or kind not in {"image", "audio"}:
                return None
            return self._resolve_media(reference, "image_path" if kind == "image" else "audio_path")

    def public_references(self) -> list[dict[str, Any]]:
        with self._lock:
            result: list[dict[str, Any]] = []
            for reference in self._references:
                item = copy.deepcopy(reference)
                image_available = self.media_path(reference["id"], "image") is not None
                audio_available = self.media_path(reference["id"], "audio") is not None
                item["image_path"] = str(reference.get("image_path") or "")
                item["audio_path"] = str(reference.get("audio_path") or "")
                item.pop("source_path", None)
                item["image_available"] = image_available
                item["audio_available"] = audio_available
                item["image_url"] = f"/api/prompt/references/{quote(reference['id'], safe='')}/image" if image_available else ""
                item["audio_url"] = f"/api/prompt/references/{quote(reference['id'], safe='')}/audio" if audio_available else ""
                item["media_type"] = "image" if image_available else ("audio" if audio_available else "text")
                result.append(item)
            return result

    def _write_source(self, kind: str, references: list[dict[str, Any]]) -> None:
        source_path = self._configured_source_paths[kind]
        source_path.parent.mkdir(parents=True, exist_ok=True)
        persisted = []
        for reference in references:
            item = copy.deepcopy(reference)
            item.pop("source_path", None)
            persisted.append(item)
        kind_label = next(label for entry_kind, label, _ in REFERENCE_DEFINITIONS if entry_kind == kind)
        document = {"version": VERSION, "kind": kind, "kind_label": kind_label, "references": persisted}
        temporary = source_path.with_name(f".{source_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(source_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def add_reference(self, kind: str, value: dict[str, Any]) -> dict[str, Any]:
        allowed = {entry_kind for entry_kind, _, _ in REFERENCE_DEFINITIONS}
        kind = str(kind or "").strip()
        if kind not in allowed:
            raise ValueError(f"未知的参考资源类型：{kind}")
        if not isinstance(value, dict):
            raise ValueError("参考资源内容必须是对象。")
        payload = dict(value)
        payload["kind"] = kind
        payload["kind_label"] = next(label for entry_kind, label, _ in REFERENCE_DEFINITIONS if entry_kind == kind)
        payload["image_path"] = _relative_path(payload.get("image_path"), self.source_root)
        payload["audio_path"] = _relative_path(payload.get("audio_path"), self.source_root)
        payload["title"] = str(payload.get("title") or "").strip()
        payload["text"] = str(payload.get("text") or "").strip()
        payload["category"] = str(payload.get("category") or "未分类").strip() or "未分类"
        payload["source_tags"] = _tags(payload.get("tags"))
        if not payload["title"] or not payload["text"] and not payload["image_path"] and not payload["audio_path"]:
            raise ValueError("参考资源名称以及媒体或文本内容不能为空。")
        payload["id"] = str(payload.get("id") or _reference_id(
            kind,
            self._source_key(self._configured_source_paths[kind]),
            payload["title"],
            category=payload["category"],
            image_path=payload["image_path"],
            audio_path=payload["audio_path"],
        )).strip()
        if any(item["id"] == payload["id"] for item in self._references):
            raise ValueError("参考资源 ID 已存在。")
        normalised = _normalise_reference(payload)
        if not normalised:
            raise ValueError("参考资源内容不完整。")
        with self._lock:
            entries = [item for item in self._references if item["kind"] == kind]
            entries.append(normalised)
            self._write_source(kind, entries)
            self.refresh(force=True)
            return copy.deepcopy(self._find(normalised["id"]))

    def update_reference(self, reference_id: str, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self._find(reference_id)
            if not current:
                raise KeyError(f"找不到参考资源：{reference_id}")
            kind = current["kind"]
            payload = dict(current)
            payload.update(value if isinstance(value, dict) else {})
            payload["id"] = reference_id
            payload["kind"] = kind
            payload["kind_label"] = current.get("kind_label") or kind
            payload["image_path"] = _relative_path(payload.get("image_path"), self.source_root)
            payload["audio_path"] = _relative_path(payload.get("audio_path"), self.source_root)
            payload["title"] = str(payload.get("title") or "").strip()
            payload["text"] = str(payload.get("text") or "").strip()
            payload["category"] = str(payload.get("category") or "未分类").strip() or "未分类"
            if isinstance(value, dict) and "source_tags" in value:
                payload["source_tags"] = _tags(value.get("source_tags"))
            elif isinstance(value, dict) and "tags" in value:
                payload["source_tags"] = _tags(value.get("tags"))
            normalised = _normalise_reference(payload)
            if not normalised:
                raise ValueError("参考资源名称以及媒体或文本内容不能为空。")
            if normalised["title"] != current["title"]:
                stem = safe_media_stem(normalised["title"], "resource")
                reference_for_media = dict(current)
                reference_for_media.update(payload)
                for field in ("image_path", "audio_path"):
                    raw_path = str(payload.get(field) or "").strip()
                    if not raw_path:
                        continue
                    media_path = self._resolve_media(reference_for_media, field)
                    if media_path is None:
                        continue
                    renamed = rename_media_files(
                        media_path.parent,
                        {field: (media_path.name, f"{stem}{media_path.suffix}")},
                    )
                    if field in renamed:
                        payload[field] = str(Path(raw_path).with_name(Path(renamed[field]).name))
                normalised = _normalise_reference(payload)
                if not normalised:
                    raise ValueError("参考资源内容不完整。")
            entries = [normalised if item["id"] == reference_id else item for item in self._references if item["kind"] == kind]
            self._write_source(kind, entries)
            self.refresh(force=True)
            return copy.deepcopy(self._find(reference_id))

    def update_reference_rating(self, reference_id: str, rating: Any) -> dict[str, Any]:
        score = normalize_library_rating(rating)
        with self._lock:
            current = self._find(reference_id)
            if not current:
                raise KeyError(f"找不到参考资源：{reference_id}")
            source_tags = replace_rating_tag(list(current.get("source_tags") or []), score)
            tags = replace_rating_tag(list(current.get("tags") or []), score)
        return self.update_reference(reference_id, {"source_tags": source_tags, "tags": tags})

    def delete_reference(self, reference_id: str) -> None:
        with self._lock:
            current = self._find(reference_id)
            if not current:
                raise KeyError(f"找不到参考资源：{reference_id}")
            kind = current["kind"]
            entries = [item for item in self._references if item["kind"] == kind and item["id"] != reference_id]
            self._write_source(kind, entries)
            self.refresh(force=True)

    def kind_counts(self) -> dict[str, int]:
        with self._lock:
            return {
                kind: sum(1 for item in self._references if item["kind"] == kind)
                for kind, _, _ in REFERENCE_DEFINITIONS
            }

    def source_status(self) -> dict[str, Any]:
        with self._lock:
            _, source_sha256 = self._source_signature()
            missing_files = [
                self._source_key(path)
                for path in self._source_files
                if not path.is_file()
            ]
            return {
                "source_root": str(self.source_root),
                "source_sha256": source_sha256,
                "reference_count": len(self._references),
                "kind_counts": self.kind_counts(),
                "source_files": [self._source_key(path) for path in self._source_files],
                "source_paths": self.source_paths(),
                "missing_files": missing_files,
            }
