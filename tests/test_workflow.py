from __future__ import annotations

import pytest

from rh_cli.errors import RhCliError
from rh_cli.workflow import client as wf_client
from rh_cli.workflow.client import (
    _apply_file_args,
    _extract_task_cost,
    _parse_file_arg,
    _poll_outputs,
    _submit,
    _output_text,
    _normalise_output_ext,
    _validate_api_workflow,
)


class _ScriptedClient:
    """post_json 按预设脚本依次返回字典或抛出异常，用于驱动 _poll_outputs。"""

    def __init__(self, steps):
        self._steps = list(steps)
        self.calls = 0

    def post_json(self, url, payload, **kwargs):
        self.calls += 1
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class _SubmitClient:
    def __init__(self, response=None):
        self.response = response or {"code": 0, "data": {"taskId": "T1"}}
        self.payload = None

    def post_json(self, url, payload, **kwargs):
        self.payload = payload
        return self.response


def test_extract_task_cost_prefers_rh_coins():
    cost_type, cost, duration = _extract_task_cost([
        {"consumeCoins": "17", "consumeMoney": "0.12", "taskCostTime": "83"}
    ])

    assert (cost_type, cost, duration) == ("coins", "17", "83")


def test_extract_task_cost_falls_back_to_money():
    cost_type, cost, duration = _extract_task_cost([
        {"consumeCoins": None, "consumeMoney": "0.12", "taskCostTime": "83"}
    ])

    assert (cost_type, cost, duration) == ("money", "0.12", "83")


def test_extract_task_cost_allows_zero_and_omits_missing_values():
    assert _extract_task_cost([{"consumeCoins": 0, "taskCostTime": "0"}]) == ("coins", "0", "0")
    assert _extract_task_cost([{"consumeCoins": "", "consumeMoney": None}]) == (None, None, None)


def test_poll_retries_transient_network_error(monkeypatch):
    """一次瞬时网络错误后应继续轮询，最终拿到结果，而不是直接崩溃。"""
    monkeypatch.setattr(wf_client.time, "sleep", lambda *_: None)
    client = _ScriptedClient([
        RhCliError("API_ERROR", "网络请求失败：[SSL: UNEXPECTED_EOF_WHILE_READING]"),
        {"code": 804},  # RUNNING
        {"code": 0, "data": [{"fileUrl": "http://x/a.mp4"}]},  # 完成
    ])

    out = _poll_outputs(client, "k", "T1", max_seconds=100, interval=1)

    assert out == [{"fileUrl": "http://x/a.mp4"}]
    assert client.calls == 3


def test_poll_gives_up_after_max_consecutive_net_fails(monkeypatch):
    """连续网络错误超过阈值才放弃，且错误里保留 taskId 以便手动恢复。"""
    monkeypatch.setattr(wf_client.time, "sleep", lambda *_: None)
    steps = [RhCliError("API_ERROR", "boom")] * (wf_client.MAX_POLL_NET_FAILS + 1)
    client = _ScriptedClient(steps)

    with pytest.raises(RhCliError) as excinfo:
        _poll_outputs(client, "k", "T2", max_seconds=10_000, interval=1)

    assert excinfo.value.code == "TASK_POLL_ERROR"
    assert "T2" in excinfo.value.message


def test_poll_does_not_retry_auth_error(monkeypatch):
    """鉴权类错误无法自愈，应立即抛出，不进入重试。"""
    monkeypatch.setattr(wf_client.time, "sleep", lambda *_: None)
    client = _ScriptedClient([RhCliError("AUTH_FAILED", "bad key")])

    with pytest.raises(RhCliError) as excinfo:
        _poll_outputs(client, "k", "T3", max_seconds=100, interval=1)

    assert excinfo.value.code == "AUTH_FAILED"
    assert client.calls == 1


def test_submit_can_send_complete_workflow_without_workflow_id():
    client = _SubmitClient()

    task_id = _submit(client, "key", None, '{"1":{}}', create_url="https://example.test/create")

    assert task_id == "T1"
    assert client.payload == {"apiKey": "key", "workflow": '{"1":{}}'}


def test_submit_includes_generic_runninghub_options():
    client = _SubmitClient()

    _submit(
        client,
        "key",
        "workflow-1",
        "{}",
        instance_type="plus",
        access_password="secret",
        webhook_url="https://example.test/hook",
        retain_seconds=60,
        use_personal_queue=True,
        add_metadata=False,
    )

    assert client.payload == {
        "apiKey": "key",
        "workflow": "{}",
        "workflowId": "workflow-1",
        "instanceType": "plus",
        "accessPassword": "secret",
        "webhookUrl": "https://example.test/hook",
        "retainSeconds": 60,
        "usePersonalQueue": True,
        "addMetadata": False,
    }


def test_submit_rejects_invalid_retain_seconds():
    client = _SubmitClient()

    with pytest.raises(RhCliError) as excinfo:
        _submit(client, "key", None, "{}", retain_seconds=9)

    assert excinfo.value.code == "INVALID_RETAIN_SECONDS"
    assert client.payload is None


def test_submit_surfaces_prompt_tips_node_errors():
    client = _SubmitClient({
        "code": 0,
        "data": {
            "taskId": "T2",
            "promptTips": '{"result": false, "node_errors": {"9": "missing node"}}',
        },
    })

    with pytest.raises(RhCliError) as excinfo:
        _submit(client, "key", None, "{}")

    assert excinfo.value.code == "NODE_ERRORS"
    assert "T2" in excinfo.value.message


def test_validate_api_workflow_accepts_unknown_node_types():
    _validate_api_workflow({"1": {"class_type": "CustomNode", "inputs": {"value": 1}}})


def test_validate_api_workflow_rejects_ui_workflow_shape():
    with pytest.raises(RhCliError) as excinfo:
        _validate_api_workflow({"nodes": [], "links": []})

    assert excinfo.value.code == "INVALID_WORKFLOW"


def test_output_helpers_preserve_text_and_unknown_results():
    assert _output_text({"text": "done"}) == "done"
    assert _output_text({"value": {"answer": 1}}) == '{"value": {"answer": 1}}'
    assert _output_text({"nodeId": "42", "fileType": "string"}) == '{"nodeId": "42", "fileType": "string"}'
    assert _output_text({"fileUrl": "https://example.test/out.png", "text": "ignored"}) is None


def test_normalise_output_ext_accepts_mime_and_common_types():
    assert _normalise_output_ext("video") == "mp4"
    assert _normalise_output_ext("image/png") == "png"
    assert _normalise_output_ext(None) == "png"


def test_parse_file_arg_preserves_equals_in_path():
    node_id, field_name, path = _parse_file_arg("52:audio=/tmp/track=final.wav")

    assert node_id == "52"
    assert field_name == "audio"
    assert str(path) == "/tmp/track=final.wav"


def test_apply_file_args_uploads_and_injects_multiple_files(tmp_path, monkeypatch):
    image = tmp_path / "reference.png"
    audio = tmp_path / "track.wav"
    image.write_bytes(b"image")
    audio.write_bytes(b"audio")
    uploaded = iter(["remote-reference.png", "remote-track.wav"])

    def fake_upload(client, file_path, upload_url=""):
        return next(uploaded)

    monkeypatch.setattr(wf_client, "upload_app_file", fake_upload)
    workflow = {
        "37": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "52": {"class_type": "LoadAudio", "inputs": {"audio": "old.wav"}},
    }

    changes = _apply_file_args(
        object(),
        workflow,
        [f"37:image={image}", f"52:audio={audio}"],
    )

    assert workflow["37"]["inputs"]["image"] == "remote-reference.png"
    assert workflow["52"]["inputs"]["audio"] == "remote-track.wav"
    assert "reference.png" in changes[0]
    assert "track.wav" in changes[1]


def test_apply_file_args_validates_all_files_before_upload(tmp_path, monkeypatch):
    image = tmp_path / "reference.png"
    image.write_bytes(b"image")
    uploads = []

    def fake_upload(client, file_path, upload_url=""):
        uploads.append(file_path)
        return "remote-reference.png"

    monkeypatch.setattr(wf_client, "upload_app_file", fake_upload)
    workflow = {
        "37": {"class_type": "LoadImage", "inputs": {}},
        "52": {"class_type": "LoadAudio", "inputs": {}},
    }

    with pytest.raises(RhCliError) as excinfo:
        _apply_file_args(
            object(),
            workflow,
            [f"37:image={image}", "52:audio=/missing/track.wav"],
        )

    assert excinfo.value.code == "FILE_NOT_FOUND"
    assert uploads == []
