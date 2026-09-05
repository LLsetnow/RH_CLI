from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Mapping


def safe_media_stem(value: str, fallback: str = "resource") -> str:
    """Turn a library title into a safe filename stem without changing its extension."""
    name = Path(str(value or "")).name.strip()
    name = re.sub(r"[^A-Za-z0-9._()\-\u4e00-\u9fff ]+", "_", name)
    if name in {"", ".", ".."}:
        return fallback
    return name[:160] or fallback


def rename_media_files(
    root: str | Path,
    entries: Mapping[str, tuple[str, str]],
) -> dict[str, str]:
    """Rename existing files and return their new paths relative to *root*.

    The old paths may be relative to root or absolute paths inside root. Existing
    target files are never overwritten. Files are staged through temporary names
    so a pair (for example an action's color/depth images) is renamed together.
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError(f"媒体库根目录不存在：{root_path}")

    planned: list[tuple[str, Path, Path]] = []
    result: dict[str, str] = {}
    for role, (raw_old, raw_new) in entries.items():
        old_value = str(raw_old or "").strip()
        new_name = Path(str(raw_new or "")).name.strip()
        if not old_value or not new_name or new_name in {".", ".."}:
            continue
        old_path = Path(old_value).expanduser()
        old_path = old_path.resolve() if old_path.is_absolute() else (root_path / old_path).resolve()
        if root_path not in old_path.parents or not old_path.is_file():
            continue
        target_path = old_path.with_name(new_name)
        if target_path == old_path:
            result[role] = str(old_path.relative_to(root_path))
            continue
        planned.append((role, old_path, target_path))

    old_paths = {old_path for _, old_path, _ in planned}
    target_paths = [target_path for _, _, target_path in planned]
    if len(set(target_paths)) != len(target_paths):
        raise ValueError("多个素材不能改成同一个文件名。")
    for _, _, target_path in planned:
        if target_path.exists() and target_path not in old_paths:
            raise ValueError(f"目标文件已存在：{target_path.name}")

    staged: list[tuple[str, Path, Path, Path]] = []
    try:
        for role, old_path, target_path in planned:
            temporary = old_path.with_name(f".{old_path.name}.{uuid.uuid4().hex}.rename")
            old_path.rename(temporary)
            staged.append((role, old_path, target_path, temporary))
        for _, _, target_path, temporary in staged:
            temporary.rename(target_path)
    except OSError as exc:
        for _, old_path, target_path, temporary in reversed(staged):
            try:
                if target_path.is_file():
                    target_path.rename(old_path)
                elif temporary.is_file():
                    temporary.rename(old_path)
            except OSError:
                pass
        raise ValueError("媒体文件改名失败，已恢复原文件名。") from exc

    for role, _, target_path, _ in staged:
        result[role] = str(target_path.relative_to(root_path))
    return result
