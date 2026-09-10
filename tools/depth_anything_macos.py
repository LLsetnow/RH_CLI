#!/usr/bin/env python3
"""Generate an 8-bit grayscale depth map with Apple's Core ML model."""

from __future__ import annotations

import argparse
from pathlib import Path

import coremltools as ct
import numpy as np
from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = (
    PROJECT_ROOT
    / ".runtime/depth_anything_v2_small_f16/DepthAnythingV2SmallF16.mlpackage"
)
MODEL_SIZE = (518, 392)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a normalized grayscale depth map with Depth Anything V2 Small F16."
    )
    parser.add_argument("input", type=Path, help="Input image path")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output PNG path")
    return parser.parse_args()


def prepare_image(image: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Letterbox an image to the fixed Core ML input and return its content crop."""
    image = image.convert("RGB")
    fitted = ImageOps.contain(image, MODEL_SIZE, method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", MODEL_SIZE, (0, 0, 0))
    left = (MODEL_SIZE[0] - fitted.width) // 2
    top = (MODEL_SIZE[1] - fitted.height) // 2
    canvas.paste(fitted, (left, top))
    return canvas, (left, top, left + fitted.width, top + fitted.height)


def normalize_depth(depth: Image.Image) -> Image.Image:
    values = np.asarray(depth, dtype=np.float32)
    finite = np.isfinite(values)
    if not finite.any():
        raise RuntimeError("Core ML returned no finite depth values")

    valid = values[finite]
    low, high = np.percentile(valid, (1, 99))
    if high <= low:
        low, high = float(valid.min()), float(valid.max())
    if high <= low:
        return Image.new("L", depth.size, 0)

    normalized = np.clip((values - low) / (high - low), 0, 1)
    normalized[~finite] = 0
    return Image.fromarray(np.round(normalized * 255).astype(np.uint8))


def main() -> None:
    args = parse_args()
    if not args.input.is_file():
        raise SystemExit(f"Input image not found: {args.input}")
    if not MODEL_PATH.is_dir():
        raise SystemExit(f"Core ML model not found: {MODEL_PATH}")

    source = Image.open(args.input)
    original_size = source.size
    prepared, crop = prepare_image(source)

    model = ct.models.MLModel(str(MODEL_PATH), compute_units=ct.ComputeUnit.ALL)
    result = model.predict({"image": prepared})
    depth = normalize_depth(result["depth"])
    depth = depth.crop(crop).resize(original_size, Image.Resampling.BICUBIC)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    depth.save(args.output)
    print(f"model={MODEL_PATH}")
    print(f"input={args.input} input_size={original_size[0]}x{original_size[1]}")
    print(f"output={args.output} output_size={depth.width}x{depth.height} mode={depth.mode}")


if __name__ == "__main__":
    main()
