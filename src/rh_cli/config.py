from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import CREATE_KEY_URL, RECHARGE_URL, RhCliError

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


PLACEHOLDER_KEYS = {
    "your_api_key_here",
    "<your_api_key>",
    "YOUR_API_KEY",
    "RUNNINGHUB_API_KEY",
}
ENV_API_KEY = "RUNNINGHUB_API_KEY"
ENV_OUTPUT_DIR = "RH_OUTPUT_DIR"


@dataclass(slots=True)
class ResolvedKey:
    value: str | None
    source: str


def config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "rh"


def config_path() -> Path:
    return config_dir() / "config.toml"


def default_output_dir() -> Path:
    env_dir = os.environ.get(ENV_OUTPUT_DIR, "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    cfg = read_config()
    configured = cfg.get("output_dir")
    if isinstance(configured, str) and configured.strip():
        return Path(configured).expanduser()
    return Path.home() / "rh-output"


def _valid_key(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized or normalized in PLACEHOLDER_KEYS:
        return None
    return normalized


def read_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_config(values: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in values.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            rendered = f'"{escaped}"'
        lines.append(f"{key} = {rendered}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_key_from_cli_config() -> str | None:
    cfg = read_config()
    value = cfg.get("api_key")
    return _valid_key(value if isinstance(value, str) else None)


def read_key_from_openclaw_config() -> str | None:
    path = Path.home() / ".openclaw" / "openclaw.json"
    if not path.exists():
        return None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    entry = cfg.get("skills", {}).get("entries", {}).get("runninghub", {})
    api_key = entry.get("apiKey")
    if isinstance(api_key, str):
        resolved = _valid_key(api_key)
        if resolved:
            return resolved
    env_val = entry.get("env", {}).get(ENV_API_KEY)
    if isinstance(env_val, str):
        return _valid_key(env_val)
    return None


def resolve_api_key(provided_key: str | None = None) -> ResolvedKey:
    cli_key = _valid_key(provided_key)
    if cli_key:
        return ResolvedKey(cli_key, "cli")

    env_key = _valid_key(os.environ.get(ENV_API_KEY))
    if env_key:
        return ResolvedKey(env_key, "env")

    cfg_key = read_key_from_cli_config()
    if cfg_key:
        return ResolvedKey(cfg_key, "config")

    legacy_key = read_key_from_openclaw_config()
    if legacy_key:
        return ResolvedKey(legacy_key, "openclaw")

    return ResolvedKey(None, "none")


def require_api_key(provided_key: str | None = None) -> ResolvedKey:
    resolved = resolve_api_key(provided_key)
    if resolved.value:
        return resolved
    raise RhCliError(
        "NO_API_KEY",
        "还没有配置 RunningHub API Key。请运行 `rh auth set-key`，或设置 RUNNINGHUB_API_KEY。",
        detail={
            "create_key_url": CREATE_KEY_URL,
            "recharge_url": RECHARGE_URL,
        },
    )
