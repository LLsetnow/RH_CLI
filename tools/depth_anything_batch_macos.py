#!/usr/bin/env python3
"""Generate a sequence of 8-bit grayscale depth maps with one Core ML load."""

from __future__ import annotations

import argparse
from pathlib import Path

import coremltools as ct
import numpy as np
from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = PROJECT_ROOT / ".runtime/depth_anything_v2_small_f16/DepthAnythingV2SmallF16.mlpackage"
MODEL_SIZE = (518, 392)


def prepare_image(image: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]]:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-generate normalized grayscale depth PNGs.")
    parser.add_argument("input", type=Path, nargs="+", help="Input frame paths in playback order")
    parser.add_argument("--output-dir", type=Path, required=True, help="Destination directory")
    parser.add_argument("--skip-existing", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not MODEL_PATH.is_dir():
        raise SystemExit(f"Core ML model not found: {MODEL_PATH}")
    sources = [path.expanduser().resolve() for path in args.input]
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise SystemExit(f"Input frame not found: {missing[0]}")
    destination_dir = args.output_dir.expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)

    model = ct.models.MLModel(str(MODEL_PATH), compute_units=ct.ComputeUnit.ALL)
    generated = 0
    skipped = 0
    for index, source in enumerate(sources, start=1):
        destination = destination_dir / f"frame_{index:06d}.png"
        if args.skip_existing and destination.is_file():
            skipped += 1
            continue
        with Image.open(source) as opened:
            original_size = opened.size
            prepared, crop = prepare_image(opened)
        result = model.predict({"image": prepared})
        depth = normalize_depth(result["depth"])
        depth = depth.crop(crop).resize(original_size, Image.Resampling.BICUBIC)
        temporary = destination.with_name(f".{destination.name}.part")
        depth.save(temporary, format="PNG")
        temporary.replace(destination)
        generated += 1
        if generated == 1 or generated % 25 == 0 or generated == len(sources):
            print(f"processed={index}/{len(sources)} output={destination}")
    print(f"model={MODEL_PATH} frames={len(sources)} generated={generated} skipped={skipped}")


if __name__ == "__main__":
    main()
