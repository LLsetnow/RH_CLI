from __future__ import annotations

from rh_cli.workflow.client import _extract_task_cost


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
