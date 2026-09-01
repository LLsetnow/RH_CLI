#!/usr/bin/env python3
"""Local browser UI for the checked-in SS_tools duck image decoder.

The server intentionally binds to 127.0.0.1 only. Uploaded files are staged
under a temporary directory and removed after each decode attempt.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
MAC_DECODER = ROOT / "macOS-duck-decoder"
LINUX_DECODER = ROOT / "duck_linux_decoder.py"
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
KNOWN_PAYLOAD_EXTENSIONS = ALLOWED_EXTENSIONS | {".mp4", ".avi", ".mov", ".mkv", ".txt", ".bin"}
PREVIEWABLE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".mp4", ".webm", ".mov", ".m4v"}
DECODE_LOCK = threading.Lock()
PREVIEW_LOCK = threading.Lock()
PREVIEWS: dict[str, tuple[Path, float]] = {}
MAX_PREVIEWS = 20
PREVIEW_TTL_SECONDS = 60 * 60


class UserInputError(Exception):
    """An expected, actionable input error for the browser UI."""


class DecoderError(Exception):
    """The decoder process failed or did not create an output file."""


def json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def safe_display_path(path: Path) -> str:
    """Return a path suitable for display without changing its meaning."""

    return str(path)


def register_preview(path: Path) -> str | None:
    if path.suffix.lower() not in PREVIEWABLE_EXTENSIONS:
        return None

    now = time.time()
    token = secrets.token_urlsafe(18)
    with PREVIEW_LOCK:
        expired = [key for key, (_, created_at) in PREVIEWS.items() if now - created_at > PREVIEW_TTL_SECONDS]
        for key in expired:
            PREVIEWS.pop(key, None)
        if len(PREVIEWS) >= MAX_PREVIEWS:
            oldest = min(PREVIEWS, key=lambda key: PREVIEWS[key][1])
            PREVIEWS.pop(oldest, None)
        PREVIEWS[token] = (path.resolve(), now)
    return token


def preview_path(token: str) -> Path | None:
    with PREVIEW_LOCK:
        entry = PREVIEWS.get(token)
        if entry is None:
            return None
        path, created_at = entry
        if time.time() - created_at > PREVIEW_TTL_SECONDS:
            PREVIEWS.pop(token, None)
            return None
    try:
        if path.is_file():
            return path
    except OSError:
        pass
    return None


def decoder_backend() -> dict:
    """Choose the native macOS binary or the dependency-light Linux backend."""

    system = platform.system()
    if system == "Darwin" and MAC_DECODER.exists() and os.access(MAC_DECODER, os.X_OK):
        return {
            "kind": "macos-native",
            "label": "macOS 原生解码器",
            "path": MAC_DECODER,
            "command": [str(MAC_DECODER)],
            "ready": True,
        }

    if system == "Linux" and LINUX_DECODER.exists():
        return {
            "kind": "linux-python",
            "label": "Linux Python 解码器",
            "path": LINUX_DECODER,
            "command": [sys.executable, str(LINUX_DECODER)],
            "ready": True,
        }

    if LINUX_DECODER.exists():
        return {
            "kind": "python-fallback",
            "label": "Python 解码器",
            "path": LINUX_DECODER,
            "command": [sys.executable, str(LINUX_DECODER)],
            "ready": True,
        }

    if system == "Darwin":
        error = "找不到可执行的 macOS 解码器。"
    elif system == "Linux":
        error = "找不到 Linux Python 解码器。"
    else:
        error = f"暂不支持 {system} 系统。"
    return {
        "kind": "unavailable",
        "label": "解码器不可用",
        "path": MAC_DECODER if system == "Darwin" else LINUX_DECODER,
        "command": [],
        "ready": False,
        "error": error,
    }


def validate_image_path(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise UserInputError("请选择一张鸭子图，或粘贴本地图片路径。")

    path = Path(raw_path.strip()).expanduser()
    if not path.exists():
        raise UserInputError(f"找不到输入图片：{path}")
    if not path.is_file():
        raise UserInputError("输入路径不是文件，请选择图片文件。")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UserInputError(f"暂只支持图片文件（{allowed}）。")
    return path.resolve()


def make_output_path(raw_path: str, input_path: Path) -> Path:
    if not raw_path or not raw_path.strip():
        raise UserInputError("请设置导出路径。")

    requested = Path(raw_path.strip()).expanduser()
    looks_like_directory = (
        requested.exists() and requested.is_dir()
    ) or raw_path.endswith((os.sep, "/")) or not requested.suffix

    if looks_like_directory:
        requested.mkdir(parents=True, exist_ok=True)
        output_path = requested / f"{input_path.stem}.decoded"
    else:
        requested.parent.mkdir(parents=True, exist_ok=True)
        output_path = requested

    output_path = output_path.resolve()
    if output_path == input_path:
        raise UserInputError("导出路径不能覆盖输入图片。")
    return output_path


def snapshot_directory(directory: Path) -> dict[Path, tuple[int, int]]:
    snapshot: dict[Path, tuple[int, int]] = {}
    try:
        entries = directory.iterdir()
    except OSError:
        return snapshot
    for entry in entries:
        try:
            if entry.is_file():
                stat = entry.stat()
                snapshot[entry] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            continue
    return snapshot


def find_decoder_output(
    requested: Path,
    before: dict[Path, tuple[int, int]],
    started_ns: int,
) -> Path | None:
    """Find the file created by decoders that auto-adjust the extension."""

    candidates: list[tuple[int, Path]] = []
    try:
        entries = requested.parent.iterdir()
    except OSError:
        return None

    for entry in entries:
        try:
            if not entry.is_file():
                continue
            stat = entry.stat()
            previous = before.get(entry)
            changed = previous is None or previous != (stat.st_mtime_ns, stat.st_size)
            if stat.st_mtime_ns < started_ns or not changed:
                continue
            if entry == requested or entry.name.startswith(requested.name):
                candidates.append((stat.st_mtime_ns, entry))
        except OSError:
            continue

    if not candidates and requested.exists():
        return requested
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def path_reported_by_decoder(output: str) -> Path | None:
    """Read the extracted file path printed by the bundled decoder."""

    for line in output.splitlines():
        match = re.search(r"(?:Extraction completed|提取完成)\s*[:：]\s*(/.+?)\s*$", line)
        if match:
            reported = Path(match.group(1).strip())
            if reported.exists() and reported.is_file():
                return reported.resolve()
    return None


def move_to_requested_location(reported: Path, requested: Path) -> Path:
    """Place a decoder-reported file at the requested path with its real suffix."""

    destination = requested
    if reported.suffix and destination.suffix.lower() != reported.suffix.lower():
        if destination.suffix.lower() in KNOWN_PAYLOAD_EXTENSIONS:
            destination = destination.with_suffix(reported.suffix)
        else:
            destination = Path(f"{destination}{reported.suffix}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if reported.resolve() != destination.resolve():
        shutil.move(str(reported), str(destination))
    return destination.resolve()


def run_decoder(input_path: Path, output_path: Path, password: str) -> Path:
    backend = decoder_backend()
    if not backend["ready"]:
        raise DecoderError(backend["error"])

    before = snapshot_directory(output_path.parent)
    started_ns = time.time_ns()
    command = [
        *backend["command"],
        "--duck",
        str(input_path),
        "--out",
        str(output_path),
    ]
    if password:
        command.extend(["--password", password])

    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15 * 60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DecoderError("解码超过 15 分钟，已停止本次任务。请检查图片是否完整。") from exc
    except OSError as exc:
        raise DecoderError(f"无法启动 macOS 解码器：{exc}") from exc

    combined_output = f"{completed.stdout}\n{completed.stderr}"
    reported_path = path_reported_by_decoder(combined_output)
    result_path = reported_path or find_decoder_output(output_path, before, started_ns)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "解码器未提供错误信息").strip()
        raise DecoderError(f"解码失败：{detail[-1200:]}")
    if result_path is None or not result_path.exists() or result_path.stat().st_size == 0:
        detail = (completed.stdout or completed.stderr or "解码器没有生成输出文件").strip()
        raise DecoderError(f"解码未完成：{detail[-1200:]}")
    return move_to_requested_location(result_path, output_path)


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    """Parse multipart form data with only Python's standard library."""

    headers = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n"
        "\r\n"
    ).encode("utf-8")
    message = BytesParser(policy=policy.default).parsebytes(headers + body)
    if not message.is_multipart():
        raise UserInputError("上传数据格式无效，请刷新页面后重试。")

    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        field_name = part.get_param("name", header="content-disposition")
        if not field_name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            files[field_name] = (Path(filename).name, payload)
        else:
            fields[field_name] = payload.decode("utf-8", errors="replace")
    return fields, files


def pick_image_on_macos() -> Path:
    if platform.system() != "Darwin":
        raise UserInputError("Linux 请使用网页上传，或手动粘贴本地图片路径。")
    if shutil.which("osascript") is None:
        raise UserInputError("本机没有可用的 macOS 文件选择器，请改用网页上传或粘贴路径。")

    script = r'''
try
    set pickedFile to choose file with prompt "选择鸭子图" of type {"public.png", "public.jpeg", "public.webp", "public.bmp"}
    return POSIX path of pickedFile
on error number -128
    return ""
end try
'''
    completed = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise UserInputError("打开文件选择器失败，请改用网页上传或粘贴路径。")
    selected = completed.stdout.strip()
    if not selected:
        raise UserInputError("已取消选择图片。")
    return validate_image_path(selected)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "SS_tools Duck UI/1.0"

    def log_message(self, format: str, *args: object) -> None:
        # Keep the terminal useful while never printing passwords or uploaded data.
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, status: HTTPStatus, payload: dict) -> None:
        data = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, status: HTTPStatus, text: str, content_type: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        request = urlparse(self.path)
        if request.path == "/api/status":
            desktop = Path.home() / "Desktop"
            default_export = desktop / "duck-decoded" if desktop.exists() else Path.home() / "duck-decoded"
            backend = decoder_backend()
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "platform": platform.system(),
                    "backend": backend["kind"],
                    "backend_label": backend["label"],
                    "decoder": safe_display_path(backend["path"]),
                    "decoder_ready": backend["ready"],
                    "native_picker": platform.system() == "Darwin",
                    "default_export_path": safe_display_path(default_export),
                },
            )
            return

        if request.path.startswith("/api/preview/"):
            token = unquote(request.path.removeprefix("/api/preview/")).strip("/")
            path = preview_path(token)
            if path is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "预览文件不存在或已过期。"})
            else:
                self.serve_preview(path)
            return

        if request.path == "/":
            self.serve_static(WEB_DIR / "index.html")
            return

        if request.path.startswith("/web/"):
            relative = unquote(request.path.removeprefix("/web/")).lstrip("/")
            self.serve_static(WEB_DIR / relative)
            return

        self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "页面不存在。"})

    def serve_preview(self, path: Path) -> None:
        try:
            file_size = path.stat().st_size
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            start = 0
            end = file_size - 1
            status = HTTPStatus.OK
            range_header = self.headers.get("Range", "")
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if not match:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers()
                    return
                requested_start, requested_end = match.groups()
                if requested_start:
                    start = int(requested_start)
                    if requested_end:
                        end = int(requested_end)
                    else:
                        end = file_size - 1
                elif requested_end:
                    suffix_length = int(requested_end)
                    start = max(file_size - suffix_length, 0)
                if start >= file_size or start > end:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers()
                    return
                end = min(end, file_size - 1)
                status = HTTPStatus.PARTIAL_CONTENT

            content_length = max(end - start + 1, 0)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(content_length))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()

            with path.open("rb") as file_handle:
                file_handle.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = file_handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Browsers can cancel a media range request while scrubbing.
            return

    def serve_static(self, path: Path) -> None:
        try:
            resolved = path.resolve()
            resolved.relative_to(WEB_DIR.resolve())
            if not resolved.is_file():
                raise FileNotFoundError
            content = resolved.read_bytes()
        except (FileNotFoundError, OSError, ValueError):
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "页面资源不存在。"})
            return

        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def read_body(self) -> bytes:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise UserInputError("请求体大小无效。") from exc
        if content_length <= 0:
            raise UserInputError("请求内容为空。")
        if content_length > MAX_UPLOAD_BYTES:
            raise UserInputError("图片超过 512 MB，暂不支持。")
        return self.rfile.read(content_length)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        request = urlparse(self.path)
        try:
            if request.path == "/api/pick-image":
                path = pick_image_on_macos()
                self.send_json(HTTPStatus.OK, {"ok": True, "path": safe_display_path(path), "name": path.name})
                return

            if request.path == "/api/decode":
                self.handle_decode()
                return

            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在。"})
        except UserInputError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except DecoderError as exc:
            self.send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": str(exc)})
        except Exception as exc:  # Keep unexpected failures readable in the UI.
            print(f"Unexpected server error: {exc}")
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "本地服务发生意外错误，请查看终端日志。"})

    def handle_decode(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise UserInputError("请使用网页表单提交图片。")

        fields, files = parse_multipart(content_type, self.read_body())
        password = fields.get("password", "")
        output_raw = fields.get("output_path", "")
        upload = files.get("image")
        staged_path: Path | None = None

        if upload and upload[1]:
            filename, payload = upload
            extension = Path(filename).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                raise UserInputError("上传的文件不是支持的图片格式。")
            with tempfile.NamedTemporaryFile(prefix="duck-input-", suffix=extension, dir=None, delete=False) as temp:
                temp.write(payload)
                staged_path = Path(temp.name)
            input_path = staged_path.resolve()
            input_label = filename
        else:
            input_path = validate_image_path(fields.get("input_path", ""))
            input_label = input_path.name

        try:
            output_path = make_output_path(output_raw, input_path)
            started = time.perf_counter()
            with DECODE_LOCK:
                result_path = run_decoder(input_path, output_path, password)
            elapsed = round(time.perf_counter() - started, 2)
            result_size = result_path.stat().st_size
            preview_token = register_preview(result_path)
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "input": input_label,
                    "output_path": safe_display_path(result_path),
                    "output_name": result_path.name,
                    "bytes": result_size,
                    "elapsed_seconds": elapsed,
                    "preview_url": f"/api/preview/{preview_token}" if preview_token else None,
                    "media_kind": "video" if result_path.suffix.lower() in {".mp4", ".webm", ".mov", ".m4v"} else "image" if result_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} else None,
                },
            )
        finally:
            if staged_path is not None:
                try:
                    staged_path.unlink(missing_ok=True)
                except OSError:
                    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 SS_tools 鸭鸭图本地解码网页工具")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("为避免把图片解码服务暴露到局域网，请使用 127.0.0.1。")
    if not WEB_DIR.exists():
        raise SystemExit(f"找不到网页资源目录：{WEB_DIR}")

    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"SS_tools 鸭鸭图解码器已启动：{url}")
    print("服务只监听本机；按 Ctrl+C 停止。")
    if not args.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url, new=2)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止本地服务。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
