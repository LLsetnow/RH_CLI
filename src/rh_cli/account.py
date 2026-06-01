from __future__ import annotations

from typing import Any

from .config import resolve_api_key
from .http import ACCOUNT_STATUS_URL, RhHttpClient


def check_account(api_key_arg: str | None = None) -> dict[str, Any]:
    resolved = resolve_api_key(api_key_arg)
    if not resolved.value:
        return {
            "status": "no_key",
            "key_source": "none",
            "message": "No API key configured",
        }

    key = resolved.value
    key_prefix = key[:4] + "****"
    with RhHttpClient(key, timeout=15.0) as client:
        try:
            response = client.post_json(ACCOUNT_STATUS_URL, {"apikey": key}, timeout=15.0)
        except Exception as exc:
            return {
                "status": "invalid_key",
                "key_prefix": key_prefix,
                "key_source": resolved.source,
                "message": str(exc),
            }

    if response.get("code") != 0:
        return {
            "status": "invalid_key",
            "key_prefix": key_prefix,
            "key_source": resolved.source,
            "message": response.get("msg", "API key verification failed"),
        }

    data = response.get("data", {})
    balance = data.get("remainMoney")
    balance_str = str(balance) if balance is not None else "0"
    try:
        balance_num = float(balance_str)
    except (TypeError, ValueError):
        balance_num = 0.0

    status = "ready" if balance_num > 0 else "no_balance"
    return {
        "status": status,
        "key_prefix": key_prefix,
        "key_source": resolved.source,
        "balance": balance_str,
        "currency": data.get("currency", "CNY"),
        "coins": data.get("remainCoins", "0"),
        "running_tasks": data.get("currentTaskCounts", "0"),
        "api_type": data.get("apiType", ""),
    }
