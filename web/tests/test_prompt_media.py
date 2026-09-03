from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

from web.action_store import ActionStore
from web import server as web_server
from web.reference_store import ReferenceStore
from web.server import prepare_prompt_resource_body


def _data_url(value: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(value).decode('ascii')}"


def test_save_prompt_media_persists_browser_video_with_original_display_name(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, "DATA_ROOT", tmp_path / "data")

    saved = web_server.save_prompt_media({
        "name": "示例视频.mp4",
        "mime": "video/mp4",
        "data": base64.b64encode(b"video-bytes").decode("ascii"),
    })

    path = Path(saved["path"])
    assert path.is_file()
    assert path.parent == tmp_path / "data" / "prompt-media"
    assert path.read_bytes() == b"video-bytes"
    assert saved["display_name"] == "示例视频.mp4"
    assert saved["media_kind"] == "video"
    assert saved["preview_kind"] == "video"


def test_prepare_prompt_action_media_copies_paired_files_and_returns_relative_paths(tmp_path):
    root = tmp_path / "ref"
    root.mkdir()

    prepared = prepare_prompt_resource_body(
        {
            "title": "走路",
            "text": "角色向前走。",
            "media": [
                {"role": "color", "name": "walk.png", "data_url": _data_url(b"color")},
                {"role": "depth", "name": "depth-map.png", "data_url": _data_url(b"depth")},
            ],
        },
        "action",
        root,
    )

    assert prepared["color_image_path"] == "pose/color/walk.png"
    assert prepared["depth_image_path"] == "pose/depth/walk_depth.png"
    assert (root / prepared["color_image_path"]).read_bytes() == b"color"
    assert (root / prepared["depth_image_path"]).read_bytes() == b"depth"

    source = root / "pose" / "pose.md"
    action = ActionStore(tmp_path / "data", source_root=root).add_action(prepared)
    content = source.read_text(encoding="utf-8")
    assert action["color_image_path"] == "pose/color/walk.png"
    assert "![200](pose/color/walk.png)" in content
    assert "![200](pose/depth/walk_depth.png)" in content


def test_prepare_prompt_reference_media_copies_into_kind_directory_and_updates_markdown(tmp_path):
    root = tmp_path / "ref"
    root.mkdir()

    prepared = prepare_prompt_resource_body(
        {
            "kind": "character",
            "title": "新人物",
            "text": "人物参考",
            "media": [{"role": "image", "name": "hero.webp", "data_url": _data_url(b"hero", "image/webp")}],
        },
        "character",
        root,
    )

    assert prepared["image_path"] == "character/hero.webp"
    assert (root / "character" / "hero.webp").read_bytes() == b"hero"

    reference = ReferenceStore(tmp_path / "data", root).add_reference("character", prepared)
    content = (root / "character" / "character.md").read_text(encoding="utf-8")
    assert reference["image_path"] == "character/hero.webp"
    assert "![200](character/hero.webp)" in content


def test_prepare_prompt_media_rejects_a_wrong_slot_type(tmp_path):
    root = tmp_path / "ref"
    root.mkdir()

    try:
        prepare_prompt_resource_body(
            {"media": [{"role": "audio", "name": "voice.mp3", "data_url": _data_url(b"audio", "audio/mpeg")}]},
            "character",
            root,
        )
    except Exception as exc:
        assert "素材槽位" in str(exc)
    else:
        raise AssertionError("expected an invalid resource media slot")


def test_generate_prompt_depth_uses_videomake_script_and_cleans_up_temp_files(tmp_path, monkeypatch):
    root = tmp_path / "ref"
    root.mkdir()
    commands = []

    monkeypatch.setattr(web_server, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(
        web_server,
        "_depth_runtime_paths",
        lambda configured_root: (Path("/runtime/python"), Path("/videomake/tools/depth_anything_macos.py")),
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"generated-depth")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(web_server.subprocess, "run", fake_run)
    result = web_server.generate_prompt_depth(
        {"source": {"name": "walk.jpg", "mime": "image/jpeg", "data": base64.b64encode(b"source").decode("ascii")}},
        root,
    )

    assert result["name"] == "walk_depth.png"
    assert base64.b64decode(result["data"]) == b"generated-depth"
    assert commands == [[
        "/runtime/python",
        "/videomake/tools/depth_anything_macos.py",
        commands[0][2],
        "-o",
        commands[0][4],
    ]]
    assert not list((tmp_path / "data" / "prompt").glob("depth-generation-*"))
