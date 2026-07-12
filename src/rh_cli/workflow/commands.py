from __future__ import annotations

import typer

from rh_cli.config import resolve_workflow_id
from rh_cli.output import print_result
from rh_cli.state import CliState
from rh_cli.workflow.client import run_workflow


app = typer.Typer(help="运行原始 ComfyUI 工作流 JSON（/task/openapi/create）。")


def _state(ctx: typer.Context) -> CliState:
    return ctx.obj if isinstance(ctx.obj, CliState) else CliState()


@app.command("run")
def run_command(
    ctx: typer.Context,
    workflow_file: str = typer.Argument(..., help="ComfyUI 工作流 JSON 文件路径（API 格式导出）。"),
    workflow_id: str | None = typer.Option(None, "--workflow-id", "-w", help="RunningHub workflowId（工作流页面 URL 末尾的数字）。缺省从配置读取。"),
    input_image: str | None = typer.Option(None, "--input", "-i", help="输入图片路径，自动注入 LoadImage 节点。"),
    load_image_node: str | None = typer.Option(None, "--load-image-node", help="手动指定 LoadImage 节点 ID，默认自动检测。"),
    set_args: list[str] = typer.Option([], "--set", help="覆盖节点参数：nodeId:field=value，可重复（如 9:denoise=0.4）。"),
    encrypt: bool = typer.Option(False, "--encrypt", help="在 SaveImage 前插入鸭鸭图加密节点，下载后本地自动解密为真图。"),
    password: str = typer.Option("", "--password", help="鸭鸭图加密/解密密码（两端须一致）。"),
    title: str = typer.Option("", "--title", help="鸭鸭图标题（可选）。"),
    decoder: str | None = typer.Option(None, "--decoder", help="macOS-duck-decoder 路径，默认自动查找。"),
    instance_type: str = typer.Option("", "--instance-type", help="GPU 实例类型（如 plus 对应 48GB）。缺省为平台默认。"),
    output: str | None = typer.Option(None, "--output", "-o", help="输出文件或目录。"),
) -> None:
    state = _state(ctx)

    # 按优先级解析 workflowId：-w > 文件名映射 > 默认
    resolved_wf_id, wf_source = resolve_workflow_id(workflow_file, workflow_id)
    state.out.print(f"[dim]workflowId：{resolved_wf_id}（{wf_source}）[/dim]")

    state.out.print("提交 ComfyUI 工作流，复杂工作流可能需要几分钟。")

    def on_override(changes: list[str]) -> None:
        for change in changes:
            state.out.print(f"[cyan]覆盖参数[/cyan] {change}")

    def on_encrypt(injected: list[str]) -> None:
        for entry in injected:
            state.out.print(f"[magenta]插入加密节点[/magenta] {entry}")

    def on_decode(real_path: str, duck_path: str) -> None:
        state.out.print(f"[green]已本地解密[/green] {real_path}")
        state.out.print(f"[dim]（鸭子图保留于 {duck_path}）[/dim]")

    with state.out.status("上传素材并提交任务...", spinner="dots") as status:

        def on_tick(elapsed: int, phase: str) -> None:
            status.update(f"任务进行中（{phase}，{elapsed}s）...")

        result = run_workflow(
            api_key_arg=state.api_key,
            key_name=state.key_name,
            workflow_file=workflow_file,
            workflow_id=resolved_wf_id,
            input_image=input_image,
            load_image_node=load_image_node,
            output=output,
            output_dir=state.output_dir,
            set_args=set_args,
            encrypt=encrypt,
            password=password,
            title=title,
            decoder=decoder,
            instance_type=instance_type,
            site=state.site,
            on_override=on_override,
            on_encrypt=on_encrypt,
            on_decode=on_decode,
            on_tick=on_tick,
        )
    print_result(state.out, result, json_output=state.json_output)
