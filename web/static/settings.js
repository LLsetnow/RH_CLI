(function () {
  "use strict";

  var state = {
    settings: null,
    keys: [],
    accounts: [],
    inboundWorkflows: [],
    workflowFolders: [],
    pendingAccountId: "",
    dirty: false,
    clearingTelegram: false,
    activeSection: "ai",
    loading: false,
    logsLoading: false,
    logLevels: { debug: true, info: true, warning: true, error: true, critical: true },
    autoScroll: true,
  };
  var credentialBusy = {};
  var accountBusy = {};

  var shortcutGroups = [
    {
      title: "全局工作台",
      items: [
        { combos: [["Ctrl", "Enter"]], description: "提交当前任务；在非任务提交页会先回到任务提交页并自动提交。" },
        { combos: [["Alt", "←"], ["Alt", "→"]], description: "在顶层页面之间循环切换；Electron 版同时注册为全局快捷键。" },
      ],
    },
    {
      title: "任务提交",
      items: [
        { combos: [["⌘", "V"], ["Ctrl", "V"]], description: "在文件输入区域粘贴剪贴板图片，并保存为可提交的本地输入文件。" },
        { combos: [["Enter"]], description: "在工作流重命名弹窗中保存名称。" },
        { combos: [["Enter"], ["Space"]], description: "激活任务卡片、账号卡片、分辨率预设和文件拖放区等可聚焦控件。" },
        { combos: [["Escape"]], description: "关闭任务详情、设置、工作流配置和工作流重命名弹窗。" },
      ],
    },
    {
      title: "提示词工坊",
      items: [
        { combos: [["←"], ["→"], ["Home"], ["End"]], description: "调整左侧积木库宽度；方向键按步进调整，Home / End 直接到边界。" },
        { combos: [["↑"], ["↓"]], description: "在输入 @ 后的参考候选列表中上下移动。" },
        { combos: [["Enter"], ["Tab"]], description: "插入当前选中的 @ 参考候选。" },
        { combos: [["Escape"]], description: "关闭参考候选、图片上下文菜单、图片/文本预览和编辑弹窗。" },
        { combos: [["Backspace"]], description: "删除光标前最后一个参考 token。" },
        { combos: [["Enter"], ["Space"]], description: "打开资源媒体槽位的文件选择器。" },
        { combos: [["0"], ["1"], ["2"], ["3"], ["4"], ["5"]], description: "对鼠标悬停的积木、动作或参考卡片评分；0 表示清除评分。" },
        { combos: [["⌘", "V"], ["Ctrl", "V"]], description: "在资源媒体槽位粘贴素材。" },
      ],
    },
    {
      title: "成片库",
      items: [
        { combos: [["←"], ["→"]], description: "预览打开时前后跳转 1 秒；未打开预览时在同一行移动选中产物。" },
        { combos: [["↑"], ["↓"]], description: "未打开预览时在产物网格中上下移动选中项。" },
        { combos: [["Space"]], description: "播放或暂停预览中的媒体；未打开预览时控制选中视频。" },
        { combos: [["0"], ["1"], ["2"], ["3"], ["4"], ["5"]], description: "对当前或悬停的产物评分；0 表示清除评分。" },
        { combos: [["Enter"]], description: "打开选中的产物预览；预览打开时关闭预览。" },
        { combos: [["Escape"]], description: "关闭产物预览、导入弹窗和右键菜单。" },
        { combos: [["Delete"], ["Backspace"]], description: "删除选中的任务记录及其产物；执行前会请求确认。" },
      ],
    },
    {
      title: "内容对比",
      items: [
        { combos: [["Space"]], description: "播放或暂停两条视频。需要至少加载两个视频，且焦点不在输入控件上。" },
        { combos: [["←"], ["→"]], description: "让两条视频同步前后跳转 1 秒。" },
        { combos: [["D"], ["F"]], description: "逐帧后退或前进 1 帧，并暂停视频。" },
        { combos: [["0"]], description: "重置对比画布的缩放和平移；需要先聚焦对比画布。" },
        { combos: [["Enter"], ["Space"]], description: "激活对比素材投放区或素材卡片。" },
      ],
    },
    {
      title: "专注模式",
      items: [
        { combos: [["Shift", "滚轮"]], description: "横向滚动专注模式中的多面板工作区。" },
        { combos: [["←"], ["→"]], description: "聚焦面板分隔条时调整相邻面板宽度。" },
      ],
    },
    {
      title: "工作流库与设置",
      items: [
        { combos: [["Enter"]], description: "工作流库编辑文件夹名称时保存。" },
        { combos: [["Escape"]], description: "工作流库取消文件夹重命名，或关闭上下文菜单和编辑弹窗。" },
        { combos: [["Enter"], ["Space"]], description: "在设置页键盘激活账号卡片，选择当前使用账号。" },
      ],
    },
  ];

  function colorToken(token, name, description, dark, light, swatch) {
    return { token: token, name: name, description: description, dark: dark, light: light, swatch: swatch || token.replace(/^--/, "") };
  }

  function derivedColor(token, name, description, formula) {
    return colorToken(token, name, description, formula, formula);
  }

  var colorGroups = [
    {
      title: "状态与语义",
      items: [
        colorToken("--accent", "强调 / 成功", "主操作、选中、可用、完成和进行中状态。", "#6ee7d8", "#168f86"),
        colorToken("--accent-deep", "强调深色", "强调色的悬停和亮色主题深阶。", "#39b7aa", "#0d746d"),
        colorToken("--warm", "提醒 / 运行中", "等待、消耗、未配置、未绑定和运行中状态。", "#ffb86b", "#b46a16"),
        colorToken("--danger", "错误 / 危险", "失败、异常和删除操作。", "#ff7e8a", "#c04457"),
        colorToken("--accent-contrast", "强调反差文字", "放在强调色底上的文字或图标。", "#061414", "#ffffff"),
        colorToken("--reference-accent", "参考资源", "参考媒体和资源类型标记。", "#5a9be8", "#2f6fb7"),
        colorToken("--type-accent", "类型 / 结构", "工作流类型和提示词结构卡片。", "#b8a2ff", "#765ac4"),
        colorToken("--disabled-ink", "禁用文字", "不可用控件中的低对比度文字。", "#3c4961", "#a7b2c1"),
        colorToken("--grip-ink", "拖拽把手", "面板分隔和拖拽提示的辅助色。", "#52617a", "#8190a3"),
      ],
    },
    {
      title: "评分色阶",
      items: [
        colorToken("--rating-gray", "未评分 / 0", "未评分或中性的评分状态。", "#aab4c7", "#8f9aaa"),
        colorToken("--rating-green", "1 星", "评分按钮的绿色语义色。", "#91e5d8", "#59c7bb"),
        colorToken("--rating-purple", "2 星", "评分按钮的紫色语义色。", "#c8baff", "#9c86dc"),
        colorToken("--rating-blue", "3 星", "评分按钮的蓝色语义色。", "#8ebcf0", "#619bd5"),
        colorToken("--rating-yellow", "4—5 星 / 日志级别", "高评分和日志 WARNING 标记。", "#f4da86", "#c9a83d"),
      ],
    },
    {
      title: "文字与界面表面",
      items: [
        colorToken("--ink", "主文字", "标题、正文和高优先级信息。", "#f2f5fb", "#172033"),
        colorToken("--muted", "次级文字", "说明、字段值和次要信息。", "#8f9ab1", "#59657a"),
        colorToken("--subtle", "辅助文字", "标签、时间和低优先级说明。", "#68748d", "#7b879b"),
        colorToken("--placeholder", "占位文字", "输入框和空状态的占位提示。", "#59657c", "var(--subtle)"),
        colorToken("--canvas", "页面背景", "页面渐变的上层背景。", "#0b1020", "#f3f7fb"),
        colorToken("--canvas-deep", "页面深层背景", "页面渐变的下层背景。", "#080c17", "#e9eff6"),
        colorToken("--panel", "面板表面", "主要卡片和面板背景。", "#121a2c", "#ffffff"),
        colorToken("--panel-raised", "抬升表面", "比普通面板更亮的内容区和拖放区。", "#172138", "#f7faff"),
        colorToken("--surface-input", "输入表面", "输入框和可编辑控件背景。", "#0e1628", "var(--panel)"),
        colorToken("--surface-control", "控件表面", "按钮、标签和分段控件背景。", "#1b2943", "#edf5f8"),
        colorToken("--surface-control-muted", "弱控件表面", "未选中、禁用和辅助控件背景。", "#18233a", "#edf2f8"),
        colorToken("--surface-control-hover", "控件悬停表面", "控件悬停时的反馈背景。", "#213653", "#e3edf3"),
        colorToken("--surface-media", "媒体表面", "图片、视频和媒体预览底色。", "#070b14", "#e8eef5"),
      ],
    },
    {
      title: "边界",
      items: [
        colorToken("--line", "主边界", "卡片和主要容器边界。", "#26324b", "#d4deeb"),
        colorToken("--line-soft", "柔和边界", "低强调容器轮廓。", "rgba(137, 153, 184, .16)", "rgba(73, 94, 123, .18)"),
        colorToken("--border-control", "控件边界", "输入框、开关和按钮的默认边界。", "#40506c", "#c6d3e1"),
        colorToken("--border-control-hover", "控件悬停边界", "控件获得悬停或聚焦反馈时的边界。", "#50617f", "#91a8bd"),
        colorToken("--border-drop", "拖放边界", "文件拖放区、空状态和编辑区共用的边界。", "#3b4967", "#b9c8da"),
        colorToken("--border-dialog", "弹窗边界", "弹窗和上下文菜单的边界。", "#344361", "var(--line)"),
      ],
    },
    {
      title: "统一层级与日志",
      items: [
        derivedColor("--accent-soft", "强调柔层", "选中、提示和悬停的统一强调背景。", "color-mix(in srgb, var(--accent) 8%, transparent)"),
        derivedColor("--accent-strong", "强调强层", "强调边框、焦点和强反馈。", "color-mix(in srgb, var(--accent) 35%, transparent)"),
        derivedColor("--warm-soft", "暖色柔层", "提醒和运行中状态的统一背景。", "color-mix(in srgb, var(--warm) 8%, transparent)"),
        derivedColor("--warm-strong", "暖色强层", "运行中边框和高优先级提醒。", "color-mix(in srgb, var(--warm) 35%, transparent)"),
        derivedColor("--danger-soft", "错误柔层", "错误和删除操作的统一背景。", "color-mix(in srgb, var(--danger) 9%, transparent)"),
        derivedColor("--danger-strong", "错误强层", "删除、失败和异常状态的强边框。", "color-mix(in srgb, var(--danger) 50%, transparent)"),
        derivedColor("--reference-soft", "参考柔层", "参考资源悬停和卡片背景。", "color-mix(in srgb, var(--reference-accent) 10%, transparent)"),
        derivedColor("--reference-strong", "参考强层", "参考资源边框和选中反馈。", "color-mix(in srgb, var(--reference-accent) 35%, transparent)"),
        derivedColor("--type-soft", "类型柔层", "提示词结构卡片的淡色背景。", "color-mix(in srgb, var(--type-accent) 10%, transparent)"),
        derivedColor("--type-strong", "类型强层", "提示词结构卡片的选中轮廓。", "color-mix(in srgb, var(--type-accent) 38%, transparent)"),
        derivedColor("--surface-deep", "深层表面", "输入卡片、拖放区和列表容器底色。", "color-mix(in srgb, var(--canvas-deep) 32%, transparent)"),
        derivedColor("--light-soft", "亮色柔层", "深色媒体舞台中的弱高光。", "color-mix(in srgb, var(--light-base) 12%, transparent)"),
        derivedColor("--light-strong", "亮色强层", "亮色导航、控件和卡片高光。", "color-mix(in srgb, var(--light-base) 78%, transparent)"),
        colorToken("--shadow-ink", "阴影基色", "阴影和遮罩的主题基色。", "#000000", "#2f486a"),
        derivedColor("--shadow-soft", "柔和阴影", "卡片和控件的轻量阴影。", "color-mix(in srgb, var(--shadow-ink) 10%, transparent)"),
        derivedColor("--shadow-medium", "中等阴影", "面板、卡片和导航的标准阴影。", "color-mix(in srgb, var(--shadow-ink) 20%, transparent)"),
        derivedColor("--shadow-strong", "强阴影", "弹窗、聚焦和拖拽状态的阴影。", "color-mix(in srgb, var(--shadow-ink) 36%, transparent)"),
        derivedColor("--scrim", "遮罩层", "模态弹窗覆盖页面时的遮罩。", "color-mix(in srgb, var(--shadow-ink) 72%, transparent)"),
        derivedColor("--subtle-soft", "辅助柔层", "标签和轻量内容表面的统一弱背景。", "color-mix(in srgb, var(--subtle) 7%, transparent)"),
        colorToken("--log-surface", "日志表面", "日志视口的专用深色底。", "#1e1e1e", "#1e1e1e"),
        colorToken("--log-text", "日志正文", "日志正文的统一文字色。", "#b7e9f2", "#b7e9f2"),
        colorToken("--log-muted", "日志辅助文字", "日志时间、阶段、调试和空状态文字。", "#899ba2", "#899ba2"),
      ],
    },
  ];

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }
  function shortcutComboMarkup(combo) {
    return combo.map(function (key, index) {
      return (index ? '<span class="settings-key-plus" aria-hidden="true">+</span>' : "") + '<kbd>' + esc(key) + '</kbd>';
    }).join("");
  }
  function shortcutMarkup(combos) {
    return combos.map(function (combo, index) {
      return (index ? '<span class="settings-key-or" aria-hidden="true">或</span>' : "") + '<span class="settings-key-combo">' + shortcutComboMarkup(combo) + '</span>';
    }).join("");
  }
  function renderShortcutReference() {
    var container = $("shortcutReference");
    if (!container) return;
    var count = 0;
    container.innerHTML = shortcutGroups.map(function (group) {
      count += group.items.length;
      return '<section class="settings-reference-group"><h4>' + esc(group.title) + '</h4><div class="settings-shortcut-list">' + group.items.map(function (item) {
        return '<div class="settings-shortcut-row"><div class="settings-shortcut-keys">' + shortcutMarkup(item.combos) + '</div><p>' + esc(item.description) + '</p></div>';
      }).join("") + '</div></section>';
    }).join("");
    var countLabel = $("shortcutReferenceCount");
    if (countLabel) countLabel.textContent = count + " 项操作";
  }
  function colorSwatchToken(item) {
    return item.swatch || item.token.replace(/^--/, "");
  }
  function colorSwatchVariable(item) {
    return "--" + colorSwatchToken(item);
  }
  function renderColorReference() {
    var container = $("colorReference");
    if (!container) return;
    var theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
    var alternate = theme === "light" ? "dark" : "light";
    var themeLabel = theme === "light" ? "亮色" : "暗色";
    var alternateLabel = alternate === "light" ? "亮色" : "暗色";
    var count = 0;
    container.innerHTML = colorGroups.map(function (group) {
      count += group.items.length;
      return '<section class="settings-color-group"><h4>' + esc(group.title) + '</h4><div class="settings-color-list">' + group.items.map(function (item) {
        var currentValue = item[theme];
        var alternateValue = item[alternate];
        return '<div class="settings-color-row"><span class="settings-color-swatch" data-color-token="' + esc(colorSwatchToken(item)) + '" style="--settings-swatch-color: var(' + esc(colorSwatchVariable(item)) + ')" aria-hidden="true"></span><div class="settings-color-copy"><div class="settings-color-title"><strong>' + esc(item.name) + '</strong><code>' + esc(item.token) + '</code></div><p>' + esc(item.description) + '</p></div><div class="settings-color-values"><span><small>' + themeLabel + '</small><code>' + esc(currentValue) + '</code></span><span><small>' + alternateLabel + '</small><code>' + esc(alternateValue) + '</code></span></div></div>';
      }).join("") + '</div></section>';
    }).join("");
    var countLabel = $("colorReferenceCount");
    if (countLabel) countLabel.textContent = count + " 个令牌";
  }
  function renderInteractionReference() {
    renderShortcutReference();
    renderColorReference();
  }
  function showToast(message, isError) {
    var toast = $("settingsToast");
    if (!toast) return;
    if (window.RHMotion && window.RHMotion.showToast) window.RHMotion.showToast(toast, message, isError);
  }
  function request(path, options) {
    return fetch(path, options || {}).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.message || data.error || "请求失败");
        return data;
      });
    });
  }
  function jsonRequest(path, method, body) {
    return request(path, { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }
  function statusLabel(status) {
    return { ready: "可用", no_balance: "无余额", unchecked: "待检测", error: "检测失败" }[status] || status || "未知";
  }
  function accountStatusLabel(status) {
    return { login_required: "待登录", ready: "已登录", checking: "签到中", checked_in: "今日已签到", not_checked_in: "未返回奖励", error: "账号异常" }[status] || status || "未知";
  }
  function relativeTime(timestamp) {
    var value = Number(timestamp);
    if (!value || isNaN(value)) return "未查询";
    var seconds = Math.max(0, Math.floor((Date.now() - value) / 1000));
    if (seconds < 60) return "刚刚";
    if (seconds < 3600) return Math.floor(seconds / 60) + " 分钟前";
    if (seconds < 86400) return Math.floor(seconds / 3600) + " 小时前";
    return new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  function credentialBalanceMarkup(key) {
    if (!Number(key.balance_checked_at)) return '<div class="credential-balance credential-balance-empty"><span>余额</span><span>未查询</span></div>';
    var symbol = key.symbol || (key.site === "cn" ? "¥" : "$");
    var balance = key.balance == null || key.balance === "" ? "—" : key.balance;
    var coins = key.coins == null || key.coins === "" ? "—" : key.coins;
    return '<div class="credential-balance"><span><span class="credential-balance-label">余额</span> <strong>' + esc(symbol) + esc(balance) + '</strong><span class="credential-coins"> · ' + esc(coins) + ' RH 币</span></span><span class="credential-balance-time">更新于 ' + esc(relativeTime(key.balance_checked_at)) + '</span></div>';
  }
  function credentialActionButton(key, action, label, className) {
    var busy = credentialBusy[key.id] === action;
    var busyLabel = action === "check-key" ? "检测中…" : action === "refresh-balance" ? "刷新中…" : "删除中…";
    return '<button class="credential-action ' + className + '" type="button" data-action="' + action + '" data-key-id="' + esc(key.id) + '"' + (busy ? ' disabled' : '') + '>' + esc(busy ? busyLabel : label) + '</button>';
  }
  function renderKeys() {
    var list = $("credentialList");
    if (!list) return;
    if (!state.keys.length) {
      list.innerHTML = '<div class="credential-empty">还没有保存 API Key。添加后会先验证站点、余额和账户类型。</div>';
      return;
    }
    list.innerHTML = state.keys.map(function (key) {
      var status = String(key.status || "unchecked");
      return '<div class="credential-card"><div class="credential-top"><div class="credential-name">' + esc(key.name) + '</div><div class="credential-tags"><span class="status-chip ' + esc(status) + '">' + esc(statusLabel(status)) + '</span><span class="capacity-chip">' + esc(key.capacity) + ' 并发</span></div></div><div class="credential-key">' + esc(key.masked_key) + ' · ' + esc(key.site) + '</div>' + credentialBalanceMarkup(key) + '<div class="credential-bottom"><span>运行 ' + esc(key.active_tasks) + ' / ' + esc(key.capacity) + ' · ' + esc(key.api_type || "类型待识别") + '</span><span class="credential-actions">' + credentialActionButton(key, "check-key", "检测", "credential-action-check") + credentialActionButton(key, "refresh-balance", "刷新余额", "credential-action-refresh") + credentialActionButton(key, "delete-key", "删除", "credential-action-delete") + '</span></div></div>';
    }).join("");
  }
  function managedAccountActionButton(account, action, label, className) {
    var busy = accountBusy[account.id] === action;
    var busyLabel = action === "account-checkin" ? "签到中…" : action === "account-login" ? "打开中…" : "删除中…";
    return '<button class="credential-action ' + className + '" type="button" data-action="' + action + '" data-account-id="' + esc(account.id) + '"' + (busy ? ' disabled' : '') + '>' + esc(busy ? busyLabel : label) + '</button>';
  }
  function renderAccounts() {
    var list = $("accountList");
    if (!list) return;
    var currentId = state.pendingAccountId || (state.settings && state.settings.current_account_id) || "__general__";
    var general = { id: "__general__", name: "通用模式", site: "", status: "ready", status_message: "不绑定任何账号，可使用所有已绑定的 API Key", general: true };
    list.innerHTML = [general].concat(state.accounts).map(function (account) {
      var status = String(account.status || "login_required");
      var current = account.id === currentId;
      var siteLabel = account.general ? "全部已绑定 API Key" : account.site === "cn" ? "runninghub.cn" : "runninghub.ai";
      var actions = account.general ? "" : managedAccountActionButton(account, "account-login", "打开登录窗口", "credential-action-check") + managedAccountActionButton(account, "account-checkin", "签到", "credential-action-refresh") + managedAccountActionButton(account, "delete-account", "删除", "credential-action-delete");
      var reward = account.general ? account.status_message : (account.daily_coin == null ? "尚未读取今日登录奖励或余额" : "+" + account.daily_coin + " RH 币 · 今日登录奖励");
      return '<div class="credential-card account-card' + (current ? ' is-current' : '') + (account.general ? ' general-account-card' : '') + '" data-action="select-account" data-account-id="' + esc(account.id) + '" role="button" tabindex="0" aria-pressed="' + (current ? 'true' : 'false') + '"><div class="credential-top"><div class="credential-name">' + esc(account.name) + '</div><div class="credential-tags"><span class="status-chip account-status-' + esc(status) + '">' + esc(account.general ? "可用" : accountStatusLabel(status)) + '</span>' + (current ? '<span class="capacity-chip account-current-tag">当前使用</span>' : '') + '<span class="capacity-chip">' + esc(siteLabel) + '</span></div></div><div class="credential-key">' + esc(account.general ? "不绑定账号，可调度所有已绑定的 API Key" : "登录凭证保存在 Electron 本地会话") + '</div><div class="account-reward"><span>' + esc(reward) + '</span><span class="credential-balance-time">' + esc(account.status_message || "") + '</span></div><div class="credential-bottom"><span>' + (account.general ? "当前模式不绑定账号" : "上次登录 " + (account.last_login_at ? relativeTime(account.last_login_at) : "未记录")) + '</span><span class="credential-actions">' + actions + '</span></div></div>';
    }).join("") + (!state.accounts.length ? '<div class="credential-empty">还没有账号。添加后会在 Electron 窗口中完成一次登录。</div>' : "");
  }

  function value(id) { var input = $(id); return input ? input.value.trim() : ""; }
  function setValue(id, next) { var input = $(id); if (input && document.activeElement !== input) input.value = next == null ? "" : String(next); }
  function setChecked(id, next) { var input = $(id); if (input && document.activeElement !== input) input.checked = Boolean(next); }
  function telegramInboundMode() {
    var select = $("telegramInboundMode");
    return select && select.value === "folder_random" ? "folder_random" : "fixed";
  }
  function renderTelegramInboundOptions() {
    var telegram = state.settings && state.settings.telegram || {};
    var workflowSelect = $("telegramInboundWorkflow");
    var folderSelect = $("telegramInboundFolder");
    var selectedWorkflowId = telegram.inbound_workflow_id || "";
    var selectedFolderId = telegram.inbound_folder_id || "";
    if (workflowSelect && document.activeElement === workflowSelect) selectedWorkflowId = workflowSelect.value;
    if (folderSelect && document.activeElement === folderSelect) selectedFolderId = folderSelect.value;
    if (workflowSelect) {
      var workflowOptions = ['<option value="">请选择可用工作流</option>'];
      state.inboundWorkflows.forEach(function (workflow) {
        var account = workflow.account_name ? " · " + workflow.account_name : "";
        workflowOptions.push('<option value="' + esc(workflow.id) + '">' + esc(workflow.name + account) + '</option>');
      });
      if (selectedWorkflowId && !state.inboundWorkflows.some(function (workflow) { return workflow.id === selectedWorkflowId; })) {
        workflowOptions.push('<option value="' + esc(selectedWorkflowId) + '">' + esc((telegram.inbound_workflow_name || selectedWorkflowId) + "（当前不可用）") + '</option>');
      }
      workflowSelect.innerHTML = workflowOptions.join("");
      setValue("telegramInboundWorkflow", selectedWorkflowId);
    }
    if (folderSelect) {
      var folderOptions = ['<option value="">请选择工作流文件夹</option>'];
      state.workflowFolders.forEach(function (folder) {
        var count = Number(folder.workflow_count) || 0;
        folderOptions.push('<option value="' + esc(folder.id) + '">' + esc(folder.name) + (count ? "（" + count + " 个工作流）" : "（空文件夹）") + '</option>');
      });
      folderSelect.innerHTML = folderOptions.join("");
      setValue("telegramInboundFolder", selectedFolderId);
    }
    updateTelegramInboundControls();
  }
  function updateTelegramInboundControls() {
    var mode = telegramInboundMode();
    var inboundEnabled = Boolean($("telegramInboundEnabled") && $("telegramInboundEnabled").checked);
    var workflowField = $("telegramInboundWorkflowField");
    var folderField = $("telegramInboundFolderField");
    var inboundPanel = $("telegramInboundPanel");
    if (inboundPanel) {
      inboundPanel.classList.toggle("is-disabled", !inboundEnabled);
      inboundPanel.setAttribute("aria-disabled", inboundEnabled ? "false" : "true");
    }
    ["telegramInboundMode", "telegramInboundWorkflow", "telegramInboundFolder"].forEach(function (id) {
      var select = $(id);
      if (select) select.disabled = !inboundEnabled;
    });
    if (workflowField) workflowField.hidden = mode !== "fixed";
    if (folderField) folderField.hidden = mode !== "folder_random";
    var summary = $("telegramInboundSummary");
    if (!summary) return;
    if (mode === "folder_random") {
      var folder = state.workflowFolders.find(function (item) { return item.id === value("telegramInboundFolder"); });
      summary.textContent = folder ? "随机文件夹：" + folder.name + (inboundEnabled ? " · 已启用" : " · 未启用") : "请选择一个工作流文件夹。";
      return;
    }
    var workflow = state.inboundWorkflows.find(function (item) { return item.id === value("telegramInboundWorkflow"); });
    var telegram = state.settings && state.settings.telegram || {};
    summary.textContent = workflow ? "固定工作流：" + workflow.name + (inboundEnabled ? " · 已启用" : " · 未启用") : telegram.inbound_workflow_id ? "固定工作流：" + (telegram.inbound_workflow_name || telegram.inbound_workflow_id) + " · 当前不可用" : "请选择一个可用工作流。";
  }
  function applyState(data) {
    state.settings = data.settings || {};
    state.keys = Array.isArray(data.keys) ? data.keys : [];
    state.accounts = Array.isArray(data.accounts) ? data.accounts : [];
    if (state.dirty) { renderKeys(); renderAccounts(); return; }
    state.pendingAccountId = state.settings.current_account_id || "__general__";
    setValue("outputDir", state.settings.output_dir || "");
    setValue("douyinCookiePath", state.settings.douyin_cookie_path || "");
    setValue("personalCapacity", state.settings.personal_capacity || 3);
    setValue("apiKeyStrategy", state.settings.api_key_strategy || "personal_then_shared");
    setValue("poseMediaImportType", state.settings.pose_media_import_type || "depth");
    setValue("mediaLibraryRoot", state.settings.media_library_root || "");
    var translation = state.settings.aliyun_translation || {};
    setValue("aliyunTranslationAccessKeyId", translation.access_key_id || "");
    setValue("aliyunTranslationAccessKeySecret", "");
    var translationStatus = $("aliyunTranslationStatus");
    if (translationStatus) { translationStatus.textContent = translation.configured ? "已配置 · " + (translation.source === "environment" ? "环境变量" : "本机") : "未配置"; translationStatus.classList.toggle("ready", Boolean(translation.configured)); }
    var vision = state.settings.aliyun_vision || {};
    setValue("aliyunVisionApiKey", "");
    var visionStatus = $("aliyunVisionStatus");
    if (visionStatus) { visionStatus.textContent = vision.configured ? "已配置 · " + (vision.source === "environment" ? "环境变量" : "本机") : "未配置"; visionStatus.classList.toggle("ready", Boolean(vision.configured)); }
    var telegram = state.settings.telegram || {};
    setValue("telegramBotToken", "");
    setValue("telegramChatId", telegram.chat_id || "");
    setChecked("telegramEnabled", telegram.enabled);
    setChecked("telegramInboundEnabled", telegram.inbound_enabled);
    setValue("telegramInboundMode", telegram.inbound_mode || "fixed");
    renderTelegramInboundOptions();
    var telegramStatus = $("telegramStatus");
    if (telegramStatus) { telegramStatus.textContent = telegram.configured ? (telegram.enabled ? "已启用 · " + (telegram.source === "environment" ? "环境变量" : "本机") : "已配置 · 未启用") : "未配置"; telegramStatus.classList.toggle("ready", Boolean(telegram.configured && telegram.enabled)); }
    renderKeys();
    renderAccounts();
  }
  function refresh(silent) {
    if (state.loading) return Promise.resolve();
    state.loading = true;
    return Promise.all([request("/api/state"), request("/api/workflow-folders")]).then(function (results) {
      state.inboundWorkflows = Array.isArray(results[0].telegram_inbound_workflows) ? results[0].telegram_inbound_workflows : [];
      state.workflowFolders = Array.isArray(results[1].folders) ? results[1].folders : [];
      applyState(results[0]);
    }).catch(function (error) { if (!silent) showToast(error.message, true); }).finally(function () { state.loading = false; });
  }
  function setDirty(dirty) {
    state.dirty = Boolean(dirty);
    var notice = $("settingsUnsavedNotice");
    var fab = $("saveSettings");
    var status = $("settingsSaveState");
    var hero = document.querySelector(".settings-hero-status");
    if (notice) notice.hidden = !state.dirty;
    if (fab) fab.classList.toggle("is-dirty", state.dirty);
    if (status) status.textContent = state.dirty ? "有未保存修改" : "已保存";
    if (hero) hero.classList.toggle("is-dirty", state.dirty);
  }
  function markDirty() { if (!state.dirty) setDirty(true); }
  function chooseDirectory() {
    if (window.rhElectron && typeof window.rhElectron.selectDirectory === "function") return Promise.resolve(window.rhElectron.selectDirectory()).then(function (path) { return String(path || "").trim(); });
    return request("/api/pick-directory", { method: "POST" }).then(function (data) { return String(data.path || "").trim(); });
  }
  function pickDouyinCookie(button) {
    var original = button.textContent;
    button.disabled = true; button.textContent = "选择中…";
    jsonRequest("/api/pick-douyin-cookie", "POST", {}).then(function (data) {
      setValue("douyinCookiePath", data.path || ""); markDirty(); showToast("已选择文件，请点击右下角保存配置");
    }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; button.textContent = original; });
  }
  function collectPayload() {
    var body = {
      output_dir: value("outputDir"), douyin_cookie_path: value("douyinCookiePath"), personal_capacity: value("personalCapacity"),
      api_key_strategy: value("apiKeyStrategy"),
      pose_media_import_type: value("poseMediaImportType") || "depth",
      current_account_id: state.pendingAccountId || (state.settings && state.settings.current_account_id) || "__general__",
      media_library_root: value("mediaLibraryRoot")
    };
    if (state.clearingTelegram) {
      body.telegram_clear = true;
    } else {
      body.telegram_chat_id = value("telegramChatId");
      body.telegram_enabled = Boolean($("telegramEnabled") && $("telegramEnabled").checked);
      body.telegram_inbound_enabled = Boolean($("telegramInboundEnabled") && $("telegramInboundEnabled").checked);
      body.telegram_inbound_mode = value("telegramInboundMode") || "fixed";
      body.telegram_inbound_workflow_id = value("telegramInboundWorkflow");
      body.telegram_inbound_folder_id = value("telegramInboundFolder");
      var botToken = value("telegramBotToken");
      if (botToken) body.telegram_bot_token = botToken;
    }
    var translationSecret = value("aliyunTranslationAccessKeySecret");
    if (translationSecret) {
      body.aliyun_translation_access_key_id = value("aliyunTranslationAccessKeyId");
      body.aliyun_translation_access_key_secret = translationSecret;
    }
    var visionApiKey = value("aliyunVisionApiKey");
    if (visionApiKey) body.aliyun_vision_api_key = visionApiKey;
    return body;
  }
  function saveAllSettings() {
    if (state.saving) return Promise.reject(new Error("配置正在保存中"));
    state.saving = true;
    var button = $("saveSettings");
    if (button) { button.disabled = true; button.querySelector("span:last-child").textContent = "保存中…"; }
    return jsonRequest("/api/settings", "PATCH", collectPayload()).then(function () {
      state.clearingTelegram = false;
      setDirty(false);
      showToast("全部配置已保存并生效");
      return refresh(true);
    }).catch(function (error) { showToast(error.message, true); throw error; }).finally(function () {
      state.saving = false;
      if (button) { button.disabled = false; button.querySelector("span:last-child").textContent = "保存配置"; }
    });
  }

  function selectSection(section) {
    var allowed = ["ai", "platform", "plugin", "extension", "reference", "logs"];
    section = allowed.indexOf(section) === -1 ? "ai" : section;
    state.activeSection = section;
    document.querySelectorAll("[data-settings-section]").forEach(function (button) { button.classList.toggle("active", button.dataset.settingsSection === section); });
    document.querySelectorAll("[data-settings-panel]").forEach(function (panel) { var active = panel.dataset.settingsPanel === section; panel.hidden = !active; panel.classList.toggle("active", active); });
    if (section === "logs") loadLogs(false);
    try {
      var settingsPath = document.body.classList.contains("focus-body") ? window.location.pathname : "/settings";
      history.replaceState({}, document.title, settingsPath + "#" + section);
    } catch (error) {}
  }
  function parseTime(timestamp) { var date = new Date(Number(timestamp)); return isNaN(date.getTime()) ? "----/--/-- --:--:--" : date.toLocaleString("zh-CN", { hour12: false }); }
  function logMarkup(log) {
    var level = String(log.level || "info").toLowerCase();
    if (!state.logLevels[level]) return "";
    return '<div class="log-line level-' + esc(level) + '"><span class="log-time">' + esc(parseTime(log.at)) + '</span><span class="log-level">' + esc(level.toUpperCase()) + '</span><span class="log-stage">' + esc(log.stage || log.source || "service") + '</span><span class="log-message">' + esc(log.message || "") + (log.task_id ? ' <span class="log-task-id">[' + esc(log.task_id) + ']</span>' : "") + '</span></div>';
  }
  function renderLogs(logs) {
    var list = $("logsList");
    var viewport = $("logsViewport");
    if (!list) return;
    var wasNearBottom = viewport ? viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 60 : true;
    var markup = (Array.isArray(logs) ? logs : []).map(logMarkup).filter(Boolean).join("");
    list.innerHTML = markup || '<div class="logs-empty">当前筛选条件下没有日志。</div>';
    if (viewport && state.autoScroll && wasNearBottom) viewport.scrollTop = viewport.scrollHeight;
  }
  function loadLogs(silent) {
    if (state.logsLoading) return;
    state.logsLoading = true;
    request("/api/logs?limit=500").then(function (data) { renderLogs(data.logs || []); }).catch(function (error) { if (!silent) showToast(error.message, true); }).finally(function () { state.logsLoading = false; });
  }
  function updateThemeToggle() {
    var dark = document.documentElement.dataset.theme === "dark";
    if ($("themeToggleIcon")) $("themeToggleIcon").textContent = dark ? "☀" : "☾";
    if ($("themeToggleLabel")) $("themeToggleLabel").textContent = dark ? "亮色" : "夜间";
    if ($("themeToggle")) $("themeToggle").setAttribute("aria-label", dark ? "切换到亮色模式" : "切换到夜间模式");
  }
  function callElectronAccount(method, account) {
    if (!window.rhElectron || typeof window.rhElectron[method] !== "function") return Promise.reject(new Error("账号管理需要通过 Electron 开发版运行"));
    return Promise.resolve(window.rhElectron[method](account));
  }
  function handleCredentialClick(event) {
    var trigger = event.target.closest("[data-action]");
    if (!trigger) return;
    var action = trigger.dataset.action; var keyId = trigger.dataset.keyId;
    var key = state.keys.find(function (item) { return item.id === keyId; });
    if (!key || credentialBusy[keyId]) return;
    if (action === "delete-key" && !window.confirm("确定删除这个 API Key 吗？")) return;
    credentialBusy[keyId] = action; renderKeys();
    var promise = action === "delete-key" ? request("/api/keys/" + encodeURIComponent(keyId), { method: "DELETE" }) : request("/api/keys/" + encodeURIComponent(keyId) + "/" + (action === "refresh-balance" ? "balance" : "check"), { method: "POST" });
    promise.then(function () { showToast(action === "delete-key" ? "API Key 已删除" : action === "refresh-balance" ? "余额已刷新" : "API Key 已检测"); return refresh(true); }).catch(function (error) { showToast(error.message, true); }).finally(function () { delete credentialBusy[keyId]; renderKeys(); });
  }
  function handleAccountClick(event) {
    var trigger = event.target.closest("[data-action]"); if (!trigger) return;
    var action = trigger.dataset.action; var accountId = trigger.dataset.accountId;
    var account = accountId === "__general__" ? { id: accountId, name: "通用模式", general: true } : state.accounts.find(function (item) { return item.id === accountId; });
    if (!account || accountBusy[accountId]) return;
    if (action === "select-account") { state.pendingAccountId = accountId; markDirty(); renderAccounts(); return; }
    if (action === "account-login" || action === "account-checkin") {
      accountBusy[accountId] = action; renderAccounts();
      callElectronAccount(action === "account-login" ? "openAccountLogin" : "accountCheckin", account).then(function (result) { showToast(result && result.message ? result.message : action === "account-login" ? "已打开账号窗口" : "签到状态已更新", result && result.status === "error"); return refresh(true); }).catch(function (error) { showToast(error.message, true); }).finally(function () { delete accountBusy[accountId]; renderAccounts(); });
      return;
    }
    if (action === "delete-account") {
      if (!window.confirm("确定删除这个账号记录吗？")) return;
      accountBusy[accountId] = action; renderAccounts();
      request("/api/accounts/" + encodeURIComponent(accountId), { method: "DELETE" }).then(function () { if (state.pendingAccountId === accountId) state.pendingAccountId = "__general__"; showToast("账号记录已删除"); return refresh(true); }).catch(function (error) { showToast(error.message, true); }).finally(function () { delete accountBusy[accountId]; renderAccounts(); });
    }
  }
  function addAccount(event) {
    event.preventDefault();
    var button = $("addAccount"); if (!button) return;
    button.disabled = true;
    jsonRequest("/api/accounts", "POST", { name: value("accountName"), site: value("accountSite") }).then(function (data) { setValue("accountName", ""); showToast("账号已保存，正在打开登录窗口"); return refresh(true).then(function () { return callElectronAccount("openAccountLogin", data.account); }); }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; });
  }
  function addKey() {
    var button = $("addKey"); var apiKey = value("keyValue"); if (!apiKey) return showToast("请输入 API Key", true);
    button.disabled = true;
    jsonRequest("/api/keys", "POST", { name: value("keyName"), site: value("keySite"), api_key: apiKey }).then(function (data) { setValue("keyValue", ""); setValue("keyName", ""); showToast(data.key.status === "ready" ? "API Key 已验证并保存，余额已更新" : data.key.status_message, data.key.status !== "ready"); return refresh(true); }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; });
  }
  function closeUnsaved() { window.RHMotion.closeModal("unsavedChangesModal"); }
  function navigateAfterDecision(href) {
    if (document.body.classList.contains("focus-body")) return;
    window.location.href = href;
  }
  function interceptNavigation(event) {
    var link = event.target.closest && event.target.closest("a");
    if (!link || link.target && link.target !== "_self" || link.hasAttribute("download") || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var url;
    try { url = new URL(link.href, window.location.href); } catch (error) { return; }
    if (url.origin !== window.location.origin || url.pathname === window.location.pathname || !state.dirty) return;
    event.preventDefault();
    state.pendingNavigation = url.href;
    window.RHMotion.openModal("unsavedChangesModal", "confirmSaveAndLeave");
  }

  function bindEvents() {
    updateThemeToggle();
    renderInteractionReference();
    var settingsThemeToggle = $("themeToggle");
    if (settingsThemeToggle) settingsThemeToggle.addEventListener("click", function () { var next = document.documentElement.dataset.theme === "light" ? "dark" : "light"; document.documentElement.dataset.theme = next; try { localStorage.setItem("rh-workflow-theme", next); } catch (error) {} updateThemeToggle(); renderColorReference(); });
    document.querySelectorAll("[data-settings-section]").forEach(function (button) { button.addEventListener("click", function () { selectSection(button.dataset.settingsSection); }); });
    document.querySelectorAll("[data-settings-input], input, select, textarea").forEach(function (input) { if (input.id === "logsAutoScroll" || input.classList.contains("log-level-filter")) return; input.addEventListener("input", markDirty); input.addEventListener("change", markDirty); });
    $("telegramInboundMode").addEventListener("change", updateTelegramInboundControls);
    $("telegramInboundWorkflow").addEventListener("change", updateTelegramInboundControls);
    $("telegramInboundFolder").addEventListener("change", updateTelegramInboundControls);
    $("telegramInboundEnabled").addEventListener("change", updateTelegramInboundControls);
    $("saveSettings").addEventListener("click", function () { saveAllSettings().catch(function () {}); });
    $("chooseOutputDir").addEventListener("click", function () { var button = this; button.disabled = true; chooseDirectory().then(function (path) { if (path) { setValue("outputDir", path); markDirty(); showToast("已选择产物目录，请点击右下角保存配置"); } }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; }); });
    $("chooseDouyinCookie").addEventListener("click", function () { pickDouyinCookie(this); });
    $("chooseMediaLibraryRoot").addEventListener("click", function () {
      var button = this;
      var original = button.textContent;
      button.disabled = true;
      request("/api/pick-media-root", { method: "POST" }).then(function (data) {
        if (data.path) {
          setValue("mediaLibraryRoot", data.path);
          markDirty();
          showToast("已选择媒体库目录，请点击右下角保存配置");
        }
      }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; button.textContent = original; });
    });
    $("testTelegram").addEventListener("click", function () { if (state.dirty) return showToast("请先保存配置，再测试 Telegram", true); var button = this; button.disabled = true; jsonRequest("/api/telegram/test", "POST", {}).then(function (data) { showToast(data.message || "Telegram 测试消息已发送"); }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; }); });
    $("clearTelegram").addEventListener("click", function () { if (!window.confirm("清除本机保存的 Telegram Bot 配置吗？点击右下角保存后生效。")) return; state.clearingTelegram = true; setValue("telegramBotToken", ""); setValue("telegramChatId", ""); setChecked("telegramEnabled", false); setChecked("telegramInboundEnabled", false); setValue("telegramInboundMode", "fixed"); setValue("telegramInboundWorkflow", ""); setValue("telegramInboundFolder", ""); updateTelegramInboundControls(); markDirty(); showToast("已准备清除 Telegram 配置，请保存"); });
    $("credentialList").addEventListener("click", handleCredentialClick);
    $("accountList").addEventListener("click", handleAccountClick);
    $("accountList").addEventListener("keydown", function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); handleAccountClick(event); } });
    $("accountForm").addEventListener("submit", addAccount);
    $("credentialForm").addEventListener("submit", function (event) { event.preventDefault(); addKey(); });
    $("refreshKeys").addEventListener("click", function () { var button = this; button.disabled = true; Promise.all(state.keys.map(function (key) { return request("/api/keys/" + encodeURIComponent(key.id) + "/check", { method: "POST" }); })).then(function () { showToast("API Key 已刷新"); return refresh(true); }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; }); });
    document.querySelectorAll(".log-level-filter").forEach(function (button) { button.addEventListener("click", function () { var level = button.dataset.logLevel; state.logLevels[level] = !state.logLevels[level]; button.classList.toggle("active", state.logLevels[level]); button.textContent = (state.logLevels[level] ? "✓ " : "○ ") + level.toUpperCase(); loadLogs(true); }); });
    $("logsAutoScroll").addEventListener("change", function () { state.autoScroll = this.checked; });
    $("refreshLogs").addEventListener("click", function () { loadLogs(false); });
    $("closeUnsavedChanges").addEventListener("click", closeUnsaved);
    $("discardSettings").addEventListener("click", function () { var href = state.pendingNavigation; state.pendingNavigation = ""; closeUnsaved(); setDirty(false); navigateAfterDecision(href); });
    $("confirmSaveAndLeave").addEventListener("click", function () { var href = state.pendingNavigation; saveAllSettings().then(function () { state.pendingNavigation = ""; closeUnsaved(); navigateAfterDecision(href); }).catch(function () {}); });
    $("unsavedChangesModal").addEventListener("click", function (event) { if (event.target === this) closeUnsaved(); });
    document.addEventListener("click", interceptNavigation);
    window.addEventListener("beforeunload", function (event) { if (!state.dirty) return; event.preventDefault(); event.returnValue = "当前配置有未保存的修改。"; });
  }

  bindEvents();
  var initialSection = (window.location.hash || "").slice(1);
  selectSection(initialSection || "ai");
  refresh(false);
  window.setInterval(function () { refresh(true); if (state.activeSection === "logs") loadLogs(true); }, 1800);
}());
