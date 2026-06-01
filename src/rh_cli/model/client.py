from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from rh_cli.catalog import find_best_for_task, find_endpoint
from rh_cli.compat import fix_mov_to_mp4
from rh_cli.config import require_api_key
from rh_cli.errors import RhCliError
from rh_cli.http import BASE_URL, RhHttpClient
from rh_cli.output import RunResult, resolve_output_path
from rh_cli.poll import poll_task

from .payload import ModelRunInput, build_payload


def guess_ext(output_type: str) -> str:
    return {"image": "png", "video": "mp4", "audio": "mp3", "3d": "glb", "string": "txt"}.get(output_type, "bin")


def _extract_task_id(response: dict[str, Any]) -> str | None:
    task_id = response.get("taskId") or response.get("task_id")
    if not task_id and isinstance(response.get("data"), dict):
        task_id = response["data"].get("taskId") or response["data"].get("task_id")
    return str(task_id) if task_id else None


def _extract_usage(final: dict[str, Any]) -> tuple[str | None, Any | None]:
    usage = final.get("usage") or {}
    cost = usage.get("consumeMoney") or usage.get("thirdPartyConsumeMoney")
    duration = usage.get("taskCostTime")
    return (str(cost) if cost is not None else None, duration)


def execute_model(
    *,
    api_key_arg: str | None,
    endpoint: str | None,
    task: str | None,
    run_input: ModelRunInput,
    output: str | None,
    output_dir: Path | None,
    console: Console | None = None,
) -> RunResult:
    resolved = require_api_key(api_key_arg)
    assert resolved.value is not None

    if endpoint:
        endpoint_def = find_endpoint(endpoint)
        if not endpoint_def:
            raise RhCliError("ENDPOINT_NOT_FOUND", f"找不到端点：{endpoint}")
    elif task:
        endpoint_def = find_best_for_task(task)
        if not endpoint_def:
            raise RhCliError("ENDPOINT_NOT_FOUND", f"找不到 task 对应端点：{task}")
        if console:
            console.print(f"[dim]Auto-selected {endpoint_def['endpoint']}[/dim]")
    else:
        raise RhCliError("INVALID_COMMAND", "必须提供 --endpoint 或 --task。")

    with RhHttpClient(resolved.value) as client:
        payload = build_payload(client, endpoint_def, run_input)
        response = client.post_json(f"{BASE_URL}/{endpoint_def['endpoint']}", payload)
        task_id = _extract_task_id(response)
        if not task_id:
            raise RhCliError("SUBMIT_FAILED", "提交成功但响应中没有 taskId。", detail=response)

        if response.get("status") == "SUCCESS" and response.get("results"):
            final = response
        else:
            final = poll_task(client, task_id)

        results = final.get("results") or []
        if not results:
            raise RhCliError("TASK_FAILED", "任务完成但没有返回结果。", detail=final)

        cost, duration = _extract_usage(final)
        files: list[str] = []
        texts: list[str] = []
        file_index = 0
        for item in results:
            result_url = item.get("url") or item.get("outputUrl")
            if not result_url:
                text = item.get("text") or item.get("content") or item.get("output")
                if text:
                    texts.append(str(text))
                continue

            file_index += 1
            ext = str(item.get("outputType") or guess_ext(str(endpoint_def.get("output_type", ""))))
            path = resolve_output_path(
                output,
                output_dir=output_dir,
                default_name=f"result.{ext}",
                ext=ext,
                index=file_index if len(results) > 1 else None,
            )
            client.download(str(result_url), str(path))
            fix_mov_to_mp4(path)
            files.append(str(path.resolve()))

    if not files and not texts:
        raise RhCliError("TASK_FAILED", "结果中没有可下载文件或文本。", detail=results)
    return RunResult(files=files, texts=texts, cost=cost, duration=duration, task_id=task_id)
