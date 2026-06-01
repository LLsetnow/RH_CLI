from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .errors import RhCliError
from .http import BASE_URL, RhHttpClient


POLL_URL = f"{BASE_URL}/query"
MAX_POLL_SECONDS = 1200
POLL_INTERVAL_SECONDS = 5


def poll_once(client: RhHttpClient, task_id: str) -> dict[str, Any] | None:
    for attempt in range(3):
        try:
            return client.post_json(POLL_URL, {"taskId": task_id}, timeout=30.0)
        except RhCliError:
            if attempt == 2:
                return None
            time.sleep(2)
    return None


def poll_task(
    client: RhHttpClient,
    task_id: str,
    *,
    max_seconds: int = MAX_POLL_SECONDS,
    interval: int = POLL_INTERVAL_SECONDS,
    on_tick: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    elapsed = 0
    consecutive_failures = 0
    while elapsed < max_seconds:
        time.sleep(interval)
        elapsed += interval
        response = poll_once(client, task_id)
        if response is None:
            consecutive_failures += 1
            if on_tick:
                on_tick(elapsed, "NETWORK_RETRY")
            if consecutive_failures >= 5:
                raise RhCliError("API_ERROR", "连续多次轮询失败，任务状态未知。")
            continue

        consecutive_failures = 0
        status = response.get("status", "UNKNOWN")
        if on_tick:
            on_tick(elapsed, str(status))

        if status == "SUCCESS":
            return response
        if status == "FAILED":
            error_msg = response.get("errorMessage", "Unknown error")
            error_code = response.get("errorCode", "")
            combined = f"{error_msg} {error_code}".lower()
            if any(token in combined for token in ("balance", "insufficient", "余额", "credit")):
                raise RhCliError("INSUFFICIENT_BALANCE", f"任务失败：{error_msg}")
            raise RhCliError("TASK_FAILED", f"任务失败：[{error_code}] {error_msg}")

    raise RhCliError("TASK_TIMEOUT", f"任务超过 {max_seconds}s 仍未完成。")
