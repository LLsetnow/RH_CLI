from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from rh_cli.errors import RhCliError

from .app import DATA_ROOT, WEB_ROOT, LocalStore, TaskManager, matches_public_output_filters, pick_local_directory_on_macos, pick_local_file_on_macos, public_account, public_dashboard, public_key, public_output_media, public_outputs, public_state, redact_detail, safe_name, workflow_input_catalog
from .action_store import ActionStore
from .prompt_store import PromptStore
from .prompt_writer import AliyunPromptWriter
from .reference_store import ReferenceStore
from .translation import AliyunTranslationClient
from .toolbox import (
    IMAGE_SUFFIXES,
    TOOLBOX_MODES,
    default_codex_image_command,
    expand_command_template,
    find_generated_media,
    normalize_codex_image_resolution,
    normalize_codex_image_size,
    normalize_toolbox_mode,
    process_media,
    run_local_command,
    validate_local_file,
)
from .vision import AliyunVisionClient
from .video_downloader import (
    SOCIAL_PLATFORM_LABELS,
    download_douyin_video,
    download_workflow_social_video,
    normalize_social_video_url,
    social_video_platform,
)


STATIC_ROOT = WEB_ROOT / "static"
LOCAL_PREVIEW_LIMIT = 8 * 1024 * 1024


def output_action_filters(query: str) -> dict[str, str]:
    """Parse the output library's folder and active filters for bulk actions."""
    values = parse_qs(query or "", keep_blank_values=False)
    filters: dict[str, str] = {}
    for key in (
        "project_id",
        "search",
        "type",
        "rating",
        "workflow",
        "tag_case",
        "tag_h",
        "range_start",
        "range_end",
        "account_id",
    ):
        value = str(values.get(key, [""])[0] or "").strip()
        if value:
            filters[key] = value
    filters["has_filters"] = "1" if any(
        key in filters
        for key in (
            "search",
            "type",
            "rating",
            "workflow",
            "tag_case",
            "tag_h",
            "range_start",
            "range_end",
            "account_id",
        )
    ) else "0"
    return filters
PASTED_IMAGE_EXTENSIONS = {
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
PROMPT_MEDIA_IMAGE_EXTENSIONS = {
    ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp",
}
PROMPT_MEDIA_AUDIO_EXTENSIONS = {
    ".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".webm",
}
PROMPT_MEDIA_VIDEO_EXTENSIONS = {
    ".avi", ".flv", ".mkv", ".mov", ".mp4", ".m4v", ".webm", ".wmv",
}
PROMPT_MEDIA_MIME_EXTENSIONS = {
    **PASTED_IMAGE_EXTENSIONS,
    "image/jpg": ".jpg",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/m4a": ".m4a",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "video/avi": ".avi",
    "video/x-flv": ".flv",
    "video/x-matroska": ".mkv",
    "video/quicktime": ".mov",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/x-ms-wmv": ".wmv",
}
PROMPT_MEDIA_LIMIT = 100 * 1024 * 1024


def open_local_directory(path: Path) -> bool:
    """Open a local directory with the operating system's file manager."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            opener = shutil.which("xdg-open")
            if not opener:
                return False
            subprocess.Popen(
                [opener, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except OSError:
        return False
    return True

FOCUS_PAGE_DEFINITIONS = (
    ("workflows", "工作流", "workflows.html", "workflows.js"),
    ("prompt", "提示词工坊", "prompt.html", "prompt.js"),
    ("submit", "任务提交", "index.html", "app.js"),
    ("outputs", "成片", "outputs.html", "outputs.js"),
    ("dashboard", "仪表盘", "dashboard.html", "dashboard.js"),
    ("settings", "设置", "settings.html", "settings.js"),
)


def _focus_remove_element(source: str, *, element_id: str | None = None, class_name: str | None = None) -> str:
    """Remove one balanced HTML element while composing the focus-mode document."""
    if element_id:
        target = re.escape(element_id)
        pattern = rf"<(?P<tag>[A-Za-z][\w:-]*)\b(?=[^>]*\bid=[\"']{target}[\"'])[^>]*>"
    elif class_name:
        target = re.escape(class_name)
        pattern = rf"<(?P<tag>[A-Za-z][\w:-]*)\b(?=[^>]*\bclass=[\"'][^\"']*\b{target}\b[^\"']*[\"'])[^>]*>"
    else:
        return source
    match = re.search(pattern, source, flags=re.IGNORECASE)
    if not match:
        return source
    tag = match.group("tag").lower()
    token_pattern = re.compile(rf"</?{re.escape(tag)}\b[^>]*>", flags=re.IGNORECASE)
    depth = 0
    end = None
    for token in token_pattern.finditer(source, match.start()):
        if token.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                end = token.end()
                break
        elif not token.group(0).rstrip().endswith("/>"):
            depth += 1
    if end is None:
        return source
    return source[:match.start()] + source[end:]


def focus_page_fragment(filename: str) -> str:
    """Extract a page body without its standalone shell/header/scripts for focus mode."""
    source = (STATIC_ROOT / filename).read_text(encoding="utf-8")
    body_match = re.search(r"<body\b[^>]*>(?P<body>.*?)</body\s*>", source, flags=re.IGNORECASE | re.DOTALL)
    if not body_match:
        raise ValueError(f"页面缺少 body：{filename}")
    fragment = body_match.group("body")
    fragment = _focus_remove_element(fragment, class_name="topbar")
    fragment = _focus_remove_element(fragment, class_name="top-nav")
    fragment = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", fragment, flags=re.IGNORECASE | re.DOTALL)
    if filename == "index.html":
        # Settings and workflow configuration are supplied by their dedicated page/modal below.
        fragment = _focus_remove_element(fragment, element_id="settingsModal")
        fragment = _focus_remove_element(fragment, element_id="workflowConfigModal")
    return fragment.strip()


def focus_page_fragments() -> list[dict[str, str]]:
    return [
        {
            "id": page_id,
            "title": title,
            "script": script,
            "html": focus_page_fragment(filename),
        }
        for page_id, title, filename, script in FOCUS_PAGE_DEFINITIONS
    ]


def prompt_image_data_url(path_value: str, roots: list[Path]) -> str:
    """Read a prompt-library image only when it remains inside an allowed media root."""
    raw = str(path_value or "").strip()
    if not raw:
        raise RhCliError("VISION_IMAGE_INVALID", "找不到当前工作台引用的图片。")
    requested = Path(raw).expanduser()
    candidates = [requested.resolve()] if requested.is_absolute() else [
        (Path(root).resolve() / requested).resolve() for root in roots
    ]
    allowed_roots = [Path(root).resolve() for root in roots]
    local_path = next(
        (
            candidate for candidate in candidates
            if any(root == candidate or root in candidate.parents for root in allowed_roots)
            and candidate.is_file()
        ),
        None,
    )
    if local_path is None:
        raise RhCliError("VISION_IMAGE_INVALID", "找不到当前工作台引用的图片，或图片不在媒体库目录内。")
    try:
        size = local_path.stat().st_size
        if size > 10 * 1024 * 1024:
            raise RhCliError("VISION_IMAGE_TOO_LARGE", "用于生成对象定义的图片不能超过 10 MB。")
        raw_image = local_path.read_bytes()
    except OSError as exc:
        raise RhCliError("VISION_IMAGE_INVALID", "读取当前工作台引用图片失败。") from exc
    mime = mimetypes.guess_type(local_path.name)[0] or "image/png"
    if not mime.startswith("image/"):
        raise RhCliError("VISION_IMAGE_INVALID", "对象定义只支持图片引用。")
    return "data:" + mime + ";base64," + base64.b64encode(raw_image).decode("ascii")


def local_file_preview(path_value: str) -> dict[str, object]:
    """Return local media metadata; small images use a data URL, other media stays streamed."""
    path = Path(str(path_value or "")).expanduser().resolve()
    if not path.is_file():
        raise RhCliError("FILE_NOT_FOUND", f"本地文件不存在：{path}")
    stat = path.stat()
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    preview_url = ""
    if mime.startswith("image/") and stat.st_size <= LOCAL_PREVIEW_LIMIT:
        try:
            preview_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        except OSError:
            preview_url = ""
    preview_kind = (
        "image" if mime.startswith("image/")
        else "audio" if mime.startswith("audio/")
        else "video" if mime.startswith("video/")
        else ""
    )
    return {
        "path": str(path),
        "name": path.name,
        "size": stat.st_size,
        "mime": mime,
        "preview_kind": preview_kind,
        "preview_url": preview_url,
    }


def save_pasted_image(body: dict[str, object]) -> dict[str, object]:
    """Persist a clipboard image so the task runner can submit it by local path."""
    mime = str(body.get("mime") or "").strip().lower().split(";", 1)[0]
    encoded = str(body.get("data") or "").strip()
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in header.lower():
            raise RhCliError("INVALID_PASTED_IMAGE", "剪贴板内容不是有效的图片数据。")
        data_mime = header[5:].split(";", 1)[0].strip().lower()
        mime = mime or data_mime
    if mime not in PASTED_IMAGE_EXTENSIONS:
        raise RhCliError("INVALID_PASTED_IMAGE", "仅支持 PNG、JPEG、WebP、GIF、BMP 或 AVIF 图片。")
    if not encoded:
        raise RhCliError("INVALID_PASTED_IMAGE", "剪贴板中没有可保存的图片。")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RhCliError("INVALID_PASTED_IMAGE", "剪贴板内容不是有效的图片数据。") from exc
    if not raw:
        raise RhCliError("INVALID_PASTED_IMAGE", "剪贴板中没有可保存的图片。")
    if len(raw) > LOCAL_PREVIEW_LIMIT:
        raise RhCliError("PASTED_IMAGE_TOO_LARGE", "剪贴板图片不能超过 8MB。")

    name = safe_name(str(body.get("name") or ""), "clipboard-image")
    stem = Path(name).stem or "clipboard-image"
    filename = f"{uuid.uuid4().hex}_{safe_name(stem, 'clipboard-image')}{PASTED_IMAGE_EXTENSIONS[mime]}"
    target_dir = DATA_ROOT / "pasted-inputs"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    temporary = target_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_bytes(raw)
        temporary.replace(target)
    except OSError as exc:
        raise RhCliError("PASTED_IMAGE_SAVE_FAILED", "无法保存剪贴板图片，请重试。") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return local_file_preview(str(target))


def _prompt_media_kind(mime: str, suffix: str) -> str:
    normalized_mime = str(mime or "").strip().lower().split(";", 1)[0]
    if normalized_mime.startswith("image/"):
        return "image"
    if normalized_mime.startswith("audio/"):
        return "audio"
    if normalized_mime.startswith("video/"):
        return "video"
    normalized_suffix = str(suffix or "").strip().lower()
    if normalized_suffix in PROMPT_MEDIA_IMAGE_EXTENSIONS:
        return "image"
    if normalized_suffix in PROMPT_MEDIA_AUDIO_EXTENSIONS and normalized_suffix != ".webm":
        return "audio"
    if normalized_suffix in PROMPT_MEDIA_VIDEO_EXTENSIONS:
        return "video"
    return ""


def save_prompt_media(body: dict[str, object]) -> dict[str, object]:
    """Persist a browser/clipboard media file for use by the prompt workbench."""
    encoded = str(body.get("data_url") or body.get("data") or "").strip()
    mime = str(body.get("mime") or "").strip().lower().split(";", 1)[0]
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in header.lower():
            raise RhCliError("INVALID_PROMPT_MEDIA", "媒体内容不是有效的 Base64 文件。")
        data_mime = header[5:].split(";", 1)[0].strip().lower()
        mime = mime or data_mime
    if not encoded:
        raise RhCliError("INVALID_PROMPT_MEDIA", "没有可保存的媒体内容。")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RhCliError("INVALID_PROMPT_MEDIA", "媒体内容不是有效的 Base64 文件。") from exc
    if not raw:
        raise RhCliError("INVALID_PROMPT_MEDIA", "不能保存空媒体文件。")
    if len(raw) > PROMPT_MEDIA_LIMIT:
        raise RhCliError("PROMPT_MEDIA_TOO_LARGE", "媒体文件不能超过 100MB。")

    original_name = Path(str(body.get("name") or "媒体文件")).name or "媒体文件"
    safe_filename = safe_name(original_name, "prompt-media")
    suffix = Path(safe_filename).suffix.lower()
    if not mime:
        mime = mimetypes.guess_type(safe_filename)[0] or ""
    kind = _prompt_media_kind(mime, suffix)
    if not kind:
        raise RhCliError("INVALID_PROMPT_MEDIA", "仅支持图片、音频或视频文件。")
    if not suffix:
        suffix = PROMPT_MEDIA_MIME_EXTENSIONS.get(mime, "")
    if not suffix:
        raise RhCliError("INVALID_PROMPT_MEDIA", "媒体文件缺少可识别的扩展名。")

    stem = safe_name(Path(safe_filename).stem, "prompt-media")
    filename = f"{uuid.uuid4().hex}_{stem}{suffix}"
    target_dir = DATA_ROOT / "prompt-media"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    temporary = target_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_bytes(raw)
        temporary.replace(target)
    except OSError as exc:
        raise RhCliError("PROMPT_MEDIA_SAVE_FAILED", "无法保存媒体文件，请重试。") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    result = local_file_preview(str(target))
    result["display_name"] = original_name[:240]
    result["media_kind"] = kind
    return result


def _decode_prompt_media(item: object, kind: str, role: str) -> tuple[bytes, str]:
    if not isinstance(item, dict):
        raise RhCliError("INVALID_PROMPT_MEDIA", "素材内容不是有效对象。")
    encoded = str(item.get("data_url") or item.get("data") or "").strip()
    mime = str(item.get("mime") or "").strip().lower().split(";", 1)[0]
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in header.lower():
            raise RhCliError("INVALID_PROMPT_MEDIA", "素材不是有效的 Base64 文件。")
        data_mime = header[5:].split(";", 1)[0].strip().lower()
        mime = mime or data_mime
    if not encoded:
        raise RhCliError("INVALID_PROMPT_MEDIA", "没有可保存的素材内容。")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RhCliError("INVALID_PROMPT_MEDIA", "素材不是有效的 Base64 文件。") from exc
    if not raw:
        raise RhCliError("INVALID_PROMPT_MEDIA", "不能保存空素材。")
    if len(raw) > PROMPT_MEDIA_LIMIT:
        raise RhCliError("PROMPT_MEDIA_TOO_LARGE", "素材不能超过 100MB。")

    filename = safe_name(str(item.get("name") or ""), "resource-media")
    suffix = Path(filename).suffix.lower()
    inferred_suffix = PROMPT_MEDIA_MIME_EXTENSIONS.get(mime, "")
    if suffix not in PROMPT_MEDIA_IMAGE_EXTENSIONS | PROMPT_MEDIA_AUDIO_EXTENSIONS:
        suffix = inferred_suffix
    if kind == "action" or role in {"image", "color", "depth"}:
        allowed = PROMPT_MEDIA_IMAGE_EXTENSIONS
    elif role == "audio":
        allowed = PROMPT_MEDIA_AUDIO_EXTENSIONS
    else:
        raise RhCliError("INVALID_PROMPT_MEDIA", "未知的资源素材类型。")
    if suffix not in allowed:
        raise RhCliError("INVALID_PROMPT_MEDIA", "该资源类型不支持此文件格式。")
    stem = safe_name(Path(filename).stem, "resource-media")
    return raw, stem + suffix


def _prompt_relative(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _write_prompt_media(
    root: Path,
    directory: Path,
    filename: str,
    raw: bytes,
    *,
    replace_relative: str = "",
) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    requested = safe_name(filename, "resource-media")
    target = directory / requested
    replace_target = (root / replace_relative).resolve() if replace_relative else None
    if target.exists() and target.resolve() != replace_target:
        stem = Path(requested).stem
        suffix = Path(requested).suffix
        counter = 2
        while target.exists() and target.resolve() != replace_target:
            target = directory / f"{stem}-{counter}{suffix}"
            counter += 1
    temporary = directory / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_bytes(raw)
        temporary.replace(target)
    except OSError as exc:
        raise RhCliError("PROMPT_MEDIA_SAVE_FAILED", "无法把素材保存到媒体库，请重试。") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return _prompt_relative(root, target)


def prepare_prompt_resource_body(
    body: dict[str, object],
    kind: str,
    root_value: str | Path,
    current: dict[str, object] | None = None,
) -> dict[str, object]:
    """Copy browser-selected resource media into ref and return JSON-ready paths."""
    payload = dict(body)
    media = body.get("media")
    if not isinstance(media, list) or not media:
        payload.pop("media", None)
        return payload
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise RhCliError("MEDIA_LIBRARY_NOT_FOUND", f"媒体库根目录不存在：{root}")
    current = current if isinstance(current, dict) else {}
    decoded: dict[str, tuple[bytes, str]] = {}
    for item in media:
        role = str(item.get("role") or "").strip().lower() if isinstance(item, dict) else ""
        allowed_roles = {"color", "depth", "skeleton"} if kind == "action" else {"audio"} if kind == "audio" else {"image"}
        if role not in allowed_roles:
            raise RhCliError("INVALID_PROMPT_MEDIA", "未知的素材槽位。")
        if role in decoded:
            raise RhCliError("INVALID_PROMPT_MEDIA", "同一个素材槽位只能选择一个文件。")
        decoded[role] = _decode_prompt_media(item, kind, role)

    if kind == "action":
        color_current = str(current.get("color_image_path") or current.get("image_path") or "").strip()
        depth_current = str(current.get("depth_image_path") or "").strip()
        skeleton_current = str(current.get("skeleton_image_path") or "").strip()
        color_item = decoded.get("color")
        depth_item = decoded.get("depth")
        skeleton_item = decoded.get("skeleton")
        pair_source = color_current or depth_current or skeleton_current
        if not pair_source and color_item:
            pair_source = color_item[1]
        if not pair_source and depth_item:
            pair_source = depth_item[1]
        if not pair_source and skeleton_item:
            pair_source = skeleton_item[1]
        pair_stem = Path(pair_source).stem
        for marker in ("_depth", "_skeleton"):
            if pair_stem.endswith(marker):
                pair_stem = pair_stem[: -len(marker)]
                break
        pair_stem = safe_name(pair_stem, "action")
        color_dir = root / "pose" / "color"
        depth_dir = root / "pose" / "depth"
        current_paths = {color_current, depth_current, skeleton_current}
        if not color_current and not depth_current and not skeleton_current:
            counter = 2
            original_stem = pair_stem
            while any(
                candidate.is_file() and _prompt_relative(root, candidate) not in current_paths
                for candidate in (
                    list(color_dir.glob(pair_stem + ".*"))
                    + list(depth_dir.glob(pair_stem + "_depth.*"))
                    + list((root / "pose" / "skeleton").glob(pair_stem + "_skeleton.*"))
                )
            ):
                pair_stem = f"{original_stem}-{counter}"
                counter += 1
        if color_item:
            color_suffix = Path(color_current).suffix or Path(color_item[1]).suffix
            payload["color_image_path"] = _write_prompt_media(
                root, color_dir, pair_stem + color_suffix, color_item[0], replace_relative=color_current,
            )
            payload["image_path"] = payload["color_image_path"]
        if depth_item:
            depth_suffix = Path(depth_current).suffix or Path(depth_item[1]).suffix
            payload["depth_image_path"] = _write_prompt_media(
                root, depth_dir, pair_stem + "_depth" + depth_suffix, depth_item[0], replace_relative=depth_current,
            )
        if skeleton_item:
            skeleton_suffix = Path(skeleton_current).suffix or Path(skeleton_item[1]).suffix
            payload["skeleton_image_path"] = _write_prompt_media(
                root, root / "pose" / "skeleton", pair_stem + "_skeleton" + skeleton_suffix, skeleton_item[0], replace_relative=skeleton_current,
            )
    else:
        media_role = "audio" if kind == "audio" else "image"
        selected = decoded.get(media_role)
        if selected:
            current_path = str(current.get("audio_path" if media_role == "audio" else "image_path") or "").strip()
            relative = _write_prompt_media(root, root / kind, selected[1], selected[0], replace_relative=current_path)
            payload["audio_path" if media_role == "audio" else "image_path"] = relative

    payload.pop("media", None)
    return payload


def _depth_runtime_paths(root: Path) -> tuple[Path, Path]:
    """Locate the VideoMake Core ML depth generator beside the configured ref root."""
    project_roots = []
    if root.name == "ref":
        project_roots.append(root.parent)
    project_roots.append(Path("/Users/apple/Documents/VideoMake"))
    seen: set[Path] = set()
    for project_root in project_roots:
        project_root = project_root.resolve()
        if project_root in seen:
            continue
        seen.add(project_root)
        script = project_root / "tools" / "depth_anything_macos.py"
        python = project_root / ".runtime" / "depth_anything_v2_small_f16" / "venv" / "bin" / "python"
        if script.is_file() and python.is_file():
            return python, script
    raise RhCliError(
        "DEPTH_GENERATOR_UNAVAILABLE",
        "找不到 Depth Anything 运行环境，请确认 VideoMake/tools/depth_anything_macos.py 和 .runtime/depth_anything_v2_small_f16/venv 存在。",
    )


def _skeleton_runtime_paths(root: Path) -> tuple[Path, Path, Path]:
    """Locate the VideoMake DWPose runtime beside the ref root."""
    project_roots = []
    if root.name == "ref":
        project_roots.append(root.parent)
    project_roots.append(Path("/Users/apple/Documents/VideoMake"))
    seen: set[Path] = set()
    for project_root in project_roots:
        project_root = project_root.resolve()
        if project_root in seen:
            continue
        seen.add(project_root)
        script = project_root / "tools" / "pose_skeleton_macos.py"
        runtime = project_root / ".runtime" / "pose_dwpose"
        python = runtime / "venv" / "bin" / "python"
        model = runtime / "checkpoints" / "dw-ll_ucoco_384.onnx"
        detector = runtime / "checkpoints" / "yolox_l.onnx"
        if script.is_file() and python.is_file() and model.is_file() and detector.is_file():
            return python, script, model
    raise RhCliError(
        "SKELETON_GENERATOR_UNAVAILABLE",
        "找不到人体骨骼图运行环境，请确认 VideoMake/tools/pose_skeleton_macos.py、"
        ".runtime/pose_dwpose/venv 和 checkpoints/dw-ll_ucoco_384.onnx、"
        "checkpoints/yolox_l.onnx 存在。",
    )


def generate_prompt_depth(body: dict[str, object], root_value: str | Path) -> dict[str, object]:
    """Generate a temporary depth PNG from one action color image and return it as data."""
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise RhCliError("MEDIA_LIBRARY_NOT_FOUND", f"媒体库根目录不存在：{root}")
    source_path = str(body.get("source_path") or "").strip()
    temporary_parent = DATA_ROOT / "prompt"
    temporary_parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix="depth-generation-", dir=str(temporary_parent)))
    temporary_source: Path | None = None
    try:
        if source_path:
            relative = Path(source_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise RhCliError("INVALID_PROMPT_MEDIA", "原图路径必须位于媒体库根目录内。")
            source = (root / relative).resolve()
            if root not in source.parents or not source.is_file():
                raise RhCliError("FILE_NOT_FOUND", "动作原图不存在，无法生成深度图。")
            temporary_source = temporary_dir / source.name
            temporary_source.write_bytes(source.read_bytes())
            source = temporary_source
        else:
            source_item = body.get("source")
            raw, filename = _decode_prompt_media(source_item, "action", "color")
            temporary_source = temporary_dir / filename
            temporary_source.write_bytes(raw)
            source = temporary_source

        output = temporary_dir / f"{source.stem}_depth.png"
        python, script = _depth_runtime_paths(root)
        completed = subprocess.run(
            [str(python), str(script), str(source), "-o", str(output)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            suffix = f"：{detail[-1][:240]}" if detail else "。"
            raise RhCliError("DEPTH_GENERATION_FAILED", "深度图生成失败" + suffix)
        encoded = base64.b64encode(output.read_bytes()).decode("ascii")
        return {
            "name": output.name,
            "mime": "image/png",
            "data": encoded,
            "data_url": "data:image/png;base64," + encoded,
        }
    except subprocess.TimeoutExpired as exc:
        raise RhCliError("DEPTH_GENERATION_TIMEOUT", "深度图生成超时，请稍后重试。") from exc
    finally:
        try:
            for path in temporary_dir.iterdir():
                path.unlink(missing_ok=True)
            temporary_dir.rmdir()
        except OSError:
            pass


def generate_prompt_skeleton(body: dict[str, object], root_value: str | Path) -> dict[str, object]:
    """Generate a temporary black-background skeleton PNG from one action image."""
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise RhCliError("MEDIA_LIBRARY_NOT_FOUND", f"媒体库根目录不存在：{root}")
    source_path = str(body.get("source_path") or "").strip()
    temporary_parent = DATA_ROOT / "prompt"
    temporary_parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix="skeleton-generation-", dir=str(temporary_parent)))
    temporary_source: Path | None = None
    try:
        if source_path:
            relative = Path(source_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise RhCliError("INVALID_PROMPT_MEDIA", "原图路径必须位于媒体库根目录内。")
            source = (root / relative).resolve()
            if root not in source.parents or not source.is_file():
                raise RhCliError("FILE_NOT_FOUND", "动作原图不存在，无法生成骨骼图。")
            temporary_source = temporary_dir / source.name
            temporary_source.write_bytes(source.read_bytes())
            source = temporary_source
        else:
            source_item = body.get("source")
            raw, filename = _decode_prompt_media(source_item, "action", "color")
            temporary_source = temporary_dir / filename
            temporary_source.write_bytes(raw)
            source = temporary_source

        output = temporary_dir / f"{source.stem}_skeleton.png"
        python, script, model = _skeleton_runtime_paths(root)
        completed = subprocess.run(
            [str(python), str(script), str(source), "-o", str(output), "--model", str(model)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            suffix = f"：{detail[-1][:240]}" if detail else "。"
            raise RhCliError("SKELETON_GENERATION_FAILED", "骨骼图生成失败" + suffix)
        encoded = base64.b64encode(output.read_bytes()).decode("ascii")
        return {
            "name": output.name,
            "mime": "image/png",
            "data": encoded,
            "data_url": "data:image/png;base64," + encoded,
        }
    except subprocess.TimeoutExpired as exc:
        raise RhCliError("SKELETON_GENERATION_TIMEOUT", "骨骼图生成超时，请稍后重试。") from exc
    finally:
        try:
            for path in temporary_dir.iterdir():
                path.unlink(missing_ok=True)
            temporary_dir.rmdir()
        except OSError:
            pass


class ToolboxManager:
    """Run local toolbox jobs and persist them through the normal task store."""

    def __init__(self, store: LocalStore) -> None:
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rh-toolbox")

    @staticmethod
    def _asset_path(value: object, *, label: str, suffixes: set[str]) -> Path:
        if isinstance(value, dict):
            value = value.get("path")
        return validate_local_file(value, label=label, suffixes=suffixes)

    def _new_task(
        self,
        *,
        name: str,
        files: dict[str, str],
        prompts: dict[str, str],
        custom_inputs: dict[str, object],
    ) -> tuple[dict[str, object], Path]:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        created_at = int(time.time() * 1000)
        output_root = Path(self.store.output_dir()).expanduser().resolve()
        task_folder = output_root / task_id
        task_folder.mkdir(parents=True, exist_ok=True)
        task = {
            "id": task_id,
            "created_at": created_at,
            "workflow_path": str(task_folder / ".toolbox-command.json"),
            "workflow_name": str(name or "本地处理").strip() or "本地处理",
            "task_type": "toolbox",
            "remote_workflow_id": "",
            "registered_workflow_id": "",
            "submission_source": "local",
            "files": files,
            "prompts": prompts,
            "custom_inputs": custom_inputs,
            "input_config": {"mode": "toolbox", "items": []},
            "bypassed_nodes": [],
            "random_noise": {},
            "resolution": {},
            "key_id": None,
            "account_id": "",
            "instance_type": "default",
            "output_prefix": "",
            "output_dir": str(output_root),
            "initial_status": "running",
            "initial_progress": "已启动本地处理…",
        }
        self.store.create_task(task)
        self.store.update_task(task_id, started_at=created_at, progress="已启动本地处理…")
        return task, task_folder

    def _update_progress(self, task_id: str, message: str, *, record_log: bool = True) -> None:
        self.store.update_task(task_id, progress=message)
        if record_log:
            self.store.append_stage_log(task_id, "toolbox", message)

    def _log_codex_cli_result(self, task_id: str, result: subprocess.CompletedProcess[str]) -> None:
        stdout = str(result.stdout or "")
        stderr = str(result.stderr or "")
        visible_streams = []
        if stdout.strip():
            visible_streams.append("stdout:\n" + str(redact_detail(stdout)))
        else:
            visible_streams.append("stdout:（空）")
        if stderr.strip():
            visible_streams.append("stderr:\n" + str(redact_detail(stderr)))
        else:
            visible_streams.append("stderr:（空）")
        self.store.append_stage_log(
            task_id,
            "toolbox",
            f"Codex CLI 会话返回（退出码 {result.returncode}）\n" + "\n".join(visible_streams),
            level="error" if result.returncode else "info",
            detail={"returncode": result.returncode, "stdout": stdout, "stderr": stderr},
        )

    @staticmethod
    def _file_output(path: Path, *, node_id: str) -> dict[str, str]:
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return {
            "kind": "file",
            "path": str(path),
            "name": path.name,
            "file_type": path.suffix.lstrip(".") or "file",
            "mime": mime,
            "node_id": node_id,
        }

    def _finish(self, task_id: str, outputs: list[dict[str, str]], started_at: int) -> None:
        completed_at = int(time.time() * 1000)
        self.store.update_task(
            task_id,
            status="completed",
            progress=f"已完成 · {len(outputs)} 个产物",
            completed_at=completed_at,
            outputs_json=json.dumps(outputs, ensure_ascii=False),
            error="",
            error_detail="{}",
            duration=str(max(0, completed_at - started_at)),
        )
        self.store.append_stage_log(task_id, "toolbox", f"本地处理完成，生成 {len(outputs)} 个产物")

    def _fail(self, task_id: str, error: Exception, started_at: int) -> None:
        completed_at = int(time.time() * 1000)
        message = error.message if isinstance(error, RhCliError) else str(error)
        detail = {"code": error.code, "message": message} if isinstance(error, RhCliError) else {"message": message}
        self.store.update_task(
            task_id,
            status="failed",
            progress="本地处理失败",
            completed_at=completed_at,
            error=message,
            error_detail=json.dumps(detail, ensure_ascii=False),
            duration=str(max(0, completed_at - started_at)),
        )
        self.store.append_stage_log(task_id, "toolbox", message, level="error", detail=detail)

    def submit_image(self, body: dict[str, object]) -> dict[str, object]:
        # The concrete CLI stays server-side. The browser only sends the prompt,
        # canvas options, and references, so users never need to know or edit command syntax.
        command = default_codex_image_command()
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise RhCliError("TOOLBOX_PROMPT_MISSING", "请输入图像生成要求。")
        resolution = normalize_codex_image_resolution(body.get("resolution"))
        size = normalize_codex_image_size(body.get("size") or body.get("aspect_ratio"))
        raw_references = body.get("references")
        if not isinstance(raw_references, list):
            raw_references = []
        references = [
            self._asset_path(item, label=f"参考图 {index + 1}", suffixes=IMAGE_SUFFIXES)
            for index, item in enumerate(raw_references)
        ]
        # Validate the template before creating a task. This keeps malformed CLI
        # settings from leaving a task that can only fail asynchronously.
        expand_command_template(
            command,
            {
                "prompt": prompt,
                "output": "/pending/toolbox-result.png",
                "references": [str(path) for path in references],
                "mode": "image",
                "resolution": resolution,
                "size": size,
            },
        )
        task, task_folder = self._new_task(
            name="Codex 图像生成",
            files={f"reference_{index + 1}": str(path) for index, path in enumerate(references)},
            prompts={"prompt": prompt},
            custom_inputs={
                "tool": "codex",
                "engine": "gpt-image",
                "resolution": resolution,
                "aspect_ratio": size,
                "reference_count": len(references),
            },
        )
        self._executor.submit(
            self._run_image,
            task["id"],
            task_folder,
            command,
            prompt,
            references,
            resolution,
            size,
            int(task["created_at"]),
        )
        return self.store.task(str(task["id"])) or task

    def _run_image(
        self,
        task_id: str,
        task_folder: Path,
        command: str,
        prompt: str,
        references: list[Path],
        resolution: str,
        size: str,
        started_at: int,
    ) -> None:
        try:
            output = task_folder / "result.png"
            context = {
                "prompt": prompt,
                "output": str(output),
                "references": [str(path) for path in references],
                "mode": "image",
                "resolution": resolution,
                "size": size,
            }
            self._update_progress(task_id, "正在执行本地 Codex 命令…")
            expand_command_template(command, context)
            run_local_command(command, context, cwd=task_folder, on_result=lambda result: self._log_codex_cli_result(task_id, result))
            generated = find_generated_media(task_folder)
            if not generated:
                raise RhCliError("TOOLBOX_OUTPUT_MISSING", "本地命令已结束，但任务目录中没有找到图片结果。")
            outputs = [self._file_output(path, node_id="codex") for path in generated]
            self._finish(task_id, outputs, started_at)
        except Exception as error:
            self._fail(task_id, error, started_at)

    def submit_media(self, body: dict[str, object]) -> dict[str, object]:
        mode = normalize_toolbox_mode(body.get("mode"))
        source = self._asset_path(body.get("input"), label="输入媒体", suffixes=IMAGE_SUFFIXES | {".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".webm", ".wmv"})
        label = {"depth": "深度图", "skeleton": "骨骼图", "depth_skeleton": "深度+骨骼图"}[mode]
        task, task_folder = self._new_task(
            name=label + ("视频处理" if source.suffix.lower() not in IMAGE_SUFFIXES else "处理"),
            files={"input": str(source)},
            prompts={},
            custom_inputs={"tool": "media_processor", "mode": mode, "input_type": "video" if source.suffix.lower() not in IMAGE_SUFFIXES else "image"},
        )
        self._executor.submit(self._run_media, task["id"], task_folder, source, mode, int(task["created_at"]))
        return self.store.task(str(task["id"])) or task

    def _run_media(self, task_id: str, task_folder: Path, source: Path, mode: str, started_at: int) -> None:
        try:
            last_phase = ""

            def report_progress(message: str) -> None:
                nonlocal last_phase
                phase = str(message).split("（", 1)[0].split(" ·", 1)[0].strip()
                should_log = phase != last_phase
                last_phase = phase
                self._update_progress(task_id, message, record_log=should_log)

            output = process_media(
                mode,
                source,
                task_folder,
                self.store.media_library_root(),
                progress=report_progress,
            )
            outputs = [self._file_output(output, node_id=mode)]
            self._finish(task_id, outputs, started_at)
        except Exception as error:
            self._fail(task_id, error, started_at)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)


class LocalHandler(BaseHTTPRequestHandler):
    server_version = "RHWorkflowDesk/0.1"

    @property
    def state(self) -> tuple[LocalStore, TaskManager]:
        app_server = self.server  # type: ignore[assignment]
        return app_server.store, app_server.manager

    def log_message(self, format: str, *args: object) -> None:
        # Keep API keys and file contents out of the terminal log.
        message = format % args
        print(f"[rh-web] {message}")
        app_server = self.server
        if hasattr(app_server, "append_runtime_log"):
            app_server.append_runtime_log(message)

    def _headers(self, content_type: str = "application/json; charset=utf-8", length: int | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:%s" % self.server.server_port)
        if length is not None:
            self.send_header("Content-Length", str(length))

    def _json(self, status: int, data: object) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._headers(length=len(raw))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 600 * 1024 * 1024:
            raise RhCliError("REQUEST_TOO_LARGE", "请求体过大。")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RhCliError("BAD_JSON", "请求体不是有效 JSON。") from exc
        if not isinstance(value, dict):
            raise RhCliError("BAD_JSON", "请求体必须是 JSON 对象。")
        return value

    def _safe_error(self, exc: Exception) -> dict:
        if isinstance(exc, RhCliError):
            return exc.to_dict()
        return {"code": "SERVER_ERROR", "message": str(exc)}

    def _local_file_preview(self, path_value: str) -> dict[str, object]:
        result = local_file_preview(path_value)
        if result.get("preview_kind") in {"audio", "video"} or (
            result.get("preview_kind") == "image" and not result.get("preview_url")
        ):
            result["preview_url"] = self.server.register_local_preview(str(result["path"]))  # type: ignore[attr-defined]
        return result

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._headers(length=0)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = unquote(parsed_url.path)
        if path in {"/toolbox", "/toolbox/"}:
            self.send_response(HTTPStatus.FOUND)
            self._headers(length=0)
            self.send_header("Location", "/?workspace=codex")
            self.end_headers()
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self._headers(length=0)
            self.end_headers()
            return
        if path == "/api/state":
            store, manager = self.state
            scope = parse_qs(parsed_url.query).get("scope", ["full"])[0]
            state = public_state(store, manager, scope=scope)
            state["settings"]["prompt_library_path"] = str(self.server.prompt_store.library_path)  # type: ignore[attr-defined]
            state["settings"]["action_resources_path"] = str(self.server.action_store.source_path)  # type: ignore[attr-defined]
            state["settings"]["reference_resources_paths"] = self.server.reference_store.source_paths()  # type: ignore[attr-defined]
            state["settings"]["media_library_root"] = store.media_library_root() or str(self.server.reference_store.source_root)  # type: ignore[attr-defined]
            self._json(200, state)
            return
        if path == "/api/outputs":
            store, manager = self.state
            self._json(200, public_outputs(store, manager))
            return
        if path == "/api/projects":
            store, _ = self.state
            self._json(200, {"projects": store.project_folders()})
            return
        if path == "/api/outputs/export/case":
            _, manager = self.state
            filters = output_action_filters(parsed_url.query)
            self._serve_case_outputs_archive(manager, filters=filters)
            return
        if path == "/api/dashboard":
            store, manager = self.state
            try:
                days = int(parse_qs(parsed_url.query).get("days", ["7"])[0])
            except (TypeError, ValueError):
                days = 7
            account_id = parse_qs(parsed_url.query).get("account_id", [""])[0]
            self._json(200, public_dashboard(store, manager, days=days, account_id=account_id))
            return
        if path == "/api/logs":
            store, _ = self.state
            try:
                limit = max(1, min(int(parse_qs(parsed_url.query).get("limit", ["500"])[0]), 1000))
            except (TypeError, ValueError):
                limit = 500
            raw_levels = parse_qs(parsed_url.query).get("levels", [""])[0]
            levels = {item.strip().lower() for item in raw_levels.split(",") if item.strip()}
            logs = store.recent_logs(limit * 2)
            logs.extend(self.server.runtime_logs(limit * 2))  # type: ignore[attr-defined]
            if levels:
                logs = [item for item in logs if str(item.get("level") or "info").lower() in levels]
            logs.sort(key=lambda item: int(item.get("at") or 0))
            self._json(200, {"logs": logs[-limit:]})
            return
        if path == "/api/focus/fragments":
            try:
                self._json(200, {"pages": focus_page_fragments()})
            except (OSError, ValueError) as exc:
                self._json(500, {"code": "FOCUS_FRAGMENT_ERROR", "message": str(exc)})
            return
        if path == "/api/workflows":
            store, _ = self.state
            self._json(200, {"workflows": store.workflows()})
            return
        if path == "/api/workflow-folders":
            store, _ = self.state
            self._json(200, {"folders": store.workflow_folders()})
            return
        if path.startswith("/api/workflows/") and path != "/api/workflows/analyze":
            workflow_id = path.rsplit("/", 1)[-1]
            store, _ = self.state
            try:
                self._json(200, store.workflow_detail(workflow_id))
            except Exception as exc:
                self._json(400 if isinstance(exc, RhCliError) else 500, self._safe_error(exc))
            return
        if path == "/api/prompt/state":
            self._json(200, self.server.prompt_store.snapshot())  # type: ignore[attr-defined]
            return
        if path == "/api/prompt/groups":
            prompt_store = self.server.prompt_store  # type: ignore[attr-defined]
            self._json(200, {"groups": prompt_store.groups(), "folders": prompt_store.prompt_group_folders()})
            return
        if path == "/api/prompt/group-folders":
            self._json(200, {"folders": self.server.prompt_store.prompt_group_folders()})  # type: ignore[attr-defined]
            return
        if path == "/api/prompt/actions":
            action_store = self.server.action_store  # type: ignore[attr-defined]
            # The configured pose JSON file is the source of truth; refresh reads it directly.
            # while the app is open without requiring a server restart.
            action_store.refresh()
            self._json(200, {"actions": action_store.public_actions(), "source_status": action_store.source_status()})
            return
        if path == "/api/prompt/actions/status":
            action_store = self.server.action_store  # type: ignore[attr-defined]
            action_store.refresh()
            self._json(200, action_store.source_status())
            return
        if path == "/api/prompt/references":
            reference_store = self.server.reference_store  # type: ignore[attr-defined]
            reference_store.refresh()
            self._json(200, {
                "references": reference_store.public_references(),
                "source_status": reference_store.source_status(),
                "kind_counts": reference_store.kind_counts(),
            })
            return
        if path == "/api/prompt/references/status":
            reference_store = self.server.reference_store  # type: ignore[attr-defined]
            reference_store.refresh()
            self._json(200, reference_store.source_status())
            return
        if path.startswith("/api/local-preview/"):
            self._serve_local_preview(path.rsplit("/", 1)[-1])
            return
        if path.startswith("/api/prompt/actions/") and path.endswith("/depth-path"):
            self._serve_action_path(path, "depth")
            return
        if path.startswith("/api/prompt/actions/") and path.endswith("/skeleton-path"):
            self._serve_action_path(path, "skeleton")
            return
        if path.startswith("/api/prompt/actions/") and path.endswith("/image-path"):
            self._serve_action_path(path, "color")
            return
        if path.startswith("/api/prompt/actions/") and path.endswith("/depth"):
            self._serve_action_image(path, "depth")
            return
        if path.startswith("/api/prompt/actions/") and path.endswith("/skeleton"):
            self._serve_action_image(path, "skeleton")
            return
        if path.startswith("/api/prompt/actions/") and path.endswith("/image"):
            self._serve_action_image(path, "color")
            return
        if path.startswith("/api/prompt/references/") and path.endswith("/image"):
            self._serve_reference_media(path, "image")
            return
        if path.startswith("/api/prompt/references/") and path.endswith("/image-path"):
            self._serve_reference_path(path, "image")
            return
        if path.startswith("/api/prompt/references/") and path.endswith("/audio-path"):
            self._serve_reference_path(path, "audio")
            return
        if path.startswith("/api/prompt/references/") and path.endswith("/audio"):
            self._serve_reference_media(path, "audio")
            return
        if path.startswith("/api/tasks/") and "/output/" in path:
            self._serve_output(path)
            return
        if path.startswith("/api/tasks/") and path.endswith("/load"):
            task_id = path.split("/")[3]
            store, _ = self.state
            try:
                self._json(200, store.load_task_workflow(task_id))
            except Exception as exc:
                self._json(400 if isinstance(exc, RhCliError) else 500, self._safe_error(exc))
            return
        if path.startswith("/api/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            store, _ = self.state
            task = store.task(task_id)
            if not task:
                self._json(404, {"code": "TASK_NOT_FOUND", "message": "找不到任务"})
            else:
                self._json(200, task)
            return
        if path == "/api/health":
            self._json(200, {"ok": True, "data_dir": str(DATA_ROOT)})
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/preview-file":
                body = self._body()
                self._json(200, self._local_file_preview(str(body.get("path") or "")))
                return
            if path == "/api/pick-file":
                selected = pick_local_file_on_macos()
                self._json(200, self._local_file_preview(str(selected)))
                return
            if path == "/api/open-file-folder":
                body = self._body()
                file_path = Path(str(body.get("path") or "")).expanduser().resolve()
                if not file_path.is_file():
                    raise RhCliError("FILE_NOT_FOUND", f"本地文件不存在：{file_path}")
                if not open_local_directory(file_path.parent):
                    raise RhCliError("OPEN_FOLDER_UNAVAILABLE", "当前系统无法打开文件所在文件夹")
                self._json(200, {"opened": True, "message": "已打开文件所在文件夹"})
                return
            if path == "/api/pick-douyin-cookie":
                selected = pick_local_file_on_macos("选择社交视频 Cookie 文件")
                self._json(200, {"path": str(selected), "name": selected.name})
                return
            if path == "/api/download-douyin":
                body = self._body()
                store, _ = self.state
                downloaded = download_douyin_video(
                    str(body.get("url") or ""),
                    store.douyin_cookie_path(),
                    DATA_ROOT,
                )
                self._json(200, self._local_file_preview(str(downloaded)))
                return
            if path == "/api/download-social-video":
                body = self._body()
                store, _ = self.state
                raw_url = str(body.get("url") or "")
                normalized_url = normalize_social_video_url(raw_url)
                platform = social_video_platform(normalized_url)
                downloaded = download_workflow_social_video(
                    normalized_url,
                    DATA_ROOT,
                    store.douyin_cookie_path(),
                )
                preview = self._local_file_preview(str(downloaded))
                preview.update({
                    "platform": platform,
                    "platform_label": SOCIAL_PLATFORM_LABELS[platform],
                })
                self._json(200, preview)
                return
            if path == "/api/paste-file":
                self._json(200, save_pasted_image(self._body()))
                return
            if path == "/api/prompt/media":
                saved = save_prompt_media(self._body())
                preview = self._local_file_preview(str(saved["path"]))
                preview.update({"display_name": saved.get("display_name"), "media_kind": saved.get("media_kind")})
                self._json(200, preview)
                return
            if path == "/api/prompt/translate":
                body = self._body()
                store, _ = self.state
                access_key_id, access_key_secret = store.aliyun_translation_credentials()
                result = AliyunTranslationClient(access_key_id, access_key_secret).translate(str(body.get("text") or ""))
                self._json(200, result)
                return
            if path == "/api/prompt/ai-prompt":
                body = self._body()
                store, _ = self.state
                result = AliyunPromptWriter(store.aliyun_vision_api_key()).write(
                    str(body.get("context") or ""),
                    str(body.get("question") or ""),
                )
                self._json(200, result)
                return
            if path == "/api/prompt/vision":
                body = self._body()
                image = str(body.get("image") or "").strip()
                kind = str(body.get("kind") or "").strip()
                if kind not in {"action", "character", "background", "clothes"}:
                    raise RhCliError("VISION_KIND_INVALID", "当前卡片类型不支持图片识图。")
                if not image:
                    image_path = str(body.get("image_path") or "").strip()
                    if not image_path:
                        raise RhCliError("VISION_IMAGE_INVALID", "请先选择图片。")
                    resource_store = self.server.action_store if kind == "action" else self.server.reference_store  # type: ignore[attr-defined]
                    root = Path(resource_store.source_root).resolve()
                    relative = Path(image_path)
                    if relative.is_absolute() or ".." in relative.parts:
                        raise RhCliError("VISION_IMAGE_INVALID", "图片路径必须位于媒体库根目录内。")
                    local_path = (root / relative).resolve()
                    if root not in local_path.parents or not local_path.is_file():
                        raise RhCliError("VISION_IMAGE_INVALID", "找不到当前卡片的图片。")
                    raw = local_path.read_bytes()
                    mime = mimetypes.guess_type(local_path.name)[0] or "image/png"
                    image = "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii")
                store, _ = self.state
                result = AliyunVisionClient(store.aliyun_vision_api_key()).recognize(image, kind)
                self._json(200, {"recognition": result})
                return
            if path == "/api/prompt/actions/generate-depth":
                action_store = self.server.action_store  # type: ignore[attr-defined]
                self._json(200, generate_prompt_depth(self._body(), action_store.source_root))
                return
            if path == "/api/prompt/actions/generate-skeleton":
                action_store = self.server.action_store  # type: ignore[attr-defined]
                self._json(200, generate_prompt_skeleton(self._body(), action_store.source_root))
                return
            if path == "/api/toolbox/image":
                self._json(202, {"task": self.server.toolbox.submit_image(self._body())})  # type: ignore[attr-defined]
                return
            if path == "/api/toolbox/media":
                self._json(202, {"task": self.server.toolbox.submit_media(self._body())})  # type: ignore[attr-defined]
                return
            if path == "/api/prompt/actions":
                action_store = self.server.action_store  # type: ignore[attr-defined]
                body = self._body()
                action = action_store.add_action(
                    prepare_prompt_resource_body(body, "action", action_store.source_root)
                )
                self._json(201, {"action": next(item for item in action_store.public_actions() if item["id"] == action["id"])})
                return
            if path == "/api/prompt/references":
                body = self._body()
                reference_store = self.server.reference_store  # type: ignore[attr-defined]
                kind = str(body.get("kind") or "")
                reference = reference_store.add_reference(
                    kind,
                    prepare_prompt_resource_body(body, kind, reference_store.source_root),
                )
                self._json(201, {"reference": next(item for item in reference_store.public_references() if item["id"] == reference["id"])})
                return
            if path == "/api/telegram/test":
                _, manager = self.state
                self._json(200, manager.test_telegram_connection())
                return
            if path.startswith("/api/tasks/") and path.endswith("/telegram"):
                task_id = path.split("/")[3]
                _, manager = self.state
                self._json(200, manager.upload_task_to_telegram(task_id, self._body().get("output_index")))
                return
            if path.startswith("/api/tasks/") and path.endswith("/open-folder"):
                parts = path.split("/")
                if len(parts) != 5 or parts[3] == "":
                    self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
                    return
                task_id = parts[3]
                store, _ = self.state
                task = store.task(task_id)
                if not task:
                    raise RhCliError("TASK_NOT_FOUND", "找不到任务")
                folder = LocalStore.task_output_path(task)
                if not folder.is_dir():
                    raise RhCliError("OUTPUT_FOLDER_NOT_FOUND", "媒体所在文件夹不存在")
                if not open_local_directory(folder):
                    raise RhCliError("OPEN_FOLDER_UNAVAILABLE", "当前系统无法打开媒体所在文件夹")
                self._json(200, {"opened": True, "message": "已打开媒体所在文件夹"})
                return
            if path == "/api/dashboard/refresh-balances":
                _, manager = self.state
                self._json(200, manager.refresh_balances())
                return
            if path == "/api/pick-action-resources":
                selected = pick_local_file_on_macos("选择动作库 JSON 文件")
                self._json(200, {"path": str(selected), "name": selected.name})
                return
            if path == "/api/pick-prompt-resource":
                body = self._body()
                labels = {
                    "library": "基础积木 JSON 文件",
                    "action": "动作库 JSON 文件",
                    "character": "人物库 JSON 文件",
                    "audio": "音频库 JSON 文件",
                    "background": "背景库 JSON 文件",
                    "clothes": "服装库 JSON 文件",
                }
                kind = str(body.get("kind") or "").strip()
                if kind not in labels:
                    raise RhCliError("INVALID_PROMPT_RESOURCE_KIND", "未知的提示词资源类型。")
                selected = pick_local_file_on_macos("选择" + labels[kind])
                self._json(200, {"path": str(selected), "name": selected.name, "kind": kind})
                return
            if path == "/api/pick-directory":
                selected = pick_local_directory_on_macos()
                self._json(200, {"path": str(selected) if selected else ""})
                return
            if path == "/api/pick-media-root":
                selected = pick_local_directory_on_macos("选择媒体库 ref 文件夹")
                self._json(200, {"path": str(selected) if selected else ""})
                return
            if path == "/api/workflows":
                body = self._body()
                content = body.get("content")
                if not isinstance(content, str):
                    raise RhCliError("INVALID_WORKFLOW", "缺少工作流 JSON 内容。")
                store, _ = self.state
                prompt_group = (
                    body.get("prompt_group")
                    if isinstance(body.get("prompt_group"), dict)
                    else self.server.prompt_store.task_group_snapshot()  # type: ignore[attr-defined]
                    if body.get("include_current_prompt_group")
                    else self.server.prompt_store.get_group(str(body.get("prompt_group_id") or ""))  # type: ignore[attr-defined]
                )
                workflow_id, _, _ = store.save_workflow(
                    str(body.get("filename") or "workflow.json"),
                    content,
                    account_id=str(body.get("account_id") or ""),
                    remote_workflow_id=str(body.get("remote_workflow_id") or ""),
                    source_dir=str(body.get("source_dir") or ""),
                    input_config=body.get("input_config") if isinstance(body.get("input_config"), dict) else None,
                    input_defaults=body.get("input_defaults") if isinstance(body.get("input_defaults"), list) else None,
                    prompt_group=prompt_group,
                )
                self._json(201, store.workflow_detail(workflow_id))
                return
            if path == "/api/workflow-folders":
                store, _ = self.state
                folder = store.create_workflow_folder(str(self._body().get("name") or ""))
                self._json(201, {"folder": folder})
                return
            if path == "/api/projects":
                store, _ = self.state
                project = store.create_project_folder(str(self._body().get("name") or ""))
                self._json(201, {"project": project})
                return
            if path == "/api/workflows/analyze":
                body = self._body()
                content = body.get("content")
                if not isinstance(content, str):
                    raise RhCliError("INVALID_WORKFLOW", "缺少工作流 JSON 内容。")
                store, _ = self.state
                workflow_id, _, analysis = store.save_workflow(
                    str(body.get("filename") or "workflow.json"),
                    content,
                    account_id=str(body.get("account_id") or ""),
                    remote_workflow_id=str(body.get("remote_workflow_id") or ""),
                    source_dir=str(body.get("source_dir") or ""),
                    register=False,
                )
                saved_workflow = json.loads(content)
                analysis["input_catalog"] = workflow_input_catalog(saved_workflow, analysis)
                saved_metadata = saved_workflow.get("__rh_meta__") if isinstance(saved_workflow, dict) else {}
                saved_metadata = saved_metadata if isinstance(saved_metadata, dict) else {}
                self._json(
                    200,
                    {
                        "workflow_id": workflow_id,
                        # The on-disk path is ID-addressed; keep the original
                        # user-facing filename in the response instead of
                        # leaking the storage key into the editor state.
                        "filename": str(body.get("filename") or "workflow.json"),
                        "analysis": analysis,
                        "remote_workflow_id": analysis.get("remote_workflow_id", ""),
                        "account_id": str(saved_metadata.get("accountId") or saved_metadata.get("account_id") or ""),
                    },
                )
                return
            if path == "/api/prompt/migrate":
                body = self._body()
                snapshot = self.server.prompt_store.migrate(  # type: ignore[attr-defined]
                    body.get("customBlocks"), body.get("stage")
                )
                self._json(200, snapshot)
                return
            if path == "/api/prompt/library":
                block = self.server.prompt_store.add_block(self._body())  # type: ignore[attr-defined]
                self._json(201, {"block": block})
                return
            if path == "/api/prompt/groups":
                body = self._body()
                group = self.server.prompt_store.save_group(  # type: ignore[attr-defined]
                    str(body.get("name") or ""),
                    body.get("items"),
                    str(body.get("id") or "") or None,
                    body.get("folder_id") if "folder_id" in body else None,
                )
                self._json(200, {"group": group})
                return
            if path == "/api/prompt/group-folders":
                folder = self.server.prompt_store.create_prompt_group_folder(str(self._body().get("name") or ""))  # type: ignore[attr-defined]
                self._json(201, {"folder": folder})
                return
            if path == "/api/keys":
                body = self._body()
                _, manager = self.state
                record = manager.add_key(str(body.get("name") or ""), str(body.get("site") or "ai"), str(body.get("api_key") or ""))
                self._json(200, {"key": record})
                return
            if path == "/api/accounts":
                body = self._body()
                store, _ = self.state
                account = store.add_account(str(body.get("name") or ""), str(body.get("site") or "ai"))
                self._json(201, {"account": public_account(account)})
                return
            if path.startswith("/api/keys/") and path.endswith("/check"):
                key_id = path.split("/")[3]
                _, manager = self.state
                self._json(200, {"key": manager.check_key(key_id)})
                return
            if path.startswith("/api/keys/") and path.endswith("/balance"):
                key_id = path.split("/")[3]
                _, manager = self.state
                self._json(200, {"key": manager.refresh_balance(key_id)})
                return
            if path == "/api/tasks":
                body = self._body()
                _, manager = self.state
                prompt_store = self.server.prompt_store  # type: ignore[attr-defined]
                if "prompt_group" in body:
                    prompt_group = body.get("prompt_group")
                    if not isinstance(prompt_group, dict):
                        raise RhCliError("INVALID_PROMPT_GROUP", "任务提交的 prompt_group 必须是对象。")
                elif str(body.get("prompt_group_id") or "").strip():
                    prompt_group_id = str(body.get("prompt_group_id") or "").strip()
                    prompt_group = prompt_store.get_group(prompt_group_id)
                    if not prompt_group:
                        raise RhCliError("PROMPT_GROUP_NOT_FOUND", f"找不到提示词组：{prompt_group_id}")
                else:
                    prompt_group = prompt_store.task_group_snapshot()
                bypassed_nodes = body.get("bypassed_nodes")
                if not isinstance(bypassed_nodes, (list, dict)):
                    bypassed_nodes = body.get("bypassed_inputs") if isinstance(body.get("bypassed_inputs"), (list, dict)) else None
                project = body.get("project") if isinstance(body.get("project"), dict) else None
                if project is None and any(key in body for key in ("project_id", "project_name", "project_path")):
                    project = {
                        "project_id": body.get("project_id"),
                        "project_name": body.get("project_name"),
                        "project_path": body.get("project_path"),
                    }
                task = manager.submit_task(
                    str(body.get("workflow_id") or ""),
                    body.get("files") if isinstance(body.get("files"), dict) else {},
                    body.get("prompts") if isinstance(body.get("prompts"), dict) else {},
                    str(body.get("key_id") or "") or None,
                    str(body.get("output_dir") or "") or None,
                    output_prefix=str(body.get("output_prefix") or "") or None,
                    remote_workflow_id=str(body.get("remote_workflow_id") or "") or None,
                    random_noise=body.get("random_noise") if isinstance(body.get("random_noise"), dict) else {},
                    resolution=body.get("resolution") if isinstance(body.get("resolution"), dict) else {},
                    bypassed_nodes=bypassed_nodes,
                    workflow_data=body.get("workflow") if isinstance(body.get("workflow"), dict) else None,
                    workflow_name=str(body.get("workflow_name") or "") or None,
                    instance_type=str(body.get("instance_type") or "default"),
                    workflow_account_id=str(body.get("workflow_account_id") or "") or None,
                    workflow_input_config=body.get("workflow_input_config") if isinstance(body.get("workflow_input_config"), dict) else None,
                    custom_inputs=body.get("custom_inputs") if isinstance(body.get("custom_inputs"), dict) else {},
                    prompt_group=prompt_group,
                    project=project,
                )
                self._json(202, {"task": task})
                return
            if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                task_id = path.split("/")[3]
                _, manager = self.state
                self._json(200, {"task": manager.cancel_task(task_id)})
                return
            self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
        except Exception as exc:
            self._json(400 if isinstance(exc, RhCliError) else 500, self._safe_error(exc))

    def do_PATCH(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path.startswith("/api/prompt/library/") and path.endswith("/rating"):
                block_id = path.split("/")[4]
                block = self.server.prompt_store.update_block_rating(block_id, self._body().get("rating"))  # type: ignore[attr-defined]
                self._json(200, {"block": block})
                return
            if path.startswith("/api/prompt/actions/") and path.endswith("/rating"):
                action_id = path.split("/")[4]
                action_store = self.server.action_store  # type: ignore[attr-defined]
                action_store.refresh()
                action = action_store.update_action_rating(action_id, self._body().get("rating"))
                public_action = next(item for item in action_store.public_actions() if item["id"] == action["id"])
                self._json(200, {"action": public_action})
                return
            if path.startswith("/api/prompt/references/") and path.endswith("/rating"):
                reference_id = path.split("/")[4]
                reference_store = self.server.reference_store  # type: ignore[attr-defined]
                reference_store.refresh()
                reference = reference_store.update_reference_rating(reference_id, self._body().get("rating"))
                public_reference = next(item for item in reference_store.public_references() if item["id"] == reference["id"])
                self._json(200, {"reference": public_reference})
                return
            if path.startswith("/api/tasks/") and path.endswith("/project"):
                parts = path.split("/")
                if len(parts) != 5 or not parts[3]:
                    self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
                    return
                task_id = parts[3]
                body = self._body()
                project = body.get("project") if isinstance(body.get("project"), dict) else body
                store, _ = self.state
                self._json(200, {"task": store.set_task_project(task_id, project)})
                return
            if path.startswith("/api/projects/"):
                project_id = path.rsplit("/", 1)[-1]
                store, _ = self.state
                project = store.rename_project_folder(project_id, str(self._body().get("name") or ""))
                self._json(200, {"project": project})
                return
            if path.startswith("/api/tasks/") and "/outputs/" in path:
                parts = path.split("/")
                if len(parts) != 6 or parts[4] != "outputs":
                    self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
                    return
                task_id = parts[3]
                body = self._body()
                try:
                    output_index = int(parts[5])
                except ValueError as exc:
                    error_code = "INVALID_OUTPUT_TAGS" if "tags" in body else "INVALID_OUTPUT_RATING"
                    raise RhCliError(error_code, "产物索引无效。") from exc
                store, _ = self.state
                if "tags" in body:
                    output = store.update_output_tags(task_id, output_index, body.get("tags"))
                else:
                    output = store.update_output_rating(task_id, output_index, body.get("rating"))
                self._json(200, {"output": output})
                return
            if path == "/api/settings":
                body = self._body()
                store, manager = self.state
                result = {
                    "output_dir": store.output_dir(),
                    "douyin_cookie_path": store.douyin_cookie_path(),
                    "personal_capacity": store.personal_capacity(),
                    "api_key_strategy": store.api_key_strategy(),
                    "pose_media_import_type": store.pose_media_import_type(),
                    "current_account_id": store.current_account_id(),
                    "prompt_library_path": str(self.server.prompt_store.library_path),  # type: ignore[attr-defined]
                    "action_resources_path": str(self.server.action_store.source_path),  # type: ignore[attr-defined]
                    "reference_resources_paths": self.server.reference_store.source_paths(),  # type: ignore[attr-defined]
                    "media_library_root": store.media_library_root() or str(self.server.reference_store.source_root),  # type: ignore[attr-defined]
                    "aliyun_translation": store.aliyun_translation_settings(),
                    "aliyun_vision": store.aliyun_vision_settings(),
                    "telegram": store.telegram_settings(),
                }
                if "current_account_id" in body:
                    result["current_account_id"] = store.set_current_account(str(body.get("current_account_id") or ""))["id"]
                    manager._wake.set()
                if "output_dir" in body:
                    result["output_dir"] = store.set_output_dir(str(body.get("output_dir") or ""))
                if "douyin_cookie_path" in body:
                    result["douyin_cookie_path"] = store.set_douyin_cookie_path(str(body.get("douyin_cookie_path") or ""))
                if "personal_capacity" in body:
                    result["personal_capacity"] = store.set_personal_capacity(body.get("personal_capacity"))
                    manager._wake.set()
                if "api_key_strategy" in body:
                    result["api_key_strategy"] = store.set_api_key_strategy(body.get("api_key_strategy"))
                    manager._wake.set()
                if "pose_media_import_type" in body:
                    result["pose_media_import_type"] = store.set_pose_media_import_type(body.get("pose_media_import_type"))
                if "prompt_library_path" in body:
                    prompt_path = store.set_prompt_library_path(str(body.get("prompt_library_path") or ""))
                    self.server.prompt_store.set_library_path(prompt_path)  # type: ignore[attr-defined]
                    result["prompt_library_path"] = str(self.server.prompt_store.library_path)  # type: ignore[attr-defined]
                if "media_library_root" in body:
                    media_root = store.set_media_library_root(str(body.get("media_library_root") or ""))
                    self.server.action_store.set_source_root(media_root)  # type: ignore[attr-defined]
                    self.server.reference_store.set_source_root(media_root)  # type: ignore[attr-defined]
                    indexed_library_path = store.prompt_library_path()
                    if Path(indexed_library_path).is_file():
                        self.server.prompt_store.set_library_path(indexed_library_path)  # type: ignore[attr-defined]
                    result["media_library_root"] = media_root
                    result["action_resources_path"] = str(self.server.action_store.source_path)  # type: ignore[attr-defined]
                    result["reference_resources_paths"] = self.server.reference_store.source_paths()  # type: ignore[attr-defined]
                if "action_resources_path" in body and "media_library_root" not in body:
                    action_path = store.set_action_resources_path(str(body.get("action_resources_path") or ""))
                    self.server.action_store.set_source_path(action_path)  # type: ignore[attr-defined]
                    result["action_resources_path"] = str(self.server.action_store.source_path)  # type: ignore[attr-defined]
                if "reference_resources_paths" in body and "media_library_root" not in body:
                    reference_paths = store.set_reference_resources_paths(body.get("reference_resources_paths"))
                    self.server.reference_store.set_source_paths(reference_paths)  # type: ignore[attr-defined]
                    result["reference_resources_paths"] = self.server.reference_store.source_paths()  # type: ignore[attr-defined]
                if "aliyun_translation_access_key_id" in body or "aliyun_translation_access_key_secret" in body:
                    result["aliyun_translation"] = store.set_aliyun_translation_credentials(
                        str(body.get("aliyun_translation_access_key_id") or ""),
                        str(body.get("aliyun_translation_access_key_secret") or ""),
                    )
                if "aliyun_vision_api_key" in body:
                    result["aliyun_vision"] = store.set_aliyun_vision_api_key(str(body.get("aliyun_vision_api_key") or ""))
                if body.get("telegram_clear"):
                    result["telegram"] = store.clear_telegram_settings()
                elif any(key in body for key in ("telegram_bot_token", "telegram_chat_id", "telegram_enabled")):
                    result["telegram"] = store.set_telegram_settings(
                        str(body.get("telegram_bot_token") or ""),
                        str(body.get("telegram_chat_id") or ""),
                        body.get("telegram_enabled"),
                    )
                if any(key in body for key in ("telegram_inbound_workflow_id", "telegram_inbound_folder_id", "telegram_inbound_mode", "telegram_inbound_enabled")):
                    result["telegram"] = store.set_telegram_inbound_settings(
                        str(body.get("telegram_inbound_workflow_id") or ""),
                        body.get("telegram_inbound_enabled"),
                        mode=body.get("telegram_inbound_mode"),
                        folder_id=str(body.get("telegram_inbound_folder_id") or ""),
                    )
                if any(key in body for key in ("telegram_video_inbound_workflow_id", "telegram_video_inbound_enabled")):
                    result["telegram"] = store.set_telegram_video_inbound_settings(
                        str(body.get("telegram_video_inbound_workflow_id") or ""),
                        body.get("telegram_video_inbound_enabled"),
                    )
                self._json(200, result)
                return
            if path.startswith("/api/workflow-folders/"):
                folder_id = path.rsplit("/", 1)[-1]
                store, _ = self.state
                folder = store.rename_workflow_folder(folder_id, str(self._body().get("name") or ""))
                self._json(200, {"folder": folder})
                return
            if path.startswith("/api/prompt/group-folders/"):
                folder_id = path.rsplit("/", 1)[-1]
                folder = self.server.prompt_store.rename_prompt_group_folder(  # type: ignore[attr-defined]
                    folder_id, str(self._body().get("name") or "")
                )
                self._json(200, {"folder": folder})
                return
            if path.startswith("/api/workflows/"):
                workflow_id = path.rsplit("/", 1)[-1]
                store, _ = self.state
                changes = self._body()
                if path.endswith("/replace"):
                    old_workflow_id = path.split("/")[-2]
                    prompt_group = (
                        changes.get("prompt_group")
                        if isinstance(changes.get("prompt_group"), dict)
                        else self.server.prompt_store.task_group_snapshot()  # type: ignore[attr-defined]
                        if changes.get("include_current_prompt_group")
                        else self.server.prompt_store.get_group(str(changes.get("prompt_group_id") or ""))  # type: ignore[attr-defined]
                    )
                    workflow = store.replace_workflow(
                        old_workflow_id,
                        str(changes.get("name") or "workflow.json"),
                        str(changes.get("content") or ""),
                        account_id=str(changes.get("account_id") or ""),
                        remote_workflow_id=str(changes.get("remote_workflow_id") or ""),
                        source_dir=str(changes.get("source_dir") or ""),
                        input_config=changes.get("input_config") if isinstance(changes.get("input_config"), dict) else None,
                        input_defaults=changes.get("input_defaults") if isinstance(changes.get("input_defaults"), list) else None,
                        prompt_group=prompt_group,
                    )
                    self._json(200, workflow)
                    return
                unsupported_direct_changes = set(changes) - {"folder_id"}
                if unsupported_direct_changes:
                    raise RhCliError(
                        "WORKFLOW_SNAPSHOT_REQUIRED",
                        "工作流库内容只能通过临时快照保存为新工作流包；请使用 /replace。",
                    )
                if "prompt_group_id" in changes:
                    changes["prompt_group"] = self.server.prompt_store.get_group(  # type: ignore[attr-defined]
                        str(changes.get("prompt_group_id") or "")
                    )
                if "name" in changes and set(changes).issubset({"name"}):
                    workflow = store.rename_workflow(workflow_id, str(changes.get("name") or ""))
                else:
                    workflow = store.update_workflow(workflow_id, changes)
                self._json(200, {"workflow": workflow})
                return
            if path.startswith("/api/accounts/"):
                account_id = path.rsplit("/", 1)[-1]
                store, _ = self.state
                self._json(200, {"account": public_account(store.update_account(account_id, self._body()))})
                return
            self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
        except Exception as exc:
            self._json(400 if isinstance(exc, RhCliError) else 500, self._safe_error(exc))

    def do_PUT(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path.startswith("/api/prompt/actions/"):
                action_id = path.rsplit("/", 1)[-1]
                action_store = self.server.action_store  # type: ignore[attr-defined]
                body = self._body()
                current = next((item for item in action_store.actions() if item["id"] == action_id), None)
                if current is None:
                    action_store.update_action(action_id, body)
                action = action_store.update_action(
                    action_id,
                    prepare_prompt_resource_body(body, "action", action_store.source_root, current),
                )
                self._json(200, {"action": next(item for item in action_store.public_actions() if item["id"] == action["id"])})
                return
            if path.startswith("/api/prompt/references/"):
                reference_id = path.rsplit("/", 1)[-1]
                reference_store = self.server.reference_store  # type: ignore[attr-defined]
                body = self._body()
                current = next((item for item in reference_store.references() if item["id"] == reference_id), None)
                kind = str((current or {}).get("kind") or body.get("kind") or "")
                if current is None:
                    reference_store.update_reference(reference_id, body)
                reference = reference_store.update_reference(
                    reference_id,
                    prepare_prompt_resource_body(body, kind, reference_store.source_root, current),
                )
                self._json(200, {"reference": next(item for item in reference_store.public_references() if item["id"] == reference["id"])})
                return
            if path.startswith("/api/prompt/library/"):
                block_id = path.rsplit("/", 1)[-1]
                block = self.server.prompt_store.update_block(block_id, self._body())  # type: ignore[attr-defined]
                self._json(200, {"block": block})
                return
            if path == "/api/prompt/state":
                body = self._body()
                document = self.server.prompt_store.save_state(body.get("items"))  # type: ignore[attr-defined]
                self._json(200, {"state": document})
                return
            self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
        except Exception as exc:
            self._json(400 if isinstance(exc, RhCliError) else 500, self._safe_error(exc))

    def do_DELETE(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path.startswith("/api/prompt/actions/"):
                action_store = self.server.action_store  # type: ignore[attr-defined]
                action_store.delete_action(path.rsplit("/", 1)[-1])
                self._json(200, {"ok": True})
                return
            if path.startswith("/api/prompt/references/"):
                reference_store = self.server.reference_store  # type: ignore[attr-defined]
                reference_store.delete_reference(path.rsplit("/", 1)[-1])
                self._json(200, {"ok": True})
                return
            if path.startswith("/api/prompt/library/"):
                block_id = path.rsplit("/", 1)[-1]
                self.server.prompt_store.delete_block(block_id)  # type: ignore[attr-defined]
                self._json(200, {"ok": True})
                return
            if path.startswith("/api/workflows/"):
                workflow_id = path.rsplit("/", 1)[-1]
                store, _ = self.state
                store.delete_workflow(workflow_id)
                self._json(200, {"ok": True})
                return
            if path.startswith("/api/workflow-folders/"):
                folder_id = path.rsplit("/", 1)[-1]
                store, _ = self.state
                store.delete_workflow_folder(folder_id)
                self._json(200, {"ok": True})
                return
            if path.startswith("/api/projects/"):
                project_id = path.rsplit("/", 1)[-1]
                store, _ = self.state
                self._json(200, store.delete_project_folder(project_id))
                return
            if path.startswith("/api/prompt/group-folders/"):
                folder_id = path.rsplit("/", 1)[-1]
                self.server.prompt_store.delete_prompt_group_folder(folder_id)  # type: ignore[attr-defined]
                self._json(200, {"ok": True})
                return
            if path.startswith("/api/prompt/groups/"):
                group_id = path.rsplit("/", 1)[-1]
                self.server.prompt_store.delete_group(group_id)  # type: ignore[attr-defined]
                self._json(200, {"ok": True})
                return
            if path.startswith("/api/keys/"):
                key_id = path.rsplit("/", 1)[-1]
                _, manager = self.state
                manager.remove_key(key_id)
                self._json(200, {"ok": True})
                return
            if path.startswith("/api/accounts/"):
                account_id = path.rsplit("/", 1)[-1]
                store, _ = self.state
                store.remove_account(account_id)
                self._json(200, {"ok": True})
                return
            if path == "/api/outputs/rating/1":
                store, manager = self.state
                filters = output_action_filters(urlparse(self.path).query)
                output_keys = None
                if filters.get("has_filters") == "1":
                    output_keys = {
                        (str(item.get("task_id") or ""), int(item.get("output_index") or 0))
                        for item in public_outputs(store, manager).get("outputs", [])
                        if int(item.get("rating") or 0) == 1 and matches_public_output_filters(item, filters)
                    }
                project_id = filters.get("project_id", "")
                self._json(200, {"ok": True, **store.delete_outputs_by_rating(1, project_id=project_id, output_keys=output_keys)})
                return
            if path.startswith("/api/tasks/"):
                task_id = path.rsplit("/", 1)[-1]
                _, manager = self.state
                manager.delete_task(task_id)
                self._json(200, {"ok": True})
                return
            self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
        except Exception as exc:
            self._json(400 if isinstance(exc, RhCliError) else 500, self._safe_error(exc))

    def _serve_output(self, path: str) -> None:
        parts = path.split("/")
        try:
            task_id = parts[3]
            index = int(parts[5])
            store, _ = self.state
            task = store.task(task_id)
            if not task:
                raise FileNotFoundError
            outputs = [item for item in task.get("outputs", []) if item.get("kind") == "file"]
            output = outputs[index]
            file_path = Path(output["path"]).resolve()
            allowed = Path(task["output_dir"]).resolve()
            if allowed not in file_path.parents or not file_path.is_file():
                raise FileNotFoundError
        except (ValueError, IndexError, KeyError, OSError):
            self._json(404, {"code": "OUTPUT_NOT_FOUND", "message": "产物不存在"})
            return

        self._serve_file_with_ranges(file_path, "OUTPUT_NOT_FOUND", "产物不存在")

    def _serve_case_outputs_archive(self, manager: TaskManager, *, project_id: str = "", filters: dict[str, str] | None = None) -> None:
        media = public_output_media(manager, project_id=project_id, filters=filters)
        if not media:
            self._json(404, {"code": "NO_CASE_OUTPUTS", "message": "还没有带“案例”标签的媒体文件。"})
            return
        archive_path: Path | None = None
        try:
            DATA_ROOT.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix=".case-outputs-", suffix=".zip", dir=DATA_ROOT, delete=False) as temporary:
                archive_path = Path(temporary.name)
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
                for item in media:
                    archive.write(item["path"], item["archive_name"])
            self._serve_download_file(archive_path, "案例成片.zip", "application/zip")
        except OSError as exc:
            self._json(500, {"code": "CASE_OUTPUT_EXPORT_FAILED", "message": f"导出案例失败：{exc}"})
        finally:
            if archive_path is not None:
                try:
                    archive_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _serve_download_file(self, file_path: Path, download_name: str, content_type: str) -> None:
        try:
            file_size = file_path.stat().st_size
        except OSError:
            self._json(404, {"code": "DOWNLOAD_NOT_FOUND", "message": "下载文件不存在。"})
            return
        self.send_response(HTTPStatus.OK)
        self._headers(content_type, file_size)
        self.send_header("Content-Disposition", f"attachment; filename=case-outputs.zip; filename*=UTF-8''{quote(download_name, safe='')}")
        self.end_headers()
        try:
            with file_path.open("rb") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _serve_local_preview(self, token: str) -> None:
        file_path = self.server.local_preview_path(token)  # type: ignore[attr-defined]
        if file_path is None or not file_path.is_file():
            self._json(404, {"code": "PREVIEW_NOT_FOUND", "message": "媒体预览不存在或已失效"})
            return
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if not (mime.startswith("image/") or mime.startswith("audio/") or mime.startswith("video/")):
            self._json(404, {"code": "PREVIEW_NOT_FOUND", "message": "该文件不是可预览的媒体"})
            return
        self._serve_file_with_ranges(file_path, "PREVIEW_NOT_FOUND", "媒体预览不存在或已失效")

    def _serve_file_with_ranges(self, file_path: Path, error_code: str, error_message: str) -> None:
        try:
            file_size = file_path.stat().st_size
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        except OSError:
            self._json(404, {"code": error_code, "message": error_message})
            return

        start = 0
        end = file_size - 1
        length = file_size
        status = HTTPStatus.OK
        range_header = str(self.headers.get("Range") or "").strip()
        if range_header:
            try:
                if not range_header.startswith("bytes=") or "," in range_header:
                    raise ValueError
                range_start, range_end = range_header[6:].split("-", 1)
                if file_size <= 0:
                    raise ValueError
                if not range_start:
                    suffix_length = int(range_end)
                    if suffix_length <= 0:
                        raise ValueError
                    start = max(0, file_size - suffix_length)
                    end = file_size - 1
                else:
                    start = int(range_start)
                    if start < 0 or start >= file_size:
                        raise ValueError
                    end = int(range_end) if range_end else file_size - 1
                    if end < start:
                        raise ValueError
                    end = min(end, file_size - 1)
                length = end - start + 1
            except (TypeError, ValueError):
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self._headers(content_type, 0)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT

        self.send_response(status)
        self._headers(content_type, length)
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        try:
            with file_path.open("rb") as stream:
                stream.seek(start)
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Browsers commonly cancel an old range request when the user seeks.
            return

    def _serve_action_image(self, path: str, kind: str = "color") -> None:
        parts = path.split("/")
        action_id = parts[4] if len(parts) == 6 else ""
        file_path = self.server.action_store.image_path(action_id, kind)  # type: ignore[attr-defined]
        if file_path is None:
            label = {"depth": "深度图", "skeleton": "骨骼图"}.get(kind, "原图")
            self._json(404, {"code": "ACTION_IMAGE_NOT_FOUND", "message": f"动作{label}不存在"})
            return
        try:
            data = file_path.read_bytes()
        except OSError:
            label = {"depth": "深度图", "skeleton": "骨骼图"}.get(kind, "原图")
            self._json(404, {"code": "ACTION_IMAGE_NOT_FOUND", "message": f"动作{label}不存在"})
            return
        self.send_response(HTTPStatus.OK)
        self._headers(mimetypes.guess_type(str(file_path))[0] or "application/octet-stream", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _serve_action_path(self, path: str, kind: str) -> None:
        parts = path.split("/")
        action_id = parts[4] if len(parts) == 6 else ""
        file_path = self.server.action_store.image_path(action_id, kind)  # type: ignore[attr-defined]
        if file_path is None:
            label = {"depth": "深度图", "skeleton": "骨骼图"}.get(kind, "原图")
            self._json(404, {"code": "ACTION_IMAGE_NOT_FOUND", "message": f"动作{label}不存在"})
            return
        self._json(200, {"path": str(file_path), "name": file_path.name, "kind": kind})

    def _serve_reference_media(self, path: str, kind: str) -> None:
        parts = path.split("/")
        reference_id = parts[4] if len(parts) == 6 else ""
        file_path = self.server.reference_store.media_path(reference_id, kind)  # type: ignore[attr-defined]
        if file_path is None:
            self._json(404, {"code": "REFERENCE_MEDIA_NOT_FOUND", "message": "参考资源媒体不存在"})
            return
        try:
            data = file_path.read_bytes()
        except OSError:
            self._json(404, {"code": "REFERENCE_MEDIA_NOT_FOUND", "message": "参考资源媒体不存在"})
            return
        self.send_response(HTTPStatus.OK)
        self._headers(mimetypes.guess_type(str(file_path))[0] or "application/octet-stream", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _serve_reference_path(self, path: str, kind: str) -> None:
        parts = path.split("/")
        reference_id = parts[4] if len(parts) == 6 else ""
        file_path = self.server.reference_store.media_path(reference_id, kind)  # type: ignore[attr-defined]
        if file_path is None:
            self._json(404, {"code": "REFERENCE_MEDIA_NOT_FOUND", "message": "参考资源媒体不存在"})
            return
        self._json(200, {"path": str(file_path), "name": file_path.name, "kind": kind})

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        if relative in {"prompt", "prompt/"}:
            relative = "prompt.html"
        if relative in {"workflows", "workflows/"}:
            relative = "workflows.html"
        if relative in {"outputs/compare", "outputs/compare/"}:
            relative = "compare.html"
        if relative in {"outputs", "outputs/"}:
            relative = "outputs.html"
        if relative in {"dashboard", "dashboard/"}:
            relative = "dashboard.html"
        if relative in {"settings", "settings/"}:
            relative = "settings.html"
        if relative in {"focus", "focus/"}:
            relative = "focus.html"
        if relative.startswith("static/"):
            relative = relative[len("static/"):]
        file_path = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in file_path.parents and file_path != STATIC_ROOT.resolve():
            self._json(404, {"code": "NOT_FOUND", "message": "页面不存在"})
            return
        if not file_path.is_file():
            self._json(404, {"code": "NOT_FOUND", "message": "页面不存在"})
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self._headers(mimetypes.guess_type(str(file_path))[0] or "application/octet-stream", len(data))
        self.end_headers()
        self.wfile.write(data)


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        self._local_preview_paths: dict[str, Path] = {}
        self._local_preview_lock = threading.Lock()
        self._runtime_logs = deque(maxlen=500)
        self._runtime_logs_lock = threading.Lock()
        self.store = LocalStore()
        self.manager = TaskManager(self.store)
        self.toolbox = ToolboxManager(self.store)
        configured_library_path = self.store.prompt_library_path()
        self.prompt_store = PromptStore(DATA_ROOT, library_path=configured_library_path)
        configured_media_root = self.store.media_library_root()
        if configured_media_root:
            self.action_store = ActionStore(DATA_ROOT, source_root=configured_media_root)
            self.reference_store = ReferenceStore(DATA_ROOT, source_root=configured_media_root)
        else:
            configured_action_path = self.store.action_resources_path()
            self.action_store = ActionStore(DATA_ROOT, source_path=configured_action_path or None)
            self.reference_store = ReferenceStore(DATA_ROOT, source_paths=self.store.reference_resources_paths())
        super().__init__(address, LocalHandler)

    def server_close(self) -> None:
        self.toolbox.shutdown()
        super().server_close()

    def append_runtime_log(self, message: str, level: str = "info") -> None:
        with self._runtime_logs_lock:
            self._runtime_logs.append(
                {
                    "at": int(time.time() * 1000),
                    "level": str(level or "info").lower(),
                    "stage": "service",
                    "source": "service",
                    "message": str(message),
                }
            )

    def runtime_logs(self, limit: int = 500) -> list[dict[str, object]]:
        try:
            requested = int(limit)
        except (TypeError, ValueError):
            requested = 500
        with self._runtime_logs_lock:
            return list(self._runtime_logs)[-max(1, min(requested, 1000)):]

    def register_local_preview(self, path_value: str) -> str:
        path = Path(path_value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        token = uuid.uuid4().hex
        with self._local_preview_lock:
            while len(self._local_preview_paths) >= 256:
                self._local_preview_paths.pop(next(iter(self._local_preview_paths)))
            self._local_preview_paths[token] = path
        return f"/api/local-preview/{token}"

    def local_preview_path(self, token: str) -> Path | None:
        with self._local_preview_lock:
            path = self._local_preview_paths.get(str(token or "").strip())
        return path

    def server_close(self) -> None:
        self.manager.close()
        self.store._db.close()
        super().server_close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="启动纯本地 RH 工作流 Web 应用")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8766, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = AppServer((args.host, args.port))
    url = f"http://{args.host}:{args.port}/"
    print(f"RH Workflow Desk 已启动：{url}")
    print(f"本地数据目录：{DATA_ROOT}")
    if not args.no_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRH Workflow Desk 已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
