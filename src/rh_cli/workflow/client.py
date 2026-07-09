from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rh_cli.config import require_api_key
from rh_cli.errors import RhCliError
from rh_cli.http import API_HOST, BASE_URL, RhHttpClient
from rh_cli.output import RunResult, resolve_output_path


# 官方 rh 只实现了 AI 应用（/task/openapi/ai-app/run）。这里补上「原始 ComfyUI
# 工作流图」这条路：提交完整 workflow JSON，并用经典的 outputs 端点轮询。
UPLOAD_URL = f"{BASE_URL}/media/upload/binary"
CREATE_URL = f"{API_HOST}/task/openapi/create"
OUTPUTS_URL = f"{API_HOST}/task/openapi/outputs"

MAX_POLL_SECONDS = 1200
POLL_INTERVAL_SECONDS = 5


def find_load_image_node(workflow: dict[str, Any]) -> str | None:
    """自动查找工作流中的 LoadImage 节点 ID。"""
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            return node_id
    return None


def _coerce_value(value_str: str, existing: Any) -> Any:
    """把命令行传入的字符串按原值类型转换（保持 int/float/bool 语义）。"""
    if isinstance(existing, bool):
        return value_str.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(existing, int) and not isinstance(existing, bool):
        try:
            return int(value_str)
        except ValueError:
            pass
    if isinstance(existing, float):
        try:
            return float(value_str)
        except ValueError:
            pass
    # 没有原值可参考：自行推断类型
    for caster in (int, float):
        try:
            return caster(value_str)
        except ValueError:
            continue
    lowered = value_str.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    return value_str


def _apply_overrides(workflow: dict[str, Any], set_args: list[str]) -> list[str]:
    """应用 --set nodeId:field=value 覆盖，返回可读的变更记录。"""
    changes: list[str] = []
    for spec in set_args:
        if ":" not in spec or "=" not in spec.split(":", 1)[1]:
            raise RhCliError("INVALID_SET", f"--set 格式应为 nodeId:field=value，收到：{spec}")
        node_id, rest = spec.split(":", 1)
        field, value_str = rest.split("=", 1)
        node_id, field = node_id.strip(), field.strip()
        node = workflow.get(node_id)
        if not isinstance(node, dict):
            raise RhCliError("INVALID_SET", f"节点 {node_id} 不存在于工作流中。")
        inputs = node.setdefault("inputs", {})
        old = inputs.get(field)
        inputs[field] = _coerce_value(value_str, old)
        changes.append(f"{node_id}.{field}: {old!r} → {inputs[field]!r}")
    return changes


def _upload_image(client: RhHttpClient, api_key: str, image_path: Path) -> str:
    response = client.upload_form(
        UPLOAD_URL,
        str(image_path),
        data={},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if response.get("code") != 0:
        raise RhCliError("UPLOAD_FAILED", f"图片上传失败：{response.get('msg', response)}", detail=response)
    file_name = response.get("data", {}).get("fileName")
    if not file_name:
        raise RhCliError("UPLOAD_FAILED", "上传成功但响应中没有 fileName。", detail=response)
    return str(file_name)


def _submit(client: RhHttpClient, api_key: str, workflow_id: str, workflow_json: str) -> str:
    payload = {"apiKey": api_key, "workflowId": workflow_id, "workflow": workflow_json}
    delay = 10
    for attempt in range(5):
        response = client.post_json(CREATE_URL, payload)
        code = response.get("code")
        if code == 0:
            task_id = response.get("data", {}).get("taskId")
            if not task_id:
                raise RhCliError("SUBMIT_FAILED", "提交成功但响应中没有 taskId。", detail=response)
            return str(task_id)
        if code == 421 and attempt < 4:  # TASK_QUEUE_MAXED：队列已满，退避重试
            time.sleep(delay)
            delay = int(delay * 1.5)
            continue
        raise RhCliError("SUBMIT_FAILED", f"任务提交失败：{response.get('msg', response)}", detail=response)
    raise RhCliError("SUBMIT_FAILED", "队列持续繁忙，多次重试后仍失败。")


def _poll_outputs(
    client: RhHttpClient,
    api_key: str,
    task_id: str,
    *,
    max_seconds: int,
    interval: int,
    on_tick: Callable[[int, str], None] | None = None,
) -> list[dict[str, Any]]:
    elapsed = 0
    while elapsed < max_seconds:
        time.sleep(interval)
        elapsed += interval
        response = client.post_json(OUTPUTS_URL, {"apiKey": api_key, "taskId": task_id})
        code = response.get("code")
        if code == 0:
            data = response.get("data", [])
            if isinstance(data, list) and data:
                return data
            if on_tick:
                on_tick(elapsed, "WAITING_OUTPUT")
        elif code == 804:  # RUNNING
            if on_tick:
                on_tick(elapsed, "RUNNING")
        elif code == 813:  # QUEUED
            if on_tick:
                on_tick(elapsed, "QUEUED")
        elif code == 805:  # FAILED
            raise RhCliError("TASK_FAILED", f"任务执行失败：{response.get('msg', 'Unknown')}", detail=response)
        else:
            if on_tick:
                on_tick(elapsed, f"CODE_{code}")
    raise RhCliError("TASK_TIMEOUT", f"任务超过 {max_seconds}s 仍未完成，taskId={task_id}（可稍后手动查询）。")


def run_workflow(
    *,
    api_key_arg: str | None,
    workflow_file: str,
    workflow_id: str,
    input_image: str | None,
    load_image_node: str | None,
    output: str | None,
    output_dir: Path | None,
    set_args: list[str] | None = None,
    on_override: Callable[[list[str]], None] | None = None,
    on_tick: Callable[[int, str], None] | None = None,
    max_seconds: int = MAX_POLL_SECONDS,
    interval: int = POLL_INTERVAL_SECONDS,
) -> RunResult:
    resolved = require_api_key(api_key_arg)
    assert resolved.value is not None
    api_key = resolved.value

    wf_path = Path(workflow_file).expanduser()
    if not wf_path.exists():
        raise RhCliError("FILE_NOT_FOUND", f"工作流文件不存在：{wf_path}")
    try:
        workflow = json.loads(wf_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RhCliError("INVALID_WORKFLOW", f"无法解析工作流 JSON：{wf_path}") from exc
    if not isinstance(workflow, dict):
        raise RhCliError("INVALID_WORKFLOW", "工作流 JSON 顶层必须是节点字典（API 格式导出）。")

    if set_args:
        changes = _apply_overrides(workflow, set_args)
        if on_override:
            on_override(changes)

    with RhHttpClient(api_key) as client:
        if input_image:
            img_path = Path(input_image).expanduser()
            if not img_path.exists():
                raise RhCliError("FILE_NOT_FOUND", f"输入图片不存在：{img_path}")
            uploaded = _upload_image(client, api_key, img_path)
            node_id = load_image_node or find_load_image_node(workflow)
            if node_id and node_id in workflow:
                workflow[node_id].setdefault("inputs", {})["image"] = uploaded
            else:
                raise RhCliError(
                    "NO_LOAD_IMAGE",
                    "提供了输入图片，但工作流里找不到 LoadImage 节点；可用 --load-image-node 手动指定。",
                )

        task_id = _submit(client, api_key, workflow_id, json.dumps(workflow))
        outputs = _poll_outputs(
            client, api_key, task_id, max_seconds=max_seconds, interval=interval, on_tick=on_tick
        )

        file_items = [item for item in outputs if item.get("fileUrl")]
        files: list[str] = []
        for index, item in enumerate(file_items, start=1):
            url = str(item["fileUrl"])
            ext = str(item.get("fileType") or "png")
            path = resolve_output_path(
                output,
                output_dir=output_dir,
                default_name=f"workflow_result.{ext}",
                ext=ext,
                index=index if len(file_items) > 1 else None,
            )
            client.download(url, str(path))
            files.append(str(path.resolve()))

    return RunResult(files=files, texts=[], task_id=task_id)
