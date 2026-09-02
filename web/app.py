from __future__ import annotations

import json
import mimetypes
import os
import platform
import re
import sqlite3
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rh_cli.config import list_keys
from rh_cli.errors import RhCliError
from rh_cli.http import (
    ACCOUNT_STATUS_URL_AI,
    ACCOUNT_STATUS_URL_CN,
    RhHttpClient,
    get_site_config,
    mask_secret,
)
from rh_cli.workflow.client import (
    _apply_file_args,
    _apply_overrides,
    _normalise_output_ext,
    _output_file_url,
    _output_text,
    _poll_outputs,
    _site_cancel_url,
    _site_urls,
    _submit,
    _upload_file,
    _validate_api_workflow,
)


WEB_ROOT = Path(__file__).resolve().parent
_DATA_ROOT_OVERRIDE = os.environ.get("RH_WORKFLOW_DESK_DATA_ROOT", "").strip()
DATA_ROOT = Path(_DATA_ROOT_OVERRIDE).expanduser().resolve() if _DATA_ROOT_OVERRIDE else WEB_ROOT / "data"
WORKFLOW_ROOT = DATA_ROOT / "workflows"
OUTPUT_ROOT = DATA_ROOT / "outputs"
KEYS_PATH = DATA_ROOT / "keys.json"
ACCOUNTS_PATH = DATA_ROOT / "accounts.json"
DB_PATH = DATA_ROOT / "tasks.sqlite3"

FILE_FIELDS = {
    "image",
    "mask",
    "audio",
    "video",
    "file",
    "filepath",
    "file_path",
    "image_path",
    "audio_path",
    "video_path",
    "input_file",
    "source_file",
}
PROMPT_FIELDS = {"text", "prompt", "positive", "negative", "caption", "instruction"}
PROMPT_CLASS_HINTS = ("textencode", "cliptext", "prompt", "t5", "llama")
FILE_CLASS_HINTS = ("loadimage", "loadaudio", "loadvideo", "loadfile", "load3d", "vhs_load")
RANDOM_NOISE_MODES = {"fixed", "randomize"}
WORKFLOW_INPUT_KINDS = {"file", "prompt", "text", "number", "select", "boolean", "resolution", "random_noise"}
WORKFLOW_VIRTUAL_FIELDS = {"resolution": "__resolution__", "random_noise": "__random_noise__"}
RESOLUTION_ASPECT_RATIOS = (
    "1:1 (Square)",
    "2:3 (Portrait Photo)",
    "3:2 (Photo)",
    "3:4 (Portrait Standard)",
    "4:3 (Standard)",
    "9:16 (Portrait Widescreen)",
    "16:9 (Widescreen)",
    "21:9 (Ultrawide)",
)
DEFAULT_PERSONAL_CAPACITY = 3
MIN_PERSONAL_CAPACITY = 1
MAX_PERSONAL_CAPACITY = 3
WORKFLOW_META_KEY = "__rh_meta__"
GENERAL_ACCOUNT_ID = "__general__"
INSTANCE_TYPES = {"default", "plus", "ultra"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


def now_ms() -> int:
    return int(time.time() * 1000)


def normalize_instance_type(value: Any) -> str:
    normalized = str(value or "default").strip().lower()
    if normalized not in INSTANCE_TYPES:
        raise RhCliError("INVALID_INSTANCE_TYPE", "提交机型只能是 default、plus 或 ultra。")
    return normalized


def task_elapsed_ms(task: dict[str, Any], current_time: int | None = None) -> int | None:
    """Return the persisted task lifetime, including local queue waiting time."""
    created_at = int(task.get("created_at") or 0)
    if not created_at:
        return None
    completed_at = int(task.get("completed_at") or 0)
    if completed_at:
        end_at = completed_at
    elif task.get("status") in TERMINAL_TASK_STATUSES:
        end_at = int(task.get("updated_at") or 0)
    else:
        end_at = int(current_time if current_time is not None else now_ms())
    if not end_at:
        return None
    return max(0, end_at - created_at)


def default_local_output_dir() -> Path:
    return OUTPUT_ROOT


def native_file_picker_available() -> bool:
    return platform.system() == "Darwin" and shutil.which("osascript") is not None


def pick_local_file_on_macos(prompt: str = "选择工作流输入文件") -> Path:
    """Use the macOS native picker so the browser never needs the real path."""
    if platform.system() != "Darwin":
        raise RhCliError("LOCAL_PICKER_UNAVAILABLE", "本机路径选择目前仅支持 macOS；请手动填写绝对路径。")
    if shutil.which("osascript") is None:
        raise RhCliError("LOCAL_PICKER_UNAVAILABLE", "本机没有可用的 macOS 文件选择器，请手动填写绝对路径。")

    prompt_literal = str(prompt or "选择文件").replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
try
    set pickedFile to choose file with prompt "{prompt_literal}"
    return POSIX path of pickedFile
on error number -128
    return ""
end try
'''
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RhCliError("LOCAL_PICKER_FAILED", "打开本机文件选择器失败，请手动填写绝对路径。") from exc
    if completed.returncode != 0:
        raise RhCliError("LOCAL_PICKER_FAILED", "打开本机文件选择器失败，请手动填写绝对路径。")
    selected = completed.stdout.strip()
    if not selected:
        raise RhCliError("LOCAL_PICKER_CANCELLED", "已取消选择文件。")
    path = Path(selected).expanduser().resolve()
    if not path.is_file():
        raise RhCliError("FILE_NOT_FOUND", f"选择的文件不存在：{path}")
    return path


def pick_local_directory_on_macos() -> Path | None:
    """Use the macOS native picker to select a directory without reading its contents."""
    if platform.system() != "Darwin":
        raise RhCliError("LOCAL_PICKER_UNAVAILABLE", "本机路径选择目前仅支持 macOS；请手动填写绝对路径。")
    if shutil.which("osascript") is None:
        raise RhCliError("LOCAL_PICKER_UNAVAILABLE", "本机没有可用的 macOS 文件选择器，请手动填写绝对路径。")

    script = r'''
try
    set pickedFolder to choose folder with prompt "选择默认产物目录"
    return POSIX path of pickedFolder
on error number -128
    return ""
end try
'''
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RhCliError("LOCAL_PICKER_FAILED", "打开本机文件夹选择器失败，请手动填写绝对路径。") from exc
    if completed.returncode != 0:
        raise RhCliError("LOCAL_PICKER_FAILED", "打开本机文件夹选择器失败，请手动填写绝对路径。")
    selected = completed.stdout.strip()
    if not selected:
        return None
    path = Path(selected).expanduser().resolve()
    if not path.is_dir():
        raise RhCliError("DIRECTORY_NOT_FOUND", f"选择的文件夹不存在：{path}")
    return path


def safe_name(value: str, fallback: str = "file") -> str:
    name = Path(str(value or "")).name.strip() or fallback
    name = re.sub(r"[^A-Za-z0-9._()\-\u4e00-\u9fff ]+", "_", name)
    return name[:160] or fallback


def canonical_workflow_name(value: str, fallback: str = "workflow.json") -> str:
    """Keep user-facing workflow names stable across repeated exports/submits."""
    name = safe_name(value, fallback)
    path = Path(name)
    suffix = path.suffix
    stem = path.stem if suffix else path.name
    # Older versions leaked one or more internal workflow IDs into task names.
    stem = re.sub(r"^(?:(?:wf_)?[0-9a-f]{12}_)+", "", stem, flags=re.IGNORECASE)
    # Exported files may be imported and exported again; keep one clean source name.
    stem = re.sub(r"(?:_modified_api)+$", "", stem, flags=re.IGNORECASE)
    if not stem:
        fallback_path = Path(fallback)
        stem = fallback_path.stem or "workflow"
        suffix = fallback_path.suffix
    return safe_name(f"{stem}{suffix}", fallback)


def workflow_name_from_path(path: Path, workflow_id: str) -> str:
    prefix = f"{workflow_id}_"
    name = path.name[len(prefix) :] if path.name.startswith(prefix) else path.name
    return canonical_workflow_name(name)


def is_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], (str, int))


def display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def personal_capacity_value(value: Any) -> int:
    try:
        capacity = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_PERSONAL_CAPACITY
    if MIN_PERSONAL_CAPACITY <= capacity <= MAX_PERSONAL_CAPACITY:
        return capacity
    return DEFAULT_PERSONAL_CAPACITY


def key_capacity(api_type: str, personal_capacity: int = DEFAULT_PERSONAL_CAPACITY) -> int:
    """Return the local scheduler cap for the RunningHub account type."""
    normalized = str(api_type or "").lower()
    return 100 if any(label in normalized for label in ("enterprise", "shared", "wallet")) else personal_capacity_value(personal_capacity)


def mask_key(value: str) -> str:
    value = str(value or "")
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}••••{value[-4:]}"


_SENSITIVE_DETAIL_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "token",
    "password",
    "secret",
)


def redact_detail(value: Any, key: str = "") -> Any:
    """Make API error details safe to persist in the local task history."""
    key_lower = str(key).lower().replace("-", "_")
    if any(marker in key_lower for marker in _SENSITIVE_DETAIL_KEYS):
        return "[已脱敏]"
    if isinstance(value, dict):
        return {str(item_key): redact_detail(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_detail(item) for item in value]
    if isinstance(value, str):
        return mask_secret(value)[:12000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:12000]


def detail_json(value: Any) -> str:
    return json.dumps(redact_detail(value), ensure_ascii=False, default=str)


def remote_workflow_id(workflow: dict[str, Any]) -> str:
    metadata = workflow.get(WORKFLOW_META_KEY)
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get("workflowId")
    if value is None:
        value = metadata.get("workflow_id")
    return str(value or "").strip()


def workflow_nodes(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return only ComfyUI nodes, excluding RH Workflow Desk metadata."""
    return {node_id: node for node_id, node in workflow.items() if node_id != WORKFLOW_META_KEY}


def metadata_bypassed_nodes(workflow: dict[str, Any]) -> list[str]:
    """Read Web-app node bypass state from local workflow metadata."""
    metadata = workflow.get(WORKFLOW_META_KEY)
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("bypassedNodes")
    if raw is None:
        raw = metadata.get("bypassed_nodes")
    if raw is None:
        # Accept the pre-node-bypass metadata written by older Web builds.
        raw = metadata.get("bypassedInputs")
    if raw is None:
        raw = metadata.get("bypassed_inputs")
    if isinstance(raw, dict):
        raw = [key for key, enabled in raw.items() if enabled]
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        input_id = str(value or "").strip()
        if input_id and input_id not in seen:
            result.append(input_id)
            seen.add(input_id)
    return result


def random_noise_spec(node_id: str, node: dict[str, Any]) -> dict[str, Any] | None:
    if str(node.get("class_type", "")).lower() != "randomnoise":
        return None
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    seed_field = "noise_seed" if "noise_seed" in inputs or "seed" not in inputs else "seed"
    return {
        "id": str(node_id),
        "node_id": str(node_id),
        "title": str(node.get("_meta", {}).get("title") or "RandomNoise"),
        "class_type": str(node.get("class_type") or "RandomNoise"),
        "seed_field": seed_field,
        "mode_field": "mode",
        "seed": display_value(inputs.get(seed_field, 0)),
        "mode": str(inputs.get("mode") or "randomize"),
    }


def resolution_spec(node_id: str, node: dict[str, Any]) -> dict[str, Any] | None:
    if str(node.get("class_type", "")).lower() != "resolutionselector":
        return None
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    return {
        "id": str(node_id),
        "node_id": str(node_id),
        "title": str(node.get("_meta", {}).get("title") or "ResolutionSelector"),
        "class_type": str(node.get("class_type") or "ResolutionSelector"),
        "aspect_ratio": str(inputs.get("aspect_ratio") or RESOLUTION_ASPECT_RATIOS[0]),
        "megapixels": display_value(inputs.get("megapixels", 0.4)),
        "aspect_ratio_options": list(RESOLUTION_ASPECT_RATIOS),
    }


def inspect_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes = workflow_nodes(workflow)
    _validate_api_workflow(nodes)
    files: list[dict[str, Any]] = []
    prompts: list[dict[str, Any]] = []
    random_noise: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    for node_id, node in nodes.items():
        class_type = str(node.get("class_type", ""))
        lower_class = class_type.lower()
        title = str(node.get("_meta", {}).get("title") or class_type)
        inputs = node.get("inputs", {})
        noise = random_noise_spec(str(node_id), node)
        if noise:
            random_noise.append(noise)
        resolution = resolution_spec(str(node_id), node)
        if resolution:
            resolutions.append(resolution)
        for field, value in inputs.items():
            lower_field = str(field).lower()
            if is_link(value):
                continue
            input_id = f"{node_id}:{field}"
            if lower_field in FILE_FIELDS and any(hint in lower_class for hint in FILE_CLASS_HINTS):
                files.append(
                    {
                        "id": input_id,
                        "node_id": str(node_id),
                        "field": str(field),
                        "title": title,
                        "class_type": class_type,
                        "default": display_value(value),
                        "required": True,
                    }
                )
                continue
            if (
                lower_field in PROMPT_FIELDS
                and any(hint in lower_class for hint in PROMPT_CLASS_HINTS)
            ) or (lower_field == "prompt" and not is_link(value)):
                prompts.append(
                    {
                        "id": input_id,
                        "node_id": str(node_id),
                        "field": str(field),
                        "title": title,
                        "class_type": class_type,
                        "default": display_value(value),
                        "required": False,
                    }
                )
    return {
        "file_inputs": files,
        "prompt_inputs": prompts,
        "file_count": len(files),
        "prompt_count": len(prompts),
        "random_noise_inputs": random_noise,
        "random_noise_count": len(random_noise),
        "resolution_inputs": resolutions,
        "resolution_count": len(resolutions),
        "bypassed_nodes": metadata_bypassed_nodes(workflow),
        "remote_workflow_id": remote_workflow_id(workflow),
    }


def workflow_input_catalog(workflow: dict[str, Any], analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return editable scalar inputs for a saved workflow configuration UI."""
    current = analysis or inspect_workflow(workflow)
    automatic: dict[str, dict[str, Any]] = {}
    for kind, key in (("file", "file_inputs"), ("prompt", "prompt_inputs")):
        for item in current.get(key) or []:
            automatic[str(item.get("id") or "")] = {"kind": kind, "required": bool(item.get("required"))}
    for item in current.get("resolution_inputs") or []:
        node_id = str(item.get("node_id") or item.get("id") or "")
        if node_id:
            automatic[f"{node_id}:{WORKFLOW_VIRTUAL_FIELDS['resolution']}"] = {"kind": "resolution", "required": False}
    for item in current.get("random_noise_inputs") or []:
        node_id = str(item.get("node_id") or item.get("id") or "")
        if node_id:
            automatic[f"{node_id}:{WORKFLOW_VIRTUAL_FIELDS['random_noise']}"] = {"kind": "random_noise", "required": False}

    catalog: list[dict[str, Any]] = []
    for node_id, node in workflow_nodes(workflow).items():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        title = str(node.get("_meta", {}).get("title") or node.get("class_type") or node_id)
        class_type = str(node.get("class_type") or "")
        for kind, virtual_field in WORKFLOW_VIRTUAL_FIELDS.items():
            if kind == "resolution" and class_type.lower() == "resolutionselector":
                input_id = f"{node_id}:{virtual_field}"
                catalog.append({
                    "id": input_id, "node_id": str(node_id), "field": "", "title": title,
                    "class_type": class_type, "label": title, "kind": kind,
                    "required": automatic.get(input_id, {}).get("required", False), "virtual": True,
                })
            if kind == "random_noise" and class_type.lower() == "randomnoise":
                input_id = f"{node_id}:{virtual_field}"
                catalog.append({
                    "id": input_id, "node_id": str(node_id), "field": "", "title": title,
                    "class_type": class_type, "label": title, "kind": kind,
                    "required": automatic.get(input_id, {}).get("required", False), "virtual": True,
                })
        for field, value in node["inputs"].items():
            if is_link(value) or isinstance(value, (list, dict, tuple)):
                continue
            input_id = f"{node_id}:{field}"
            automatic_item = automatic.get(input_id, {})
            catalog.append({
                "id": input_id, "node_id": str(node_id), "field": str(field), "title": title,
                "class_type": class_type, "label": f"{title} · {field}",
                "kind": automatic_item.get("kind", "text"),
                "required": automatic_item.get("required", False), "default": display_value(value),
                "default_value": value,
            })
    return catalog


def normalize_workflow_input_config(
    workflow: dict[str, Any], config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate the optional per-library-workflow input configuration."""
    if config is None:
        return None
    if not isinstance(config, dict):
        raise RhCliError("INVALID_WORKFLOW_INPUT_CONFIG", "工作流输入配置必须是对象。")
    mode = str(config.get("mode") or "auto").strip().lower()
    if mode not in {"auto", "manual"}:
        raise RhCliError("INVALID_WORKFLOW_INPUT_CONFIG", "工作流输入配置模式只能是 auto 或 manual。")
    if mode == "auto":
        return {"mode": "auto", "items": []}
    raw_items = config.get("items")
    if not isinstance(raw_items, list):
        raise RhCliError("INVALID_WORKFLOW_INPUT_CONFIG", "手动输入配置必须是 items 列表。")
    nodes = workflow_nodes(workflow)
    catalog = {str(item["id"]): item for item in workflow_input_catalog(workflow)}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise RhCliError("INVALID_WORKFLOW_INPUT_CONFIG", f"第 {position + 1} 个输入配置不是对象。")
        node_id = str(raw.get("node_id") or "").strip()
        field = str(raw.get("field") or "").strip()
        input_id = str(raw.get("id") or "").strip()
        if not input_id and node_id:
            input_id = f"{node_id}:{field or WORKFLOW_VIRTUAL_FIELDS.get(str(raw.get('kind') or '').strip(), '')}"
        entry = catalog.get(input_id)
        if entry:
            node_id = str(entry["node_id"])
            field = str(entry.get("field") or "")
        if node_id not in nodes:
            raise RhCliError("INVALID_WORKFLOW_INPUT_CONFIG", f"找不到输入配置节点：{node_id}")
        kind = str(raw.get("kind") or (entry or {}).get("kind") or "text").strip().lower()
        if kind not in WORKFLOW_INPUT_KINDS:
            raise RhCliError("INVALID_WORKFLOW_INPUT_CONFIG", f"输入 {input_id} 的类型不支持：{kind}")
        virtual_field = WORKFLOW_VIRTUAL_FIELDS.get(kind)
        if kind in WORKFLOW_VIRTUAL_FIELDS:
            field = ""
            input_id = f"{node_id}:{virtual_field}"
            node_class = str(nodes[node_id].get("class_type") or "").lower()
            expected = "resolutionselector" if kind == "resolution" else "randomnoise"
            if node_class != expected:
                raise RhCliError("INVALID_WORKFLOW_INPUT_CONFIG", f"节点 {node_id} 不是有效的 {kind} 节点。")
        else:
            if not field or field not in (nodes[node_id].get("inputs") or {}):
                raise RhCliError("INVALID_WORKFLOW_INPUT_CONFIG", f"找不到输入字段：{node_id}:{field}")
            input_id = f"{node_id}:{field}"
        if input_id in seen:
            raise RhCliError("INVALID_WORKFLOW_INPUT_CONFIG", f"输入配置重复：{input_id}")
        seen.add(input_id)
        options = raw.get("options", [])
        if kind == "select":
            if not isinstance(options, list) or not options or any(not isinstance(option, (str, int, float, bool)) for option in options):
                raise RhCliError("INVALID_WORKFLOW_INPUT_CONFIG", f"下拉输入 {input_id} 必须提供选项列表。")
            options = [str(option) for option in options]
        else:
            options = []
        label = str(raw.get("label") or (entry or {}).get("label") or (entry or {}).get("title") or field or node_id).strip()
        if not label:
            label = input_id
        items.append({
            "id": input_id,
            "node_id": node_id,
            "field": field,
            "title": str((entry or {}).get("title") or nodes[node_id].get("_meta", {}).get("title") or nodes[node_id].get("class_type") or node_id),
            "class_type": str((entry or {}).get("class_type") or nodes[node_id].get("class_type") or ""),
            "label": label[:160],
            "kind": kind,
            "required": bool(raw.get("required", (entry or {}).get("required", False))),
            "options": options,
            "virtual": bool((entry or {}).get("virtual")),
            "order": position,
        })
    return {"mode": "manual", "items": items}


def configured_workflow_analysis(
    workflow: dict[str, Any], config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Replace automatic inputs with a saved manual configuration when enabled."""
    base = inspect_workflow(workflow)
    normalized = normalize_workflow_input_config(workflow, config)
    if not normalized or normalized.get("mode") != "manual":
        base["input_mode"] = "auto"
        base["custom_inputs"] = []
        base["custom_input_count"] = 0
        return base
    nodes = workflow_nodes(workflow)
    files: list[dict[str, Any]] = []
    prompts: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    random_noise: list[dict[str, Any]] = []
    custom: list[dict[str, Any]] = []
    for item in normalized["items"]:
        node = nodes[item["node_id"]]
        inputs = node.get("inputs") or {}
        if item["kind"] == "resolution":
            spec = resolution_spec(item["node_id"], node) or {}
            spec.update({"title": item["label"], "required": item["required"], "config_id": item["id"]})
            resolutions.append(spec)
        elif item["kind"] == "random_noise":
            spec = random_noise_spec(item["node_id"], node) or {}
            spec.update({"title": item["label"], "config_id": item["id"]})
            random_noise.append(spec)
        elif item["kind"] == "file":
            files.append({**item, "default": display_value(inputs.get(item["field"]))})
        elif item["kind"] == "prompt":
            prompts.append({**item, "default": display_value(inputs.get(item["field"]))})
        else:
            custom.append({**item, "default": display_value(inputs.get(item["field"]))})
    result = dict(base)
    result.update({
        "file_inputs": files,
        "prompt_inputs": prompts,
        "resolution_inputs": resolutions,
        "random_noise_inputs": random_noise,
        "custom_inputs": custom,
        "file_count": len(files),
        "prompt_count": len(prompts),
        "resolution_count": len(resolutions),
        "random_noise_count": len(random_noise),
        "custom_input_count": len(custom),
        "input_mode": "manual",
    })
    return result


def normalize_custom_input_values(
    workflow: dict[str, Any], analysis: dict[str, Any], values: dict[str, Any] | None,
) -> dict[str, Any]:
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise RhCliError("INVALID_CUSTOM_INPUT", "自定义输入值必须是对象。")
    result: dict[str, Any] = {}
    for item in analysis.get("custom_inputs") or []:
        input_id = str(item.get("id") or "")
        if not input_id or input_id not in values:
            continue
        raw = values[input_id]
        kind = str(item.get("kind") or "text")
        if kind == "number":
            try:
                number = float(str(raw).strip())
            except (TypeError, ValueError) as exc:
                raise RhCliError("INVALID_CUSTOM_INPUT", f"输入 {item.get('label') or input_id} 必须是数字。") from exc
            result[input_id] = int(number) if number.is_integer() else number
        elif kind == "boolean":
            if isinstance(raw, bool):
                result[input_id] = raw
            elif str(raw).strip().lower() in {"true", "1", "yes", "on"}:
                result[input_id] = True
            elif str(raw).strip().lower() in {"false", "0", "no", "off"}:
                result[input_id] = False
            else:
                raise RhCliError("INVALID_CUSTOM_INPUT", f"输入 {item.get('label') or input_id} 必须是布尔值。")
        else:
            result[input_id] = str(raw)
        if kind == "select" and result[input_id] not in (item.get("options") or []):
            raise RhCliError("INVALID_CUSTOM_INPUT", f"输入 {item.get('label') or input_id} 的选项无效。")
    required = [
        item for item in analysis.get("custom_inputs") or []
        if item.get("required") and not str(result.get(str(item.get("id") or ""), "")).strip()
    ]
    if required:
        raise RhCliError("MISSING_INPUT", "请填写所有必填的自定义输入。", detail={"inputs": [item.get("id") for item in required]})
    return result


def apply_custom_input_values(workflow: dict[str, Any], values: dict[str, Any] | None) -> list[str]:
    changes: list[str] = []
    for input_id, value in (values or {}).items():
        node_id, separator, field = str(input_id).partition(":")
        if not separator or not field or node_id not in workflow:
            continue
        node = workflow.get(node_id)
        if not isinstance(node, dict):
            continue
        inputs = node.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            continue
        inputs[field] = value
        changes.append(f"{node_id}.{field}")
    return changes


def normalize_bypassed_nodes(
    workflow: dict[str, Any], values: list[str] | dict[str, Any] | None,
) -> list[str]:
    """Validate node IDs whose execution should be bypassed."""
    if values is None:
        raw_values: Any = metadata_bypassed_nodes(workflow)
    elif isinstance(values, dict):
        raw_values = [key for key, enabled in values.items() if enabled]
    elif isinstance(values, (list, tuple, set)):
        raw_values = values
    else:
        raise RhCliError("INVALID_BYPASS", "输入旁路配置必须是列表或对象。")

    known = {str(node_id) for node_id in workflow_nodes(workflow)}
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        node_id = str(raw_value or "").strip()
        if not node_id:
            continue
        # Convert IDs saved by the previous input-override implementation.
        if node_id not in known and ":" in node_id:
            node_id = node_id.split(":", 1)[0]
        if node_id not in known:
            raise RhCliError("INVALID_BYPASS", f"找不到可旁路的工作流节点：{node_id}")
        if node_id in seen:
            continue
        result.append(node_id)
        seen.add(node_id)
    return result


def apply_bypassed_nodes(workflow: dict[str, Any], bypassed_nodes: set[str]) -> list[str]:
    """Remove bypassed nodes and direct links to their outputs from an API graph."""
    if not bypassed_nodes:
        return []
    nodes = workflow_nodes(workflow)
    removed = sorted(node_id for node_id in bypassed_nodes if node_id in nodes)
    for node_id in removed:
        workflow.pop(node_id, None)
    removed_set = set(removed)
    for node in workflow_nodes(workflow).values():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            continue
        for field, value in list(inputs.items()):
            if is_link(value) and str(value[0]) in removed_set:
                inputs.pop(field, None)
    return removed


def normalize_random_noise_inputs(
    workflow: dict[str, Any], values: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise RhCliError("INVALID_RANDOM_NOISE", "RandomNoise 配置必须是对象。")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_node_id, raw_config in values.items():
        node_id = str(raw_node_id).strip()
        node = workflow.get(node_id)
        if not isinstance(node, dict) or str(node.get("class_type", "")).lower() != "randomnoise":
            raise RhCliError("INVALID_RANDOM_NOISE", f"节点 {node_id} 不是有效的 RandomNoise 节点。")
        if not isinstance(raw_config, dict):
            raise RhCliError("INVALID_RANDOM_NOISE", f"RandomNoise 节点 {node_id} 配置无效。")
        raw_seed = raw_config.get("seed")
        try:
            seed = int(str(raw_seed).strip())
        except (TypeError, ValueError) as exc:
            raise RhCliError("INVALID_RANDOM_NOISE", f"RandomNoise 节点 {node_id} 的随机种子必须是整数。") from exc
        mode = str(raw_config.get("mode") or "").strip().lower()
        if mode not in RANDOM_NOISE_MODES:
            raise RhCliError("INVALID_RANDOM_NOISE", f"RandomNoise 节点 {node_id} 的模式只能是 fixed 或 randomize。")
        normalized[node_id] = {"seed": seed, "mode": mode}
    return normalized


def apply_random_noise_inputs(workflow: dict[str, Any], values: dict[str, Any] | None) -> list[str]:
    normalized = normalize_random_noise_inputs(workflow, values)
    changes: list[str] = []
    for node_id, config in normalized.items():
        node = workflow[node_id]
        inputs = node.setdefault("inputs", {})
        seed_field = "noise_seed" if "noise_seed" in inputs or "seed" not in inputs else "seed"
        inputs[seed_field] = config["seed"]
        inputs["mode"] = config["mode"]
        changes.append(f"{node_id}.{seed_field}={config['seed']}")
        changes.append(f"{node_id}.mode={config['mode']}")
    return changes


def normalize_resolution_inputs(
    workflow: dict[str, Any], values: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise RhCliError("INVALID_RESOLUTION", "尺寸节点配置必须是对象。")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_node_id, raw_config in values.items():
        node_id = str(raw_node_id).strip()
        node = workflow.get(node_id)
        if not isinstance(node, dict) or str(node.get("class_type", "")).lower() != "resolutionselector":
            raise RhCliError("INVALID_RESOLUTION", f"节点 {node_id} 不是有效的 ResolutionSelector 节点。")
        if not isinstance(raw_config, dict):
            raise RhCliError("INVALID_RESOLUTION", f"尺寸节点 {node_id} 配置无效。")
        aspect_ratio = str(raw_config.get("aspect_ratio") or "").strip()
        if aspect_ratio not in RESOLUTION_ASPECT_RATIOS:
            raise RhCliError("INVALID_RESOLUTION", f"尺寸节点 {node_id} 的比例选项无效。")
        try:
            megapixels = float(str(raw_config.get("megapixels")).strip())
        except (TypeError, ValueError) as exc:
            raise RhCliError("INVALID_RESOLUTION", f"尺寸节点 {node_id} 的 megapixels 必须是数字。") from exc
        if not 0.1 <= megapixels <= 4:
            raise RhCliError("INVALID_RESOLUTION", f"尺寸节点 {node_id} 的 megapixels 范围必须是 0.1 到 4。")
        normalized[node_id] = {"aspect_ratio": aspect_ratio, "megapixels": megapixels, "multiple": 32}
    return normalized


def apply_resolution_inputs(workflow: dict[str, Any], values: dict[str, Any] | None) -> list[str]:
    normalized = normalize_resolution_inputs(workflow, values)
    changes: list[str] = []
    for node_id, config in normalized.items():
        node = workflow[node_id]
        inputs = node.setdefault("inputs", {})
        inputs["aspect_ratio"] = config["aspect_ratio"]
        inputs["megapixels"] = config["megapixels"]
        inputs["multiple"] = 32
        changes.append(f"{node_id}.aspect_ratio={config['aspect_ratio']}")
        changes.append(f"{node_id}.megapixels={config['megapixels']}")
    return changes


def public_key(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
        "account_id": str(record.get("account_id") or ""),
        "site": record["site"],
        "masked_key": mask_key(record.get("api_key", "")),
        "status": record.get("status", "unchecked"),
        "status_message": record.get("status_message", ""),
        "api_type": record.get("api_type", ""),
        "capacity": int(record.get("capacity") or key_capacity(record.get("api_type", ""))),
        "active_tasks": int(record.get("active_tasks", 0)),
        "balance": record.get("balance", ""),
        "coins": record.get("coins", ""),
        "symbol": record.get("symbol", "¥" if record["site"] == "cn" else "$"),
        "balance_checked_at": int(record.get("balance_checked_at") or 0),
        "checked_at": record.get("checked_at", 0),
    }


ACCOUNT_STATUSES = {"login_required", "ready", "checking", "checked_in", "not_checked_in", "error"}


def public_account(record: dict[str, Any]) -> dict[str, Any]:
    """Expose account hosting state without returning browser credentials."""
    return {
        "id": str(record.get("id") or ""),
        "name": str(record.get("name") or ""),
        "site": "cn" if record.get("site") == "cn" else "ai",
        "status": str(record.get("status") or "login_required"),
        "status_message": str(record.get("status_message") or ""),
        "last_login_at": int(record.get("last_login_at") or 0),
        "last_checkin_at": int(record.get("last_checkin_at") or 0),
        "daily_coin": str(record.get("daily_coin") or ""),
        "balance": str(record.get("balance") or ""),
        "checked_at": int(record.get("checked_at") or 0),
    }


class LocalStore:
    def __init__(self) -> None:
        for directory in (DATA_ROOT, WORKFLOW_ROOT, OUTPUT_ROOT):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              started_at INTEGER,
              completed_at INTEGER,
              status TEXT NOT NULL,
              progress TEXT NOT NULL DEFAULT '',
              workflow_path TEXT NOT NULL,
              workflow_name TEXT NOT NULL,
              key_id TEXT,
              account_id TEXT NOT NULL DEFAULT '',
              instance_type TEXT NOT NULL DEFAULT 'default',
              dispatch_key_name TEXT NOT NULL DEFAULT '',
              dispatch_key_site TEXT NOT NULL DEFAULT '',
              dispatch_key_api_type TEXT NOT NULL DEFAULT '',
              remote_task_id TEXT,
              remote_workflow_id TEXT NOT NULL DEFAULT '',
              input_json TEXT NOT NULL,
              prompt_json TEXT NOT NULL,
              custom_json TEXT NOT NULL DEFAULT '{}',
              input_config_json TEXT NOT NULL DEFAULT '{}',
              bypass_json TEXT NOT NULL DEFAULT '[]',
              random_noise_json TEXT NOT NULL DEFAULT '{}',
              resolution_json TEXT NOT NULL DEFAULT '{}',
              workflow_snapshot_path TEXT NOT NULL DEFAULT '',
              output_dir TEXT NOT NULL,
              outputs_json TEXT NOT NULL DEFAULT '[]',
              error TEXT NOT NULL DEFAULT '',
              error_detail TEXT NOT NULL DEFAULT '{}',
              stage_logs_json TEXT NOT NULL DEFAULT '[]',
              cost_type TEXT,
              cost TEXT,
              duration TEXT
            )
            """
        )
        self._migrate_schema()
        self._db.commit()
        self._interrupt_incomplete()

    def _migrate_schema(self) -> None:
        """Add fields introduced by newer web builds to an existing local database."""
        columns = {str(row[1]) for row in self._db.execute("PRAGMA table_info(tasks)").fetchall()}
        if "remote_workflow_id" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN remote_workflow_id TEXT NOT NULL DEFAULT ''")
        if "error_detail" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN error_detail TEXT NOT NULL DEFAULT '{}'")
        if "stage_logs_json" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN stage_logs_json TEXT NOT NULL DEFAULT '[]'")
        if "random_noise_json" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN random_noise_json TEXT NOT NULL DEFAULT '{}'")
        if "resolution_json" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN resolution_json TEXT NOT NULL DEFAULT '{}'")
        if "bypass_json" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN bypass_json TEXT NOT NULL DEFAULT '[]'")
        if "custom_json" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN custom_json TEXT NOT NULL DEFAULT '{}'")
        if "input_config_json" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN input_config_json TEXT NOT NULL DEFAULT '{}'")
        if "workflow_snapshot_path" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN workflow_snapshot_path TEXT NOT NULL DEFAULT ''")
        if "dispatch_key_name" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN dispatch_key_name TEXT NOT NULL DEFAULT ''")
        if "dispatch_key_site" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN dispatch_key_site TEXT NOT NULL DEFAULT ''")
        if "dispatch_key_api_type" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN dispatch_key_api_type TEXT NOT NULL DEFAULT ''")
        if "account_id" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
        if "instance_type" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN instance_type TEXT NOT NULL DEFAULT 'default'")
        self._backfill_dispatch_credential_snapshots()

    def _infer_key_account_id(self, key: dict[str, Any], accounts: list[dict[str, Any]]) -> str:
        """Infer legacy Key ownership from an explicit account-name/prefix match."""
        key_name = str(key.get("name") or "").strip().lower()
        if not key_name:
            return ""
        for account in accounts:
            account_name = str(account.get("name") or "").strip().lower()
            if account_name and (key_name == account_name or key_name.startswith(account_name + "-") or key_name.startswith(account_name + "_")):
                return str(account.get("id") or "")
        site = "cn" if key.get("site") == "cn" else "ai"
        prefix_matches = [
            account for account in accounts
            if str(account.get("site") or "") == site
            and (key_name.startswith(site + "-") or key_name.startswith(site + "_"))
        ]
        return str(prefix_matches[0].get("id") or "") if len(prefix_matches) == 1 else ""

    def _backfill_dispatch_credential_snapshots(self) -> None:
        """Fill immutable credential labels for older tasks when the Key still exists."""
        data = self._read_json_file()
        raw_keys = data.get("keys", [])
        if not isinstance(raw_keys, list):
            return
        keys = {
            str(item.get("id")): item
            for item in raw_keys
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        if not keys:
            return
        rows = self._db.execute(
            "SELECT id,key_id FROM tasks "
            "WHERE status != 'queued' AND key_id IS NOT NULL AND key_id != '' "
            "AND COALESCE(dispatch_key_name, '') = ''"
        ).fetchall()
        updates = []
        for row in rows:
            key = keys.get(str(row[1]))
            if not key:
                continue
            updates.append(
                (
                    str(key.get("name") or ""),
                    "cn" if key.get("site") == "cn" else "ai",
                    str(key.get("api_type") or ""),
                    row[0],
                )
            )
        if updates:
            self._db.executemany(
                "UPDATE tasks SET dispatch_key_name=?, dispatch_key_site=?, dispatch_key_api_type=? WHERE id=?",
                updates,
            )

    def _interrupt_incomplete(self) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='interrupted', progress='应用重新启动，准备检查本地产物并恢复轮询', updated_at=? "
                "WHERE status IN ('submitting', 'running')",
                (now_ms(),),
            )
            self._db.commit()

    def _read_json_file(self) -> dict[str, Any]:
        if not KEYS_PATH.exists():
            return {}
        try:
            data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_json_file(self, data: dict[str, Any]) -> None:
        KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = KEYS_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(KEYS_PATH)

    def keys(self) -> list[dict[str, Any]]:
        with self._lock:
            data = self._read_json_file()
            accounts = self.accounts()
            changed = False
            configured_capacity = personal_capacity_value(data.get("personal_capacity", DEFAULT_PERSONAL_CAPACITY))
            raw_keys = data.get("keys", [])
            if not isinstance(raw_keys, list):
                raw_keys = []
            if not raw_keys and not data.get("initialized"):
                imported: list[dict[str, Any]] = []
                for name, api_key in list_keys().items():
                    site = "cn" if str(name).lower().startswith("cn-") else "ai"
                    imported.append(
                        {
                            "id": f"key_{uuid.uuid4().hex[:12]}",
                            "name": name,
                            "account_id": self._infer_key_account_id({"name": name, "site": site}, accounts),
                            "site": site,
                            "api_key": api_key,
                            "status": "unchecked",
                            "status_message": "从 rh CLI 配置导入，尚未检测",
                            "api_type": "",
                            "capacity": configured_capacity,
                            "active_tasks": 0,
                            "balance": "",
                            "coins": "",
                            "symbol": "¥" if site == "cn" else "$",
                            "balance_checked_at": 0,
                            "checked_at": 0,
                        }
                    )
                if imported:
                    data.update(
                        {
                            "keys": imported,
                            "output_dir": data.get("output_dir") or str(default_local_output_dir()),
                            "personal_capacity": configured_capacity,
                            "initialized": True,
                        }
                    )
                    self._write_json_file(data)
                    raw_keys = imported
                else:
                    data["initialized"] = True
                    data["personal_capacity"] = configured_capacity
                    self._write_json_file(data)
            result: list[dict[str, Any]] = []
            for item in raw_keys:
                if not isinstance(item, dict) or not str(item.get("api_key", "")).strip():
                    continue
                normalized = dict(item)
                normalized["site"] = "cn" if item.get("site") == "cn" else "ai"
                account_id = str(item.get("account_id") or "").strip()
                if not account_id:
                    account_id = self._infer_key_account_id(normalized, accounts)
                    if account_id:
                        item["account_id"] = account_id
                        changed = True
                normalized["account_id"] = account_id
                api_type = str(item.get("api_type") or "")
                normalized["capacity"] = key_capacity(api_type, configured_capacity)
                normalized["active_tasks"] = int(item.get("active_tasks") or 0)
                result.append(normalized)
            if changed:
                data["keys"] = raw_keys
                self._write_json_file(data)
            return result

    def get_key(self, key_id: str) -> dict[str, Any] | None:
        return next((item for item in self.keys() if item["id"] == key_id), None)

    def save_keys(self, records: list[dict[str, Any]]) -> None:
        with self._lock:
            data = self._read_json_file()
            data["keys"] = records
            data["initialized"] = True
            if "output_dir" not in data:
                data["output_dir"] = str(default_local_output_dir())
            data["personal_capacity"] = personal_capacity_value(data.get("personal_capacity", DEFAULT_PERSONAL_CAPACITY))
            self._write_json_file(data)

    def current_account_id(self) -> str:
        with self._lock:
            data = self._read_json_file()
            configured = str(data.get("current_account_id") or "").strip()
            accounts = self.accounts()
            account_ids = {str(item.get("id") or "") for item in accounts}
            if configured == GENERAL_ACCOUNT_ID:
                return GENERAL_ACCOUNT_ID
            if configured and configured in account_ids:
                return configured
            if not accounts:
                return ""
            selected = str(accounts[0]["id"])
            data["current_account_id"] = selected
            self._write_json_file(data)
            return selected

    def current_account(self) -> dict[str, Any] | None:
        account_id = self.current_account_id()
        return self.get_account(account_id) if account_id else None

    def set_current_account(self, account_id: str) -> dict[str, Any]:
        account_id = str(account_id or "").strip()
        if account_id == GENERAL_ACCOUNT_ID:
            with self._lock:
                data = self._read_json_file()
                data["current_account_id"] = GENERAL_ACCOUNT_ID
                self._write_json_file(data)
            return {"id": GENERAL_ACCOUNT_ID, "name": "通用模式", "site": "", "status": "ready"}
        account = self.get_account(account_id)
        if not account:
            raise RhCliError("ACCOUNT_NOT_FOUND", "找不到要切换的账号。")
        with self._lock:
            data = self._read_json_file()
            data["current_account_id"] = account_id
            self._write_json_file(data)
        return account

    def _read_accounts_file(self) -> dict[str, Any]:
        if not ACCOUNTS_PATH.exists():
            return {"accounts": []}
        try:
            data = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"accounts": []}
        except (OSError, ValueError):
            return {"accounts": []}

    def _write_accounts_file(self, data: dict[str, Any]) -> None:
        ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = ACCOUNTS_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(ACCOUNTS_PATH)

    def accounts(self) -> list[dict[str, Any]]:
        """Read managed accounts; the file intentionally contains no passwords or tokens."""
        with self._lock:
            raw_accounts = self._read_accounts_file().get("accounts", [])
            if not isinstance(raw_accounts, list):
                raw_accounts = []
            result: list[dict[str, Any]] = []
            for item in raw_accounts:
                if not isinstance(item, dict) or not str(item.get("id") or "").strip():
                    continue
                status = str(item.get("status") or "login_required")
                result.append(
                    {
                        "id": str(item["id"]),
                        "name": str(item.get("name") or "未命名账号"),
                        "site": "cn" if item.get("site") == "cn" else "ai",
                        "status": status if status in ACCOUNT_STATUSES else "login_required",
                        "status_message": str(item.get("status_message") or "首次使用请登录账号"),
                        "last_login_at": int(item.get("last_login_at") or 0),
                        "last_checkin_at": int(item.get("last_checkin_at") or 0),
                        "daily_coin": str(item.get("daily_coin") or ""),
                        "balance": str(item.get("balance") or ""),
                        "checked_at": int(item.get("checked_at") or 0),
                    }
                )
            return result

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        account_id = str(account_id or "").strip()
        return next((item for item in self.accounts() if item["id"] == account_id), None)

    def save_accounts(self, records: list[dict[str, Any]]) -> None:
        with self._lock:
            # Only the explicitly listed fields are persisted. In particular, a
            # renderer or future caller cannot accidentally put a token/password
            # into the local account registry.
            clean: list[dict[str, Any]] = []
            for item in records:
                if not isinstance(item, dict) or not str(item.get("id") or "").strip():
                    continue
                clean.append(
                    {
                        "id": str(item["id"]),
                        "name": str(item.get("name") or "未命名账号"),
                        "site": "cn" if item.get("site") == "cn" else "ai",
                        "status": str(item.get("status") or "login_required") if str(item.get("status") or "") in ACCOUNT_STATUSES else "login_required",
                        "status_message": str(item.get("status_message") or ""),
                        "last_login_at": int(item.get("last_login_at") or 0),
                        "last_checkin_at": int(item.get("last_checkin_at") or 0),
                        "daily_coin": str(item.get("daily_coin") or ""),
                        "balance": str(item.get("balance") or ""),
                        "checked_at": int(item.get("checked_at") or 0),
                    }
                )
            self._write_accounts_file({"accounts": clean})

    def add_account(self, name: str, site: str) -> dict[str, Any]:
        site = "cn" if site == "cn" else "ai"
        records = self.accounts()
        account = {
            "id": f"account_{uuid.uuid4().hex[:12]}",
            "name": str(name or "").strip() or f"{site.upper()} 账号 {len(records) + 1}",
            "site": site,
            "status": "login_required",
            "status_message": "请在打开的 RunningHub 窗口完成登录",
            "last_login_at": 0,
            "last_checkin_at": 0,
            "daily_coin": "",
            "balance": "",
            "checked_at": 0,
        }
        records.append(account)
        self.save_accounts(records)
        return account

    def update_account(self, account_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        account_id = str(account_id or "").strip()
        if not account_id:
            raise RhCliError("ACCOUNT_NOT_FOUND", "账号 ID 不能为空。")
        records = self.accounts()
        account = next((item for item in records if item["id"] == account_id), None)
        if not account:
            raise RhCliError("ACCOUNT_NOT_FOUND", "找不到这个账号。")
        for field in (
            "name", "site", "status", "status_message", "last_login_at",
            "last_checkin_at", "daily_coin", "balance", "checked_at",
        ):
            if field not in changes:
                continue
            if field == "site":
                account[field] = "cn" if changes[field] == "cn" else "ai"
            elif field == "status":
                status = str(changes[field] or "")
                if status not in ACCOUNT_STATUSES:
                    raise RhCliError("INVALID_ACCOUNT_STATUS", "账号状态无效。")
                account[field] = status
            elif field in {"last_login_at", "last_checkin_at", "checked_at"}:
                try:
                    account[field] = max(0, int(changes[field] or 0))
                except (TypeError, ValueError) as exc:
                    raise RhCliError("INVALID_ACCOUNT_TIMESTAMP", "账号时间字段无效。") from exc
            else:
                account[field] = str(changes[field] or "")
        self.save_accounts(records)
        return account

    def remove_account(self, account_id: str) -> None:
        records = self.accounts()
        remaining = [item for item in records if item["id"] != str(account_id or "").strip()]
        if len(remaining) == len(records):
            raise RhCliError("ACCOUNT_NOT_FOUND", "找不到这个账号。")
        self.save_accounts(remaining)

    def output_dir(self) -> str:
        value = self._read_json_file().get("output_dir")
        return str(value).strip() if isinstance(value, str) and value.strip() else str(default_local_output_dir())

    def set_output_dir(self, value: str) -> str:
        path = str(Path(value).expanduser()).strip()
        if not path:
            raise RhCliError("INVALID_OUTPUT_DIR", "输出目录不能为空。")
        Path(path).mkdir(parents=True, exist_ok=True)
        data = self._read_json_file()
        data["output_dir"] = path
        self._write_json_file(data)
        return path

    def action_resources_path(self) -> str:
        value = self._read_json_file().get("action_resources_path")
        return str(value).strip() if isinstance(value, str) and value.strip() else ""

    def set_action_resources_path(self, value: str) -> str:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise RhCliError("INVALID_ACTION_RESOURCES_PATH", f"动作库文件不存在：{path}")
        data = self._read_json_file()
        data["action_resources_path"] = str(path)
        self._write_json_file(data)
        return str(path)

    def prompt_library_path(self) -> str:
        value = self._read_json_file().get("prompt_library_path")
        if isinstance(value, str) and value.strip():
            return str(Path(value).expanduser().resolve())
        return str((Path.home() / "Documents" / "VideoMake" / "ref" / "prompt" / "library.md").resolve())

    def set_prompt_library_path(self, value: str) -> str:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise RhCliError("INVALID_PROMPT_LIBRARY_PATH", f"基础积木 Markdown 文件不存在：{path}")
        if path.suffix.lower() in {".md", ".markdown"}:
            try:
                path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise RhCliError("INVALID_PROMPT_LIBRARY_PATH", f"基础积木 Markdown 文件无法读取：{path}") from exc
            data = self._read_json_file()
            data["prompt_library_path"] = str(path)
            self._write_json_file(data)
            return str(path)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise RhCliError("INVALID_PROMPT_LIBRARY_PATH", f"基础积木 JSON 文件无法读取：{path}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("blocks", []), list):
            raise RhCliError("INVALID_PROMPT_LIBRARY_PATH", "基础积木 JSON 必须是包含 blocks 数组的对象。")
        data = self._read_json_file()
        data["prompt_library_path"] = str(path)
        self._write_json_file(data)
        return str(path)

    def reference_resources_paths(self) -> dict[str, str]:
        value = self._read_json_file().get("reference_resources_paths")
        if not isinstance(value, dict):
            return {}
        return {
            str(kind): str(path).strip()
            for kind, path in value.items()
            if str(kind).strip() and isinstance(path, str) and path.strip()
        }

    def set_reference_resources_paths(self, values: Any) -> dict[str, str]:
        if not isinstance(values, dict):
            raise RhCliError("INVALID_REFERENCE_RESOURCES_PATHS", "参考资源路径必须是对象。")
        allowed = {"character", "audio", "background", "clothes"}
        current = self.reference_resources_paths()
        updated = dict(current)
        for raw_kind, raw_path in values.items():
            kind = str(raw_kind or "").strip()
            if kind not in allowed:
                raise RhCliError("INVALID_REFERENCE_RESOURCE_KIND", f"未知的参考资源类型：{kind}")
            path = Path(str(raw_path or "")).expanduser().resolve()
            if not path.is_file():
                raise RhCliError("INVALID_REFERENCE_RESOURCES_PATH", f"{kind} 资源 Markdown 文件不存在：{path}")
            updated[kind] = str(path)
        data = self._read_json_file()
        data["reference_resources_paths"] = updated
        self._write_json_file(data)
        return updated

    def personal_capacity(self) -> int:
        return personal_capacity_value(self._read_json_file().get("personal_capacity", DEFAULT_PERSONAL_CAPACITY))

    def set_personal_capacity(self, value: Any) -> int:
        try:
            capacity = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise RhCliError("INVALID_PERSONAL_CAPACITY", "个人并发数必须是 1 到 3 的整数。") from exc
        if not MIN_PERSONAL_CAPACITY <= capacity <= MAX_PERSONAL_CAPACITY:
            raise RhCliError("INVALID_PERSONAL_CAPACITY", "个人并发数必须是 1 到 3 的整数。")
        data = self._read_json_file()
        data["personal_capacity"] = capacity
        data.setdefault("output_dir", str(default_local_output_dir()))
        self._write_json_file(data)
        return capacity

    def _workflow_registry_path(self) -> Path:
        return WORKFLOW_ROOT.parent / "workflow-registry.json"

    def _read_workflow_registry(self) -> list[dict[str, Any]]:
        path = self._workflow_registry_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        records = data.get("workflows", []) if isinstance(data, dict) else []
        return [dict(item) for item in records if isinstance(item, dict) and str(item.get("id") or "").strip()]

    def _write_workflow_registry(self, records: list[dict[str, Any]]) -> None:
        path = self._workflow_registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"workflows": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    @staticmethod
    def _workflow_local_id_from_path(path: Path) -> str:
        match = re.match(r"^(wf_[A-Za-z0-9]+)_(.+)$", path.name)
        return match.group(1) if match else path.stem

    def _upsert_workflow_registry(self, record: dict[str, Any]) -> None:
        records = self._read_workflow_registry()
        records = [item for item in records if str(item.get("id") or "") != str(record.get("id") or "")]
        saved = {
            "id": str(record.get("id") or ""),
            "name": str(record.get("name") or "workflow.json"),
            "account_id": str(record.get("account_id") or ""),
            "site": str(record.get("site") or ""),
            "remote_workflow_id": str(record.get("remote_workflow_id") or ""),
            "source_dir": str(record.get("source_dir") or ""),
            "source": "library",
            "created_at": int(record.get("created_at") or now_ms()),
            "updated_at": int(record.get("updated_at") or now_ms()),
        }
        if isinstance(record.get("input_config"), dict):
            saved["input_config"] = record["input_config"]
        records.append(saved)
        self._write_workflow_registry(records)

    def save_workflow(
        self,
        filename: str,
        content: str,
        *,
        account_id: str = "",
        remote_workflow_id: str = "",
        source_dir: str = "",
        register: bool = True,
    ) -> tuple[str, Path, dict[str, Any]]:
        try:
            workflow = json.loads(content)
        except (ValueError, TypeError) as exc:
            raise RhCliError("INVALID_WORKFLOW", "无法解析工作流 JSON。") from exc
        if not isinstance(workflow, dict):
            raise RhCliError("INVALID_WORKFLOW", "工作流顶层必须是 API 格式节点字典。")
        try:
            analysis = inspect_workflow(workflow)
        except RhCliError as exc:
            # ComfyUI's editor format has top-level fields such as id, nodes,
            # links and groups. It is not directly runnable as an API prompt;
            # surface that distinction instead of reporting the first scalar
            # field as if it were a malformed node.
            if isinstance(workflow.get("nodes"), list) and isinstance(workflow.get("links"), list):
                raise RhCliError(
                    "INVALID_WORKFLOW",
                    "检测到 ComfyUI 编辑器工作流格式，请在 ComfyUI 中导出 API 工作流 JSON 后再导入。",
                ) from exc
            raise
        account_id = str(account_id or "").strip()
        if account_id == GENERAL_ACCOUNT_ID:
            account_id = ""
        metadata = workflow.get(WORKFLOW_META_KEY)
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        embedded_account_id = str(metadata.get("accountId") or metadata.get("account_id") or "").strip()
        if account_id and embedded_account_id and account_id != embedded_account_id:
            raise RhCliError("WORKFLOW_ACCOUNT_MISMATCH", "工作流已绑定其他账号，不能导入到当前账号。")
        if not account_id:
            account_id = embedded_account_id
        account = self.get_account(account_id) if account_id else None
        if account_id and not account:
            raise RhCliError("ACCOUNT_NOT_FOUND", "找不到该工作流所属账号。")
        if account_id:
            metadata["accountId"] = account_id
            workflow[WORKFLOW_META_KEY] = metadata
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        clean_name = canonical_workflow_name(filename)
        path = WORKFLOW_ROOT / f"{workflow_id}_{clean_name}"
        path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
        if register:
            self._upsert_workflow_registry(
                {
                    "id": workflow_id,
                    "name": clean_name,
                    "account_id": account["id"] if account else "",
                    "site": account["site"] if account else "",
                    "remote_workflow_id": str(remote_workflow_id or "").strip() or analysis.get("remote_workflow_id", ""),
                    "source_dir": str(source_dir or "").strip(),
                    "created_at": now_ms(),
                    "updated_at": now_ms(),
                }
            )
        return workflow_id, path, analysis

    def workflow_path(self, workflow_id: str) -> Path:
        matches = list(WORKFLOW_ROOT.glob(f"{workflow_id}_*"))
        if not matches:
            raise RhCliError("WORKFLOW_NOT_FOUND", f"找不到工作流：{workflow_id}")
        return matches[0]

    def workflows(self) -> list[dict[str, Any]]:
        """Return local workflow library records without exposing workflow JSON."""
        registry = {
            str(item.get("id")): item
            for item in self._read_workflow_registry()
            if str(item.get("source") or "") == "library"
        }
        accounts = {str(item.get("id")): item for item in self.accounts()}
        result: list[dict[str, Any]] = []
        for local_id, registered in registry.items():
            matches = [path for path in WORKFLOW_ROOT.glob(f"{local_id}_*") if path.is_file()]
            if not matches:
                continue
            path = matches[0]
            try:
                stat = path.stat()
                workflow = json.loads(path.read_text(encoding="utf-8"))
                analysis = inspect_workflow(workflow)
                analysis_error = ""
            except (OSError, ValueError, RhCliError) as exc:
                stat = None
                workflow = {}
                analysis = {}
                analysis_error = str(exc)
            metadata = workflow.get(WORKFLOW_META_KEY) if isinstance(workflow, dict) else {}
            metadata = metadata if isinstance(metadata, dict) else {}
            account_id = str(registered.get("account_id") or metadata.get("accountId") or metadata.get("account_id") or "").strip()
            account = accounts.get(account_id)
            name = str(registered.get("name") or "").strip()
            if not name:
                name = path.name[len(local_id) + 1 :] if path.name.startswith(local_id + "_") else path.name
            site = account.get("site") if account else str(registered.get("site") or "").strip()
            remote_id = str(registered.get("remote_workflow_id") or "").strip() or str(analysis.get("remote_workflow_id") or "").strip()
            created_at = int(registered.get("created_at") or (stat.st_ctime * 1000 if stat else 0))
            updated_at = int(registered.get("updated_at") or (stat.st_mtime * 1000 if stat else 0))
            result.append(
                {
                    "id": local_id,
                    "name": name,
                    "account_id": account_id,
                    "account_name": str(account.get("name") or "") if account else "",
                    "site": site,
                    "remote_workflow_id": remote_id,
                    "source_dir": str(registered.get("source_dir") or ""),
                    "workflow_path": str(path.resolve()),
                    "input_config": registered.get("input_config") if isinstance(registered.get("input_config"), dict) else None,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "file_size": int(stat.st_size if stat else 0),
                    "file_count": int(analysis.get("file_count") or 0),
                    "prompt_count": int(analysis.get("prompt_count") or 0),
                    "resolution_count": int(analysis.get("resolution_count") or 0),
                    "random_noise_count": int(analysis.get("random_noise_count") or 0),
                    "node_count": len(workflow_nodes(workflow)) if isinstance(workflow, dict) else 0,
                    "analysis_error": analysis_error,
                }
            )
        return sorted(result, key=lambda item: (int(item.get("updated_at") or 0), str(item.get("name") or "")), reverse=True)

    def workflow_record(self, workflow_id: str) -> dict[str, Any]:
        workflow_id = str(workflow_id or "").strip()
        record = next((item for item in self.workflows() if item["id"] == workflow_id), None)
        if not record:
            raise RhCliError("WORKFLOW_NOT_FOUND", f"找不到工作流：{workflow_id}")
        return record

    def workflow_account_id(self, workflow_id: str) -> str:
        workflow_id = str(workflow_id or "").strip()
        if not workflow_id:
            return ""
        record = next((item for item in self._read_workflow_registry() if str(item.get("id") or "") == workflow_id), None)
        return str(record.get("account_id") or "").strip() if record else ""

    def workflow_detail(self, workflow_id: str) -> dict[str, Any]:
        record = self.workflow_record(workflow_id)
        path = Path(record["workflow_path"])
        try:
            workflow = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RhCliError("INVALID_WORKFLOW", f"无法读取工作流：{path}") from exc
        if not isinstance(workflow, dict):
            raise RhCliError("INVALID_WORKFLOW", "工作流顶层必须是 API 格式节点字典。")
        analysis = inspect_workflow(workflow)
        analysis["input_catalog"] = workflow_input_catalog(workflow, analysis)
        return {"record": record, "workflow": workflow, "analysis": analysis}

    def update_workflow(self, workflow_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        detail = self.workflow_detail(workflow_id)
        current = detail["record"]
        workflow = detail["workflow"]
        account_id = str(changes["account_id"]).strip() if "account_id" in changes else str(current.get("account_id") or "")
        account = self.get_account(account_id) if account_id else None
        if account_id and not account:
            raise RhCliError("ACCOUNT_NOT_FOUND", "找不到该工作流所属账号。")
        name = str(changes.get("name", current.get("name") or "workflow.json")).strip()
        name = safe_name(name, "workflow.json")
        remote_id = str(changes.get("remote_workflow_id", current.get("remote_workflow_id") or "")).strip()
        input_config = current.get("input_config") if isinstance(current.get("input_config"), dict) else None
        if "input_config" in changes:
            input_config = normalize_workflow_input_config(workflow, changes.get("input_config"))
        record = {
            **current,
            "name": name,
            "account_id": account["id"] if account else "",
            "account_name": account["name"] if account else "",
            "site": account["site"] if account else str(current.get("site") or ""),
            "remote_workflow_id": remote_id,
            "input_config": input_config,
            "updated_at": now_ms(),
        }
        metadata = workflow.get(WORKFLOW_META_KEY)
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if remote_id:
            metadata["workflowId"] = remote_id
        else:
            metadata.pop("workflowId", None)
        if account_id:
            metadata["accountId"] = account_id
        else:
            metadata.pop("accountId", None)
        if metadata:
            workflow[WORKFLOW_META_KEY] = metadata
        else:
            workflow.pop(WORKFLOW_META_KEY, None)
        Path(record["workflow_path"]).write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._upsert_workflow_registry(record)
        return self.workflow_record(workflow_id)

    def delete_workflow(self, workflow_id: str) -> None:
        record = self.workflow_record(workflow_id)
        path = Path(record["workflow_path"])
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RhCliError("WORKFLOW_DELETE_FAILED", f"删除工作流失败：{path}") from exc
        records = [item for item in self._read_workflow_registry() if str(item.get("id") or "") != str(workflow_id)]
        self._write_workflow_registry(records)

    @staticmethod
    def task_snapshot_path(task: dict[str, Any]) -> Path:
        saved = str(task.get("workflow_snapshot_path") or "").strip()
        if saved:
            return Path(saved).expanduser().resolve()
        return (Path(str(task.get("output_dir") or "")).expanduser() / str(task.get("id") or "") / "workflow_api.json").resolve()

    @staticmethod
    def task_output_path(task: dict[str, Any]) -> Path:
        return (Path(str(task.get("output_dir") or "")).expanduser() / str(task.get("id") or "")).resolve()

    @classmethod
    def existing_task_outputs(cls, task: dict[str, Any]) -> list[dict[str, Any]]:
        """Rebuild output records from a task folder after an interrupted process."""
        folder = cls.task_output_path(task)
        if not folder.is_dir():
            return []
        output_root = folder.resolve()
        saved: list[dict[str, Any]] = []
        known_paths: set[Path] = set()
        for item in task.get("outputs") or []:
            if not isinstance(item, dict):
                continue
            if item.get("kind") != "file":
                if item.get("kind") == "text":
                    saved.append(item)
                continue
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            path = Path(raw_path).expanduser().resolve()
            if output_root not in path.parents or not path.is_file():
                continue
            known_paths.add(path)
            saved.append(item)
        try:
            candidates = sorted(folder.iterdir(), key=lambda path: path.name.lower())
        except OSError:
            candidates = []
        for path in candidates:
            if not path.is_file() or path.name == "workflow_api.json" or path.name.endswith(".json.tmp") or path.name.startswith("."):
                continue
            resolved = path.resolve()
            if resolved in known_paths:
                continue
            file_type = path.suffix.lstrip(".").lower() or "file"
            saved.append(
                {
                    "kind": "file",
                    "path": str(resolved),
                    "name": path.name,
                    "file_type": file_type,
                    "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    "node_id": "",
                }
            )
        return saved

    def save_task_workflow_snapshot(self, task: dict[str, Any], workflow: dict[str, Any]) -> Path:
        snapshot_path = self.task_snapshot_path(task)
        if not snapshot_path.parent.name or not snapshot_path.parent.parent:
            raise RhCliError("INVALID_OUTPUT_DIR", "任务输出目录无效，无法保存工作流快照。")
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = snapshot_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(snapshot_path)
        return snapshot_path

    def task_workflow_path(self, task: dict[str, Any]) -> Path:
        snapshot_path = self.task_snapshot_path(task)
        if snapshot_path.is_file():
            return snapshot_path
        original_path = Path(str(task.get("workflow_path") or "")).expanduser().resolve()
        if original_path.is_file():
            return original_path
        raise RhCliError(
            "WORKFLOW_NOT_FOUND",
            "任务对应的工作流快照和原始工作流都不存在，无法加载。",
            detail={"snapshot_path": str(snapshot_path), "workflow_path": str(original_path)},
        )

    def load_task_workflow(self, task_id: str) -> dict[str, Any]:
        task = self.task(task_id)
        if not task:
            raise RhCliError("TASK_NOT_FOUND", "找不到这个任务。")
        workflow_path = self.task_workflow_path(task)
        try:
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RhCliError("INVALID_WORKFLOW", f"无法读取任务对应的工作流：{workflow_path}") from exc
        if not isinstance(workflow, dict):
            raise RhCliError("INVALID_WORKFLOW", "任务对应的工作流不是 API 格式节点字典。")
        original_path = Path(str(task.get("workflow_path") or "")).expanduser().resolve()
        snapshot_path = self.task_snapshot_path(task)
        if workflow_path != snapshot_path:
            snapshot_path = self.save_task_workflow_snapshot(task, workflow)
            self.update_task(task_id, workflow_snapshot_path=str(snapshot_path))
            task = self.task(task_id) or task
            workflow_path = snapshot_path
        name_parts = original_path.name.split("_", 2)
        saved_workflow_id = "_".join(name_parts[:2]) if len(name_parts) >= 2 else workflow_path.stem
        return {
            "workflow_id": saved_workflow_id,
            "filename": task.get("workflow_name") or workflow_path.name,
            "workflow_path": str(workflow_path),
            "workflow": workflow,
            "analysis": configured_workflow_analysis(workflow, task.get("input_config")),
            "input_config": task.get("input_config"),
            "task": task,
        }

    def create_task(self, task: dict[str, Any]) -> None:
        fields = {
            "id": task["id"],
            "created_at": task["created_at"],
            "updated_at": task["created_at"],
            "status": "queued",
            "progress": "已加入本地等待队列，等待并发槽位…",
            "workflow_path": task["workflow_path"],
            "workflow_name": task["workflow_name"],
            "key_id": task.get("key_id"),
            "account_id": str(task.get("account_id") or "").strip(),
            "instance_type": normalize_instance_type(task.get("instance_type")),
            "dispatch_key_name": str(task.get("dispatch_key_name") or "").strip(),
            "dispatch_key_site": str(task.get("dispatch_key_site") or "").strip(),
            "dispatch_key_api_type": str(task.get("dispatch_key_api_type") or "").strip(),
            "remote_task_id": None,
            "remote_workflow_id": str(task.get("remote_workflow_id") or "").strip(),
            "input_json": json.dumps(task["files"], ensure_ascii=False),
            "prompt_json": json.dumps(task["prompts"], ensure_ascii=False),
            "custom_json": json.dumps(task.get("custom_inputs") or {}, ensure_ascii=False),
            "input_config_json": json.dumps(task.get("input_config") or {}, ensure_ascii=False),
            "bypass_json": json.dumps(task.get("bypassed_nodes") or [], ensure_ascii=False),
            "random_noise_json": json.dumps(task.get("random_noise") or {}, ensure_ascii=False),
            "resolution_json": json.dumps(task.get("resolution") or {}, ensure_ascii=False),
            "workflow_snapshot_path": str(task.get("workflow_snapshot_path") or ""),
            "output_dir": task["output_dir"],
            "outputs_json": "[]",
            "error": "",
            "error_detail": "{}",
            "stage_logs_json": json.dumps(
                [
                    {
                        "at": task["created_at"],
                        "stage": "queue",
                        "message": "任务已加入本地队列",
                        "level": "info",
                    }
                ],
                ensure_ascii=False,
            ),
            "cost_type": None,
            "cost": None,
            "duration": None,
        }
        with self._lock:
            self._db.execute(
                "INSERT INTO tasks (id,created_at,updated_at,status,progress,workflow_path,workflow_name,key_id,account_id,instance_type,dispatch_key_name,dispatch_key_site,dispatch_key_api_type,remote_task_id,remote_workflow_id,"
                "input_json,prompt_json,custom_json,input_config_json,bypass_json,random_noise_json,resolution_json,workflow_snapshot_path,output_dir,outputs_json,error,error_detail,stage_logs_json,cost_type,cost,duration) "
                "VALUES (:id,:created_at,:updated_at,:status,:progress,:workflow_path,:workflow_name,:key_id,:account_id,:instance_type,:dispatch_key_name,:dispatch_key_site,:dispatch_key_api_type,:remote_task_id,:remote_workflow_id,"
                ":input_json,:prompt_json,:custom_json,:input_config_json,:bypass_json,:random_noise_json,:resolution_json,:workflow_snapshot_path,:output_dir,:outputs_json,:error,:error_detail,:stage_logs_json,:cost_type,:cost,:duration)",
                fields,
            )
            self._db.commit()

    def update_task(self, task_id: str, **changes: Any) -> None:
        allowed = {
            "status", "progress", "updated_at", "started_at", "completed_at", "key_id", "account_id", "dispatch_key_name", "dispatch_key_site", "dispatch_key_api_type", "remote_task_id", "remote_workflow_id",
            "outputs_json", "error", "error_detail", "stage_logs_json", "cost_type", "cost", "duration", "output_dir", "workflow_snapshot_path",
        }
        changes = {key: value for key, value in changes.items() if key in allowed}
        if not changes:
            return
        changes["updated_at"] = now_ms()
        assignments = ", ".join(f"{key}=:{key}" for key in changes)
        changes["task_id"] = task_id
        with self._lock:
            self._db.execute(f"UPDATE tasks SET {assignments} WHERE id=:task_id", changes)
            self._db.commit()

    def append_stage_log(
        self,
        task_id: str,
        stage: str,
        message: str,
        *,
        level: str = "info",
        detail: Any | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "at": now_ms(),
            "stage": str(stage),
            "message": str(message),
            "level": str(level),
        }
        if detail is not None:
            entry["detail"] = redact_detail(detail)
        with self._lock:
            row = self._db.execute("SELECT stage_logs_json FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return
            try:
                logs = json.loads(row[0] or "[]")
            except (TypeError, ValueError):
                logs = []
            if not isinstance(logs, list):
                logs = []
            logs.append(entry)
            logs = logs[-200:]
            self._db.execute(
                "UPDATE tasks SET stage_logs_json=?, updated_at=? WHERE id=?",
                (json.dumps(logs, ensure_ascii=False, default=str), now_ms(), task_id),
            )
            self._db.commit()

    def set_error_detail(self, task_id: str, detail: Any) -> None:
        self.update_task(task_id, error_detail=detail_json(detail))

    @staticmethod
    def row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        task["workflow_name"] = canonical_workflow_name(task.get("workflow_name") or "workflow.json")
        names = {
            "input_json": "files",
            "prompt_json": "prompts",
            "custom_json": "custom_inputs",
            "input_config_json": "input_config",
            "bypass_json": "bypassed_nodes",
            "random_noise_json": "random_noise",
            "resolution_json": "resolution",
            "outputs_json": "outputs",
            "stage_logs_json": "stage_logs",
            "error_detail": "error_detail",
        }
        for field, public_name in names.items():
            try:
                task[public_name] = json.loads(task.pop(field))
            except (ValueError, TypeError):
                task[public_name] = {} if public_name in {"error_detail", "custom_inputs", "input_config"} else []
        return task

    def task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self.row_to_task(row) if row else None

    def tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [self.row_to_task(row) for row in rows]

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            self._db.commit()


class TaskManager:
    def __init__(self, store: LocalStore) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._active_by_key: dict[str, int] = {}
        self._claimed: set[str] = set()
        self._cancel_events: dict[str, threading.Event] = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=100, thread_name_prefix="rh-web")
        self._recover_tasks_on_startup()
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="rh-web-dispatcher", daemon=True)
        self._dispatcher.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._dispatcher.join(timeout=1.5)
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _recover_tasks_on_startup(self) -> None:
        """Resolve files left by a previous process before resuming remote polling."""
        for task in self.store.tasks():
            if task.get("status") != "interrupted":
                continue
            existing = self.store.existing_task_outputs(task)
            if existing:
                self.store.update_task(
                    task["id"],
                    status="completed",
                    progress=f"已从本地产物恢复 · {len([item for item in existing if item.get('kind') == 'file'])} 个文件",
                    completed_at=now_ms(),
                    outputs_json=json.dumps(existing, ensure_ascii=False),
                    error="",
                    error_detail="{}",
                )
                self._log_stage(task["id"], "recovery", f"重启后发现本地产物，已恢复为完成（{len(existing)} 个）")
                continue
            remote_task_id = str(task.get("remote_task_id") or "").strip()
            if remote_task_id:
                self.store.update_task(
                    task["id"],
                    status="recovering",
                    progress="未发现本地产物，准备恢复远程轮询…",
                )
                self._log_stage(task["id"], "recovery", f"未发现本地产物，准备恢复轮询 taskId：{remote_task_id}")
            else:
                self.store.update_task(
                    task["id"],
                    progress="应用重启，未发现本地产物且没有远程 taskId，无法恢复",
                )
                self._log_stage(task["id"], "recovery", "未发现本地产物，也没有远程 taskId，保留为已中断", level="warning")

    def _log_stage(
        self,
        task_id: str,
        stage: str,
        message: str,
        *,
        level: str = "info",
        detail: Any | None = None,
    ) -> None:
        self.store.append_stage_log(task_id, stage, message, level=level, detail=detail)

    def public_tasks(self) -> list[dict[str, Any]]:
        result = []
        keys = {item["id"]: item for item in self.store.keys()}
        for task in self.store.tasks():
            key = keys.get(task.get("key_id"))
            snapshot_name = str(task.get("dispatch_key_name") or "").strip()
            snapshot_site = str(task.get("dispatch_key_site") or "").strip()
            snapshot_api_type = str(task.get("dispatch_key_api_type") or "").strip()
            task["key_name"] = snapshot_name or (key.get("name") if key else ("自动调度" if not task.get("key_id") else "已删除 Key"))
            task["key_site"] = snapshot_site or (key.get("site") if key else "")
            task["key_api_type"] = snapshot_api_type or (key.get("api_type") if key else "")
            task["dispatch_credential_recorded"] = bool(snapshot_name)
            task["remote_task_id"] = task.get("remote_task_id") or ""
            task["remote_workflow_id"] = task.get("remote_workflow_id") or ""
            stored_instance_type = str(task.get("instance_type") or "default").strip().lower()
            task["instance_type"] = stored_instance_type if stored_instance_type in INSTANCE_TYPES else "default"
            task["elapsed_ms"] = task_elapsed_ms(task)
            result.append(task)
        queued = sorted((item for item in result if item.get("status") == "queued"), key=lambda item: item.get("created_at", 0))
        positions = {item["id"]: index for index, item in enumerate(queued, start=1)}
        for task in result:
            task["queue_position"] = positions.get(task["id"], 0)
        return result

    def public_keys(self, account_id: str = "") -> list[dict[str, Any]]:
        with self._lock:
            active = dict(self._active_by_key)
        records = self.store.keys()
        account_id = str(account_id or "").strip()
        if account_id == GENERAL_ACCOUNT_ID:
            return [public_key({**record, "active_tasks": active.get(record["id"], 0)}) for record in records]
        elif account_id:
            records = [item for item in records if str(item.get("account_id") or "").strip() == account_id]
        return [public_key({**record, "active_tasks": active.get(record["id"], 0)}) for record in records]

    def add_key(self, name: str, site: str, api_key: str) -> dict[str, Any]:
        api_key = str(api_key or "").strip()
        account_id = self.store.current_account_id()
        account = self.store.get_account(account_id) if account_id else None
        site = account["site"] if account else ("cn" if site == "cn" else "ai")
        if not api_key:
            raise RhCliError("INVALID_API_KEY", "API Key 不能为空。")
        records = self.store.keys()
        if any(item.get("api_key") == api_key for item in records):
            raise RhCliError("DUPLICATE_API_KEY", "这个 API Key 已经保存。")
        record = {
            "id": f"key_{uuid.uuid4().hex[:12]}",
            "name": str(name or "").strip() or f"{site.upper()} Key {len(records) + 1}",
            "account_id": account_id,
            "site": site,
            "api_key": api_key,
            "status": "unchecked",
            "status_message": "正在检测…",
            "api_type": "",
            "capacity": self.store.personal_capacity(),
            "active_tasks": 0,
            "balance": "",
            "coins": "",
            "symbol": "¥" if site == "cn" else "$",
            "balance_checked_at": 0,
            "checked_at": 0,
        }
        records.append(record)
        self.store.save_keys(records)
        return self.check_key(record["id"])

    def _fetch_account_data(self, record: dict[str, Any]) -> dict[str, Any]:
        site = record["site"]
        url = ACCOUNT_STATUS_URL_CN if site == "cn" else ACCOUNT_STATUS_URL_AI
        with RhHttpClient(
            record["api_key"],
            timeout=15.0,
            no_proxy_host="runninghub.ai" if site == "ai" else "",
        ) as client:
            response = client.post_json(url, {"apikey": record["api_key"]}, timeout=15.0)
        if response.get("code") != 0:
            raise RhCliError("AUTH_FAILED", str(response.get("msg") or response.get("message") or "API Key 无效"))
        data = response.get("data") or {}
        return data if isinstance(data, dict) else {}

    def _update_balance(self, record: dict[str, Any], data: dict[str, Any]) -> None:
        site = record["site"]
        record.update(
            {
                "balance": str(data.get("remainMoney") if data.get("remainMoney") is not None else "0"),
                "coins": str(data.get("remainCoins") if data.get("remainCoins") is not None else "0"),
                "symbol": "¥" if site == "cn" else "$",
                "balance_checked_at": now_ms(),
            }
        )

    def check_key(self, key_id: str) -> dict[str, Any]:
        records = self.store.keys()
        record = next((item for item in records if item["id"] == key_id), None)
        if not record:
            raise RhCliError("KEY_NOT_FOUND", "找不到这个 API Key。")
        try:
            data = self._fetch_account_data(record)
            api_type = str(data.get("apiType") or "")
            self._update_balance(record, data)
            record.update(
                {
                    "status": "ready" if self._has_balance(data) else "no_balance",
                    "status_message": "检测成功" if self._has_balance(data) else "Key 有效但余额为 0",
                    "api_type": api_type,
                    "capacity": key_capacity(api_type, self.store.personal_capacity()),
                    "checked_at": now_ms(),
                }
            )
        except Exception as exc:
            record.update({"status": "error", "status_message": str(exc), "checked_at": now_ms()})
        self.store.save_keys(records)
        self._wake.set()
        return public_key(record)

    def refresh_balance(self, key_id: str) -> dict[str, Any]:
        records = self.store.keys()
        record = next((item for item in records if item["id"] == key_id), None)
        if not record:
            raise RhCliError("KEY_NOT_FOUND", "找不到这个 API Key。")
        data = self._fetch_account_data(record)
        self._update_balance(record, data)
        self.store.save_keys(records)
        self._wake.set()
        return public_key(record)

    @staticmethod
    def _has_balance(data: dict[str, Any]) -> bool:
        for field in ("remainMoney", "remainCoins"):
            try:
                if float(data.get(field) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def remove_key(self, key_id: str) -> None:
        records = self.store.keys()
        if any(item["id"] == key_id and self._active_by_key.get(key_id, 0) for item in records):
            raise RhCliError("KEY_IN_USE", "这个 Key 正在执行任务，暂时不能删除。")
        remaining = [item for item in records if item["id"] != key_id]
        if len(remaining) == len(records):
            raise RhCliError("KEY_NOT_FOUND", "找不到这个 API Key。")
        self.store.save_keys(remaining)

    def submit_task(
        self,
        workflow_id: str,
        files: dict[str, str],
        prompts: dict[str, str],
        key_id: str | None,
        output_dir: str | None,
        remote_workflow_id: str | None = None,
        random_noise: dict[str, Any] | None = None,
        resolution: dict[str, Any] | None = None,
        bypassed_nodes: list[str] | dict[str, Any] | None = None,
        workflow_data: dict[str, Any] | None = None,
        workflow_name: str | None = None,
        instance_type: str = "default",
        workflow_account_id: str | None = None,
        workflow_input_config: dict[str, Any] | None = None,
        custom_inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if workflow_data is not None:
            if not isinstance(workflow_data, dict):
                raise RhCliError("INVALID_WORKFLOW", "当前工作流必须是 API 格式节点字典。")
            workflow = workflow_data
        else:
            workflow_path = self.store.workflow_path(workflow_id)
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        instance_type = normalize_instance_type(instance_type)
        current_account_id = self.store.current_account_id()
        selected_key = self.store.get_key(key_id) if key_id else None
        if key_id and not selected_key:
            raise RhCliError("KEY_NOT_FOUND", "指定的 API Key 不存在。")
        selected_key_account_id = str(selected_key.get("account_id") or "").strip() if selected_key else ""
        account_restricted = current_account_id not in {"", GENERAL_ACCOUNT_ID}
        if key_id and account_restricted and selected_key_account_id != current_account_id:
            raise RhCliError("KEY_ACCOUNT_MISMATCH", "所选 API Key 不属于当前使用账号，请切换账号或重新选择 Key。")
        bound_workflow_account_id = str(workflow_account_id or "").strip()
        if not bound_workflow_account_id:
            bound_workflow_account_id = self.store.workflow_account_id(workflow_id)
        if not bound_workflow_account_id:
            metadata = workflow.get(WORKFLOW_META_KEY)
            if isinstance(metadata, dict):
                bound_workflow_account_id = str(metadata.get("accountId") or metadata.get("account_id") or "").strip()
        if bound_workflow_account_id and account_restricted and bound_workflow_account_id != current_account_id:
            raise RhCliError("WORKFLOW_ACCOUNT_MISMATCH", "当前工作流属于其他账号，请切换到对应账号后再提交。")
        library_record = None
        try:
            library_record = self.store.workflow_record(workflow_id)
        except RhCliError:
            library_record = None
        saved_input_config = workflow_input_config
        if saved_input_config is None and library_record:
            saved_input_config = library_record.get("input_config")
        # A raw task-page import has no library record, so it always stays in automatic mode.
        normalized_input_config = normalize_workflow_input_config(workflow, saved_input_config) if library_record else None
        analysis = configured_workflow_analysis(workflow, normalized_input_config)
        normalized_custom_inputs = normalize_custom_input_values(workflow, analysis, custom_inputs)
        remote_id = str(remote_workflow_id or "").strip() or analysis.get("remote_workflow_id", "")
        if not remote_id:
            raise RhCliError("MISSING_WORKFLOW_ID", "请填写 RunningHub workflowId 后再提交。")
        normalized_bypassed_nodes = normalize_bypassed_nodes(workflow, bypassed_nodes)
        bypassed_set = set(normalized_bypassed_nodes)
        normalized_random_noise = normalize_random_noise_inputs(workflow, random_noise)
        active_random_noise = {
            node_id: config
            for node_id, config in normalized_random_noise.items()
            if node_id not in bypassed_set
        }
        normalized_resolution = normalize_resolution_inputs(workflow, resolution)
        active_resolution = {
            node_id: config
            for node_id, config in normalized_resolution.items()
            if node_id not in bypassed_set
        }
        if workflow_data is not None:
            source_name = str(workflow_name or "workflow_api.json").strip() or "workflow_api.json"
            workflow_id, workflow_path, _ = self.store.save_workflow(
                source_name,
                json.dumps(workflow_data, ensure_ascii=False),
                register=False,
            )
        required = {
            item["id"]
            for item in analysis["file_inputs"]
            if item["node_id"] not in bypassed_set and item.get("required", True)
        }
        missing = sorted(item for item in required if not str(files.get(item, "")).strip())
        if missing:
            raise RhCliError("MISSING_INPUT", "请为所有检测到的文件输入选择本地文件。", detail={"inputs": missing})
        for item_id in required:
            path = Path(str(files[item_id])).expanduser()
            if not path.exists() or not path.is_file():
                raise RhCliError("FILE_NOT_FOUND", f"本地输入文件不存在：{path}")
        if key_id and not self.store.get_key(key_id):
            raise RhCliError("KEY_NOT_FOUND", "指定的 API Key 不存在。")
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        root = Path(output_dir or self.store.output_dir()).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        task = {
            "id": task_id,
            "created_at": now_ms(),
            "workflow_path": str(workflow_path),
            "workflow_name": workflow_name_from_path(workflow_path, workflow_id),
            "remote_workflow_id": remote_id,
            "files": files,
            "prompts": prompts,
            "custom_inputs": normalized_custom_inputs,
            "input_config": normalized_input_config or {"mode": "auto", "items": []},
            "bypassed_nodes": normalized_bypassed_nodes,
            "random_noise": normalized_random_noise,
            "resolution": normalized_resolution,
            "key_id": key_id or None,
            "account_id": current_account_id or selected_key_account_id or bound_workflow_account_id,
            "instance_type": instance_type,
            "output_dir": str(root),
        }
        snapshot_workflow = json.loads(json.dumps(workflow, ensure_ascii=False))
        for values in (files, prompts):
            for input_id, value in values.items():
                if str(input_id).split(":", 1)[0] in bypassed_set:
                    continue
                separator = str(input_id).find(":")
                if separator <= 0:
                    continue
                node = snapshot_workflow.get(str(input_id)[:separator])
                if not isinstance(node, dict):
                    continue
                inputs = node.setdefault("inputs", {})
                if isinstance(inputs, dict):
                    inputs[str(input_id)[separator + 1 :]] = value
        apply_custom_input_values(snapshot_workflow, normalized_custom_inputs)
        apply_random_noise_inputs(snapshot_workflow, active_random_noise)
        apply_resolution_inputs(snapshot_workflow, active_resolution)
        if task.get("account_id") and task.get("account_id") != GENERAL_ACCOUNT_ID:
            metadata = snapshot_workflow.get(WORKFLOW_META_KEY)
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            metadata["accountId"] = str(task["account_id"])
            snapshot_workflow[WORKFLOW_META_KEY] = metadata
        snapshot_path = self.store.save_task_workflow_snapshot(task, snapshot_workflow)
        task["workflow_snapshot_path"] = str(snapshot_path)
        self.store.create_task(task)
        self._wake.set()
        return self.store.task(task_id) or task

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.task(task_id)
        if not task:
            raise RhCliError("TASK_NOT_FOUND", "找不到这个任务。")
        if task["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        with self._lock:
            event = self._cancel_events.get(task_id)
            if event:
                event.set()
            else:
                self.store.update_task(task_id, status="cancelled", progress="已取消")
        return self.store.task(task_id) or task

    def delete_task(self, task_id: str) -> None:
        task = self.store.task(task_id)
        if not task:
            raise RhCliError("TASK_NOT_FOUND", "找不到这个任务。")
        if task["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
            raise RhCliError("TASK_IN_USE", "运行中的任务不能删除，请先取消。")
        self.store.delete_task(task_id)

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            self._dispatch_once()
            self._wake.wait(0.35)
            self._wake.clear()

    def _dispatch_once(self) -> None:
        keys = self.store.keys()
        records = {item["id"]: item for item in keys}
        for task in self.store.tasks():
            if task["status"] not in {"queued", "recovering"} or task["id"] in self._claimed:
                continue
            recovery = task["status"] == "recovering"
            scoped_keys = self._keys_for_task(task, keys)
            record = self._select_key(task, scoped_keys, records)
            if not record:
                wait_message = self._queue_wait_message(task, scoped_keys, records)
                if task.get("progress") != wait_message:
                    self.store.update_task(task["id"], progress=wait_message)
                continue
            with self._lock:
                if task["id"] in self._claimed:
                    continue
                self._claimed.add(task["id"])
                self._active_by_key[record["id"]] = self._active_by_key.get(record["id"], 0) + 1
                event = threading.Event()
                self._cancel_events[task["id"]] = event
            self.store.update_task(
                task["id"],
                status="submitting",
                key_id=record["id"],
                dispatch_key_name=str(record.get("name") or ""),
                dispatch_key_site=str(record.get("site") or ""),
                dispatch_key_api_type=str(record.get("api_type") or ""),
                started_at=now_ms(),
                progress=f"使用 {record['name']} {'恢复轮询' if recovery else '提交'}中…",
            )
            self._log_stage(task["id"], "dispatch", f"已选择 {record['name']}，开始{'恢复轮询' if recovery else '执行'}")
            self._executor.submit(self._run_task, task["id"], record, event, recovery)

    @staticmethod
    def _keys_for_task(task: dict[str, Any], keys: list[dict[str, Any]]) -> list[dict[str, Any]]:
        account_id = str(task.get("account_id") or "").strip()
        if account_id == GENERAL_ACCOUNT_ID:
            return keys
        if not account_id:
            return keys
        return [item for item in keys if str(item.get("account_id") or "").strip() == account_id]

    def _queue_wait_message(
        self,
        task: dict[str, Any],
        keys: list[dict[str, Any]],
        records: dict[str, dict[str, Any]],
    ) -> str:
        if task.get("key_id"):
            record = records.get(task["key_id"])
            if record and record.get("status") == "ready":
                active = self._active_by_key.get(record["id"], 0)
                capacity = int(record.get("capacity") or 3)
                return f"本地等待队列 · {record['name']} 并发已满（{active}/{capacity}）"
            return "本地等待队列 · 等待指定 Key 可用"
        ready = [item for item in keys if item.get("status") == "ready"]
        if ready:
            capacities = ", ".join(
                f"{item['name']} {self._active_by_key.get(item['id'], 0)}/{int(item.get('capacity') or 3)}"
                for item in ready
            )
            return f"本地等待队列 · 等待并发槽位（{capacities}）"
        return "本地等待队列 · 等待可用 Key"

    def _select_key(
        self,
        task: dict[str, Any],
        keys: list[dict[str, Any]],
        records: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        with self._lock:
            if task.get("key_id"):
                candidate = records.get(task["key_id"])
                if not candidate or candidate.get("status") != "ready":
                    return None
                account_id = str(task.get("account_id") or "").strip()
                if account_id and str(candidate.get("account_id") or "").strip() != account_id:
                    return None
                if self._active_by_key.get(candidate["id"], 0) >= int(candidate.get("capacity") or 3):
                    return None
                return candidate
            candidates = [
                item
                for item in keys
                if item.get("status") == "ready"
                and self._active_by_key.get(item["id"], 0) < int(item.get("capacity") or 3)
            ]
            if not candidates:
                return None
            return min(candidates, key=lambda item: (self._active_by_key.get(item["id"], 0), item.get("created_at", 0)))

    def _recover_task(
        self,
        task: dict[str, Any],
        key: dict[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        remote_id = str(task.get("remote_task_id") or "").strip()
        if not remote_id:
            raise RhCliError("TASK_RECOVERY_UNAVAILABLE", "任务没有远程 taskId，无法恢复轮询。")
        task_id = task["id"]
        task_output_dir = Path(task["output_dir"]) / task_id
        task_output_dir.mkdir(parents=True, exist_ok=True)
        _, _, site_outputs = _site_urls(key["site"])
        self._log_stage(task_id, "recovery", f"开始恢复远程轮询：{remote_id}")
        with RhHttpClient(key["api_key"], no_proxy_host="runninghub.ai" if key["site"] == "ai" else "") as client:
            last_state = {"value": ""}

            def on_tick(elapsed: int, state: str) -> None:
                labels = {"RUNNING": "恢复轮询：RunningHub 执行中…", "QUEUED": "恢复轮询：等待 RunningHub 队列…", "WAITING_OUTPUT": "恢复轮询：等待产物返回…"}
                self.store.update_task(task_id, status="running", progress=f"{labels.get(state, state)} {elapsed}s")
                if state != last_state["value"]:
                    last_state["value"] = state
                    self._log_stage(task_id, "poll", f"恢复后的远程状态：{state}（{elapsed}s）", detail={"state": state, "elapsed": elapsed})

            outputs = _poll_outputs(
                client,
                key["api_key"],
                remote_id,
                max_seconds=1200,
                interval=5,
                on_tick=on_tick,
                outputs_url=site_outputs,
                cancel_event=cancel_event,
                cancel_url=_site_cancel_url(key["site"]),
            )
            self._log_stage(task_id, "download", f"恢复后开始保存 {len(outputs)} 个远程产物")
            saved = self._download_outputs(client, outputs, task_output_dir)
            self._log_stage(task_id, "download", f"恢复后产物保存完成：{len(saved)} 个")
        cost_type, cost, duration = self._task_cost(outputs)
        self.store.update_task(
            task_id,
            status="completed",
            progress=f"已完成 · {len(saved)} 个产物（重启后恢复）",
            completed_at=now_ms(),
            outputs_json=json.dumps(saved, ensure_ascii=False),
            cost_type=cost_type,
            cost=cost,
            duration=str(duration) if duration is not None else None,
        )
        self._log_stage(task_id, "complete", f"重启后恢复完成，共保存 {len(saved)} 个产物")

    def _run_task(
        self,
        task_id: str,
        key: dict[str, Any],
        cancel_event: threading.Event,
        recovery: bool = False,
    ) -> None:
        try:
            task = self.store.task(task_id)
            if not task:
                return
            if cancel_event.is_set():
                raise RhCliError("TASK_CANCELLED", "任务已取消。")
            if recovery:
                self._recover_task(task, key, cancel_event)
                return
            bypassed_values = task.get("bypassed_nodes") or task.get("bypassed_inputs") or []
            task_output_dir = Path(task["output_dir"]) / task_id
            task_output_dir.mkdir(parents=True, exist_ok=True)
            site_upload, site_create, site_outputs = _site_urls(key["site"])

            with RhHttpClient(key["api_key"], no_proxy_host="runninghub.ai" if key["site"] == "ai" else "") as client:
                self._log_stage(task_id, "prepare", "已读取工作流，准备应用输入配置")
                workflow_path = self.store.task_workflow_path(task)
                workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
                workflow = workflow_nodes(workflow)
                bypassed_nodes = set(normalize_bypassed_nodes(workflow, bypassed_values))
                file_args = [
                    f"{item_id}={path}"
                    for item_id, path in task["files"].items()
                    if str(item_id).split(":", 1)[0] not in bypassed_nodes
                ]
                set_args = [
                    f"{item_id}={value}"
                    for item_id, value in task["prompts"].items()
                    if str(item_id).split(":", 1)[0] not in bypassed_nodes
                ]
                removed_nodes = apply_bypassed_nodes(workflow, bypassed_nodes)
                if removed_nodes:
                    self._log_stage(task_id, "prepare", f"已旁路 {len(removed_nodes)} 个节点")
                remote_id_value = str(task.get("remote_workflow_id") or "").strip()
                if not remote_id_value:
                    raise RhCliError("MISSING_WORKFLOW_ID", "任务缺少 RunningHub workflowId，请重新提交。")
                if set_args:
                    prompt_changes = _apply_overrides(workflow, set_args)
                    self._log_stage(task_id, "prepare", f"已应用 {len(prompt_changes)} 个文本配置")
                random_noise_values = {
                    node_id: config
                    for node_id, config in (task.get("random_noise") or {}).items()
                    if node_id not in bypassed_nodes
                }
                random_noise_changes = apply_random_noise_inputs(workflow, random_noise_values)
                if random_noise_changes:
                    self._log_stage(task_id, "prepare", f"已应用 {len(random_noise_changes) // 2} 个 RandomNoise 配置")
                resolution_values = {
                    node_id: config
                    for node_id, config in (task.get("resolution") or {}).items()
                    if node_id not in bypassed_nodes
                }
                resolution_changes = apply_resolution_inputs(workflow, resolution_values)
                if resolution_changes:
                    self._log_stage(task_id, "prepare", f"已应用 {len(resolution_changes) // 2} 个尺寸节点配置")
                if bypassed_nodes and not removed_nodes:
                    self._log_stage(task_id, "prepare", f"已请求旁路 {len(bypassed_nodes)} 个节点")
                self._log_stage(task_id, "upload", f"开始上传 {len(file_args)} 个输入文件")
                changes = _apply_file_args(client, workflow, file_args, f"{get_site_config(key['site'])['api_host']}/task/openapi/upload")
                self._log_stage(task_id, "upload", f"输入文件上传完成：{len(changes)} 个")
                self.store.update_task(task_id, progress=f"已上传 {len(changes)} 个输入，正在提交…")
                self._log_stage(task_id, "submit", f"正在提交完整 API 工作流（workflowId：{remote_id_value}）")
                remote_id = _submit(
                    client,
                    key["api_key"],
                    remote_id_value,
                    json.dumps(workflow, ensure_ascii=False),
                    instance_type=str(task.get("instance_type") or "default"),
                    create_url=site_create,
                    add_metadata=True,
                )
                self.store.update_task(task_id, remote_task_id=remote_id, status="running", progress=f"已提交 · {remote_id}")
                self._log_stage(task_id, "submit", f"RunningHub 已返回 taskId：{remote_id}")
                self._log_stage(task_id, "poll", "开始轮询任务状态")

                last_state = {"value": ""}

                def on_tick(elapsed: int, state: str) -> None:
                    labels = {"RUNNING": "RunningHub 执行中…", "QUEUED": "等待 RunningHub 队列…", "WAITING_OUTPUT": "等待产物返回…"}
                    self.store.update_task(task_id, status="running", progress=f"{labels.get(state, state)} {elapsed}s")
                    if state != last_state["value"]:
                        last_state["value"] = state
                        self._log_stage(task_id, "poll", f"远程状态：{state}（{elapsed}s）", detail={"state": state, "elapsed": elapsed})

                outputs = _poll_outputs(
                    client,
                    key["api_key"],
                    remote_id,
                    max_seconds=1200,
                    interval=5,
                    on_tick=on_tick,
                    outputs_url=site_outputs,
                    cancel_event=cancel_event,
                    cancel_url=_site_cancel_url(key["site"]),
                )
                self._log_stage(task_id, "download", f"开始保存 {len(outputs)} 个远程产物")
                saved = self._download_outputs(client, outputs, task_output_dir)
                self._log_stage(task_id, "download", f"产物保存完成：{len(saved)} 个")

            cost_type, cost, duration = self._task_cost(outputs)
            self.store.update_task(
                task_id,
                status="completed",
                progress=f"已完成 · {len(saved)} 个文件",
                completed_at=now_ms(),
                outputs_json=json.dumps(saved, ensure_ascii=False),
                cost_type=cost_type,
                cost=cost,
                duration=str(duration) if duration is not None else None,
            )
            self._log_stage(task_id, "complete", f"任务完成，共保存 {len(saved)} 个产物")
        except RhCliError as exc:
            status = "cancelled" if exc.code == "TASK_CANCELLED" else "failed"
            error_detail = {"code": exc.code, "message": exc.message}
            if exc.detail is not None:
                error_detail["detail"] = exc.detail
            self.store.set_error_detail(task_id, error_detail)
            self.store.update_task(task_id, status=status, progress=exc.message, error=redact_detail(exc.message), completed_at=now_ms())
            self._log_stage(task_id, "cancelled" if status == "cancelled" else "failed", exc.message, level="warning" if status == "cancelled" else "error", detail=exc.detail)
        except Exception as exc:  # pragma: no cover - final safety net for background jobs
            error_detail = {"type": type(exc).__name__, "message": str(exc)}
            self.store.set_error_detail(task_id, error_detail)
            self.store.update_task(task_id, status="failed", progress="任务失败", error=redact_detail(str(exc)), completed_at=now_ms())
            self._log_stage(task_id, "failed", str(exc), level="error")
        finally:
            with self._lock:
                self._claimed.discard(task_id)
                self._cancel_events.pop(task_id, None)
                key_id = key["id"]
                self._active_by_key[key_id] = max(0, self._active_by_key.get(key_id, 1) - 1)
            self._wake.set()

    @staticmethod
    def _download_outputs(client: RhHttpClient, outputs: list[dict[str, Any]], folder: Path) -> list[dict[str, Any]]:
        saved: list[dict[str, Any]] = []
        file_index = 0
        for item in outputs:
            url = _output_file_url(item)
            if not url:
                text = _output_text(item)
                if text is not None:
                    saved.append({"kind": "text", "text": text, "node_id": str(item.get("nodeId", ""))})
                continue
            file_index += 1
            extension = _normalise_output_ext(item.get("fileType"))
            filename = f"output_{file_index}.{extension}"
            path = folder / filename
            # Keep incomplete downloads out of startup recovery. If the app is
            # closed while streaming, only the hidden .part file remains and
            # the next launch will resume the remote task instead of declaring
            # a truncated artifact complete.
            partial_path = folder / f".{filename}.part"
            client.download(str(url), str(partial_path))
            partial_path.replace(path)
            saved.append(
                {
                    "kind": "file",
                    "path": str(path),
                    "name": filename,
                    "file_type": str(item.get("fileType") or extension),
                    "mime": mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    "node_id": str(item.get("nodeId", "")),
                }
            )
        return saved

    @staticmethod
    def _task_cost(outputs: list[dict[str, Any]]) -> tuple[str | None, str | None, Any]:
        if not outputs:
            return None, None, None
        first = outputs[0]
        for field, cost_type in (("consumeCoins", "coins"), ("consumeMoney", "money")):
            if first.get(field) is not None and str(first.get(field)).strip():
                return cost_type, str(first[field]), first.get("taskCostTime")
        return None, None, first.get("taskCostTime")


def public_state(store: LocalStore, manager: TaskManager) -> dict[str, Any]:
    current_account_id = store.current_account_id()
    current_account = store.get_account(current_account_id) if current_account_id else None
    return {
        "settings": {
            "output_dir": store.output_dir(),
            "personal_capacity": store.personal_capacity(),
            "current_account_id": current_account_id,
            "current_mode": "general" if current_account_id == GENERAL_ACCOUNT_ID else "account",
            "data_dir": str(DATA_ROOT),
            "native_file_picker": native_file_picker_available(),
        },
        "current_account": public_account(current_account) if current_account else None,
        "keys": manager.public_keys(current_account_id),
        "accounts": [public_account(item) for item in store.accounts()],
        "tasks": manager.public_tasks(),
    }


def public_outputs(store: LocalStore, manager: TaskManager) -> dict[str, Any]:
    """Return locally available task artifacts for the output library page."""
    artifacts: list[dict[str, Any]] = []
    type_counts = {"image": 0, "video": 0, "audio": 0, "other": 0, "text": 0}

    for task in manager.public_tasks():
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        file_index = 0
        task_output_root = Path(str(task.get("output_dir") or "")).expanduser().resolve()
        for output in task.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            kind = str(output.get("kind") or "file")
            if kind == "text":
                text = str(output.get("text") or "")
                if not text.strip():
                    continue
                type_counts["text"] += 1
                artifacts.append(
                    {
                        "id": f"{task_id}:text:{len(artifacts)}",
                        "kind": "text",
                        "display_type": "text",
                        "name": str(output.get("name") or f"文本输出 · {output.get('node_id') or 'output'}"),
                        "text": text,
                        "node_id": str(output.get("node_id") or ""),
                        "task_id": task_id,
                        "task_name": str(task.get("workflow_name") or task_id),
                        "task_status": str(task.get("status") or ""),
                        "task_created_at": int(task.get("created_at") or 0),
                        "task_completed_at": int(task.get("completed_at") or 0),
                        "cost_type": str(task.get("cost_type") or ""),
                        "cost": str(task.get("cost") or ""),
                    }
                )
                continue

            if kind != "file":
                continue
            current_index = file_index
            file_index += 1
            raw_path = str(output.get("path") or "").strip()
            if not raw_path:
                continue
            file_path = Path(raw_path).expanduser().resolve()
            try:
                stat = file_path.stat()
            except OSError:
                continue
            if not file_path.is_file() or task_output_root not in file_path.parents:
                continue
            mime = str(output.get("mime") or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream")
            if mime.startswith("image/"):
                display_type = "image"
            elif mime.startswith("video/"):
                display_type = "video"
            elif mime.startswith("audio/"):
                display_type = "audio"
            else:
                display_type = "other"
            type_counts[display_type] += 1
            artifacts.append(
                {
                    "id": f"{task_id}:file:{current_index}",
                    "kind": "file",
                    "display_type": display_type,
                    "name": str(output.get("name") or file_path.name),
                    "mime": mime,
                    "file_type": str(output.get("file_type") or file_path.suffix.lstrip(".") or "file"),
                    "node_id": str(output.get("node_id") or ""),
                    "task_id": task_id,
                    "task_name": str(task.get("workflow_name") or task_id),
                    "task_status": str(task.get("status") or ""),
                    "task_created_at": int(task.get("created_at") or 0),
                    "task_completed_at": int(task.get("completed_at") or 0),
                    "cost_type": str(task.get("cost_type") or ""),
                    "cost": str(task.get("cost") or ""),
                    "file_index": current_index,
                    "size": stat.st_size,
                    "modified_at": int(stat.st_mtime * 1000),
                }
            )

    artifacts.sort(key=lambda item: int(item.get("modified_at") or item.get("task_created_at") or 0), reverse=True)
    return {
        "outputs": artifacts,
        "summary": {
            "total": len(artifacts),
            "tasks": len({item["task_id"] for item in artifacts}),
            **type_counts,
        },
    }
