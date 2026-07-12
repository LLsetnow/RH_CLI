from __future__ import annotations

from typing import Any

from .config import resolve_api_key
from .http import ACCOUNT_STATUS_URL_CN, ACCOUNT_STATUS_URL_AI, RhHttpClient


def check_account(api_key_arg: str | None = None, key_name: str | None = None) -> dict[str, Any]:
    resolved = resolve_api_key(api_key_arg, key_name)
    if not resolved.value:
        return {
            "status": "no_key",
            "key_source": "none",
            "message": "No API key configured",
        }

    key = resolved.value
    key_prefix = key[:4] + "****"

    # 尝试两个站点，合并结果
    results: list[dict[str, Any]] = []
    for site, url in [("ai", ACCOUNT_STATUS_URL_AI), ("cn", ACCOUNT_STATUS_URL_CN)]:
        try:
            with RhHttpClient(key, timeout=15.0) as client:
                response = client.post_json(url, {"apikey": key}, timeout=15.0)
                if response.get("code") == 0:
                    data = response.get("data", {})
                    results.append({
                        "site": site,
                        "remainCoins": str(data.get("remainCoins", "0")),
                        "remainMoney": str(data.get("remainMoney", "0")) if data.get("remainMoney") is not None else "0",
                        "currency": str(data.get("currency", "")) if data.get("currency") else "",
                        "apiType": str(data.get("apiType", "")),
                        "currentTaskCounts": str(data.get("currentTaskCounts", "0")),
                    })
        except Exception:
            pass

    if not results:
        return {
            "status": "invalid_key",
            "key_prefix": key_prefix,
            "key_source": resolved.source,
            "message": "API key verification failed on all sites",
        }

    # 汇总余额
    total_money = 0.0
    total_coins = 0
    for r in results:
        try:
            total_money += float(r["remainMoney"])
        except (ValueError, TypeError):
            pass
        try:
            total_coins += int(r["remainCoins"])
        except (ValueError, TypeError):
            pass

    status = "ready" if total_money > 0 or total_coins > 0 else "no_balance"
    return {
        "status": status,
        "key_prefix": key_prefix,
        "key_source": resolved.source,
        "sites": results,
        "balance_money": str(total_money),
        "balance_coins": str(total_coins),
        "running_tasks": results[0].get("currentTaskCounts", "0") if results else "0",
    }
