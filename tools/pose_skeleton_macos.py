#!/usr/bin/env python3
"""Generate OpenPose-style black-background skeleton PNGs with DWPose."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from easy_dwpose.body_estimation import Wholebody, resize_image
from easy_dwpose.draw import draw_openpose


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / ".runtime" / "pose_dwpose"
DEFAULT_MODEL_PATH = RUNTIME_DIR / "checkpoints" / "dw-ll_ucoco_384.onnx"
DEFAULT_DETECTOR_PATH = RUNTIME_DIR / "checkpoints" / "yolox_l.onnx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate OpenPose-style human skeleton PNGs with DWPose."
    )
    parser.add_argument("input", type=Path, nargs="+", help="One or more input image paths")
    parser.add_argument("-o", "--output", type=Path, help="Output path when processing one input")
    parser.add_argument("--output-dir", type=Path, help="Output directory when processing multiple inputs")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH, help="DWPose body/face/hand model path")
    parser.add_argument("--det-model", type=Path, default=DEFAULT_DETECTOR_PATH, help="DWPose person detector path")
    parser.add_argument("--num-poses", type=int, default=2, help="Maximum number of people to draw")
    parser.add_argument("--min-confidence", type=float, default=0.30, help="Body keypoint confidence threshold")
    parser.add_argument("--detect-resolution", type=int, default=512, help="DWPose detection resolution")
    parser.add_argument("--skip-failures", action="store_true", help="批量处理时跳过未检测到人体的图片")
    return parser.parse_args()


def output_paths(inputs: list[Path], output: Path | None, output_dir: Path | None) -> list[Path]:
    if len(inputs) == 1 and output is not None:
        return [output]
    if output is not None:
        raise SystemExit("--output 只能和单张输入一起使用；批量处理请使用 --output-dir")
    directory = output_dir or inputs[0].parent
    return [directory / f"{source.stem}_skeleton.png" for source in inputs]


def format_pose(candidates: np.ndarray, scores: np.ndarray, width: int, height: int, threshold: float) -> dict[str, np.ndarray]:
    candidates = candidates.copy()
    scores = scores.copy()
    num_candidates, _, locs = candidates.shape
    candidates[..., 0] /= float(width)
    candidates[..., 1] /= float(height)

    bodies = candidates[:, :18].copy().reshape(num_candidates * 18, locs)
    body_scores = scores[:, :18].copy()
    for index in range(len(body_scores)):
        for joint in range(len(body_scores[index])):
            body_scores[index][joint] = 18 * index + joint if body_scores[index][joint] > threshold else -1

    faces = candidates[:, 24:92]
    faces_scores = scores[:, 24:92]
    hands = np.vstack([candidates[:, 92:113], candidates[:, 113:]])
    hands_scores = np.vstack([scores[:, 92:113], scores[:, 113:]])
    return {
        "bodies": bodies,
        "body_scores": body_scores,
        "hands": hands,
        "hands_scores": hands_scores,
        "faces": faces,
        "faces_scores": faces_scores,
    }


def generate(
    source: Path,
    destination: Path,
    estimator: Wholebody,
    num_poses: int,
    threshold: float,
    detect_resolution: int,
) -> int:
    if not source.is_file():
        raise SystemExit(f"Input image not found: {source}")
    with Image.open(source) as opened:
        original = np.asarray(opened.convert("RGB"), dtype=np.uint8)
    original_height, original_width = original.shape[:2]
    detected = resize_image(original, target_resolution=detect_resolution)
    candidates, scores = estimator(detected)
    if candidates.size == 0 or scores.size == 0:
        raise RuntimeError(f"未检测到人体骨骼：{source.name}")

    candidates = candidates[:num_poses]
    scores = scores[:num_poses]
    pose = format_pose(candidates, scores, detected.shape[1], detected.shape[0], threshold)
    skeleton = draw_openpose(
        pose,
        height=detected.shape[0],
        width=detected.shape[1],
        include_face=True,
        include_hands=True,
    )
    skeleton = cv2.resize(skeleton, (original_width, original_height), interpolation=cv2.INTER_LANCZOS4)
    output = Image.fromarray(skeleton)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    output.save(temporary, format="PNG")
    temporary.replace(destination)
    print(
        f"model=DWPose input={source} "
        f"input_size={original_width}x{original_height} "
        f"output={destination} output_size={output.width}x{output.height} "
        f"mode={output.mode} poses={len(candidates)}"
    )
    return len(candidates)


def main() -> None:
    args = parse_args()
    model_path = args.model.expanduser().resolve()
    detector_path = args.det_model.expanduser().resolve()
    if not model_path.is_file():
        raise SystemExit(f"DWPose model not found: {model_path}")
    if not detector_path.is_file():
        raise SystemExit(f"DWPose detector model not found: {detector_path}")
    if not 1 <= args.num_poses <= 10:
        raise SystemExit("--num-poses 必须在 1 到 10 之间")
    if not 0.0 <= args.min_confidence <= 1.0:
        raise SystemExit("--min-confidence 必须在 0 到 1 之间")
    if args.detect_resolution < 256:
        raise SystemExit("--detect-resolution 必须不小于 256")

    destinations = output_paths(args.input, args.output, args.output_dir)
    estimator = Wholebody(str(detector_path), str(model_path), device="cpu")
    failures = 0
    for source, destination in zip(args.input, destinations):
        try:
            generate(
                source.expanduser().resolve(),
                destination.expanduser().resolve(),
                estimator,
                args.num_poses,
                args.min_confidence,
                args.detect_resolution,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            if not args.skip_failures or len(args.input) == 1:
                raise
            failures += 1
            print(f"skip={source} reason={exc}")
    if failures:
        print(f"skipped={failures}")


if __name__ == "__main__":
    main()
