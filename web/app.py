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
DEFAULT_PERSONAL_CAPACITY = 3
MIN_PERSONAL_CAPACITY = 1
MAX_PERSONAL_CAPACITY = 3
WORKFLOW_META_KEY = "__rh_meta__"


def now_ms() -> int:
    return int(time.time() * 1000)


def default_local_output_dir() -> Path:
    return OUTPUT_ROOT


def native_file_picker_available() -> bool:
    return platform.system() == "Darwin" and shutil.which("osascript") is not None


def pick_local_file_on_macos() -> Path:
    """Use the macOS native picker so the browser never needs the real path."""
    if platform.system() != "Darwin":
        raise RhCliError("LOCAL_PICKER_UNAVAILABLE", "本机路径选择目前仅支持 macOS；请手动填写绝对路径。")
    if shutil.which("osascript") is None:
        raise RhCliError("LOCAL_PICKER_UNAVAILABLE", "本机没有可用的 macOS 文件选择器，请手动填写绝对路径。")

    script = r'''
try
    set pickedFile to choose file with prompt "选择工作流输入文件"
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


def metadata_bypassed_inputs(workflow: dict[str, Any]) -> list[str]:
    """Read Web-app input bypass state from local workflow metadata."""
    metadata = workflow.get(WORKFLOW_META_KEY)
    if not isinstance(metadata, dict):
        return []
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


def inspect_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes = workflow_nodes(workflow)
    _validate_api_workflow(nodes)
    files: list[dict[str, Any]] = []
    prompts: list[dict[str, Any]] = []
    random_noise: list[dict[str, Any]] = []
    for node_id, node in nodes.items():
        class_type = str(node.get("class_type", ""))
        lower_class = class_type.lower()
        title = str(node.get("_meta", {}).get("title") or class_type)
        inputs = node.get("inputs", {})
        noise = random_noise_spec(str(node_id), node)
        if noise:
            random_noise.append(noise)
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
        "bypassed_inputs": metadata_bypassed_inputs(workflow),
        "remote_workflow_id": remote_workflow_id(workflow),
    }


def normalize_bypassed_inputs(
    workflow: dict[str, Any], values: list[str] | dict[str, Any] | None,
) -> list[str]:
    """Validate input-card IDs whose Web overrides should be ignored."""
    if values is None:
        raw_values: Any = metadata_bypassed_inputs(workflow)
    elif isinstance(values, dict):
        raw_values = [key for key, enabled in values.items() if enabled]
    elif isinstance(values, (list, tuple, set)):
        raw_values = values
    else:
        raise RhCliError("INVALID_BYPASS", "输入旁路配置必须是列表或对象。")

    analysis = inspect_workflow(workflow)
    known = {
        item["id"]
        for group in (analysis["file_inputs"], analysis["prompt_inputs"])
        for item in group
    }
    known.update(item["id"] for item in analysis["random_noise_inputs"])
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        input_id = str(raw_value or "").strip()
        if not input_id or input_id in seen:
            continue
        if input_id not in known:
            raise RhCliError("INVALID_BYPASS", f"找不到可旁路的输入节点：{input_id}")
        result.append(input_id)
        seen.add(input_id)
    return result


def bypassed_local_file_args(workflow: dict[str, Any], bypassed_inputs: set[str]) -> list[str]:
    """Upload original local file values needed by bypassed Load* inputs."""
    if not bypassed_inputs:
        return []
    file_ids = {item["id"] for item in inspect_workflow(workflow)["file_inputs"]}
    file_args: list[str] = []
    for input_id in sorted(bypassed_inputs & file_ids):
        separator = input_id.find(":")
        if separator <= 0:
            continue
        node = workflow.get(input_id[:separator])
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            continue
        original_value = inputs.get(input_id[separator + 1 :])
        if not isinstance(original_value, str) or not original_value.strip():
            continue
        original_path = Path(original_value).expanduser()
        if not original_path.is_absolute():
            continue
        if not original_path.is_file():
            raise RhCliError(
                "BYPASS_FILE_NOT_FOUND",
                f"旁路输入 {input_id} 的原始本机文件不存在：{original_path}。请关闭旁路并选择新文件，或恢复原始文件。",
            )
        file_args.append(f"{input_id}={original_path}")
    return file_args


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


def public_key(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
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
              dispatch_key_name TEXT NOT NULL DEFAULT '',
              dispatch_key_site TEXT NOT NULL DEFAULT '',
              dispatch_key_api_type TEXT NOT NULL DEFAULT '',
              remote_task_id TEXT,
              remote_workflow_id TEXT NOT NULL DEFAULT '',
              input_json TEXT NOT NULL,
              prompt_json TEXT NOT NULL,
              bypass_json TEXT NOT NULL DEFAULT '[]',
              random_noise_json TEXT NOT NULL DEFAULT '{}',
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
        if "bypass_json" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN bypass_json TEXT NOT NULL DEFAULT '[]'")
        if "workflow_snapshot_path" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN workflow_snapshot_path TEXT NOT NULL DEFAULT ''")
        if "dispatch_key_name" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN dispatch_key_name TEXT NOT NULL DEFAULT ''")
        if "dispatch_key_site" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN dispatch_key_site TEXT NOT NULL DEFAULT ''")
        if "dispatch_key_api_type" not in columns:
            self._db.execute("ALTER TABLE tasks ADD COLUMN dispatch_key_api_type TEXT NOT NULL DEFAULT ''")
        self._backfill_dispatch_credential_snapshots()

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
                    self._write_json_file(
                        {
                            "keys": imported,
                            "output_dir": str(default_local_output_dir()),
                            "personal_capacity": configured_capacity,
                            "initialized": True,
                        }
                    )
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
                api_type = str(item.get("api_type") or "")
                normalized["capacity"] = key_capacity(api_type, configured_capacity)
                normalized["active_tasks"] = int(item.get("active_tasks") or 0)
                result.append(normalized)
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
            raise RhCliError("ACCOUNT_NOT_FOUND", "找不到这个托管账号。")
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
            raise RhCliError("ACCOUNT_NOT_FOUND", "找不到这个托管账号。")
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

    def save_workflow(self, filename: str, content: str) -> tuple[str, Path, dict[str, Any]]:
        try:
            workflow = json.loads(content)
        except (ValueError, TypeError) as exc:
            raise RhCliError("INVALID_WORKFLOW", "无法解析工作流 JSON。") from exc
        if not isinstance(workflow, dict):
            raise RhCliError("INVALID_WORKFLOW", "工作流顶层必须是 API 格式节点字典。")
        analysis = inspect_workflow(workflow)
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        clean_name = safe_name(filename, "workflow.json")
        path = WORKFLOW_ROOT / f"{workflow_id}_{clean_name}"
        path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
        return workflow_id, path, analysis

    def workflow_path(self, workflow_id: str) -> Path:
        matches = list(WORKFLOW_ROOT.glob(f"{workflow_id}_*"))
        if not matches:
            raise RhCliError("WORKFLOW_NOT_FOUND", f"找不到工作流：{workflow_id}")
        return matches[0]

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
            "analysis": inspect_workflow(workflow),
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
            "dispatch_key_name": str(task.get("dispatch_key_name") or "").strip(),
            "dispatch_key_site": str(task.get("dispatch_key_site") or "").strip(),
            "dispatch_key_api_type": str(task.get("dispatch_key_api_type") or "").strip(),
            "remote_task_id": None,
            "remote_workflow_id": str(task.get("remote_workflow_id") or "").strip(),
            "input_json": json.dumps(task["files"], ensure_ascii=False),
            "prompt_json": json.dumps(task["prompts"], ensure_ascii=False),
            "bypass_json": json.dumps(task.get("bypassed_inputs") or [], ensure_ascii=False),
            "random_noise_json": json.dumps(task.get("random_noise") or {}, ensure_ascii=False),
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
                "INSERT INTO tasks (id,created_at,updated_at,status,progress,workflow_path,workflow_name,key_id,dispatch_key_name,dispatch_key_site,dispatch_key_api_type,remote_task_id,remote_workflow_id,"
                "input_json,prompt_json,bypass_json,random_noise_json,workflow_snapshot_path,output_dir,outputs_json,error,error_detail,stage_logs_json,cost_type,cost,duration) "
                "VALUES (:id,:created_at,:updated_at,:status,:progress,:workflow_path,:workflow_name,:key_id,:dispatch_key_name,:dispatch_key_site,:dispatch_key_api_type,:remote_task_id,:remote_workflow_id,"
                ":input_json,:prompt_json,:bypass_json,:random_noise_json,:workflow_snapshot_path,:output_dir,:outputs_json,:error,:error_detail,:stage_logs_json,:cost_type,:cost,:duration)",
                fields,
            )
            self._db.commit()

    def update_task(self, task_id: str, **changes: Any) -> None:
        allowed = {
            "status", "progress", "updated_at", "started_at", "completed_at", "key_id", "dispatch_key_name", "dispatch_key_site", "dispatch_key_api_type", "remote_task_id", "remote_workflow_id",
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
        names = {
            "input_json": "files",
            "prompt_json": "prompts",
            "bypass_json": "bypassed_inputs",
            "random_noise_json": "random_noise",
            "outputs_json": "outputs",
            "stage_logs_json": "stage_logs",
            "error_detail": "error_detail",
        }
        for field, public_name in names.items():
            try:
                task[public_name] = json.loads(task.pop(field))
            except (ValueError, TypeError):
                task[public_name] = {} if public_name == "error_detail" else []
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
            result.append(task)
        queued = sorted((item for item in result if item.get("status") == "queued"), key=lambda item: item.get("created_at", 0))
        positions = {item["id"]: index for index, item in enumerate(queued, start=1)}
        for task in result:
            task["queue_position"] = positions.get(task["id"], 0)
        return result

    def public_keys(self) -> list[dict[str, Any]]:
        with self._lock:
            active = dict(self._active_by_key)
        records = self.store.keys()
        return [public_key({**record, "active_tasks": active.get(record["id"], 0)}) for record in records]

    def add_key(self, name: str, site: str, api_key: str) -> dict[str, Any]:
        api_key = str(api_key or "").strip()
        site = "cn" if site == "cn" else "ai"
        if not api_key:
            raise RhCliError("INVALID_API_KEY", "API Key 不能为空。")
        records = self.store.keys()
        if any(item.get("api_key") == api_key for item in records):
            raise RhCliError("DUPLICATE_API_KEY", "这个 API Key 已经保存。")
        record = {
            "id": f"key_{uuid.uuid4().hex[:12]}",
            "name": str(name or "").strip() or f"{site.upper()} Key {len(records) + 1}",
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
        bypassed_inputs: list[str] | dict[str, Any] | None = None,
        workflow_data: dict[str, Any] | None = None,
        workflow_name: str | None = None,
    ) -> dict[str, Any]:
        if workflow_data is not None:
            if not isinstance(workflow_data, dict):
                raise RhCliError("INVALID_WORKFLOW", "当前工作流必须是 API 格式节点字典。")
            workflow = workflow_data
        else:
            workflow_path = self.store.workflow_path(workflow_id)
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        analysis = inspect_workflow(workflow)
        remote_id = str(remote_workflow_id or "").strip() or analysis.get("remote_workflow_id", "")
        if not remote_id:
            raise RhCliError("MISSING_WORKFLOW_ID", "请填写 RunningHub workflowId 后再提交。")
        normalized_bypassed_inputs = normalize_bypassed_inputs(workflow, bypassed_inputs)
        bypassed_set = set(normalized_bypassed_inputs)
        normalized_random_noise = normalize_random_noise_inputs(workflow, random_noise)
        active_random_noise = {
            node_id: config
            for node_id, config in normalized_random_noise.items()
            if node_id not in bypassed_set
        }
        if workflow_data is not None:
            source_name = str(workflow_name or "workflow_api.json").strip() or "workflow_api.json"
            workflow_id, workflow_path, _ = self.store.save_workflow(
                source_name,
                json.dumps(workflow_data, ensure_ascii=False),
            )
        required = {
            item["id"]
            for item in analysis["file_inputs"]
            if item["id"] not in bypassed_set
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
            "workflow_name": workflow_path.name.split("_", 1)[-1],
            "remote_workflow_id": remote_id,
            "files": files,
            "prompts": prompts,
            "bypassed_inputs": normalized_bypassed_inputs,
            "random_noise": normalized_random_noise,
            "key_id": key_id or None,
            "output_dir": str(root),
        }
        snapshot_workflow = json.loads(json.dumps(workflow, ensure_ascii=False))
        for values in (files, prompts):
            for input_id, value in values.items():
                if input_id in bypassed_set:
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
        apply_random_noise_inputs(snapshot_workflow, active_random_noise)
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
            record = self._select_key(task, keys, records)
            if not record:
                wait_message = self._queue_wait_message(task, keys, records)
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
            bypassed_inputs = {str(item) for item in task.get("bypassed_inputs") or []}
            file_args = [
                f"{item_id}={path}"
                for item_id, path in task["files"].items()
                if item_id not in bypassed_inputs
            ]
            set_args = [
                f"{item_id}={value}"
                for item_id, value in task["prompts"].items()
                if item_id not in bypassed_inputs
            ]
            task_output_dir = Path(task["output_dir"]) / task_id
            task_output_dir.mkdir(parents=True, exist_ok=True)
            site_upload, site_create, site_outputs = _site_urls(key["site"])

            with RhHttpClient(key["api_key"], no_proxy_host="runninghub.ai" if key["site"] == "ai" else "") as client:
                self._log_stage(task_id, "prepare", "已读取工作流，准备应用输入配置")
                workflow_path = self.store.task_workflow_path(task)
                workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
                workflow = workflow_nodes(workflow)
                file_args.extend(bypassed_local_file_args(workflow, bypassed_inputs))
                remote_id_value = str(task.get("remote_workflow_id") or "").strip()
                if not remote_id_value:
                    raise RhCliError("MISSING_WORKFLOW_ID", "任务缺少 RunningHub workflowId，请重新提交。")
                if set_args:
                    prompt_changes = _apply_overrides(workflow, set_args)
                    self._log_stage(task_id, "prepare", f"已应用 {len(prompt_changes)} 个文本配置")
                random_noise_values = {
                    node_id: config
                    for node_id, config in (task.get("random_noise") or {}).items()
                    if node_id not in bypassed_inputs
                }
                random_noise_changes = apply_random_noise_inputs(workflow, random_noise_values)
                if random_noise_changes:
                    self._log_stage(task_id, "prepare", f"已应用 {len(random_noise_changes) // 2} 个 RandomNoise 配置")
                if bypassed_inputs:
                    self._log_stage(task_id, "prepare", f"已忽略 {len(bypassed_inputs)} 个输入覆盖")
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
    return {
        "settings": {
            "output_dir": store.output_dir(),
            "personal_capacity": store.personal_capacity(),
            "data_dir": str(DATA_ROOT),
            "native_file_picker": native_file_picker_available(),
        },
        "keys": manager.public_keys(),
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
