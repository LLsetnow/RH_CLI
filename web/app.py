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


def key_capacity(api_type: str) -> int:
    """Return the local scheduler cap for the RunningHub account type."""
    normalized = str(api_type or "").lower()
    return 100 if "enterprise" in normalized or "shared" in normalized else 3


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
        "remote_workflow_id": remote_workflow_id(workflow),
    }


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
              remote_task_id TEXT,
              remote_workflow_id TEXT NOT NULL DEFAULT '',
              input_json TEXT NOT NULL,
              prompt_json TEXT NOT NULL,
              random_noise_json TEXT NOT NULL DEFAULT '{}',
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

    def _interrupt_incomplete(self) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='interrupted', progress='应用重新启动，保留历史记录但未继续轮询', updated_at=? "
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
                            "capacity": 3,
                            "active_tasks": 0,
                            "balance": "",
                            "coins": "",
                            "symbol": "¥" if site == "cn" else "$",
                            "balance_checked_at": 0,
                            "checked_at": 0,
                        }
                    )
                if imported:
                    self._write_json_file({"keys": imported, "output_dir": str(default_local_output_dir()), "initialized": True})
                    raw_keys = imported
                else:
                    data["initialized"] = True
                    self._write_json_file(data)
            result: list[dict[str, Any]] = []
            for item in raw_keys:
                if not isinstance(item, dict) or not str(item.get("api_key", "")).strip():
                    continue
                normalized = dict(item)
                normalized["site"] = "cn" if item.get("site") == "cn" else "ai"
                api_type = str(item.get("api_type") or "")
                normalized["capacity"] = key_capacity(api_type) if api_type else int(item.get("capacity") or 3)
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
            self._write_json_file(data)

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

    def load_task_workflow(self, task_id: str) -> dict[str, Any]:
        task = self.task(task_id)
        if not task:
            raise RhCliError("TASK_NOT_FOUND", "找不到这个任务。")
        workflow_path = Path(str(task.get("workflow_path") or "")).expanduser().resolve()
        if not workflow_path.is_file():
            raise RhCliError("WORKFLOW_NOT_FOUND", f"任务对应的工作流文件不存在：{workflow_path}")
        try:
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RhCliError("INVALID_WORKFLOW", f"无法读取任务对应的工作流：{workflow_path}") from exc
        if not isinstance(workflow, dict):
            raise RhCliError("INVALID_WORKFLOW", "任务对应的工作流不是 API 格式节点字典。")
        name_parts = workflow_path.name.split("_", 2)
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
            "progress": "等待本地调度…",
            "workflow_path": task["workflow_path"],
            "workflow_name": task["workflow_name"],
            "key_id": task.get("key_id"),
            "remote_task_id": None,
            "remote_workflow_id": str(task.get("remote_workflow_id") or "").strip(),
            "input_json": json.dumps(task["files"], ensure_ascii=False),
            "prompt_json": json.dumps(task["prompts"], ensure_ascii=False),
            "random_noise_json": json.dumps(task.get("random_noise") or {}, ensure_ascii=False),
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
                "INSERT INTO tasks (id,created_at,updated_at,status,progress,workflow_path,workflow_name,key_id,remote_task_id,remote_workflow_id,"
                "input_json,prompt_json,random_noise_json,output_dir,outputs_json,error,error_detail,stage_logs_json,cost_type,cost,duration) "
                "VALUES (:id,:created_at,:updated_at,:status,:progress,:workflow_path,:workflow_name,:key_id,:remote_task_id,:remote_workflow_id,"
                ":input_json,:prompt_json,:random_noise_json,:output_dir,:outputs_json,:error,:error_detail,:stage_logs_json,:cost_type,:cost,:duration)",
                fields,
            )
            self._db.commit()

    def update_task(self, task_id: str, **changes: Any) -> None:
        allowed = {
            "status", "progress", "updated_at", "started_at", "completed_at", "key_id", "remote_task_id", "remote_workflow_id",
            "outputs_json", "error", "error_detail", "stage_logs_json", "cost_type", "cost", "duration", "output_dir",
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
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="rh-web-dispatcher", daemon=True)
        self._dispatcher.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._dispatcher.join(timeout=1.5)
        self._executor.shutdown(wait=False, cancel_futures=False)

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
            task["key_name"] = key.get("name") if key else ("自动调度" if not task.get("key_id") else "已删除 Key")
            task["key_site"] = key.get("site") if key else ""
            task["remote_task_id"] = task.get("remote_task_id") or ""
            task["remote_workflow_id"] = task.get("remote_workflow_id") or ""
            result.append(task)
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
            "capacity": 3,
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
                    "capacity": key_capacity(api_type),
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
        normalized_random_noise = normalize_random_noise_inputs(workflow, random_noise)
        if workflow_data is not None:
            source_name = str(workflow_name or "workflow_api.json").strip() or "workflow_api.json"
            workflow_id, workflow_path, _ = self.store.save_workflow(
                source_name,
                json.dumps(workflow_data, ensure_ascii=False),
            )
        required = {item["id"] for item in analysis["file_inputs"]}
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
            "random_noise": normalized_random_noise,
            "key_id": key_id or None,
            "output_dir": str(root),
        }
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
            if task["status"] != "queued" or task["id"] in self._claimed:
                continue
            record = self._select_key(task, keys, records)
            if not record:
                continue
            with self._lock:
                if task["id"] in self._claimed:
                    continue
                self._claimed.add(task["id"])
                self._active_by_key[record["id"]] = self._active_by_key.get(record["id"], 0) + 1
                event = threading.Event()
                self._cancel_events[task["id"]] = event
            self.store.update_task(
                task["id"], status="submitting", key_id=record["id"], started_at=now_ms(), progress=f"使用 {record['name']} 提交中…"
            )
            self._log_stage(task["id"], "dispatch", f"已选择 {record['name']}，开始执行")
            self._executor.submit(self._run_task, task["id"], record, event)

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

    def _run_task(self, task_id: str, key: dict[str, Any], cancel_event: threading.Event) -> None:
        try:
            task = self.store.task(task_id)
            if not task:
                return
            if cancel_event.is_set():
                raise RhCliError("TASK_CANCELLED", "任务已取消。")
            file_args = [f"{item_id}={path}" for item_id, path in task["files"].items()]
            set_args = [f"{item_id}={value}" for item_id, value in task["prompts"].items()]
            task_output_dir = Path(task["output_dir"]) / task_id
            task_output_dir.mkdir(parents=True, exist_ok=True)
            site_upload, site_create, site_outputs = _site_urls(key["site"])

            with RhHttpClient(key["api_key"], no_proxy_host="runninghub.ai" if key["site"] == "ai" else "") as client:
                self._log_stage(task_id, "prepare", "已读取工作流，准备应用输入配置")
                workflow = json.loads(Path(task["workflow_path"]).read_text(encoding="utf-8"))
                workflow = workflow_nodes(workflow)
                remote_id_value = str(task.get("remote_workflow_id") or "").strip()
                if not remote_id_value:
                    raise RhCliError("MISSING_WORKFLOW_ID", "任务缺少 RunningHub workflowId，请重新提交。")
                if set_args:
                    prompt_changes = _apply_overrides(workflow, set_args)
                    self._log_stage(task_id, "prepare", f"已应用 {len(prompt_changes)} 个文本配置")
                random_noise_changes = apply_random_noise_inputs(workflow, task.get("random_noise") or {})
                if random_noise_changes:
                    self._log_stage(task_id, "prepare", f"已应用 {len(random_noise_changes) // 2} 个 RandomNoise 配置")
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
            client.download(str(url), str(path))
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
            "data_dir": str(DATA_ROOT),
            "native_file_picker": native_file_picker_available(),
        },
        "keys": manager.public_keys(),
        "tasks": manager.public_tasks(),
    }
