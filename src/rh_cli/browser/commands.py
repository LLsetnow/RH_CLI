"""浏览器模式 CLI 命令 ── `rh browser run`。"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from rh_cli.config import resolve_workflow_id, require_api_key
from rh_cli.errors import RhCliError
from rh_cli.output import RunResult
from rh_cli.state import CliState

from .client import run_browser

app = typer.Typer(help="浏览器模式：使用 session token 和 /task/forward 端点。")


def _state(ctx: typer.Context) -> CliState:
    return ctx.obj if isinstance(ctx.obj, CliState) else CliState()


@app.command("run")
def browser_run(
    ctx: typer.Context,
    workflow_file: str = typer.Argument(..., help="ComfyUI 工作流 JSON（API 格式导出）。"),
    workflow_id: str | None = typer.Option(None, "--workflow-id", "-w", help="workflowId，缺省从配置按文件名匹配。"),
    input_image: str | None = typer.Option(None, "--input", "-i", help="输入图片或视频路径。自动注入 LoadImage/VHS_LoadVideo。"),
    instance_type: str = typer.Option("", "--instance-type", help="实例类型（如 plus）。"),
    set_args: list[str] = typer.Option([], "--set", help="覆盖节点参数：nodeId:field=value"),
    output: str | None = typer.Option(None, "--output", "-o", help="输出文件路径。"),
) -> None:
    """浏览器模式运行工作流。使用 session token 提交到 /task/forward。"""
    state = _state(ctx)

    resolved_wf_id, wf_source = resolve_workflow_id(workflow_file, workflow_id)
    state.out.print(f"[dim]workflowId：{resolved_wf_id}（{wf_source}）[/dim]")

    resolved = require_api_key(state.api_key, state.key_name)
    assert resolved.value is not None

    state.out.print("浏览器模式提交，复杂工作流可能需要几分钟。")

    try:
        result = run_browser(
            api_key=resolved.value,
            site=state.site,
            workflow_file=workflow_file,
            workflow_id=resolved_wf_id,
            input_image=input_image,
            output=output,
            instance_type=instance_type,
        )
    except RhCliError:
        raise

    state.out.print(json.dumps({"files": result["files"], "task_id": result["task_id"]}, indent=2))
