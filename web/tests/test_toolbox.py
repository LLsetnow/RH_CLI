import subprocess
import sys
import time

import pytest

from web import toolbox as toolbox_module
from web import server as web_server
from web.toolbox import (
    DEFAULT_CODEX_IMAGE_COMMAND,
    _run_video_stage,
    _video_progress_message,
    expand_command_template,
    normalize_codex_image_resolution,
    normalize_codex_image_size,
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


def test_toolbox_mode_validation_is_explicit():
    assert normalize_toolbox_mode("depth_skeleton") == "depth_skeleton"
    with pytest.raises(RhCliError):
        normalize_toolbox_mode("depth+pose")


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

    task = manager.submit_image({"prompt": "一只猫", "resolution": "2K", "size": "16:9"})

    assert task["workflow_name"] == "Codex 图像生成"
    assert task["task_type"] == "toolbox"
    assert task["custom_inputs"]["resolution"] == "2k"
    assert task["custom_inputs"]["aspect_ratio"] == "16:9"
    assert manager._executor.calls[0][1][5:7] == ("2k", "16:9")


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
