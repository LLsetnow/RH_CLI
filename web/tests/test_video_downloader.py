from __future__ import annotations

import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from rh_cli.errors import RhCliError
from web import video_downloader


def test_normalize_douyin_url_accepts_short_and_standard_links():
    assert video_downloader.normalize_douyin_url("https://v.douyin.com/abc123/").startswith("https://")
    assert video_downloader.normalize_douyin_url("https://www.douyin.com/video/123") == "https://www.douyin.com/video/123"

    with pytest.raises(RhCliError) as excinfo:
        video_downloader.normalize_douyin_url("https://example.com/video/123")
    assert excinfo.value.code == "INVALID_DOUYIN_URL"


def test_social_video_urls_support_douyin_bilibili_and_x():
    assert video_downloader.social_video_platform("https://v.douyin.com/abc123/") == "douyin"
    assert video_downloader.social_video_platform("https://www.bilibili.com/video/BV1xx") == "bilibili"
    assert video_downloader.social_video_platform("https://b23.tv/abc123") == "bilibili"
    assert video_downloader.social_video_platform("https://x.com/user/status/123") == "x"
    assert video_downloader.social_video_platform("https://twitter.com/user/status/123") == "x"
    assert video_downloader.extract_social_video_url("请看这个链接：https://www.bilibili.com/video/BV1xx。") == "https://www.bilibili.com/video/BV1xx"
    assert video_downloader.extract_social_video_url(
        "[https://x.com/user/status/123/video/1?s=52](https://x.com/user/status/123/video/1?s=52)"
    ) == "https://x.com/user/status/123"
    assert video_downloader.extract_social_video_url(
        "5.33 复制打开抖音，看看【作品】 [https://v.douyin.com/abc123/](https://v.douyin.com/abc123/) 09/19"
    ) == "https://v.douyin.com/abc123/"
    assert video_downloader.normalize_social_video_url(
        "https://x.com/user/status/123/video/1?s=52"
    ) == "https://x.com/user/status/123"

    with pytest.raises(RhCliError) as excinfo:
        video_downloader.normalize_social_video_url("https://example.com/video/123")
    assert excinfo.value.code == "INVALID_SOCIAL_VIDEO_URL"


def test_cookie_file_for_platform_selects_matching_file(tmp_path):
    (tmp_path / "music.163.com_cookies.txt").write_text("", encoding="utf-8")
    bilibili = tmp_path / "www.bilibili.com_cookies.txt"
    bilibili.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    x_cookie = tmp_path / "x.com_cookies.textClipping"
    x_cookie.write_bytes(plistlib.dumps({"text": "# Netscape HTTP Cookie File\n"}))

    assert video_downloader.cookie_file_for_platform("bilibili", tmp_path) == bilibili.resolve()
    assert video_downloader.cookie_file_for_platform("x", tmp_path) == x_cookie.resolve()


def test_download_douyin_video_uses_cookie_file_and_returns_local_video(tmp_path, monkeypatch):
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(video_downloader, "yt_dlp_command", lambda: ["/usr/local/bin/yt-dlp"])

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        output_template = Path(command[command.index("--output") + 1])
        video = Path(str(output_template).replace("%(id)s", "video-123").replace("%(ext)s", "mp4"))
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video_downloader.subprocess, "run", fake_run)
    result = video_downloader.download_douyin_video(
        "https://v.douyin.com/abc123/",
        str(cookie),
        tmp_path / "data",
    )

    assert result.is_file()
    assert result.suffix == ".mp4"
    assert result.parent.parent.name == "downloaded-inputs"
    command, kwargs = calls[0]
    assert "--no-playlist" in command
    assert command[command.index("--cookies") + 1] == str(cookie.resolve())
    assert command[-1] == "https://v.douyin.com/abc123/"
    assert kwargs["timeout"] == video_downloader.DOWNLOAD_TIMEOUT_SECONDS


def test_download_social_video_uses_platform_cookie_directory(tmp_path, monkeypatch):
    auth = tmp_path / "auth"
    auth.mkdir()
    cookie = auth / "www.bilibili.com_cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(video_downloader, "yt_dlp_command", lambda: ["/usr/local/bin/yt-dlp"])

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        output_template = Path(command[command.index("--output") + 1])
        video = Path(str(output_template).replace("%(id)s", "video-456").replace("%(ext)s", "mp4"))
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video_downloader.subprocess, "run", fake_run)
    result = video_downloader.download_social_video(
        "https://www.bilibili.com/video/BV1xx",
        tmp_path / "data",
        cookie_dir=auth,
    )

    assert result.is_file()
    command, kwargs = calls[0]
    assert command[command.index("--cookies") + 1] == str(cookie.resolve())
    assert command[-1] == "https://www.bilibili.com/video/BV1xx"
    assert kwargs["timeout"] == video_downloader.DOWNLOAD_TIMEOUT_SECONDS


def test_download_social_video_retries_transient_failure_with_backoff(tmp_path, monkeypatch):
    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / "www.bilibili.com_cookies.txt").write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    calls = []
    sleeps = []

    monkeypatch.setattr(video_downloader, "yt_dlp_command", lambda: ["/usr/local/bin/yt-dlp"])
    monkeypatch.setattr(video_downloader.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: HTTP Error 503: Service Unavailable")
        output_template = Path(command[command.index("--output") + 1])
        video = Path(str(output_template).replace("%(id)s", "video-retry").replace("%(ext)s", "mp4"))
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video_downloader.subprocess, "run", fake_run)
    result = video_downloader.download_social_video(
        "https://www.bilibili.com/video/BV1xx",
        tmp_path / "data",
        cookie_dir=auth,
        max_attempts=3,
        retry_delay=0.25,
    )

    assert result.is_file()
    assert len(calls) == 2
    assert sleeps == [0.25]


def test_download_social_video_does_not_retry_permanent_failure(tmp_path, monkeypatch):
    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / "www.bilibili.com_cookies.txt").write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    calls = []
    sleeps = []

    monkeypatch.setattr(video_downloader, "yt_dlp_command", lambda: ["/usr/local/bin/yt-dlp"])
    monkeypatch.setattr(video_downloader.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: [BiliBili] Video unavailable")

    monkeypatch.setattr(video_downloader.subprocess, "run", fake_run)
    with pytest.raises(RhCliError) as excinfo:
        video_downloader.download_social_video(
            "https://www.bilibili.com/video/BV1xx",
            tmp_path / "data",
            cookie_dir=auth,
            max_attempts=3,
            retry_delay=0,
        )

    assert excinfo.value.code == "SOCIAL_VIDEO_DOWNLOAD_FAILED"
    assert len(calls) == 1
    assert sleeps == []


def test_download_social_video_converts_textclipping_cookie_temporarily(tmp_path, monkeypatch):
    auth = tmp_path / "auth"
    auth.mkdir()
    source = auth / "x.com_cookies.textClipping"
    source.write_bytes(plistlib.dumps({"nested": {"text": "# Netscape HTTP Cookie File\n"}}))
    cookie_arguments = []

    monkeypatch.setattr(video_downloader, "yt_dlp_command", lambda: ["/usr/local/bin/yt-dlp"])

    def fake_run(command, **kwargs):
        cookie_arguments.append(Path(command[command.index("--cookies") + 1]))
        output_template = Path(command[command.index("--output") + 1])
        video = Path(str(output_template).replace("%(id)s", "video-789").replace("%(ext)s", "mp4"))
        video.write_bytes(b"video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video_downloader.subprocess, "run", fake_run)
    result = video_downloader.download_social_video(
        "https://x.com/user/status/789",
        tmp_path / "data",
        cookie_dir=auth,
    )

    assert result.is_file()
    assert cookie_arguments[0] != source
    assert cookie_arguments[0].exists() is False


def test_download_douyin_video_rejects_missing_cookie_before_starting_process(tmp_path, monkeypatch):
    monkeypatch.setattr(video_downloader.subprocess, "run", lambda *args, **kwargs: pytest.fail("不应启动下载进程"))

    with pytest.raises(RhCliError) as excinfo:
        video_downloader.download_douyin_video(
            "https://www.douyin.com/video/123",
            str(tmp_path / "missing-cookies.txt"),
            tmp_path / "data",
        )
    assert excinfo.value.code == "DOUYIN_COOKIE_NOT_FOUND"


def test_download_social_video_rejects_missing_platform_cookie(tmp_path, monkeypatch):
    monkeypatch.setattr(video_downloader.subprocess, "run", lambda *args, **kwargs: pytest.fail("不应启动下载进程"))

    with pytest.raises(RhCliError) as excinfo:
        video_downloader.download_social_video(
            "https://x.com/user/status/123",
            tmp_path / "data",
            cookie_dir=tmp_path / "auth",
        )
    assert excinfo.value.code == "SOCIAL_COOKIE_DIR_NOT_FOUND"


def test_download_workflow_social_video_can_try_public_url_without_cookie(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(video_downloader, "yt_dlp_command", lambda: ["/usr/local/bin/yt-dlp"])

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        output_template = Path(command[command.index("--output") + 1])
        video = Path(str(output_template).replace("%(id)s", "public-123").replace("%(ext)s", "mp4"))
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video_downloader.subprocess, "run", fake_run)
    result = video_downloader.download_workflow_social_video(
        "https://www.bilibili.com/video/BV1public",
        tmp_path / "data",
        cookie_dir=tmp_path / "missing-auth",
    )

    assert result.is_file()
    assert "--cookies" not in calls[0][0]
    assert calls[0][0][-1] == "https://www.bilibili.com/video/BV1public"
