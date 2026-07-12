from __future__ import annotations

import json
import os
import time as _time
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
    lines: list[str] = []
    # 先写普通键值（非 dict），再写 [section]
    simple: list[str] = []
    sections: list[str] = []
    for key, value in values.items():
        if isinstance(value, dict):
            sections.append(f"\n[{key}]")
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, bool):
                    rendered = "true" if sub_value else "false"
                else:
                    escaped = str(sub_value).replace("\\", "\\\\").replace('"', '\\"')
                    rendered = f'"{escaped}"'
                safe_key = f'"{sub_key}"' if "." in sub_key else sub_key
                sections.append(f'{safe_key} = {rendered}')
        else:
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            else:
                escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
                rendered = f'"{escaped}"'
            simple.append(f"{key} = {rendered}")
    lines = simple + sections
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_key_from_cli_config() -> str | None:
    cfg = read_config()
    value = cfg.get("api_key")
    return _valid_key(value if isinstance(value, str) else None)


def list_keys() -> dict[str, str]:
    """列出所有已命名的 Key。"""
    cfg = read_config()
    mapping = cfg.get("keys", {})
    if not isinstance(mapping, dict):
        return {}
    return {k: v for k, v in mapping.items() if isinstance(v, str) and v.strip()}


def get_key_by_name(name: str) -> str | None:
    """按名称获取 Key。"""
    keys = list_keys()
    return keys.get(name)


def get_default_key_name() -> str:
    """获取默认 Key 名称。"""
    cfg = read_config()
    default = cfg.get("default_key")
    if isinstance(default, str) and default.strip():
        return default.strip()
    return ""


def save_keys(keys: dict[str, str], default: str = "") -> Path:
    """保存多个命名 Key 到配置。"""
    cfg = read_config()
    cfg["keys"] = keys
    if default:
        cfg["default_key"] = default
    return write_config(cfg)


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


def read_workflow_id_from_config() -> str | None:
    """从配置读取默认 workflowId。"""
    cfg = read_config()
    value = cfg.get("workflow_id")
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def read_workflow_id_mapping() -> dict[str, str]:
    """读取 [workflow_ids] 映射表：JSON 文件名 → workflowId。"""
    cfg = read_config()
    mapping = cfg.get("workflow_ids", {})
    if not isinstance(mapping, dict):
        return {}
    return {
        k: str(v).strip()
        for k, v in mapping.items()
        if isinstance(v, str) and v.strip()
    }


def resolve_workflow_id(
    workflow_file: str,
    explicit_id: str | None = None,
) -> tuple[str, str]:
    """解析 workflowId，返回 (id, source)。

    优先级：显式 -w > JSON 文件名匹配 > 默认 workflow_id
    source 用于提示用户来源。
    """
    # 1. 显式指定
    if explicit_id:
        return explicit_id.strip(), "-w"

    # 2. 按 JSON 文件名匹配
    filename = Path(workflow_file).name
    mapping = read_workflow_id_mapping()
    matched = mapping.get(filename)
    if matched:
        return matched, f"映射({filename})"

    # 3. 通配：尝试用文件名前缀匹配（去掉 _api.json / .json）
    for key, wid in mapping.items():
        key_stem = Path(key).stem  # e.g. "VideoRemove_api" → stem
        file_stem = Path(filename).stem
        if key_stem and file_stem.startswith(key_stem):
            return wid, f"映射({key})"

    # 4. 兜底默认
    default = read_workflow_id_from_config()
    if default:
        return default, "默认"

    raise RhCliError(
        "NO_WORKFLOW_ID",
        f"未找到 {filename} 对应的 workflowId。\n"
        f"  用 -w 临时指定，或用 `rh auth set-workflow-id <ID> <文件名>` 保存映射。",
    )


def save_workflow_id(workflow_id: str, filename: str | None = None) -> Path:
    """保存 workflowId 到配置。

    传 filename → 存入 [workflow_ids] 映射表
    不传       → 设为默认 workflow_id
    """
    cfg = read_config()
    if filename:
        mapping = cfg.setdefault("workflow_ids", {})
        if not isinstance(mapping, dict):
            mapping = {}
        mapping[filename] = workflow_id
        cfg["workflow_ids"] = mapping
        path = write_config(cfg)
    else:
        cfg["workflow_id"] = workflow_id
        path = write_config(cfg)
    return path


def resolve_api_key(provided_key: str | None = None, key_name: str | None = None) -> ResolvedKey:
    """解析 API Key。支持按名称查找。"""
    # 1. 显式 CLI 传入（可能是 key 名或直接 key）
    if key_name:
        named_key = get_key_by_name(key_name)
        if named_key:
            return ResolvedKey(named_key, f"keys.{key_name}")

    cli_key = _valid_key(provided_key)
    if cli_key:
        if len(cli_key) == 32:  # 直接 UUID key
            return ResolvedKey(cli_key, "cli")
        named_key = get_key_by_name(cli_key)
        if named_key:
            return ResolvedKey(named_key, f"keys.{cli_key}")

    env_key = _valid_key(os.environ.get(ENV_API_KEY))
    if env_key:
        return ResolvedKey(env_key, "env")

    # 默认 key
    default_name = get_default_key_name()
    if default_name:
        named_key = get_key_by_name(default_name)
        if named_key:
            return ResolvedKey(named_key, f"keys.{default_name}")

    cfg_key = read_key_from_cli_config()
    if cfg_key:
        return ResolvedKey(cfg_key, "config")

    legacy_key = read_key_from_openclaw_config()
    if legacy_key:
        return ResolvedKey(legacy_key, "openclaw")

    return ResolvedKey(None, "none")


def require_api_key(provided_key: str | None = None, key_name: str | None = None) -> ResolvedKey:
    resolved = resolve_api_key(provided_key, key_name)
    if resolved.value:
        return resolved
    raise RhCliError(
        "NO_API_KEY",
        "还没有配置 RunningHub API Key。请运行 `rh auth keys` 管理，或设置 RUNNINGHUB_API_KEY。",
        detail={
            "create_key_url": CREATE_KEY_URL,
            "recharge_url": RECHARGE_URL,
        },
    )


# ---------------------------------------------------------------------------
# Browser session tokens (for /task/forward mode)
# ---------------------------------------------------------------------------

def read_browser_tokens(site: str = "ai") -> dict[str, str]:
    """读取指定站点的浏览器 session token。返回 {rh_comfy_auth, rh_identify}。"""
    cfg = read_config()
    tokens = cfg.get("browser_tokens", {})
    if not isinstance(tokens, dict):
        return {}
    site_tokens = tokens.get(site, {})
    if isinstance(site_tokens, dict):
        result: dict[str, str] = {}
        for k, v in site_tokens.items():
            if isinstance(v, str) and v.strip():
                result[k] = v
        return result
    return {k: v for k, v in tokens.items() if isinstance(v, str) and v.strip()}


def check_browser_token_expiry(site: str = "ai") -> tuple[bool, int]:
    """检查浏览器 token 是否过期。返回 (is_expired, remaining_seconds)。"""
    import base64
    tokens = read_browser_tokens(site)
    auth = tokens.get("rh_comfy_auth", "")
    if not auth:
        return True, 0
    try:
        payload = auth.split(".", 1)[1] if "." in auth else auth
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        expire_ms = decoded.get("signExpire", 0)
        now_ms = int(_time.time() * 1000)
        remaining = (expire_ms - now_ms) // 1000 if expire_ms else 3600
        return remaining <= 0, remaining
    except Exception:
        return False, 3600


def save_browser_token(rh_comfy_auth: str, rh_identify: str) -> Path:
    """保存浏览器 session token 到配置。"""
    cfg = read_config()
    cfg["browser_tokens"] = {
        "rh_comfy_auth": rh_comfy_auth,
        "rh_identify": rh_identify,
    }
    return write_config(cfg)


