(function () {
  "use strict";

  var state = {
    settings: null,
    keys: [],
    accounts: [],
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

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
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
    setValue("promptLibraryPath", state.settings.prompt_library_path || "");
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
    var inbound = $("telegramInboundWorkflow");
    if (inbound) inbound.textContent = telegram.inbound_workflow_id ? "当前工作流：" + (telegram.inbound_workflow_name || telegram.inbound_workflow_id) + (telegram.inbound_enabled ? " · 已启用" : " · 未启用") : "未选择工作流。请在工作流卡片上点击“设为 Telegram 入站”。";
    var telegramStatus = $("telegramStatus");
    if (telegramStatus) { telegramStatus.textContent = telegram.configured ? (telegram.enabled ? "已启用 · " + (telegram.source === "environment" ? "环境变量" : "本机") : "已配置 · 未启用") : "未配置"; telegramStatus.classList.toggle("ready", Boolean(telegram.configured && telegram.enabled)); }
    renderKeys();
    renderAccounts();
  }
  function refresh(silent) {
    if (state.loading) return Promise.resolve();
    state.loading = true;
    return request("/api/state").then(applyState).catch(function (error) { if (!silent) showToast(error.message, true); }).finally(function () { state.loading = false; });
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
  function pickFile(kind, button) {
    var original = button.textContent;
    button.disabled = true; button.textContent = "选择中…";
    var promise = kind === "douyin" ? jsonRequest("/api/pick-douyin-cookie", "POST", {}) : jsonRequest("/api/pick-prompt-resource", "POST", { kind: kind });
    promise.then(function (data) {
      var id = kind === "douyin" ? "douyinCookiePath" : "promptLibraryPath";
      setValue(id, data.path || ""); markDirty(); showToast("已选择文件，请点击右下角保存配置");
    }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; button.textContent = original; });
  }
  function collectPayload() {
    var body = {
      output_dir: value("outputDir"), douyin_cookie_path: value("douyinCookiePath"), personal_capacity: value("personalCapacity"),
      api_key_strategy: value("apiKeyStrategy"),
      current_account_id: state.pendingAccountId || (state.settings && state.settings.current_account_id) || "__general__",
      prompt_library_path: value("promptLibraryPath"), media_library_root: value("mediaLibraryRoot")
    };
    if (state.clearingTelegram) {
      body.telegram_clear = true;
    } else {
      body.telegram_chat_id = value("telegramChatId");
      body.telegram_enabled = Boolean($("telegramEnabled") && $("telegramEnabled").checked);
      body.telegram_inbound_enabled = Boolean($("telegramInboundEnabled") && $("telegramInboundEnabled").checked);
      body.telegram_inbound_workflow_id = (state.settings && state.settings.telegram && state.settings.telegram.inbound_workflow_id) || "";
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
    var allowed = ["ai", "platform", "plugin", "extension", "logs"];
    section = allowed.indexOf(section) === -1 ? "ai" : section;
    state.activeSection = section;
    document.querySelectorAll("[data-settings-section]").forEach(function (button) { button.classList.toggle("active", button.dataset.settingsSection === section); });
    document.querySelectorAll("[data-settings-panel]").forEach(function (panel) { var active = panel.dataset.settingsPanel === section; panel.hidden = !active; panel.classList.toggle("active", active); });
    if (section === "logs") loadLogs(false);
    try { history.replaceState({}, document.title, "/settings#" + section); } catch (error) {}
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
    $("themeToggle").addEventListener("click", function () { var next = document.documentElement.dataset.theme === "light" ? "dark" : "light"; document.documentElement.dataset.theme = next; try { localStorage.setItem("rh-workflow-theme", next); } catch (error) {} updateThemeToggle(); });
    document.querySelectorAll("[data-settings-section]").forEach(function (button) { button.addEventListener("click", function () { selectSection(button.dataset.settingsSection); }); });
    document.querySelectorAll("[data-settings-input], input, select, textarea").forEach(function (input) { if (input.id === "logsAutoScroll" || input.classList.contains("log-level-filter")) return; input.addEventListener("input", markDirty); input.addEventListener("change", markDirty); });
    $("saveSettings").addEventListener("click", function () { saveAllSettings().catch(function () {}); });
    $("chooseOutputDir").addEventListener("click", function () { var button = this; button.disabled = true; chooseDirectory().then(function (path) { if (path) { setValue("outputDir", path); markDirty(); showToast("已选择产物目录，请点击右下角保存配置"); } }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; }); });
    $("chooseDouyinCookie").addEventListener("click", function () { pickFile("douyin", this); });
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
    document.querySelectorAll("[data-pick-prompt-resource]").forEach(function (button) { button.addEventListener("click", function () { pickFile(button.dataset.pickPromptResource, button); }); });
    $("testTelegram").addEventListener("click", function () { if (state.dirty) return showToast("请先保存配置，再测试 Telegram", true); var button = this; button.disabled = true; jsonRequest("/api/telegram/test", "POST", {}).then(function (data) { showToast(data.message || "Telegram 测试消息已发送"); }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; }); });
    $("clearTelegram").addEventListener("click", function () { if (!window.confirm("清除本机保存的 Telegram Bot 配置吗？点击右下角保存后生效。")) return; state.clearingTelegram = true; setValue("telegramBotToken", ""); setValue("telegramChatId", ""); setChecked("telegramEnabled", false); setChecked("telegramInboundEnabled", false); markDirty(); showToast("已准备清除 Telegram 配置，请保存"); });
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
