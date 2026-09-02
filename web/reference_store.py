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
from urllib.parse import quote, unquote


VERSION = 1
DEFAULT_REFERENCE_ROOT = Path("/Users/apple/Documents/VideoMake/ref")

REFERENCE_DEFINITIONS = (
    ("character", "人物", "character/character.md"),
    ("audio", "音频", "audio/audio.md"),
    ("background", "背景", "background/background.md"),
    ("clothes", "服装", "clothes/clothes.md"),
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


def _reference_id(kind: str, source: str, title: str) -> str:
    digest = hashlib.sha1(f"{kind}:{source}:{title}".encode("utf-8")).hexdigest()[:12]
    return f"{kind}-{digest}"


def _clean_heading(value: str) -> str:
    return re.sub(r"（[^）]*）$", "", value.strip()).strip()


def _normalise_reference(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    reference_id = str(value.get("id") or "").strip()
    kind = str(value.get("kind") or "").strip()
    title = str(value.get("title") or "").strip()
    text = str(value.get("text") or "").strip()
    image_path = str(value.get("image_path") or "").strip()
    audio_path = str(value.get("audio_path") or "").strip()
    if not reference_id or not kind or not title or (not text and not image_path and not audio_path):
        return None
    kind_label = str(value.get("kind_label") or kind).strip()
    return {
        "id": reference_id,
        "kind": kind,
        "kind_label": kind_label,
        "tags": _tags(value.get("tags")),
        "title": title,
        "text": text,
        "image_path": image_path,
        "audio_path": audio_path,
        "source_path": str(value.get("source_path") or "").strip(),
    }


class ReferenceStore:
    """Build and serve the non-pose reference libraries from local Markdown indexes."""

    def __init__(
        self,
        data_root: str | Path,
        source_root: str | Path | None = None,
        source_paths: dict[str, str | Path] | None = None,
    ) -> None:
        self.root = Path(data_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.prompt_root = self.root / "prompt"
        self.prompt_root.mkdir(parents=True, exist_ok=True)
        self.path = self.prompt_root / "references.json"
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
                Path(value).expanduser().resolve()
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
            if not path.is_file():
                raise FileNotFoundError(f"{kind} 资源 Markdown 文件不存在：{path}")
            next_paths[kind] = path
        with self._lock:
            self._configured_source_paths = next_paths
            self.refresh(force=True)
        return self.source_paths()

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

    @staticmethod
    def _media_links(line: str) -> tuple[list[str], list[str]]:
        images: list[str] = []
        audio: list[str] = []
        for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)|\[[^\]]*\]\(([^)]+)\)", line):
            raw_path = str(match.group(1) or match.group(2) or "").strip()
            if not raw_path or raw_path.startswith(("http://", "https://", "data:")):
                continue
            path = unquote(raw_path.split("#", 1)[0].split("?", 1)[0].strip())
            suffix = Path(path).suffix.lower()
            if suffix in IMAGE_EXTENSIONS and path not in images:
                images.append(path)
            elif suffix in AUDIO_EXTENSIONS and path not in audio:
                audio.append(path)
        return images, audio

    def _parse_source(self, kind: str, kind_label: str, source_path: Path) -> list[dict[str, Any]]:
        if not source_path.is_file():
            return []
        lines = source_path.read_text(encoding="utf-8").splitlines()
        references: list[dict[str, Any]] = []
        current_category = ""
        current_parent = ""
        current: dict[str, Any] | None = None

        def finish() -> None:
            nonlocal current
            if not current:
                return
            text = re.sub(r"\n{3,}", "\n\n", "\n".join(current["prompt_lines"])).strip()
            images = current["images"]
            audio = current["audio"]
            if not text and not images and not audio:
                current = None
                return
            title = current["title"]
            tags = [kind_label]
            if current_category:
                tags.append(current_category)
            if kind == "character" and " · " in title:
                tags.append(title.split(" · ", 1)[0].strip())
            elif current_parent and current_parent != title:
                tags.append(current_parent)
            source_key = self._source_key(source_path)
            references.append({
                "id": _reference_id(kind, source_key, title),
                "kind": kind,
                "kind_label": kind_label,
                "tags": _tags(tags),
                "title": title,
                "text": text,
                "image_path": images[0] if images else "",
                "audio_path": audio[0] if audio else "",
                "source_path": str(source_path),
            })
            current = None

        for raw_line in lines:
            line = raw_line.strip()
            if line.startswith("## "):
                if line[3:].strip() == kind:
                    continue
                if current and line[3:].strip() != kind:
                    finish()
                    break
                continue
            if line.startswith("### "):
                finish()
                current_category = _clean_heading(line[4:])
                current_parent = ""
                continue
            if line.startswith("#### "):
                finish()
                current_parent = _clean_heading(line[5:])
                # A character document uses this level for the character name,
                # while the other indexes use it for the actual resource.
                current = {
                    "title": current_parent,
                    "images": [],
                    "audio": [],
                    "prompt_lines": [],
                }
                continue
            if line.startswith("##### ") and kind == "character":
                finish()
                child_title = _clean_heading(line[6:])
                current = {
                    "title": f"{current_parent} · {child_title}" if current_parent else child_title,
                    "images": [],
                    "audio": [],
                    "prompt_lines": [],
                }
                continue
            if not current:
                continue
            images, audio = self._media_links(line)
            current["images"].extend(path for path in images if path not in current["images"])
            current["audio"].extend(path for path in audio if path not in current["audio"])
            if raw_line.lstrip().startswith(">"):
                current["prompt_lines"].append(raw_line.lstrip()[1:].strip())
            elif not line and current["prompt_lines"] and current["prompt_lines"][-1] != "":
                current["prompt_lines"].append("")
        finish()
        return references

    def _parse_all(self) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        self._source_files = []
        for kind, kind_label, _ in REFERENCE_DEFINITIONS:
            path = self._configured_source_paths[kind]
            self._source_files.append(path)
            references.extend(self._parse_source(kind, kind_label, path))
        return [item for item in (_normalise_reference(value) for value in references) if item]

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            signatures, source_sha256 = self._source_signature()
            document = self._read()
            if not force and document.get("source_sha256") == source_sha256 and isinstance(document.get("references"), list):
                values = [_normalise_reference(value) for value in document["references"]]
                self._references = [value for value in values if value]
                self._source_files = self._source_paths()
                return
            self._references = self._parse_all()
            self._write({
                "version": VERSION,
                "source_root": str(self.source_root),
                "source_files": [self._source_key(path) for path in self._source_files],
                "source_paths": self.source_paths(),
                "source_signatures": signatures,
                "source_sha256": source_sha256,
                "references": self._references,
            })

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
                item.pop("image_path", None)
                item.pop("audio_path", None)
                item.pop("source_path", None)
                item["image_available"] = image_available
                item["audio_available"] = audio_available
                item["image_url"] = f"/api/prompt/references/{quote(reference['id'], safe='')}/image" if image_available else ""
                item["audio_url"] = f"/api/prompt/references/{quote(reference['id'], safe='')}/audio" if audio_available else ""
                item["media_type"] = "image" if image_available else ("audio" if audio_available else "text")
                result.append(item)
            return result

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
