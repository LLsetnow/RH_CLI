from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from . import __version__
from .account import check_account
from .app.commands import app as app_commands
from .config import config_path, read_config, resolve_api_key, write_config
from .errors import RhCliError
from .model.commands import image_command, model_app, video_command
from .state import CliState
from .workflow.commands import app as workflow_commands


console = Console()
app = typer.Typer(help="RH CLI：RunningHub 标准模型与 AI 应用命令行工具。", no_args_is_help=True)
auth_app = typer.Typer(help="管理 RunningHub API Key 和 CLI 配置。")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"rh-cli {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    api_key: str | None = typer.Option(None, "--api-key", help="临时指定 RunningHub API Key。"),
    key_name: str | None = typer.Option(None, "--key", "-k", help="使用配置中的命名 Key（如 cn-rh, ai-wallet）。"),
    output_dir: Path | None = typer.Option(None, "--output-dir", help="默认输出目录。"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON。"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="打印更多调试信息。"),
    site: str = typer.Option("cn", "--site", help="目标站点：cn (runninghub.cn) 或 ai (runninghub.ai)。默认 cn。"),
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True, help="显示版本。"),
) -> None:
    # 根据 key 名称自动推断站点
    effective_site = site
    if key_name and site == "cn":  # site 是默认值才自动推断
        if key_name.startswith("ai-"):
            effective_site = "ai"
        elif key_name.startswith("cn-"):
            effective_site = "cn"

    ctx.obj = CliState(
        api_key=api_key,
        key_name=key_name,
        output_dir=output_dir,
        json_output=json_output,
        verbose=verbose,
        site=effective_site,
        console=console,
    )


@app.command("check")
def check(ctx: typer.Context) -> None:
    state = ctx.obj if isinstance(ctx.obj, CliState) else CliState(console=console)
    result = check_account(state.api_key, state.key_name)
    if state.json_output:
        state.out.print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    status = result.get("status")
    if status == "ready":
        sites = result.get("sites", [])
        parts = [f"\nRunningHub 已就绪，Key 来源：{result.get('key_source')}"]
        for s in sites:
            coin = s.get("remainCoins", "0")
            money = s.get("remainMoney", "0")
            cur = s.get("currency", "")
            parts.append(f"  {s['site']}.runninghub: {coin} RH币" + (f" + ${money} {cur}" if money != "0" else ""))
        state.out.print("[green]" + "\n".join(parts) + "[/green]")
    elif status == "no_balance":
        state.out.print(f"[yellow]API Key 可用，但余额为 0。[/yellow]请充值后再运行生成任务。")
    elif status == "no_key":
        state.out.print("[red]还没有配置 API Key。[/red]运行 `rh auth set-key` 或设置 RUNNINGHUB_API_KEY。")
    else:
        state.out.print(f"[red]API Key 校验失败：[/red]{result.get('message')}")


@auth_app.command("set-key")
def set_key(
    api_key: str = typer.Argument(..., help="RunningHub API Key。"),
    name: str | None = typer.Option(None, "--name", "-n", help="Key 名称（如 cn-rh, ai-wallet）。不传则设为旧版默认 Key。"),
) -> None:
    if name:
        from rh_cli.config import list_keys, save_keys, get_default_key_name
        keys = list_keys()
        keys[name] = api_key
        default = get_default_key_name() or name
        path = save_keys(keys, default)
        console.print(f"[green]已保存 Key[/green] {name} → {api_key[:8]}... 到 {path}")
    else:
        cfg = read_config()
        cfg["api_key"] = api_key
        path = write_config(cfg)
        console.print(f"[green]已保存 API Key 到[/green] {path}")


@auth_app.command("keys")
def auth_keys(
    action: str = typer.Argument("list", help="list | use | rm"),
    name: str | None = typer.Argument(None, help="Key 名称。"),
) -> None:
    from rh_cli.config import list_keys, save_keys, get_default_key_name
    keys = list_keys()
    if action == "list":
        if not keys:
            console.print("[dim]尚未配置命名 Key。[/dim]")
            return
        default = get_default_key_name()
        for k, v in keys.items():
            mark = " [green]← 默认[/green]" if k == default else ""
            console.print(f"  {k}: {v[:8]}...{mark}")
    elif action == "use":
        if not name or name not in keys:
            console.print(f"[red]未知的 Key 名称：{name}。用 `rh auth keys list` 查看。[/red]")
            return
        path = save_keys(keys, name)
        console.print(f"[green]已切换到[/green] {name} ({keys[name][:8]}...)")
    elif action == "rm":
        if not name or name not in keys:
            console.print(f"[red]未知的 Key 名称：{name}。[/red]")
            return
        del keys[name]
        default = get_default_key_name()
        if default == name:
            default = next(iter(keys), "")
        path = save_keys(keys, default)
        console.print(f"[green]已删除[/green] {name}")


@auth_app.command("show")
def show_auth() -> None:
    resolved = resolve_api_key()
    cfg_path = config_path()
    if resolved.value:
        console.print(f"API Key 来源：{resolved.source}，当前 Key：{resolved.value[:4]}****")
    else:
        console.print("尚未配置 API Key。")
    console.print(f"配置文件：{cfg_path}")


@auth_app.command("set-output-dir")
def set_output_dir(output_dir: Path = typer.Argument(..., help="默认输出目录。")) -> None:
    cfg = read_config()
    cfg["output_dir"] = str(output_dir.expanduser())
    path = write_config(cfg)
    console.print(f"[green]已保存默认输出目录到[/green] {path}")


@auth_app.command("set-workflow-id")
def set_workflow_id(
    workflow_id: str = typer.Argument(..., help="RunningHub workflowId（工作流页面 URL 末尾的数字）。"),
    filename: str | None = typer.Argument(None, help="关联的 JSON 文件名（可选）。传此参数则存入映射表，不传则设为默认。"),
) -> None:
    from rh_cli.config import save_workflow_id
    path = save_workflow_id(workflow_id, filename)
    if filename:
        console.print(f"[green]已保存 workflowId 映射[/green] {filename} → {workflow_id} 到 {path}")
    else:
        console.print(f"[green]已保存默认 workflowId 到[/green] {path}")


@auth_app.command("browser-token")
def set_browser_token(
    rh_comfy_auth: str = typer.Argument(..., help="浏览器 localStorage 中的 Rh-Comfy-Auth 值。"),
    rh_identify: str = typer.Argument(..., help="浏览器 localStorage 中的 Rh-Identify 值。"),
    site: str = typer.Option("ai", "--site", help="站点：ai 或 cn。"),
) -> None:
    """设置浏览器 session token（从 localStorage 获取）。"""
    from rh_cli.config import read_config, write_config
    cfg = read_config()
    browser = cfg.setdefault("browser_tokens", {})
    if not isinstance(browser, dict):
        browser = {}
    browser[site] = {"rh_comfy_auth": rh_comfy_auth, "rh_identify": rh_identify}
    cfg["browser_tokens"] = browser
    path = write_config(cfg)
    console.print(f"[green]已保存 {site} 站点浏览器 token 到[/green] {path}")
    console.print("[dim]提示：token 有过期时间。报 401 时重新获取。[/dim]")


app.add_typer(auth_app, name="auth")
app.add_typer(model_app, name="model")
app.command("image")(image_command)
app.command("video")(video_command)
app.add_typer(app_commands, name="app")
app.add_typer(workflow_commands, name="workflow")

from rh_cli.browser.commands import app as browser_app
app.add_typer(browser_app, name="browser")


def run() -> None:
    try:
        app()
    except RhCliError as exc:
        console.print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        raise typer.Exit(exc.exit_code) from exc


if __name__ == "__main__":
    run()
