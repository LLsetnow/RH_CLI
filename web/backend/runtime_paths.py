"""Paths for RH_CLI-owned local model runtimes and helper scripts."""

from __future__ import annotations

import os
from pathlib import Path

from rh_cli.errors import RhCliError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = PROJECT_ROOT / "tools"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
_RUNTIME_ROOT_OVERRIDE = os.environ.get("RH_RUNTIME_ROOT", "").strip()
RUNTIME_ROOT = (
    Path(_RUNTIME_ROOT_OVERRIDE).expanduser().resolve()
    if _RUNTIME_ROOT_OVERRIDE
    else DEFAULT_RUNTIME_ROOT
)


def depth_runtime_paths() -> tuple[Path, Path, Path, Path]:
    runtime = RUNTIME_ROOT / "depth_anything_v2_small_f16"
    python = runtime / "venv" / "bin" / "python"
    model = runtime / "DepthAnythingV2SmallF16.mlpackage"
    script = TOOLS_ROOT / "depth_anything_macos.py"
    batch_script = TOOLS_ROOT / "depth_anything_batch_macos.py"
    if all(path.is_file() or path.is_dir() for path in (python, model, script, batch_script)):
        return python, script, batch_script, model
    raise RhCliError(
        "DEPTH_GENERATOR_UNAVAILABLE",
        "找不到 RH_CLI 的 Depth Anything 运行环境，请确认 .runtime/depth_anything_v2_small_f16 和 tools/depth_anything*.py 存在。",
    )


def skeleton_runtime_paths() -> tuple[Path, Path, Path, Path]:
    runtime = RUNTIME_ROOT / "pose_dwpose"
    python = runtime / "venv" / "bin" / "python"
    model = runtime / "checkpoints" / "dw-ll_ucoco_384.onnx"
    detector = runtime / "checkpoints" / "yolox_l.onnx"
    script = TOOLS_ROOT / "pose_skeleton_macos.py"
    if all(path.is_file() for path in (python, model, detector, script)):
        return python, script, model, detector
    raise RhCliError(
        "SKELETON_GENERATOR_UNAVAILABLE",
        "找不到 RH_CLI 的 DWPose 运行环境，请确认 .runtime/pose_dwpose、模型文件和 tools/pose_skeleton_macos.py 存在。",
    )
