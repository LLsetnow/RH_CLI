from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import platform
import random
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
from .telegram import TelegramDeliveryError, TelegramNotifier
from .video_downloader import extract_social_video_url, social_video_platform, download_social_video


WEB_ROOT = Path(__file__).resolve().parent
_DATA_ROOT_OVERRIDE = os.environ.get("RH_WORKFLOW_DESK_DATA_ROOT", "").strip()
DATA_ROOT = Path(_DATA_ROOT_OVERRIDE).expanduser().resolve() if _DATA_ROOT_OVERRIDE else WEB_ROOT / "data"
# A library workflow is a directory package.  Keep the singular directory
# name separate from the historical ``workflows/<id>.json`` layout so the
# store can migrate existing installations without making task snapshots
# depend on the library files.
WORKFLOW_ROOT = DATA_ROOT / "workflow"
OUTPUT_ROOT = DATA_ROOT / "outputs"
KEYS_PATH = DATA_ROOT / "keys.json"
ACCOUNTS_PATH = DATA_ROOT / "accounts.json"
DB_PATH = DATA_ROOT / "tasks.sqlite3"
DEFAULT_RESOURCE_INDEX_PATH = Path.home() / "Documents" / "VideoMake" / "ref" / "Resources.json"
WORKFLOW_REGISTRY_FORMAT_VERSION = 2

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
LOCAL_WORKER_CAPACITY = 100
DEFAULT_API_KEY_STRATEGY = "personal_then_shared"
API_KEY_STRATEGIES = {"personal_only", "personal_then_shared", "shared_only"}
DEFAULT_POSE_MEDIA_IMPORT_TYPE = "depth"
POSE_MEDIA_IMPORT_TYPES = {"depth", "skeleton"}
WORKFLOW_META_KEY = "__rh_meta__"
PROMPT_GROUP_SNAPSHOT_FILENAME = "prompt_group.json"
TASK_MANIFEST_FILENAME = "manifest.json"
WORKFLOW_PROMPT_GROUP_SUFFIX = ".prompt_group.json"
WORKFLOW_API_FILENAME = "workflow_api.json"
WORKFLOW_PACKAGE_MANIFEST_FILENAME = "manifest.json"
GENERAL_ACCOUNT_ID = "__general__"
UNBOUND_ACCOUNT_ID = "__unbound__"
TELEGRAM_PROJECT_NAME = "Telegrame"
INSTANCE_TYPES = {"default", "plus", "ultra"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
VIDEO_OUTPUT_SUFFIXES = {
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
    ".wmv",
}


def _json_source_path(value: str | Path) -> Path:
    """Resolve a resource setting to its JSON source during the migration."""
    path = Path(value).expanduser()
    if path.suffix.lower() in {".md", ".markdown"}:
        path = path.with_suffix(".json")
    return path.resolve()


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


def pick_local_directory_on_macos(prompt: str = "选择默认产物目录") -> Path | None:
    """Use the macOS native picker to select a directory without reading its contents."""
    if platform.system() != "Darwin":
        raise RhCliError("LOCAL_PICKER_UNAVAILABLE", "本机路径选择目前仅支持 macOS；请手动填写绝对路径。")
    if shutil.which("osascript") is None:
        raise RhCliError("LOCAL_PICKER_UNAVAILABLE", "本机没有可用的 macOS 文件选择器，请手动填写绝对路径。")

    safe_prompt = str(prompt or "选择文件夹").replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
try
    set pickedFolder to choose folder with prompt "{safe_prompt}"
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


def normalize_output_prefix(value: Any) -> str:
    """Return a safe optional basename prefix for downloaded task outputs."""
    raw = str(value or "").strip()
    return safe_name(raw, "") if raw else ""


def _stable_project_id(value: str) -> str:
    digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:12]
    return f"project_{digest}"


def _infer_project_path(*values: str | Path) -> Path | None:
    """Infer the owning VideoMake project without scanning or copying its files."""
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            continue
        parts = path.parts
        lowered = [part.casefold() for part in parts]
        if "projects" not in lowered:
            continue
        marker = lowered.index("projects")
        if marker + 1 >= len(parts):
            continue
        return Path(*parts[: marker + 2])
    return None


def normalize_project(
    project: Any | None = None,
    *,
    output_dir: str | Path = "",
    workflow_path: str | Path = "",
    infer_from_paths: bool = True,
) -> dict[str, str]:
    """Return stable project metadata for a task; paths are metadata only."""
    payload = project if isinstance(project, dict) else {}
    project_id = str(payload.get("id") or payload.get("project_id") or "").strip()
    project_name = str(payload.get("name") or payload.get("project_name") or "").strip()
    project_path = str(payload.get("path") or payload.get("project_path") or "").strip()

    has_explicit_metadata = bool(project_id or project_name or project_path)
    if infer_from_paths and not has_explicit_metadata:
        inferred_path = _infer_project_path(output_dir, workflow_path)
        project_path = str(inferred_path) if inferred_path else ""
    if project_path:
        try:
            project_path = str(Path(project_path).expanduser().resolve())
        except OSError:
            project_path = ""
    if project_path and not project_name:
        project_name = Path(project_path).name.strip()
    project_name = re.sub(r"[\x00-\x1f\x7f]", "", project_name).strip()[:120]
    if not project_id:
        if project_path:
            project_id = _stable_project_id(project_path)
        elif project_name:
            project_id = _stable_project_id(f"name:{project_name}")
    return {"id": project_id, "name": project_name, "path": project_path}


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


def public_workflow_name(value: object) -> str:
    """Keep legacy local-tool task labels readable after the toolbox merge."""
    name = str(value or "").strip()
    for prefix in ("工具箱 · ", "工具箱 _ ", "工具箱_", "工具箱 "):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break
    if name == "本地 Codex 图像生成":
        return "Codex 图像生成"
    return name


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


def is_shared_api_key_type(api_type: Any) -> bool:
    """Return whether RunningHub identified a Key as shared/enterprise tier."""
    normalized = str(api_type or "").strip().lower()
    return any(label in normalized for label in (
        "enterprise", "shared", "wallet", "team", "organization", "企业", "共享", "钱包",
    ))


def normalize_api_key_strategy(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in API_KEY_STRATEGIES:
        return normalized
    raise RhCliError(
        "INVALID_API_KEY_STRATEGY",
        "API Key 调度策略只能是 personal_only、personal_then_shared 或 shared_only。",
    )


def normalize_pose_media_import_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in POSE_MEDIA_IMPORT_TYPES:
        return normalized
    raise RhCliError(
        "INVALID_POSE_MEDIA_IMPORT_TYPE",
        "动作导入媒体类型只能是 depth 或 skeleton。",
    )


def key_capacity(api_type: str, personal_capacity: int = DEFAULT_PERSONAL_CAPACITY) -> int:
    """Return the local scheduler cap for the RunningHub account type."""
    return 100 if is_shared_api_key_type(api_type) else personal_capacity_value(personal_capacity)


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
            kind = automatic_item.get("kind")
            if not kind:
                kind = "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "text"
            catalog.append({
                "id": input_id, "node_id": str(node_id), "field": str(field), "title": title,
                "class_type": class_type, "label": f"{title} · {field}",
                "kind": kind,
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
            "default": display_value(
                raw.get("default") if "default" in raw else (
                    (nodes[node_id].get("inputs") or {}).get(field) if field else ""
                )
            ),
            "virtual": bool((entry or {}).get("virtual")),
            "order": position,
        })
    return {"mode": "manual", "items": items}


def prune_workflow_input_config_for_workflow(
    workflow: dict[str, Any], config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Drop manual input entries for nodes intentionally removed from a draft.

    The task page removes a bypassed node from the JSON before saving a
    library copy, while its old manual input catalog entry can still be in
    ``input_config``.  That stale entry must not make the save fail with a
    misleading missing-node error.
    """
    if config is None or not isinstance(config, dict):
        return config
    if str(config.get("mode") or "auto").strip().lower() != "manual":
        return config
    raw_items = config.get("items")
    if not isinstance(raw_items, list):
        return config
    nodes = workflow_nodes(workflow)
    kept: list[Any] = []
    for item in raw_items:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        node_id = str(item.get("node_id") or "").strip()
        if not node_id:
            input_id = str(item.get("id") or "").strip()
            node_id = input_id.split(":", 1)[0] if ":" in input_id else ""
        if node_id and node_id not in nodes:
            continue
        kept.append(item)
    if len(kept) == len(raw_items):
        return config
    return {**config, "items": kept}


def apply_workflow_input_defaults(workflow: dict[str, Any], defaults: Any) -> None:
    """Write editable scalar input defaults back into a workflow JSON object."""
    if defaults is None:
        return
    if not isinstance(defaults, list):
        raise RhCliError("INVALID_WORKFLOW_INPUT_DEFAULTS", "工作流输入默认值必须是列表。")
    nodes = workflow_nodes(workflow)
    seen: set[str] = set()
    for position, raw in enumerate(defaults):
        if not isinstance(raw, dict):
            raise RhCliError("INVALID_WORKFLOW_INPUT_DEFAULTS", f"第 {position + 1} 个默认值不是对象。")
        node_id = str(raw.get("node_id") or "").strip()
        field = str(raw.get("field") or "").strip()
        input_id = f"{node_id}:{field}"
        if not node_id or not field:
            raise RhCliError("INVALID_WORKFLOW_INPUT_DEFAULTS", f"第 {position + 1} 个默认值缺少节点或字段。")
        if input_id in seen:
            raise RhCliError("INVALID_WORKFLOW_INPUT_DEFAULTS", f"输入默认值重复：{input_id}")
        seen.add(input_id)
        node = nodes.get(node_id)
        if not isinstance(node, dict):
            raise RhCliError("INVALID_WORKFLOW_INPUT_DEFAULTS", f"找不到输入配置节点：{node_id}")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or field not in inputs:
            raise RhCliError("INVALID_WORKFLOW_INPUT_DEFAULTS", f"找不到输入字段：{input_id}")
        if is_link(inputs[field]):
            raise RhCliError("INVALID_WORKFLOW_INPUT_DEFAULTS", f"不能修改连线输入的默认值：{input_id}")
        if "default" not in raw:
            raise RhCliError("INVALID_WORKFLOW_INPUT_DEFAULTS", f"输入默认值缺少 default：{input_id}")
        value = raw["default"]
        if isinstance(value, (list, dict, tuple)):
            raise RhCliError("INVALID_WORKFLOW_INPUT_DEFAULTS", f"输入默认值必须是标量：{input_id}")
        inputs[field] = value


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


def apply_default_file_inputs(
    workflow: dict[str, Any],
    analysis: dict[str, Any],
    files: dict[str, str] | None,
    bypassed_nodes: set[str],
    workflow_path: Path,
) -> dict[str, str]:
    """Keep optional file inputs on their workflow defaults when not overridden.

    A configured optional input means "do not require a replacement from the
    current task", not "remove the input from the API workflow". Local default
    paths still need to be uploaded before submission to RunningHub.
    """
    resolved = dict(files or {})
    for item in analysis.get("file_inputs") or []:
        if not isinstance(item, dict) or bool(item.get("required", True)):
            continue
        input_id = str(item.get("id") or "").strip()
        node_id = str(item.get("node_id") or "").strip()
        field = str(item.get("field") or "").strip()
        if not input_id or not node_id or not field or node_id in bypassed_nodes:
            continue
        if str(resolved.get(input_id) or "").strip():
            continue
        node = workflow.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) else None
        default_value = inputs.get(field) if isinstance(inputs, dict) else None
        if not isinstance(default_value, str) or not default_value.strip():
            continue
        default_path = Path(default_value).expanduser()
        is_absolute_default = default_path.is_absolute()
        if not default_path.is_absolute():
            default_path = workflow_path.parent / default_path
        default_path = default_path.resolve()
        if default_path.is_file():
            resolved[input_id] = str(default_path)
        elif is_absolute_default:
            raise RhCliError("FILE_NOT_FOUND", f"工作流默认输入文件不存在：{default_path}")
    return resolved


def telegram_inbound_file_input(detail: dict[str, Any], media_type: str = "image") -> dict[str, Any]:
    """Resolve the only required image or video input used by Telegram intake."""
    media_type = str(media_type or "image").strip().lower()
    if media_type not in {"image", "video"}:
        raise RhCliError("INVALID_TELEGRAM_INBOUND_MEDIA", "Telegram 入站媒体类型无效。")
    workflow = detail.get("workflow") if isinstance(detail, dict) else None
    record = detail.get("record") if isinstance(detail, dict) else None
    if not isinstance(workflow, dict) or not isinstance(record, dict):
        raise RhCliError("INVALID_TELEGRAM_INBOUND_WORKFLOW", "Telegram 入站工作流资料不完整。")

    analysis = configured_workflow_analysis(workflow, record.get("input_config"))
    required_inputs: list[tuple[str, dict[str, Any]]] = []
    for kind, key in (
        ("file", "file_inputs"),
        ("prompt", "prompt_inputs"),
        ("custom", "custom_inputs"),
        ("resolution", "resolution_inputs"),
        ("random_noise", "random_noise_inputs"),
    ):
        required_inputs.extend(
            (kind, item)
            for item in analysis.get(key) or []
            if isinstance(item, dict) and bool(item.get("required"))
        )

    media_label = "视频" if media_type == "video" else "图片"
    if len(required_inputs) != 1 or required_inputs[0][0] != "file":
        raise RhCliError(
            "INVALID_TELEGRAM_INBOUND_WORKFLOW",
            f"Telegram {media_label}入站要求工作流恰好有一个必填输入节点，且该节点必须是{media_label}节点；其他输入请设为非必填。",
        )
    file_input = required_inputs[0][1]
    class_type = str(file_input.get("class_type") or "").lower()
    field = str(file_input.get("field") or "").lower()
    if media_type == "video":
        valid_media_input = "video" in class_type or "video" in field
    else:
        valid_media_input = "loadimage" in class_type or "image" in field
    if not valid_media_input:
        raise RhCliError(
            "INVALID_TELEGRAM_INBOUND_WORKFLOW",
            f"Telegram {media_label}入站的唯一必填输入节点必须是{media_label}节点。",
        )
    return file_input


def telegram_video_inbound_file_input(detail: dict[str, Any]) -> dict[str, Any]:
    return telegram_inbound_file_input(detail, "video")


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
              project_id TEXT NOT NULL DEFAULT '',
              project_name TEXT NOT NULL DEFAULT '',
              project_path TEXT NOT NULL DEFAULT '',
              project_inference_disabled INTEGER NOT NULL DEFAULT 0,
              key_id TEXT,
              account_id TEXT NOT NULL DEFAULT '',
              instance_type TEXT NOT NULL DEFAULT 'default',
              output_prefix TEXT NOT NULL DEFAULT '',
              dispatch_key_name TEXT NOT NULL DEFAULT '',
              dispatch_key_site TEXT NOT NULL DEFAULT '',
              dispatch_key_api_type TEXT NOT NULL DEFAULT '',
              submission_source TEXT NOT NULL DEFAULT 'local',
              task_type TEXT NOT NULL DEFAULT 'workflow',
              remote_task_id TEXT,
              remote_workflow_id TEXT NOT NULL DEFAULT '',
              registered_workflow_id TEXT NOT NULL DEFAULT '',
              local_workflow_id TEXT NOT NULL DEFAULT '',
              input_json TEXT NOT NULL,
              prompt_json TEXT NOT NULL,
              custom_json TEXT NOT NULL DEFAULT '{}',
              input_config_json TEXT NOT NULL DEFAULT '{}',
              bypass_json TEXT NOT NULL DEFAULT '[]',
              random_noise_json TEXT NOT NULL DEFAULT '{}',
              resolution_json TEXT NOT NULL DEFAULT '{}',
              workflow_snapshot_path TEXT NOT NULL DEFAULT '',
              prompt_group_snapshot_path TEXT NOT NULL DEFAULT '',
              manifest_path TEXT NOT NULL DEFAULT '',
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
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              path TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_records (
              task_id TEXT PRIMARY KEY,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              account_id TEXT NOT NULL DEFAULT '',
              site TEXT NOT NULL DEFAULT '',
              started_at INTEGER,
              completed_at INTEGER,
              status TEXT NOT NULL,
              workflow_name TEXT NOT NULL DEFAULT '',
              cost_type TEXT,
              cost TEXT,
              duration TEXT,
              elapsed_ms INTEGER,
              output_count INTEGER NOT NULL DEFAULT 0,
              video_seconds TEXT NOT NULL DEFAULT '0'
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_deliveries (
              task_id TEXT NOT NULL,
              delivery_key TEXT NOT NULL,
              sent_at INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'sent',
              claimed_by TEXT NOT NULL DEFAULT '',
              claim_until INTEGER NOT NULL DEFAULT 0,
              attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT NOT NULL DEFAULT '',
              updated_at INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (task_id, delivery_key)
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_inbound_updates (
              update_id INTEGER PRIMARY KEY,
              received_at INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT '',
              task_id TEXT NOT NULL DEFAULT '',
              detail TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS remote_queue_cooldowns (
              key_id TEXT PRIMARY KEY,
              retry_after INTEGER NOT NULL DEFAULT 0,
              attempts INTEGER NOT NULL DEFAULT 0,
              wait_for_predecessors INTEGER NOT NULL DEFAULT 0,
              probe_task_id TEXT NOT NULL DEFAULT '',
              updated_at INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._migrate_schema()
        self._db.commit()
        self._interrupt_incomplete()
        self._backfill_task_projects()
        self._backfill_project_registry()
        self._backfill_telegram_projects()
        self._backfill_task_replay_snapshots()
        self._migrate_legacy_workflow_files()
        self._backfill_registered_workflow_source_paths()
        self._backfill_usage_records()

    def _migrate_schema(self) -> None:
        """Add fields introduced by newer web builds to an existing local database."""
        columns = {str(row[1]) for row in self._db.execute("PRAGMA table_info(tasks)").fetchall()}
        if "remote_workflow_id" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN remote_workflow_id TEXT NOT NULL DEFAULT ''")
        if "registered_workflow_id" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN registered_workflow_id TEXT NOT NULL DEFAULT ''")
        if "local_workflow_id" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN local_workflow_id TEXT NOT NULL DEFAULT ''")
        if "error_detail" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN error_detail TEXT NOT NULL DEFAULT '{}'")
        if "stage_logs_json" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN stage_logs_json TEXT NOT NULL DEFAULT '[]'")
        if "submission_source" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN submission_source TEXT NOT NULL DEFAULT 'local'")
            self._db.execute(
                "UPDATE tasks SET submission_source='telegram' "
                "WHERE stage_logs_json LIKE '%已从 Telegram 接收图片并提交工作流%'"
            )
        if "task_type" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'workflow'")
            # Historical tasks were all created before toolbox jobs had a
            # distinct type. Keep their legacy meaning explicit and let new
            # toolbox submissions opt into the separate value below.
            self._db.execute("UPDATE tasks SET task_type='workflow'")
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
        if "prompt_group_snapshot_path" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN prompt_group_snapshot_path TEXT NOT NULL DEFAULT ''")
        if "manifest_path" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN manifest_path TEXT NOT NULL DEFAULT ''")
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
        if "output_prefix" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN output_prefix TEXT NOT NULL DEFAULT ''")
        if "project_id" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN project_id TEXT NOT NULL DEFAULT ''")
        if "project_name" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN project_name TEXT NOT NULL DEFAULT ''")
        if "project_path" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN project_path TEXT NOT NULL DEFAULT ''")
        if "project_inference_disabled" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN project_inference_disabled INTEGER NOT NULL DEFAULT 0")
        remote_queue_columns = {
            str(row[1]) for row in self._db.execute("PRAGMA table_info(remote_queue_cooldowns)").fetchall()
        }
        if "wait_for_predecessors" not in remote_queue_columns:
            self._db.execute(
                "ALTER TABLE remote_queue_cooldowns "
                "ADD COLUMN wait_for_predecessors INTEGER NOT NULL DEFAULT 0"
            )
        delivery_columns = {str(row[1]) for row in self._db.execute("PRAGMA table_info(telegram_deliveries)").fetchall()}
        if "status" not in delivery_columns:
            self._db.execute("ALTER TABLE telegram_deliveries ADD COLUMN status TEXT NOT NULL DEFAULT 'sent'")
        if "claimed_by" not in delivery_columns:
            self._db.execute("ALTER TABLE telegram_deliveries ADD COLUMN claimed_by TEXT NOT NULL DEFAULT ''")
        if "claim_until" not in delivery_columns:
            self._db.execute("ALTER TABLE telegram_deliveries ADD COLUMN claim_until INTEGER NOT NULL DEFAULT 0")
        if "attempts" not in delivery_columns:
            self._db.execute("ALTER TABLE telegram_deliveries ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if "last_error" not in delivery_columns:
            self._db.execute("ALTER TABLE telegram_deliveries ADD COLUMN last_error TEXT NOT NULL DEFAULT ''")
        if "updated_at" not in delivery_columns:
            self._db.execute("ALTER TABLE telegram_deliveries ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0")
        usage_columns = {str(row[1]) for row in self._db.execute("PRAGMA table_info(usage_records)").fetchall()}
        if "account_id" not in usage_columns:
            self._db.execute("ALTER TABLE usage_records ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
        if "site" not in usage_columns:
            self._db.execute("ALTER TABLE usage_records ADD COLUMN site TEXT NOT NULL DEFAULT ''")
        if "video_seconds" not in usage_columns:
            self._db.execute("ALTER TABLE usage_records ADD COLUMN video_seconds TEXT NOT NULL DEFAULT '0'")
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

    def douyin_cookie_path(self) -> str:
        value = self._read_json_file().get("douyin_cookie_path")
        return str(Path(value).expanduser().resolve()) if isinstance(value, str) and value.strip() else ""

    def set_douyin_cookie_path(self, value: str) -> str:
        raw_path = str(value or "").strip()
        data = self._read_json_file()
        if not raw_path:
            data.pop("douyin_cookie_path", None)
            self._write_json_file(data)
            return ""
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise RhCliError("INVALID_DOUYIN_COOKIE_PATH", f"抖音 Cookie 文件不存在：{path}")
        data["douyin_cookie_path"] = str(path)
        self._write_json_file(data)
        return str(path)

    def set_output_dir(self, value: str) -> str:
        path = str(Path(value).expanduser().resolve()).strip()
        if not path:
            raise RhCliError("INVALID_OUTPUT_DIR", "输出目录不能为空。")
        Path(path).mkdir(parents=True, exist_ok=True)
        data = self._read_json_file()
        data["output_dir"] = path
        self._write_json_file(data)
        return path

    def action_resources_path(self) -> str:
        value = self._read_json_file().get("action_resources_path")
        return str(_json_source_path(value)) if isinstance(value, str) and value.strip() else ""

    def set_action_resources_path(self, value: str) -> str:
        raw_path = Path(value).expanduser()
        if raw_path.suffix.lower() != ".json":
            raise RhCliError("INVALID_ACTION_RESOURCES_PATH", "动作库必须使用 JSON 文件。")
        path = _json_source_path(value)
        if not path.is_file():
            raise RhCliError("INVALID_ACTION_RESOURCES_PATH", f"动作库 JSON 文件不存在：{path}")
        data = self._read_json_file()
        data["action_resources_path"] = str(path)
        self._write_json_file(data)
        return str(path)

    def media_library_root(self) -> str:
        value = self._read_json_file().get("media_library_root")
        if isinstance(value, str) and value.strip():
            return str(Path(value).expanduser().resolve())
        return ""

    def _prompt_library_path_from_resources(self) -> str:
        roots: list[Path] = []
        configured_root = self.media_library_root()
        if configured_root:
            roots.append(Path(configured_root))
        roots.append(DEFAULT_RESOURCE_INDEX_PATH.parent)
        seen: set[Path] = set()
        for root in roots:
            root = root.expanduser().resolve()
            if root in seen:
                continue
            seen.add(root)
            index_path = root / "Resources.json"
            try:
                document = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if not isinstance(document, dict):
                continue
            sources = document.get("sources")
            prompt_source = sources.get("prompt") if isinstance(sources, dict) else ""
            if not isinstance(prompt_source, str) or not prompt_source.strip():
                continue
            media_root = document.get("media_root", ".")
            if not isinstance(media_root, str) or not media_root.strip():
                media_root = "."
            media_root_path = Path(media_root).expanduser()
            if not media_root_path.is_absolute():
                media_root_path = index_path.parent / media_root_path
            prompt_path = (media_root_path / prompt_source).resolve()
            if prompt_path.is_file():
                return str(prompt_path)
        return ""

    def set_media_library_root(self, value: str) -> str:
        raw_path = str(value or "").strip()
        if not raw_path:
            raise RhCliError("INVALID_MEDIA_LIBRARY_ROOT", "媒体库 ref 文件夹不能为空。")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_dir():
            raise RhCliError("INVALID_MEDIA_LIBRARY_ROOT", f"媒体库 ref 文件夹不存在：{path}")
        data = self._read_json_file()
        data["media_library_root"] = str(path)
        # The single root is now authoritative; leave the old setters available
        # for older clients, but prevent stale paths from taking precedence.
        data.pop("action_resources_path", None)
        data.pop("reference_resources_paths", None)
        self._write_json_file(data)
        return str(path)

    def prompt_library_path(self) -> str:
        indexed_path = self._prompt_library_path_from_resources()
        if indexed_path:
            return indexed_path
        value = self._read_json_file().get("prompt_library_path")
        if isinstance(value, str) and value.strip():
            return str(_json_source_path(value))
        return str((Path.home() / "Documents" / "VideoMake" / "ref" / "prompt" / "library.json").resolve())

    def set_prompt_library_path(self, value: str) -> str:
        raw_path = Path(value).expanduser()
        if raw_path.suffix.lower() != ".json":
            raise RhCliError("INVALID_PROMPT_LIBRARY_PATH", "基础积木必须使用 JSON 文件。")
        path = _json_source_path(value)
        if not path.is_file():
            raise RhCliError("INVALID_PROMPT_LIBRARY_PATH", f"基础积木 JSON 文件不存在：{path}")
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
            str(kind): str(_json_source_path(path))
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
            if Path(str(raw_path or "")).expanduser().suffix.lower() != ".json":
                raise RhCliError("INVALID_REFERENCE_RESOURCES_PATH", f"{kind} 资源库必须使用 JSON 文件。")
            path = _json_source_path(str(raw_path or ""))
            if not path.is_file():
                raise RhCliError("INVALID_REFERENCE_RESOURCES_PATH", f"{kind} 资源 JSON 文件不存在：{path}")
            updated[kind] = str(path)
        data = self._read_json_file()
        data["reference_resources_paths"] = updated
        self._write_json_file(data)
        return updated

    def personal_capacity(self) -> int:
        return personal_capacity_value(self._read_json_file().get("personal_capacity", DEFAULT_PERSONAL_CAPACITY))

    def pose_media_import_type(self) -> str:
        value = self._read_json_file().get("pose_media_import_type", DEFAULT_POSE_MEDIA_IMPORT_TYPE)
        try:
            return normalize_pose_media_import_type(value)
        except RhCliError:
            return DEFAULT_POSE_MEDIA_IMPORT_TYPE

    def set_pose_media_import_type(self, value: Any) -> str:
        import_type = normalize_pose_media_import_type(value)
        data = self._read_json_file()
        data["pose_media_import_type"] = import_type
        data.setdefault("output_dir", str(default_local_output_dir()))
        self._write_json_file(data)
        return import_type

    def api_key_strategy(self) -> str:
        value = self._read_json_file().get("api_key_strategy", DEFAULT_API_KEY_STRATEGY)
        try:
            return normalize_api_key_strategy(value)
        except RhCliError:
            return DEFAULT_API_KEY_STRATEGY

    def set_api_key_strategy(self, value: Any) -> str:
        strategy = normalize_api_key_strategy(value)
        data = self._read_json_file()
        data["api_key_strategy"] = strategy
        data.setdefault("output_dir", str(default_local_output_dir()))
        self._write_json_file(data)
        return strategy

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

    def aliyun_translation_credentials(self) -> tuple[str, str]:
        """Return local Aliyun translation credentials, falling back to env vars."""
        data = self._read_json_file()
        local_id = str(data.get("aliyun_translation_access_key_id") or "").strip()
        local_secret = str(data.get("aliyun_translation_access_key_secret") or "").strip()
        if local_id and local_secret:
            return local_id, local_secret
        env_id = str(os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID") or os.environ.get("ALIYUN_ACCESS_KEY_ID") or "").strip()
        env_secret = str(os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET") or os.environ.get("ALIYUN_ACCESS_KEY_SECRET") or "").strip()
        return env_id, env_secret

    def aliyun_translation_settings(self) -> dict[str, Any]:
        access_key_id, access_key_secret = self.aliyun_translation_credentials()
        data = self._read_json_file()
        has_local = bool(str(data.get("aliyun_translation_access_key_id") or "").strip() and str(data.get("aliyun_translation_access_key_secret") or "").strip())
        has_environment = bool(
            str(os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID") or os.environ.get("ALIYUN_ACCESS_KEY_ID") or "").strip()
            and str(os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET") or os.environ.get("ALIYUN_ACCESS_KEY_SECRET") or "").strip()
        )
        return {
            "configured": bool(access_key_id and access_key_secret),
            "access_key_id": access_key_id,
            "access_key_id_hint": mask_key(access_key_id) if access_key_id else "",
            "source": "local" if has_local else "environment" if has_environment else "",
        }

    def set_aliyun_translation_credentials(self, access_key_id: str, access_key_secret: str) -> dict[str, Any]:
        access_key_id = str(access_key_id or "").strip()
        access_key_secret = str(access_key_secret or "").strip()
        if not access_key_id or not access_key_secret:
            raise RhCliError("INVALID_TRANSLATION_CREDENTIALS", "AccessKey ID 和 AccessKey Secret 不能为空。")
        data = self._read_json_file()
        data["aliyun_translation_access_key_id"] = access_key_id
        data["aliyun_translation_access_key_secret"] = access_key_secret
        self._write_json_file(data)
        return self.aliyun_translation_settings()

    def aliyun_vision_api_key(self) -> str:
        """Return the local DashScope key, falling back to the standard environment variable."""
        data = self._read_json_file()
        local_key = str(data.get("aliyun_vision_api_key") or "").strip()
        return local_key or str(os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ALIYUN_VISION_API_KEY") or "").strip()

    def aliyun_vision_settings(self) -> dict[str, Any]:
        data = self._read_json_file()
        local_key = str(data.get("aliyun_vision_api_key") or "").strip()
        environment_key = str(os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ALIYUN_VISION_API_KEY") or "").strip()
        key = local_key or environment_key
        return {
            "configured": bool(key),
            "api_key_hint": mask_key(key) if key else "",
            "model": "qwen-vl-max",
            "source": "local" if local_key else "environment" if environment_key else "",
        }

    def set_aliyun_vision_api_key(self, api_key: str) -> dict[str, Any]:
        api_key = str(api_key or "").strip()
        if not api_key:
            raise RhCliError("INVALID_VISION_CREDENTIALS", "阿里云百炼 API Key 不能为空。")
        data = self._read_json_file()
        data["aliyun_vision_api_key"] = api_key
        self._write_json_file(data)
        return self.aliyun_vision_settings()

    def telegram_settings(self) -> dict[str, Any]:
        return TelegramNotifier(self).settings()

    def set_telegram_settings(self, bot_token: str, chat_id: str, enabled: Any) -> dict[str, Any]:
        data = self._read_json_file()
        bot_token = str(bot_token or "").strip()
        chat_id = str(chat_id or "").strip()
        if bot_token:
            data["telegram_bot_token"] = bot_token
        if chat_id:
            data["telegram_chat_id"] = chat_id
        effective_token = str(data.get("telegram_bot_token") or os.environ.get("RH_TELEGRAM_BOT_TOKEN") or "").strip()
        effective_chat_id = str(data.get("telegram_chat_id") or os.environ.get("RH_TELEGRAM_CHAT_ID") or "").strip()
        enabled_value = str(enabled or "").strip().lower() in {"1", "true", "yes", "on"} if isinstance(enabled, str) else bool(enabled)
        if enabled_value and not effective_token:
            raise RhCliError("INVALID_TELEGRAM_SETTINGS", "启用 Telegram 前请填写 Bot Token。")
        if enabled_value and not TelegramNotifier.parse_chat_ids(effective_chat_id):
            raise RhCliError("INVALID_TELEGRAM_SETTINGS", "启用 Telegram 前请填写 Chat ID。")
        data["telegram_enabled"] = enabled_value
        self._write_json_file(data)
        return self.telegram_settings()

    def set_telegram_inbound_settings(
        self,
        workflow_id: str,
        enabled: Any,
        mode: str | None = None,
        folder_id: str = "",
    ) -> dict[str, Any]:
        workflow_id = str(workflow_id or "").strip()
        enabled_value = str(enabled or "").strip().lower() in {"1", "true", "yes", "on"} if isinstance(enabled, str) else bool(enabled)
        data = self._read_json_file()
        mode_value = str(mode or "").strip().lower() if mode is not None else "fixed"
        mode_value = mode_value or "fixed"
        if mode_value not in {"fixed", "folder_random"}:
            raise RhCliError("INVALID_TELEGRAM_INBOUND_MODE", "Telegram 图片入站模式无效。")
        folder_id = str(folder_id or "").strip()
        if mode_value == "folder_random":
            folder_id = self._validate_workflow_folder_id(folder_id)
        if enabled_value:
            token = str(data.get("telegram_bot_token") or os.environ.get("RH_TELEGRAM_BOT_TOKEN") or "").strip()
            chat_id = str(data.get("telegram_chat_id") or os.environ.get("RH_TELEGRAM_CHAT_ID") or "").strip()
            if not token or not TelegramNotifier.parse_chat_ids(chat_id):
                raise RhCliError("INVALID_TELEGRAM_SETTINGS", "启用图片入站前请先配置 Bot Token 和 Chat ID。")
            if mode_value == "fixed":
                if not workflow_id:
                    raise RhCliError("INVALID_TELEGRAM_INBOUND_WORKFLOW", "请先选择 Telegram 入站工作流。")
                candidate = next(
                    (item for item in self.telegram_inbound_workflows() if str(item.get("id") or "") == workflow_id),
                    None,
                )
                if candidate is None:
                    raise RhCliError(
                        "INVALID_TELEGRAM_INBOUND_WORKFLOW",
                        "固定入站工作流必须已绑定账号、workflowId，并且只有一个必填图片输入。",
                    )
                data["telegram_inbound_workflow_id"] = workflow_id
                data["telegram_inbound_file_input_id"] = str(candidate.get("file_input_id") or "")
                data.pop("telegram_inbound_folder_id", None)
            else:
                if not folder_id:
                    raise RhCliError("INVALID_TELEGRAM_INBOUND_FOLDER", "请先选择 Telegram 图片入站文件夹。")
                candidates = self.telegram_inbound_workflows(folder_id)
                if not candidates:
                    raise RhCliError(
                        "INVALID_TELEGRAM_INBOUND_FOLDER",
                        "所选文件夹中没有可用的入站工作流，请先放入符合条件的工作流。",
                    )
                data["telegram_inbound_folder_id"] = folder_id
                data.pop("telegram_inbound_workflow_id", None)
                data.pop("telegram_inbound_file_input_id", None)
        elif mode_value == "fixed" and workflow_id:
            data["telegram_inbound_workflow_id"] = workflow_id
        elif mode_value == "folder_random" and folder_id:
            data["telegram_inbound_folder_id"] = folder_id
        data["telegram_inbound_mode"] = mode_value
        data["telegram_inbound_enabled"] = enabled_value
        self._write_json_file(data)
        return self.telegram_settings()

    def set_telegram_video_inbound_settings(self, workflow_id: str, enabled: Any) -> dict[str, Any]:
        """Configure a fixed workflow that receives one downloaded social video."""
        workflow_id = str(workflow_id or "").strip()
        enabled_value = str(enabled or "").strip().lower() in {"1", "true", "yes", "on"} if isinstance(enabled, str) else bool(enabled)
        data = self._read_json_file()
        if enabled_value:
            token = str(data.get("telegram_bot_token") or os.environ.get("RH_TELEGRAM_BOT_TOKEN") or "").strip()
            chat_id = str(data.get("telegram_chat_id") or os.environ.get("RH_TELEGRAM_CHAT_ID") or "").strip()
            if not token or not TelegramNotifier.parse_chat_ids(chat_id):
                raise RhCliError("INVALID_TELEGRAM_SETTINGS", "启用视频链接入站前请先配置 Bot Token 和 Chat ID。")
            if not workflow_id:
                raise RhCliError("INVALID_TELEGRAM_INBOUND_WORKFLOW", "请先选择 Telegram 视频入站工作流。")
            candidate = next(
                (item for item in self.telegram_video_inbound_workflows() if str(item.get("id") or "") == workflow_id),
                None,
            )
            if candidate is None:
                raise RhCliError(
                    "INVALID_TELEGRAM_INBOUND_WORKFLOW",
                    "固定视频入站工作流必须已绑定账号、workflowId，并且只有一个必填视频输入。",
                )
            data["telegram_video_inbound_workflow_id"] = workflow_id
            data["telegram_video_inbound_file_input_id"] = str(candidate.get("file_input_id") or "")
        elif workflow_id:
            data["telegram_video_inbound_workflow_id"] = workflow_id
        data["telegram_video_inbound_enabled"] = enabled_value
        self._write_json_file(data)
        return self.telegram_settings()

    def clear_telegram_settings(self) -> dict[str, Any]:
        data = self._read_json_file()
        data.pop("telegram_bot_token", None)
        data.pop("telegram_chat_id", None)
        data["telegram_enabled"] = False
        data.pop("telegram_inbound_workflow_id", None)
        data.pop("telegram_inbound_folder_id", None)
        data.pop("telegram_inbound_mode", None)
        data.pop("telegram_inbound_file_input_id", None)
        data["telegram_inbound_enabled"] = False
        data.pop("telegram_video_inbound_workflow_id", None)
        data.pop("telegram_video_inbound_file_input_id", None)
        data["telegram_video_inbound_enabled"] = False
        self._write_json_file(data)
        return self.telegram_settings()

    def _workflow_registry_path(self) -> Path:
        return DATA_ROOT / "workflow-registry.json"

    @staticmethod
    def _workflow_id(workflow_id: str) -> str:
        clean_id = str(workflow_id or "").strip()
        if not clean_id or Path(clean_id).name != clean_id:
            raise RhCliError("WORKFLOW_NOT_FOUND", f"工作流 ID 无效：{workflow_id}")
        return clean_id

    @staticmethod
    def _legacy_workflow_root() -> Path:
        return DATA_ROOT / "workflows"

    @staticmethod
    def _package_layout_enabled() -> bool:
        """Return whether the active root uses the new directory package layout.

        Tests and older callers may still inject a ``workflows`` root.  That
        root remains readable in its legacy flat layout while normal runtime
        data uses the singular ``workflow`` package root.
        """
        return WORKFLOW_ROOT.name == "workflow"

    def _workflow_package_dir(self, workflow_id: str) -> Path:
        clean_id = self._workflow_id(workflow_id)
        if self._package_layout_enabled():
            return WORKFLOW_ROOT / clean_id
        return WORKFLOW_ROOT

    def _legacy_registry_entry_path(self, workflow_id: str) -> Path:
        clean_id = self._workflow_id(workflow_id)
        return DATA_ROOT / "workflow-registry" / f"{clean_id}.json"

    def _workflow_registry_entries_root(self) -> Path:
        return DATA_ROOT / "workflow-registry"

    def _workflow_registry_entry_path(self, workflow_id: str) -> Path:
        clean_id = self._workflow_id(workflow_id)
        if self._package_layout_enabled():
            return self._workflow_package_dir(clean_id) / WORKFLOW_PACKAGE_MANIFEST_FILENAME
        return self._legacy_registry_entry_path(clean_id)

    def _workflow_relative_file(self, workflow_id: str) -> str:
        clean_id = self._workflow_id(workflow_id)
        if self._package_layout_enabled():
            return f"workflow/{clean_id}/{WORKFLOW_API_FILENAME}"
        return f"workflows/{clean_id}.json"

    def _workflow_relative_prompt_group_file(self, workflow_id: str) -> str:
        clean_id = self._workflow_id(workflow_id)
        if self._package_layout_enabled():
            return f"workflow/{clean_id}/{PROMPT_GROUP_SNAPSHOT_FILENAME}"
        return f"workflows/{clean_id}{WORKFLOW_PROMPT_GROUP_SUFFIX}"

    def _workflow_file_path(self, workflow_id: str) -> Path:
        clean_id = self._workflow_id(workflow_id)
        if self._package_layout_enabled():
            return WORKFLOW_ROOT / clean_id / WORKFLOW_API_FILENAME
        return WORKFLOW_ROOT / f"{clean_id}.json"

    def _migrate_legacy_workflow_files(self) -> None:
        """Migrate the historical flat library into directory packages."""
        if self._package_layout_enabled():
            self._migrate_workflow_library_packages()
            return

        # Compatibility path for callers that explicitly inject the old
        # ``data/workflows`` root (including older integrations and tests).
        grouped: dict[str, list[Path]] = {}
        for path in WORKFLOW_ROOT.glob("wf_*_*.json"):
            if not path.is_file():
                continue
            match = re.match(r"^(wf_[A-Za-z0-9]+)_.+\.json$", path.name)
            if not match:
                continue
            grouped.setdefault(match.group(1), []).append(path.resolve())

        changed = False
        moved_ids: set[str] = set()
        for workflow_id, candidates in grouped.items():
            stable = self._workflow_file_path(workflow_id).resolve()
            if stable.exists() or len(candidates) != 1:
                # Do not guess when a manually assembled directory contains
                # conflicting files, and never overwrite an existing stable
                # file.
                continue
            source = candidates[0]
            try:
                source.rename(stable)
            except OSError:
                continue
            self._db.execute(
                "UPDATE tasks SET workflow_path=? WHERE workflow_path IN (?, ?)",
                (str(stable), str(source), str(source.resolve())),
            )
            changed = True
            moved_ids.add(workflow_id)
        if changed:
            self._db.commit()
            records = self._read_workflow_registry()
            if records:
                for record in records:
                    if str(record.get("id") or "").strip() in moved_ids:
                        record["workflow_file"] = self._workflow_relative_file(str(record.get("id") or ""))
                self._write_workflow_registry(records)

    def _migrate_workflow_library_packages(self) -> None:
        """Move registered flat workflow files into three-file packages.

        The migration is deliberately conservative: it only handles IDs in
        the registry, copies each source before validating the destination,
        updates task metadata, and removes the old files only after all three
        package members are present.  A caller can keep a filesystem archive
        of ``data/workflows`` before opening the store for an additional
        recovery point.
        """
        legacy_root = self._legacy_workflow_root()
        if legacy_root.resolve() == WORKFLOW_ROOT.resolve() or not legacy_root.is_dir():
            return
        records = self._read_workflow_registry()
        if not records:
            return
        migrated = False
        for record in records:
            workflow_id = str(record.get("id") or "").strip()
            if not workflow_id:
                continue
            package_dir = self._workflow_package_dir(workflow_id)
            workflow_path = package_dir / WORKFLOW_API_FILENAME
            prompt_path = package_dir / PROMPT_GROUP_SNAPSHOT_FILENAME
            manifest_path = package_dir / WORKFLOW_PACKAGE_MANIFEST_FILENAME
            source = self._legacy_registry_workflow_source(workflow_id, record, legacy_root)
            legacy_prompt = legacy_root / f"{workflow_id}{WORKFLOW_PROMPT_GROUP_SUFFIX}"
            if not workflow_path.is_file() and source is not None:
                package_dir.mkdir(parents=True, exist_ok=True)
                temporary = workflow_path.with_suffix(".json.tmp")
                shutil.copy2(source, temporary)
                temporary.replace(workflow_path)
                self._db.execute(
                    "UPDATE tasks SET workflow_path=? WHERE workflow_path IN (?, ?)",
                    (str(workflow_path.resolve()), str(source), str(source.resolve())),
                )
                migrated = True
            if workflow_path.is_file() and not prompt_path.is_file():
                package_dir.mkdir(parents=True, exist_ok=True)
                if legacy_prompt.is_file():
                    temporary = prompt_path.with_suffix(".json.tmp")
                    shutil.copy2(legacy_prompt, temporary)
                    temporary.replace(prompt_path)
                else:
                    self._write_workflow_prompt_group(workflow_id, None, keep_empty=True)
                migrated = True
            if workflow_path.is_file() and prompt_path.is_file() and not manifest_path.is_file():
                migrated = True

            if workflow_path.is_file() and prompt_path.is_file():
                # The package manifest is written by _write_workflow_registry
                # below, after all source paths have been normalized.
                old_workflow = legacy_root / f"{workflow_id}.json"
                old_prompt = legacy_prompt
                if old_workflow.is_file() and old_workflow.resolve() != workflow_path.resolve():
                    try:
                        old_workflow.unlink()
                    except OSError:
                        pass
                if old_prompt.is_file() and old_prompt.resolve() != prompt_path.resolve():
                    try:
                        old_prompt.unlink()
                    except OSError:
                        pass

        if migrated:
            self._db.commit()
            normalized_records = [dict(record) for record in records if str(record.get("id") or "").strip()]
            self._write_workflow_registry(normalized_records)
            # Registered task rows retain their immutable output snapshot, but
            # their source metadata should point at the new library package.
            for record in normalized_records:
                workflow_id = str(record.get("id") or "").strip()
                package_path = self._workflow_file_path(workflow_id).resolve()
                self._db.execute(
                    "UPDATE tasks SET workflow_path=? WHERE registered_workflow_id=?",
                    (str(package_path), workflow_id),
                )
            self._db.commit()
            # Legacy detailed registry files are no longer the package
            # registration source.  Leave unrelated hand-written files alone.
            entries_root = self._workflow_registry_entries_root()
            for record in normalized_records:
                entry = entries_root / f"{str(record.get('id') or '').strip()}.json"
                try:
                    entry.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    def _legacy_registry_workflow_source(
        self,
        workflow_id: str,
        record: dict[str, Any],
        legacy_root: Path,
    ) -> Path | None:
        """Resolve a pre-package API file without following registry traversal."""
        candidates: list[Path] = []
        reference = str(record.get("workflow_file") or "").strip()
        if reference:
            candidate = Path(reference).expanduser()
            if not candidate.is_absolute():
                candidate = DATA_ROOT / candidate
            try:
                resolved = candidate.resolve()
                resolved.relative_to(legacy_root.resolve())
            except (OSError, ValueError):
                resolved = None
            if resolved is not None and resolved.is_file():
                candidates.append(resolved)
        candidates.extend(
            path.resolve()
            for path in (
                legacy_root / f"{workflow_id}.json",
                *legacy_root.glob(f"{workflow_id}_*.json"),
            )
            if path.is_file()
        )
        unique = {str(path): path for path in candidates}
        if len(unique) == 1:
            return next(iter(unique.values()))
        if len(unique) > 1:
            raw_name = str(record.get("name") or "").strip()
            preferred = legacy_root / f"{workflow_id}_{canonical_workflow_name(raw_name)}" if raw_name else None
            if preferred and preferred.is_file():
                return preferred.resolve()
        return None

    def _backfill_registered_workflow_source_paths(self) -> None:
        """Point registered task source metadata at the current library package."""
        if not self._package_layout_enabled():
            return
        records = self._read_workflow_registry()
        changed = False
        for record in records:
            workflow_id = str(record.get("id") or "").strip()
            if not workflow_id or not self._workflow_file_path(workflow_id).is_file():
                continue
            cursor = self._db.execute(
                "UPDATE tasks SET workflow_path=? WHERE registered_workflow_id=? AND workflow_path!=?",
                (str(self._workflow_file_path(workflow_id).resolve()), workflow_id, str(self._workflow_file_path(workflow_id).resolve())),
            )
            changed = changed or cursor.rowcount > 0
        if changed:
            self._db.commit()

    def _registry_workflow_file_path(self, workflow_id: str, record: dict[str, Any] | None = None) -> Path:
        """Resolve an indexed workflow path without allowing registry traversal."""
        fallback = self._workflow_file_path(workflow_id)
        reference = str((record or {}).get("workflow_file") or "").strip()
        if not reference:
            return fallback
        candidate = Path(reference).expanduser()
        if not candidate.is_absolute():
            candidate = DATA_ROOT / candidate
        try:
            resolved = candidate.resolve()
            allowed_root = WORKFLOW_ROOT.resolve() if self._package_layout_enabled() else self._legacy_workflow_root().resolve()
            resolved.relative_to(allowed_root)
        except (OSError, ValueError):
            return fallback
        return resolved if resolved.suffix.lower() == ".json" else fallback

    def _legacy_workflow_candidates(self, workflow_id: str, record: dict[str, Any] | None = None) -> list[Path]:
        stable = self._workflow_file_path(workflow_id).resolve()
        candidates: list[Path] = []
        indexed = self._registry_workflow_file_path(workflow_id, record)
        if indexed != stable and indexed.is_file():
            candidates.append(indexed)
        candidates.extend(
            path.resolve()
            for path in (WORKFLOW_ROOT if not self._package_layout_enabled() else self._legacy_workflow_root()).glob(f"{workflow_id}_*.json")
            if path.is_file()
        )
        unique: dict[str, Path] = {str(path): path for path in candidates}
        return sorted(unique.values(), key=lambda path: str(path))

    def _migrate_legacy_workflow_file(self, workflow_id: str, record: dict[str, Any] | None = None) -> Path:
        """Move one legacy ``<id>_<name>.json`` file to the stable ID path."""
        stable = self._workflow_file_path(workflow_id).resolve()
        if stable.is_file():
            return stable
        if self._package_layout_enabled():
            source = self._legacy_registry_workflow_source(workflow_id, record or {}, self._legacy_workflow_root())
            if source is None:
                return stable
            stable.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, stable)
            except OSError as exc:
                raise RhCliError("WORKFLOW_MIGRATION_FAILED", f"工作流文件迁移失败：{source.name}") from exc
            self._db.execute(
                "UPDATE tasks SET workflow_path=? WHERE workflow_path IN (?, ?)",
                (str(stable), str(source), str(source.resolve())),
            )
            self._db.commit()
            if record is not None:
                migrated = dict(record)
                migrated["workflow_file"] = self._workflow_relative_file(workflow_id)
                self._upsert_workflow_registry(migrated)
            return stable
        candidates = self._legacy_workflow_candidates(workflow_id, record)
        if len(candidates) > 1:
            raw_name = str((record or {}).get("name") or "").strip()
            expected = stable.parent / f"{workflow_id}_{canonical_workflow_name(raw_name)}" if raw_name else None
            preferred = expected.resolve() if expected else None
            if preferred and preferred in candidates:
                candidates = [preferred]
            else:
                raise RhCliError("WORKFLOW_FILE_AMBIGUOUS", f"工作流 {workflow_id} 存在多个旧版文件，无法自动迁移。")
        if not candidates:
            return stable
        source = candidates[0]
        try:
            source.rename(stable)
        except FileExistsError:
            return stable if stable.is_file() else source
        except OSError as exc:
            raise RhCliError("WORKFLOW_MIGRATION_FAILED", f"工作流文件迁移失败：{source.name}") from exc
        # Existing task history may still point to the old physical filename.
        # Keep those records replayable when the file is moved in place.
        self._db.execute(
            "UPDATE tasks SET workflow_path=? WHERE workflow_path IN (?, ?)",
            (str(stable), str(source), str(source.resolve())),
        )
        self._db.commit()
        if record is not None:
            migrated = dict(record)
            migrated["workflow_file"] = self._workflow_relative_file(workflow_id)
            self._upsert_workflow_registry(migrated)
        return stable

    @staticmethod
    def _read_workflow_registry_entry(path: Path, workflow_id: str) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        value["id"] = workflow_id
        return value

    def _read_workflow_registry(self) -> list[dict[str, Any]]:
        path = self._workflow_registry_path()
        data: Any = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                data = {}
        records = data.get("workflows", []) if isinstance(data, dict) else []
        if not isinstance(records, list):
            records = []

        # A package manifest is the authoritative registration file in the
        # new layout.  The old detailed sidecars remain a read-only fallback
        # while an installation is being migrated.
        if self._package_layout_enabled() and not records:
            for manifest in sorted(WORKFLOW_ROOT.glob(f"*/{WORKFLOW_PACKAGE_MANIFEST_FILENAME}")):
                workflow_id = manifest.parent.name
                entry = self._read_workflow_registry_entry(manifest, workflow_id)
                if entry is not None:
                    records.append({"id": workflow_id, "file": str(manifest.relative_to(DATA_ROOT))})

        data_root = path.parent.resolve()
        entries_root = self._workflow_registry_entries_root().resolve()
        result: list[dict[str, Any]] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            workflow_id = str(item.get("id") or "").strip()
            if not workflow_id:
                continue
            reference = str(item.get("file") or item.get("path") or "").strip()
            if reference:
                reference_path = Path(reference).expanduser()
                if reference_path.is_absolute():
                    candidate_paths = [reference_path.resolve()]
                else:
                    candidate_paths = [
                        (path.parent / reference_path).resolve(),
                        (entries_root / reference_path).resolve(),
                    ]
                entry_path = next(
                    (
                        candidate
                        for candidate in candidate_paths
                        if candidate.is_file()
                        and (
                            candidate.is_relative_to(data_root)
                            or candidate.is_relative_to(entries_root)
                        )
                    ),
                    None,
                )
                if entry_path is not None:
                    entry = self._read_workflow_registry_entry(entry_path, workflow_id)
                    if entry is not None:
                        # Index fields are allowed as a fallback for hand-edited
                        # entries, while the sidecar remains the source of detail.
                        entry.update(
                            {
                                key: value
                                for key, value in item.items()
                                if key not in {"file", "path"} and key not in entry
                            }
                        )
                        result.append(entry)
                        continue
            # Legacy inline records remain readable and are converted on the
            # next registry write.
            result.append(dict(item))
        return result

    def _write_workflow_registry(self, records: list[dict[str, Any]]) -> None:
        path = self._workflow_registry_path()
        entries_root = self._workflow_registry_entries_root()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self._package_layout_enabled():
            entries_root.mkdir(parents=True, exist_ok=True)
        index_records: list[dict[str, str]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            workflow_id = str(record.get("id") or "").strip()
            if not workflow_id:
                continue
            saved_record = dict(record)
            saved_record["workflow_file"] = self._workflow_relative_file(workflow_id)
            if self._package_layout_enabled():
                saved_record["prompt_group_file"] = self._workflow_relative_prompt_group_file(workflow_id)
                saved_record["manifest_file"] = f"workflow/{workflow_id}/{WORKFLOW_PACKAGE_MANIFEST_FILENAME}"
                saved_record["package_dir"] = f"workflow/{workflow_id}"
            elif str(saved_record.get("prompt_group_id") or "").strip() or str(saved_record.get("prompt_group_file") or "").strip():
                saved_record["prompt_group_file"] = self._workflow_relative_prompt_group_file(workflow_id)
            else:
                saved_record.pop("prompt_group_file", None)
            entry_path = self._workflow_registry_entry_path(workflow_id)
            entry_path.parent.mkdir(parents=True, exist_ok=True)
            entry_temporary = entry_path.with_suffix(entry_path.suffix + ".tmp")
            entry_temporary.write_text(json.dumps(saved_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(entry_temporary, 0o600)
            entry_temporary.replace(entry_path)
            index_records.append(
                {
                    "id": workflow_id,
                    "file": str(entry_path.relative_to(path.parent)),
                }
            )
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"version": WORKFLOW_REGISTRY_FORMAT_VERSION, "workflows": index_records},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def _workflow_folder_registry_path(self) -> Path:
        return WORKFLOW_ROOT.parent / "workflow-folders.json"

    def _read_workflow_folders(self) -> list[dict[str, Any]]:
        path = self._workflow_folder_registry_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        records = data.get("folders", []) if isinstance(data, dict) else []
        return [dict(item) for item in records if isinstance(item, dict) and str(item.get("id") or "").strip()]

    def _write_workflow_folders(self, folders: list[dict[str, Any]]) -> None:
        path = self._workflow_folder_registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"folders": folders}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def workflow_folders(self) -> list[dict[str, Any]]:
        """Return the persisted folders with current library membership counts."""
        counts: dict[str, int] = {}
        for record in self._read_workflow_registry():
            if str(record.get("source") or "") != "library":
                continue
            folder_id = str(record.get("folder_id") or "").strip()
            if folder_id:
                counts[folder_id] = counts.get(folder_id, 0) + 1
        result = []
        for folder in self._read_workflow_folders():
            folder_id = str(folder.get("id") or "").strip()
            result.append(
                {
                    "id": folder_id,
                    "name": str(folder.get("name") or "未命名文件夹"),
                    "created_at": int(folder.get("created_at") or 0),
                    "updated_at": int(folder.get("updated_at") or 0),
                    "workflow_count": counts.get(folder_id, 0),
                }
            )
        return sorted(result, key=lambda item: (int(item.get("created_at") or 0), str(item.get("name") or "")))

    @staticmethod
    def _clean_workflow_folder_name(name: str) -> str:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise RhCliError("INVALID_WORKFLOW_FOLDER", "文件夹名称不能为空。")
        if len(clean_name) > 80:
            raise RhCliError("INVALID_WORKFLOW_FOLDER", "文件夹名称不能超过 80 个字符。")
        if any(char in clean_name for char in "/\\\0"):
            raise RhCliError("INVALID_WORKFLOW_FOLDER", "文件夹名称不能包含路径分隔符。")
        return clean_name

    def create_workflow_folder(self, name: str) -> dict[str, Any]:
        clean_name = self._clean_workflow_folder_name(name)
        folders = self._read_workflow_folders()
        if any(str(item.get("name") or "").strip().casefold() == clean_name.casefold() for item in folders):
            raise RhCliError("WORKFLOW_FOLDER_EXISTS", f"文件夹已存在：{clean_name}")
        timestamp = now_ms()
        folder = {
            "id": f"wff_{uuid.uuid4().hex[:12]}",
            "name": clean_name,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        folders.append(folder)
        self._write_workflow_folders(folders)
        return {**folder, "workflow_count": 0}

    def rename_workflow_folder(self, folder_id: str, name: str) -> dict[str, Any]:
        folder_id = str(folder_id or "").strip()
        clean_name = self._clean_workflow_folder_name(name)
        folders = self._read_workflow_folders()
        target = next((item for item in folders if str(item.get("id") or "") == folder_id), None)
        if target is None:
            raise RhCliError("WORKFLOW_FOLDER_NOT_FOUND", f"找不到文件夹：{folder_id}")
        if any(
            str(item.get("id") or "") != folder_id
            and str(item.get("name") or "").strip().casefold() == clean_name.casefold()
            for item in folders
        ):
            raise RhCliError("WORKFLOW_FOLDER_EXISTS", f"文件夹已存在：{clean_name}")
        target["name"] = clean_name
        target["updated_at"] = now_ms()
        self._write_workflow_folders(folders)
        return next(item for item in self.workflow_folders() if item["id"] == folder_id)

    def delete_workflow_folder(self, folder_id: str) -> None:
        folder_id = str(folder_id or "").strip()
        folders = self._read_workflow_folders()
        if not any(str(item.get("id") or "") == folder_id for item in folders):
            raise RhCliError("WORKFLOW_FOLDER_NOT_FOUND", f"找不到文件夹：{folder_id}")
        self._write_workflow_folders([item for item in folders if str(item.get("id") or "") != folder_id])
        records = self._read_workflow_registry()
        changed = False
        for record in records:
            if str(record.get("folder_id") or "") == folder_id:
                record.pop("folder_id", None)
                changed = True
        if changed:
            self._write_workflow_registry(records)
        data = self._read_json_file()
        if str(data.get("telegram_inbound_folder_id") or "").strip() == folder_id:
            data.pop("telegram_inbound_folder_id", None)
            if str(data.get("telegram_inbound_mode") or "fixed").strip().lower() == "folder_random":
                data["telegram_inbound_enabled"] = False
            self._write_json_file(data)

    def _validate_workflow_folder_id(self, folder_id: Any) -> str:
        value = str(folder_id or "").strip()
        if value and not any(str(item.get("id") or "") == value for item in self._read_workflow_folders()):
            raise RhCliError("WORKFLOW_FOLDER_NOT_FOUND", f"找不到文件夹：{value}")
        return value

    def set_workflow_folder(self, workflow_id: str, folder_id: Any) -> dict[str, Any]:
        """Move a library workflow into a folder; an empty folder ID means unclassified."""
        record = self.workflow_record(workflow_id)
        folder_id = self._validate_workflow_folder_id(folder_id)
        records = self._read_workflow_registry()
        target = next((item for item in records if str(item.get("id") or "") == str(record["id"])), None)
        if target is None:
            raise RhCliError("WORKFLOW_NOT_FOUND", f"找不到工作流：{workflow_id}")
        if folder_id:
            target["folder_id"] = folder_id
        else:
            target.pop("folder_id", None)
        target["updated_at"] = now_ms()
        self._write_workflow_registry(records)
        return self.workflow_record(workflow_id)

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
            "workflow_file": self._workflow_relative_file(str(record.get("id") or "")),
            "account_id": str(record.get("account_id") or ""),
            "site": str(record.get("site") or ""),
            "remote_workflow_id": str(record.get("remote_workflow_id") or ""),
            "source_dir": str(record.get("source_dir") or ""),
            "source": "library",
            "created_at": int(record.get("created_at") or now_ms()),
            "updated_at": int(record.get("updated_at") or now_ms()),
        }
        if str(record.get("folder_id") or "").strip():
            saved["folder_id"] = str(record.get("folder_id")).strip()
        if isinstance(record.get("input_config"), dict):
            saved["input_config"] = record["input_config"]
        prompt_group_id = str(record.get("prompt_group_id") or "").strip()
        prompt_group_name = str(record.get("prompt_group_name") or "").strip()
        if prompt_group_id or bool(record.get("_package_prompt_group")):
            saved["prompt_group_id"] = prompt_group_id
            saved["prompt_group_file"] = self._workflow_relative_prompt_group_file(str(record.get("id") or ""))
            if prompt_group_name:
                saved["prompt_group_name"] = prompt_group_name
        records.append(saved)
        self._write_workflow_registry(records)

    @staticmethod
    def _normalise_workflow_prompt_group(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise RhCliError("INVALID_PROMPT_GROUP", "关联提示词组格式无效。")
        group_id = str(value.get("id") or "").strip()
        name = str(value.get("name") or "").strip()
        items = value.get("items")
        if not isinstance(items, list):
            raise RhCliError("INVALID_PROMPT_GROUP", "关联提示词组必须包含 ID、名称和积木列表。")
        # Every library package owns a prompt-group sidecar.  An empty sidecar
        # is an internal package component and is deliberately not exposed as
        # a user-facing prompt group.
        if not group_id and not name:
            return {"id": "", "name": "", "updated_at": int(value.get("updated_at") or now_ms()), "items": []}
        if not group_id or not name:
            raise RhCliError("INVALID_PROMPT_GROUP", "关联提示词组必须包含 ID、名称和积木列表。")
        return {
            "id": group_id,
            "name": name,
            "updated_at": int(value.get("updated_at") or now_ms()),
            "items": json.loads(json.dumps(items, ensure_ascii=False)),
        }

    @staticmethod
    def workflow_prompt_group_path(workflow_id: str) -> Path:
        clean_id = str(workflow_id or "").strip()
        if not clean_id or Path(clean_id).name != clean_id:
            raise RhCliError("WORKFLOW_NOT_FOUND", f"工作流 ID 无效：{workflow_id}")
        if WORKFLOW_ROOT.name == "workflow":
            return WORKFLOW_ROOT / clean_id / PROMPT_GROUP_SNAPSHOT_FILENAME
        return WORKFLOW_ROOT / f"{clean_id}{WORKFLOW_PROMPT_GROUP_SUFFIX}"

    def _read_workflow_prompt_group(self, workflow_id: str) -> dict[str, Any] | None:
        path = self.workflow_prompt_group_path(workflow_id)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RhCliError("INVALID_PROMPT_GROUP", f"无法读取工作流关联的提示词组：{path.name}") from exc
        group = self._normalise_workflow_prompt_group(value)
        return group if group and (group.get("id") or group.get("name")) else None

    def _write_workflow_prompt_group(self, workflow_id: str, value: Any, *, keep_empty: bool = False) -> None:
        path = self.workflow_prompt_group_path(workflow_id)
        group = self._normalise_workflow_prompt_group(value)
        if group is None:
            if keep_empty:
                group = {"id": "", "name": "", "updated_at": now_ms(), "items": []}
            else:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise RhCliError("PROMPT_GROUP_DELETE_FAILED", f"删除工作流提示词组失败：{path.name}") from exc
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(group, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def save_workflow(
        self,
        filename: str,
        content: str,
        *,
        account_id: str = "",
        remote_workflow_id: str = "",
        source_dir: str = "",
        input_config: dict[str, Any] | None = None,
        input_defaults: list[dict[str, Any]] | None = None,
        prompt_group: dict[str, Any] | None = None,
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
        input_config = prune_workflow_input_config_for_workflow(workflow, input_config)
        normalized_input_config = normalize_workflow_input_config(workflow, input_config)
        if input_defaults:
            apply_workflow_input_defaults(workflow, input_defaults)
            analysis = inspect_workflow(workflow)
        normalized_prompt_group = self._normalise_workflow_prompt_group(prompt_group)
        if account_id:
            metadata["accountId"] = account_id
            workflow[WORKFLOW_META_KEY] = metadata
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        clean_name = canonical_workflow_name(filename)
        path = self._workflow_file_path(workflow_id)
        if register:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(path)
            record = {
                "id": workflow_id,
                "name": clean_name,
                "account_id": account["id"] if account else "",
                "site": account["site"] if account else "",
                "remote_workflow_id": str(remote_workflow_id or "").strip() or analysis.get("remote_workflow_id", ""),
                "source_dir": str(source_dir or "").strip(),
                "created_at": now_ms(),
                "updated_at": now_ms(),
                "input_config": normalized_input_config,
                "_package_prompt_group": True,
            }
            if normalized_prompt_group:
                record["prompt_group_id"] = normalized_prompt_group["id"]
                record["prompt_group_name"] = normalized_prompt_group["name"]
            self._upsert_workflow_registry(record)
            self._write_workflow_prompt_group(workflow_id, normalized_prompt_group, keep_empty=True)
        return workflow_id, path, analysis

    def workflow_path(self, workflow_id: str) -> Path:
        workflow_id = str(workflow_id or "").strip()
        registered = next(
            (item for item in self._read_workflow_registry() if str(item.get("id") or "") == workflow_id),
            None,
        )
        path = self._migrate_legacy_workflow_file(workflow_id, registered)
        if not path.is_file():
            raise RhCliError("WORKFLOW_NOT_FOUND", f"找不到工作流：{workflow_id}")
        return path.resolve()

    def workflows(self) -> list[dict[str, Any]]:
        """Return local workflow library records without exposing workflow JSON."""
        registry = {
            str(item.get("id")): item
            for item in self._read_workflow_registry()
            if str(item.get("source") or "") == "library"
        }
        accounts = {str(item.get("id")): item for item in self.accounts()}
        known_folder_ids = {
            str(item.get("id") or "").strip()
            for item in self._read_workflow_folders()
            if str(item.get("id") or "").strip()
        }
        result: list[dict[str, Any]] = []
        for local_id, registered in registry.items():
            path = self._migrate_legacy_workflow_file(local_id, registered)
            if not path.is_file():
                continue
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
                name = path.name if path.name != f"{local_id}.json" else "workflow.json"
            site = account.get("site") if account else str(registered.get("site") or "").strip()
            remote_id = str(registered.get("remote_workflow_id") or "").strip() or str(analysis.get("remote_workflow_id") or "").strip()
            folder_id = str(registered.get("folder_id") or "").strip()
            if folder_id not in known_folder_ids:
                folder_id = ""
            created_at = int(registered.get("created_at") or (stat.st_ctime * 1000 if stat else 0))
            updated_at = int(registered.get("updated_at") or (stat.st_mtime * 1000 if stat else 0))
            prompt_group = self._read_workflow_prompt_group(local_id)
            prompt_group_id = str((prompt_group or {}).get("id") or registered.get("prompt_group_id") or "").strip()
            prompt_group_name = str((prompt_group or {}).get("name") or registered.get("prompt_group_name") or "").strip()
            result.append(
                {
                    "id": local_id,
                    "name": name,
                    "account_id": account_id,
                    "account_name": str(account.get("name") or "") if account else "",
                    "site": site,
                    "remote_workflow_id": remote_id,
                    "folder_id": folder_id,
                    "source_dir": str(registered.get("source_dir") or ""),
                    "workflow_path": str(path.resolve()),
                    "prompt_group_path": str(self.workflow_prompt_group_path(local_id).resolve()) if self.workflow_prompt_group_path(local_id).is_file() else "",
                    "input_config": registered.get("input_config") if isinstance(registered.get("input_config"), dict) else None,
                    "prompt_group_id": prompt_group_id,
                    "prompt_group_name": prompt_group_name,
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

    def _telegram_inbound_workflows_for_media(
        self, folder_id: str = "", media_type: str = "image",
    ) -> list[dict[str, Any]]:
        """Return valid library workflows for one required Telegram media input."""
        folder_id = str(folder_id or "").strip()
        result: list[dict[str, Any]] = []
        for record in self.workflows():
            workflow_id = str(record.get("id") or "").strip()
            if not workflow_id or (folder_id and str(record.get("folder_id") or "").strip() != folder_id):
                continue
            if not str(record.get("account_id") or "").strip() or not str(record.get("remote_workflow_id") or "").strip():
                continue
            try:
                file_input = telegram_inbound_file_input(self.workflow_detail(workflow_id), media_type)
            except (RhCliError, OSError, ValueError):
                continue
            result.append(
                {
                    "id": workflow_id,
                    "name": str(record.get("name") or workflow_id).strip() or workflow_id,
                    "account_name": str(record.get("account_name") or "").strip(),
                    "folder_id": str(record.get("folder_id") or "").strip(),
                    "file_input_id": str(file_input.get("id") or "").strip(),
                }
            )
        return sorted(result, key=lambda item: (str(item.get("name") or ""), str(item.get("id") or "")))

    def telegram_inbound_workflows(self, folder_id: str = "") -> list[dict[str, Any]]:
        """Return valid library workflows that can receive one Telegram image."""
        return self._telegram_inbound_workflows_for_media(folder_id, "image")

    def telegram_video_inbound_workflows(self, folder_id: str = "") -> list[dict[str, Any]]:
        """Return valid library workflows that can receive one Telegram video."""
        return self._telegram_inbound_workflows_for_media(folder_id, "video")

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
        return {
            "record": record,
            "workflow": workflow,
            "analysis": analysis,
            "prompt_group": self._read_workflow_prompt_group(workflow_id),
        }

    def rename_workflow(self, workflow_id: str, name: str) -> dict[str, Any]:
        """Rename the display name while keeping the stable ID-based JSON path."""
        workflow_id = str(workflow_id or "").strip()
        if not workflow_id:
            raise RhCliError("WORKFLOW_NOT_FOUND", "缺少工作流 ID。")
        clean_name = canonical_workflow_name(str(name or "").strip(), "workflow.json")
        if not clean_name.lower().endswith(".json"):
            clean_name += ".json"
        source_path = self.workflow_path(workflow_id)

        record = next(
            (item for item in self._read_workflow_registry() if str(item.get("id") or "") == workflow_id),
            None,
        )
        if record is None:
            return {"id": workflow_id, "name": clean_name, "workflow_path": str(source_path.resolve())}
        record["name"] = clean_name
        record["updated_at"] = now_ms()
        self._upsert_workflow_registry(record)
        return self.workflow_record(workflow_id)

    def update_workflow(self, workflow_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        detail = self.workflow_detail(workflow_id)
        current = detail["record"]
        workflow = detail["workflow"]
        if "content" in changes:
            raw_content = changes.get("content")
            if isinstance(raw_content, dict):
                candidate = raw_content
            else:
                try:
                    candidate = json.loads(str(raw_content or ""))
                except (TypeError, ValueError) as exc:
                    raise RhCliError("INVALID_WORKFLOW", "工作流 JSON 格式无效。") from exc
            if not isinstance(candidate, dict):
                raise RhCliError("INVALID_WORKFLOW", "工作流顶层必须是 API 格式节点字典。")
            inspect_workflow(candidate)
            workflow = candidate
        if "input_defaults" in changes:
            apply_workflow_input_defaults(workflow, changes.get("input_defaults"))
        account_id = str(changes["account_id"]).strip() if "account_id" in changes else str(current.get("account_id") or "")
        account = self.get_account(account_id) if account_id else None
        if account_id and not account:
            raise RhCliError("ACCOUNT_NOT_FOUND", "找不到该工作流所属账号。")
        name = canonical_workflow_name(
            str(changes.get("name", current.get("name") or "workflow.json")).strip(),
            "workflow.json",
        )
        if not name.lower().endswith(".json"):
            name += ".json"
        remote_id = str(changes.get("remote_workflow_id", current.get("remote_workflow_id") or "")).strip()
        folder_id = self._validate_workflow_folder_id(changes.get("folder_id", current.get("folder_id") or ""))
        input_config = current.get("input_config") if isinstance(current.get("input_config"), dict) else None
        if "input_config" in changes:
            input_config = prune_workflow_input_config_for_workflow(workflow, changes.get("input_config"))
            input_config = normalize_workflow_input_config(workflow, input_config)
        prompt_group = detail.get("prompt_group")
        if "prompt_group" in changes:
            prompt_group = self._normalise_workflow_prompt_group(changes.get("prompt_group"))
        record = {
            **current,
            "name": name,
            "account_id": account["id"] if account else "",
            "account_name": account["name"] if account else "",
            "site": account["site"] if account else str(current.get("site") or ""),
            "remote_workflow_id": remote_id,
            "folder_id": folder_id,
            "input_config": input_config,
            "updated_at": now_ms(),
        }
        if "prompt_group" in changes:
            if prompt_group:
                record["prompt_group_id"] = prompt_group["id"]
                record["prompt_group_name"] = prompt_group["name"]
            else:
                record.pop("prompt_group_id", None)
                record.pop("prompt_group_name", None)
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
        source_path = self.workflow_path(workflow_id)
        source_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        record["workflow_path"] = str(source_path)
        self._upsert_workflow_registry(record)
        if "prompt_group" in changes:
            self._write_workflow_prompt_group(workflow_id, prompt_group)
        return self.workflow_record(workflow_id)

    def workflow_references(self, workflow_id: str) -> list[dict[str, str]]:
        """Return active configuration references to a library workflow.

        Task rows are intentionally excluded: each submitted task owns an
        immutable workflow snapshot and therefore does not depend on the
        current library entry.
        """
        workflow_id = str(workflow_id or "").strip()
        record = self.workflow_record(workflow_id)
        references: list[dict[str, str]] = []
        settings = self._read_json_file()
        if str(settings.get("telegram_inbound_workflow_id") or "").strip() == workflow_id:
            references.append({"kind": "telegram_inbound", "key": "telegram_inbound_workflow_id"})
        if str(settings.get("telegram_video_inbound_workflow_id") or "").strip() == workflow_id:
            references.append({"kind": "telegram_video_inbound", "key": "telegram_video_inbound_workflow_id"})
        folder_id = str(record.get("folder_id") or "").strip()
        if folder_id:
            references.append({"kind": "workflow_folder", "key": folder_id})

        def walk(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    child_name = str(child_key or "")
                    lowered = child_name.lower()
                    if (
                        "workflow" in lowered
                        and "remote" not in lowered
                        and (lowered.endswith("_id") or lowered.endswith("_ids") or lowered == "workflowid")
                    ):
                        if isinstance(child_value, list) and workflow_id in {str(item or "").strip() for item in child_value}:
                            references.append({"kind": "active_setting", "key": child_name})
                        elif str(child_value or "").strip() == workflow_id:
                            references.append({"kind": "active_setting", "key": child_name})
                    walk(child_value, child_name)
            elif isinstance(value, list):
                for item in value:
                    walk(item, key)

        walk(settings)
        unique: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in references:
            marker = (item["kind"], item["key"])
            if marker not in seen:
                seen.add(marker)
                unique.append(item)
        return unique

    def _migrate_workflow_references(self, old_id: str, new_id: str) -> list[dict[str, str]]:
        """Move active settings from one library ID to another before deletion."""
        old_id = str(old_id or "").strip()
        new_id = str(new_id or "").strip()
        if not old_id or not new_id or old_id == new_id:
            raise RhCliError("INVALID_WORKFLOW_REPLACEMENT", "替换工作流 ID 无效。")
        self.workflow_record(new_id)
        data = self._read_json_file()
        old_record = self.workflow_record(old_id)
        new_record = self.workflow_record(new_id)
        changed = False
        if str(data.get("telegram_inbound_workflow_id") or "").strip() == old_id:
            data["telegram_inbound_workflow_id"] = new_id
            try:
                data["telegram_inbound_file_input_id"] = str(
                    telegram_inbound_file_input(self.workflow_detail(new_id), "image").get("id") or ""
                )
            except (RhCliError, OSError, ValueError):
                data.pop("telegram_inbound_file_input_id", None)
            changed = True
        if str(data.get("telegram_video_inbound_workflow_id") or "").strip() == old_id:
            data["telegram_video_inbound_workflow_id"] = new_id
            try:
                data["telegram_video_inbound_file_input_id"] = str(
                    telegram_inbound_file_input(self.workflow_detail(new_id), "video").get("id") or ""
                )
            except (RhCliError, OSError, ValueError):
                data.pop("telegram_video_inbound_file_input_id", None)
            changed = True

        def replace(value: Any, key: str = "") -> Any:
            nonlocal changed
            if isinstance(value, dict):
                return {child_key: replace(child_value, str(child_key or "")) for child_key, child_value in value.items()}
            if isinstance(value, list):
                return [replace(item, key) for item in value]
            lowered = key.lower()
            if (
                "workflow" in lowered
                and "remote" not in lowered
                and (lowered.endswith("_id") or lowered.endswith("_ids") or lowered == "workflowid")
                and str(value or "").strip() == old_id
            ):
                changed = True
                return new_id
            return value

        migrated = replace(data)
        if migrated != data:
            changed = True
        if changed:
            self._write_json_file(migrated)
        # A replacement always keeps the old package's folder membership.
        old_folder = str(old_record.get("folder_id") or "").strip()
        new_folder = str(new_record.get("folder_id") or "").strip()
        if old_folder and old_folder != new_folder:
            self.set_workflow_folder(new_id, old_folder)
        return self.workflow_references(old_id)

    def _delete_workflow_files(self, workflow_id: str, *, clear_references: bool = True) -> None:
        record = self.workflow_record(workflow_id)
        path = Path(record["workflow_path"])
        if self._package_layout_enabled():
            package_dir = self._workflow_package_dir(workflow_id)
            try:
                if package_dir.exists():
                    shutil.rmtree(package_dir)
            except OSError as exc:
                raise RhCliError("WORKFLOW_DELETE_FAILED", f"删除工作流包失败：{package_dir}") from exc
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise RhCliError("WORKFLOW_DELETE_FAILED", f"删除工作流失败：{path}") from exc
            self._write_workflow_prompt_group(workflow_id, None)
        entry_path = self._workflow_registry_entry_path(workflow_id)
        try:
            entry_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RhCliError("WORKFLOW_REGISTRY_DELETE_FAILED", f"删除工作流登记文件失败：{entry_path.name}") from exc
        records = [item for item in self._read_workflow_registry() if str(item.get("id") or "") != str(workflow_id)]
        self._write_workflow_registry(records)
        if not clear_references:
            return
        data = self._read_json_file()
        changed = False
        for key in (
            "telegram_inbound_workflow_id",
            "telegram_video_inbound_workflow_id",
        ):
            if str(data.get(key) or "").strip() == str(workflow_id):
                data.pop(key, None)
                changed = True
        if str(data.get("telegram_inbound_workflow_id") or "").strip() == "":
            data.pop("telegram_inbound_file_input_id", None)
            if str(data.get("telegram_inbound_mode") or "fixed").strip().lower() != "folder_random":
                data["telegram_inbound_enabled"] = False
        if str(data.get("telegram_video_inbound_workflow_id") or "").strip() == "":
            data.pop("telegram_video_inbound_file_input_id", None)
            data["telegram_video_inbound_enabled"] = False
        if changed:
            self._write_json_file(data)

    def replace_workflow(
        self,
        old_workflow_id: str,
        filename: str,
        content: str,
        *,
        account_id: str = "",
        remote_workflow_id: str = "",
        source_dir: str = "",
        input_config: dict[str, Any] | None = None,
        input_defaults: list[dict[str, Any]] | None = None,
        prompt_group: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Save a new package, migrate active references, then retire the old one."""
        old_record = self.workflow_record(old_workflow_id)
        effective_input_config = input_config if input_config is not None else (
            old_record.get("input_config") if isinstance(old_record.get("input_config"), dict) else None
        )
        workflow_id, _, _ = self.save_workflow(
            filename,
            content,
            account_id=account_id or str(old_record.get("account_id") or ""),
            remote_workflow_id=remote_workflow_id or str(old_record.get("remote_workflow_id") or ""),
            source_dir=source_dir or str(old_record.get("source_dir") or ""),
            input_config=effective_input_config,
            input_defaults=input_defaults,
            prompt_group=prompt_group,
        )
        try:
            package = self.workflow_detail(workflow_id)
            package_path = Path(str(package["record"].get("workflow_path") or ""))
            prompt_path = self.workflow_prompt_group_path(workflow_id)
            registration_path = self._workflow_registry_entry_path(workflow_id)
            if not package_path.is_file() or not prompt_path.is_file() or not registration_path.is_file():
                raise RhCliError("WORKFLOW_PACKAGE_INCOMPLETE", "新工作流包的注册文件、工作流文件或提示词组文件不完整。")
            remaining = self._migrate_workflow_references(old_workflow_id, workflow_id)
            unresolved = [item for item in remaining if item.get("kind") != "workflow_folder"]
            if unresolved:
                labels = ", ".join(item.get("key", "") for item in unresolved)
                raise RhCliError("WORKFLOW_REFERENCE_MIGRATION_FAILED", f"无法迁移旧工作流的活动引用：{labels}")
            self._delete_workflow_files(old_workflow_id, clear_references=False)
        except Exception:
            # Keep both packages available for recovery if migration/deletion
            # fails.  The old package remains the active one.
            raise
        return self.workflow_detail(workflow_id)

    def delete_workflow(self, workflow_id: str) -> None:
        references = self.workflow_references(workflow_id)
        if references:
            labels = ", ".join(item["key"] for item in references)
            raise RhCliError("WORKFLOW_REFERENCED", f"工作流仍被活动配置引用：{labels}。请先迁移引用或关闭配置。")
        self._delete_workflow_files(workflow_id, clear_references=False)

    @staticmethod
    def task_snapshot_path(task: dict[str, Any]) -> Path:
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
            if not path.is_file() or path.name in {"workflow_api.json", PROMPT_GROUP_SNAPSHOT_FILENAME, TASK_MANIFEST_FILENAME} or path.name.endswith(".json.tmp") or path.name.startswith("."):
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

    @staticmethod
    def local_outputs_match_task_records(
        task: dict[str, Any],
        existing: list[dict[str, Any]],
    ) -> bool:
        """Return whether local files exactly match persisted output records.

        File discovery alone cannot distinguish a complete task from one that
        was interrupted after only the first remote file was downloaded.
        Startup recovery therefore trusts only records already written to the
        task row, and rejects extra files discovered on disk.
        """
        recorded = task.get("outputs")
        if not isinstance(recorded, list) or not recorded or not existing:
            return False

        def signature(item: Any) -> tuple[str, str, str] | None:
            if not isinstance(item, dict):
                return None
            kind = str(item.get("kind") or "file")
            if kind == "file":
                raw_path = str(item.get("path") or "").strip()
                if not raw_path:
                    return None
                return ("file", str(Path(raw_path).expanduser().resolve()), "")
            if kind == "text":
                return (
                    "text",
                    str(item.get("node_id") or ""),
                    str(item.get("text") or ""),
                )
            return (kind, str(item.get("node_id") or ""), str(item.get("name") or ""))

        recorded_signatures = [signature(item) for item in recorded]
        existing_signatures = [signature(item) for item in existing]
        if any(item is None for item in recorded_signatures + existing_signatures):
            return False
        return sorted(recorded_signatures) == sorted(existing_signatures)

    def save_task_workflow_snapshot(self, task: dict[str, Any], workflow: dict[str, Any]) -> Path:
        snapshot_path = self.task_snapshot_path(task)
        if not snapshot_path.parent.name or not snapshot_path.parent.parent:
            raise RhCliError("INVALID_OUTPUT_DIR", "任务输出目录无效，无法保存工作流快照。")
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = snapshot_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(snapshot_path)
        return snapshot_path

    def task_prompt_group_snapshot_path(self, task: dict[str, Any]) -> Path:
        return self.task_output_path(task) / PROMPT_GROUP_SNAPSHOT_FILENAME

    def task_manifest_path(self, task: dict[str, Any]) -> Path:
        return self.task_output_path(task) / TASK_MANIFEST_FILENAME

    def save_task_prompt_group_snapshot(self, task: dict[str, Any], group: dict[str, Any]) -> Path:
        if not isinstance(group, dict) or not isinstance(group.get("items"), list):
            raise RhCliError("INVALID_PROMPT_GROUP", "当前提示词组状态格式无效，无法保存任务快照。")
        snapshot_path = self.task_prompt_group_snapshot_path(task)
        if not snapshot_path.parent.name or not snapshot_path.parent.parent:
            raise RhCliError("INVALID_OUTPUT_DIR", "任务输出目录无效，无法保存提示词组状态快照。")
        document = {
            "version": 1,
            "group": {
                "id": str(group.get("id") or "").strip(),
                "name": str(group.get("name") or "任务提交时组装台").strip() or "任务提交时组装台",
                "updated_at": int(group.get("updated_at") or now_ms()),
                "items": group["items"],
            },
        }
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = snapshot_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(snapshot_path)
        return snapshot_path

    def load_task_prompt_group_snapshot(self, task: dict[str, Any]) -> dict[str, Any] | None:
        snapshot_path = self.task_prompt_group_snapshot_path(task)
        if not snapshot_path.is_file():
            return None
        try:
            document = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        group = document.get("group") if isinstance(document, dict) else None
        if not isinstance(group, dict) or not isinstance(group.get("items"), list):
            return None
        return {
            "id": str(group.get("id") or "").strip(),
            "name": str(group.get("name") or "任务提交时组装台").strip() or "任务提交时组装台",
            "updated_at": int(group.get("updated_at") or 0),
            "items": group["items"],
        }

    def _backfill_task_replay_snapshots(self) -> None:
        """Bind legacy task folders that already contain a prompt-group snapshot."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM tasks").fetchall()

        for row in rows:
            task = self.row_to_task(row)
            task_id = str(task.get("id") or "").strip()
            if not task_id:
                continue
            prompt_group = self.load_task_prompt_group_snapshot(task)
            if prompt_group is None:
                continue

            prompt_group_path = self.task_prompt_group_snapshot_path(task)
            manifest_path = self.task_manifest_path(task)
            changes: dict[str, str] = {}
            if not str(task.get("prompt_group_snapshot_path") or "").strip():
                changes["prompt_group_snapshot_path"] = str(prompt_group_path)

            if not manifest_path.is_file():
                workflow_snapshot_path = self.task_snapshot_path(task)
                if (
                    not str(task.get("workflow_snapshot_path") or "").strip()
                    and workflow_snapshot_path.is_file()
                ):
                    changes["workflow_snapshot_path"] = str(workflow_snapshot_path)
                task_for_manifest = {
                    **task,
                    "prompt_group_snapshot_path": str(prompt_group_path),
                    "workflow_snapshot_path": (
                        str(workflow_snapshot_path)
                        if workflow_snapshot_path.is_file()
                        else str(task.get("workflow_snapshot_path") or "").strip()
                    ),
                }
                self.save_task_manifest_snapshot(task_for_manifest, prompt_group)
            if not str(task.get("manifest_path") or "").strip():
                changes["manifest_path"] = str(manifest_path)

            if changes:
                self.update_task(task_id, **changes)

    def _backfill_task_projects(self) -> None:
        """Infer project metadata for legacy tasks from their existing paths."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM tasks").fetchall()

        for row in rows:
            task = self.row_to_task(row)
            task_id = str(task.get("id") or "").strip()
            if not task_id:
                continue
            if not bool(task.get("project_inference_disabled")):
                inferred = normalize_project(
                    None,
                    output_dir=task.get("output_dir") or "",
                    workflow_path=task.get("workflow_path") or "",
                )
                inferred_fields = {
                    "project_id": inferred["id"],
                    "project_name": inferred["name"],
                    "project_path": inferred["path"],
                }
                changes = {
                    field: inferred_fields[field]
                    for field in ("project_id", "project_name", "project_path")
                    if not str(task.get(field) or "").strip() and inferred_fields[field]
                }
                if changes:
                    self.update_task(task_id, **changes)
                    task = self.task(task_id) or {**task, **changes}
            self._ensure_project_folder(task)
            self._sync_task_manifest_project_metadata(task)

    @staticmethod
    def _clean_project_folder_name(name: str) -> str:
        clean_name = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()
        if not clean_name:
            raise RhCliError("INVALID_PROJECT_FOLDER", "项目名称不能为空。")
        if len(clean_name) > 80:
            raise RhCliError("INVALID_PROJECT_FOLDER", "项目名称不能超过 80 个字符。")
        if any(char in clean_name for char in "/\\"):
            raise RhCliError("INVALID_PROJECT_FOLDER", "项目名称不能包含路径分隔符。")
        if clean_name in {"未归类", "全部成片"}:
            raise RhCliError("INVALID_PROJECT_FOLDER", f"“{clean_name}”是系统项目名称，不能使用。")
        return clean_name

    @staticmethod
    def _project_payload(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(value.get("id") or "").strip(),
            "name": str(value.get("name") or "").strip(),
            "path": str(value.get("path") or "").strip(),
            "created_at": int(value.get("created_at") or 0),
            "updated_at": int(value.get("updated_at") or 0),
        }

    def _ensure_project_folder(self, project: dict[str, Any]) -> None:
        """Register a task-backed project without overwriting a user rename."""
        project_id = str(project.get("project_id") or project.get("id") or "").strip()
        project_name = str(project.get("project_name") or project.get("name") or "").strip()
        project_path = str(project.get("project_path") or project.get("path") or "").strip()
        if not project_id or not project_name:
            return
        timestamp = now_ms()
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO projects (id,name,path,created_at,updated_at) VALUES (?,?,?,?,?)",
                (project_id, project_name, project_path, timestamp, timestamp),
            )
            self._db.commit()

    def _backfill_project_registry(self) -> None:
        """Make every classified historical task visible as a persisted project folder."""
        with self._lock:
            rows = self._db.execute(
                "SELECT project_id,project_name,project_path FROM tasks "
                "WHERE project_id != '' AND project_name != ''"
            ).fetchall()
        for row in rows:
            self._ensure_project_folder(dict(row))

    def telegram_project(self) -> dict[str, str]:
        """Return the persisted project reserved for Telegram inbound tasks."""
        with self._lock:
            row = self._db.execute(
                "SELECT id,name,path FROM projects WHERE name = ? COLLATE NOCASE "
                "ORDER BY updated_at DESC, id LIMIT 1",
                (TELEGRAM_PROJECT_NAME,),
            ).fetchone()
        if row:
            return {
                "id": str(row["id"] or "").strip(),
                "name": str(row["name"] or TELEGRAM_PROJECT_NAME).strip(),
                "path": str(row["path"] or "").strip(),
            }
        return normalize_project({"name": TELEGRAM_PROJECT_NAME}, infer_from_paths=False)

    def _backfill_telegram_projects(self) -> None:
        """Recover every historical Telegram inbound task into the reserved project."""
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET submission_source='telegram' "
                "WHERE LOWER(TRIM(submission_source)) = 'telegram' "
                "OR stage_logs_json LIKE '%已从 Telegram 接收%' "
                "OR input_json LIKE '%telegram-inputs/%'"
            )
            self._db.commit()
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE LOWER(TRIM(submission_source)) = 'telegram'"
            ).fetchall()
        if not rows:
            return

        project = self.telegram_project()
        self._ensure_project_folder(project)
        for row in rows:
            task = self.row_to_task(row)
            task_id = str(task.get("id") or "").strip()
            if not task_id:
                continue
            changes = {
                "project_id": project["id"],
                "project_name": project["name"],
                "project_path": project["path"],
                "project_inference_disabled": 0,
            }
            if any(str(task.get(field) or "") != str(value or "") for field, value in changes.items()):
                self.update_task(task_id, **changes)
                task = self.task(task_id) or {**task, **changes}
            self._sync_task_manifest_project_metadata(task)

    def project_folders(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id,name,path,created_at,updated_at FROM projects ORDER BY updated_at DESC, name COLLATE NOCASE"
            ).fetchall()
            task_rows = self._db.execute(
                "SELECT project_id,COUNT(*) AS task_count FROM tasks WHERE project_id != '' GROUP BY project_id"
            ).fetchall()
        task_counts = {str(row["project_id"] or ""): int(row["task_count"] or 0) for row in task_rows}
        return [
            {**self._project_payload(dict(row)), "task_count": task_counts.get(str(row["id"]), 0)}
            for row in rows
        ]

    def project_folder(self, project_id: str) -> dict[str, Any] | None:
        project_id = str(project_id or "").strip()
        if not project_id:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT id,name,path,created_at,updated_at FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        return self._project_payload(dict(row)) if row else None

    def create_project_folder(self, name: str) -> dict[str, Any]:
        clean_name = self._clean_project_folder_name(name)
        timestamp = now_ms()
        project = {
            "id": f"project_{uuid.uuid4().hex[:12]}",
            "name": clean_name,
            "path": "",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with self._lock:
            duplicate = self._db.execute(
                "SELECT id FROM projects WHERE name = ? COLLATE NOCASE", (clean_name,)
            ).fetchone()
            if duplicate:
                raise RhCliError("PROJECT_FOLDER_EXISTS", f"项目已存在：{clean_name}")
            self._db.execute(
                "INSERT INTO projects (id,name,path,created_at,updated_at) VALUES (?,?,?,?,?)",
                (project["id"], project["name"], project["path"], timestamp, timestamp),
            )
            self._db.commit()
        return project

    def _sync_task_manifest_project_metadata(self, task: dict[str, Any]) -> None:
        """Update only project metadata in an existing task manifest."""
        manifest_path = self.task_manifest_path(task)
        if not manifest_path.is_file():
            return
        expected_project = {
            "id": str(task.get("project_id") or "").strip(),
            "name": str(task.get("project_name") or "").strip(),
            "path": str(task.get("project_path") or "").strip(),
        }
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(manifest, dict) or manifest.get("project") == expected_project:
            return
        manifest["project"] = expected_project
        temporary = manifest_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(manifest_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def rename_project_folder(self, project_id: str, name: str) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        clean_name = self._clean_project_folder_name(name)
        current = self.project_folder(project_id)
        if not current:
            raise RhCliError("PROJECT_FOLDER_NOT_FOUND", f"找不到项目：{project_id}")
        timestamp = now_ms()
        with self._lock:
            duplicate = self._db.execute(
                "SELECT id FROM projects WHERE name = ? COLLATE NOCASE AND id != ?", (clean_name, project_id)
            ).fetchone()
            if duplicate:
                raise RhCliError("PROJECT_FOLDER_EXISTS", f"项目已存在：{clean_name}")
            task_rows = self._db.execute("SELECT * FROM tasks WHERE project_id=?", (project_id,)).fetchall()
            self._db.execute("UPDATE projects SET name=?, updated_at=? WHERE id=?", (clean_name, timestamp, project_id))
            self._db.execute(
                "UPDATE tasks SET project_name=?, updated_at=? WHERE project_id=?",
                (clean_name, timestamp, project_id),
            )
            self._db.commit()
        for row in task_rows:
            task = self.row_to_task(row)
            task["project_name"] = clean_name
            task["updated_at"] = timestamp
            self._sync_task_manifest_project_metadata(task)
        return self.project_folder(project_id) or {**current, "name": clean_name, "updated_at": timestamp}

    def delete_project_folder(self, project_id: str) -> dict[str, Any]:
        project_id = str(project_id or "").strip()
        current = self.project_folder(project_id)
        if not current:
            raise RhCliError("PROJECT_FOLDER_NOT_FOUND", f"找不到项目：{project_id}")
        timestamp = now_ms()
        with self._lock:
            task_rows = self._db.execute("SELECT * FROM tasks WHERE project_id=?", (project_id,)).fetchall()
            self._db.execute("DELETE FROM projects WHERE id=?", (project_id,))
            self._db.execute(
                "UPDATE tasks SET project_id='', project_name='', project_path='', project_inference_disabled=1, updated_at=? "
                "WHERE project_id=?",
                (timestamp, project_id),
            )
            self._db.commit()
        for row in task_rows:
            task = self.row_to_task(row)
            task.update({
                "project_id": "",
                "project_name": "",
                "project_path": "",
                "project_inference_disabled": 1,
                "updated_at": timestamp,
            })
            self._sync_task_manifest_project_metadata(task)
        return {"project": current, "affected_task_count": len(task_rows)}

    def save_task_manifest_snapshot(self, task: dict[str, Any], prompt_group: dict[str, Any]) -> Path:
        """Save the immutable, path-only replay manifest for one submission."""
        manifest_path = self.task_manifest_path(task)
        if not manifest_path.parent.name or not manifest_path.parent.parent:
            raise RhCliError("INVALID_OUTPUT_DIR", "任务输出目录无效，无法保存复现清单。")
        document = {
            "version": 1,
            "kind": "rh-workflow-task",
            "task_id": str(task.get("id") or "").strip(),
            "created_at": int(task.get("created_at") or now_ms()),
            "submission_source": str(task.get("submission_source") or "local").strip() or "local",
            "task_type": str(task.get("task_type") or "workflow").strip().lower() or "workflow",
            "workflow": {
                "name": str(task.get("workflow_name") or "").strip(),
                "registered_workflow_id": str(task.get("registered_workflow_id") or "").strip(),
                "local_workflow_id": str(task.get("local_workflow_id") or "").strip(),
                "remote_workflow_id": str(task.get("remote_workflow_id") or "").strip(),
                "source_path": str(task.get("workflow_path") or "").strip(),
                "snapshot_path": str(task.get("workflow_snapshot_path") or "").strip(),
            },
            "prompt_group": {
                "id": str(prompt_group.get("id") or "").strip(),
                "name": str(prompt_group.get("name") or "").strip(),
                "snapshot_path": str(task.get("prompt_group_snapshot_path") or "").strip(),
            },
            "project": {
                "id": str(task.get("project_id") or "").strip(),
                "name": str(task.get("project_name") or "").strip(),
                "path": str(task.get("project_path") or "").strip(),
            },
            "execution": {
                "instance_type": normalize_instance_type(task.get("instance_type")),
                "output_prefix": normalize_output_prefix(task.get("output_prefix")),
                "account_id": str(task.get("account_id") or "").strip(),
                "input_config": task.get("input_config") if isinstance(task.get("input_config"), dict) else {},
                "bypassed_nodes": task.get("bypassed_nodes") if isinstance(task.get("bypassed_nodes"), list) else [],
                "random_noise": task.get("random_noise") if isinstance(task.get("random_noise"), dict) else {},
                "resolution": task.get("resolution") if isinstance(task.get("resolution"), dict) else {},
                "custom_inputs": task.get("custom_inputs") if isinstance(task.get("custom_inputs"), dict) else {},
            },
            "inputs": {
                "files": task.get("files") if isinstance(task.get("files"), dict) else {},
                "prompts": task.get("prompts") if isinstance(task.get("prompts"), dict) else {},
                "policy": "paths-only",
            },
        }
        temporary = manifest_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(manifest_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return manifest_path

    def task_workflow_path(self, task: dict[str, Any]) -> Path:
        snapshot_path = self.task_snapshot_path(task)
        if snapshot_path.is_file():
            return snapshot_path
        raise RhCliError(
            "WORKFLOW_NOT_FOUND",
            "任务输出目录中的工作流快照不存在，无法加载任务记录。",
            detail={"snapshot_path": str(snapshot_path)},
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
        if not str(task.get("workflow_snapshot_path") or "").strip():
            self.update_task(task_id, workflow_snapshot_path=str(snapshot_path))
            task = self.task(task_id) or task
        derived_workflow_id = self._workflow_local_id_from_path(original_path)
        saved_workflow_id = (
            str(task.get("registered_workflow_id") or "").strip()
            or str(task.get("local_workflow_id") or "").strip()
            or derived_workflow_id
        )
        prompt_group = self.load_task_prompt_group_snapshot(task)
        task_changes: dict[str, str] = {}
        prompt_group_snapshot_path = self.task_prompt_group_snapshot_path(task)
        if prompt_group and not str(task.get("prompt_group_snapshot_path") or "").strip():
            task_changes["prompt_group_snapshot_path"] = str(prompt_group_snapshot_path)
        manifest_path = self.task_manifest_path(task)
        if prompt_group and not str(task.get("manifest_path") or "").strip():
            if not manifest_path.is_file():
                task_for_manifest = {**task, "prompt_group_snapshot_path": str(prompt_group_snapshot_path)}
                self.save_task_manifest_snapshot(task_for_manifest, prompt_group)
            task_changes["manifest_path"] = str(manifest_path)
        if task_changes:
            self.update_task(task_id, **task_changes)
            task = self.task(task_id) or task
        return {
            "workflow_id": saved_workflow_id,
            "filename": task.get("workflow_name") or workflow_path.name,
            "workflow_path": str(workflow_path),
            "workflow": workflow,
            "analysis": configured_workflow_analysis(workflow, task.get("input_config")),
            "input_catalog": workflow_input_catalog(workflow),
            "input_config": task.get("input_config"),
            "prompt_group": prompt_group,
            "prompt_group_snapshot_path": str(self.task_prompt_group_snapshot_path(task)) if prompt_group else "",
            "manifest_path": str(self.task_manifest_path(task)) if manifest_path.is_file() else "",
            "task": task,
        }

    def create_task(self, task: dict[str, Any]) -> None:
        submission_source = "telegram" if str(task.get("submission_source") or "").strip().lower() == "telegram" else "local"
        if submission_source == "telegram":
            telegram_project = self.telegram_project()
            task = {
                **task,
                "submission_source": submission_source,
                "project_id": telegram_project["id"],
                "project_name": telegram_project["name"],
                "project_path": telegram_project["path"],
                "project_inference_disabled": 0,
            }
        fields = {
            "id": task["id"],
            "created_at": task["created_at"],
            "updated_at": task["created_at"],
            "status": str(task.get("initial_status") or "queued").strip().lower() if str(task.get("initial_status") or "queued").strip().lower() in {"queued", "running"} else "queued",
            "progress": str(task.get("initial_progress") or "已加入本地等待队列，等待并发槽位…"),
            "workflow_path": task["workflow_path"],
            "workflow_name": task["workflow_name"],
            "key_id": task.get("key_id"),
            "account_id": str(task.get("account_id") or "").strip(),
            "instance_type": normalize_instance_type(task.get("instance_type")),
            "output_prefix": normalize_output_prefix(task.get("output_prefix")),
            "dispatch_key_name": str(task.get("dispatch_key_name") or "").strip(),
            "dispatch_key_site": str(task.get("dispatch_key_site") or "").strip(),
            "dispatch_key_api_type": str(task.get("dispatch_key_api_type") or "").strip(),
            "submission_source": submission_source,
            "task_type": "toolbox" if str(task.get("task_type") or "").strip().lower() == "toolbox" else "workflow",
            "remote_task_id": None,
            "remote_workflow_id": str(task.get("remote_workflow_id") or "").strip(),
            "registered_workflow_id": str(task.get("registered_workflow_id") or "").strip(),
            "local_workflow_id": str(task.get("local_workflow_id") or "").strip(),
            "input_json": json.dumps(task["files"], ensure_ascii=False),
            "prompt_json": json.dumps(task["prompts"], ensure_ascii=False),
            "custom_json": json.dumps(task.get("custom_inputs") or {}, ensure_ascii=False),
            "input_config_json": json.dumps(task.get("input_config") or {}, ensure_ascii=False),
            "bypass_json": json.dumps(task.get("bypassed_nodes") or [], ensure_ascii=False),
            "random_noise_json": json.dumps(task.get("random_noise") or {}, ensure_ascii=False),
            "resolution_json": json.dumps(task.get("resolution") or {}, ensure_ascii=False),
            "workflow_snapshot_path": str(task.get("workflow_snapshot_path") or ""),
            "prompt_group_snapshot_path": str(task.get("prompt_group_snapshot_path") or ""),
            "manifest_path": str(task.get("manifest_path") or ""),
            "project_id": str(task.get("project_id") or "").strip(),
            "project_name": str(task.get("project_name") or "").strip(),
            "project_path": str(task.get("project_path") or "").strip(),
            "project_inference_disabled": 1 if task.get("project_inference_disabled") else 0,
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
                "INSERT INTO tasks (id,created_at,updated_at,status,progress,workflow_path,workflow_name,project_id,project_name,project_path,project_inference_disabled,key_id,account_id,instance_type,output_prefix,dispatch_key_name,dispatch_key_site,dispatch_key_api_type,submission_source,task_type,remote_task_id,remote_workflow_id,registered_workflow_id,local_workflow_id,"
                "input_json,prompt_json,custom_json,input_config_json,bypass_json,random_noise_json,resolution_json,workflow_snapshot_path,prompt_group_snapshot_path,manifest_path,output_dir,outputs_json,error,error_detail,stage_logs_json,cost_type,cost,duration) "
                "VALUES (:id,:created_at,:updated_at,:status,:progress,:workflow_path,:workflow_name,:project_id,:project_name,:project_path,:project_inference_disabled,:key_id,:account_id,:instance_type,:output_prefix,:dispatch_key_name,:dispatch_key_site,:dispatch_key_api_type,:submission_source,:task_type,:remote_task_id,:remote_workflow_id,:registered_workflow_id,:local_workflow_id,"
                ":input_json,:prompt_json,:custom_json,:input_config_json,:bypass_json,:random_noise_json,:resolution_json,:workflow_snapshot_path,:prompt_group_snapshot_path,:manifest_path,:output_dir,:outputs_json,:error,:error_detail,:stage_logs_json,:cost_type,:cost,:duration)",
                fields,
            )
            self._sync_usage_record_locked(task["id"])
            self._db.commit()
        self._ensure_project_folder(fields)

    def update_task(self, task_id: str, **changes: Any) -> None:
        allowed = {
            "status", "progress", "updated_at", "started_at", "completed_at", "key_id", "account_id", "instance_type", "dispatch_key_name", "dispatch_key_site", "dispatch_key_api_type", "remote_task_id", "remote_workflow_id",
            "outputs_json", "error", "error_detail", "stage_logs_json", "cost_type", "cost", "duration", "output_dir", "workflow_snapshot_path",
            "prompt_group_snapshot_path", "manifest_path", "project_id", "project_name", "project_path", "project_inference_disabled",
        }
        changes = {key: value for key, value in changes.items() if key in allowed}
        if not changes:
            return
        changes["updated_at"] = now_ms()
        assignments = ", ".join(f"{key}=:{key}" for key in changes)
        changes["task_id"] = task_id
        with self._lock:
            self._db.execute(f"UPDATE tasks SET {assignments} WHERE id=:task_id", changes)
            self._sync_usage_record_locked(task_id)
            self._db.commit()

    def set_task_project(self, task_id: str, project: dict[str, Any] | None) -> dict[str, Any]:
        """Change only a task's project classification; never move its media."""
        current = self.task(task_id)
        if not current:
            raise RhCliError("TASK_NOT_FOUND", "找不到这个任务。")
        normalized = normalize_project(project, infer_from_paths=False)
        if normalized["id"] and not normalized["name"]:
            stored = self.project_folder(normalized["id"])
            if not stored:
                raise RhCliError("PROJECT_FOLDER_NOT_FOUND", "找不到目标项目。")
            normalized = {"id": stored["id"], "name": stored["name"], "path": stored["path"]}
        inference_disabled = 0 if normalized["id"] else 1
        self.update_task(
            task_id,
            project_id=normalized["id"],
            project_name=normalized["name"],
            project_path=normalized["path"],
            project_inference_disabled=inference_disabled,
        )
        updated = self.task(task_id) or {**current, **normalized}
        updated["project_inference_disabled"] = inference_disabled
        self._ensure_project_folder(updated)
        self._sync_task_manifest_project_metadata(updated)
        return self.task(task_id) or updated

    def _sync_usage_record_locked(self, task_id: str) -> None:
        row = self._db.execute(
            "SELECT id, created_at, updated_at, account_id, dispatch_key_site, started_at, completed_at, status, workflow_name, cost_type, cost, duration, outputs_json FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if not row:
            return
        try:
            outputs = json.loads(row["outputs_json"] or "[]")
        except (TypeError, ValueError):
            outputs = []
        output_count = len(outputs) if isinstance(outputs, list) else 0
        previous_usage = self._db.execute(
            "SELECT video_seconds FROM usage_records WHERE task_id=?",
            (task_id,),
        ).fetchone()
        video_seconds = _video_seconds_from_outputs(outputs)
        previous_video_seconds = _decimal_value(previous_usage["video_seconds"]) if previous_usage else None
        if video_seconds <= 0 and previous_video_seconds is not None and previous_video_seconds > 0:
            # The ledger outlives task/output cleanup, so never lose a duration
            # that was already measured just because the local file is gone.
            video_seconds = previous_video_seconds
        task = dict(row)
        elapsed_ms = task_elapsed_ms(task)
        site = str(row["dispatch_key_site"] or "").strip()
        if site not in {"cn", "ai"}:
            account = self.get_account(str(row["account_id"] or "").strip())
            site = str(account.get("site") or "").strip() if account else ""
        self._db.execute(
            """
            INSERT INTO usage_records (
              task_id, created_at, updated_at, account_id, site, started_at, completed_at, status,
              workflow_name, cost_type, cost, duration, elapsed_ms, output_count, video_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
              created_at=excluded.created_at,
              updated_at=excluded.updated_at,
              account_id=excluded.account_id,
              site=excluded.site,
              started_at=excluded.started_at,
              completed_at=excluded.completed_at,
              status=excluded.status,
              workflow_name=excluded.workflow_name,
              cost_type=excluded.cost_type,
              cost=excluded.cost,
              duration=excluded.duration,
              elapsed_ms=excluded.elapsed_ms,
              output_count=excluded.output_count,
              video_seconds=excluded.video_seconds
            """,
            (
                str(row["id"]),
                int(row["created_at"] or 0),
                int(row["updated_at"] or 0),
                str(row["account_id"] or "").strip(),
                site,
                row["started_at"],
                row["completed_at"],
                str(row["status"] or ""),
                str(row["workflow_name"] or ""),
                str(row["cost_type"] or ""),
                str(row["cost"] or "") or None,
                str(row["duration"] or "") or None,
                elapsed_ms,
                output_count,
                _format_metric(video_seconds, 3),
            ),
        )

    def _backfill_usage_records(self) -> None:
        with self._lock:
            task_ids = [str(row[0]) for row in self._db.execute("SELECT id FROM tasks").fetchall()]
            for task_id in task_ids:
                self._sync_usage_record_locked(task_id)
            self._db.commit()

    def update_output_rating(self, task_id: str, output_index: int, rating: Any) -> dict[str, Any]:
        try:
            index = int(output_index)
        except (TypeError, ValueError) as exc:
            raise RhCliError("INVALID_OUTPUT_RATING", "产物索引无效。") from exc
        try:
            score = int(rating)
        except (TypeError, ValueError) as exc:
            raise RhCliError("INVALID_OUTPUT_RATING", "评分必须是 0 到 5 星。") from exc
        if score < 0 or score > 5:
            raise RhCliError("INVALID_OUTPUT_RATING", "评分必须是 0 到 5 星。")
        with self._lock:
            row = self._db.execute("SELECT outputs_json FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise RhCliError("TASK_NOT_FOUND", "找不到任务。")
            try:
                outputs = json.loads(row["outputs_json"] or "[]")
            except (TypeError, ValueError) as exc:
                raise RhCliError("OUTPUT_NOT_FOUND", "任务产物记录无效。") from exc
            if not isinstance(outputs, list) or index < 0 or index >= len(outputs) or not isinstance(outputs[index], dict):
                raise RhCliError("OUTPUT_NOT_FOUND", "找不到这个产物。")
            output = outputs[index]
            if score == 0:
                output.pop("rating", None)
            else:
                output["rating"] = score
            self._db.execute(
                "UPDATE tasks SET outputs_json=?, updated_at=? WHERE id=?",
                (json.dumps(outputs, ensure_ascii=False), now_ms(), task_id),
            )
            self._db.commit()
            return dict(output)

    def update_output_tags(self, task_id: str, output_index: int, tags: Any) -> dict[str, Any]:
        try:
            index = int(output_index)
        except (TypeError, ValueError) as exc:
            raise RhCliError("INVALID_OUTPUT_TAGS", "产物索引无效。") from exc
        if not isinstance(tags, list):
            raise RhCliError("INVALID_OUTPUT_TAGS", "产物标签必须是数组。")
        clean_tags: list[str] = []
        for value in tags:
            tag = str(value or "").strip()
            if tag and tag not in clean_tags:
                clean_tags.append(tag)
        with self._lock:
            row = self._db.execute("SELECT outputs_json FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise RhCliError("TASK_NOT_FOUND", "找不到任务。")
            try:
                outputs = json.loads(row["outputs_json"] or "[]")
            except (TypeError, ValueError) as exc:
                raise RhCliError("OUTPUT_NOT_FOUND", "任务产物记录无效。") from exc
            if not isinstance(outputs, list) or index < 0 or index >= len(outputs) or not isinstance(outputs[index], dict):
                raise RhCliError("OUTPUT_NOT_FOUND", "找不到这个产物。")
            output = outputs[index]
            if clean_tags:
                output["tags"] = clean_tags
            else:
                output.pop("tags", None)
            self._db.execute(
                "UPDATE tasks SET outputs_json=?, updated_at=? WHERE id=?",
                (json.dumps(outputs, ensure_ascii=False), now_ms(), task_id),
            )
            self._db.commit()
            return dict(output)

    def telegram_delivery_sent(self, task_id: str, delivery_key: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM telegram_deliveries WHERE task_id=? AND delivery_key=? AND status='sent'",
                (str(task_id), str(delivery_key)),
            ).fetchone()
        return bool(row)

    def claim_telegram_delivery(
        self,
        task_id: str,
        delivery_key: str,
        claim_id: str,
        *,
        lease_ms: int,
        allow_unknown: bool = False,
    ) -> bool:
        """Atomically reserve one task output for one Telegram sender.

        The reservation is backed by SQLite's write lock, so it also works when
        two local Web server processes share the same data directory. A stale
        ``sending`` reservation can be reclaimed after its lease expires; an
        ``unknown`` result is intentionally not reclaimed automatically because
        the original POST may already have created a Telegram message.
        """
        task_value = str(task_id)
        delivery_value = str(delivery_key)
        owner = str(claim_id or "").strip()
        if not owner:
            raise ValueError("Telegram delivery claim requires an owner")
        now = now_ms()
        claim_until = now + max(1_000, int(lease_ms))
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._db.execute(
                    """
                    INSERT INTO telegram_deliveries
                      (task_id, delivery_key, sent_at, status, claimed_by, claim_until, attempts, last_error, updated_at)
                    VALUES (?, ?, 0, 'sending', ?, ?, 1, '', ?)
                    ON CONFLICT(task_id, delivery_key) DO UPDATE SET
                      status='sending',
                      claimed_by=excluded.claimed_by,
                      claim_until=excluded.claim_until,
                      attempts=telegram_deliveries.attempts + 1,
                      last_error='',
                      updated_at=excluded.updated_at
                    WHERE telegram_deliveries.status='retryable'
                       OR (telegram_deliveries.status='unknown' AND ?=1)
                       OR (telegram_deliveries.status='sending' AND telegram_deliveries.claim_until <= ?)
                    """,
                    (task_value, delivery_value, owner, claim_until, now, int(bool(allow_unknown)), now),
                )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return cursor.rowcount == 1

    def finish_telegram_delivery(
        self,
        task_id: str,
        delivery_key: str,
        claim_id: str,
        status: str,
        error: str = "",
    ) -> None:
        """Finish a claim only if this process still owns it."""
        status_value = str(status or "").strip().lower()
        if status_value not in {"sent", "retryable", "unknown"}:
            raise ValueError(f"Invalid Telegram delivery status: {status_value}")
        now = now_ms()
        sent_at = now if status_value == "sent" else 0
        with self._lock:
            self._db.execute(
                """
                UPDATE telegram_deliveries
                SET status=?, sent_at=?, claimed_by='', claim_until=0,
                    last_error=?, updated_at=?
                WHERE task_id=? AND delivery_key=? AND status='sending' AND claimed_by=?
                """,
                (
                    status_value,
                    sent_at,
                    str(error or "")[:500],
                    now,
                    str(task_id),
                    str(delivery_key),
                    str(claim_id),
                ),
            )
            self._db.commit()

    def mark_telegram_delivery_sent(self, task_id: str, delivery_key: str) -> None:
        """Backward-compatible unconditional sent marker for older callers."""
        with self._lock:
            self._db.execute(
                """
                INSERT INTO telegram_deliveries
                  (task_id, delivery_key, sent_at, status, updated_at)
                VALUES (?, ?, ?, 'sent', ?)
                ON CONFLICT(task_id, delivery_key) DO UPDATE SET
                  sent_at=excluded.sent_at, status='sent', claimed_by='',
                  claim_until=0, last_error='', updated_at=excluded.updated_at
                """,
                (str(task_id), str(delivery_key), now_ms(), now_ms()),
            )
            self._db.commit()

    def claim_telegram_inbound_update(self, update_id: int) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO telegram_inbound_updates (update_id, received_at, status) VALUES (?, ?, ?)",
                (int(update_id), now_ms(), "processing"),
            )
            self._db.commit()
        return cursor.rowcount == 1

    def finish_telegram_inbound_update(self, update_id: int, status: str, task_id: str = "", detail: str = "") -> None:
        with self._lock:
            self._db.execute(
                "UPDATE telegram_inbound_updates SET status=?, task_id=?, detail=? WHERE update_id=?",
                (str(status or "")[:40], str(task_id or "")[:120], str(detail or "")[:500], int(update_id)),
            )
            self._db.commit()

    def delete_outputs_by_rating(
        self,
        rating: Any,
        *,
        project_id: str = "",
        output_keys: set[tuple[str, int]] | None = None,
    ) -> dict[str, int]:
        """Delete rated outputs within the selected project, preserving tasks."""
        try:
            score = int(rating)
        except (TypeError, ValueError) as exc:
            raise RhCliError("INVALID_OUTPUT_RATING", "评分必须是 1 到 5 星。") from exc
        if score < 1 or score > 5:
            raise RhCliError("INVALID_OUTPUT_RATING", "评分必须是 1 到 5 星。")

        files_to_delete: set[Path] = set()
        updates: list[tuple[str, int, str]] = []
        deleted_count = 0
        with self._lock:
            query = "SELECT id, output_dir, outputs_json FROM tasks"
            parameters: tuple[str, ...] = ()
            if project_id:
                query += " WHERE project_id=?"
                parameters = ("" if project_id == "__unclassified__" else project_id,)
            rows = self._db.execute(query, parameters).fetchall()
            for row in rows:
                try:
                    outputs = json.loads(row["outputs_json"] or "[]")
                except (TypeError, ValueError) as exc:
                    raise RhCliError("OUTPUT_NOT_FOUND", "任务产物记录无效。") from exc
                if not isinstance(outputs, list):
                    continue
                kept_outputs: list[dict[str, Any]] = []
                removed_from_task = 0
                task_id = str(row["id"] or "")
                task_folder = (Path(str(row["output_dir"] or "")).expanduser() / task_id).resolve()
                for output_index, output in enumerate(outputs):
                    if not isinstance(output, dict):
                        kept_outputs.append(output)
                        continue
                    try:
                        output_rating = int(output.get("rating") or 0)
                    except (TypeError, ValueError):
                        output_rating = 0
                    if output_rating != score or (output_keys is not None and (task_id, output_index) not in output_keys):
                        kept_outputs.append(output)
                        continue
                    if str(output.get("kind") or "file") == "file":
                        raw_path = str(output.get("path") or "").strip()
                        if raw_path:
                            file_path = Path(raw_path).expanduser()
                            if file_path.is_symlink():
                                raise RhCliError("OUTPUT_DELETE_FAILED", "产物文件是符号链接，拒绝删除。")
                            resolved_path = file_path.resolve()
                            if task_folder == resolved_path or task_folder not in resolved_path.parents:
                                raise RhCliError("OUTPUT_DELETE_FAILED", "产物路径不在任务输出目录内，拒绝删除。")
                            files_to_delete.add(resolved_path)
                    removed_from_task += 1
                if removed_from_task:
                    deleted_count += removed_from_task
                    updates.append(
                        (
                            json.dumps(kept_outputs, ensure_ascii=False),
                            now_ms(),
                            task_id,
                        )
                    )

            try:
                for file_path in files_to_delete:
                    if file_path.is_file():
                        file_path.unlink()
                for outputs_json, updated_at, task_id in updates:
                    self._db.execute(
                        "UPDATE tasks SET outputs_json=?, updated_at=? WHERE id=?",
                        (outputs_json, updated_at, task_id),
                    )
                self._db.commit()
            except OSError as exc:
                self._db.rollback()
                raise RhCliError("OUTPUT_DELETE_FAILED", "删除一星产物文件失败。") from exc

        # Keep usage_records.output_count unchanged: the independent ledger
        # records what the task generated, even after library cleanup.
        return {"deleted": deleted_count, "tasks_updated": len(updates)}

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

    def recent_logs(self, limit: int = 500) -> list[dict[str, Any]]:
        """Flatten persisted task stage logs for the local platform log viewer."""
        try:
            requested = int(limit)
        except (TypeError, ValueError):
            requested = 500
        requested = max(1, min(requested, 2000))
        with self._lock:
            rows = self._db.execute(
                "SELECT id, workflow_name, stage_logs_json, updated_at FROM tasks"
            ).fetchall()
        logs: list[dict[str, Any]] = []
        for row in rows:
            task_id = str(row[0] or "")
            workflow_name = canonical_workflow_name(row[1] or "workflow.json")
            try:
                entries = json.loads(row[2] or "[]")
            except (TypeError, ValueError):
                entries = []
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                logs.append(
                    {
                        "at": int(entry.get("at") or row[3] or 0),
                        "level": str(entry.get("level") or "info").lower(),
                        "stage": str(entry.get("stage") or "task"),
                        "message": str(entry.get("message") or ""),
                        "task_id": task_id,
                        "task_name": workflow_name,
                    }
                )
        logs.sort(key=lambda item: int(item.get("at") or 0))
        return logs[-requested:]

    def set_error_detail(self, task_id: str, detail: Any) -> None:
        self.update_task(task_id, error_detail=detail_json(detail))

    @staticmethod
    def row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        task_type = str(task.get("task_type") or "").strip().lower()
        task["task_type"] = task_type if task_type in {"workflow", "toolbox"} else "workflow"
        task["workflow_name"] = public_workflow_name(canonical_workflow_name(task.get("workflow_name") or "workflow.json"))
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

    def dispatchable_tasks(self) -> list[dict[str, Any]]:
        """Return pending tasks in their actual insertion order.

        The normal task listing is newest-first for the dashboard. Dispatching
        needs the opposite order, and SQLite's rowid gives us a stable tie
        breaker when several tasks share the same millisecond timestamp.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE status IN ('queued','recovering') "
                "ORDER BY created_at ASC, rowid ASC"
            ).fetchall()
        return [self.row_to_task(row) for row in rows]

    def queued_task_ids(self) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM tasks WHERE status='queued' ORDER BY created_at ASC, rowid ASC"
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def active_task_count(self, key_id: str = "") -> int:
        query = "SELECT COUNT(*) AS count FROM tasks WHERE status IN ('submitting','running')"
        params: tuple[Any, ...] = ()
        if key_id:
            query += " AND key_id=?"
            params = (key_id,)
        with self._lock:
            row = self._db.execute(query, params).fetchone()
        return int(row["count"] if row else 0)

    def queued_task_count_for_key(self, key_id: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS count FROM tasks "
                "WHERE key_id=? AND status IN ('queued','recovering')",
                (key_id,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def remote_queue_states(self) -> dict[str, dict[str, Any]]:
        """Return persisted remote-queue gate state for every API Key.

        Web and Electron can run separate TaskManager processes against the
        same task database. Keeping this state in SQLite, rather than a
        process-local dictionary, prevents either process from bypassing a
        personal-key predecessor gate created by the other.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT cooldown.key_id,cooldown.retry_after,cooldown.attempts,"
                "cooldown.wait_for_predecessors,cooldown.probe_task_id,"
                "cooldown.updated_at,probe.status AS probe_status "
                "FROM remote_queue_cooldowns AS cooldown "
                "LEFT JOIN tasks AS probe ON probe.id=cooldown.probe_task_id"
            ).fetchall()
        return {
            str(row["key_id"]): {
                "retry_after": int(row["retry_after"] or 0),
                "attempts": int(row["attempts"] or 0),
                "wait_for_predecessors": bool(row["wait_for_predecessors"]),
                "probe_task_id": str(row["probe_task_id"] or ""),
                "probe_active": str(row["probe_status"] or "") == "submitting",
                "updated_at": int(row["updated_at"] or 0),
            }
            for row in rows
            if str(row["key_id"] or "")
        }

    def defer_task_for_remote_queue(
        self,
        task_id: str,
        key_id: str,
        *,
        automatic_dispatch: bool,
    ) -> tuple[int, int]:
        """Requeue a 421 task and atomically close its Key's submit gate.

        A 421 belongs to the remote queue for the selected Key, not just the
        one task that happened to receive it. Every Key type stays in the
        local FIFO queue until its already-submitted tasks have completed;
        this deliberately does not use a time-based submit retry.
        """
        normalized_key_id = str(key_id or "").strip()
        if not normalized_key_id:
            raise RhCliError("INVALID_KEY", "远程队列重试缺少 API Key。")
        timestamp = now_ms()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                task_row = self._db.execute(
                    "SELECT status FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
                if not task_row or task_row["status"] not in {"queued", "submitting", "running"}:
                    self._db.rollback()
                    return 0, 0
                state = self._db.execute(
                    "SELECT attempts FROM remote_queue_cooldowns WHERE key_id=?",
                    (normalized_key_id,),
                ).fetchone()
                attempts = int(state["attempts"] if state else 0) + 1
                predecessor_row = self._db.execute(
                    "SELECT COUNT(*) AS count FROM tasks "
                    "WHERE key_id=? AND id<>? AND status IN ('submitting','running')",
                    (normalized_key_id, task_id),
                ).fetchone()
                predecessor_count = int(predecessor_row["count"] if predecessor_row else 0)
                delay_seconds = 0
                retry_after = 0
                wait_for_predecessors = 1
                if predecessor_count:
                    progress = (
                        "RunningHub API Key 并发已满，已加入本地队列，"
                        f"等待 {predecessor_count} 个前序任务完成后再提交"
                    )
                else:
                    progress = "RunningHub API Key 并发已满，已加入本地队列，等待并发闸门释放后再提交"
                self._db.execute(
                    "INSERT INTO remote_queue_cooldowns "
                    "(key_id,retry_after,attempts,wait_for_predecessors,probe_task_id,updated_at) "
                    "VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(key_id) DO UPDATE SET retry_after=excluded.retry_after, "
                    "attempts=excluded.attempts, "
                    "wait_for_predecessors=excluded.wait_for_predecessors, "
                    "probe_task_id='', updated_at=excluded.updated_at",
                    (normalized_key_id, retry_after, attempts, wait_for_predecessors, "", timestamp),
                )
                updated = self._db.execute(
                    "UPDATE tasks SET status='queued', key_id=?, remote_task_id=NULL, "
                    "started_at=NULL, completed_at=NULL, error='', error_detail='{}', "
                    "progress=?, updated_at=? WHERE id=? AND status IN ('queued','submitting','running')",
                    (
                        None if automatic_dispatch else normalized_key_id,
                        progress,
                        timestamp,
                        task_id,
                    ),
                )
                if updated.rowcount != 1:
                    self._db.rollback()
                    return 0, 0
                self._sync_usage_record_locked(task_id)
                self._db.commit()
                return delay_seconds, attempts
            except Exception:
                self._db.rollback()
                raise

    def clear_remote_queue_probe(self, key_id: str, task_id: str) -> None:
        """Clear a successful or aborted post-gate probe, if this task owns it."""
        normalized_key_id = str(key_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        if not normalized_key_id or not normalized_task_id:
            return
        with self._lock:
            self._db.execute(
                "DELETE FROM remote_queue_cooldowns WHERE key_id=? AND probe_task_id=?",
                (normalized_key_id, normalized_task_id),
            )
            self._db.commit()

    def claim_task_slot(
        self,
        task_id: str,
        key: dict[str, Any],
        capacity: int,
        worker_capacity: int,
        recovery: bool,
    ) -> bool:
        """Atomically claim a key and local worker slot for a pending task."""
        key_id = str(key.get("id") or "")
        if not key_id:
            return False
        now = now_ms()
        progress = f"使用 {key.get('name') or key_id} {'恢复轮询' if recovery else '提交'}中…"
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                task_row = self._db.execute(
                    "SELECT status FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
                if not task_row or task_row["status"] not in {"queued", "recovering"}:
                    self._db.rollback()
                    return False
                if not recovery:
                    queue_state = self._db.execute(
                        "SELECT retry_after,attempts,wait_for_predecessors,probe_task_id "
                        "FROM remote_queue_cooldowns WHERE key_id=?",
                        (key_id,),
                    ).fetchone()
                    if queue_state:
                        wait_for_predecessors = (
                            bool(queue_state["wait_for_predecessors"])
                            or int(queue_state["attempts"] or 0) > 0
                        )
                        probe_task_id = str(queue_state["probe_task_id"] or "")
                        if wait_for_predecessors:
                            predecessor_row = self._db.execute(
                                "SELECT COUNT(*) AS count FROM tasks "
                                "WHERE key_id=? AND id<>? AND status IN ('submitting','running')",
                                (key_id, task_id),
                            ).fetchone()
                            if int(predecessor_row["count"] if predecessor_row else 0) > 0:
                                self._db.rollback()
                                return False
                        if probe_task_id:
                            probe_row = self._db.execute(
                                "SELECT status FROM tasks WHERE id=?", (probe_task_id,)
                            ).fetchone()
                            if probe_row and probe_row["status"] == "submitting":
                                self._db.rollback()
                                return False
                            self._db.execute(
                                "UPDATE remote_queue_cooldowns SET probe_task_id='', updated_at=? WHERE key_id=?",
                                (now, key_id),
                            )
                        if wait_for_predecessors:
                            self._db.execute(
                                "UPDATE remote_queue_cooldowns SET probe_task_id=?, updated_at=? WHERE key_id=?",
                                (task_id, now, key_id),
                            )
                key_row = self._db.execute(
                    "SELECT COUNT(*) AS count FROM tasks "
                    "WHERE key_id=? AND status IN ('submitting','running')",
                    (key_id,),
                ).fetchone()
                if int(key_row["count"] if key_row else 0) >= int(capacity):
                    self._db.rollback()
                    return False
                worker_row = self._db.execute(
                    "SELECT COUNT(*) AS count FROM tasks WHERE status IN ('submitting','running')"
                ).fetchone()
                if int(worker_row["count"] if worker_row else 0) >= int(worker_capacity):
                    self._db.rollback()
                    return False
                updated = self._db.execute(
                    "UPDATE tasks SET status='submitting', key_id=?, "
                    "dispatch_key_name=?, dispatch_key_site=?, dispatch_key_api_type=?, "
                    "started_at=?, progress=?, updated_at=? "
                    "WHERE id=? AND status IN ('queued','recovering')",
                    (
                        key_id,
                        str(key.get("name") or ""),
                        str(key.get("site") or ""),
                        str(key.get("api_type") or ""),
                        now,
                        progress,
                        now,
                        task_id,
                    ),
                )
                if updated.rowcount != 1:
                    self._db.rollback()
                    return False
                self._sync_usage_record_locked(task_id)
                self._db.commit()
                return True
            except Exception:
                self._db.rollback()
                raise

    def usage_records(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT task_id, created_at, updated_at, account_id, site, started_at, completed_at, status, workflow_name, cost_type, cost, duration, elapsed_ms, output_count, video_seconds "
                "FROM usage_records ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

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
        self._executor = ThreadPoolExecutor(max_workers=LOCAL_WORKER_CAPACITY, thread_name_prefix="rh-web")
        self._telegram_notifier = TelegramNotifier(store)
        self._telegram_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rh-telegram")
        self._telegram_upload_lock = threading.Lock()
        self._telegram_uploading: set[tuple[str, int]] = set()
        self._recover_tasks_on_startup()
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="rh-web-dispatcher", daemon=True)
        self._dispatcher.start()
        self._telegram_inbound = threading.Thread(target=self._telegram_inbound_loop, name="rh-telegram-inbound", daemon=True)
        self._telegram_inbound.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._dispatcher.join(timeout=1.5)
        self._telegram_inbound.join(timeout=1.5)
        self._executor.shutdown(wait=False, cancel_futures=False)
        # Telegram workers use the SQLite store. Wait for the active delivery
        # to finish before server_close() closes the database connection; queued
        # deliveries can be cancelled because the process is shutting down.
        self._telegram_executor.shutdown(wait=True, cancel_futures=True)

    def test_telegram_connection(self) -> dict[str, Any]:
        return self._telegram_notifier.test_connection()

    def _telegram_switchable_workflows(self) -> list[dict[str, str]]:
        """Return workflows that can accept one required Telegram image input."""
        return self.store.telegram_inbound_workflows()

    def _telegram_switchable_folders(self) -> list[dict[str, Any]]:
        """Return folders containing at least one currently usable inbound workflow."""
        folders = []
        for folder in self.store.workflow_folders():
            folder_id = str(folder.get("id") or "").strip()
            if not folder_id:
                continue
            workflow_count = len(self.store.telegram_inbound_workflows(folder_id))
            if workflow_count:
                folders.append({**folder, "workflow_count": workflow_count})
        return sorted(folders, key=lambda item: (str(item.get("name") or ""), str(item.get("id") or "")))

    def telegram_inbound_workflows(self) -> list[dict[str, Any]]:
        """Expose valid inbound choices to the local settings page."""
        return self.store.telegram_inbound_workflows()

    def telegram_video_inbound_workflows(self) -> list[dict[str, Any]]:
        """Expose valid single-video inbound choices to the local settings page."""
        return self.store.telegram_video_inbound_workflows()

    def _telegram_project(self) -> dict[str, str]:
        resolver = getattr(self.store, "telegram_project", None)
        project = resolver() if callable(resolver) else None
        if isinstance(project, dict) and str(project.get("name") or "").strip():
            return normalize_project(project, infer_from_paths=False)
        return normalize_project({"name": TELEGRAM_PROJECT_NAME}, infer_from_paths=False)

    def _select_telegram_inbound_workflow(self, settings: dict[str, Any]) -> dict[str, Any]:
        mode = str(settings.get("inbound_mode") or "fixed").strip().lower()
        if mode == "folder_random":
            folder_id = str(settings.get("inbound_folder_id") or "").strip()
            candidates = self.store.telegram_inbound_workflows(folder_id)
            if not candidates:
                raise RhCliError(
                    "INVALID_TELEGRAM_INBOUND_FOLDER",
                    "Telegram 图片入站文件夹中没有可用的工作流，请检查文件夹内容。",
                )
            # This method is called from _handle_telegram_update for every
            # incoming image. Only the folder is persisted in settings; the
            # workflow choice must be freshly randomized for each task.
            return random.choice(candidates)

        workflow_id = str(settings.get("inbound_workflow_id") or "").strip()
        candidate = next(
            (item for item in self.store.telegram_inbound_workflows() if str(item.get("id") or "") == workflow_id),
            None,
        )
        if candidate is None:
            raise RhCliError(
                "INVALID_TELEGRAM_INBOUND_WORKFLOW",
                "固定 Telegram 入站工作流当前不可用，请在设置中重新选择。",
            )
        return candidate

    def _select_telegram_video_inbound_workflow(self, settings: dict[str, Any]) -> dict[str, Any]:
        workflow_id = str(settings.get("video_inbound_workflow_id") or "").strip()
        candidate = next(
            (item for item in self.store.telegram_video_inbound_workflows() if str(item.get("id") or "") == workflow_id),
            None,
        )
        if candidate is None:
            raise RhCliError(
                "INVALID_TELEGRAM_INBOUND_WORKFLOW",
                "固定 Telegram 视频入站工作流当前不可用，请在设置中重新选择。",
            )
        return candidate

    @staticmethod
    def _telegram_switch_menu(
        workflows: list[dict[str, str]],
        current_workflow_id: str = "",
        *,
        folders: list[dict[str, Any]] | None = None,
        current_mode: str = "fixed",
        current_folder_id: str = "",
    ) -> tuple[str, dict[str, Any] | None]:
        current_id = str(current_workflow_id or "").strip()
        mode = str(current_mode or "fixed").strip().lower()
        folder_id = str(current_folder_id or "").strip()
        current = next((item for item in workflows if item.get("id") == current_id), None)
        current_folder = next((item for item in folders or [] if str(item.get("id") or "") == folder_id), None)
        if mode == "folder_random":
            current_label = (
                f"文件夹随机 · {current_folder.get('name') or folder_id}"
                if current_folder else "文件夹随机（当前不可用）"
            )
        else:
            current_label = str(current.get("name") or "") if current else "未选择"
        if not workflows:
            return "当前没有可用的单输入图片工作流。请先在工作流页面配置一个入站工作流。", None
        rows = []
        rows.append([{
            "text": ("✓ " if mode == "folder_random" else "") + (
                "文件夹随机" if folders else "文件夹随机（暂无可用文件夹）"
            ),
            "callback_data": "rh_switch:folder_random",
        }])
        for item in workflows:
            label = (
                "✓ " if mode == "fixed" and item.get("id") == current_id else ""
            ) + str(item.get("name") or item.get("id") or "工作流")
            account_name = str(item.get("account_name") or "").strip()
            if account_name:
                label += f" · {account_name}"
            rows.append([{
                "text": label[:64],
                "callback_data": f"rh_switch:{item.get('id')}",
            }])
        rows.append([{"text": "取消", "callback_data": "rh_switch:cancel"}])
        return f"请选择 Telegram 入站方式：\n当前：{current_label}", {"inline_keyboard": rows}

    @staticmethod
    def _telegram_switch_folder_menu(
        folders: list[dict[str, Any]], current_folder_id: str = "",
    ) -> tuple[str, dict[str, Any] | None]:
        current_id = str(current_folder_id or "").strip()
        current = next((item for item in folders if str(item.get("id") or "") == current_id), None)
        current_label = str(current.get("name") or "") if current else "未选择"
        if not folders:
            return (
                "当前没有可用的随机文件夹。请先在工作流页面建立文件夹，并放入可用的图片入站工作流。",
                {"inline_keyboard": [[
                    {"text": "← 返回固定工作流", "callback_data": "rh_switch:back"},
                    {"text": "取消", "callback_data": "rh_switch:cancel"},
                ]]},
            )
        rows = []
        for folder in folders:
            folder_id = str(folder.get("id") or "").strip()
            label = ("✓ " if folder_id == current_id else "") + str(folder.get("name") or folder_id or "文件夹")
            label += f" · {int(folder.get('workflow_count') or 0)} 个可用工作流"
            rows.append([{
                "text": label[:64],
                "callback_data": f"rh_switch_folder:{folder_id}",
            }])
        rows.append([{"text": "← 返回固定工作流", "callback_data": "rh_switch:back"}])
        rows.append([{"text": "取消", "callback_data": "rh_switch:cancel"}])
        return f"请选择 Telegram 入站随机文件夹：\n当前：{current_label}", {"inline_keyboard": rows}

    def _send_telegram_switch_menu(
        self, settings: dict[str, Any], chat_id: str, message_id: int | None = None,
    ) -> None:
        workflows = self._telegram_switchable_workflows()
        text, reply_markup = self._telegram_switch_menu(
            workflows,
            settings.get("inbound_workflow_id"),
            folders=self._telegram_switchable_folders(),
            current_mode=str(settings.get("inbound_mode") or "fixed"),
            current_folder_id=settings.get("inbound_folder_id"),
        )
        if message_id is not None:
            self._telegram_notifier.edit_message_text(chat_id, message_id, text, reply_markup)
        else:
            self._telegram_notifier.send_message(text, chat_id, reply_markup)

    def _send_telegram_switch_folder_menu(
        self, settings: dict[str, Any], chat_id: str, message_id: int,
    ) -> None:
        folders = self._telegram_switchable_folders()
        text, reply_markup = self._telegram_switch_folder_menu(
            folders, settings.get("inbound_folder_id")
        )
        self._telegram_notifier.edit_message_text(chat_id, message_id, text, reply_markup)

    def _handle_telegram_callback(
        self, update: dict[str, Any], settings: dict[str, Any], message: dict[str, Any], chat_id: str,
    ) -> str:
        callback = update.get("callback_query") if isinstance(update, dict) else None
        if not isinstance(callback, dict):
            return ""
        callback_id = str(callback.get("id") or "").strip()
        data = str(callback.get("data") or "").strip()
        message_id = message.get("message_id")
        if data == "rh_switch:cancel":
            try:
                if callback_id:
                    self._telegram_notifier.answer_callback_query(callback_id)
                if message_id is not None:
                    self._telegram_notifier.delete_message(chat_id, int(message_id))
            except (TelegramDeliveryError, TypeError, ValueError):
                pass
            return ""
        if data == "rh_switch:folder_random":
            folders = self._telegram_switchable_folders()
            if not folders:
                if callback_id:
                    self._telegram_notifier.answer_callback_query(
                        callback_id, "当前没有可用的随机文件夹，请先配置文件夹。", True
                    )
                return ""
            try:
                if callback_id:
                    self._telegram_notifier.answer_callback_query(callback_id)
                if message_id is not None:
                    self._send_telegram_switch_folder_menu(settings, chat_id, int(message_id))
            except (TelegramDeliveryError, TypeError, ValueError):
                pass
            return ""
        if data == "rh_switch:back":
            try:
                if callback_id:
                    self._telegram_notifier.answer_callback_query(callback_id)
                if message_id is not None:
                    self._send_telegram_switch_menu(settings, chat_id, int(message_id))
            except (TelegramDeliveryError, TypeError, ValueError):
                pass
            return ""
        if data.startswith("rh_switch_folder:"):
            folder_id = data.split(":", 1)[1].strip()
            available_folders = {str(item.get("id") or ""): item for item in self._telegram_switchable_folders()}
            if folder_id not in available_folders:
                if callback_id:
                    self._telegram_notifier.answer_callback_query(
                        callback_id, "该随机文件夹当前不可用，请重新发送 /switch。", True
                    )
                return ""
            try:
                self.store.set_telegram_inbound_settings("", True, mode="folder_random", folder_id=folder_id)
            except RhCliError as exc:
                if callback_id:
                    self._telegram_notifier.answer_callback_query(callback_id, exc.message[:200], True)
                return ""
            if callback_id:
                try:
                    self._telegram_notifier.answer_callback_query(callback_id)
                except TelegramDeliveryError:
                    pass
            try:
                if message_id is not None:
                    self._telegram_notifier.delete_message(chat_id, int(message_id))
                self._telegram_notifier.send_message(
                    f"已切换到文件夹随机：{available_folders[folder_id].get('name') or folder_id}", chat_id
                )
            except (TelegramDeliveryError, TypeError, ValueError):
                pass
            return ""
        if not data.startswith("rh_switch:"):
            if callback_id:
                self._telegram_notifier.answer_callback_query(callback_id, "不支持的操作。", True)
            return ""
        workflow_id = data.split(":", 1)[1].strip()
        if not workflow_id:
            if callback_id:
                self._telegram_notifier.answer_callback_query(callback_id, "请选择一个工作流或随机文件夹。", True)
            return ""
        available = {item["id"]: item for item in self._telegram_switchable_workflows()}
        if workflow_id not in available:
            if callback_id:
                self._telegram_notifier.answer_callback_query(callback_id, "该工作流当前不可用，请重新发送 /switch。", True)
            return ""
        try:
            self.store.set_telegram_inbound_settings(workflow_id, True)
        except RhCliError as exc:
            if callback_id:
                self._telegram_notifier.answer_callback_query(callback_id, exc.message[:200], True)
            return ""
        if callback_id:
            try:
                # A blank callback answer clears Telegram's loading state
                # without showing the native bottom toast.
                self._telegram_notifier.answer_callback_query(callback_id)
            except TelegramDeliveryError:
                pass
        try:
            if message_id is not None:
                self._telegram_notifier.delete_message(chat_id, int(message_id))
            self._telegram_notifier.send_message(f"已切换到：{available[workflow_id]['name']}", chat_id)
        except (TelegramDeliveryError, TypeError, ValueError):
            # The workflow has already been switched; a stale menu should not
            # make Telegram retry the callback as if the switch had failed.
            pass
        return ""

    @staticmethod
    def _telegram_command(update: dict[str, Any]) -> str:
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            return ""
        text = str(message.get("text") or "").strip()
        if not text.startswith("/"):
            return ""
        return text.split(None, 1)[0].split("@", 1)[0].lower()

    def _queue_telegram_task_failure(self, task_id: str, message: str) -> None:
        """Notify the configured chat when a Telegram-originated task fails."""
        task = self.store.task(task_id)
        if not task or str(task.get("submission_source") or "").strip().lower() != "telegram":
            return
        workflow_name = str(task.get("workflow_name") or "工作流").strip() or "工作流"
        safe_message = str(redact_detail(message) or "未知错误").strip()[:2000]
        notification = (
            "❌ Telegram 入站任务处理失败\n"
            f"工作流：{workflow_name}\n"
            f"任务 ID：{task_id}\n"
            f"原因：{safe_message}"
        )
        try:
            self._telegram_executor.submit(
                self._send_telegram_task_failure,
                task_id,
                notification,
            )
        except (RuntimeError, AttributeError):
            # A shutdown race must not change the already-recorded task result.
            self._log_stage(task_id, "telegram", "任务失败，但 Telegram 通知线程已关闭", level="warning")

    def _send_telegram_task_failure(self, task_id: str, notification: str) -> None:
        try:
            self._telegram_notifier.send_message(notification)
        except Exception as exc:  # pragma: no cover - background safety net
            self._log_stage(task_id, "telegram", f"任务失败通知发送失败：{exc}", level="warning")

    def _queue_telegram_delivery(
        self,
        task_id: str,
        outputs: list[dict[str, Any]],
        *,
        force: bool = False,
        output_indices: list[int] | None = None,
    ) -> None:
        self._telegram_executor.submit(self._deliver_telegram_outputs, task_id, list(outputs), force, output_indices)

    def _deliver_telegram_outputs(
        self,
        task_id: str,
        outputs: list[dict[str, Any]],
        force: bool = False,
        output_indices: list[int] | None = None,
    ) -> None:
        try:
            result = self._telegram_notifier.notify_task(task_id, outputs, force=force, output_indices=output_indices)
            if result.get("status") == "disabled":
                return
            if result.get("status") == "not_configured":
                self._log_stage(task_id, "telegram", "Telegram 未配置，跳过成片推送")
                return
            failed = int(result.get("failed") or 0)
            self._log_stage(
                task_id,
                "telegram",
                f"Telegram 推送完成：成功 {int(result.get('sent') or 0)} 个，失败 {failed} 个",
                level="warning" if failed else "info",
            )
        except Exception as exc:  # pragma: no cover - background safety net
            self._log_stage(task_id, "telegram", f"Telegram 推送异常：{exc}", level="warning")

    def upload_task_to_telegram(self, task_id: str, output_index: Any) -> dict[str, Any]:
        task = self.store.task(task_id)
        if not task:
            raise RhCliError("TASK_NOT_FOUND", "找不到任务。")
        settings = self._telegram_notifier.settings()
        if not settings.get("configured"):
            raise RhCliError("TELEGRAM_NOT_CONFIGURED", "请先配置 Telegram Bot Token 和 Chat ID。")
        try:
            index = int(output_index)
        except (TypeError, ValueError) as exc:
            raise RhCliError("TELEGRAM_OUTPUT_NOT_FOUND", "请选择一个有效的成片。") from exc
        outputs = task.get("outputs") or []
        if not isinstance(outputs, list) or index < 0 or index >= len(outputs) or not isinstance(outputs[index], dict):
            raise RhCliError("TELEGRAM_OUTPUT_NOT_FOUND", "找不到要上传的成片。")
        output = outputs[index]
        output_name = str(output.get("name") or "成片").strip() or "成片"
        upload_key = (str(task_id), index)
        with self._telegram_upload_lock:
            if upload_key in self._telegram_uploading:
                raise RhCliError("TELEGRAM_UPLOAD_IN_PROGRESS", "该成片正在上传，请稍候。")
            self._telegram_uploading.add(upload_key)
        try:
            future = self._telegram_executor.submit(
                self._telegram_notifier.notify_task,
                task_id,
                [output],
                force=True,
                output_indices=[index],
            )
            result = future.result()
            failed = int(result.get("failed") or 0)
            if result.get("status") in {"disabled", "not_configured"} or failed:
                self._log_stage(task_id, "telegram", f"Telegram 上传失败：{output_name}", level="warning")
                raise RhCliError("TELEGRAM_DELIVERY_FAILED", f"「{output_name}」上传到 Telegram 失败，请重试。")
            self._log_stage(task_id, "telegram", f"Telegram 上传完成：{output_name}")
            return {"status": "sent", "message": f"「{output_name}」已上传到 Telegram。"}
        except Exception as exc:
            if isinstance(exc, RhCliError) and exc.code == "TELEGRAM_DELIVERY_FAILED":
                raise
            self._log_stage(task_id, "telegram", f"Telegram 上传失败：{output_name}", level="warning")
            raise
        finally:
            with self._telegram_upload_lock:
                self._telegram_uploading.discard(upload_key)

    def _telegram_inbound_loop(self) -> None:
        """Poll the configured private chat and turn supported inputs into normal tasks."""
        offset: int | None = None
        while not self._stop.is_set():
            settings = self._telegram_notifier.settings()
            if not settings.get("configured"):
                self._stop.wait(2)
                continue
            try:
                updates = self._telegram_notifier.poll_updates(offset)
                for update in updates:
                    if self._stop.is_set():
                        break
                    update_id = update.get("update_id")
                    try:
                        update_id = int(update_id)
                    except (TypeError, ValueError):
                        continue
                    offset = max(offset or update_id, update_id + 1)
                    if not self.store.claim_telegram_inbound_update(update_id):
                        continue
                    current_settings = self._telegram_notifier.settings()
                    if not current_settings.get("configured"):
                        self.store.finish_telegram_inbound_update(update_id, "ignored", detail="Telegram 入站已关闭或未配置")
                        continue
                    task_id = ""
                    status = "ignored"
                    detail = ""
                    try:
                        task_id = self._handle_telegram_update(update, current_settings) or ""
                        status = "submitted" if task_id else "ignored"
                    except Exception as exc:  # pragma: no cover - background safety net
                        status = "failed"
                        detail = exc.message if isinstance(exc, RhCliError) else str(exc)
                    finally:
                        self.store.finish_telegram_inbound_update(update_id, status, task_id, detail)
            except TelegramDeliveryError:
                self._stop.wait(5)

    def _handle_telegram_video_update(
        self, update: dict[str, Any], settings: dict[str, Any], video_url: str,
    ) -> str:
        platform_labels = {"douyin": "抖音", "bilibili": "Bilibili", "x": "X"}
        platform_label = platform_labels.get(social_video_platform(video_url), "视频")
        try:
            selected_workflow = self._select_telegram_video_inbound_workflow(settings)
            workflow_id = str(selected_workflow.get("id") or "").strip()
            detail = self.store.workflow_detail(workflow_id)
            record = detail["record"]
            configured_input_id = str(settings.get("video_inbound_file_input_id") or "").strip()
            input_id = str(telegram_video_inbound_file_input(detail).get("id") or "").strip()
            if configured_input_id and configured_input_id != input_id:
                raise RhCliError("INVALID_TELEGRAM_INBOUND_WORKFLOW", "入站工作流的视频输入配置已变化，请重新设置。")
            if not input_id:
                raise RhCliError("INVALID_TELEGRAM_INBOUND_WORKFLOW", "没有找到入站视频输入节点。")
            target_path = download_social_video(video_url, DATA_ROOT)
            workflow_data = json.loads(json.dumps(detail["workflow"], ensure_ascii=False))
            input_duration = _apply_telegram_video_duration(workflow_data, Path(target_path))
            prompt_group = detail.get("prompt_group")
            if not isinstance(prompt_group, dict):
                prompt_group = {
                    "id": f"telegram-video-{workflow_id}",
                    "name": f"Telegram 视频入站 · {str(record.get('name') or workflow_id).strip()}",
                    "updated_at": now_ms(),
                    "items": [],
                }
            task = self.submit_task(
                workflow_id=workflow_id,
                files={input_id: str(target_path)},
                prompts={},
                key_id=None,
                output_dir=None,
                remote_workflow_id=str(record.get("remote_workflow_id") or "").strip(),
                workflow_name=str(record.get("name") or "").strip(),
                workflow_account_id=str(record.get("account_id") or "").strip(),
                workflow_input_config=record.get("input_config"),
                custom_inputs={},
                random_noise={},
                resolution={},
                workflow_data=workflow_data,
                prompt_group=prompt_group,
                force_workflow_account=True,
                submission_source="telegram",
                project=self._telegram_project(),
            )
        except Exception as exc:
            message = exc.message if isinstance(exc, RhCliError) else "Telegram 视频任务提交失败，请查看本机任务日志。"
            try:
                self._telegram_notifier.send_message(f"{platform_label}视频链接任务未提交：{message}")
            except TelegramDeliveryError:
                pass
            raise
        task_id = str(task.get("id") or "")
        duration_suffix = f"，节点 14 时长已设为 {input_duration:.3f} 秒" if input_duration is not None else ""
        self._log_stage(task_id, "telegram", f"已从 Telegram 接收{platform_label}视频链接并提交工作流{duration_suffix}")
        try:
            self._telegram_notifier.send_message(
                f"已收到{platform_label}视频链接，已下载并排队{duration_suffix}：{task.get('workflow_name') or record.get('name') or workflow_id}\n任务 ID：{task_id}"
            )
        except TelegramDeliveryError:
            self._log_stage(task_id, "telegram", "任务已提交，但 Telegram 回执发送失败", level="warning")
        return task_id

    def _handle_telegram_update(self, update: dict[str, Any], settings: dict[str, Any]) -> str:
        callback = update.get("callback_query") if isinstance(update, dict) else None
        message = callback.get("message") if isinstance(callback, dict) else update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            return ""
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = str(chat.get("id") or "").strip()
        if chat_id not in TelegramNotifier.parse_chat_ids(settings.get("chat_id")):
            return ""
        if isinstance(callback, dict):
            return self._handle_telegram_callback(update, settings, message, chat_id)
        if self._telegram_command(update) == "/switch":
            self._send_telegram_switch_menu(settings, chat_id)
            return ""
        if settings.get("video_inbound_enabled"):
            video_url = extract_social_video_url(self._telegram_notifier.message_text(update))
            if video_url:
                if not str(settings.get("video_inbound_workflow_id") or "").strip():
                    return ""
                return self._handle_telegram_video_update(update, settings, video_url)
        if not settings.get("inbound_enabled"):
            return ""
        mode = str(settings.get("inbound_mode") or "fixed").strip().lower()
        if mode == "folder_random" and not str(settings.get("inbound_folder_id") or "").strip():
            return ""
        if mode != "folder_random" and not str(settings.get("inbound_workflow_id") or "").strip():
            return ""
        reference = self._telegram_notifier.image_file_reference(update)
        if not reference:
            return ""
        try:
            selected_workflow = self._select_telegram_inbound_workflow(settings)
            workflow_id = str(selected_workflow.get("id") or "").strip()
            detail = self.store.workflow_detail(workflow_id)
            record = detail["record"]
            configured_input_id = str(settings.get("inbound_file_input_id") or "").strip()
            input_id = str(telegram_inbound_file_input(detail).get("id") or "").strip()
            if mode != "folder_random" and configured_input_id and configured_input_id != input_id:
                raise RhCliError("INVALID_TELEGRAM_INBOUND_WORKFLOW", "入站工作流的图片输入配置已变化，请重新设置。")
            if not input_id:
                raise RhCliError("INVALID_TELEGRAM_INBOUND_WORKFLOW", "没有找到入站图片输入节点。")
            target_path = self._telegram_notifier.download_image(
                int(update.get("update_id") or 0), reference, DATA_ROOT / "telegram-inputs"
            )
            prompt_group = detail.get("prompt_group")
            if not isinstance(prompt_group, dict):
                prompt_group = {
                    "id": f"telegram-{workflow_id}",
                    "name": f"Telegram 入站 · {str(record.get('name') or workflow_id).strip()}",
                    "updated_at": now_ms(),
                    "items": [],
                }
            task = self.submit_task(
                workflow_id=workflow_id,
                files={input_id: str(target_path)},
                prompts={},
                key_id=None,
                output_dir=None,
                remote_workflow_id=str(record.get("remote_workflow_id") or "").strip(),
                workflow_name=str(record.get("name") or "").strip(),
                workflow_account_id=str(record.get("account_id") or "").strip(),
                workflow_input_config=record.get("input_config"),
                custom_inputs={},
                random_noise={},
                resolution={},
                prompt_group=prompt_group,
                force_workflow_account=True,
                submission_source="telegram",
                project=self._telegram_project(),
            )
        except Exception as exc:
            message = exc.message if isinstance(exc, RhCliError) else "Telegram 图片任务提交失败，请查看本机任务日志。"
            try:
                self._telegram_notifier.send_message(f"图片任务未提交：{message}")
            except TelegramDeliveryError:
                pass
            raise
        task_id = str(task.get("id") or "")
        self._log_stage(task_id, "telegram", "已从 Telegram 接收图片并提交工作流")
        try:
            self._telegram_notifier.send_message(
                f"已收到图片，工作流已排队：{task.get('workflow_name') or record.get('name') or workflow_id}\n任务 ID：{task_id}"
            )
        except TelegramDeliveryError:
            self._log_stage(task_id, "telegram", "任务已提交，但 Telegram 回执发送失败", level="warning")
        return task_id

    def _recover_tasks_on_startup(self) -> None:
        """Resolve files left by a previous process before resuming remote polling."""
        for task in self.store.tasks():
            if task.get("status") != "interrupted":
                continue
            existing = self.store.existing_task_outputs(task)
            if self.store.local_outputs_match_task_records(task, existing):
                self.store.update_task(
                    task["id"],
                    status="completed",
                    progress=f"已从本地产物恢复 · 保存 {len([item for item in existing if item.get('kind') == 'file'])} 个产物",
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
                    progress=(
                        "发现的本地产物记录不完整，准备恢复远程轮询…"
                        if existing
                        else "未发现本地产物，准备恢复远程轮询…"
                    ),
                )
                self._log_stage(
                    task["id"],
                    "recovery",
                    (
                        f"发现的本地产物记录不完整，准备恢复轮询 taskId：{remote_task_id}"
                        if existing
                        else f"未发现本地产物，准备恢复轮询 taskId：{remote_task_id}"
                    ),
                )
            else:
                self.store.update_task(
                    task["id"],
                    progress=(
                        "应用重启，发现的本地产物记录不完整且没有远程 taskId，无法确认任务完成"
                        if existing
                        else "应用重启，未发现本地产物且没有远程 taskId，无法恢复"
                    ),
                )
                self._log_stage(
                    task["id"],
                    "recovery",
                    (
                        "发现的本地产物记录不完整且没有远程 taskId，保留为已中断"
                        if existing
                        else "未发现本地产物，也没有远程 taskId，保留为已中断"
                    ),
                    level="warning",
                )

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
            task["key_name"] = snapshot_name or (key.get("name") if key else ("自动调度" if not task.get("key_id") else "已删除 API Key"))
            task["key_site"] = snapshot_site or (key.get("site") if key else "")
            task["key_api_type"] = snapshot_api_type or (key.get("api_type") if key else "")
            task["dispatch_credential_recorded"] = bool(snapshot_name)
            task["remote_task_id"] = task.get("remote_task_id") or ""
            task["remote_workflow_id"] = task.get("remote_workflow_id") or ""
            stored_instance_type = str(task.get("instance_type") or "default").strip().lower()
            task["instance_type"] = stored_instance_type if stored_instance_type in INSTANCE_TYPES else "default"
            task["elapsed_ms"] = task_elapsed_ms(task)
            result.append(task)
        positions = {task_id: index for index, task_id in enumerate(self.store.queued_task_ids(), start=1)}
        for task in result:
            task["queue_position"] = positions.get(task["id"], 0)
        return result

    def _active_count_for_key(self, key_id: str, local_active: dict[str, int] | None = None) -> int:
        if local_active is None:
            with self._lock:
                local_count = self._active_by_key.get(key_id, 0)
        else:
            local_count = local_active.get(key_id, 0)
        # The SQLite count also includes work claimed by another process. The
        # max avoids double-counting this manager's in-memory mirror.
        return max(local_count, self.store.active_task_count(key_id))

    def public_keys(self, account_id: str = "") -> list[dict[str, Any]]:
        with self._lock:
            active = dict(self._active_by_key)
        records = self.store.keys()
        account_id = str(account_id or "").strip()
        if account_id == GENERAL_ACCOUNT_ID:
            return [
                public_key({**record, "active_tasks": self._active_count_for_key(record["id"], active)})
                for record in records
            ]
        elif account_id:
            records = [item for item in records if str(item.get("account_id") or "").strip() == account_id]
        return [
            public_key({**record, "active_tasks": self._active_count_for_key(record["id"], active)})
            for record in records
        ]

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
            "name": str(name or "").strip() or f"{site.upper()} API Key {len(records) + 1}",
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

    def _apply_key_health(self, record: dict[str, Any], data: dict[str, Any], *, checked: bool) -> None:
        """Synchronize balance refreshes with the scheduler's key health."""
        self._update_balance(record, data)
        if not any(field in data for field in ("remainMoney", "remainCoins")):
            if checked:
                record["checked_at"] = now_ms()
            return
        api_type = str(data.get("apiType") or record.get("api_type") or "")
        has_balance = self._has_balance(data)
        record.update(
            {
                "status": "ready" if has_balance else "no_balance",
                "status_message": "检测成功" if has_balance else "API Key 有效但余额为 0",
                "api_type": api_type,
                "capacity": key_capacity(api_type, self.store.personal_capacity()),
            }
        )
        if checked:
            record["checked_at"] = now_ms()

    def check_key(self, key_id: str) -> dict[str, Any]:
        records = self.store.keys()
        record = next((item for item in records if item["id"] == key_id), None)
        if not record:
            raise RhCliError("KEY_NOT_FOUND", "找不到这个 API Key。")
        try:
            data = self._fetch_account_data(record)
            self._apply_key_health(record, data, checked=True)
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
        self._apply_key_health(record, data, checked=False)
        self.store.save_keys(records)
        self._wake.set()
        return public_key(record)

    def refresh_balances(self) -> dict[str, Any]:
        """Refresh every saved API Key so aggregate dashboard balances are current."""
        records = self.store.keys()
        refreshed: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for record in records:
            try:
                data = self._fetch_account_data(record)
                self._apply_key_health(record, data, checked=False)
                refreshed.append(public_key(record))
            except Exception as exc:
                errors.append(
                    {
                        "id": str(record.get("id") or ""),
                        "name": str(record.get("name") or "未命名 API Key"),
                        "message": exc.message if isinstance(exc, RhCliError) else str(exc),
                    }
                )
        if records:
            self.store.save_keys(records)
        self._wake.set()
        return {
            "refreshed": len(refreshed),
            "failed": len(errors),
            "keys": refreshed,
            "errors": errors,
        }

    @staticmethod
    def _has_balance(data: dict[str, Any]) -> bool:
        for field in ("remainMoney", "remainCoins"):
            try:
                if float(data.get(field) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    @staticmethod
    def _runtime_key_failure_code(exc: RhCliError) -> str:
        if exc.code in {"AUTH_FAILED", "INSUFFICIENT_BALANCE"}:
            return exc.code
        if exc.code != "SUBMIT_FAILED":
            return ""
        detail = json.dumps(redact_detail(exc.detail), ensure_ascii=False).lower()
        message = f"{exc.message} {detail}"
        if any(token in message for token in ("401", "403", "unauthorized", "invalid api key", "api key 无效")):
            return "AUTH_FAILED"
        if any(token in message for token in ("insufficient", "no balance", "余额不足", "credit")):
            return "INSUFFICIENT_BALANCE"
        return ""

    def _mark_runtime_key_failure(self, key: dict[str, Any], failure_code: str) -> None:
        records = self.store.keys()
        record = next((item for item in records if item.get("id") == key.get("id")), None)
        if not record:
            return
        if failure_code == "INSUFFICIENT_BALANCE":
            record.update(
                {
                    "status": "no_balance",
                    "status_message": "任务运行时检测到余额不足，请充值或更换 API Key",
                }
            )
        else:
            record.update(
                {
                    "status": "error",
                    "status_message": "任务运行时验证 API Key 失败，请重新检测",
                }
            )
        record["checked_at"] = now_ms()
        self.store.save_keys(records)
        self._wake.set()

    def remove_key(self, key_id: str) -> None:
        records = self.store.keys()
        if any(item["id"] == key_id for item in records) and self._active_count_for_key(key_id) > 0:
            raise RhCliError("KEY_IN_USE", "这个 API Key 正在执行任务，暂时不能删除。")
        if any(item["id"] == key_id for item in records) and self.store.queued_task_count_for_key(key_id) > 0:
            raise RhCliError("KEY_IN_QUEUE", "这个 API Key 仍被等待队列中的任务使用，请先取消这些任务。")
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
        prompt_group: dict[str, Any] | None = None,
        force_workflow_account: bool = False,
        submission_source: str = "local",
        project: dict[str, Any] | None = None,
        output_prefix: str | None = None,
    ) -> dict[str, Any]:
        inline_workflow = workflow_data is not None
        if workflow_data is not None:
            if not isinstance(workflow_data, dict):
                raise RhCliError("INVALID_WORKFLOW", "当前工作流必须是 API 格式节点字典。")
            workflow = workflow_data
            requested_workflow_id = str(workflow_id or "").strip()
            workflow_id = (
                requested_workflow_id
                if re.fullmatch(r"wf_[A-Za-z0-9]+", requested_workflow_id)
                else f"wf_{uuid.uuid4().hex[:12]}"
            )
            # This is only a logical source path used while resolving legacy
            # relative defaults. The task itself is persisted to its own
            # output snapshot below; no file is created here.
            workflow_path = self.store._workflow_file_path(workflow_id)
        else:
            workflow_path = self.store.workflow_path(workflow_id)
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        instance_type = normalize_instance_type(instance_type)
        output_prefix = normalize_output_prefix(output_prefix)
        current_account_id = self.store.current_account_id()
        selected_key = self.store.get_key(key_id) if key_id else None
        if key_id and not selected_key:
            raise RhCliError("KEY_NOT_FOUND", "指定的 API Key 不存在。")
        selected_key_account_id = str(selected_key.get("account_id") or "").strip() if selected_key else ""
        account_restricted = not force_workflow_account and current_account_id not in {"", GENERAL_ACCOUNT_ID}
        if key_id and account_restricted and selected_key_account_id != current_account_id:
            raise RhCliError("KEY_ACCOUNT_MISMATCH", "所选 API Key 不属于当前使用账号，请切换账号或重新选择 API Key。")
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
        registered_workflow_id = str(library_record.get("id") or "").strip() if library_record else ""
        saved_input_config = workflow_input_config
        if saved_input_config is None and library_record:
            saved_input_config = library_record.get("input_config")
        # A raw task-page import can carry a temporary input configuration from
        # the task page editor; library records continue to use their saved
        # configuration when the request does not provide one.
        normalized_input_config = normalize_workflow_input_config(workflow, saved_input_config) if (library_record or workflow_data is not None) else None
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
        normalized_prompt_group = self.store._normalise_workflow_prompt_group(prompt_group)
        if normalized_prompt_group is None:
            normalized_prompt_group = {
                "id": f"task-group-{uuid.uuid4().hex[:12]}",
                "name": "任务提交时组装台（空）",
                "updated_at": now_ms(),
                "items": [],
            }
        submission_source = str(submission_source or "local").strip().lower()
        if submission_source not in {"local", "telegram"}:
            submission_source = "local"
        if submission_source == "telegram":
            project = self._telegram_project()
        files = apply_default_file_inputs(workflow, analysis, files, bypassed_set, workflow_path)
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
        root = Path(output_dir or self.store.output_dir()).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        normalized_project = normalize_project(
            project, output_dir=root, workflow_path=workflow_path, infer_from_paths=project is None,
        )
        if normalized_project["id"] and not normalized_project["name"]:
            stored_project = self.store.project_folder(normalized_project["id"])
            if not stored_project:
                raise RhCliError("PROJECT_FOLDER_NOT_FOUND", "找不到所选项目，请重新选择。")
            normalized_project = {key: stored_project[key] for key in ("id", "name", "path")}
        task_workflow_name = str(workflow_name or "").strip()
        if not task_workflow_name and library_record:
            task_workflow_name = str(library_record.get("name") or "").strip()
        if not task_workflow_name and inline_workflow:
            task_workflow_name = "workflow_api.json"
        if not task_workflow_name:
            task_workflow_name = workflow_name_from_path(workflow_path, workflow_id)
        task = {
            "id": task_id,
            "created_at": now_ms(),
            "workflow_path": str(workflow_path),
            "workflow_name": canonical_workflow_name(task_workflow_name or "workflow_api.json"),
            "task_type": "workflow",
            "remote_workflow_id": remote_id,
            "registered_workflow_id": registered_workflow_id,
            "local_workflow_id": workflow_id if inline_workflow else "",
            "submission_source": submission_source,
            "files": files,
            "prompts": prompts,
            "custom_inputs": normalized_custom_inputs,
            "input_config": normalized_input_config or {"mode": "auto", "items": []},
            "bypassed_nodes": normalized_bypassed_nodes,
            "random_noise": normalized_random_noise,
            "resolution": normalized_resolution,
            "key_id": key_id or None,
            "account_id": bound_workflow_account_id if force_workflow_account else current_account_id or selected_key_account_id or bound_workflow_account_id,
            "instance_type": instance_type,
            "output_prefix": output_prefix,
            "project_id": normalized_project["id"],
            "project_name": normalized_project["name"],
            "project_path": normalized_project["path"],
            "project_inference_disabled": project is not None and not normalized_project["id"],
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
        if inline_workflow:
            task["workflow_path"] = str(snapshot_path)
        prompt_group_snapshot_path = self.store.save_task_prompt_group_snapshot(task, normalized_prompt_group)
        task["prompt_group_snapshot_path"] = str(prompt_group_snapshot_path)
        manifest_path = self.store.save_task_manifest_snapshot(task, normalized_prompt_group)
        task["manifest_path"] = str(manifest_path)
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
            current = self.store.task(task_id) or task
            event = self._cancel_events.get(task_id)
            if current["status"] in {"queued", "recovering"}:
                self.store.update_task(task_id, status="cancelled", progress="已取消")
            elif event:
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
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id or Path(normalized_task_id).name != normalized_task_id or normalized_task_id in {".", ".."}:
            raise RhCliError("INVALID_TASK_ID", "任务 ID 无效，拒绝删除任务目录。")
        output_root = Path(str(task.get("output_dir") or "")).expanduser().resolve()
        task_folder = output_root / normalized_task_id
        if task_folder == output_root or task_folder.parent != output_root:
            raise RhCliError("INVALID_OUTPUT_DIR", "任务输出目录无效，拒绝删除任务目录。")
        if task_folder.is_symlink():
            raise RhCliError("TASK_DELETE_FAILED", "任务输出目录是符号链接，拒绝删除。")
        if task_folder.exists() and not task_folder.is_dir():
            raise RhCliError("TASK_DELETE_FAILED", "任务输出路径不是文件夹，拒绝删除。")
        try:
            if task_folder.is_dir():
                shutil.rmtree(task_folder)
        except OSError as exc:
            raise RhCliError("TASK_DELETE_FAILED", f"删除任务产物失败：{task_folder}") from exc
        self.store.delete_task(task_id)

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            self._dispatch_once()
            self._wake.wait(0.35)
            self._wake.clear()

    def _dispatch_once(self) -> None:
        keys = self.store.keys()
        records = {item["id"]: item for item in keys}
        remote_queue_states = self.store.remote_queue_states()
        for task in self.store.dispatchable_tasks():
            if task["id"] in self._claimed:
                continue
            recovery = task["status"] == "recovering"
            automatic_dispatch = not bool(task.get("key_id"))
            scoped_keys = self._keys_for_task(task, keys)
            record = self._select_key(task, scoped_keys, records, remote_queue_states)
            if not record:
                wait_message = self._queue_wait_message(task, scoped_keys, records, remote_queue_states)
                if task.get("progress") != wait_message:
                    self.store.update_task(task["id"], progress=wait_message)
                continue
            with self._lock:
                if task["id"] in self._claimed:
                    continue
                # Re-read the state after selecting a key. This closes the
                # cancellation window between the queue scan and the claim.
                latest = self.store.task(task["id"])
                if not latest or latest["status"] not in {"queued", "recovering"}:
                    continue
                current_record = next(
                    (item for item in self.store.keys() if item.get("id") == record.get("id")),
                    None,
                )
                if not current_record or current_record.get("status") != "ready":
                    continue
                record = current_record
                if self.store.active_task_count() >= LOCAL_WORKER_CAPACITY:
                    break
                capacity = int(record.get("capacity") or DEFAULT_PERSONAL_CAPACITY)
                if not self.store.claim_task_slot(
                    task["id"],
                    record,
                    capacity=capacity,
                    worker_capacity=LOCAL_WORKER_CAPACITY,
                    recovery=recovery,
                ):
                    continue
                self._claimed.add(task["id"])
                self._active_by_key[record["id"]] = self._active_by_key.get(record["id"], 0) + 1
                event = threading.Event()
                self._cancel_events[task["id"]] = event
            self._log_stage(task["id"], "dispatch", f"已选择 {record['name']}，开始{'恢复轮询' if recovery else '执行'}")
            self._executor.submit(self._run_task, task["id"], record, event, recovery, automatic_dispatch)

    @staticmethod
    def _keys_for_task(task: dict[str, Any], keys: list[dict[str, Any]]) -> list[dict[str, Any]]:
        account_id = str(task.get("account_id") or "").strip()
        if account_id == GENERAL_ACCOUNT_ID:
            return keys
        if not account_id:
            return keys
        return [item for item in keys if str(item.get("account_id") or "").strip() == account_id]

    def _remote_queue_state_blocks_dispatch(
        self,
        key: dict[str, Any],
        state: dict[str, Any] | None,
    ) -> bool:
        if not state:
            return False
        if state.get("probe_active"):
            return True
        if state.get("wait_for_predecessors") or int(state.get("attempts") or 0) > 0:
            return self._active_count_for_key(str(key.get("id") or "")) > 0
        return False

    def _remote_queue_wait_label(
        self,
        key: dict[str, Any],
        state: dict[str, Any] | None,
    ) -> str:
        if not state:
            return ""
        if state.get("probe_active"):
            return "正在提交闸门任务"
        if state.get("wait_for_predecessors") or int(state.get("attempts") or 0) > 0:
            active = self._active_count_for_key(str(key.get("id") or ""))
            if active:
                return f"等待 {active} 个前序任务完成"
        return ""

    def _automatic_candidates(
        self,
        keys: list[dict[str, Any]],
        remote_queue_states: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Return ready Keys with capacity, honoring the configured tier policy."""
        states = remote_queue_states if remote_queue_states is not None else self.store.remote_queue_states()
        with self._lock:
            available = [
                item
                for item in keys
                if item.get("status") == "ready"
                and self._active_count_for_key(item["id"]) < int(item.get("capacity") or DEFAULT_PERSONAL_CAPACITY)
                and not self._remote_queue_state_blocks_dispatch(
                    item,
                    states.get(str(item.get("id") or "")),
                )
            ]
        strategy = self.store.api_key_strategy()
        personal = [item for item in available if not is_shared_api_key_type(item.get("api_type"))]
        shared = [item for item in available if is_shared_api_key_type(item.get("api_type"))]
        if strategy == "personal_only":
            return personal
        if strategy == "shared_only":
            return shared
        return personal or shared

    def _queue_wait_message(
        self,
        task: dict[str, Any],
        keys: list[dict[str, Any]],
        records: dict[str, dict[str, Any]],
        remote_queue_states: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        states = remote_queue_states if remote_queue_states is not None else self.store.remote_queue_states()
        if task.get("key_id"):
            record = records.get(task["key_id"])
            if record and record.get("status") == "ready":
                remote_queue_label = self._remote_queue_wait_label(
                    record,
                    states.get(str(record["id"])),
                )
                if remote_queue_label:
                    return f"本地等待队列 · RunningHub 远程队列繁忙，{record['name']} {remote_queue_label}"
                active = self._active_count_for_key(record["id"])
                capacity = int(record.get("capacity") or DEFAULT_PERSONAL_CAPACITY)
                return f"本地等待队列 · {record['name']} 并发已满（{active}/{capacity}）"
            return "本地等待队列 · 等待指定 API Key 可用"
        candidates = self._automatic_candidates(keys, states)
        if candidates:
            capacities = ", ".join(
                f"{item['name']} {self._active_count_for_key(item['id'])}/{int(item.get('capacity') or DEFAULT_PERSONAL_CAPACITY)}"
                for item in candidates
            )
            return f"本地等待队列 · 等待并发槽位（{capacities}）"
        remote_queue_labels = [
            f"{item.get('name') or item.get('id')} {self._remote_queue_wait_label(item, states.get(str(item.get('id') or '')))}"
            for item in keys
            if item.get("status") == "ready"
            and self._remote_queue_wait_label(item, states.get(str(item.get("id") or "")))
        ]
        if remote_queue_labels:
            return "本地等待队列 · RunningHub 远程队列繁忙（" + "，".join(remote_queue_labels) + "）"
        strategy = self.store.api_key_strategy()
        labels = {
            "personal_only": "个人 API Key",
            "shared_only": "共享/企业 API Key",
            "personal_then_shared": "个人或共享/企业 API Key",
        }
        return f"本地等待队列 · 等待可用的{labels.get(strategy, 'API Key')}"

    def _select_key(
        self,
        task: dict[str, Any],
        keys: list[dict[str, Any]],
        records: dict[str, dict[str, Any]],
        remote_queue_states: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        states = remote_queue_states if remote_queue_states is not None else self.store.remote_queue_states()
        with self._lock:
            if task.get("key_id"):
                candidate = records.get(task["key_id"])
                if not candidate or candidate.get("status") != "ready":
                    return None
                account_id = str(task.get("account_id") or "").strip()
                if account_id and str(candidate.get("account_id") or "").strip() != account_id:
                    return None
                if self._remote_queue_state_blocks_dispatch(
                    candidate,
                    states.get(str(candidate["id"])),
                ):
                    return None
                if self._active_count_for_key(candidate["id"]) >= int(candidate.get("capacity") or DEFAULT_PERSONAL_CAPACITY):
                    return None
                return candidate
            candidates = self._automatic_candidates(keys, states)
            if not candidates:
                return None
            return min(
                candidates,
                key=lambda item: (self._active_count_for_key(item["id"]), item.get("created_at", 0)),
            )

    @staticmethod
    def _is_remote_805(exc: RhCliError) -> bool:
        """Recognize a RunningHub task failure response with code 805."""
        detail = exc.detail
        candidates = [detail]
        if isinstance(detail, dict):
            candidates.append(detail.get("detail"))
        return any(
            isinstance(item, dict) and str(item.get("code") or "").strip() == "805"
            for item in candidates
        )

    @staticmethod
    def _instance_type_after_805(instance_type: str, retry_count: int) -> str | None:
        """Return the one allowed 805 retry machine, or None when exhausted."""
        if retry_count >= 1:
            return None
        normalized = normalize_instance_type(instance_type)
        if normalized in {"default", "plus"}:
            return "plus"
        return None

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
            saved = self._download_outputs(client, outputs, task_output_dir, task.get("output_prefix"))
            self._log_stage(task_id, "download", f"恢复后保存 {len(saved)} 个产物")
        cost_type, cost, duration = self._task_cost(outputs)
        self.store.update_task(
            task_id,
            status="completed",
            progress=f"已完成 · 保存 {len(saved)} 个产物（重启后恢复）",
            completed_at=now_ms(),
            outputs_json=json.dumps(saved, ensure_ascii=False),
            cost_type=cost_type,
            cost=cost,
            duration=str(duration) if duration is not None else None,
        )
        self._log_stage(task_id, "complete", f"重启后恢复完成，共保存 {len(saved)} 个产物")
        self._queue_telegram_delivery(task_id, saved)

    def _run_task(
        self,
        task_id: str,
        key: dict[str, Any],
        cancel_event: threading.Event,
        recovery: bool = False,
        automatic_dispatch: bool = False,
    ) -> None:
        requeued = False
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
            retry_805_count = 0
            submit_instance_type = normalize_instance_type(task.get("instance_type"))

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
                while True:
                    self._log_stage(
                        task_id,
                        "submit",
                        f"正在提交完整 API 工作流（workflowId：{remote_id_value}，机型：{submit_instance_type}）",
                    )
                    try:
                        remote_id = _submit(
                            client,
                            key["api_key"],
                            remote_id_value,
                            json.dumps(workflow, ensure_ascii=False),
                            instance_type=submit_instance_type,
                            create_url=site_create,
                            add_metadata=True,
                            requeue_on_queue_full=True,
                        )
                        self.store.clear_remote_queue_probe(key["id"], task_id)
                        self.store.update_task(
                            task_id,
                            remote_task_id=remote_id,
                            status="running",
                            progress=f"已提交 · {remote_id}（{submit_instance_type}）",
                        )
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
                    except RhCliError as exc:
                        next_instance_type = self._instance_type_after_805(
                            submit_instance_type,
                            retry_805_count,
                        ) if self._is_remote_805(exc) else None
                        if next_instance_type is None:
                            raise
                        retry_805_count += 1
                        previous_instance_type = submit_instance_type
                        submit_instance_type = next_instance_type
                        self.store.update_task(
                            task_id,
                            instance_type=submit_instance_type,
                            status="submitting",
                            remote_task_id=None,
                            progress=(
                                "任务执行返回 805，"
                                f"{previous_instance_type} 机型失败，准备使用 {submit_instance_type} 机型重试（1/1）"
                            ),
                        )
                        self._log_stage(
                            task_id,
                            "retry",
                            f"任务执行返回 805，{previous_instance_type} 机型失败，改用 {submit_instance_type} 机型重试（1/1）",
                            level="warning",
                            detail=exc.detail,
                        )
                        continue
                    self._log_stage(task_id, "download", f"开始保存 {len(outputs)} 个远程产物")
                    saved = self._download_outputs(client, outputs, task_output_dir, task.get("output_prefix"))
                    self._log_stage(task_id, "download", f"保存 {len(saved)} 个产物")
                    break

            cost_type, cost, duration = self._task_cost(outputs)
            self.store.update_task(
                task_id,
                status="completed",
                progress=f"已完成 · 保存 {len(saved)} 个产物",
                completed_at=now_ms(),
                outputs_json=json.dumps(saved, ensure_ascii=False),
                cost_type=cost_type,
                cost=cost,
                duration=str(duration) if duration is not None else None,
            )
            self._log_stage(task_id, "complete", f"任务完成，共保存 {len(saved)} 个产物")
            self._queue_telegram_delivery(task_id, saved)
        except RhCliError as exc:
            if exc.code == "REMOTE_QUEUE_FULL":
                if cancel_event.is_set():
                    self.store.update_task(task_id, status="cancelled", progress="已取消", completed_at=now_ms())
                    self._log_stage(task_id, "cancelled", "任务在远程队列繁忙时被取消", level="warning")
                    return
                _, attempts = self.store.defer_task_for_remote_queue(
                    task_id,
                    key["id"],
                    automatic_dispatch=automatic_dispatch,
                )
                requeued = bool(attempts)
                if requeued:
                    self._log_stage(
                        task_id,
                        "queue",
                        "API Key 并发已满，已加入本地队列，等待前序任务完成后再提交",
                        level="warning",
                    )
                else:
                    self.store.update_task(task_id, status="cancelled", progress="任务已取消", completed_at=now_ms())
                return
            runtime_key_failure = self._runtime_key_failure_code(exc)
            if runtime_key_failure:
                self._mark_runtime_key_failure(key, runtime_key_failure)
            status = "cancelled" if exc.code == "TASK_CANCELLED" else "failed"
            error_detail = {"code": exc.code, "message": exc.message}
            if exc.detail is not None:
                error_detail["detail"] = exc.detail
            self.store.set_error_detail(task_id, error_detail)
            self.store.update_task(task_id, status=status, progress=exc.message, error=redact_detail(exc.message), completed_at=now_ms())
            self._log_stage(task_id, "cancelled" if status == "cancelled" else "failed", exc.message, level="warning" if status == "cancelled" else "error", detail=exc.detail)
            if status == "failed":
                self._queue_telegram_task_failure(task_id, exc.message)
        except Exception as exc:  # pragma: no cover - final safety net for background jobs
            error_detail = {"type": type(exc).__name__, "message": str(exc)}
            self.store.set_error_detail(task_id, error_detail)
            self.store.update_task(task_id, status="failed", progress="任务失败", error=redact_detail(str(exc)), completed_at=now_ms())
            self._log_stage(task_id, "failed", str(exc), level="error")
            self._queue_telegram_task_failure(task_id, str(exc))
        finally:
            with self._lock:
                self._claimed.discard(task_id)
                self._cancel_events.pop(task_id, None)
                key_id = key["id"]
                self._active_by_key[key_id] = max(0, self._active_by_key.get(key_id, 1) - 1)
            if not requeued:
                self.store.clear_remote_queue_probe(key["id"], task_id)
            self._wake.set()

    @staticmethod
    def _download_outputs(
        client: RhHttpClient,
        outputs: list[dict[str, Any]],
        folder: Path,
        output_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        saved: list[dict[str, Any]] = []
        file_index = 0
        prefix = normalize_output_prefix(output_prefix)
        for item in outputs:
            url = _output_file_url(item)
            if not url:
                text = _output_text(item)
                if text is not None:
                    saved.append({"kind": "text", "text": text, "node_id": str(item.get("nodeId", ""))})
                continue
            file_index += 1
            extension = _normalise_output_ext(item.get("fileType"))
            filename = f"{prefix or 'output'}_{file_index}.{extension}"
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


def public_state(
    store: LocalStore,
    manager: TaskManager,
    *,
    scope: str = "full",
) -> dict[str, Any]:
    """Return only the state needed by the requesting page.

    The complete snapshot remains the backwards-compatible default. Page
    callers can skip assembling large, unrelated collections, especially
    task history and Telegram workflow candidates.
    """
    scope = str(scope or "full").strip().lower()
    if scope not in {"full", "submit", "workflows", "prompt", "toolbox", "outputs", "settings"}:
        scope = "full"
    current_account_id = store.current_account_id()
    current_account = store.get_account(current_account_id) if current_account_id else None
    result: dict[str, Any] = {
        "settings": {
            "output_dir": store.output_dir(),
            "douyin_cookie_path": store.douyin_cookie_path(),
            "personal_capacity": store.personal_capacity(),
            "api_key_strategy": store.api_key_strategy(),
            "pose_media_import_type": store.pose_media_import_type(),
            "current_account_id": current_account_id,
            "current_mode": "general" if current_account_id == GENERAL_ACCOUNT_ID else "account",
            "data_dir": str(DATA_ROOT),
            "native_file_picker": native_file_picker_available(),
            "aliyun_translation": store.aliyun_translation_settings(),
            "aliyun_vision": store.aliyun_vision_settings(),
            "telegram": store.telegram_settings(),
        },
    }
    if scope in {"full", "submit", "workflows", "settings"}:
        result["current_account"] = public_account(current_account) if current_account else None
        result["accounts"] = [public_account(item) for item in store.accounts()]
    if scope in {"full", "submit", "settings"}:
        result["keys"] = manager.public_keys(current_account_id)
    if scope in {"full", "submit"}:
        result["tasks"] = manager.public_tasks()
        result["projects"] = store.project_folders()
    if scope in {"full", "settings"}:
        result["telegram_inbound_workflows"] = manager.telegram_inbound_workflows()
        result["telegram_video_inbound_workflows"] = manager.telegram_video_inbound_workflows()
    return result


def _decimal_value(value: Any) -> float | None:
    try:
        parsed = float(str(value or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in {float("inf"), float("-inf")} else None


def _is_video_output(output: dict[str, Any]) -> bool:
    if str(output.get("kind") or "file").strip().lower() != "file":
        return False
    mime = str(output.get("mime") or "").strip().lower()
    if mime.startswith("video/"):
        return True
    for value in (output.get("file_type"), output.get("name"), output.get("path")):
        if str(value or "").strip().lower().endswith(tuple(VIDEO_OUTPUT_SUFFIXES)):
            return True
    return False


def _probe_video_duration(path: Path) -> float:
    """Read a local video's duration without making media parsing a hard dependency."""
    if not path.is_file() or shutil.which("ffprobe") is None:
        return 0.0
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=duration:format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0
    for line in result.stdout.splitlines():
        duration = _decimal_value(line)
        if duration is not None and duration > 0:
            return duration
    return 0.0


def _apply_telegram_video_duration(workflow: dict[str, Any], video_path: Path) -> float | None:
    """Apply a downloaded video's duration to the conventional H3 duration node.

    Telegram video intake supports general single-video workflows, so a
    workflow without node 14 remains unchanged. H3 workflows that expose the
    conventional ``PrimitiveFloat`` node 14 get the measured duration in the
    task-local workflow copy; the library workflow itself is never modified.
    """
    node = workflow.get("14") if isinstance(workflow, dict) else None
    inputs = node.get("inputs") if isinstance(node, dict) else None
    if not isinstance(inputs, dict) or "value" not in inputs:
        return None

    duration = _probe_video_duration(video_path)
    if duration <= 0:
        raise RhCliError(
            "TELEGRAM_VIDEO_DURATION_UNAVAILABLE",
            f"无法读取下载视频的实际时长：{video_path.name}。请检查本机 ffprobe 是否可用。",
        )
    measured = round(duration, 3)
    inputs["value"] = measured
    return measured


def _video_seconds_from_outputs(outputs: Any) -> float:
    total = 0.0
    if not isinstance(outputs, list):
        return total
    for output in outputs:
        if not isinstance(output, dict) or not _is_video_output(output):
            continue
        duration = None
        for field in ("video_seconds", "duration_seconds", "duration"):
            duration = _decimal_value(output.get(field))
            if duration is not None and duration > 0:
                break
        if duration is None or duration <= 0:
            duration = _probe_video_duration(Path(str(output.get("path") or "")).expanduser())
        if duration > 0:
            total += duration
    return total


def _format_metric(value: float, decimals: int = 2) -> str:
    if abs(value) < 0.0000001:
        return "0"
    formatted = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return formatted or "0"


def _usage_duration_seconds(record: dict[str, Any], current_time: int) -> float:
    duration = _decimal_value(record.get("duration"))
    if duration is not None and duration >= 0:
        return duration
    created_at = int(record.get("created_at") or 0)
    completed_at = int(record.get("completed_at") or 0)
    if completed_at and created_at:
        return max(0.0, (completed_at - created_at) / 1000)
    elapsed_ms = _decimal_value(record.get("elapsed_ms"))
    if elapsed_ms is not None:
        return max(0.0, elapsed_ms / 1000)
    if created_at:
        return max(0.0, (current_time - created_at) / 1000)
    return 0.0


def _usage_record_wall_interval(
    record: dict[str, Any],
    current_time: int,
    range_start_ms: int,
    range_end_ms: int,
) -> tuple[int, int] | None:
    """Return the task's submitted-to-finished interval, clipped to the range."""
    if (_decimal_value(record.get("video_seconds")) or 0) <= 0:
        return None
    start_at = int(record.get("created_at") or 0)
    if not start_at:
        return None
    end_at = int(record.get("completed_at") or 0)
    if not end_at:
        if str(record.get("status") or "") in TERMINAL_TASK_STATUSES:
            end_at = int(record.get("updated_at") or 0)
        else:
            end_at = current_time
    if end_at <= start_at:
        end_at = start_at + round(_usage_duration_seconds(record, current_time) * 1000)
    start_at = max(start_at, range_start_ms)
    end_at = min(end_at, range_end_ms)
    return (start_at, end_at) if end_at > start_at else None


def _dashboard_wall_clock_seconds(
    records: list[dict[str, Any]],
    current_time: int,
    range_start_ms: int,
    range_end_ms: int,
) -> float:
    """Merge overlapping video-task intervals so concurrency is counted once."""
    intervals = sorted(
        interval
        for record in records
        for interval in [_usage_record_wall_interval(record, current_time, range_start_ms, range_end_ms)]
        if interval is not None
    )
    if not intervals:
        return 0.0
    total_ms = 0
    merged_start, merged_end = intervals[0]
    for start_at, end_at in intervals[1:]:
        if start_at <= merged_end:
            merged_end = max(merged_end, end_at)
            continue
        total_ms += merged_end - merged_start
        merged_start, merged_end = start_at, end_at
    total_ms += merged_end - merged_start
    return total_ms / 1000


def _balance_key_group(record: dict[str, Any]) -> str:
    """Return the account bucket used when selecting one balance snapshot."""
    account_id = str(record.get("account_id") or "").strip()
    if account_id:
        return f"account:{account_id}"
    # Legacy keys without account metadata are grouped by site so they cannot
    # accidentally inflate the balance just because several keys were imported.
    site = "cn" if record.get("site") == "cn" else "ai"
    return f"unbound:{site}"


def _balance_key_priority(record: dict[str, Any]) -> tuple[int, int, int, str]:
    """Prefer the key with the freshest successful balance snapshot."""
    balance_checked_at = int(record.get("balance_checked_at") or 0)
    checked_at = int(record.get("checked_at") or 0)
    has_balance = int(
        _decimal_value(record.get("coins")) is not None
        or _decimal_value(record.get("balance")) is not None
    )
    has_snapshot = int(balance_checked_at > 0 and has_balance)
    return has_snapshot, balance_checked_at, has_balance, str(record.get("id") or "")


def _select_balance_keys(keys: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select one API Key per account for current balance display."""
    selected: dict[str, dict[str, Any]] = {}
    for key in keys:
        group = _balance_key_group(key)
        previous = selected.get(group)
        if previous is None or _balance_key_priority(key) > _balance_key_priority(previous):
            selected[group] = key
    return sorted(
        selected.values(),
        key=lambda item: (
            str(item.get("account_id") or ""),
            str(item.get("site") or ""),
            str(item.get("name") or ""),
        ),
    )


def _dashboard_records_in_range(
    records: list[dict[str, Any]],
    start_ms: int,
    end_ms: int,
    account_id: str,
) -> list[dict[str, Any]]:
    scoped = [
        record for record in records
        if start_ms <= int(record.get("created_at") or 0) < end_ms
    ]
    if account_id == UNBOUND_ACCOUNT_ID:
        return [record for record in scoped if not str(record.get("account_id") or "").strip()]
    if account_id:
        return [record for record in scoped if str(record.get("account_id") or "").strip() == account_id]
    return scoped


def _usage_record_site(record: dict[str, Any], account_by_id: dict[str, dict[str, Any]]) -> str:
    site = str(record.get("site") or "").strip()
    if site in {"cn", "ai"}:
        return site
    account = account_by_id.get(str(record.get("account_id") or "").strip())
    account_site = str(account.get("site") or "").strip() if account else ""
    return account_site if account_site in {"cn", "ai"} else ""


def _dashboard_path_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return raw


def _dashboard_registered_workflow_id(
    task: dict[str, Any],
    workflows: list[dict[str, Any]],
) -> str:
    """Resolve a task to a library workflow, with compatibility fallbacks."""
    workflow_by_id = {str(item.get("id") or "").strip(): item for item in workflows}
    explicit_id = str(task.get("registered_workflow_id") or "").strip()
    if explicit_id in workflow_by_id:
        return explicit_id

    task_path = _dashboard_path_key(task.get("workflow_path"))
    if task_path:
        path_matches = [
            item for item in workflows
            if _dashboard_path_key(item.get("workflow_path")) == task_path
        ]
        if len(path_matches) == 1:
            return str(path_matches[0].get("id") or "").strip()

    remote_id = str(task.get("remote_workflow_id") or "").strip()
    if remote_id:
        remote_matches = [
            item for item in workflows
            if str(item.get("remote_workflow_id") or "").strip() == remote_id
        ]
        if len(remote_matches) == 1:
            return str(remote_matches[0].get("id") or "").strip()

    task_name = canonical_workflow_name(str(task.get("workflow_name") or "").strip())
    if task_name:
        name_matches = [
            item for item in workflows
            if canonical_workflow_name(str(item.get("name") or "").strip()) == task_name
        ]
        if len(name_matches) == 1:
            return str(name_matches[0].get("id") or "").strip()
    return ""


def _dashboard_workflow_scores(
    store: LocalStore,
    manager: TaskManager,
    active_task_ids: set[str],
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate current output-library ratings for registered workflows."""
    workflows = [
        item for item in store.workflows()
        if str(item.get("id") or "").strip()
    ]
    tasks = tasks if tasks is not None else manager.public_tasks()
    tasks_by_id = {
        str(task.get("id") or "").strip(): task
        for task in tasks
        if str(task.get("id") or "").strip()
    }
    workflow_by_id = {
        str(item.get("id") or "").strip(): item
        for item in workflows
    }
    score_rows: dict[str, dict[str, Any]] = {}
    task_ids_by_workflow: dict[str, set[str]] = {}

    for output in public_outputs(store, manager, tasks=tasks).get("outputs", []):
        task_id = str(output.get("task_id") or "").strip()
        if not task_id or task_id not in active_task_ids:
            continue
        try:
            rating = int(output.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0
        if rating not in range(1, 6):
            continue
        task = tasks_by_id.get(task_id)
        if not task:
            continue
        workflow_id = _dashboard_registered_workflow_id(task, workflows)
        if workflow_id not in workflow_by_id:
            continue
        record = workflow_by_id[workflow_id]
        row = score_rows.setdefault(
            workflow_id,
            {
                "id": workflow_id,
                "name": str(record.get("name") or workflow_id),
                "account_name": str(record.get("account_name") or ""),
                "site": str(record.get("site") or ""),
                "total_score": 0,
                "rated_output_count": 0,
                "average_rating": 0,
                "run_count": 0,
            },
        )
        row["total_score"] += rating
        row["rated_output_count"] += 1
        task_ids_by_workflow.setdefault(workflow_id, set()).add(task_id)

    rows = list(score_rows.values())
    for row in rows:
        row["average_rating"] = round(row["total_score"] / row["rated_output_count"], 1)
        row["run_count"] = len(task_ids_by_workflow.get(row["id"], set()))
    rows.sort(
        key=lambda item: (
            -int(item["total_score"]),
            -float(item["average_rating"]),
            -int(item["rated_output_count"]),
            str(item["name"] or "").casefold(),
        )
    )
    return {
        "items": rows[:5],
        "registered_count": len(workflows),
        "rated_count": len(rows),
        "formula": "成片页可见产物的已评分星级之和",
    }


def public_dashboard(
    store: LocalStore,
    manager: TaskManager,
    days: int = 7,
    account_id: str = "",
    current_time: int | None = None,
) -> dict[str, Any]:
    """Return usage analytics from the independent local usage ledger."""
    days = days if days in {1, 7, 30} else 7
    account_id = str(account_id or "").strip()
    now = int(current_time if current_time is not None else now_ms())
    range_start_ms = now - days * 86_400_000
    range_end_ms = now + 1

    records = store.usage_records()
    active_records = _dashboard_records_in_range(records, range_start_ms, range_end_ms, account_id)
    account_records = {
        str(record.get("account_id") or "").strip()
        for record in records
        if str(record.get("account_id") or "").strip()
    }
    accounts = store.accounts()
    account_by_id = {
        str(account.get("id") or "").strip(): account
        for account in accounts
        if str(account.get("id") or "").strip()
    }
    account_options = [
        {
            "id": str(account["id"]),
            "name": str(account.get("name") or "未命名账号"),
            "site": str(account.get("site") or ""),
        }
        for account in accounts
        if str(account.get("id") or "").strip()
    ]
    for historical_account_id in sorted(account_records - set(account_by_id)):
        account_options.append({"id": historical_account_id, "name": "账号已删除", "site": ""})
    if any(not str(record.get("account_id") or "").strip() for record in records):
        account_options.append({"id": UNBOUND_ACCOUNT_ID, "name": "未绑定账号", "site": ""})
    account_filter_name = "全部账号"
    if account_id == UNBOUND_ACCOUNT_ID:
        account_filter_name = "未绑定账号"
    elif account_id:
        selected_account = account_by_id.get(account_id)
        account_filter_name = str(selected_account.get("name") if selected_account else next((item["name"] for item in account_options if item["id"] == account_id), "账号已删除"))
    current_tasks = manager.public_tasks()
    current_task_ids = {str(task.get("id") or "") for task in current_tasks}
    active_task_ids = {str(record.get("task_id") or "") for record in active_records}
    workflow_scores = _dashboard_workflow_scores(store, manager, active_task_ids, tasks=current_tasks)

    total_coins = 0.0
    money_spent: dict[str, dict[str, Any]] = {}
    total_seconds = 0.0
    total_video_seconds = 0.0
    video_task_count = 0
    completed = 0
    failed = 0
    output_count = 0
    for record in active_records:
        duration_seconds = _usage_duration_seconds(record, now)
        total_seconds += duration_seconds
        video_seconds = _decimal_value(record.get("video_seconds")) or 0
        if video_seconds > 0:
            total_video_seconds += video_seconds
            video_task_count += 1
        output_count += int(record.get("output_count") or 0)
        cost = _decimal_value(record.get("cost"))
        if str(record.get("cost_type") or "") == "coins" and cost is not None:
            total_coins += cost
        elif str(record.get("cost_type") or "") == "money" and cost is not None:
            site = _usage_record_site(record, account_by_id)
            bucket_key = site or "unknown"
            bucket = money_spent.setdefault(
                bucket_key,
                {
                    "site": site,
                    "symbol": "¥" if site == "cn" else "$" if site == "ai" else "",
                    "value": 0.0,
                },
            )
            bucket["value"] += cost
        status = str(record.get("status") or "")
        if status == "completed":
            completed += 1
        elif status in {"failed", "cancelled", "interrupted"}:
            failed += 1

    wall_clock_seconds = _dashboard_wall_clock_seconds(active_records, now, range_start_ms, range_end_ms)
    response_seconds_per_video_second = (
        wall_clock_seconds / total_video_seconds
        if wall_clock_seconds > 0 and total_video_seconds > 0
        else 0.0
    )

    keys = manager.public_keys()
    selected_balance_keys = _select_balance_keys(keys)
    coin_balance = 0.0
    money_balances: dict[str, dict[str, Any]] = {}
    balances: list[dict[str, Any]] = []
    latest_balance_checked_at = 0
    for key in selected_balance_keys:
        coins = _decimal_value(key.get("coins"))
        if coins is not None:
            coin_balance += coins
        balance = _decimal_value(key.get("balance"))
        site = "cn" if key.get("site") == "cn" else "ai"
        symbol = str(key.get("symbol") or ("¥" if site == "cn" else "$"))
        if balance is not None:
            bucket = money_balances.setdefault(site, {"site": site, "symbol": symbol, "value": 0.0})
            bucket["value"] += balance
        checked_at = int(key.get("balance_checked_at") or 0)
        latest_balance_checked_at = max(latest_balance_checked_at, checked_at)
        account_id = str(key.get("account_id") or "").strip()
        account = account_by_id.get(account_id)
        balances.append({
            "account_id": account_id,
            "account_name": str(account.get("name") if account else ("未绑定账号" if not account_id else "账号已删除")),
            "key_name": str(key.get("name") or "未命名 API Key"),
            "name": str(key.get("name") or "未命名 API Key"),
            "site": site,
            "status": str(key.get("status") or "unchecked"),
            "coins": str(key.get("coins") or ""),
            "balance": str(key.get("balance") or ""),
            "symbol": symbol,
            "balance_checked_at": checked_at,
            "api_type": str(key.get("api_type") or ""),
        })

    recent = []
    for record in active_records[:12]:
        recent.append({
            "task_id": str(record.get("task_id") or ""),
            "created_at": int(record.get("created_at") or 0),
            "status": str(record.get("status") or ""),
            "workflow_name": str(record.get("workflow_name") or "未命名工作流"),
            "cost_type": str(record.get("cost_type") or ""),
            "cost": str(record.get("cost") or ""),
            "duration_seconds": _format_metric(_usage_duration_seconds(record, now)),
            "output_count": int(record.get("output_count") or 0),
            "task_available": str(record.get("task_id") or "") in current_task_ids,
        })

    return {
        "range_days": days,
        "account_filter": account_id,
        "account_filter_name": account_filter_name,
        "accounts": account_options,
        "range_start": range_start_ms,
        "range_end": range_end_ms,
        "source": {
            "type": "usage_records",
            "label": "独立用量记录",
            "record_count": len(records),
            "description": "首次启用时由现有任务初始化；后续删除任务不会删除统计记录。",
        },
        "summary": {
            "coins_spent": _format_metric(total_coins, 4),
            "money_spent": [
                {
                    "site": value["site"],
                    "symbol": value["symbol"],
                    "value": _format_metric(value["value"], 4),
                }
                for value in (money_spent[key] for key in sorted(money_spent))
            ],
            "submissions": len(active_records),
            "processing_seconds": _format_metric(total_seconds),
            "wall_clock_seconds": _format_metric(wall_clock_seconds),
            "video_seconds": _format_metric(total_video_seconds, 3),
            "video_task_count": video_task_count,
            "response_seconds_per_video_second": _format_metric(response_seconds_per_video_second, 3),
            "completed": completed,
            "failed": failed,
            "success_rate": round(completed / len(active_records) * 100, 1) if active_records else 0,
            "outputs": output_count,
        },
        "balances": {
            "coins": _format_metric(coin_balance, 4),
            "account_count": len(selected_balance_keys),
            "key_count": len(keys),
            "selection_note": "每个账号只取最近一次成功查询的一个 API Key；未绑定旧 Key 按站点合并。",
            "money": [
                {"site": value["site"], "symbol": value["symbol"], "value": _format_metric(value["value"], 4)}
                for value in money_balances.values()
            ],
            "latest_checked_at": latest_balance_checked_at,
            "keys": balances,
        },
        "workflow_scores": workflow_scores,
        "recent": recent,
    }


def _output_tags(value: Any) -> list[str]:
    raw = value.get("tags") if isinstance(value, dict) else value
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    for item in raw:
        tag = str(item or "").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def matches_public_output_filters(item: dict[str, Any], filters: dict[str, Any] | None = None) -> bool:
    """Apply the output-library folder and filter state to one public artifact."""
    filters = filters or {}
    selected_project = str(filters.get("project_id") or "").strip()
    item_project = str(item.get("project_id") or "").strip()
    if selected_project:
        if selected_project == "__unclassified__":
            if item_project:
                return False
        elif item_project != selected_project:
            return False

    selected_type = str(filters.get("type") or "").strip()
    if selected_type and selected_type != "all" and str(item.get("display_type") or "") != selected_type:
        return False

    selected_rating = str(filters.get("rating") or "").strip()
    if selected_rating:
        try:
            item_rating = int(item.get("rating") or 0)
        except (TypeError, ValueError):
            item_rating = 0
        if selected_rating == "unrated":
            if item_rating != 0:
                return False
        else:
            try:
                if item_rating != int(selected_rating):
                    return False
            except ValueError:
                pass

    search = str(filters.get("search") or "").strip().lower()
    if search:
        searchable = " ".join(
            str(item.get(field) or "").lower()
            for field in ("name", "task_name", "project_name")
        )
        if search not in searchable:
            return False

    workflow = str(filters.get("workflow") or "").strip()
    if workflow and str(item.get("task_name") or item.get("workflow_name") or "").strip() != workflow:
        return False

    tags = set(_output_tags(item))
    for filter_key, tag in (("tag_case", "案例"), ("tag_h", "H")):
        mode = str(filters.get(filter_key) or "off").strip()
        if mode == "include" and tag not in tags:
            return False
        if mode == "exclude" and tag in tags:
            return False

    try:
        created_at = int(item.get("task_created_at") or 0)
    except (TypeError, ValueError):
        created_at = 0
    try:
        range_start = int(filters.get("range_start") or 0)
    except (TypeError, ValueError):
        range_start = 0
    try:
        range_end = int(filters.get("range_end") or 0)
    except (TypeError, ValueError):
        range_end = 0
    if range_start and created_at < range_start:
        return False
    if range_end and created_at >= range_end:
        return False

    selected_account = str(filters.get("account_id") or "").strip()
    item_account = str(item.get("account_id") or "").strip()
    if selected_account == "__unbound__" and item_account:
        return False
    if selected_account and selected_account != "__unbound__" and item_account != selected_account:
        return False
    return True


def public_output_media(
    manager: TaskManager,
    tag: str = "案例",
    *,
    project_id: str = "",
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return tagged local media within the selected project (empty means all)."""
    media: list[dict[str, Any]] = []
    used_archive_names: set[str] = set()
    output_filters = dict(filters or {})
    if project_id and "project_id" not in output_filters:
        output_filters["project_id"] = project_id
    for task in manager.public_tasks():
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        task_output_root = Path(str(task.get("output_dir") or "")).expanduser().resolve()
        task_name = safe_name(str(task.get("workflow_name") or "").strip(), task_id)
        task_folder = safe_name(task_id, "task")
        file_index = 0
        for output_index, output in enumerate(task.get("outputs") or []):
            if not isinstance(output, dict) or str(output.get("kind") or "file") != "file":
                continue
            current_index = file_index
            file_index += 1
            if tag not in _output_tags(output):
                continue
            raw_path = str(output.get("path") or "").strip()
            if not raw_path:
                continue
            file_path = Path(raw_path).expanduser().resolve()
            try:
                mime = str(output.get("mime") or mimetypes.guess_type(str(file_path))[0] or "")
                if not file_path.is_file() or task_output_root not in file_path.parents:
                    continue
                if not mime.startswith(("image/", "video/", "audio/")):
                    continue
            except OSError:
                continue
            if mime.startswith("image/"):
                display_type = "image"
            elif mime.startswith("video/"):
                display_type = "video"
            else:
                display_type = "audio"
            try:
                rating = int(output.get("rating") or 0)
            except (TypeError, ValueError):
                rating = 0
            if rating not in range(1, 6):
                rating = 0
            output_item = {
                "id": f"{task_id}:file:{current_index}",
                "kind": "file",
                "display_type": display_type,
                "name": str(output.get("name") or file_path.name),
                "task_id": task_id,
                "account_id": str(task.get("account_id") or "").strip(),
                "project_id": str(task.get("project_id") or "").strip(),
                "project_name": str(task.get("project_name") or "").strip(),
                "task_name": str(task.get("workflow_name") or task_id),
                "task_created_at": int(task.get("created_at") or 0),
                "rating": rating,
                "tags": _output_tags(output),
                "output_index": output_index,
                "file_index": current_index,
            }
            if not matches_public_output_filters(output_item, output_filters):
                continue
            filename = safe_name(str(output.get("name") or file_path.name), file_path.name)
            archive_name = f"{task_name}/{task_folder}/{filename}"
            if archive_name in used_archive_names:
                stem = Path(filename).stem or "output"
                suffix = Path(filename).suffix
                counter = 2
                while archive_name in used_archive_names:
                    archive_name = f"{task_name}/{task_folder}/{stem} ({counter}){suffix}"
                    counter += 1
            used_archive_names.add(archive_name)
            media.append({"path": file_path, "archive_name": archive_name, "mime": mime})
    return media


def public_outputs(
    store: LocalStore,
    manager: TaskManager,
    *,
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return locally available task artifacts for the output library page."""
    artifacts: list[dict[str, Any]] = []
    type_counts = {"image": 0, "video": 0, "audio": 0, "other": 0, "text": 0}
    rating_counts = {"unrated": 0, **{str(score): 0 for score in range(1, 6)}}
    tag_counts: dict[str, int] = {"案例": 0}
    registered_workflows = [
        item for item in store.workflows()
        if str(item.get("id") or "").strip()
    ]

    tasks = tasks if tasks is not None else manager.public_tasks()
    for task in tasks:
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        file_index = 0
        task_output_root = Path(str(task.get("output_dir") or "")).expanduser().resolve()
        task_workflow_path = Path(str(task.get("workflow_path") or "")).expanduser()
        task_snapshot_path = LocalStore.task_snapshot_path(task)
        workflow_available = task_snapshot_path.is_file() or task_workflow_path.is_file()
        registered_workflow_id = _dashboard_registered_workflow_id(task, registered_workflows)
        account_id = str(task.get("account_id") or "").strip()
        project_id = str(task.get("project_id") or "").strip()
        project_name = str(task.get("project_name") or "").strip()
        project_path = str(task.get("project_path") or "").strip()
        for output_index, output in enumerate(task.get("outputs") or []):
            if not isinstance(output, dict):
                continue
            try:
                rating = int(output.get("rating") or 0)
            except (TypeError, ValueError):
                rating = 0
            if rating not in range(1, 6):
                rating = 0
            kind = str(output.get("kind") or "file")
            if kind == "text":
                text = str(output.get("text") or "")
                if not text.strip():
                    continue
                type_counts["text"] += 1
                tags = _output_tags(output)
                for tag in tags:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
                artifacts.append(
                    {
                        "id": f"{task_id}:text:{len(artifacts)}",
                        "kind": "text",
                        "display_type": "text",
                        "name": str(output.get("name") or f"文本输出 · {output.get('node_id') or 'output'}"),
                        "text": text,
                        "node_id": str(output.get("node_id") or ""),
                        "task_id": task_id,
                        "registered_workflow_id": registered_workflow_id,
                        "account_id": account_id,
                        "project_id": project_id,
                        "project_name": project_name,
                        "project_path": project_path,
                        "workflow_available": workflow_available,
                        "output_index": output_index,
                        "rating": rating,
                        "tags": tags,
                        "task_name": str(task.get("workflow_name") or task_id),
                        "task_status": str(task.get("status") or ""),
                        "task_created_at": int(task.get("created_at") or 0),
                        "task_completed_at": int(task.get("completed_at") or 0),
                        "cost_type": str(task.get("cost_type") or ""),
                        "cost": str(task.get("cost") or ""),
                    }
                )
                rating_counts[str(rating) if rating else "unrated"] += 1
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
            tags = _output_tags(output)
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
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
                    "registered_workflow_id": registered_workflow_id,
                    "account_id": account_id,
                    "project_id": project_id,
                    "project_name": project_name,
                    "project_path": project_path,
                    "workflow_available": workflow_available,
                    "output_index": output_index,
                    "rating": rating,
                    "tags": tags,
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
            rating_counts[str(rating) if rating else "unrated"] += 1

    artifacts.sort(key=lambda item: int(item.get("modified_at") or item.get("task_created_at") or 0), reverse=True)
    return {
        "outputs": artifacts,
        "projects": store.project_folders(),
        "summary": {
            "total": len(artifacts),
            "tasks": len({item["task_id"] for item in artifacts}),
            "rating_counts": rating_counts,
            "tag_counts": tag_counts,
            **type_counts,
        },
    }
