import json
import subprocess
import sys
import time

import pytest

from web.backend import toolbox as toolbox_module
from web.backend import server as web_server
from web.backend import tts as tts_module
from web.backend.toolbox import (
    DEFAULT_CODEX_IMAGE_MODEL,
    DEFAULT_CODEX_IMAGE_COMMAND,
    _run_video_stage,
    _video_progress_message,
    expand_command_template,
    normalize_codex_image_resolution,
    normalize_codex_image_model,
    normalize_codex_image_size,
    normalize_media_duration,
    normalize_media_resolution,
    normalize_media_start_frame,
    normalize_toolbox_mode,
)
from rh_cli.errors import RhCliError


def test_command_template_expands_zero_or_many_references_as_argv_values():
    template = "codex-image --prompt {prompt} --references {references} --output {output}"
    context = {"prompt": "一只蓝色的猫", "output": "/tmp/result.png", "references": []}
    assert expand_command_template(template, context) == [
        "codex-image", "--prompt", "一只蓝色的猫", "--output", "/tmp/result.png",
    ]
    context["references"] = ["/tmp/a.png", "/tmp/b.png"]
    assert expand_command_template(template, context)[4:7] == ["/tmp/a.png", "/tmp/b.png", "--output"]


def test_command_template_is_shell_free_and_requires_prompt_and_output():
    with pytest.raises(RhCliError, match="必须包含"):
        expand_command_template("codex-image --prompt {prompt}", {"prompt": "x"})
    assert expand_command_template("codex-image --prompt {prompt} --output {output}", {"prompt": "a && b", "output": "/tmp/x.png"})[-2:] == ["--output", "/tmp/x.png"]


def test_internal_codex_command_builds_optional_repeated_reference_flags():
    context = {"prompt": "一只猫", "output": "/tmp/result.png", "references": [], "resolution": "1k", "size": "9:16"}
    assert "{prompt}" in DEFAULT_CODEX_IMAGE_COMMAND
    assert "{output}" in DEFAULT_CODEX_IMAGE_COMMAND
    assert "{resolution}" in DEFAULT_CODEX_IMAGE_COMMAND
    assert "{size}" in DEFAULT_CODEX_IMAGE_COMMAND
    assert expand_command_template(DEFAULT_CODEX_IMAGE_COMMAND, context) == [
        "opc", "image", "generate", "一只猫", "--engine", "gpt-image", "--resolution", "1k", "--size", "9:16", "--output", "/tmp/result.png", "--no-enhance",
    ]
    context["references"] = ["/tmp/a.png", "/tmp/b.png"]
    assert expand_command_template(DEFAULT_CODEX_IMAGE_COMMAND, context)[-4:] == [
        "--ref", "/tmp/a.png", "--ref", "/tmp/b.png",
    ]


def test_codex_image_model_defaults_and_validates_supported_values():
    assert DEFAULT_CODEX_IMAGE_MODEL == "gpt-image-2.5-flare"
    assert normalize_codex_image_model(None) == DEFAULT_CODEX_IMAGE_MODEL
    assert normalize_codex_image_model("GPT-IMAGE-2.5-SUNBURST") == "gpt-image-2.5-sunburst"
    with pytest.raises(RhCliError, match="图像模型"):
        normalize_codex_image_model("gpt-image-2")


def test_toolbox_mode_validation_is_explicit():
    assert normalize_toolbox_mode("depth_skeleton") == "depth_skeleton"
    with pytest.raises(RhCliError):
        normalize_toolbox_mode("depth+pose")


def test_media_resolution_validation_is_explicit():
    assert [normalize_media_resolution(value) for value in ("original", "480p", "720p", "1080p")] == [
        "original", "480p", "720p", "1080p",
    ]
    with pytest.raises(RhCliError, match="媒体分辨率"):
        normalize_media_resolution("4k")


def test_media_duration_validation_accepts_optional_positive_seconds():
    assert normalize_media_duration(None) is None
    assert normalize_media_duration("") is None
    assert normalize_media_duration("2.5") == 2.5
    with pytest.raises(RhCliError, match="处理时长"):
        normalize_media_duration("0")
    with pytest.raises(RhCliError, match="处理时长"):
        normalize_media_duration("not-a-number")


def test_media_start_frame_validation_accepts_zero_based_integer_frames():
    assert normalize_media_start_frame(None) == 0
    assert normalize_media_start_frame(0) == 0
    assert normalize_media_start_frame("48") == 48
    with pytest.raises(RhCliError, match="起始帧"):
        normalize_media_start_frame("-1")
    with pytest.raises(RhCliError, match="起始帧"):
        normalize_media_start_frame("1.5")
    with pytest.raises(RhCliError, match="起始帧"):
        normalize_media_start_frame("not-a-frame")


@pytest.mark.parametrize("suffix", [".mp4", ".png"])
def test_target_media_resolution_creates_a_working_copy_before_inference(monkeypatch, tmp_path, suffix):
    source = tmp_path / f"source{suffix}"
    source.write_bytes(b"source")
    output_dir = tmp_path / "task"
    output_dir.mkdir()
    commands = []
    progress = []

    monkeypatch.setattr(toolbox_module.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None)

    def fake_run_checked(command, *, label, timeout=3600):
        commands.append((command, label, timeout))
        output = toolbox_module.Path(command[-1])
        output.write_bytes(b"resized")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(toolbox_module, "_run_checked", fake_run_checked)
    result = toolbox_module._prepare_processing_source(source, output_dir, "480p", progress.append)

    assert result.is_file()
    expected_name = "processing_input_480p_24fps.mp4" if suffix == ".mp4" else "processing_input_480p.png"
    assert result.name == expected_name
    assert progress == ["正在先缩放输入到 480p…"]
    assert len(commands) == 1
    video_filter = commands[0][0][commands[0][0].index("-vf") + 1]
    assert ("fps=24" in video_filter) is (suffix == ".mp4")
    assert "480" in video_filter
    assert commands[0][0][-1] == str(result)


def test_target_media_duration_is_clamped_to_actual_video_length(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output_dir = tmp_path / "task"
    output_dir.mkdir()
    commands = []
    progress = []

    monkeypatch.setattr(toolbox_module.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(toolbox_module, "_probe_duration", lambda path: 2.25)

    def fake_run_checked(command, *, label, timeout=3600):
        commands.append((command, label, timeout))
        output = toolbox_module.Path(command[-1])
        output.write_bytes(b"trimmed")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(toolbox_module, "_run_checked", fake_run_checked)
    result = toolbox_module._prepare_processing_source(source, output_dir, "original", progress.append, duration_seconds=5)

    assert result.name == "processing_input_original_24fps.mp4"
    assert progress == ["正在准备视频第 0 帧起，处理 2.25 秒（统一 24 帧/秒）…"]
    command = commands[0][0]
    assert "-t" not in command
    assert command[command.index("-vf") + 1] == "fps=24,trim=start_frame=0:end_frame=54,setpts=PTS-STARTPTS"
    assert commands[0][1] == "输入视频预处理并统一为 24 帧/秒（第 0 帧起，2.25 秒）"


def test_target_media_start_frame_and_duration_are_trimmed_on_the_24fps_timeline(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output_dir = tmp_path / "task"
    output_dir.mkdir()
    commands = []

    monkeypatch.setattr(toolbox_module.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(toolbox_module, "_probe_duration", lambda path: 5.0)

    def fake_run_checked(command, *, label, timeout=3600):
        commands.append((command, label, timeout))
        output = toolbox_module.Path(command[-1])
        output.write_bytes(b"trimmed")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(toolbox_module, "_run_checked", fake_run_checked)
    result = toolbox_module._prepare_processing_source(
        source,
        output_dir,
        "original",
        duration_seconds=2,
        start_frame=48,
    )

    assert result.name == "processing_input_original_24fps.mp4"
    command = commands[0][0]
    assert command[command.index("-vf") + 1] == "fps=24,trim=start_frame=48:end_frame=96,setpts=PTS-STARTPTS"
    assert commands[0][1] == "输入视频预处理并统一为 24 帧/秒（第 48 帧起，2 秒）"


def test_target_media_start_frame_rejects_a_frame_after_the_video(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output_dir = tmp_path / "task"
    output_dir.mkdir()
    monkeypatch.setattr(toolbox_module.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(toolbox_module, "_probe_duration", lambda path: 2.0)

    with pytest.raises(RhCliError, match="超出视频范围"):
        toolbox_module._prepare_processing_source(source, output_dir, "original", start_frame=48)


def test_video_progress_reports_frames_speed_and_eta():
    message = _video_progress_message("正在生成深度图", 56, 120, 28)
    assert "56/120 帧" in message
    assert "2.00 帧/秒" in message
    assert "预计剩余 32 秒" in message
    assert _video_progress_message("正在生成深度图", 0, 120, 0).endswith("预计剩余 计算中")
    assert _video_progress_message("正在生成深度图", 120, 120, 60).endswith("预计剩余 完成")


def test_video_stage_monitor_reports_written_frame_progress(monkeypatch, tmp_path):
    output_dir = tmp_path / "depth"
    output_dir.mkdir()
    seen = []

    def fake_run_checked(command, *, label, timeout):
        for index in range(1, 3):
            (output_dir / f"frame_{index:06d}.png").write_bytes(b"frame")
            time.sleep(0.3)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(toolbox_module, "_run_checked", fake_run_checked)
    result = _run_video_stage(
        ["fake-depth-batch"],
        label="正在生成深度图",
        output_dir=output_dir,
        output_pattern="frame_*.png",
        total_frames=2,
        progress=seen.append,
    )

    assert result.returncode == 0
    assert seen[0].startswith("正在生成深度图（0/2 帧）")
    assert any("2/2 帧" in message and "速度" in message and "预计剩余 完成" in message for message in seen)


def test_video_variants_share_one_frame_pass_and_encode_three_24fps_outputs(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    root = tmp_path / "ref"
    root.mkdir()
    commands = []

    monkeypatch.setattr(toolbox_module.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(toolbox_module, "_depth_runtime_paths", lambda configured_root: (tmp_path / "depth-python", tmp_path / "depth-script", tmp_path / "depth-batch"))
    monkeypatch.setattr(toolbox_module, "_skeleton_runtime_paths", lambda configured_root: (tmp_path / "skeleton-python", tmp_path / "skeleton-script", tmp_path / "skeleton-model", tmp_path / "skeleton-detector"))
    monkeypatch.setattr(toolbox_module, "_combine_images", lambda depth, skeleton, output: output.write_bytes(b"combined"))

    def fake_run_checked(command, *, label, timeout=3600):
        commands.append(command)
        if "%06d" in str(command[-1]):
            toolbox_module.Path(command[-1].replace("%06d", "000001")).write_bytes(b"frame")
        elif "--output-dir" in command:
            directory = toolbox_module.Path(command[command.index("--output-dir") + 1])
            directory.mkdir(parents=True, exist_ok=True)
            if "--model" in command:
                (directory / "frame_000001_skeleton.png").write_bytes(b"skeleton")
            else:
                (directory / "frame_000001.png").write_bytes(b"depth")
        else:
            toolbox_module.Path(command[-1]).write_bytes(b"encoded")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(toolbox_module, "_run_checked", fake_run_checked)
    result = toolbox_module._run_video_processors(
        {"depth", "skeleton", "depth_skeleton"},
        source,
        output_dir,
        root,
    )

    assert set(result) == {"depth", "skeleton", "depth_skeleton"}
    assert all(path.is_file() for path in result.values())
    extraction_commands = [command for command in commands if "%06d" in str(command[-1])]
    encoding_commands = [command for command in commands if str(command[-1]).endswith(".part.mp4")]
    assert len(extraction_commands) == 1
    assert len(encoding_commands) == 3
    assert all(command[command.index("-framerate") + 1] == "24.000000" for command in encoding_commands)


def test_codex_image_canvas_defaults_and_validates_supported_values():
    assert normalize_codex_image_resolution(None) == "1k"
    assert normalize_codex_image_size(None) == "9:16"
    assert normalize_codex_image_resolution("4K") == "4k"
    assert normalize_codex_image_size("21:9") == "21:9"
    with pytest.raises(RhCliError):
        normalize_codex_image_resolution("8k")
    with pytest.raises(RhCliError):
        normalize_codex_image_size("7:5")


def test_submit_image_persists_canvas_options_and_passes_them_to_the_runner(tmp_path):
    class FakeStore:
        def __init__(self):
            self.tasks = {}

        def output_dir(self):
            return str(tmp_path / "output")

        def create_task(self, task):
            self.tasks[task["id"]] = dict(task)

        def update_task(self, task_id, **updates):
            self.tasks[task_id].update(updates)

        def task(self, task_id):
            return self.tasks.get(task_id)

        def append_stage_log(self, *_args, **_kwargs):
            return None

    class FakeExecutor:
        def __init__(self):
            self.calls = []

        def submit(self, function, *args):
            self.calls.append((function, args))

    manager = web_server.ToolboxManager.__new__(web_server.ToolboxManager)
    manager.store = FakeStore()
    manager._executor = FakeExecutor()

    task = manager.submit_image({"prompt": "一只猫", "model": "gpt-image-2.5-sunburst", "resolution": "2K", "size": "16:9"})

    assert task["workflow_name"] == "Codex 图像生成"
    assert task["task_type"] == "toolbox"
    assert task["custom_inputs"]["resolution"] == "2k"
    assert task["custom_inputs"]["model"] == "gpt-image-2.5-sunburst"
    assert task["custom_inputs"]["aspect_ratio"] == "16:9"
    assert manager._executor.calls[0][1][5:7] == ("2k", "16:9")


def test_discover_tts_voices_requires_the_matching_local_asset_set(tmp_path):
    voice_dir = tmp_path / "千夏"
    reference_dir = voice_dir / "reference"
    reference_dir.mkdir(parents=True)
    (voice_dir / "voice.ckpt").write_bytes(b"gpt")
    (voice_dir / "voice.pth").write_bytes(b"sovits")
    (reference_dir / "sample.wav").write_bytes(b"wav")
    (reference_dir / "参考文本.txt").write_text("参考台词", encoding="utf-8")
    incomplete = tmp_path / "缺少模型" / "reference"
    incomplete.mkdir(parents=True)
    (incomplete / "参考文本.txt").write_text("不完整", encoding="utf-8")

    voices = tts_module.discover_tts_voices(tmp_path)

    assert [voice["id"] for voice in voices] == ["千夏"]
    assert voices[0]["prompt_text"] == "参考台词"
    assert voices[0]["reference_path"].endswith("千夏/reference/sample.wav")


def test_discover_tts_voices_uses_the_resources_index(tmp_path):
    root = tmp_path / "ref"
    voice_dir = root / "tts" / "角色"
    reference_dir = voice_dir / "reference"
    reference_dir.mkdir(parents=True)
    (voice_dir / "voice.ckpt").write_bytes(b"gpt")
    (voice_dir / "voice.pth").write_bytes(b"sovits")
    (reference_dir / "sample.wav").write_bytes(b"wav")
    (reference_dir / "参考文本.txt").write_text("参考台词", encoding="utf-8")
    index = root / "Resources.json"
    index.write_text(json.dumps({"media_root": ".", "sources": {"tts": "tts"}}, ensure_ascii=False), encoding="utf-8")

    voices = tts_module.discover_tts_voices(resources_index_path=index)

    assert [voice["id"] for voice in voices] == ["角色"]
    assert voices[0]["reference_path"].endswith("角色/reference/sample.wav")


def test_tts_client_switches_model_and_sends_selected_reference(monkeypatch, tmp_path):
    voice_dir = tmp_path / "角色"
    reference_dir = voice_dir / "reference"
    reference_dir.mkdir(parents=True)
    gpt = voice_dir / "voice.ckpt"
    sovits = voice_dir / "voice.pth"
    reference = reference_dir / "sample.wav"
    gpt.write_bytes(b"gpt")
    sovits.write_bytes(b"sovits")
    reference.write_bytes(b"wav")
    (reference_dir / "参考文本.txt").write_text("参考台词", encoding="utf-8")

    class FakeResponse:
        def __init__(self, status_code, payload=None, content=b"", content_type="application/json"):
            self.status_code = status_code
            self._payload = payload
            self.content = content
            self.headers = {"content-type": content_type}

        def json(self):
            return self._payload

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/set_model"):
            return FakeResponse(200, {"code": 0})
        return FakeResponse(200, content=b"RIFFfake-wav", content_type="audio/wav")

    monkeypatch.setattr(tts_module.httpx, "post", fake_post)
    client = tts_module.TtsClient(tmp_path, api_url="http://127.0.0.1:9889")

    audio, voice = client.synthesize("角色", "你好，世界。")

    assert audio.startswith(b"RIFF")
    assert voice["id"] == "角色"
    assert calls[0][0].endswith("/set_model")
    assert calls[0][1]["json"]["gpt_model_path"] == str(gpt.resolve())
    assert calls[1][0].endswith("/")
    assert calls[1][1]["json"]["refer_wav_path"] == str(reference.resolve())
    assert calls[1][1]["json"]["prompt_text"] == "参考台词"
    assert calls[1][1]["json"]["text"] == "你好，世界。"


def test_submit_tts_creates_a_replayable_toolbox_task(tmp_path):
    class FakeStore:
        def __init__(self):
            self.tasks = {}

        def output_dir(self):
            return str(tmp_path / "output")

        def create_task(self, task):
            self.tasks[task["id"]] = dict(task)

        def update_task(self, task_id, **updates):
            self.tasks[task_id].update(updates)

        def task(self, task_id):
            return self.tasks.get(task_id)

        def append_stage_log(self, *_args, **_kwargs):
            return None

    class FakeTts:
        def voice(self, voice_id):
            assert voice_id == "千夏"
            return {
                "id": "千夏",
                "name": "千夏",
                "reference_path": "/voices/千夏/reference.wav",
                "prompt_text": "参考台词",
            }

    class FakeExecutor:
        def __init__(self):
            self.calls = []

        def submit(self, function, *args):
            self.calls.append((function, args))

    manager = web_server.ToolboxManager.__new__(web_server.ToolboxManager)
    manager.store = FakeStore()
    manager._tts = FakeTts()
    manager._executor = FakeExecutor()

    task = manager.submit_tts({"voice": "千夏", "text": "你好。"})

    assert task["workflow_name"] == "千夏 语音生成"
    assert task["local_workflow_id"] == "toolbox.tts"
    assert task["custom_inputs"]["voice"] == "千夏"
    assert task["prompts"] == {"text": "你好。", "reference_text": "参考台词"}
    assert manager._executor.calls[0][1][2:4] == ("千夏", "你好。")


def test_run_image_persists_codex_cli_session_result_in_stage_logs(monkeypatch, tmp_path):
    class FakeStore:
        def __init__(self):
            self.logs = []
            self.updates = []

        def update_task(self, task_id, **updates):
            self.updates.append((task_id, updates))

        def append_stage_log(self, task_id, stage, message, **kwargs):
            self.logs.append((task_id, stage, message, kwargs))

    result = subprocess.CompletedProcess(
        ["opc", "image", "generate"],
        0,
        stdout="生成会话完成\nrequest_id=req_1234567890",
        stderr="",
    )

    def fake_run_local_command(*args, on_result=None, **kwargs):
        if on_result is not None:
            on_result(result)
        return result

    output = tmp_path / "result.png"
    output.write_bytes(b"png")
    monkeypatch.setattr(web_server, "run_local_command", fake_run_local_command)
    monkeypatch.setattr(web_server, "find_generated_media", lambda folder: [output])

    manager = web_server.ToolboxManager.__new__(web_server.ToolboxManager)
    manager.store = FakeStore()
    manager._run_image("task_test", tmp_path, "opc image generate {prompt} --output {output}", "一只猫", [], "1k", "9:16", 0)

    cli_logs = [entry for entry in manager.store.logs if "Codex CLI 会话返回" in entry[2]]
    assert len(cli_logs) == 1
    task_id, stage, message, kwargs = cli_logs[0]
    assert task_id == "task_test"
    assert stage == "toolbox"
    assert "退出码 0" in message
    assert "生成会话完成" in message
    assert kwargs["detail"] == {"returncode": 0, "stdout": result.stdout, "stderr": ""}


def test_run_media_persists_live_progress_without_repeating_phase_logs(monkeypatch, tmp_path):
    class FakeStore:
        def __init__(self):
            self.updates = []
            self.logs = []

        def update_task(self, task_id, **updates):
            self.updates.append((task_id, updates))

        def append_stage_log(self, task_id, stage, message, **kwargs):
            self.logs.append((task_id, stage, message, kwargs))

        def media_library_root(self):
            return str(tmp_path)

    output = tmp_path / "depth.mp4"
    output.write_bytes(b"video")

    def fake_process_media(*args, progress=None, **kwargs):
        progress("正在生成深度图（0/2 帧） · 速度 计算中 · 预计剩余 计算中")
        progress("正在生成深度图（1/2 帧） · 速度 1.00 帧/秒 · 预计剩余 1 秒")
        progress("正在生成深度图（2/2 帧） · 速度 1.00 帧/秒 · 预计剩余 完成")
        return output

    monkeypatch.setattr(web_server, "process_media", fake_process_media)
    manager = web_server.ToolboxManager.__new__(web_server.ToolboxManager)
    manager.store = FakeStore()
    manager._run_media("task_media", tmp_path, tmp_path / "source.mp4", "depth", 0)

    progress_updates = [updates["progress"] for _, updates in manager.store.updates if "progress" in updates]
    assert progress_updates == [
        "正在生成深度图（0/2 帧） · 速度 计算中 · 预计剩余 计算中",
        "正在生成深度图（1/2 帧） · 速度 1.00 帧/秒 · 预计剩余 1 秒",
        "正在生成深度图（2/2 帧） · 速度 1.00 帧/秒 · 预计剩余 完成",
        "已完成 · 1 个产物",
    ]
    phase_logs = [message for _, stage, message, _ in manager.store.logs if stage == "toolbox" and message.startswith("正在生成深度图")]
    assert phase_logs == [progress_updates[0]]


def test_run_local_command_reports_completed_process_before_raising(monkeypatch, tmp_path):
    result = subprocess.CompletedProcess(
        ["fake-codex"],
        1,
        stdout="stdout result",
        stderr="stderr result",
    )
    monkeypatch.setattr(toolbox_module.subprocess, "run", lambda *args, **kwargs: result)
    observed = []

    with pytest.raises(RhCliError, match="本地 Codex 命令失败"):
        toolbox_module.run_local_command(
            "fake-codex --prompt {prompt} --output {output}",
            {"prompt": "一只猫", "output": str(tmp_path / "result.png")},
            cwd=tmp_path,
            on_result=observed.append,
        )

    assert observed == [result]


def test_image_magick_fallback_forces_color_output(monkeypatch, tmp_path):
    depth = tmp_path / "depth.png"
    skeleton = tmp_path / "skeleton.png"
    output = tmp_path / "depth_skeleton.png"
    depth.write_bytes(b"depth")
    skeleton.write_bytes(b"skeleton")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "-format" in command:
            return subprocess.CompletedProcess(command, 0, stdout="128x128", stderr="")
        output.write_bytes(b"color png")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setattr(toolbox_module.shutil, "which", lambda name: "/opt/homebrew/bin/magick" if name == "magick" else None)
    monkeypatch.setattr(toolbox_module.subprocess, "run", fake_run)

    toolbox_module._combine_images(depth, skeleton, output)

    assert output.read_bytes() == b"color png"
    compose_command = calls[-1]
    assert compose_command.count("-colorspace") == 3
    assert compose_command[-3:-1] == ["-type", "TrueColor"]
