"""Resource-library indexing and safe first-time initialization."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from rh_cli.errors import RhCliError


RESOURCE_INDEX_FILENAME = "Resources.json"
RESOURCE_INDEX_ENV = "RH_RESOURCE_INDEX_PATH"
RESOURCE_LIBRARY_VERSION = 1
PROMPT_LIBRARY_VERSION = 1
ACTION_LIBRARY_VERSION = 7
REFERENCE_LIBRARY_VERSION = 6

REFERENCE_LIBRARY_DEFINITIONS = (
    ("character", "人物", "character/character.json"),
    ("audio", "音频", "audio/audio.json"),
    ("background", "背景", "background/background.json"),
    ("clothes", "服装", "clothes/clothes.json"),
)


def default_resource_index_path() -> Path:
    configured = os.environ.get(RESOURCE_INDEX_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / "Documents" / "VideoMake" / RESOURCE_INDEX_FILENAME).resolve()


def resource_index_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / RESOURCE_INDEX_FILENAME


def read_resource_index(index_path: str | Path) -> dict[str, Any]:
    path = Path(index_path).expanduser().resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RhCliError("RESOURCE_INDEX_MISSING", f"找不到资源索引：{path}") from exc
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise RhCliError("RESOURCE_INDEX_INVALID", f"资源索引无法读取：{path}") from exc
    if not isinstance(document, dict):
        raise RhCliError("RESOURCE_INDEX_INVALID", f"资源索引必须是 JSON 对象：{path}")
    return document


def _indexed_media_root(index_path: Path, document: dict[str, Any]) -> Path:
    raw_root = str(document.get("media_root") or ".").strip() or "."
    media_root = Path(raw_root).expanduser()
    if not media_root.is_absolute():
        media_root = index_path.parent / media_root
    return media_root.resolve()


def resolve_indexed_source(index_path: str | Path, source_name: str) -> Path:
    index = Path(index_path).expanduser().resolve()
    document = read_resource_index(index)
    sources = document.get("sources")
    if not isinstance(sources, dict):
        raise RhCliError("RESOURCE_SOURCE_MISSING", f"资源索引缺少 sources：{index}")
    raw_source = str(sources.get(source_name) or "").strip()
    if not raw_source:
        raise RhCliError(
            "RESOURCE_SOURCE_MISSING",
            f"资源索引未配置 sources.{source_name}：{index}",
        )
    source = Path(raw_source).expanduser()
    if not source.is_absolute():
        source = _indexed_media_root(index, document) / source
    return source.resolve()


def resolve_tts_root(index_path: str | Path | None = None) -> Path:
    index = Path(index_path).expanduser().resolve() if index_path else default_resource_index_path()
    return resolve_indexed_source(index, "tts")


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def resource_library_document() -> dict[str, Any]:
    sources = {
        "prompt": "prompt/library.json",
        "pose": "pose/pose.json",
        "character": "character/character.json",
        "audio": "audio/audio.json",
        "background": "background/background.json",
        "clothes": "clothes/clothes.json",
        "tts": "tts",
    }
    return {
        "version": RESOURCE_LIBRARY_VERSION,
        "media_root": ".",
        "sources": sources,
        "media_directories": {
            "pose_color": "pose/color",
            "pose_depth": "pose/depth",
            "pose_skeleton": "pose/skeleton",
            "pose_video": "pose/video",
            "pose_video_depth": "pose/video-depth",
            "pose_video_skeleton": "pose/video-skeleton",
            "pose_video_depth_skeleton": "pose/video-depth-skeleton",
            "character": "character",
            "audio": "audio",
            "background": "background",
            "clothes": "clothes",
            "tts": "tts",
        },
        "schemas": {
            "prompt": "blocks",
            "pose": "actions",
            "character": "references",
            "audio": "references",
            "background": "references",
            "clothes": "references",
            "tts": "directory",
        },
    }


def initialize_resource_library(root_value: str | Path) -> Path:
    """Create a complete empty resource library without overwriting data."""
    raw_root = str(root_value or "").strip()
    if not raw_root:
        raise RhCliError("RESOURCE_LIBRARY_PATH_MISSING", "新资源库路径不能为空。")
    root = Path(raw_root).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise RhCliError("RESOURCE_LIBRARY_PATH_INVALID", f"资源库路径不是文件夹：{root}")
    if root.exists() and any(root.iterdir()):
        raise RhCliError("RESOURCE_LIBRARY_NOT_EMPTY", f"新资源库目录必须为空：{root}")

    directories = (
        "prompt",
        "pose/color",
        "pose/depth",
        "pose/skeleton",
        "pose/video",
        "pose/video-depth",
        "pose/video-skeleton",
        "pose/video-depth-skeleton",
        "character",
        "audio",
        "background",
        "clothes",
        "tts",
    )
    for relative in directories:
        (root / relative).mkdir(parents=True, exist_ok=True)

    _write_json(root / RESOURCE_INDEX_FILENAME, resource_library_document())
    _write_json(root / "prompt/library.json", {"version": PROMPT_LIBRARY_VERSION, "blocks": []})
    _write_json(root / "pose/pose.json", {"version": ACTION_LIBRARY_VERSION, "actions": []})
    for kind, label, _ in REFERENCE_LIBRARY_DEFINITIONS:
        _write_json(
            root / f"{kind}/{kind}.json",
            {
                "version": REFERENCE_LIBRARY_VERSION,
                "kind": kind,
                "kind_label": label,
                "references": [],
            },
        )
    return root
