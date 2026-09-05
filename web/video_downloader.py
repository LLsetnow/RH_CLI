from __future__ import annotations

import importlib.util
import mimetypes
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from rh_cli.errors import RhCliError


DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")
BILIBILI_DOMAINS = ("bilibili.com", "b23.tv")
X_DOMAINS = ("x.com", "twitter.com", "t.co")
SOCIAL_VIDEO_DOMAINS = {
    "douyin": DOUYIN_DOMAINS,
    "bilibili": BILIBILI_DOMAINS,
    "x": X_DOMAINS,
}
SOCIAL_COOKIE_HINTS = {
    "douyin": ("douyin", "iesdouyin"),
    "bilibili": ("bilibili", "b23"),
    "x": ("x.com", "twitter"),
}
SOCIAL_PLATFORM_LABELS = {"douyin": "抖音", "bilibili": "Bilibili", "x": "X"}
DEFAULT_COOKIE_DIR = Path("/Users/apple/Documents/github/OPC/auth")
VIDEO_EXTENSIONS = {".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".webm", ".wmv"}
DOWNLOAD_TIMEOUT_SECONDS = 300
DOWNLOAD_MAX_ATTEMPTS = 3
DOWNLOAD_RETRY_DELAY_SECONDS = 2.0
SOCIAL_URL_PATTERN = re.compile(r"https?://[^\s<>\]\)]+", re.IGNORECASE)
SOCIAL_COOKIE_SUFFIXES = {".txt", ".cookies", ".json", ".textclipping"}


def normalize_douyin_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise RhCliError("INVALID_DOUYIN_URL", "请输入完整的抖音 http(s) 链接。")
    if not any(hostname == domain or hostname.endswith("." + domain) for domain in DOUYIN_DOMAINS):
        raise RhCliError("INVALID_DOUYIN_URL", "只支持抖音链接（douyin.com 或 iesdouyin.com）。")
    return url


def social_video_platform(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise RhCliError("INVALID_SOCIAL_VIDEO_URL", "请输入完整的抖音、Bilibili 或 X 视频 http(s) 链接。")
    for platform, domains in SOCIAL_VIDEO_DOMAINS.items():
        if any(hostname == domain or hostname.endswith("." + domain) for domain in domains):
            return platform
    raise RhCliError("INVALID_SOCIAL_VIDEO_URL", "只支持抖音、Bilibili/b23.tv 或 X/Twitter 视频链接。")


def normalize_social_video_url(value: str) -> str:
    raw = str(value or "").strip()
    url = extract_social_video_url(raw) or raw
    social_video_platform(url)
    return _canonical_social_video_url(url)


def _canonical_social_video_url(url: str) -> str:
    """Remove presentation-only suffixes that commonly come from shared links."""
    platform = social_video_platform(url)
    if platform != "x":
        return url
    parsed = urlparse(url)
    path = re.sub(r"/video/\d+/?$", "", parsed.path, flags=re.IGNORECASE).rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def extract_social_video_url(value: str) -> str:
    """Return the first supported social video URL embedded in Telegram text."""
    for match in SOCIAL_URL_PATTERN.findall(str(value or "")):
        candidate = match.rstrip(".,!?;:]}>'\"，。！？；：）》】")
        try:
            social_video_platform(candidate)
        except RhCliError:
            continue
        return _canonical_social_video_url(candidate)
    return ""


def cookie_file_for_platform(
    platform: str,
    cookie_dir: str | Path = DEFAULT_COOKIE_DIR,
) -> Path:
    platform = str(platform or "").strip().lower()
    if platform not in SOCIAL_VIDEO_DOMAINS:
        raise RhCliError("INVALID_SOCIAL_VIDEO_PLATFORM", "不支持的视频平台。")
    directory = Path(cookie_dir).expanduser().resolve()
    if not directory.is_dir():
        raise RhCliError("SOCIAL_COOKIE_DIR_NOT_FOUND", "统一 Cookie 文件夹不存在，请检查 OPC/auth 目录。")
    try:
        files = sorted(
            (
                path for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in SOCIAL_COOKIE_SUFFIXES
            ),
            key=lambda path: path.name.lower(),
        )
    except OSError as exc:
        raise RhCliError("SOCIAL_COOKIE_DIR_NOT_FOUND", "无法读取统一 Cookie 文件夹。") from exc
    hints = SOCIAL_COOKIE_HINTS[platform]
    preferred = [path for path in files if any(hint in path.name.lower() for hint in hints)]
    if not preferred:
        raise RhCliError(
            "SOCIAL_COOKIE_NOT_FOUND",
            f"统一 Cookie 文件夹中没有可用的 {SOCIAL_PLATFORM_LABELS[platform]} Cookie 文件。",
        )
    return preferred[0].resolve()


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
    raise RhCliError("YTDLP_NOT_FOUND", "本机未找到 yt-dlp，请先安装 yt-dlp 后再下载视频。")


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
    detail = lines[-1].replace(url, "<视频链接>")
    if cookie_path:
        detail = detail.replace(str(cookie_path), "<Cookie 文件>")
    return detail[:300]


def _is_retryable_download_failure(output: str) -> bool:
    """Avoid retrying failures that a second yt-dlp invocation cannot fix."""
    lowered = str(output or "").casefold()
    permanent_markers = (
        "unsupported url",
        "no video formats found",
        "requested format is not available",
        "requested format not available",
        "video unavailable",
        "private video",
        "sign in to confirm",
        "login required",
        "does not exist",
        "not found",
        "not available in your country",
        "authentication required",
    )
    return not any(marker in lowered for marker in permanent_markers)


def _wait_before_download_retry(delay_seconds: float, attempt: int) -> None:
    delay = max(0.0, float(delay_seconds)) * (2 ** max(0, attempt - 1))
    if delay > 0:
        time.sleep(delay)


def _find_cookie_text(value: object) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    if isinstance(value, str) and "Netscape HTTP Cookie File" in value:
        return value
    if isinstance(value, dict):
        for item in value.values():
            found = _find_cookie_text(item)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_cookie_text(item)
            if found:
                return found
    return ""


def _cookie_argument(cookie_path: Path, target_dir: Path) -> tuple[Path, Path | None]:
    """Convert Apple's .textClipping cookie export into a temporary text file."""
    if cookie_path.suffix.lower() != ".textclipping":
        return cookie_path, None
    try:
        payload = plistlib.loads(cookie_path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        raise RhCliError("SOCIAL_COOKIE_INVALID", "Cookie 文件不是可读的 Netscape 格式。") from exc
    cookie_text = _find_cookie_text(payload)
    if not cookie_text:
        raise RhCliError("SOCIAL_COOKIE_INVALID", "Cookie 文件不是可读的 Netscape 格式。")
    temporary = target_dir / (".rh-social-cookies-" + uuid.uuid4().hex + ".txt")
    try:
        temporary.write_text(cookie_text, encoding="utf-8")
        os.chmod(temporary, 0o600)
    except OSError as exc:
        raise RhCliError("SOCIAL_COOKIE_INVALID", "无法准备 Cookie 文件。") from exc
    return temporary, temporary


def _download_video(
    normalized_url: str,
    cookie: Path | None,
    data_root: Path,
    *,
    platform: str,
    error_prefix: str,
    error_code_prefix: str,
    timeout: int,
    max_attempts: int,
    retry_delay: float,
) -> Path:
    download_root = Path(data_root).expanduser().resolve() / "downloaded-inputs"
    target_dir = download_root / (platform + "-" + uuid.uuid4().hex)
    target_dir.mkdir(parents=True, exist_ok=True)
    temporary_cookie: Path | None = None
    try:
        attempt_limit = max(1, int(max_attempts))
    except (TypeError, ValueError):
        attempt_limit = DOWNLOAD_MAX_ATTEMPTS
    try:
        retry_delay_seconds = max(0.0, float(retry_delay))
    except (TypeError, ValueError):
        retry_delay_seconds = DOWNLOAD_RETRY_DELAY_SECONDS
    try:
        cookie_argument = None
        if cookie:
            cookie_argument, temporary_cookie = _cookie_argument(cookie, target_dir)
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
        if cookie_argument:
            command.extend(["--cookies", str(cookie_argument)])
        command.append(normalized_url)

        for attempt in range(1, attempt_limit + 1):
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=max(1, int(timeout)),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                if attempt < attempt_limit:
                    _wait_before_download_retry(retry_delay_seconds, attempt)
                    continue
                raise RhCliError(
                    f"{error_code_prefix}_DOWNLOAD_TIMEOUT",
                    f"{error_prefix}视频下载超时（已尝试 {attempt} 次），请检查链接或稍后重试。",
                ) from exc
            except OSError as exc:
                raise RhCliError(f"{error_code_prefix}_DOWNLOAD_FAILED", "无法启动 yt-dlp，请检查本机安装。") from exc

            if completed.returncode != 0:
                raw_detail = (completed.stderr or "") + "\n" + (completed.stdout or "")
                if attempt < attempt_limit and _is_retryable_download_failure(raw_detail):
                    _wait_before_download_retry(retry_delay_seconds, attempt)
                    continue
                detail = _process_error(raw_detail, normalized_url, cookie)
                raise RhCliError(
                    f"{error_code_prefix}_DOWNLOAD_FAILED",
                    f"{error_prefix}视频下载失败（已尝试 {attempt} 次）：" + detail,
                )

            candidates = _downloaded_video_candidates(target_dir)
            if candidates:
                return candidates[0]
            if attempt < attempt_limit:
                _wait_before_download_retry(retry_delay_seconds, attempt)
                continue
            raise RhCliError(
                f"{error_code_prefix}_DOWNLOAD_EMPTY",
                f"{error_prefix}链接没有产生可用的视频文件（已尝试 {attempt} 次）。",
            )
    except RhCliError:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    finally:
        if temporary_cookie:
            try:
                temporary_cookie.unlink(missing_ok=True)
            except OSError:
                pass


def download_social_video(
    url: str,
    data_root: Path,
    cookie_dir: str | Path = DEFAULT_COOKIE_DIR,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
    max_attempts: int = DOWNLOAD_MAX_ATTEMPTS,
    retry_delay: float = DOWNLOAD_RETRY_DELAY_SECONDS,
) -> Path:
    normalized_url = normalize_social_video_url(url)
    platform = social_video_platform(normalized_url)
    cookie = cookie_file_for_platform(platform, cookie_dir)
    return _download_video(
        normalized_url,
        cookie,
        data_root,
        platform=platform,
        error_prefix=SOCIAL_PLATFORM_LABELS[platform],
        error_code_prefix="SOCIAL_VIDEO",
        timeout=timeout,
        max_attempts=max_attempts,
        retry_delay=retry_delay,
    )


def download_workflow_social_video(
    url: str,
    data_root: Path,
    cookie_path: str = "",
    cookie_dir: str | Path = DEFAULT_COOKIE_DIR,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
    max_attempts: int = DOWNLOAD_MAX_ATTEMPTS,
    retry_delay: float = DOWNLOAD_RETRY_DELAY_SECONDS,
) -> Path:
    """Download one social video for the task submission page.

    A manually configured Cookie file takes precedence. Otherwise, use a
    platform-matching Cookie from the shared auth folder when available, but
    still let yt-dlp try a public download when that folder or Cookie is
    missing.
    """
    normalized_url = normalize_social_video_url(url)
    platform = social_video_platform(normalized_url)
    normalized_cookie_path = str(cookie_path or "").strip()
    cookie: Path | None = None
    if normalized_cookie_path:
        cookie = Path(normalized_cookie_path).expanduser().resolve()
        if not cookie.is_file():
            raise RhCliError("SOCIAL_COOKIE_NOT_FOUND", f"Cookie 文件不存在：{cookie}")
    else:
        try:
            cookie = cookie_file_for_platform(platform, cookie_dir)
        except RhCliError as exc:
            if exc.code not in {"SOCIAL_COOKIE_DIR_NOT_FOUND", "SOCIAL_COOKIE_NOT_FOUND"}:
                raise

    return _download_video(
        normalized_url,
        cookie,
        data_root,
        platform=platform,
        error_prefix=SOCIAL_PLATFORM_LABELS[platform],
        error_code_prefix="SOCIAL_VIDEO",
        timeout=timeout,
        max_attempts=max_attempts,
        retry_delay=retry_delay,
    )


def download_douyin_video(
    url: str,
    cookie_path: str,
    data_root: Path,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
    max_attempts: int = DOWNLOAD_MAX_ATTEMPTS,
    retry_delay: float = DOWNLOAD_RETRY_DELAY_SECONDS,
) -> Path:
    normalized_url = normalize_douyin_url(url)
    normalized_cookie_path = str(cookie_path or "").strip()
    cookie = Path(normalized_cookie_path).expanduser().resolve() if normalized_cookie_path else None
    if cookie and not cookie.is_file():
        raise RhCliError("DOUYIN_COOKIE_NOT_FOUND", f"抖音 Cookie 文件不存在：{cookie}")
    return _download_video(
        normalized_url,
        cookie,
        data_root,
        platform="douyin",
        error_prefix="抖音",
        error_code_prefix="DOUYIN",
        timeout=timeout,
        max_attempts=max_attempts,
        retry_delay=retry_delay,
    )
