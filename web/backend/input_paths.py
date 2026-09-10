"""Canonical storage paths for locally persisted input files."""

from __future__ import annotations

from pathlib import Path


INPUT_ROOT_NAME = "input"
INPUT_SOURCE_NAMES = {
    "downloaded": "downloaded",
    "pasted": "pasted",
    "prompt": "prompt",
    "telegram": "telegram",
    "transcoded": "transcoded",
}


def input_source_path(data_root: str | Path, source: str) -> Path:
    """Return the canonical ``data/input/<source>`` directory."""
    try:
        source_name = INPUT_SOURCE_NAMES[source]
    except KeyError as exc:
        raise ValueError(f"unknown input source: {source}") from exc
    return Path(data_root).expanduser().resolve() / INPUT_ROOT_NAME / source_name
