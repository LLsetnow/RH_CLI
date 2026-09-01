#!/usr/bin/env python3
"""Linux decoder for the SS_tools duck-payload image format.

This standalone implementation only needs Pillow and NumPy. It mirrors the
payload extraction and password format used by SS_tools without requiring
ComfyUI, PyTorch, MoviePy, or a Windows/macOS executable.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import struct
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:
    print(
        "Linux 解码器需要 Pillow 和 NumPy。请先执行：python3 -m pip install -r requirements-linux.txt",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


WATERMARK_SKIP_W_RATIO = 0.40
WATERMARK_SKIP_H_RATIO = 0.08
KNOWN_PAYLOAD_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".mp4",
    ".webm",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
    ".txt",
    ".bin",
}


def extract_payload(image: Image.Image, lsb_bits: int) -> bytes:
    image_array = np.asarray(image.convert("RGB"), dtype=np.uint8)
    height, width, channels = image_array.shape
    skip_width = int(width * WATERMARK_SKIP_W_RATIO)
    skip_height = int(height * WATERMARK_SKIP_H_RATIO)

    usable_mask = np.ones((height, width), dtype=bool)
    if skip_width > 0 and skip_height > 0:
        usable_mask[:skip_height, :skip_width] = False
    channel_mask = np.repeat(usable_mask[:, :, None], channels, axis=2)
    flat = image_array.reshape(-1)
    indexes = np.flatnonzero(channel_mask.reshape(-1))
    values = (flat[indexes] & ((1 << lsb_bits) - 1)).astype(np.uint8)
    bits = np.unpackbits(values, bitorder="big").reshape(-1, 8)[:, -lsb_bits:].reshape(-1)

    if len(bits) < 32:
        raise ValueError("图像数据不足。")
    header_length = struct.unpack(">I", np.packbits(bits[:32], bitorder="big").tobytes())[0]
    total_bits = 32 + header_length * 8
    if header_length <= 0 or total_bits > len(bits):
        raise ValueError("载荷长度异常。")
    return np.packbits(bits[32:total_bits], bitorder="big").tobytes()


def generate_key_stream(password: str, salt: bytes, length: int) -> bytes:
    key_material = (password + salt.hex()).encode("utf-8")
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        stream.extend(hashlib.sha256(key_material + str(counter).encode("utf-8")).digest())
        counter += 1
    return bytes(stream[:length])


def parse_header(header: bytes, password: str) -> tuple[bytes, str, bool]:
    if len(header) < 1:
        raise ValueError("文件头损坏。")
    index = 0
    encrypted = header[index] == 1
    index += 1
    password_hash = b""
    salt = b""
    if encrypted:
        if len(header) < index + 48:
            raise ValueError("文件头损坏。")
        password_hash = header[index:index + 32]
        index += 32
        salt = header[index:index + 16]
        index += 16
    if len(header) < index + 1:
        raise ValueError("文件头损坏。")

    extension_length = header[index]
    index += 1
    if len(header) < index + extension_length + 4:
        raise ValueError("文件头损坏。")
    extension = header[index:index + extension_length].decode("utf-8", errors="ignore")
    index += extension_length
    data_length = struct.unpack(">I", header[index:index + 4])[0]
    index += 4
    data = header[index:]
    if len(data) != data_length:
        raise ValueError("数据长度不匹配。")
    if not encrypted:
        return data, extension, False
    if not password:
        raise ValueError("需要密码。")
    if hashlib.sha256((password + salt.hex()).encode("utf-8")).digest() != password_hash:
        raise ValueError("密码错误。")
    plaintext = bytes(a ^ b for a, b in zip(data, generate_key_stream(password, salt, len(data))))
    return plaintext, extension, True


def destination_for_extension(requested: Path, extension: str) -> Path:
    normalized = extension.lower().lstrip(".")
    suffix = ".mp4" if normalized.endswith(".binpng") or normalized == "binpng" else f".{normalized}" if normalized else ""
    if not suffix:
        return requested
    if requested.suffix.lower() in KNOWN_PAYLOAD_EXTENSIONS:
        return requested.with_suffix(suffix)
    return Path(f"{requested}{suffix}")


def decode(input_path: Path, requested_output: Path, password: str) -> tuple[Path, str, bool]:
    with Image.open(input_path) as image:
        errors: list[str] = []
        for lsb_bits in (2, 6, 8):
            try:
                header = extract_payload(image, lsb_bits)
                raw_data, extension, encrypted = parse_header(header, password)
                break
            except Exception as exc:
                errors.append(f"LSB {lsb_bits}: {exc}")
        else:
            raise ValueError("无法解析鸭子图载荷；" + "；".join(errors))

    output_path = destination_for_extension(requested_output, extension)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if extension.lower().lstrip(".").endswith(".binpng") or extension.lower().lstrip(".") == "binpng":
        with Image.open(io.BytesIO(raw_data)) as payload_image:
            output_path.write_bytes(payload_image.convert("RGB").tobytes().rstrip(b"\x00"))
    else:
        output_path.write_bytes(raw_data)
    return output_path.resolve(), extension, encrypted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SS_tools Linux 鸭鸭图解码器")
    parser.add_argument("--duck", required=True, type=Path, help="鸭子图路径")
    parser.add_argument("--out", required=True, type=Path, help="输出文件基名")
    parser.add_argument("--password", default="", help="解码密码")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.duck.is_file():
        print(f"找不到输入图片：{args.duck}", file=sys.stderr)
        return 1
    try:
        output_path, extension, encrypted = decode(args.duck, args.out, args.password)
    except Exception as exc:
        print(f"Decode failed 解码失败：{exc}", file=sys.stderr)
        return 1

    print(f"Extraction completed 提取完成：{output_path}")
    print(f"Original extension 原始扩展名: {extension} | Encrypted 是否加密: {encrypted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
