from __future__ import annotations

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


def test_download_douyin_video_rejects_missing_cookie_before_starting_process(tmp_path, monkeypatch):
    monkeypatch.setattr(video_downloader.subprocess, "run", lambda *args, **kwargs: pytest.fail("不应启动下载进程"))

    with pytest.raises(RhCliError) as excinfo:
        video_downloader.download_douyin_video(
            "https://www.douyin.com/video/123",
            str(tmp_path / "missing-cookies.txt"),
            tmp_path / "data",
        )
    assert excinfo.value.code == "DOUYIN_COOKIE_NOT_FOUND"
