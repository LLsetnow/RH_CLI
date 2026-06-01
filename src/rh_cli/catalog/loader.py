from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from rh_cli.errors import RhCliError


def _candidate_paths() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        here.parent / "data" / "capabilities.json",
        here.parents[4] / "runninghub" / "data" / "capabilities.json",
        Path.cwd() / "runninghub" / "data" / "capabilities.json",
    ]


@lru_cache(maxsize=1)
def load_capabilities() -> dict[str, Any]:
    try:
        resource = resources.files("rh_cli.catalog").joinpath("data/capabilities.json")
        if resource.is_file():
            return json.loads(resource.read_text(encoding="utf-8"))
    except Exception:
        pass

    for path in _candidate_paths():
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

    raise RhCliError("CATALOG_NOT_FOUND", "找不到 capabilities.json，请确认 RH CLI 包含端点目录。")


def list_endpoints(
    *,
    output_type: str | None = None,
    task: str | None = None,
) -> list[dict[str, Any]]:
    endpoints = list(load_capabilities().get("endpoints", []))
    if output_type:
        endpoints = [item for item in endpoints if item.get("output_type") == output_type]
    if task:
        endpoints = [item for item in endpoints if item.get("task") == task]
    return endpoints


def find_endpoint(endpoint: str) -> dict[str, Any] | None:
    return next((item for item in list_endpoints() if item.get("endpoint") == endpoint), None)


def find_best_for_task(task: str) -> dict[str, Any] | None:
    matches = list_endpoints(task=task)
    if not matches:
        return None
    return min(matches, key=lambda item: item.get("popularity", 99))
