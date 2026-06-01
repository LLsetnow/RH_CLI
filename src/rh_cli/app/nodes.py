from __future__ import annotations

import copy
import re

from rh_cli.errors import RhCliError
from rh_cli.http import RhHttpClient
from rh_cli.media import upload_app_file


WEBAPP_ID_RE = re.compile(r"(\d{12,})")


def parse_webapp_id(value: str) -> str:
    match = WEBAPP_ID_RE.search(value)
    if not match:
        raise RhCliError("INVALID_WEBAPP_ID", f"无法从输入中解析 webappId：{value}")
    return match.group(1)


def extract_webapp_id_from_invoke(invoke_example: str) -> str | None:
    match = re.search(r"/run/ai-app/(\d+)", invoke_example)
    return match.group(1) if match else None


def parse_node_arg(arg: str) -> tuple[str, str, str]:
    colon_idx = arg.find(":")
    if colon_idx == -1:
        raise RhCliError("INVALID_NODE_ARG", f"节点参数格式错误：{arg}，应为 nodeId:fieldName=value。")
    node_id = arg[:colon_idx]
    rest = arg[colon_idx + 1 :]
    eq_idx = rest.find("=")
    if eq_idx == -1:
        raise RhCliError("INVALID_NODE_ARG", f"节点参数格式错误：{arg}，应为 nodeId:fieldName=value。")
    return node_id, rest[:eq_idx], rest[eq_idx + 1 :]


def apply_modifications(
    client: RhHttpClient,
    node_list: list[dict],
    node_args: list[str] | None,
    file_args: list[str] | None,
) -> list[dict]:
    nodes = copy.deepcopy(node_list)
    for arg in node_args or []:
        node_id, field_name, field_value = parse_node_arg(arg)
        _upsert_node(nodes, node_id, field_name, field_value)

    for arg in file_args or []:
        node_id, field_name, file_path = parse_node_arg(arg)
        uploaded_name = upload_app_file(client, file_path)
        _upsert_node(nodes, node_id, field_name, uploaded_name)

    return nodes


def _upsert_node(nodes: list[dict], node_id: str, field_name: str, field_value: str) -> None:
    target = next(
        (node for node in nodes if str(node.get("nodeId")) == node_id and node.get("fieldName") == field_name),
        None,
    )
    if target is not None:
        target["fieldValue"] = field_value
        return
    nodes.append({"nodeId": node_id, "fieldName": field_name, "fieldValue": field_value})
