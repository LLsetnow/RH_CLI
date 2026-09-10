from __future__ import annotations

import base64
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from web.backend.action_store import ActionStore
from web.backend import server as web_server
from web.backend.reference_store import ReferenceStore
from web.backend.server import prepare_prompt_resource_body


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
                {"role": "skeleton", "name": "skeleton-map.png", "data_url": _data_url(b"skeleton")},
            ],
        },
        "action",
        root,
    )

    assert prepared["color_image_path"] == "pose/color/walk.png"
    assert prepared["depth_image_path"] == "pose/depth/walk_depth.png"
    assert prepared["skeleton_image_path"] == "pose/skeleton/walk_skeleton.png"
    assert (root / prepared["color_image_path"]).read_bytes() == b"color"
    assert (root / prepared["depth_image_path"]).read_bytes() == b"depth"
    assert (root / prepared["skeleton_image_path"]).read_bytes() == b"skeleton"

    source = root / "pose" / "pose.json"
    action = ActionStore(tmp_path / "data", source_root=root).add_action(prepared)
    content = json.loads(source.read_text(encoding="utf-8"))
    assert action["color_image_path"] == "pose/color/walk.png"
    assert content["actions"][0]["color_image_path"] == "pose/color/walk.png"
    assert content["actions"][0]["depth_image_path"] == "pose/depth/walk_depth.png"
    assert content["actions"][0]["skeleton_image_path"] == "pose/skeleton/walk_skeleton.png"


def test_prepare_prompt_action_video_media_copies_all_four_variants(tmp_path):
    root = tmp_path / "ref"
    root.mkdir()
    roles = {
        "video": "original.mp4",
        "depth_video": "depth.mp4",
        "skeleton_video": "skeleton.mp4",
        "depth_skeleton_video": "overlay.mp4",
    }

    prepared = prepare_prompt_resource_body(
        {
            "title": "视频动作",
            "text": "",
            "media_type": "video",
            "media": [
                {"role": role, "name": name, "mime": "video/mp4", "data_url": _data_url(role.encode(), "video/mp4")}
                for role, name in roles.items()
            ],
        },
        "action",
        root,
    )

    assert prepared["media_type"] == "video"
    expected = {
        "video_path": "pose/video/original.mp4",
        "depth_video_path": "pose/video-depth/original_depth.mp4",
        "skeleton_video_path": "pose/video-skeleton/original_skeleton.mp4",
        "depth_skeleton_video_path": "pose/video-depth-skeleton/original_depth_skeleton.mp4",
    }
    for field, relative in expected.items():
        assert prepared[field] == relative
        assert (root / relative).read_bytes() == {
            "video_path": b"video",
            "depth_video_path": b"depth_video",
            "skeleton_video_path": b"skeleton_video",
            "depth_skeleton_video_path": b"depth_skeleton_video",
        }[field]

    action = ActionStore(tmp_path / "data", source_root=root).add_action(prepared)
    assert action["media_type"] == "video"
    assert action["depth_skeleton_video_path"] == expected["depth_skeleton_video_path"]


def test_prepare_action_video_generation_body_saves_one_explicit_source_object(tmp_path):
    root = tmp_path / "ref"
    root.mkdir()
    prepared = web_server.prepare_action_video_generation_body(
        {
            "title": "视频动作",
            "text": "角色向前走。",
            "media_type": "video",
            "source": {"name": "walk.mp4", "mime": "video/mp4", "data": base64.b64encode(b"video").decode("ascii")},
            "start_frame": "48",
            "duration_seconds": "2.5",
        },
        root,
    )

    assert prepared["video_path"] == "pose/video/walk.mp4"
    assert "source" not in prepared
    assert "media" not in prepared
    assert (root / prepared["video_path"]).read_bytes() == b"video"


def test_prepare_prompt_reference_media_copies_into_kind_directory_and_updates_json(tmp_path):
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
    content = json.loads((root / "character" / "character.json").read_text(encoding="utf-8"))
    assert reference["image_path"] == "character/hero.webp"
    assert content["references"][0]["image_path"] == "character/hero.webp"


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


def test_generate_prompt_skeleton_uses_dwpose_runtime_and_cleans_up_temp_files(tmp_path, monkeypatch):
    root = tmp_path / "ref"
    root.mkdir()
    commands = []

    monkeypatch.setattr(web_server, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(
        web_server,
        "_skeleton_runtime_paths",
        lambda configured_root: (Path("/runtime/python"), Path("/videomake/tools/pose_skeleton_macos.py"), Path("/videomake/.runtime/pose_dwpose/checkpoints/dw-ll_ucoco_384.onnx")),
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[command.index("-o") + 1]).write_bytes(b"generated-skeleton")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(web_server.subprocess, "run", fake_run)
    result = web_server.generate_prompt_skeleton(
        {"source": {"name": "walk.jpg", "mime": "image/jpeg", "data": base64.b64encode(b"source").decode("ascii")}},
        root,
    )

    assert result["name"] == "walk_skeleton.png"
    assert base64.b64decode(result["data"]) == b"generated-skeleton"
    assert commands == [[
        "/runtime/python",
        "/videomake/tools/pose_skeleton_macos.py",
        commands[0][2],
        "-o",
        commands[0][4],
        "--model",
        "/videomake/.runtime/pose_dwpose/checkpoints/dw-ll_ucoco_384.onnx",
    ]]
    assert not list((tmp_path / "data" / "prompt").glob("skeleton-generation-*"))


def test_generate_prompt_video_stages_all_variants_with_24fps_controls(tmp_path, monkeypatch):
    root = tmp_path / "ref"
    source = tmp_path / "input.mov"
    root.mkdir()
    source.write_bytes(b"source-video")
    calls = []

    monkeypatch.setattr(web_server, "DATA_ROOT", tmp_path / "data")

    def fake_process_media_variants(modes, input_path, output_dir, configured_root, **kwargs):
        calls.append((set(modes), input_path, output_dir, configured_root, kwargs))
        output_dir.mkdir(parents=True, exist_ok=True)
        result = {}
        for mode in modes:
            output = output_dir / f"{mode}.mp4"
            output.write_bytes(mode.encode("ascii"))
            result[mode] = output
        return result

    monkeypatch.setattr(web_server, "process_media_variants", fake_process_media_variants)
    result = web_server.generate_prompt_video(
        {
            "source_path": str(source),
            "title": "走路动作",
            "resolution": "720p",
            "start_frame": "48",
            "duration_seconds": "2.5",
        },
        root,
    )

    assert result["fps"] == 24
    assert result["resolution"] == "720p"
    assert result["start_frame"] == 48
    assert result["duration_seconds"] == 2.5
    assert set(result["paths"]) == {
        "video_path",
        "depth_video_path",
        "skeleton_video_path",
        "depth_skeleton_video_path",
    }
    assert calls[0][0] == {"depth", "skeleton", "depth_skeleton"}
    assert calls[0][1] == source.resolve()
    assert calls[0][3] == root.resolve()
    assert calls[0][4]["start_frame"] == 48
    assert calls[0][4]["duration_seconds"] == 2.5
    assert calls[0][4]["resolution"] == "720p"
    for relative in result["paths"].values():
        assert (root / relative).is_file()
    assert (root / result["paths"]["video_path"]).read_bytes() == b"source-video"
    assert not list((tmp_path / "data" / "prompt").glob("video-generation-*"))


def test_background_action_video_generation_updates_saved_action_after_original_is_available(tmp_path, monkeypatch):
    root = tmp_path / "ref"
    original = root / "pose" / "video" / "walk.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    store = ActionStore(tmp_path / "data", source_root=root)
    action = store.add_action({
        "title": "走路动作",
        "text": "角色向前走。",
        "media_type": "video",
        "video_path": "pose/video/walk.mp4",
    })
    monkeypatch.setattr(web_server, "DATA_ROOT", tmp_path / "data")
    calls = []

    def fake_process_media_variants(modes, input_path, output_dir, configured_root, **kwargs):
        calls.append(kwargs)
        output_dir.mkdir(parents=True, exist_ok=True)
        result = {}
        for mode in modes:
            output = output_dir / f"{mode}.mp4"
            output.write_bytes(mode.encode("ascii"))
            result[mode] = output
        return result

    monkeypatch.setattr(web_server, "process_media_variants", fake_process_media_variants)
    manager = web_server.ToolboxManager.__new__(web_server.ToolboxManager)
    manager._action_video_jobs = {
        "job-1": {"id": "job-1", "action_id": action["id"], "status": "queued", "resolution": "480p"},
    }
    manager._action_video_jobs_lock = threading.Lock()
    manager._run_action_video_generation("job-1", store, action["id"], original, 48, 2.5, "480p")

    job = manager.action_video_job("job-1")
    public = store.public_actions()[0]
    assert job["status"] == "completed"
    assert public["pair_status"] == "video_ready"
    assert public["depth_video_available"] is True
    assert public["skeleton_video_available"] is True
    assert public["depth_skeleton_video_available"] is True
    assert calls[0]["resolution"] == "480p"
