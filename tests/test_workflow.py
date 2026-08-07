from __future__ import annotations

import pytest

from rh_cli.errors import RhCliError
from rh_cli.workflow import client as wf_client
from rh_cli.workflow.client import _extract_task_cost, _poll_outputs


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
