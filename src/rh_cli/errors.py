from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CREATE_KEY_URL = "https://www.runninghub.cn/enterprise-api/sharedApi"
RECHARGE_URL = "https://www.runninghub.cn/vip-rights/4"


@dataclass(slots=True)
class RhCliError(Exception):
    code: str
    message: str
    exit_code: int = 1
    detail: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.detail is not None:
            data["detail"] = self.detail
        return data


def classify_api_error(message: str, code: str | int | None = None) -> RhCliError:
    code_text = str(code or "").lower()
    msg = str(message)
    msg_lower = msg.lower()
    combined = f"{code_text} {msg_lower}"

    if any(token in combined for token in ("auth", "401", "403", "token", "key")):
        return RhCliError(
            "AUTH_FAILED",
            f"API key 验证失败：{msg}",
            detail={"manage_url": CREATE_KEY_URL},
        )

    if any(token in combined for token in ("balance", "insufficient", "余额", "credit")):
        return RhCliError(
            "INSUFFICIENT_BALANCE",
            f"账户余额不足：{msg}",
            detail={"recharge_url": RECHARGE_URL},
        )

    return RhCliError("API_ERROR", f"RunningHub API 请求失败：{msg}")
