#!/usr/bin/env -S uv run python3
"""提取 MP4 视频文件的首帧，保存为 PNG 图片。

用法:
    uv run python script/extract_first_frame.py <input.mp4> [-o output.png]

示例:
    uv run python script/extract_first_frame.py input/video.mp4
    uv run python script/extract_first_frame.py input/video.mp4 -o thumb.png
"""

import argparse
import subprocess
import sys
from pathlib import Path


def extract_first_frame(input_path: str, output_path: str | None = None) -> str:
    src = Path(input_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"文件不存在：{src}")

    if output_path:
        dst = Path(output_path).expanduser().resolve()
    else:
        dst = src.with_stem(f"{src.stem}_first_frame").with_suffix(".png")

    dst.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",                    # 覆盖已存在文件
        "-i", str(src),          # 输入文件
        "-vframes", "1",         # 只取 1 帧
        "-q:v", "2",             # 高质量
        str(dst),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or "Unknown error"
        raise RuntimeError(f"提取失败：{err}")

    return str(dst)


def main() -> None:
    parser = argparse.ArgumentParser(description="提取 MP4 视频文件的首帧为 PNG 图片")
    parser.add_argument("input", help="输入 MP4 文件路径")
    parser.add_argument("-o", "--output", help="输出 PNG 路径（缺省：同目录下 <原文件名>_first_frame.png）")
    args = parser.parse_args()

    try:
        output = extract_first_frame(args.input, args.output)
        size_kb = Path(output).stat().st_size / 1024
        print(f"✅ 首帧已提取: {output} ({size_kb:.1f} KB)")
    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
