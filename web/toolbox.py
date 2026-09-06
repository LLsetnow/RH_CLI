from __future__ import annotations

import json
import mimetypes
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from rh_cli.errors import RhCliError


VIDEO_SUFFIXES = {".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".webm", ".wmv"}
IMAGE_SUFFIXES = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
TOOLBOX_MODES = {"depth", "skeleton", "depth_skeleton"}
SUPPORTED_OUTPUT_SUFFIXES = VIDEO_SUFFIXES | IMAGE_SUFFIXES
CODEX_IMAGE_RESOLUTIONS = {"1k", "2k", "4k"}
CODEX_IMAGE_SIZES = {
    "auto",
    "1:1",
    "3:2",
    "2:3",
    "4:3",
    "3:4",
    "5:4",
    "4:5",
    "16:9",
    "9:16",
    "2:1",
    "1:2",
    "3:1",
    "1:3",
    "21:9",
    "9:21",
}
DEFAULT_RUNTIME_ROOT = Path("/Users/apple/Documents/VideoMake/ref")
DEFAULT_CODEX_IMAGE_COMMAND = (
    "opc image generate {prompt} --engine gpt-image --resolution {resolution} --size {size} --output {output} "
    "--no-enhance {reference_args}"
)


def default_codex_image_command() -> str:
    """Return the internal image command; users do not configure this in the UI."""
    return str(os.environ.get("RH_CODEX_IMAGE_COMMAND") or DEFAULT_CODEX_IMAGE_COMMAND).strip()


def normalize_toolbox_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in TOOLBOX_MODES:
        raise RhCliError("TOOLBOX_MODE_INVALID", "处理类型只能是深度图、骨骼图或深度+骨骼图。")
    return mode


def normalize_codex_image_resolution(value: Any) -> str:
    resolution = str(value or "1k").strip().lower()
    if resolution not in CODEX_IMAGE_RESOLUTIONS:
        raise RhCliError("TOOLBOX_IMAGE_RESOLUTION_INVALID", "图像分辨率只能是 1k、2k 或 4k。")
    return resolution


def normalize_codex_image_size(value: Any) -> str:
    size = str(value or "9:16").strip().lower()
    if size not in CODEX_IMAGE_SIZES:
        raise RhCliError("TOOLBOX_IMAGE_SIZE_INVALID", "图像画幅比例不受支持。")
    return size


def is_video_path(path: str | Path) -> bool:
    value = Path(str(path)).suffix.lower()
    if value in VIDEO_SUFFIXES:
        return True
    return str(mimetypes.guess_type(str(path))[0] or "").startswith("video/")


def validate_local_file(value: Any, *, label: str, suffixes: set[str] | None = None) -> Path:
    path = Path(str(value or "")).expanduser().resolve()
    if not path.is_file():
        raise RhCliError("FILE_NOT_FOUND", f"{label}不存在：{path}")
    if suffixes and path.suffix.lower() not in suffixes:
        raise RhCliError("TOOLBOX_MEDIA_INVALID", f"{label}不是支持的媒体格式：{path.name}")
    return path


def expand_command_template(template: str, context: dict[str, Any]) -> list[str]:
    """Expand a local CLI template without invoking a shell.

    Exact ``{references}`` expands to one argument per reference. All other
    placeholders are substituted inside their containing argument. This keeps
    prompts and file paths as single argv values while still allowing a normal
    command-line template such as ``tool --prompt {prompt} --out {output}``.
    """
    raw = str(template or "").strip()
    if not raw:
        raise RhCliError("TOOLBOX_COMMAND_MISSING", "请先填写本地 Codex 命令模板。")
    try:
        tokens = shlex.split(raw, posix=os.name != "nt")
    except ValueError as exc:
        raise RhCliError("TOOLBOX_COMMAND_INVALID", f"命令模板无法解析：{exc}") from exc
    if not tokens:
        raise RhCliError("TOOLBOX_COMMAND_MISSING", "请先填写本地 Codex 命令模板。")
    if "{prompt}" not in raw or "{output}" not in raw:
        raise RhCliError("TOOLBOX_COMMAND_INVALID", "命令模板必须包含 {prompt} 和 {output}。")

    references = [str(item) for item in context.get("references", []) if str(item).strip()]
    scalar_values = {
        "prompt": str(context.get("prompt") or ""),
        "output": str(context.get("output") or ""),
        "input": str(context.get("input") or ""),
        "mode": str(context.get("mode") or ""),
        "resolution": str(context.get("resolution") or "1k"),
        "size": str(context.get("size") or "9:16"),
        "references_json": json.dumps(references, ensure_ascii=False),
    }
    expanded: list[str] = []
    for token in tokens:
        if token == "{reference_args}":
            for reference in references:
                expanded.extend(["--ref", reference])
            continue
        if token == "{references}":
            if not references and expanded and expanded[-1] in {
                "-r",
                "--image",
                "--images",
                "--reference",
                "--references",
                "--refs",
                "--ref",
            }:
                # A template such as ``--references {references}`` should not
                # leave a dangling flag when the user intentionally supplied no
                # reference images.
                expanded.pop()
            expanded.extend(references)
            continue
        value = token
        for name, replacement in scalar_values.items():
            value = value.replace("{" + name + "}", replacement)
        for index, reference in enumerate(references, start=1):
            value = value.replace("{reference_" + str(index) + "}", reference)
        expanded.append(value)
    return expanded


def _command_error(result: subprocess.CompletedProcess[str], label: str) -> RhCliError:
    lines = (result.stderr or result.stdout or "").strip().splitlines()
    detail = "：" + lines[-1][:280] if lines else "。"
    return RhCliError("TOOLBOX_COMMAND_FAILED", f"{label}失败{detail}")


def run_local_command(
    template: str,
    context: dict[str, Any],
    *,
    cwd: Path,
    timeout: int = 3600,
    on_result: Callable[[subprocess.CompletedProcess[str]], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = expand_command_template(template, context)
    environment = os.environ.copy()
    environment.update(
        {
            "RH_TOOLBOX_PROMPT": str(context.get("prompt") or ""),
            "RH_TOOLBOX_INPUT": str(context.get("input") or ""),
            "RH_TOOLBOX_OUTPUT": str(context.get("output") or ""),
            "RH_TOOLBOX_REFERENCES": json.dumps(context.get("references", []), ensure_ascii=False),
            "RH_TOOLBOX_MODE": str(context.get("mode") or ""),
            "RH_TOOLBOX_RESOLUTION": str(context.get("resolution") or ""),
            "RH_TOOLBOX_SIZE": str(context.get("size") or ""),
        }
    )
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RhCliError("TOOLBOX_COMMAND_NOT_FOUND", f"找不到本地命令：{command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RhCliError("TOOLBOX_COMMAND_TIMEOUT", "本地命令执行超时，请检查命令或缩短输入。") from exc
    if on_result is not None:
        on_result(result)
    if result.returncode != 0:
        raise _command_error(result, "本地 Codex 命令")
    return result


def find_generated_media(folder: Path, *, exclude: set[Path] | None = None) -> list[Path]:
    excluded = {path.resolve() for path in (exclude or set())}
    result = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.resolve() in excluded or path.name.startswith("."):
            continue
        if path.suffix.lower() in SUPPORTED_OUTPUT_SUFFIXES:
            result.append(path.resolve())
    return sorted(result, key=lambda item: item.stat().st_mtime_ns)


def _runtime_root(configured_root: str | Path | None) -> Path:
    raw = str(configured_root or "").strip()
    root = Path(raw).expanduser().resolve() if raw else DEFAULT_RUNTIME_ROOT
    if root.is_dir():
        return root
    if DEFAULT_RUNTIME_ROOT.is_dir():
        return DEFAULT_RUNTIME_ROOT
    raise RhCliError("TOOLBOX_RUNTIME_UNAVAILABLE", "找不到 VideoMake 本地媒体库根目录。")


def _depth_runtime_paths(root: Path) -> tuple[Path, Path, Path]:
    project_roots = []
    if root.name == "ref":
        project_roots.append(root.parent)
    project_roots.append(DEFAULT_RUNTIME_ROOT.parent)
    seen: set[Path] = set()
    for project_root in project_roots:
        project_root = project_root.resolve()
        if project_root in seen:
            continue
        seen.add(project_root)
        script = project_root / "tools" / "depth_anything_macos.py"
        batch_script = project_root / "tools" / "depth_anything_batch_macos.py"
        runtime = project_root / ".runtime" / "depth_anything_v2_small_f16"
        python = runtime / "venv" / "bin" / "python"
        if python.is_file() and script.is_file() and batch_script.is_file():
            return python, script, batch_script
    raise RhCliError("DEPTH_GENERATOR_UNAVAILABLE", "找不到 Depth Anything 本地运行环境。")


def _skeleton_runtime_paths(root: Path) -> tuple[Path, Path, Path]:
    project_roots = []
    if root.name == "ref":
        project_roots.append(root.parent)
    project_roots.append(DEFAULT_RUNTIME_ROOT.parent)
    seen: set[Path] = set()
    for project_root in project_roots:
        project_root = project_root.resolve()
        if project_root in seen:
            continue
        seen.add(project_root)
        script = project_root / "tools" / "pose_skeleton_macos.py"
        runtime = project_root / ".runtime" / "pose_dwpose"
        python = runtime / "venv" / "bin" / "python"
        model = runtime / "checkpoints" / "dw-ll_ucoco_384.onnx"
        detector = runtime / "checkpoints" / "yolox_l.onnx"
        if script.is_file() and python.is_file() and model.is_file() and detector.is_file():
            return python, script, model
    raise RhCliError("SKELETON_GENERATOR_UNAVAILABLE", "找不到 DWPose 本地运行环境。")


def _run_checked(command: list[str], *, label: str, timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RhCliError("TOOLBOX_BINARY_NOT_FOUND", f"找不到本地工具：{command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RhCliError("TOOLBOX_PROCESS_TIMEOUT", f"{label}超时，请检查本地运行环境。") from exc
    if result.returncode != 0:
        raise _command_error(result, label)
    return result


def _combine_images(depth_path: Path, skeleton_path: Path, output_path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        image_tool = shutil.which("magick") or shutil.which("convert")
        if not image_tool:
            raise RhCliError("TOOLBOX_IMAGE_TOOL_MISSING", "当前 Python 环境缺少 Pillow，且找不到 ImageMagick，无法合成深度+骨骼图。")
        try:
            identify_command = [image_tool, "identify"] if Path(image_tool).name == "magick" else [shutil.which("identify") or image_tool]
            identify = subprocess.run(
                [*identify_command, "-format", "%wx%h", str(depth_path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if identify.returncode != 0 or "x" not in identify.stdout:
                raise RhCliError("TOOLBOX_COMBINE_FAILED", "无法读取深度图尺寸。")
            width, height = identify.stdout.strip().split("x", 1)
            result = subprocess.run(
                [
                    image_tool,
                    str(depth_path),
                    "-colorspace", "sRGB",
                    "(",
                    str(skeleton_path),
                    "-resize", f"{width}x{height}!",
                    "-colorspace", "sRGB",
                    "-fuzz", "7%",
                    "-transparent", "black",
                    ")",
                    "-compose", "over", "-composite",
                    "-colorspace", "sRGB",
                    "-type", "TrueColor",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RhCliError("TOOLBOX_IMAGE_TOOL_MISSING", "找不到 ImageMagick。") from exc
        except subprocess.TimeoutExpired as exc:
            raise RhCliError("TOOLBOX_COMBINE_TIMEOUT", "深度+骨骼图合成超时。") from exc
        if result.returncode != 0:
            raise _command_error(result, "深度+骨骼图合成")
        return
    try:
        with Image.open(depth_path) as depth, Image.open(skeleton_path) as skeleton:
            base = depth.convert("RGB")
            overlay = skeleton.convert("RGB").resize(base.size, Image.Resampling.BICUBIC)
            mask = overlay.convert("L").point(lambda value: 255 if value > 18 else 0)
            Image.composite(overlay, base, mask).save(output_path, format="PNG")
    except (OSError, ValueError) as exc:
        raise RhCliError("TOOLBOX_COMBINE_FAILED", f"深度+骨骼图合成失败：{exc}") from exc


def _probe_fps(path: Path) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        value = (result.stdout or "").strip()
        fps = float(Fraction(value)) if value and value != "0/0" else 24.0
    except (FileNotFoundError, OSError, ValueError, ZeroDivisionError):
        fps = 24.0
    return min(120.0, max(1.0, fps))


def _format_video_eta(seconds: float | None) -> str:
    if seconds is None:
        return "计算中"
    remaining = max(0, int(round(seconds)))
    if remaining < 60:
        return f"{remaining} 秒"
    minutes, seconds = divmod(remaining, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds:02d} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes:02d} 分"


def _video_progress_message(stage: str, completed: int, total: int, elapsed: float) -> str:
    completed = max(0, min(int(completed), int(total)))
    total = max(0, int(total))
    elapsed = max(0.0, float(elapsed))
    speed = completed / elapsed if completed and elapsed > 0.25 else None
    eta = (total - completed) / speed if speed and total >= completed else None
    speed_label = f"{speed:.2f} 帧/秒" if speed is not None else "计算中"
    eta_label = "完成" if total and completed >= total else _format_video_eta(eta)
    return f"{stage}（{completed}/{total} 帧） · 速度 {speed_label} · 预计剩余 {eta_label}"


def _count_video_outputs(directory: Path, pattern: str) -> int:
    return sum(1 for path in directory.glob(pattern) if path.is_file())


def _run_video_stage(
    command: list[str],
    *,
    label: str,
    output_dir: Path,
    output_pattern: str,
    total_frames: int,
    progress: Callable[[str], None] | None,
    timeout: int = 7200,
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    if progress:
        progress(_video_progress_message(label, 0, total_frames, 0))
    stop_event = threading.Event()

    def monitor() -> None:
        last_count = -1
        last_emit = 0.0
        while not stop_event.is_set():
            count = _count_video_outputs(output_dir, output_pattern)
            now = time.monotonic()
            if progress and count != last_count and (last_emit == 0.0 or now - last_emit >= 0.75):
                progress(_video_progress_message(label, count, total_frames, now - started))
                last_count = count
                last_emit = now
            stop_event.wait(0.25)

    watcher = threading.Thread(target=monitor, name="rh-toolbox-progress", daemon=True)
    watcher.start()
    try:
        result = _run_checked(command, label=label, timeout=timeout)
    finally:
        stop_event.set()
        watcher.join(timeout=2)
    if progress:
        completed = _count_video_outputs(output_dir, output_pattern)
        progress(_video_progress_message(label, completed, total_frames, time.monotonic() - started))
    return result


def _run_image_processor(mode: str, source: Path, output_dir: Path, root: Path) -> Path:
    depth_path = output_dir / "depth.png"
    skeleton_path = output_dir / "skeleton.png"
    if mode in {"depth", "depth_skeleton"}:
        python, script, _ = _depth_runtime_paths(root)
        _run_checked([str(python), str(script), str(source), "-o", str(depth_path)], label="深度图生成")
    if mode in {"skeleton", "depth_skeleton"}:
        python, script, model = _skeleton_runtime_paths(root)
        _run_checked([str(python), str(script), str(source), "-o", str(skeleton_path), "--model", str(model)], label="骨骼图生成")
    if mode == "depth_skeleton":
        output_path = output_dir / "depth_skeleton.png"
        _combine_images(depth_path, skeleton_path, output_path)
        depth_path.unlink(missing_ok=True)
        skeleton_path.unlink(missing_ok=True)
        return output_path
    return depth_path if mode == "depth" else skeleton_path


def _run_video_processor(
    mode: str,
    source: Path,
    output_dir: Path,
    root: Path,
    progress: Callable[[str], None] | None = None,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RhCliError("FFMPEG_UNAVAILABLE", "找不到 ffmpeg，无法处理视频。")
    fps = _probe_fps(source)
    work_dir = Path(tempfile.mkdtemp(prefix="toolbox-frames-", dir=str(output_dir)))
    frames_dir = work_dir / "source"
    depth_dir = work_dir / "depth"
    skeleton_dir = work_dir / "skeleton"
    combined_dir = work_dir / "combined"
    for path in (frames_dir, depth_dir, skeleton_dir, combined_dir):
        path.mkdir(parents=True, exist_ok=True)
    try:
        if progress:
            progress("正在提取视频帧…")
        _run_checked([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source), "-vsync", "0", str(frames_dir / "frame_%06d.png")], label="视频帧提取")
        frames = sorted(frames_dir.glob("frame_*.png"))
        if not frames:
            raise RhCliError("VIDEO_FRAMES_EMPTY", "视频没有读取到可处理的画面。")
        total_frames = len(frames)
        if progress:
            progress(f"视频帧提取完成 · 共 {total_frames} 帧")
        if mode in {"depth", "depth_skeleton"}:
            python, _, batch_script = _depth_runtime_paths(root)
            _run_video_stage(
                [str(python), str(batch_script), *map(str, frames), "--output-dir", str(depth_dir)],
                label="正在生成深度图",
                output_dir=depth_dir,
                output_pattern="frame_*.png",
                total_frames=total_frames,
                progress=progress,
            )
        if mode in {"skeleton", "depth_skeleton"}:
            python, script, model = _skeleton_runtime_paths(root)
            _run_video_stage(
                [str(python), str(script), *map(str, frames), "--output-dir", str(skeleton_dir), "--model", str(model)],
                label="正在生成骨骼图",
                output_dir=skeleton_dir,
                output_pattern="frame_*_skeleton.png",
                total_frames=total_frames,
                progress=progress,
            )

        if mode == "depth_skeleton":
            combine_started = time.monotonic()
            last_combine_emit = 0.0
            for index, frame in enumerate(frames, start=1):
                depth_path = depth_dir / frame.name
                skeleton_path = skeleton_dir / f"{frame.stem}_skeleton.png"
                if not depth_path.is_file() or not skeleton_path.is_file():
                    raise RhCliError("VIDEO_OUTPUT_INCOMPLETE", f"第 {index} 帧没有生成完整的深度/骨骼结果。")
                _combine_images(depth_path, skeleton_path, combined_dir / frame.name)
                now = time.monotonic()
                if progress and (index == 1 or index == total_frames or now - last_combine_emit >= 0.75):
                    progress(_video_progress_message("正在合成深度+骨骼视频", index, total_frames, now - combine_started))
                    last_combine_emit = now
            input_pattern = combined_dir / "frame_%06d.png"
            output_name = "depth_skeleton.mp4"
        elif mode == "depth":
            input_pattern = depth_dir / "frame_%06d.png"
            output_name = "depth.mp4"
        else:
            input_pattern = skeleton_dir / "frame_%06d_skeleton.png"
            output_name = "skeleton.mp4"
        if progress:
            progress("正在编码结果视频…")
        output_path = output_dir / output_name
        partial_path = output_dir / (output_path.stem + ".part.mp4")
        _run_checked([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-framerate", f"{fps:.6f}", "-i", str(input_pattern), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(partial_path)], label="结果视频编码", timeout=7200)
        partial_path.replace(output_path)
        return output_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def process_media(
    mode: str,
    source: Path,
    output_dir: Path,
    configured_root: str | Path | None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    normalized_mode = normalize_toolbox_mode(mode)
    source = validate_local_file(source, label="媒体文件", suffixes=IMAGE_SUFFIXES | VIDEO_SUFFIXES)
    output_dir.mkdir(parents=True, exist_ok=True)
    root = _runtime_root(configured_root)
    if is_video_path(source):
        return _run_video_processor(normalized_mode, source, output_dir, root, progress)
    return _run_image_processor(normalized_mode, source, output_dir, root)
