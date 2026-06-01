from __future__ import annotations

import struct
from pathlib import Path


def fix_mov_to_mp4(file_path: str | Path) -> bool:
    """Patch a QuickTime ftyp header to improve MP4 compatibility without re-encoding."""
    path = Path(file_path)
    try:
        header = path.read_bytes()[:64]
    except OSError:
        return False

    if len(header) < 16:
        return False

    box_size = struct.unpack(">I", header[0:4])[0]
    if header[4:8] != b"ftyp" or box_size < 16 or box_size > len(header):
        return False
    if header[8:12] != b"qt  ":
        return False

    minor_version = header[12:16]
    brands = [b"isom", b"iso2", b"avc1", b"mp41"]
    max_brands = (box_size - 16) // 4
    new_ftyp = struct.pack(">I", box_size) + b"ftyp" + b"isom" + minor_version
    new_ftyp += b"".join(brands[:max_brands])
    new_ftyp += b"\x00" * (box_size - len(new_ftyp))

    try:
        with path.open("r+b") as fh:
            fh.write(new_ftyp)
    except OSError:
        return False
    return True
