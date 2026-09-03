from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from rh_cli.errors import RhCliError

from .app import DATA_ROOT, WEB_ROOT, LocalStore, TaskManager, pick_local_directory_on_macos, pick_local_file_on_macos, public_account, public_dashboard, public_key, public_outputs, public_state, safe_name
from .action_store import ActionStore
from .prompt_store import PromptStore
from .reference_store import ReferenceStore
from .translation import AliyunTranslationClient
from .vision import AliyunVisionClient
from .video_downloader import download_douyin_video


STATIC_ROOT = WEB_ROOT / "static"
LOCAL_PREVIEW_LIMIT = 8 * 1024 * 1024
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
    """Copy browser-selected resource media into ref and return Markdown-ready paths."""
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
        allowed_roles = {"color", "depth"} if kind == "action" else {"audio"} if kind == "audio" else {"image"}
        if role not in allowed_roles:
            raise RhCliError("INVALID_PROMPT_MEDIA", "未知的素材槽位。")
        if role in decoded:
            raise RhCliError("INVALID_PROMPT_MEDIA", "同一个素材槽位只能选择一个文件。")
        decoded[role] = _decode_prompt_media(item, kind, role)

    if kind == "action":
        color_current = str(current.get("color_image_path") or current.get("image_path") or "").strip()
        depth_current = str(current.get("depth_image_path") or "").strip()
        color_item = decoded.get("color")
        depth_item = decoded.get("depth")
        pair_source = color_current or depth_current
        if not pair_source and color_item:
            pair_source = color_item[1]
        if not pair_source and depth_item:
            pair_source = depth_item[1]
        pair_stem = Path(pair_source).stem
        if pair_stem.endswith("_depth"):
            pair_stem = pair_stem[:-6]
        pair_stem = safe_name(pair_stem, "action")
        color_dir = root / "pose" / "color"
        depth_dir = root / "pose" / "depth"
        current_paths = {color_current, depth_current}
        if not color_current and not depth_current:
            counter = 2
            original_stem = pair_stem
            while any(
                candidate.is_file() and _prompt_relative(root, candidate) not in current_paths
                for candidate in list(color_dir.glob(pair_stem + ".*")) + list(depth_dir.glob(pair_stem + "_depth.*"))
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
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self._headers(length=0)
            self.end_headers()
            return
        if path == "/api/state":
            store, manager = self.state
            state = public_state(store, manager)
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
        if path == "/api/workflows":
            store, _ = self.state
            self._json(200, {"workflows": store.workflows()})
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
        if path == "/api/prompt/actions":
            action_store = self.server.action_store  # type: ignore[attr-defined]
            # The configured pose Markdown file is the source of truth. A hash check makes edits visible
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
        if path.startswith("/api/prompt/actions/") and path.endswith("/image-path"):
            self._serve_action_path(path, "color")
            return
        if path.startswith("/api/prompt/actions/") and path.endswith("/depth"):
            self._serve_action_image(path, "depth")
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
            if path == "/api/pick-douyin-cookie":
                selected = pick_local_file_on_macos("选择抖音 Cookie 文件")
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
            if path == "/api/dashboard/refresh-balances":
                _, manager = self.state
                self._json(200, manager.refresh_balances())
                return
            if path == "/api/pick-action-resources":
                selected = pick_local_file_on_macos("选择动作库 Markdown 文件")
                self._json(200, {"path": str(selected), "name": selected.name})
                return
            if path == "/api/pick-prompt-resource":
                body = self._body()
                labels = {
                    "library": "基础积木 Markdown 文件",
                    "action": "动作库 Markdown 文件",
                    "character": "人物库 Markdown 文件",
                    "audio": "音频库 Markdown 文件",
                    "background": "背景库 Markdown 文件",
                    "clothes": "服装库 Markdown 文件",
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
                workflow_id, _, _ = store.save_workflow(
                    str(body.get("filename") or "workflow.json"),
                    content,
                    account_id=str(body.get("account_id") or ""),
                    remote_workflow_id=str(body.get("remote_workflow_id") or ""),
                    source_dir=str(body.get("source_dir") or ""),
                    input_config=body.get("input_config") if isinstance(body.get("input_config"), dict) else None,
                )
                self._json(201, store.workflow_detail(workflow_id))
                return
            if path == "/api/workflows/analyze":
                body = self._body()
                content = body.get("content")
                if not isinstance(content, str):
                    raise RhCliError("INVALID_WORKFLOW", "缺少工作流 JSON 内容。")
                store, _ = self.state
                workflow_id, workflow_path, analysis = store.save_workflow(
                    str(body.get("filename") or "workflow.json"),
                    content,
                    account_id=str(body.get("account_id") or ""),
                    remote_workflow_id=str(body.get("remote_workflow_id") or ""),
                    source_dir=str(body.get("source_dir") or ""),
                    register=False,
                )
                saved_workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
                saved_metadata = saved_workflow.get("__rh_meta__") if isinstance(saved_workflow, dict) else {}
                saved_metadata = saved_metadata if isinstance(saved_metadata, dict) else {}
                self._json(
                    200,
                    {
                        "workflow_id": workflow_id,
                        "filename": workflow_path.name[len(f"{workflow_id}_") :] if workflow_path.name.startswith(f"{workflow_id}_") else workflow_path.name,
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
                    str(body.get("name") or ""), body.get("items"), str(body.get("id") or "") or None
                )
                self._json(200, {"group": group})
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
                prompt_group = self.server.prompt_store.task_group_snapshot()  # type: ignore[attr-defined]
                bypassed_nodes = body.get("bypassed_nodes")
                if not isinstance(bypassed_nodes, (list, dict)):
                    bypassed_nodes = body.get("bypassed_inputs") if isinstance(body.get("bypassed_inputs"), (list, dict)) else None
                task = manager.submit_task(
                    str(body.get("workflow_id") or ""),
                    body.get("files") if isinstance(body.get("files"), dict) else {},
                    body.get("prompts") if isinstance(body.get("prompts"), dict) else {},
                    str(body.get("key_id") or "") or None,
                    str(body.get("output_dir") or "") or None,
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
            if path.startswith("/api/tasks/") and "/outputs/" in path:
                parts = path.split("/")
                if len(parts) != 6 or parts[4] != "outputs":
                    self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
                    return
                task_id = parts[3]
                try:
                    output_index = int(parts[5])
                except ValueError as exc:
                    raise RhCliError("INVALID_OUTPUT_RATING", "产物索引无效。") from exc
                store, _ = self.state
                output = store.update_output_rating(task_id, output_index, self._body().get("rating"))
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
                if "prompt_library_path" in body:
                    prompt_path = store.set_prompt_library_path(str(body.get("prompt_library_path") or ""))
                    self.server.prompt_store.set_library_path(prompt_path)  # type: ignore[attr-defined]
                    result["prompt_library_path"] = str(self.server.prompt_store.library_path)  # type: ignore[attr-defined]
                if "media_library_root" in body:
                    media_root = store.set_media_library_root(str(body.get("media_library_root") or ""))
                    self.server.action_store.set_source_root(media_root)  # type: ignore[attr-defined]
                    self.server.reference_store.set_source_root(media_root)  # type: ignore[attr-defined]
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
                if any(key in body for key in ("telegram_inbound_workflow_id", "telegram_inbound_enabled")):
                    result["telegram"] = store.set_telegram_inbound_settings(
                        str(body.get("telegram_inbound_workflow_id") or ""),
                        body.get("telegram_inbound_enabled"),
                    )
                self._json(200, result)
                return
            if path.startswith("/api/workflows/"):
                workflow_id = path.rsplit("/", 1)[-1]
                store, _ = self.state
                self._json(200, {"workflow": store.update_workflow(workflow_id, self._body())})
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
                store, _ = self.state
                self._json(200, {"ok": True, **store.delete_outputs_by_rating(1)})
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
            label = "深度图" if kind == "depth" else "原图"
            self._json(404, {"code": "ACTION_IMAGE_NOT_FOUND", "message": f"动作{label}不存在"})
            return
        try:
            data = file_path.read_bytes()
        except OSError:
            label = "深度图" if kind == "depth" else "原图"
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
            label = "深度图" if kind == "depth" else "原图"
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
