from __future__ import annotations

import json
from pathlib import Path

import pytest

from rh_cli.errors import RhCliError
from web.backend.resource_library import (
    initialize_resource_library,
    read_resource_index,
    resolve_tts_root,
)


def test_initialize_resource_library_creates_index_and_empty_sources(tmp_path):
    root = initialize_resource_library(tmp_path / "new-ref")

    assert root.is_dir()
    index = read_resource_index(root / "Resources.json")
    assert index["sources"]["prompt"] == "prompt/library.json"
    assert index["sources"]["tts"] == "tts"
    assert resolve_tts_root(root / "Resources.json") == (root / "tts").resolve()
    assert json.loads((root / "prompt/library.json").read_text()) == {"version": 1, "blocks": []}
    assert json.loads((root / "pose/pose.json").read_text()) == {"version": 7, "actions": []}
    assert json.loads((root / "character/character.json").read_text())["references"] == []
    assert (root / "pose/color").is_dir()
    assert (root / "pose/depth").is_dir()
    assert (root / "pose/skeleton").is_dir()


def test_initialize_resource_library_never_overwrites_non_empty_directory(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    marker = root / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(RhCliError, match="必须为空") as excinfo:
        initialize_resource_library(root)

    assert excinfo.value.code == "RESOURCE_LIBRARY_NOT_EMPTY"
    assert marker.read_text(encoding="utf-8") == "keep"

