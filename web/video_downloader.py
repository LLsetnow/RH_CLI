from __future__ import annotations

import importlib.util
import mimetypes
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from rh_cli.errors import RhCliError


DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")
VIDEO_EXTENSIONS = {".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".webm", ".wmv"}
DOWNLOAD_TIMEOUT_SECONDS = 300


def normalize_douyin_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise RhCliError("INVALID_DOUYIN_URL", "请输入完整的抖音 http(s) 链接。")
    if not any(hostname == domain or hostname.endswith("." + domain) for domain in DOUYIN_DOMAINS):
        raise RhCliError("INVALID_DOUYIN_URL", "只支持抖音链接（douyin.com 或 iesdouyin.com）。")
    return url


def yt_dlp_command() -> list[str]:
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]

    candidates = (
        Path.home() / ".local" / "bin" / "yt-dlp",
        Path("/opt/homebrew/bin/yt-dlp"),
        Path("/usr/local/bin/yt-dlp"),
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]

    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise RhCliError("YTDLP_NOT_FOUND", "本机未找到 yt-dlp，请先安装 yt-dlp 后再下载抖音视频。")


def _downloaded_video_candidates(directory: Path) -> list[Path]:
    candidates = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > 0 and (mimetypes.guess_type(str(path))[0] or "").startswith("video/"):
                candidates.append(path)
        except OSError:
            continue
    return sorted(candidates, key=lambda path: path.stat().st_mtime_ns, reverse=True)


def _process_error(output: str, url: str, cookie_path: Path | None) -> str:
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    if not lines:
        return "请检查链接、Cookie 文件和 yt-dlp 安装状态。"
    detail = lines[-1].replace(url, "<抖音链接>")
    if cookie_path:
        detail = detail.replace(str(cookie_path), "<Cookie 文件>")
    return detail[:300]


def download_douyin_video(
    url: str,
    cookie_path: str,
    data_root: Path,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> Path:
    normalized_url = normalize_douyin_url(url)
    normalized_cookie_path = str(cookie_path or "").strip()
    cookie = Path(normalized_cookie_path).expanduser().resolve() if normalized_cookie_path else None
    if cookie and not cookie.is_file():
        raise RhCliError("DOUYIN_COOKIE_NOT_FOUND", f"抖音 Cookie 文件不存在：{cookie}")

    download_root = Path(data_root).expanduser().resolve() / "downloaded-inputs"
    target_dir = download_root / ("douyin-" + uuid.uuid4().hex)
    target_dir.mkdir(parents=True, exist_ok=True)
    command = yt_dlp_command() + [
        "--no-playlist",
        "--no-warnings",
        "--newline",
        "--format",
        "bv*+ba/b",
        "--merge-output-format",
        "mp4",
        "--output",
        str(target_dir / "%(id)s.%(ext)s"),
    ]
    if cookie:
        command.extend(["--cookies", str(cookie)])
    command.append(normalized_url)

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise RhCliError("DOUYIN_DOWNLOAD_TIMEOUT", "抖音视频下载超时，请检查链接或稍后重试。") from exc
    except OSError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise RhCliError("DOUYIN_DOWNLOAD_FAILED", "无法启动 yt-dlp，请检查本机安装。") from exc

    if completed.returncode != 0:
        detail = _process_error((completed.stderr or "") + "\n" + (completed.stdout or ""), normalized_url, cookie)
        shutil.rmtree(target_dir, ignore_errors=True)
        raise RhCliError("DOUYIN_DOWNLOAD_FAILED", "抖音视频下载失败：" + detail)

    candidates = _downloaded_video_candidates(target_dir)
    if not candidates:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise RhCliError("DOUYIN_DOWNLOAD_EMPTY", "抖音链接没有产生可用的视频文件。")
    return candidates[0]
