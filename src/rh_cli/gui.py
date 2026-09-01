"""RH CLI 配置 TUI（Textual，终端内图形界面）。

通过 `rh gui` 打开：在终端里可视化查看/切换当前站点、默认 Key，并查看账户余额。
界面只是 `~/.config/rh/config.toml` 的可视化编辑层，复用 rh_cli.config / rh_cli.account，
不新增任何持久化逻辑。
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    RadioButton,
    RadioSet,
    Select,
    Static,
)

from rh_cli.account import check_account
from rh_cli.config import (
    config_path,
    get_default_key_name,
    list_keys,
    read_default_site,
    resolve_api_key,
    save_default_site,
    save_keys,
)

_KEY_LEN = 32


# ---------------------------------------------------------------------------
# 纯逻辑（便于单测）
# ---------------------------------------------------------------------------

def validate_key_value(value: str) -> str | None:
    """校验 API Key 值。合法返回清洗后的值，否则返回 None。"""
    normalized = value.strip()
    if not normalized or normalized in {"your_api_key_here", "<your_api_key>", "YOUR_API_KEY", "RUNNINGHUB_API_KEY"}:
        return None
    if len(normalized) != _KEY_LEN:
        return None
    return normalized


def next_default_after_remove(keys: dict[str, str], removed: str, current_default: str) -> str:
    """删除某个 Key 后应设的新默认 Key 名。当前默认被删则回退到剩余第一个。"""
    if current_default != removed:
        return current_default
    remaining = [name for name in keys if name != removed]
    return remaining[0] if remaining else ""


def _mask(value: str) -> str:
    """Key 值脱敏显示，绝不全量展示。"""
    return f"{value[:4]}****" if len(value) > 4 else "****"


def _format_balance(result: dict) -> str:
    status = result.get("status")
    if status == "no_key":
        return "尚未配置 API Key。"
    if status == "invalid_key":
        return f"API Key 校验失败：{result.get('message')}"
    if status == "no_balance":
        return "API Key 可用，但余额为 0。"
    lines = []
    for s in result.get("sites", []):
        coin = s.get("remainCoins", "0")
        money = s.get("remainMoney", "0")
        cur = s.get("currency", "")
        line = f"{s['site']} 站：{coin} RH币"
        if money not in ("", "0", "0.0", 0):
            line += f" + {money} {cur}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 删除确认弹窗
# ---------------------------------------------------------------------------

class ConfirmScreen(ModalScreen[bool]):
    """删除 Key 前的确认弹窗。"""

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self._message, id="confirm-msg")
            with Horizontal(id="confirm-buttons"):
                yield Button("取消", id="confirm-no", variant="error")
                yield Button("确认", id="confirm-yes", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")


# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------

class ConfigApp(App):
    TITLE = "RH CLI 配置"
    SUB_TITLE = "站点 / Key / 账户"
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; overflow-y: auto; padding: 1 2; }
    #site-section, #key-section, #acc-section {
        border: round $accent; padding: 1 2; margin-bottom: 1;
    }
    .section-title { text-style: bold; margin-bottom: 1; }
    #site-current { margin-bottom: 1; }
    #key-current { margin-bottom: 1; }
    #key-row, #add-row { height: auto; }
    #add-name { width: 16; }
    #add-value { width: 32; }
    #acc-balance { margin-bottom: 1; }
    #confirm-dialog {
        width: 60; height: 7; border: thick $error; padding: 1 2;
        background: $surface; layer: overlay;
    }
    #confirm-msg { content-align: center top; }
    #confirm-buttons { align: center middle; }
    """

    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("r", "refresh_balance", "刷新余额"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            with Vertical(id="site-section"):
                yield Static("【站点】", classes="section-title")
                yield Static("", id="site-current")
                with RadioSet(id="site-radio"):
                    yield RadioButton("ai 站 runninghub.ai", id="site-ai")
                    yield RadioButton("cn 站 runninghub.cn", id="site-cn")
            with Vertical(id="key-section"):
                yield Static("【默认 Key】", classes="section-title")
                yield Static("", id="key-current")
                with Horizontal(id="key-row"):
                    yield Select([], id="key-select", prompt="选择命名 Key", allow_blank=True)
                    yield Button("设为默认", id="btn-set-default")
                    yield Button("删除所选", id="btn-remove")
                with Horizontal(id="add-row"):
                    yield Input(placeholder="Key 名称", id="add-name")
                    yield Input(placeholder="API Key（32位）", id="add-value")
                    yield Button("添加 Key", id="btn-add")
            with Vertical(id="acc-section"):
                yield Static("【账户信息】", classes="section-title")
                yield Static("点击「刷新余额」或按 r 查看。", id="acc-balance")
                yield Button("刷新余额", id="btn-refresh")
        yield Footer()

    # -- 状态加载与刷新 ------------------------------------------------------

    def on_mount(self) -> None:
        self._reload()
        self._refresh_all()

    def _reload(self) -> None:
        self._site = read_default_site()
        self._keys = list_keys()
        self._default_key = get_default_key_name()

    def _refresh_all(self) -> None:
        self.query_one("#site-current", Static).update(f"当前默认站点：{self._site}")
        for rb in self.query(RadioButton):
            rb.value = rb.id == f"site-{self._site}"

        resolved = resolve_api_key()
        if resolved.value:
            self.query_one("#key-current", Static).update(
                f"当前默认：{self._default_key or '（未设置）'}　来源：{resolved.source}　{_mask(resolved.value)}"
            )
        else:
            self.query_one("#key-current", Static).update("尚未配置 API Key。")

        names = list(self._keys)
        select = self.query_one("#key-select", Select)
        select.set_options([(name, name) for name in names])
        if names:
            select.allow_blank = False
            select.disabled = False
            select.value = self._default_key if self._default_key in names else names[0]
        else:
            select.allow_blank = True
            select.disabled = True
            select.value = Select.NULL

    # -- 事件 ----------------------------------------------------------------

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id != "site-radio":
            return
        site = event.pressed.id.removeprefix("site-")
        # diff 检查：mount 时程序化设值会触发 Changed，但站点未变则跳过
        if site not in ("ai", "cn") or site == self._site:
            return
        save_default_site(site)
        self._site = site
        self.query_one("#site-current", Static).update(f"当前默认站点：{site}")
        self.notify(f"已保存站点 → {site}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn-set-default":
            self._do_set_default()
        elif button_id == "btn-remove":
            self._do_remove_key()
        elif button_id == "btn-add":
            self._do_add_key()
        elif button_id == "btn-refresh":
            self.action_refresh_balance()

    # -- 动作 ----------------------------------------------------------------

    def _do_set_default(self) -> None:
        name = self.query_one("#key-select", Select).value
        if not name or name not in list_keys():
            return
        save_keys(list_keys(), name)
        self._reload()
        self._refresh_all()
        self.notify(f"已切换默认 Key → {name}")

    async def _do_remove_key(self) -> None:
        name = self.query_one("#key-select", Select).value
        keys = list_keys()
        if not name or name not in keys:
            return
        confirmed = await self.push_screen(ConfirmScreen(f"确认删除命名 Key「{name}」？"))
        if not confirmed:
            return
        current_default = get_default_key_name()
        del keys[name]
        default = next_default_after_remove(keys, name, current_default)
        save_keys(keys, default)
        self._reload()
        self._refresh_all()
        self.notify(f"已删除 Key「{name}」")

    def _do_add_key(self) -> None:
        name = self.query_one("#add-name", Input).value.strip()
        value = validate_key_value(self.query_one("#add-value", Input).value)
        if not name:
            self.notify("Key 名称不能为空。", severity="error")
            return
        keys = list_keys()
        if name in keys:
            self.notify(f"Key 名称「{name}」已存在。", severity="error")
            return
        if value is None:
            self.notify(f"Key 值需为 {_KEY_LEN} 位 RunningHub API Key。", severity="error")
            return
        keys[name] = value
        save_keys(keys, get_default_key_name())
        self.query_one("#add-name", Input).value = ""
        self.query_one("#add-value", Input).value = ""
        self._reload()
        self._refresh_all()
        self.notify(f"已添加 Key「{name}」")

    def action_refresh_balance(self) -> None:
        self.query_one("#acc-balance", Static).update("查询中…")
        self.run_worker(self._fetch_balance())

    async def _fetch_balance(self) -> None:
        try:
            result = await asyncio.to_thread(check_account, None, get_default_key_name())
            text = _format_balance(result)
        except Exception as exc:  # noqa: BLE001 —— 界面层兜底展示
            text = f"余额查询失败：{exc}"
        self.query_one("#acc-balance", Static).update(text)


def run_gui() -> None:
    """启动 Textual TUI（阻塞直到退出）。"""
    ConfigApp().run()
