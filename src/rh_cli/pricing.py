"""RunningHub 计费费率与任务花费估算。

数据来源：RunningHub 官方 API 文档 / 企业级共享 API 页面（截至 2026-08）。
- 消费级 / 共享机：按 GPU **实际运行秒数**扣 RH币，仅任务执行时扣费，编辑配置免费；
  通过 API 跑一个工作流的价格与网页端跑同一工作流完全一致。
- 企业级专享机：同样按秒结算，官方以「每小时 ¥」报价（口径不同于消费级 RH币，不做换算）。

费率可能随官方调整，最终以官网为准。想看账户实际余额用 `rh check`；
任务实际花费以 API 返回的 consumeCoins / 账单为准，本模块的估算仅供参考。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console


PRICING_SOURCE = "https://www.runninghub.cn/runninghub-api-doc-cn/doc-8287334"


@dataclass(frozen=True, slots=True)
class MachineTier:
    """消费级 / 共享机的按秒计费档位。"""

    key: str
    label: str
    vram: str
    coins_per_sec: float  # RH币 / 秒


# 消费级共享机费率（RH币/秒）—— 与网页端一致，也是 API 实际扣费口径。
MACHINE_TIERS: tuple[MachineTier, ...] = (
    MachineTier("standard", "Standard 标准", "24G", 0.2),
    MachineTier("plus", "Plus", "48G", 0.4),
)

# 企业级专享机报价（¥/小时）—— 按秒结算、并发任务累加计费。仅作参考，不与 RH币 换算。
ENTERPRISE_HOURLY_YUAN: tuple[tuple[str, float], ...] = (
    ("Lite", 0.4),
    ("Standard 24G", 4.0),
    ("Plus 48G", 6.0),
)

DEFAULT_TIER = "standard"

_TIER_BY_KEY: dict[str, MachineTier] = {t.key: t for t in MACHINE_TIERS}


def get_tier(key: str) -> MachineTier | None:
    return _TIER_BY_KEY.get(key.strip().lower())


def estimate_coins(seconds: float | str | int | None, tier: str = DEFAULT_TIER) -> float | None:
    """按耗时（秒）估算某档位任务消耗的 RH币；无法计算时返回 None。"""
    t = get_tier(tier)
    if t is None:
        return None
    try:
        secs = float(seconds)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    if secs <= 0:
        return None
    return round(secs * t.coins_per_sec, 2)


def pricing_dict(seconds: float | None = None) -> dict[str, Any]:
    """机器可读的费率表（供 --json 使用）。"""
    tiers: list[dict[str, Any]] = []
    for t in MACHINE_TIERS:
        entry: dict[str, Any] = {
            "key": t.key,
            "label": t.label,
            "vram": t.vram,
            "coins_per_sec": t.coins_per_sec,
            "coins_per_min": round(t.coins_per_sec * 60, 2),
            "coins_per_hour": round(t.coins_per_sec * 3600, 2),
        }
        if seconds is not None:
            entry["estimate_coins"] = estimate_coins(seconds, t.key)
        tiers.append(entry)
    return {
        "unit": "RH币 (coins)",
        "billing": "按 GPU 实际运行秒数计费，仅任务执行时扣费",
        "seconds": seconds,
        "tiers": tiers,
        "enterprise_hourly_yuan": [{"tier": name, "yuan_per_hour": y} for name, y in ENTERPRISE_HOURLY_YUAN],
        "source": PRICING_SOURCE,
    }


def render_pricing(console: "Console", seconds: float | None = None) -> None:
    """以表格形式打印费率；给了 seconds 则附上各档位预估花费。"""
    from rich.table import Table

    title = "RunningHub 计费费率（RH币）"
    if seconds is not None:
        title += f" · 按 {seconds:g}s 预估"
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("机型")
    table.add_column("显存")
    table.add_column("RH币/秒", justify="right")
    table.add_column("RH币/分", justify="right")
    table.add_column("RH币/时", justify="right")
    if seconds is not None:
        table.add_column(f"预估({seconds:g}s)", justify="right", style="green")

    for t in MACHINE_TIERS:
        row = [
            t.label,
            t.vram,
            f"{t.coins_per_sec:g}",
            f"{t.coins_per_sec * 60:g}",
            f"{t.coins_per_sec * 3600:g}",
        ]
        if seconds is not None:
            est = estimate_coins(seconds, t.key)
            row.append(f"{est:g} RH币" if est is not None else "—")
        table.add_row(*row)

    console.print(table)

    hourly = "，".join(f"{name} ¥{y:g}" for name, y in ENTERPRISE_HOURLY_YUAN)
    console.print("[dim]· 按 GPU 实际运行秒数计费，仅任务执行时扣费；编辑 / 配置免费。[/dim]")
    console.print("[dim]· 消费级 API 价格与网页端跑同一工作流一致。[/dim]")
    console.print(f"[dim]· 企业级专享机（按秒结算，仅参考）：{hourly} / 小时。[/dim]")
    console.print(f"[dim]· 费率以官方为准：{PRICING_SOURCE}[/dim]")
