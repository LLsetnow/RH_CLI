"""浏览器模式客户端 ── 完全独立的实现，不依赖 workflow/ 模块。

使用浏览器 session token（Rh-Comfy-Auth + Rh-Identify）和内部 /task/forward
端点提交任务。文件上传、轮询均为独立实现。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from rh_cli.config import check_browser_token_expiry, read_browser_tokens
from rh_cli.errors import RhCliError
from rh_cli.http import get_site_config

MAX_POLL_SECONDS = 1200
POLL_INTERVAL_SECONDS = 5


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _find_load_image_node(workflow: dict[str, Any]) -> str | None:
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            return node_id
    return None


def _find_vhs_load_video_node(workflow: dict[str, Any]) -> str | None:
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == "VHS_LoadVideo":
            return node_id
    return None


# ── HTTP 传输层（直连，不使用 SOCKS 代理）────────────────────────────────────

def _make_client(site: str) -> httpx.Client:
    """创建 HTTP 客户端，AI 站点直连不走代理。"""
    import os
    no_proxy = "runninghub.ai" if site == "ai" else ""
    env = os.environ.copy()
    if no_proxy and no_proxy not in env.get("NO_PROXY", ""):
        env["NO_PROXY"] = f"{env.get('NO_PROXY', '')},{no_proxy}".strip(",")
    return httpx.Client(timeout=60.0, follow_redirects=True)


def _upload_file(client: httpx.Client, site: str, api_key: str, image_path: str) -> str:
    """上传文件到 RunningHub，返回服务端 fileName。"""
    cfg = get_site_config(site)
    url = f"{cfg['base_url']}/media/upload/binary"
    with open(image_path, "rb") as fh:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": fh},
            timeout=120.0,
        )
    data = resp.json()
    if data.get("code") != 0:
        raise RhCliError("UPLOAD_FAILED", f"上传失败：{data.get('msg', data)}")
    filename = data["data"]["fileName"]
    return str(filename)


# ── 浏览器模式提交 ────────────────────────────────────────────────────────────

def _submit_forward(
    client: httpx.Client,
    site: str,
    rh_auth: str,
    rh_id: str,
    workflow_json: str,
) -> str:
    """通过 /task/forward 提交，返回 taskId。"""
    cfg = get_site_config(site)
    url = f"{cfg['api_host']}/task/forward"
    params = {
        "Rh-Comfy-Auth": rh_auth,
        "Rh-Identify": rh_id,
    }
    try:
        resp = client.post(url, params=params, json={"workflow": workflow_json})
    except Exception as exc:
        raise RhCliError("API_ERROR", f"forward 提交失败：{exc}") from exc

    if resp.status_code >= 400:
        msg = resp.text[:200]
        try:
            msg = resp.json().get("msg", msg)
        except Exception:
            pass
        raise RhCliError("SUBMIT_FAILED", f"forward [{resp.status_code}]：{msg}")

    try:
        data = resp.json()
    except Exception:
        raise RhCliError("SUBMIT_FAILED", f"forward 返回非 JSON：{resp.text[:200]}")

    code = data.get("code")
    if code == 0:
        task_id = data.get("data", {}).get("taskId")
        if task_id:
            return str(task_id)
    raise RhCliError("SUBMIT_FAILED", f"forward 返回异常：{data.get('msg', data)}")


# ── 轮询 ──────────────────────────────────────────────────────────────────────

def _poll_outputs(
    client: httpx.Client,
    site: str,
    api_key: str,
    task_id: str,
    *,
    max_seconds: int = MAX_POLL_SECONDS,
    interval: int = POLL_INTERVAL_SECONDS,
) -> list[dict[str, Any]]:
    """轮询任务输出。"""
    cfg = get_site_config(site)
    url = f"{cfg['api_host']}/task/openapi/outputs"
    elapsed = 0
    while elapsed < max_seconds:
        time.sleep(interval)
        elapsed += interval
        try:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"apiKey": api_key, "taskId": task_id},
            )
            data = resp.json()
        except Exception as exc:
            raise RhCliError("API_ERROR", f"轮询失败：{exc}") from exc

        code = data.get("code")
        if code == 0:
            outputs = data.get("data", [])
            if isinstance(outputs, list) and outputs:
                return outputs
        elif code == 804:  # RUNNING
            pass
        elif code == 813:  # QUEUED
            pass
        elif code == 805:  # FAILED
            raise RhCliError("TASK_FAILED", f"任务失败：{data.get('msg', '')}", detail=data)
    raise RhCliError("TASK_TIMEOUT", f"任务超时（{max_seconds}s），taskId={task_id}")


# ── 下载 ──────────────────────────────────────────────────────────────────────

def _download(client: httpx.Client, url: str, output_path: str) -> str:
    try:
        with client.stream("GET", url, timeout=300.0) as resp:
            resp.raise_for_status()
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
    except Exception as exc:
        raise RhCliError("DOWNLOAD_FAILED", f"下载失败：{exc}") from exc
    return output_path


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run_browser(
    *,
    api_key: str,
    site: str,
    workflow_file: str,
    workflow_id: str,
    input_image: str | None,
    output: str | None,
    set_args: list[str] | None = None,
    instance_type: str = "",
    max_seconds: int = MAX_POLL_SECONDS,
    interval: int = POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """浏览器模式主入口：上传 → 注入 → forward → 轮询 → 下载。

    返回 {"files": [...], "task_id": "..."}
    """

    # 1. 验证 token
    expired, remaining = check_browser_token_expiry(site)
    if expired:
        raise RhCliError(
            "TOKEN_EXPIRED",
            f"浏览器 token 已过期（{site} 站点，剩余 {remaining}s）。\n"
            "  F12 Console 获取新 token：\n"
            "  JSON.stringify({a:localStorage['Rh-Comfy-Auth'],i:localStorage['Rh-Identify']})\n"
            f"  rh auth browser-token <auth> <id> --site {site}",
        )
    tokens = read_browser_tokens(site)
    rh_auth = tokens.get("rh_comfy_auth", "")
    rh_id = tokens.get("rh_identify", "")
    if not rh_auth or not rh_id:
        raise RhCliError(
            "NO_BROWSER_TOKEN",
            "浏览器模式需要 session token。\n"
            f"  rh auth browser-token <auth> <id> --site {site}",
        )

    # 2. 加载工作流
    wf_path = Path(workflow_file).expanduser()
    if not wf_path.exists():
        raise RhCliError("FILE_NOT_FOUND", f"工作流不存在：{wf_path}")
    workflow = json.loads(wf_path.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        raise RhCliError("INVALID_WORKFLOW", "顶层必须是节点字典。")

    # 3. 上传并注入输入文件
    client = _make_client(site)

    if input_image:
        img_path = Path(input_image).expanduser()
        if not img_path.exists():
            raise RhCliError("FILE_NOT_FOUND", f"图片不存在：{img_path}")

        ext = img_path.suffix.lower()
        if ext in (".mp4", ".mov", ".avi", ".webm"):
            # 视频文件 → 注入 VHS_LoadVideo
            uploaded = _upload_file(client, site, api_key, img_path)
            node_id = _find_vhs_load_video_node(workflow)
            if node_id:
                workflow[node_id].setdefault("inputs", {})["video"] = str(uploaded)
            else:
                raise RhCliError("NO_VHS_LOAD_VIDEO", "工作流中缺少 VHS_LoadVideo 节点。")
        else:
            # 图片文件 → 注入 LoadImage
            uploaded = _upload_file(client, site, api_key, img_path)
            node_id = _find_load_image_node(workflow)
            if node_id:
                workflow[node_id].setdefault("inputs", {})["image"] = str(uploaded)
            else:
                raise RhCliError("NO_LOAD_IMAGE", "工作流中缺少 LoadImage 节点。")

    # 4. 提交
    wf_str = json.dumps(workflow)
    task_id = _submit_forward(client, site, rh_auth, rh_id, wf_str)

    # 5. 轮询
    outputs = _poll_outputs(client, site, api_key, task_id, max_seconds=max_seconds, interval=interval)

    # 6. 下载
    files: list[str] = []
    for item in outputs:
        file_url = item.get("fileUrl")
        if not file_url:
            continue
        ext = item.get("fileType", "png")
        if output:
            out_path = Path(output).expanduser()
            if out_path.suffix:
                final = str(out_path)
            else:
                out_path.mkdir(parents=True, exist_ok=True)
                final = str(out_path / f"{task_id}.{ext}")
        else:
            final = f"./output/{task_id}.{ext}"
        _download(client, file_url, final)
        files.append(final)

    client.close()
    return {"files": files, "task_id": task_id}
