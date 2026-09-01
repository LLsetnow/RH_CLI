from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rh_cli.config import require_api_key
from rh_cli.errors import RhCliError
from rh_cli.http import BASE_URL_CN, BASE_URL_AI, API_HOST_CN, API_HOST_AI, RhHttpClient, get_site_config
from rh_cli.media import upload_app_file
from rh_cli.output import RunResult, resolve_output_path


def _site_urls(site: str = "cn") -> tuple[str, str, str]:
    """返回 (upload_url, create_url, outputs_url) 三元组。"""
    cfg = get_site_config(site)
    api_host = cfg["api_host"]
    base_url = cfg["base_url"]
    return (
        f"{base_url}/media/upload/binary",
        f"{api_host}/task/openapi/create",
        f"{api_host}/task/openapi/outputs",
    )


def _site_cancel_url(site: str = "cn") -> str:
    cfg = get_site_config(site)
    return f"{cfg['api_host']}/task/openapi/cancel"


def cancel_task(client: RhHttpClient, api_key: str, task_id: str, cancel_url: str = "") -> None:
    """向 RunningHub 发送取消请求（best-effort，失败时静默）。"""
    url = cancel_url or CANCEL_URL
    try:
        client.post_json(url, {"apiKey": api_key, "taskId": task_id})
    except Exception:
        pass


# 默认 URL（向后兼容）
UPLOAD_URL = f"{BASE_URL_CN}/media/upload/binary"
CREATE_URL = f"{API_HOST_CN}/task/openapi/create"
OUTPUTS_URL = f"{API_HOST_CN}/task/openapi/outputs"
CANCEL_URL = f"{API_HOST_CN}/task/openapi/cancel"

MAX_POLL_SECONDS = 1200
POLL_INTERVAL_SECONDS = 5
# 轮询期间允许的最大「连续」瞬时网络错误次数，超过才判定为真的连不上。
MAX_POLL_NET_FAILS = 20

# SS_tools 鸭鸭图加密节点（copyangle/SS_tools）：把真实图片隐写进一张鸭子图。
DUCK_ENCODE_CLASS = "DuckHideNode"


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


def _next_node_id(workflow: dict[str, Any]) -> str:
    """生成一个不与现有节点冲突的新节点 ID。"""
    max_id = 0
    for key in workflow:
        try:
            max_id = max(max_id, int(key))
        except (TypeError, ValueError):
            continue
    return str(max_id + 1)


def _inject_duck_encode(workflow: dict[str, Any], *, password: str, title: str) -> list[str]:
    """在每个 SaveImage 前插入 DuckHideNode（鸭鸭图加密），返回变更记录。

    把原本喂给 SaveImage 的图像改接到新节点的 images 输入，再让 SaveImage
    读取新节点的输出——于是 SaveImage 存下来的是隐写后的鸭子图。
    """
    save_nodes = [
        nid for nid, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == "SaveImage"
    ]
    if not save_nodes:
        raise RhCliError("NO_SAVE_IMAGE", "工作流里找不到 SaveImage 节点，无法插入加密节点。")
    injected: list[str] = []
    for save_id in save_nodes:
        save_inputs = workflow[save_id].setdefault("inputs", {})
        source = save_inputs.get("images")
        if not (isinstance(source, list) and len(source) == 2):
            continue
        new_id = _next_node_id(workflow)
        workflow[new_id] = {
            "inputs": {
                "password": password,
                "title": title,
                "fps": 16,
                "compress": 2,
                "combine_video": True,
                "images": source,
            },
            "class_type": DUCK_ENCODE_CLASS,
            "_meta": {"title": "鸭鸭图加密"},
        }
        save_inputs["images"] = [new_id, 0]
        injected.append(f"{new_id}(DuckHideNode) → {save_id}(SaveImage)")
    if not injected:
        raise RhCliError(
            "NO_SAVE_IMAGE",
            "SaveImage 的 images 输入未连到节点输出，无法插入加密节点。",
        )
    return injected


def _resolve_decoder(explicit: str | None) -> Path:
    """定位 macOS-duck-decoder：显式路径 > 当前目录 SS_tools > 仓库根 SS_tools。"""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise RhCliError("DECODER_NOT_FOUND", f"解码器不存在：{path}")
        return path
    candidates = [
        Path.cwd() / "SS_tools" / "macOS-duck-decoder",
        Path(__file__).resolve().parents[3] / "SS_tools" / "macOS-duck-decoder",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise RhCliError(
        "DECODER_NOT_FOUND",
        "找不到 macOS-duck-decoder；用 --decoder 指定路径，或在仓库根目录运行。",
    )


def _decode_duck(decoder: Path, duck_path: Path, out_path: Path, password: str) -> None:
    """用 macOS-duck-decoder 把鸭子图解回真图。"""
    cmd = [str(decoder), "--duck", str(duck_path), "--out", str(out_path)]
    if password:
        cmd += ["--password", password]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or "Unknown error"
        raise RhCliError("DECODE_FAILED", f"鸭鸭图解密失败：{err}")
    if not out_path.exists():
        raise RhCliError("DECODE_FAILED", f"解密未生成输出文件：{out_path}")


def _upload_file(client: RhHttpClient, api_key: str, file_path: Path, upload_url: str = "") -> str:
    url = upload_url or UPLOAD_URL
    response = client.upload_form(
        url,
        str(file_path),
        data={},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if response.get("code") != 0:
        raise RhCliError("UPLOAD_FAILED", f"文件上传失败：{response.get('msg', response)}", detail=response)
    file_name = response.get("data", {}).get("fileName")
    if not file_name:
        raise RhCliError("UPLOAD_FAILED", "上传成功但响应中没有 fileName。", detail=response)
    return str(file_name)


def _parse_file_arg(spec: str) -> tuple[str, str, Path]:
    """解析 nodeId:fieldName=path，并返回节点、字段和本地路径。"""
    if ":" not in spec or "=" not in spec.split(":", 1)[1]:
        raise RhCliError("INVALID_FILE_ARG", f"--file 格式应为 nodeId:fieldName=路径，收到：{spec}")
    node_id, rest = spec.split(":", 1)
    field_name, file_path = rest.split("=", 1)
    node_id, field_name, file_path = node_id.strip(), field_name.strip(), file_path.strip()
    if not node_id or not field_name or not file_path:
        raise RhCliError("INVALID_FILE_ARG", f"--file 格式应为 nodeId:fieldName=路径，收到：{spec}")
    return node_id, field_name, Path(file_path).expanduser()


def _apply_file_args(
    client: RhHttpClient,
    workflow: dict[str, Any],
    file_args: list[str],
    upload_url: str = "",
) -> list[str]:
    """上传并注入任意工作流输入文件，返回可读的变更记录。"""
    parsed: list[tuple[str, str, Path, dict[str, Any]]] = []
    for spec in file_args:
        node_id, field_name, file_path = _parse_file_arg(spec)
        node = workflow.get(node_id)
        if not isinstance(node, dict):
            raise RhCliError("INVALID_FILE_ARG", f"节点 {node_id} 不存在于工作流中。")
        if not file_path.exists() or not file_path.is_file():
            raise RhCliError("FILE_NOT_FOUND", f"输入文件不存在：{file_path}")
        parsed.append((node_id, field_name, file_path, node))

    changes: list[str] = []
    for node_id, field_name, file_path, node in parsed:
        uploaded = upload_app_file(client, file_path, upload_url=upload_url)
        inputs = node.setdefault("inputs", {})
        old = inputs.get(field_name)
        inputs[field_name] = uploaded
        changes.append(f"{node_id}.{field_name}: {old!r} → {uploaded!r}（{file_path.name}）")
    return changes


def _validate_api_workflow(workflow: dict[str, Any]) -> None:
    """校验 ComfyUI API 工作流的最小结构，不限制具体节点类型。"""
    if not workflow:
        raise RhCliError("INVALID_WORKFLOW", "工作流 JSON 不能为空。")
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            raise RhCliError("INVALID_WORKFLOW", f"节点 {node_id} 必须是对象。")
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type.strip():
            raise RhCliError("INVALID_WORKFLOW", f"节点 {node_id} 缺少有效的 class_type。")
        if not isinstance(node.get("inputs"), dict):
            raise RhCliError("INVALID_WORKFLOW", f"节点 {node_id} 缺少有效的 inputs 对象。")


def _check_prompt_tips(response: dict[str, Any]) -> None:
    """将 RunningHub 提交时返回的节点校验错误提前转换成 CLI 错误。"""
    data = response.get("data")
    if not isinstance(data, dict):
        return
    prompt_tips = data.get("promptTips")
    if isinstance(prompt_tips, str):
        try:
            prompt_tips = json.loads(prompt_tips)
        except ValueError:
            return
    if not isinstance(prompt_tips, dict):
        return
    node_errors = prompt_tips.get("node_errors")
    if prompt_tips.get("result") is False or node_errors:
        task_id = data.get("taskId")
        suffix = f"，taskId={task_id}" if task_id else ""
        raise RhCliError(
            "NODE_ERRORS",
            f"工作流节点校验失败{suffix}。",
            detail=prompt_tips,
        )


def _submit(
    client: RhHttpClient,
    api_key: str,
    workflow_id: str | None,
    workflow_json: str,
    instance_type: str = "",
    create_url: str = "",
    *,
    access_password: str | None = None,
    webhook_url: str | None = None,
    retain_seconds: int | None = None,
    use_personal_queue: bool = False,
    add_metadata: bool | None = None,
) -> str:
    url = create_url or CREATE_URL
    payload: dict[str, Any] = {"apiKey": api_key, "workflow": workflow_json}
    if workflow_id:
        payload["workflowId"] = workflow_id
    if instance_type:
        payload["instanceType"] = instance_type
    if access_password:
        payload["accessPassword"] = access_password
    if webhook_url:
        payload["webhookUrl"] = webhook_url
    if retain_seconds is not None:
        if not 10 <= retain_seconds <= 180:
            raise RhCliError("INVALID_RETAIN_SECONDS", "--retain-seconds 必须在 10 到 180 秒之间。")
        payload["retainSeconds"] = retain_seconds
    if use_personal_queue:
        payload["usePersonalQueue"] = True
    if add_metadata is not None:
        payload["addMetadata"] = add_metadata
    delay = 10
    for attempt in range(5):
        response = client.post_json(url, payload)
        code = response.get("code")
        if code == 0:
            _check_prompt_tips(response)
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
    outputs_url: str = "",
    cancel_event: threading.Event | None = None,
    cancel_url: str = "",
) -> list[dict[str, Any]]:
    url = outputs_url or OUTPUTS_URL
    elapsed = 0
    net_fails = 0  # 连续瞬时网络错误计数：单次抖断不该丢掉正在跑的任务
    while elapsed < max_seconds:
        if cancel_event is not None and cancel_event.is_set():
            cancel_task(client, api_key, task_id, cancel_url)
            raise RhCliError("TASK_CANCELLED", f"任务已取消，taskId={task_id}")
        time.sleep(interval)
        elapsed += interval
        if cancel_event is not None and cancel_event.is_set():
            cancel_task(client, api_key, task_id, cancel_url)
            raise RhCliError("TASK_CANCELLED", f"任务已取消，taskId={task_id}")
        try:
            response = client.post_json(url, {"apiKey": api_key, "taskId": task_id})
        except RhCliError as exc:
            # 鉴权/余额类错误无法自愈，立即失败；其余（TLS 抖断、超时、5xx 等）自动重试，
            # 因为任务多半仍在服务端运行，一次瞬断不该让整条命令崩掉。
            if exc.code in ("AUTH_FAILED", "INSUFFICIENT_BALANCE"):
                raise
            net_fails += 1
            if net_fails > MAX_POLL_NET_FAILS:
                raise RhCliError(
                    "TASK_POLL_ERROR",
                    f"轮询连续失败 {net_fails} 次仍无法恢复，taskId={task_id}"
                    f"（任务可能仍在运行，可稍后凭该 taskId 手动查询结果）。原因：{exc.message}",
                    detail=exc.detail,
                ) from exc
            if on_tick:
                on_tick(elapsed, f"NET_RETRY({net_fails})")
            continue
        net_fails = 0  # 一旦成功拿到响应就清零连续失败计数
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


def _extract_task_cost(outputs: list[dict[str, Any]]) -> tuple[str | None, str | None, str | None]:
    """从首个工作流输出中提取单项费用，优先 RH 币、金额兜底。"""
    if not outputs:
        return (None, None, None)
    usage = outputs[0] if isinstance(outputs[0], dict) else {}
    duration = usage.get("taskCostTime")
    for field, cost_type in (("consumeCoins", "coins"), ("consumeMoney", "money")):
        value = usage.get(field)
        if value is not None and str(value).strip():
            return (cost_type, str(value), duration)
    return (None, None, duration)


def run_workflow(
    *,
    api_key_arg: str | None,
    key_name: str | None = None,
    workflow_file: str,
    workflow_id: str | None,
    input_image: str | None,
    load_image_node: str | None,
    file_args: list[str] | None = None,
    output: str | None,
    output_dir: Path | None,
    set_args: list[str] | None = None,
    encrypt: bool = False,
    password: str = "",
    title: str = "",
    decoder: str | None = None,
    instance_type: str = "",
    access_password: str | None = None,
    webhook_url: str | None = None,
    retain_seconds: int | None = None,
    use_personal_queue: bool = False,
    add_metadata: bool | None = None,
    site: str = "cn",
    on_override: Callable[[list[str]], None] | None = None,
    on_file: Callable[[list[str]], None] | None = None,
    on_encrypt: Callable[[list[str]], None] | None = None,
    on_decode: Callable[[str, str], None] | None = None,
    on_tick: Callable[[int, str], None] | None = None,
    max_seconds: int = MAX_POLL_SECONDS,
    interval: int = POLL_INTERVAL_SECONDS,
    cancel_event: threading.Event | None = None,
) -> RunResult:
    resolved = require_api_key(api_key_arg, key_name)
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
    _validate_api_workflow(workflow)

    if set_args:
        changes = _apply_overrides(workflow, set_args)
        if on_override:
            on_override(changes)

    decoder_path: Path | None = None
    if encrypt:
        decoder_path = _resolve_decoder(decoder)  # 尽早失败：没有解码器就别提交任务
        injected = _inject_duck_encode(workflow, password=password, title=title)
        if on_encrypt:
            on_encrypt(injected)

    s_upload, s_create, s_outputs = _site_urls(site)

    # AI 站点直连，绕过 SOCKS 代理
    no_proxy = "runninghub.ai" if site == "ai" else ""
    with RhHttpClient(api_key, no_proxy_host=no_proxy) as client:
        if input_image:
            img_path = Path(input_image).expanduser()
            if not img_path.exists():
                raise RhCliError("FILE_NOT_FOUND", f"输入图片不存在：{img_path}")
            uploaded = _upload_file(client, api_key, img_path, s_upload)
            node_id = load_image_node or find_load_image_node(workflow)
            if node_id and node_id in workflow:
                workflow[node_id].setdefault("inputs", {})["image"] = uploaded
            else:
                raise RhCliError(
                    "NO_LOAD_IMAGE",
                    "提供了输入图片，但工作流里找不到 LoadImage 节点；可用 --load-image-node 手动指定。",
                )

        if file_args:
            workflow_upload_url = f"{get_site_config(site)['api_host']}/task/openapi/upload"
            changes = _apply_file_args(client, workflow, file_args, workflow_upload_url)
            if on_file:
                on_file(changes)

        task_id = _submit(
            client,
            api_key,
            workflow_id,
            json.dumps(workflow, ensure_ascii=False),
            instance_type,
            s_create,
            access_password=access_password,
            webhook_url=webhook_url,
            retain_seconds=retain_seconds,
            use_personal_queue=use_personal_queue,
            add_metadata=add_metadata,
        )
        s_cancel = _site_cancel_url(site)
        outputs = _poll_outputs(
            client, api_key, task_id, max_seconds=max_seconds, interval=interval,
            on_tick=on_tick, outputs_url=s_outputs,
            cancel_event=cancel_event, cancel_url=s_cancel,
        )
        cost_type, cost, duration = _extract_task_cost(outputs)

        file_items = [item for item in outputs if _output_file_url(item)]
        texts: list[str] = []
        for item in outputs:
            text = _output_text(item)
            if text is not None:
                texts.append(text)
        files: list[str] = []
        for index, item in enumerate(file_items, start=1):
            url = str(_output_file_url(item))
            ext = _normalise_output_ext(item.get("fileType"))
            final_path = resolve_output_path(
                output,
                output_dir=output_dir,
                default_name=f"workflow_result.{ext}",
                ext=ext,
                index=index if len(file_items) > 1 else None,
            )
            if encrypt:
                assert decoder_path is not None
                # 下载的是鸭子图，落地为 *.duck.*；本地解密后真图用干净文件名
                duck_path = final_path.with_name(f"{final_path.stem}.duck{final_path.suffix}")
                client.download(url, str(duck_path))
                _decode_duck(decoder_path, duck_path, final_path, password)
                if on_decode:
                    on_decode(str(final_path.resolve()), str(duck_path.resolve()))
                files.append(str(final_path.resolve()))
            else:
                client.download(url, str(final_path))
                files.append(str(final_path.resolve()))
    return RunResult(
        files=files,
        texts=texts,
        cost=cost,
        cost_type=cost_type,
        duration=duration,
        task_id=task_id,
    )


def _output_file_url(item: dict[str, Any]) -> str | None:
    """兼容工作流结果和其他 RunningHub 结果对象的文件 URL 字段。"""
    for key in ("fileUrl", "url", "outputUrl"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _output_text(item: dict[str, Any]) -> str | None:
    """提取文本结果；文件结果不重复作为文本输出。"""
    if _output_file_url(item):
        return None
    for key in ("text", "content"):
        value = item.get(key)
        if value is not None:
            return _stringify_output_value(value)
    value = item.get("output")
    if value is not None:
        return _stringify_output_value(value)
    if item:
        # 保留没有标准 fileUrl/text 字段的合法工作流输出，避免 CLI 静默丢弃结果。
        return json.dumps(item, ensure_ascii=False, default=str)
    return None


def _stringify_output_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _normalise_output_ext(file_type: Any) -> str:
    """把 fileType/mime type 统一为 resolve_output_path 可用的扩展名。"""
    value = str(file_type or "png").strip().lower().lstrip(".")
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    return {
        "audio": "mp3",
        "image": "png",
        "jpeg": "jpg",
        "quicktime": "mov",
        "string": "txt",
        "video": "mp4",
    }.get(value, value or "bin")
