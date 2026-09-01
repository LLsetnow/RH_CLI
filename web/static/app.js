(function () {
  "use strict";

  var appState = { workflowId: "", remoteWorkflowId: "", workflow: null, workflowName: "", workflowSourceDir: "", analysis: null, workflowDirty: false, bypassedNodes: {}, keys: [], accounts: [], tasks: [], settings: null, loading: false };
  var previewUrls = {};
  var credentialBusy = {};
  var accountBusy = {};
  var toastTimer = 0;
  var draftSaveTimer = 0;
  var submitButtonLabel = "";
  var submitButtonGlyph = "";
  var draftStorageWarningShown = false;
  var draftStorageKey = "rh-workflow-desk-draft-v1";
  var pendingPromptStorageKey = "rh-workflow-desk-pending-prompt-v1";
  var statusLabels = {
    queued: "排队中", submitting: "提交中", running: "执行中", completed: "已完成",
    failed: "失败", cancelled: "已取消", interrupted: "已中断", recovering: "恢复中", no_balance: "无余额",
    unchecked: "待检测", error: "检测失败"
  };
  var accountStatusLabels = {
    login_required: "待登录", ready: "已登录", checking: "签到中",
    checked_in: "今日已签到", not_checked_in: "未返回奖励", error: "账号异常"
  };
  var resolutionAspectRatios = [
    "1:1 (Square)",
    "2:3 (Portrait Photo)",
    "3:2 (Photo)",
    "3:4 (Portrait Standard)",
    "4:3 (Standard)",
    "9:16 (Portrait Widescreen)",
    "16:9 (Widescreen)",
    "21:9 (Ultrawide)"
  ];

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }
  function formatTime(timestamp) {
    if (!timestamp) return "—";
    var date = new Date(Number(timestamp));
    if (isNaN(date.getTime())) return "—";
    return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  function relativeTime(timestamp) {
    var value = Number(timestamp);
    if (!value || isNaN(value)) return "未查询";
    var seconds = Math.max(0, Math.floor((Date.now() - value) / 1000));
    if (seconds < 60) return "刚刚";
    if (seconds < 3600) return Math.floor(seconds / 60) + " 分钟前";
    if (seconds < 86400) return Math.floor(seconds / 3600) + " 小时前";
    return formatTime(value);
  }
  function statusLabel(status) { return statusLabels[status] || status || "未知"; }
  function accountStatusLabel(status) { return accountStatusLabels[status] || status || "未知"; }
  function showToast(message, isError) {
    var toast = $("toast");
    toast.textContent = message;
    toast.className = "toast show" + (isError ? " error" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.className = "toast"; }, 3600);
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
    return request(path, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
  }

  function readDraft() {
    try {
      var raw = localStorage.getItem(draftStorageKey);
      if (!raw) return null;
      var draft = JSON.parse(raw);
      return draft && draft.version === 1 ? draft : null;
    } catch (error) {
      return null;
    }
  }

  function draftCredential() {
    return { selectedKeyId: $("keySelect") ? $("keySelect").value : "" };
  }

  function saveDraftNow() {
    var previous = readDraft() || {};
    var draft = {
      version: 1,
      credential: draftCredential(),
      workflow: previous.workflow || null
    };
    if (appState.workflow && appState.analysis && appState.workflowId) {
      draft.workflow = {
        id: appState.workflowId,
        remoteWorkflowId: appState.remoteWorkflowId || $("remoteWorkflowId").value.trim(),
        name: appState.workflowName || "workflow_api.json",
        sourceDir: appState.workflowSourceDir || "",
        data: appState.workflow,
        analysis: appState.analysis,
        values: collectInputs(),
        savedAt: Date.now()
      };
    }
    try {
      localStorage.setItem(draftStorageKey, JSON.stringify(draft));
    } catch (error) {
      if (!draftStorageWarningShown) {
        draftStorageWarningShown = true;
        showToast("当前工作流过大，无法保存刷新后的恢复记录", true);
      }
    }
  }

  function scheduleDraftSave() {
    window.clearTimeout(draftSaveTimer);
    draftSaveTimer = window.setTimeout(saveDraftNow, 180);
  }

  function applyPendingPrompt() {
    var pending = null;
    try {
      var raw = localStorage.getItem(pendingPromptStorageKey);
      if (raw) pending = JSON.parse(raw);
    } catch (error) {
      pending = null;
    }
    var text = pending && pending.version === 1 ? String(pending.text || "").trim() : "";
    if (!text) return false;
    var prompt = document.querySelector('.input-card:not(.is-bypassed) .prompt-value') || document.querySelector('.prompt-value');
    if (!prompt) return false;
    prompt.value = text;
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    var card = prompt.closest(".input-card");
    var title = card && card.querySelector(".input-title") ? card.querySelector(".input-title").textContent : "提示词节点";
    var meta = card && card.querySelector("[data-prompt-meta-id]");
    if (meta) meta.textContent = "已从提示词工坊导入，可继续编辑";
    try { localStorage.removeItem(pendingPromptStorageKey); } catch (error) {}
    prompt.scrollIntoView({ behavior: "smooth", block: "center" });
    showToast("已导入成品提示词到「" + title + "」");
    return true;
  }

  function restoreDraft() {
    var draft = readDraft();
    if (!draft) return;

    var credential = draft.credential || {};
    if ($("keySelect") && credential.selectedKeyId && appState.keys.some(function (key) {
      return key.id === credential.selectedKeyId && key.status === "ready";
    })) {
      $("keySelect").value = credential.selectedKeyId;
    }

    var savedWorkflow = draft.workflow;
    if (!savedWorkflow || !savedWorkflow.data || typeof savedWorkflow.data !== "object" || !savedWorkflow.analysis || typeof savedWorkflow.analysis !== "object") return;
    appState.workflowId = String(savedWorkflow.id || "");
    appState.remoteWorkflowId = String(savedWorkflow.remoteWorkflowId || "").trim();
    appState.workflow = savedWorkflow.data;
    appState.workflowName = String(savedWorkflow.name || "workflow_api.json");
    appState.workflowSourceDir = String(savedWorkflow.sourceDir || "");
    appState.analysis = savedWorkflow.analysis;
    // The restored JSON is sent as the current workflow so edits made before
    // the refresh (including dynamically added nodes) are not lost.
    appState.workflowDirty = true;
    renderAnalysis(appState.analysis);
    setRemoteWorkflowId(appState.remoteWorkflowId);
    restoreInputValues(savedWorkflow.values || {});
    $("workflowFilename").textContent = "已恢复 " + appState.workflowName;
    $("workflowRemoteConfig").hidden = false;
    $("exportWorkflowButton").hidden = false;
    showToast("已恢复上次工作流和输入配置");
  }

  function credentialBalanceMarkup(key) {
    if (!Number(key.balance_checked_at)) {
      return '<div class="credential-balance credential-balance-empty"><span>余额</span><span>未查询</span></div>';
    }
    var symbol = key.symbol || (key.site === "cn" ? "¥" : "$");
    var balance = key.balance == null || key.balance === "" ? "—" : key.balance;
    var coins = key.coins == null || key.coins === "" ? "—" : key.coins;
    return '<div class="credential-balance"><span><span class="credential-balance-label">余额</span> <strong>' + esc(symbol) + esc(balance) + '</strong><span class="credential-coins"> · ' + esc(coins) + ' RH 币</span></span>' +
      '<span class="credential-balance-time">更新于 ' + esc(relativeTime(key.balance_checked_at)) + '</span></div>';
  }

  function credentialActionButton(key, action, label, className) {
    var busy = credentialBusy[key.id] === action;
    var busyLabel = action === "check-key" ? "检测中…" : (action === "refresh-balance" ? "刷新中…" : "删除中…");
    return '<button class="credential-action ' + className + '" type="button" data-action="' + action + '" data-key-id="' + esc(key.id) + '"' + (busy ? ' disabled' : '') + '>' +
      esc(busy ? busyLabel : label) + '</button>';
  }

  function renderKeys() {
    var list = $("credentialList");
    var select = $("keySelect");
    var current = select.value;
    if (!appState.keys.length) {
      list.innerHTML = '<div class="credential-empty">还没有保存凭据。添加后会先验证站点、余额和账户类型。</div>';
    } else {
      list.innerHTML = appState.keys.map(function (key) {
        var active = key.status === "ready";
        var statusClass = esc(key.status);
        var statusText = key.status === "ready" ? "可用" : (key.status === "no_balance" ? "无余额" : statusLabel(key.status));
        return '<div class="credential-card">' +
          '<div class="credential-top"><div class="credential-name">' + esc(key.name) + '</div>' +
          '<div class="credential-tags"><span class="status-chip ' + statusClass + '">' + statusText + '</span>' +
          '<span class="capacity-chip">' + esc(key.capacity) + ' 并发</span></div></div>' +
          '<div class="credential-key">' + esc(key.masked_key) + ' · ' + esc(key.site) + '</div>' +
          credentialBalanceMarkup(key) +
          '<div class="credential-bottom"><span>运行 ' + esc(key.active_tasks) + ' / ' + esc(key.capacity) + ' · ' + esc(key.api_type || "类型待识别") + '</span>' +
          '<span class="credential-actions">' + credentialActionButton(key, "check-key", "检测", "credential-action-check") +
          credentialActionButton(key, "refresh-balance", "刷新余额", "credential-action-refresh") +
          credentialActionButton(key, "delete-key", "删除", "credential-action-delete") + '</span></div></div>';
      }).join("");
    }
    var options = '<option value="">自动选择可用 Key</option>';
    appState.keys.filter(function (key) { return key.status === "ready"; }).forEach(function (key) {
      options += '<option value="' + esc(key.id) + '">' + esc(key.name) + ' · ' + esc(key.capacity) + '并发 · ' + esc(key.site) + '</option>';
    });
    select.innerHTML = options;
    if (current && appState.keys.some(function (key) { return key.id === current && key.status === "ready"; })) select.value = current;
  }

  function managedAccountActionButton(account, action, label, className) {
    var busy = accountBusy[account.id] === action;
    var busyLabel = action === "account-checkin" ? "签到中…" : (action === "account-login" ? "打开中…" : "删除中…");
    return '<button class="credential-action ' + className + '" type="button" data-action="' + action + '" data-account-id="' + esc(account.id) + '"' + (busy ? ' disabled' : '') + '>' +
      esc(busy ? busyLabel : label) + '</button>';
  }

  function accountRewardMarkup(account) {
    var reward = account.daily_coin == null ? "" : String(account.daily_coin).trim();
    var balance = account.balance == null ? "" : String(account.balance).trim();
    var details = [];
    if (reward) details.push('<strong>+' + esc(reward) + ' RH 币</strong> 今日登录奖励');
    if (balance) details.push('余额 ' + esc(balance));
    if (!details.length) details.push('尚未读取今日登录奖励或余额');
    var checked = Number(account.last_checkin_at) ? ' · 上次签到 ' + esc(relativeTime(account.last_checkin_at)) : '';
    return '<div class="account-reward"><span>' + details.join('<span class="account-reward-separator"> · </span>') + '</span><span class="credential-balance-time">' + esc(account.status_message || "") + esc(checked) + '</span></div>';
  }

  function renderAccounts() {
    var list = $("accountList");
    if (!list) return;
    if (!appState.accounts.length) {
      list.innerHTML = '<div class="credential-empty">还没有托管账号。添加后会在 Electron 窗口中完成一次登录。</div>';
      return;
    }
    list.innerHTML = appState.accounts.map(function (account) {
      var status = String(account.status || "login_required");
      var siteLabel = account.site === "cn" ? "runninghub.cn" : "runninghub.ai";
      return '<div class="credential-card account-card">' +
        '<div class="credential-top"><div class="credential-name">' + esc(account.name) + '</div>' +
        '<div class="credential-tags"><span class="status-chip account-status-' + esc(status) + '">' + esc(accountStatusLabel(status)) + '</span>' +
        '<span class="capacity-chip">本地会话</span></div></div>' +
        '<div class="credential-key">' + esc(siteLabel) + ' · 登录凭证保存在 Electron 本地会话</div>' +
        accountRewardMarkup(account) +
        '<div class="credential-bottom"><span>上次登录 ' + esc(account.last_login_at ? relativeTime(account.last_login_at) : "未记录") + '</span>' +
        '<span class="credential-actions">' + managedAccountActionButton(account, "account-login", "打开登录窗口", "credential-action-check") +
        managedAccountActionButton(account, "account-checkin", "签到", "credential-action-refresh") +
        managedAccountActionButton(account, "delete-account", "删除", "credential-action-delete") + '</span></div></div>';
    }).join("");
  }

  function formatTaskCost(task) {
    if (!task || task.cost == null || String(task.cost).trim() === "" || !task.cost_type) {
      return task && task.status === "completed" ? "费用未返回" : "";
    }
    if (task.cost_type === "coins") return "消耗 " + String(task.cost) + " RH 币";
    if (task.cost_type === "money") {
      var symbol = task.key_site === "ai" ? "$" : (task.key_site === "cn" ? "¥" : "");
      return "消耗 " + (symbol || "金额 ") + String(task.cost);
    }
    return "";
  }

  function taskCredentialLabel(task) {
    var name = String(task && task.key_name || "").trim() || "自动调度";
    var site = task && task.key_site === "cn" ? "runninghub.cn" : (task && task.key_site === "ai" ? "runninghub.ai" : "");
    return site ? name + " · " + site : name;
  }

  function renderTasks() {
    var list = $("queueList");
    var tasks = appState.tasks || [];
    $("queueCount").textContent = tasks.length;
    if (!tasks.length) {
      list.innerHTML = '<div class="empty-queue"><div class="empty-line"></div><strong>队列还是空的</strong><span>导入一个工作流，准备第一次提交。</span></div>';
      return;
    }
    list.innerHTML = tasks.map(function (task) {
      var outputCount = (task.outputs || []).filter(function (item) { return item.kind === "file"; }).length;
      var outputLabel = outputCount ? outputCount + " 个产物" : (task.status === "completed" ? "无文件产物" : "");
      var costLabel = formatTaskCost(task);
      var canCancel = ["queued", "submitting", "running", "recovering"].indexOf(task.status) !== -1;
      var canDelete = ["completed", "failed", "cancelled", "interrupted"].indexOf(task.status) !== -1;
      var queueLabel = task.status === "queued" && task.queue_position ? '<span>本地队列第 ' + esc(task.queue_position) + ' 位</span><span>·</span>' : "";
      var statusClass = esc(task.status);
      return '<article class="task-card ' + statusClass + '" data-task-id="' + esc(task.id) + '">' +
        '<div class="task-top"><button class="task-name task-name-button" type="button" data-action="open-task" title="打开任务详情" aria-label="打开任务 ' + esc(task.workflow_name) + '">' + esc(task.workflow_name) + '</button>' +
        '<span class="task-status ' + statusClass + '">' + statusLabel(task.status) + '</span></div>' +
        '<div class="task-meta"><span>' + esc(taskCredentialLabel(task)) + '</span><span>·</span>' + queueLabel + '<span>workflowId ' + esc(task.remote_workflow_id || "未记录") + '</span><span>·</span><span>' + formatTime(task.created_at) + '</span></div>' +
        '<div class="task-progress">' + esc(task.progress || "等待调度…") + (task.error ? '<br /><span style="color:var(--danger)">' + esc(task.error) + '</span>' : "") + '</div>' +
        '<div class="task-footer"><span class="task-footer-info"><span class="task-output-count">' + esc(outputLabel) + '</span>' + (costLabel ? '<span class="task-cost">' + esc(costLabel) + '</span>' : '') + '</span>' +
        '<span class="task-actions"><button class="task-load-button" type="button" data-action="load-task">加载</button>' +
        (canCancel ? '<button type="button" data-action="cancel-task">取消</button>' : "") +
        (canDelete ? '<button type="button" data-action="delete-task">删除</button>' : "") + '</span></div></article>';
    }).join("");
  }

  function animateTaskCard(taskId) {
    if (!taskId) return;
    var card = document.querySelector('.task-card[data-task-id="' + CSS.escape(String(taskId)) + '"]');
    if (!card) return;
    card.classList.remove("task-arrival");
    void card.offsetWidth;
    card.classList.add("task-arrival");
    window.setTimeout(function () { card.classList.remove("task-arrival"); }, 420);
  }

  function renderState(data) {
    appState.keys = data.keys || [];
    appState.accounts = data.accounts || [];
    appState.tasks = data.tasks || [];
    appState.settings = data.settings || {};
    if (document.activeElement !== $("outputDir")) $("outputDir").value = appState.settings.output_dir || "";
    if (document.activeElement !== $("personalCapacity")) $("personalCapacity").value = appState.settings.personal_capacity || 3;
    renderKeys();
    renderAccounts();
    renderTasks();
  }

  function refresh(silent) {
    if (appState.loading) return Promise.resolve();
    appState.loading = true;
    return request("/api/state").then(renderState).catch(function (error) {
      if (!silent) showToast(error.message, true);
    }).finally(function () { appState.loading = false; });
  }

  function openSettingsFromQuery() {
    var params = new URLSearchParams(window.location.search);
    if (params.get("openSettings") !== "1" || !$('settingsModal')) return;
    window.RHMotion.openModal("settingsModal", "outputDir");
    if (window.history && window.history.replaceState) {
      window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
    }
  }

  function setAnalysisStatus(message, isError) {
    var element = $("analysisStatus");
    element.hidden = !message;
    element.className = "inline-status" + (isError ? " error" : "");
    element.textContent = message || "";
  }

  function setRemoteWorkflowId(value) {
    appState.remoteWorkflowId = String(value || "").trim();
    var input = $("remoteWorkflowId");
    if (input) input.value = appState.remoteWorkflowId;
  }

  function updateThemeToggle() {
    var button = $("themeToggle");
    var icon = $("themeToggleIcon");
    var label = $("themeToggleLabel");
    if (!button || !icon || !label) return;
    var isLight = document.documentElement.dataset.theme === "light";
    icon.textContent = isLight ? "☾" : "☀";
    label.textContent = isLight ? "夜间" : "日间";
    button.setAttribute("aria-label", isLight ? "切换到夜间模式" : "切换到日间模式");
    button.title = isLight ? "切换到夜间模式" : "切换到日间模式";
  }

  function jumpToInput(inputId) {
    var card = document.querySelector('.input-card[data-input-id="' + CSS.escape(inputId) + '"]');
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.remove("input-target");
    void card.offsetWidth;
    card.classList.add("input-target");
    window.setTimeout(function () { card.classList.remove("input-target"); }, 1300);
  }

  function setProcessStep(step) {
    document.querySelectorAll(".process-step").forEach(function (button) {
      var active = button.dataset.processStep === step;
      button.classList.toggle("active", active);
      button.setAttribute("aria-current", active ? "step" : "false");
    });
  }

  function jumpToProcessStep(step) {
    var target = step === "queue" ? document.querySelector(".queue-panel") : document.querySelector(step === "inputs" ? "#workflowInputs" : ".workflow-panel");
    if (step === "inputs" && (!target || target.hidden)) target = document.querySelector(".workflow-panel");
    if (!target) return;
    setProcessStep(step);
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    target.classList.remove("process-target");
    void target.offsetWidth;
    target.classList.add("process-target");
    window.setTimeout(function () { target.classList.remove("process-target"); }, 1350);
  }

  function bypassedNodeList() {
    return Object.keys(appState.bypassedNodes || {}).filter(function (nodeId) {
      return appState.bypassedNodes[nodeId];
    });
  }

  function setBypassedNodeMap(values) {
    appState.bypassedNodes = {};
    if (Array.isArray(values)) {
      values.forEach(function (value) {
        var nodeId = String(value || "").trim().split(":", 1)[0];
        if (nodeId) appState.bypassedNodes[nodeId] = true;
      });
      return;
    }
    if (values && typeof values === "object") {
      Object.keys(values).forEach(function (value) {
        var nodeId = String(value || "").trim().split(":", 1)[0];
        if (values[value] && nodeId) appState.bypassedNodes[nodeId] = true;
      });
    }
  }

  function isNodeBypassed(nodeId) {
    return Boolean(appState.bypassedNodes && appState.bypassedNodes[nodeId]);
  }

  function bypassControlMarkup(inputId, nodeId) {
    var active = isNodeBypassed(nodeId);
    return '<button class="input-bypass-toggle' + (active ? ' is-active' : '') + '" type="button" role="switch" aria-checked="' + (active ? 'true' : 'false') + '" aria-label="' + (active ? '恢复节点' : '旁路节点') + '" title="' + (active ? '恢复节点' : '旁路节点') + '" data-action="toggle-bypass" data-input-id="' + esc(inputId) + '" data-node-id="' + esc(nodeId) + '"><span class="input-bypass-track" aria-hidden="true"><span class="input-bypass-thumb"></span></span><span class="input-bypass-label">' + (active ? '已旁路' : '旁路') + '</span></button>';
  }

  function updateBypassSummary() {
    var summary = document.querySelector(".bypass-summary");
    if (!summary) return;
    summary.innerHTML = '<strong>' + bypassedNodeList().length + '</strong> 个旁路节点';
  }

  function setNodeBypassState(nodeId, active, refreshSummary) {
    active = Boolean(active);
    if (active) appState.bypassedNodes[nodeId] = true;
    else delete appState.bypassedNodes[nodeId];
    document.querySelectorAll('.input-card[data-node-id="' + CSS.escape(nodeId) + '"]').forEach(function (card) {
      card.classList.toggle("is-bypassed", active);
      var toggle = card.querySelector('[data-action="toggle-bypass"]');
      if (toggle) {
        toggle.classList.toggle("is-active", active);
        toggle.setAttribute("aria-checked", active ? "true" : "false");
        toggle.setAttribute("aria-label", active ? "恢复节点" : "旁路节点");
        toggle.title = active ? "恢复节点" : "旁路节点";
        var label = toggle.querySelector(".input-bypass-label");
        if (label) label.textContent = active ? "已旁路" : "旁路";
      }
      card.querySelectorAll("input, textarea, select, button[data-action='pick-file'], button[data-action='pick-native-file'], button[data-action='pick-prompt']").forEach(function (control) {
        control.disabled = active;
      });
      var dropzone = card.querySelector(".file-dropzone");
      if (dropzone) dropzone.setAttribute("aria-disabled", active ? "true" : "false");
      var note = card.querySelector(".input-bypass-note");
      if (note) note.hidden = !active;
    });
    if (refreshSummary !== false) updateBypassSummary();
  }

  function applyNodeBypassStates() {
    document.querySelectorAll(".input-card[data-node-id]").forEach(function (card) {
      setNodeBypassState(card.dataset.nodeId, isNodeBypassed(card.dataset.nodeId), false);
    });
    updateBypassSummary();
  }

  function toggleNodeBypass(nodeId) {
    if (!nodeId) return;
    var active = !isNodeBypassed(nodeId);
    setNodeBypassState(nodeId, active, true);
    appState.workflowDirty = true;
    scheduleDraftSave();
    showToast(active ? "已旁路节点，本次提交会移除该节点" : "已恢复节点");
  }

  function applyRandomNoiseValues(workflow, values, bypassedNodes) {
    var bypassed = bypassedNodes || [];
    Object.keys(values || {}).forEach(function (nodeId) {
      if (bypassed.indexOf(nodeId) !== -1) return;
      var config = values[nodeId] || {};
      var node = workflow[nodeId];
      if (!node || typeof node !== "object") return;
      if (!node.inputs || typeof node.inputs !== "object") node.inputs = {};
      var seedField = Object.prototype.hasOwnProperty.call(node.inputs, "noise_seed") || !Object.prototype.hasOwnProperty.call(node.inputs, "seed") ? "noise_seed" : "seed";
      var rawSeed = String(config.seed == null ? "" : config.seed).trim();
      node.inputs[seedField] = /^-?\d+$/.test(rawSeed) ? Number(rawSeed) : rawSeed;
      node.inputs.mode = String(config.mode || "randomize").trim().toLowerCase();
    });
    return workflow;
  }

  function applyResolutionValues(workflow, values, bypassedNodes) {
    var bypassed = bypassedNodes || [];
    Object.keys(values || {}).forEach(function (nodeId) {
      if (bypassed.indexOf(nodeId) !== -1) return;
      var config = values[nodeId] || {};
      var node = workflow[nodeId];
      if (!node || typeof node !== "object") return;
      if (!node.inputs || typeof node.inputs !== "object") node.inputs = {};
      node.inputs.aspect_ratio = String(config.aspect_ratio || resolutionAspectRatios[0]).trim();
      var rawMegapixels = String(config.megapixels == null ? "" : config.megapixels).trim();
      var megapixels = Number(rawMegapixels);
      node.inputs.megapixels = Number.isFinite(megapixels) ? megapixels : rawMegapixels;
      node.inputs.multiple = 32;
    });
    return workflow;
  }

  function resolutionRatioValue(aspect) {
    var match = String(aspect || "").match(/^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)/);
    if (!match) return 16 / 9;
    var width = Number(match[1]);
    var height = Number(match[2]);
    return width > 0 && height > 0 ? width / height : 16 / 9;
  }

  function resolutionRatioLabel(aspect) {
    var value = String(aspect || "").match(/^\d+(?:\.\d+)?:\d+(?:\.\d+)?/);
    return value ? value[0] : "16:9";
  }

  function resolutionReferenceMarkup(aspect) {
    var ratio = resolutionRatioValue(aspect);
    var presets = [
      { label: "480p", megapixels: "0.4", width: 864, height: 480 },
      { label: "720p", megapixels: "0.9", width: 1280, height: 736 },
      { label: "1080p", megapixels: "2.0", width: 1920, height: 1088 },
      { label: "2K", megapixels: "2.4", width: 2048, height: 1152 },
      { label: "4K", megapixels: "4.0", width: 3840, height: 2176 }
    ];
    var rows = presets.map(function (preset) {
      var area = preset.width * preset.height;
      var width = preset.width;
      var height = preset.height;
      if (Math.abs(ratio - (16 / 9)) > 0.001) {
        width = Math.max(32, Math.ceil(Math.sqrt(area * ratio) / 32) * 32);
        height = Math.max(32, Math.ceil(Math.sqrt(area / ratio) / 32) * 32);
      }
      return '<tr data-resolution-megapixels="' + esc(preset.megapixels) + '" tabindex="0" role="button" title="点击填写 ' + esc(preset.megapixels) + '"><th scope="row">' + preset.label + '</th><td>' + preset.megapixels + '</td><td>' + width + ' × ' + height + '</td></tr>';
    }).join("");
    return '<div class="resolution-reference-heading"><strong>分辨率参考</strong><span>' + esc(resolutionRatioLabel(aspect)) + '</span></div>' +
      '<table class="resolution-reference-table"><thead><tr><th scope="col">档位</th><th scope="col">MP</th><th scope="col">输出尺寸</th></tr></thead><tbody>' + rows + '</tbody></table>' +
      '<div class="resolution-reference-note">点击任一档位即可填写；4K 按当前上限 4.0 处理。</div>';
  }

  function updateResolutionReference(card, aspect) {
    if (!card) return;
    var reference = card.querySelector(".resolution-reference-popover");
    if (reference) reference.innerHTML = resolutionReferenceMarkup(aspect);
  }

  function applyResolutionPreset(row) {
    if (!row) return;
    var card = row.closest(".resolution-card");
    var input = card && card.querySelector(".resolution-megapixels");
    if (!input) return;
    input.value = row.dataset.resolutionMegapixels || "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    showToast("已将 megapixels 设置为 " + input.value);
  }

  function applyBypassedNodes(workflow, bypassedNodes) {
    var bypassed = {};
    (bypassedNodes || []).forEach(function (nodeId) { bypassed[String(nodeId)] = true; });
    Object.keys(bypassed).forEach(function (nodeId) { delete workflow[nodeId]; });
    Object.keys(workflow || {}).forEach(function (nodeId) {
      if (nodeId === "__rh_meta__") return;
      var node = workflow[nodeId];
      var inputs = node && typeof node === "object" ? node.inputs : null;
      if (!inputs || typeof inputs !== "object") return;
      Object.keys(inputs).forEach(function (field) {
        var value = inputs[field];
        if (Array.isArray(value) && value.length >= 2 && bypassed[String(value[0])]) delete inputs[field];
      });
    });
    return workflow;
  }

  function restoreInputValues(values) {
    values = values || {};
    setBypassedNodeMap(values.bypassedNodes || values.bypassed_nodes || values.bypassedInputs || values.bypassed_inputs || []);
    Object.keys(values.files || {}).forEach(function (inputId) {
      var path = document.querySelector('.file-path[data-input-id="' + CSS.escape(inputId) + '"]');
      var value = String(values.files[inputId] || "").trim();
      if (!path) return;
      path.value = value;
      if (value) previewLocalPath(inputId, value);
    });
    Object.keys(values.prompts || {}).forEach(function (inputId) {
      var prompt = document.querySelector('.prompt-value[data-input-id="' + CSS.escape(inputId) + '"]');
      if (prompt) prompt.value = String(values.prompts[inputId] == null ? "" : values.prompts[inputId]);
    });
    Object.keys(values.randomNoise || {}).forEach(function (nodeId) {
      var config = values.randomNoise[nodeId] || {};
      var seed = document.querySelector('.random-noise-seed[data-node-id="' + CSS.escape(nodeId) + '"]');
      var mode = document.querySelector('.random-noise-mode[data-node-id="' + CSS.escape(nodeId) + '"]');
      if (seed && config.seed != null) seed.value = String(config.seed);
      if (mode && (config.mode === "fixed" || config.mode === "randomize")) mode.value = config.mode;
    });
    Object.keys(values.resolution || {}).forEach(function (nodeId) {
      var config = values.resolution[nodeId] || {};
      var aspect = document.querySelector('.resolution-aspect[data-node-id="' + CSS.escape(nodeId) + '"]');
      var megapixels = document.querySelector('.resolution-megapixels[data-node-id="' + CSS.escape(nodeId) + '"]');
      if (aspect && resolutionAspectRatios.indexOf(String(config.aspect_ratio || "")) !== -1) aspect.value = String(config.aspect_ratio);
      if (megapixels && config.megapixels != null) megapixels.value = String(config.megapixels);
    });
    applyNodeBypassStates();
  }

  function nextWorkflowNodeId() {
    var maxId = 0;
    Object.keys(appState.workflow || {}).forEach(function (nodeId) {
      if (/^\d+$/.test(nodeId)) maxId = Math.max(maxId, Number(nodeId));
    });
    return String(maxId + 1);
  }

  function addRandomNoiseNode() {
    if (!appState.workflow || !appState.analysis) {
      showToast("请先导入或加载 API 工作流", true);
      return;
    }
    var values = collectInputs();
    var nodeId = nextWorkflowNodeId();
    appState.workflow[nodeId] = {
      inputs: { noise_seed: 0, mode: "randomize" },
      class_type: "RandomNoise",
      _meta: { title: "RandomNoise" }
    };
    var analysis = JSON.parse(JSON.stringify(appState.analysis));
    analysis.random_noise_inputs = analysis.random_noise_inputs || [];
    analysis.random_noise_inputs.push({
      id: nodeId,
      node_id: nodeId,
      title: "RandomNoise",
      class_type: "RandomNoise",
      seed_field: "noise_seed",
      mode_field: "mode",
      seed: 0,
      mode: "randomize"
    });
    analysis.random_noise_count = analysis.random_noise_inputs.length;
    appState.analysis = analysis;
    appState.workflowDirty = true;
    values.randomNoise[nodeId] = { seed: "0", mode: "randomize" };
    renderAnalysis(analysis);
    restoreInputValues(values);
    saveDraftNow();
    jumpToInput(nodeId);
    showToast("已添加 RandomNoise 节点 " + nodeId);
  }

  function renderAnalysis(analysis) {
    var summary = $("workflowSummary");
    var inputs = $("workflowInputs");
    var files = analysis.file_inputs || [];
    var prompts = analysis.prompt_inputs || [];
    var randomNoise = analysis.random_noise_inputs || [];
    var resolutions = analysis.resolution_inputs || [];
    summary.hidden = false;
    summary.innerHTML = '<div class="summary-item"><strong>' + files.length + '</strong> 个文件输入</div>' +
      '<div class="summary-item"><strong>' + prompts.length + '</strong> 个提示词节点</div>' +
      '<div class="summary-item"><strong>' + resolutions.length + '</strong> 个尺寸节点</div>' +
      '<div class="summary-item"><strong>' + randomNoise.length + '</strong> 个 RandomNoise</div>' +
      '<div class="summary-item bypass-summary"><strong>' + bypassedNodeList().length + '</strong> 个旁路节点</div>' +
      '<div class="summary-item">已完成节点扫描</div>';
    var html = "";
    var inputNodes = files.map(function (item) { return { item: item, kind: "file" }; }).concat(prompts.map(function (item) { return { item: item, kind: "prompt" }; })).concat(resolutions.map(function (item) { return { item: item, kind: "resolution" }; })).concat(randomNoise.map(function (item) { return { item: item, kind: "random-noise" }; }));
    if (inputNodes.length) {
      html += '<div class="input-jump-bar"><div class="input-jump-heading"><span>输入节点</span><small>点击标签快速定位</small></div><div class="input-jump-list">';
      inputNodes.forEach(function (entry) {
        var item = entry.item;
        var icon = entry.kind === "file" ? "▧" : (entry.kind === "prompt" ? "Aa" : (entry.kind === "resolution" ? "WH" : "RN"));
        html += '<button class="input-jump-tag ' + entry.kind + '" type="button" data-action="jump-input" data-input-id="' + esc(item.id) + '" title="定位到 ' + esc(item.id) + '"><span class="input-jump-icon" aria-hidden="true">' + icon + '</span><span class="input-jump-title">' + esc(item.title || item.class_type) + '</span><code>' + esc(item.id) + '</code></button>';
      });
      html += '</div></div>';
    }
    if (files.length) {
      html += '<div class="section-kicker">文件输入 · 必填</div>';
      files.forEach(function (item) {
        var originalFileValue = String(item.default || "");
        var visibleFileValue = /^(\/|[A-Za-z]:[\\/])/.test(originalFileValue) ? originalFileValue : "";
        html += '<div class="input-card file-input-card' + (isNodeBypassed(item.node_id) ? ' is-bypassed' : '') + '" data-input-id="' + esc(item.id) + '" data-node-id="' + esc(item.node_id) + '"><div class="input-card-head"><div class="input-card-label"><div class="input-title">' + esc(item.title) + '</div><div class="input-card-subhead"><div class="input-type">' + esc(item.class_type) + '</div><span class="input-bypass-note" hidden>本次提交会移除该节点及其直接输出连线</span></div></div><div class="input-card-actions">' + bypassControlMarkup(item.id, item.node_id) + '<span class="field-code">' + esc(item.id) + '</span></div></div>' +
          '<div class="file-input-layout"><div class="file-input-controls">' +
          '<div class="file-dropzone" data-action="pick-file" data-input-id="' + esc(item.id) + '" tabindex="0" role="group" aria-label="文件拖放区域">' +
          '<span class="file-drop-mark" aria-hidden="true">↓</span><span class="file-drop-copy"><strong class="file-drop-title">拖入文件到这里</strong><small class="file-drop-hint">拖入或点击“预览”查看图片</small></span>' +
          '<input class="file-picker" data-input-id="' + esc(item.id) + '" type="file" hidden /><button class="file-button" data-action="pick-file" data-input-id="' + esc(item.id) + '" type="button">预览</button></div>' +
          '<div class="input-control-row"><input class="file-path" data-input-id="' + esc(item.id) + '" data-original-value="' + esc(originalFileValue) + '" type="text" placeholder="输入本机绝对路径（不会复制文件）" value="' + esc(visibleFileValue) + '" /><button class="file-button native-file-button" data-action="pick-native-file" data-input-id="' + esc(item.id) + '" type="button">选择文件</button></div>' +
          '<div class="file-meta" data-meta-id="' + esc(item.id) + '">点击“选择文件”后，这里会显示本机绝对路径；输入文件不会复制到项目目录。</div></div>' +
          '<figure class="file-preview" data-preview-id="' + esc(item.id) + '" hidden><figcaption>图片预览</figcaption><div class="file-preview-frame"><img alt="" /></div><div class="file-preview-name"></div></figure></div></div>';
      });
    }
    if (prompts.length) {
      html += '<div class="section-kicker prompt-section-label">提示词节点 · 可选</div>';
      prompts.forEach(function (item) {
        html += '<div class="input-card' + (isNodeBypassed(item.node_id) ? ' is-bypassed' : '') + '" data-input-id="' + esc(item.id) + '" data-node-id="' + esc(item.node_id) + '"><div class="input-card-head"><div class="input-card-label"><div class="input-title">' + esc(item.title) + '</div><div class="input-card-subhead"><div class="input-type">' + esc(item.class_type) + '</div><span class="input-bypass-note" hidden>本次提交会移除该节点及其直接输出连线</span></div></div><div class="input-card-actions">' + bypassControlMarkup(item.id, item.node_id) + '<span class="field-code">' + esc(item.id) + '</span></div></div>' +
          '<textarea class="prompt-value" data-input-id="' + esc(item.id) + '" placeholder="可以直接输入，也可以从下方加载 .txt">' + esc(item.default || "") + '</textarea>' +
          '<div class="prompt-tools"><input class="prompt-picker" data-input-id="' + esc(item.id) + '" type="file" accept=".txt,text/plain" hidden /><button class="file-button" data-action="pick-prompt" data-input-id="' + esc(item.id) + '" type="button">加载 TXT</button><span class="file-meta" data-prompt-meta-id="' + esc(item.id) + '">读取内容后仍可继续编辑</span></div></div>';
      });
    }
    if (resolutions.length) {
      html += '<div class="section-kicker resolution-section-label">尺寸节点 · 可编辑</div>';
      resolutions.forEach(function (item) {
        var aspect = resolutionAspectRatios.indexOf(String(item.aspect_ratio || "")) !== -1 ? String(item.aspect_ratio) : resolutionAspectRatios[0];
        var megapixels = item.megapixels == null || item.megapixels === "" ? 0.4 : item.megapixels;
        var options = (item.aspect_ratio_options || resolutionAspectRatios).map(function (option) {
          return '<option value="' + esc(option) + '"' + (option === aspect ? ' selected' : '') + '>' + esc(option) + '</option>';
        }).join("");
        html += '<div class="input-card resolution-card' + (isNodeBypassed(item.node_id || item.id) ? ' is-bypassed' : '') + '" data-input-id="' + esc(item.id) + '" data-node-id="' + esc(item.node_id || item.id) + '"><div class="input-card-head"><div class="input-card-label"><div class="input-title">' + esc(item.title || "尺寸") + '</div><div class="input-card-subhead"><div class="input-type">' + esc(item.class_type || "ResolutionSelector") + '</div><span class="input-bypass-note" hidden>本次提交会移除该节点及其直接输出连线</span></div></div><div class="input-card-actions">' + bypassControlMarkup(item.id, item.node_id || item.id) + '<span class="field-code">' + esc(item.id) + '</span></div></div>' +
          '<div class="resolution-grid"><label class="field-group"><span class="field-label">宽高比例</span><select class="resolution-aspect" data-node-id="' + esc(item.node_id || item.id) + '">' + options + '</select></label>' +
          '<div class="field-group resolution-megapixels-group"><div class="resolution-field-label"><span class="field-label">megapixels</span><button class="resolution-help" type="button" aria-label="查看 megapixels 分辨率参考" aria-expanded="false">?</button></div><input class="resolution-megapixels" data-node-id="' + esc(item.node_id || item.id) + '" type="number" min="0.1" max="4" step="0.1" inputmode="decimal" aria-label="megapixels" value="' + esc(megapixels) + '" /><div class="resolution-reference-popover" role="tooltip">' + resolutionReferenceMarkup(aspect) + '</div></div></div>' +
          '<div class="file-meta">megapixels 范围 0.1–4</div></div>';
      });
    }
    if (randomNoise.length) {
      html += '<div class="section-kicker random-noise-section-label">RandomNoise · 可编辑</div>';
      randomNoise.forEach(function (item) {
        var seed = item.seed == null || item.seed === "" ? 0 : item.seed;
        var mode = item.mode === "fixed" ? "fixed" : "randomize";
        html += '<div class="input-card random-noise-card' + (isNodeBypassed(item.node_id || item.id) ? ' is-bypassed' : '') + '" data-input-id="' + esc(item.id) + '" data-node-id="' + esc(item.node_id || item.id) + '"><div class="input-card-head"><div class="input-card-label"><div class="input-title">' + esc(item.title || "RandomNoise") + '</div><div class="input-card-subhead"><div class="input-type">' + esc(item.class_type || "RandomNoise") + '</div><span class="input-bypass-note" hidden>本次提交会移除该节点及其直接输出连线</span></div></div><div class="input-card-actions">' + bypassControlMarkup(item.id, item.node_id || item.id) + '<span class="field-code">' + esc(item.id) + '</span></div></div>' +
          '<div class="random-noise-grid"><label class="field-group"><span class="field-label">随机种子</span><input class="random-noise-seed" data-node-id="' + esc(item.node_id || item.id) + '" type="number" inputmode="numeric" step="1" value="' + esc(seed) + '" /></label>' +
          '<label class="field-group"><span class="field-label">模式</span><select class="random-noise-mode" data-node-id="' + esc(item.node_id || item.id) + '"><option value="fixed"' + (mode === "fixed" ? " selected" : "") + '>fixed</option><option value="randomize"' + (mode === "randomize" ? " selected" : "") + '>randomize</option></select></label></div>' +
          '<div class="file-meta">导出或提交时写入 ' + esc(item.seed_field || "noise_seed") + ' 和 mode</div></div>';
      });
    }
    if (!files.length && !prompts.length && !resolutions.length && !randomNoise.length) html += '<div class="empty-queue" style="min-height:130px"><strong>没有识别到可填写输入</strong><span>当前工作流没有需要在这里配置的输入节点。</span></div>';
    inputs.innerHTML = html;
    inputs.hidden = false;
    $("submitStrip").hidden = false;
    setProcessStep("inputs");
    applyNodeBypassStates();
    files.forEach(function (item) {
      var value = String(item.default || "").trim();
      if (/^(\/|[A-Za-z]:[\\/])/.test(value) || appState.workflowSourceDir) previewLocalPath(item.id, value);
    });
  }

  function analyzeFile(file) {
    if (!file) return;
    setAnalysisStatus("正在读取并分析工作流…", false);
    $("workflowFilename").textContent = file.name;
    $("workflowRemoteConfig").hidden = true;
    setRemoteWorkflowId("");
    appState.workflowSourceDir = "";
    if (window.rhElectron && typeof window.rhElectron.getPathForFile === "function") {
      try {
        var workflowPath = String(window.rhElectron.getPathForFile(file) || "").trim();
        if (workflowPath) appState.workflowSourceDir = workflowPath.replace(/[\\/][^\\/]*$/, "");
      } catch (error) {}
    }
    file.text().then(function (content) {
      return jsonRequest("/api/workflows/analyze", "POST", { filename: file.name, content: content }).then(function (data) {
        return { data: data, workflow: JSON.parse(content) };
      });
    }).then(function (result) {
      var data = result.data;
      appState.workflowId = data.workflow_id;
      setRemoteWorkflowId(data.remote_workflow_id || (data.analysis && data.analysis.remote_workflow_id) || "");
      appState.workflow = result.workflow;
      appState.workflowName = file.name;
      appState.workflowDirty = false;
      appState.analysis = data.analysis;
      setBypassedNodeMap(data.analysis && data.analysis.bypassed_nodes);
      renderAnalysis(data.analysis);
      $("workflowRemoteConfig").hidden = false;
      $("exportWorkflowButton").hidden = false;
      setAnalysisStatus("工作流已识别，可以准备输入并提交。", false);
      applyPendingPrompt();
      saveDraftNow();
      showToast("工作流分析完成");
    }).catch(function (error) {
      appState.workflowId = "";
      setRemoteWorkflowId("");
      appState.workflow = null;
      appState.workflowName = "";
      appState.workflowSourceDir = "";
      appState.workflowDirty = false;
      $("workflowInputs").hidden = true;
      $("submitStrip").hidden = true;
      $("workflowRemoteConfig").hidden = true;
      $("exportWorkflowButton").hidden = true;
      setAnalysisStatus(error.message, true);
    });
  }

  function loadTask(task) {
    return request("/api/tasks/" + encodeURIComponent(task.id) + "/load").then(function (data) {
      var savedTask = data.task || task;
      appState.workflowId = data.workflow_id || "";
      appState.remoteWorkflowId = String(savedTask.remote_workflow_id || (data.analysis && data.analysis.remote_workflow_id) || "").trim();
      appState.workflow = data.workflow || null;
      appState.workflowName = savedTask.workflow_name || data.filename || "workflow_api.json";
      appState.workflowSourceDir = "";
      appState.workflowDirty = false;
      appState.analysis = data.analysis || {};
      renderAnalysis(appState.analysis);
      setRemoteWorkflowId(appState.remoteWorkflowId);
      restoreInputValues({
        files: savedTask.files || {},
        prompts: savedTask.prompts || {},
        randomNoise: savedTask.random_noise || {},
        resolution: savedTask.resolution || {},
        bypassedNodes: savedTask.bypassed_nodes || savedTask.bypassed_inputs || []
      });
      applyPendingPrompt();
      if ($("keySelect") && savedTask.key_id && appState.keys.some(function (key) {
        return key.id === savedTask.key_id && key.status === "ready";
      })) {
        $("keySelect").value = savedTask.key_id;
      }
      $("workflowFilename").textContent = "已加载 " + appState.workflowName;
      $("workflowRemoteConfig").hidden = false;
      $("exportWorkflowButton").hidden = false;
      setAnalysisStatus("已加载任务数据，可以继续修改后提交。", false);
      var panel = document.querySelector(".workflow-panel");
      if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
      saveDraftNow();
      showToast("已加载任务：" + appState.workflowName);
    });
  }

  function recordInputFile(inputId, file) {
    return recordInputFileWithEvent(inputId, file, null);
  }

  function droppedFilePath(event, file) {
    if (window.rhElectron && typeof window.rhElectron.getPathForFile === "function") {
      try {
        var electronPath = String(window.rhElectron.getPathForFile(file) || "").trim();
        if (electronPath && /^(\/|[A-Za-z]:[\\/])/.test(electronPath)) return electronPath;
      } catch (error) {}
    }
    var directPath = String(file && file.path || "").trim();
    if (directPath && /^(\/|[A-Za-z]:[\\/])/.test(directPath)) return directPath;
    var transfer = event && event.dataTransfer;
    if (!transfer || typeof transfer.getData !== "function") return "";
    var uri = String(transfer.getData("text/uri-list") || "").split(/\r?\n/).find(function (item) {
      return item.indexOf("file://") === 0;
    });
    if (!uri) return "";
    try {
      var path = decodeURIComponent(new URL(uri).pathname);
      return /^(\/|[A-Za-z]:[\\/])/.test(path) ? path : "";
    } catch (error) {
      return "";
    }
  }

  function recordInputFileWithEvent(inputId, file, event) {
    if (isNodeBypassed(String(inputId).split(":", 1)[0])) return;
    var meta = document.querySelector('[data-meta-id="' + CSS.escape(inputId) + '"]');
    var path = document.querySelector('.file-path[data-input-id="' + CSS.escape(inputId) + '"]');
    var zone = document.querySelector('.file-dropzone[data-input-id="' + CSS.escape(inputId) + '"]');
    var card = zone && zone.closest(".file-input-card");
    var selectedPath = droppedFilePath(event, file);
    updateImagePreview(inputId, file);
    if (path) path.value = selectedPath;
    if (card) card.classList.remove("is-loading", "is-dragging");
    if (zone) {
      zone.classList.remove("is-loading", "is-dragging");
      zone.classList.add("is-ready");
      zone.querySelector(".file-drop-title").textContent = "已选择 " + file.name;
      zone.querySelector(".file-drop-hint").textContent = selectedPath ? "路径已记录，可重新拖入替换" : "预览已显示，请点击旁边的选择文件记录路径";
    }
    if (card) card.classList.add("is-ready");
    if (meta) meta.textContent = selectedPath ? file.name + " · 路径已记录，不会复制文件" : file.name + " · 已显示预览；请点击旁边的选择文件同步绝对路径";
    scheduleDraftSave();
  }

  function clearImagePreview(inputId) {
    var preview = document.querySelector('.file-preview[data-preview-id="' + CSS.escape(inputId) + '"]');
    if (!preview) return;
    if (previewUrls[inputId] && previewUrls[inputId].indexOf("blob:") === 0) URL.revokeObjectURL(previewUrls[inputId]);
    delete previewUrls[inputId];
    preview.querySelector("img").removeAttribute("src");
    preview.querySelector("img").removeAttribute("alt");
    preview.querySelector(".file-preview-name").textContent = "";
    preview.hidden = true;
  }

  function pickNativeInput(inputId, button) {
    if (isNodeBypassed(String(inputId).split(":", 1)[0])) return;
    var path = document.querySelector('.file-path[data-input-id="' + CSS.escape(inputId) + '"]');
    var meta = document.querySelector('[data-meta-id="' + CSS.escape(inputId) + '"]');
    var zone = document.querySelector('.file-dropzone[data-input-id="' + CSS.escape(inputId) + '"]');
    var card = zone && zone.closest(".file-input-card");
    var original = button.textContent;
    button.disabled = true;
    button.textContent = "选择中…";
    request("/api/pick-file", { method: "POST" }).then(function (selected) {
      if (path) path.value = selected.path;
      setPathPreview(inputId, selected);
      if (card) card.classList.remove("is-loading", "is-dragging");
      if (zone) {
        zone.classList.remove("is-loading", "is-dragging");
        zone.classList.add("is-ready");
        zone.querySelector(".file-drop-title").textContent = "已选择 " + selected.name;
        zone.querySelector(".file-drop-hint").textContent = "路径已记录，可重新选择";
      }
      if (card) card.classList.add("is-ready");
      if (meta) meta.textContent = selected.name + " · 路径已记录，不会复制文件";
      scheduleDraftSave();
      showToast("已记录本机文件路径");
    }).catch(function (error) {
      showToast(error.message, true);
    }).finally(function () {
      button.disabled = false;
      button.textContent = original;
    });
  }

  function chooseOutputDirectory() {
    if (window.rhElectron && typeof window.rhElectron.selectDirectory === "function") {
      return Promise.resolve(window.rhElectron.selectDirectory()).then(function (path) {
        return String(path || "").trim();
      });
    }
    return request("/api/pick-directory", { method: "POST" }).then(function (selected) {
      return String(selected.path || "").trim();
    });
  }

  function pickOutputDirectory(button) {
    var original = button.textContent;
    button.disabled = true;
    button.textContent = "选择中…";
    chooseOutputDirectory().then(function (path) {
      if (!path) return;
      $("outputDir").value = path;
      showToast("已选择产物目录，请点击“保存路径”确认");
    }).catch(function (error) {
      showToast(error.message, true);
    }).finally(function () {
      button.disabled = false;
      button.textContent = original;
    });
  }

  function setPathPreview(inputId, selected) {
    var preview = document.querySelector('.file-preview[data-preview-id="' + CSS.escape(inputId) + '"]');
    if (!preview) return;
    clearImagePreview(inputId);
    if (!selected || !selected.preview_url) return;
    previewUrls[inputId] = selected.preview_url;
    preview.querySelector("img").src = selected.preview_url;
    preview.querySelector("img").alt = selected.name || "输入图片预览";
    preview.querySelector(".file-preview-name").textContent = selected.name || "";
    preview.hidden = false;
  }

  function previewLocalPath(inputId, value) {
    var normalized = String(value || "").trim();
    var requestPath = normalized;
    if (!/^(\/|[A-Za-z]:[\\/])/.test(requestPath) && appState.workflowSourceDir && requestPath && !/^[a-z]+:\/\//i.test(requestPath)) {
      requestPath = appState.workflowSourceDir.replace(/[\\/]$/, "") + "/" + requestPath.replace(/^[/\\]+/, "");
    }
    if (!/^(\/|[A-Za-z]:[\\/])/.test(requestPath)) {
      clearImagePreview(inputId);
      return;
    }
    request("/api/preview-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: requestPath })
    }).then(function (selected) {
      var path = document.querySelector('.file-path[data-input-id="' + CSS.escape(inputId) + '"]');
      if (!path || (path.value.trim() !== normalized && path.value.trim() !== requestPath && path.value.trim() !== "")) return;
      if (path.value.trim() !== selected.path) path.value = selected.path;
      var zone = document.querySelector('.file-dropzone[data-input-id="' + CSS.escape(inputId) + '"]');
      var card = zone && zone.closest(".file-input-card");
      var meta = document.querySelector('[data-meta-id="' + CSS.escape(inputId) + '"]');
      setPathPreview(inputId, selected);
      if (card) card.classList.add("is-ready");
      if (zone) {
        zone.classList.add("is-ready");
        zone.querySelector(".file-drop-title").textContent = "已识别 " + selected.name;
        zone.querySelector(".file-drop-hint").textContent = selected.preview_url ? "已从本机路径加载预览，可重新选择" : "路径已记录，可重新选择";
      }
      if (meta) meta.textContent = selected.preview_url ? selected.name + " · 已从本机路径加载预览" : selected.name + " · 路径已记录，不会复制文件";
    }).catch(function () {
      // 工作流中的旧路径可能已经失效；保留路径，让用户可以直接替换它。
    });
  }

  function updateImagePreview(inputId, file) {
    var preview = document.querySelector('.file-preview[data-preview-id="' + CSS.escape(inputId) + '"]');
    if (!preview) return;
    if (previewUrls[inputId] && previewUrls[inputId].indexOf("blob:") === 0) URL.revokeObjectURL(previewUrls[inputId]);
    previewUrls[inputId] = "";
    var filename = String(file && file.name || "");
    var isImage = Boolean(file && ((String(file.type || "").indexOf("image/") === 0) || (/\.(png|jpe?g|gif|webp|bmp|avif)$/i).test(filename)));
    if (!isImage) {
      preview.hidden = true;
      return;
    }
    var url = URL.createObjectURL(file);
    previewUrls[inputId] = url;
    preview.querySelector("img").src = url;
    preview.querySelector("img").alt = filename || "输入图片预览";
    preview.querySelector(".file-preview-name").textContent = filename;
    preview.hidden = false;
  }

  function collectInputs() {
    var files = {};
    document.querySelectorAll(".file-path").forEach(function (input) { files[input.dataset.inputId] = input.value.trim(); });
    var prompts = {};
    document.querySelectorAll(".prompt-value").forEach(function (input) { prompts[input.dataset.inputId] = input.value; });
    var randomNoise = {};
    document.querySelectorAll(".random-noise-card").forEach(function (card) {
      var nodeId = card.dataset.nodeId || card.dataset.inputId;
      var seed = card.querySelector(".random-noise-seed");
      var mode = card.querySelector(".random-noise-mode");
      randomNoise[nodeId] = { seed: seed ? seed.value.trim() : "", mode: mode ? mode.value.trim() : "" };
    });
    var resolution = {};
    document.querySelectorAll(".resolution-card").forEach(function (card) {
      var nodeId = card.dataset.nodeId || card.dataset.inputId;
      var aspect = card.querySelector(".resolution-aspect");
      var megapixels = card.querySelector(".resolution-megapixels");
      resolution[nodeId] = { aspect_ratio: aspect ? aspect.value.trim() : "", megapixels: megapixels ? megapixels.value.trim() : "" };
    });
    return { files: files, prompts: prompts, randomNoise: randomNoise, resolution: resolution, bypassedNodes: bypassedNodeList() };
  }

  function exportWorkflow() {
    if (!appState.workflow) return showToast("请先导入 API 工作流", true);
    var workflow;
    try {
      workflow = JSON.parse(JSON.stringify(appState.workflow));
    } catch (error) {
      return showToast("当前工作流无法导出", true);
    }
    delete workflow.__rh_meta__;
    var currentRemoteWorkflowId = $("remoteWorkflowId") ? $("remoteWorkflowId").value.trim() : "";
    var values = collectInputs();
    var metadata = {};
    if (currentRemoteWorkflowId) metadata.workflowId = currentRemoteWorkflowId;
    if (Object.keys(metadata).length) workflow.__rh_meta__ = metadata;
    var changes = 0;
    [values.files, values.prompts].forEach(function (group) {
      Object.keys(group).forEach(function (inputId) {
        if (values.bypassedNodes.indexOf(inputId.split(":", 1)[0]) !== -1) return;
        var separator = inputId.indexOf(":");
        if (separator <= 0) return;
        var nodeId = inputId.slice(0, separator);
        var field = inputId.slice(separator + 1);
        var node = workflow[nodeId];
        if (!node || typeof node !== "object") return;
        if (!node.inputs || typeof node.inputs !== "object") node.inputs = {};
        var value = group[inputId];
        if (group === values.files && !value) {
          var fileInput = document.querySelector('.file-path[data-input-id="' + CSS.escape(inputId) + '"]');
          value = fileInput ? (fileInput.dataset.originalValue || "") : "";
        }
        node.inputs[field] = value;
        changes += 1;
      });
    });
    applyRandomNoiseValues(workflow, values.randomNoise, values.bypassedNodes);
    changes += Object.keys(values.randomNoise).filter(function (nodeId) {
      return values.bypassedNodes.indexOf(nodeId) === -1;
    }).length * 2;
    applyResolutionValues(workflow, values.resolution, values.bypassedNodes);
    changes += Object.keys(values.resolution).filter(function (nodeId) {
      return values.bypassedNodes.indexOf(nodeId) === -1;
    }).length * 2;
    applyBypassedNodes(workflow, values.bypassedNodes);
    var sourceName = appState.workflowName || "workflow_api.json";
    var stem = sourceName.replace(/\.json$/i, "") || "workflow";
    var blob = new Blob([JSON.stringify(workflow, null, 2) + "\n"], { type: "application/json;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = stem + "_modified_api.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    showToast("已导出当前 API 工作流，保留了 " + changes + " 个输入配置" + (values.bypassedNodes.length ? "，已旁路 " + values.bypassedNodes.length + " 个节点" : "") + (currentRemoteWorkflowId ? "和 workflowId" : ""));
  }

  function submitTask() {
    var button = $("submitButton");
    if (button.disabled) return;
    if (!appState.workflowId) return showToast("请先导入 API 工作流", true);
    var currentRemoteWorkflowId = $("remoteWorkflowId") ? $("remoteWorkflowId").value.trim() : "";
    if (!currentRemoteWorkflowId) {
      showToast("请先填写 RunningHub workflowId", true);
      $("remoteWorkflowId").focus();
      return;
    }
    var values = collectInputs();
    var required = (appState.analysis && appState.analysis.file_inputs) || [];
    var missing = required.some(function (item) {
      return values.bypassedNodes.indexOf(item.node_id) === -1 && !values.files[item.id];
    });
    if (missing) return showToast("请先为所有文件输入选择本地文件", true);
    var invalidNoise = Object.keys(values.randomNoise).some(function (nodeId) {
      if (values.bypassedNodes.indexOf(nodeId) !== -1) return false;
      var config = values.randomNoise[nodeId];
      return !/^-?\d+$/.test(String(config.seed || "").trim()) || ["fixed", "randomize"].indexOf(config.mode) === -1;
    });
    if (invalidNoise) return showToast("RandomNoise 的随机种子必须是整数，模式只能是 fixed 或 randomize", true);
    var invalidResolution = Object.keys(values.resolution).some(function (nodeId) {
      if (values.bypassedNodes.indexOf(nodeId) !== -1) return false;
      var config = values.resolution[nodeId];
      var megapixels = Number(String(config.megapixels || "").trim());
      return resolutionAspectRatios.indexOf(String(config.aspect_ratio || "").trim()) === -1 || !Number.isFinite(megapixels) || megapixels < 0.1 || megapixels > 4;
    });
    if (invalidResolution) return showToast("尺寸节点的比例无效，megapixels 范围必须是 0.1 到 4", true);
    var workflowPayload = null;
    if (appState.workflowDirty && appState.workflow) {
      try {
        workflowPayload = JSON.parse(JSON.stringify(appState.workflow));
        applyRandomNoiseValues(workflowPayload, values.randomNoise, values.bypassedNodes);
        applyResolutionValues(workflowPayload, values.resolution, values.bypassedNodes);
      } catch (error) {
        return showToast("当前工作流无法保存", true);
      }
    }
    button.disabled = true;
    button.classList.add("is-submitting");
    button.setAttribute("aria-busy", "true");
    var label = button.querySelector(".button-label");
    var glyph = button.querySelector(".button-glyph");
    submitButtonLabel = label ? label.textContent : "加入任务队列";
    submitButtonGlyph = glyph ? glyph.textContent : "→";
    if (label) label.textContent = "加入中…";
    if (glyph) glyph.textContent = "↻";
    jsonRequest("/api/tasks", "POST", {
      workflow_id: appState.workflowId,
      workflow: workflowPayload,
      workflow_name: appState.workflowName,
      remote_workflow_id: currentRemoteWorkflowId,
      files: values.files,
      prompts: values.prompts,
      random_noise: values.randomNoise,
      resolution: values.resolution,
      bypassed_nodes: values.bypassedNodes,
      key_id: $("keySelect").value || null,
      output_dir: $("outputDir").value.trim() || null
    }).then(function (data) {
      showToast("任务已加入本地队列");
      return refresh(true).then(function () {
        jumpToProcessStep("queue");
        animateTaskCard(data && data.task && data.task.id);
      });
    }).catch(function (error) { showToast(error.message, true); }).finally(function () {
      button.disabled = false;
      button.classList.remove("is-submitting");
      button.removeAttribute("aria-busy");
      if (label) label.textContent = submitButtonLabel || "加入任务队列";
      if (glyph) glyph.textContent = submitButtonGlyph || "→";
    });
  }

  function stageLabel(stage) {
    return {
      queue: "排队",
      dispatch: "调度",
      prepare: "准备",
      upload: "输入上传",
      submit: "提交",
      poll: "远程轮询",
      download: "保存产物",
      complete: "完成",
      failed: "失败",
      cancelled: "取消"
    }[stage] || stage || "阶段";
  }

  function logTime(timestamp) {
    if (!timestamp) return "—";
    var date = new Date(Number(timestamp));
    if (isNaN(date.getTime())) return "—";
    return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function jsonPreview(value) {
    try { return esc(JSON.stringify(value, null, 2)); } catch (error) { return esc(String(value)); }
  }

  function renderDiagnostics(task) {
    var diagnostics = $("modalDiagnostics");
    var logs = Array.isArray(task.stage_logs) ? task.stage_logs : [];
    var errorDetail = task.error_detail;
    var hasErrorDetail = errorDetail && (typeof errorDetail !== "object" || Object.keys(errorDetail).length > 0);
    var logMarkup = logs.length ? '<ol class="stage-log-list">' + logs.map(function (log) {
      var detail = log && log.detail != null ? '<details class="stage-log-detail"><summary>查看阶段详情</summary><pre>' + jsonPreview(log.detail) + '</pre></details>' : '';
      return '<li class="stage-log-item ' + (log.level === "error" ? "error" : (log.level === "warning" ? "warning" : "")) + '">' +
        '<div class="stage-log-marker"></div><div class="stage-log-body"><div class="stage-log-top"><span class="stage-log-stage">' + esc(stageLabel(log.stage)) + '</span><time>' + logTime(log.at) + '</time></div>' +
        '<div class="stage-log-message">' + esc(log.message || "") + '</div>' + detail + '</div></li>';
    }).join("") + '</ol>' : '<div class="diagnostics-empty">暂无阶段日志</div>';
    var errorMarkup = hasErrorDetail ? '<details class="error-detail" open><summary>错误详情</summary><pre>' + jsonPreview(errorDetail) + '</pre></details>' : '';
    diagnostics.innerHTML = '<section class="diagnostics-section"><div class="diagnostics-heading"><span>阶段日志</span><span class="diagnostics-count">' + logs.length + '</span></div>' + logMarkup + errorMarkup + '</section>';
  }

  function stopTaskMedia() {
    var outputs = $("modalOutputs");
    if (!outputs) return;
    outputs.querySelectorAll("video, audio").forEach(function (media) {
      try { media.pause(); } catch (error) {}
      media.removeAttribute("src");
      try { media.load(); } catch (error) {}
    });
  }

  function closeTaskModal() {
    stopTaskMedia();
    window.RHMotion.closeModal("taskModal");
  }

  function openTask(task) {
    stopTaskMedia();
    $("modalTitle").textContent = task.workflow_name || "任务详情";
    var meta = $("modalMeta");
    var costLabel = formatTaskCost(task);
    meta.innerHTML = '<span>' + statusLabel(task.status) + '</span><span>凭据：' + esc(taskCredentialLabel(task)) + '</span><span>workflowId：' + esc(task.remote_workflow_id || "未记录") + '</span><span>taskId：' + esc(task.remote_task_id || "尚未返回") + '</span>' + (costLabel ? '<span>' + esc(costLabel) + '</span>' : '') + '<span>' + formatTime(task.created_at) + '</span>';
    renderDiagnostics(task);
    var outputs = $("modalOutputs");
    var items = task.outputs || [];
    if (!items.length) outputs.innerHTML = '<div class="output-empty">任务当前没有可预览的产物。</div>';
    else {
      var fileIndex = 0;
      outputs.innerHTML = items.map(function (item) {
        if (item.kind === "text") return '<div class="output-item"><div class="output-label">TEXT / ' + esc(item.node_id || "output") + '</div><pre>' + esc(item.text) + '</pre></div>';
        var url = "/api/tasks/" + encodeURIComponent(task.id) + "/output/" + fileIndex++;
        var type = String(item.mime || "");
        var content = type.indexOf("image/") === 0 ? '<img src="' + url + '" alt="' + esc(item.name) + '" />' :
          type.indexOf("video/") === 0 ? '<video src="' + url + '" controls preload="metadata"></video>' :
          type.indexOf("audio/") === 0 ? '<audio src="' + url + '" controls preload="metadata"></audio>' :
          '<a class="output-link" href="' + url + '" target="_blank" rel="noreferrer">打开或下载文件</a>';
        return '<div class="output-item"><div class="output-label">' + esc(item.name || "output") + '</div>' + content + '</div>';
      }).join("");
    }
    window.RHMotion.openModal("taskModal", "closeModal");
  }

  function handleQueueClick(event) {
    var action = event.target.dataset.action;
    var card = event.target.closest("[data-task-id]");
    if (!card || !action) return;
    var task = appState.tasks.find(function (item) { return item.id === card.dataset.taskId; });
    if (!task) return;
    if (action === "load-task") {
      loadTask(task).catch(function (error) { showToast(error.message, true); });
      return;
    }
    if (action === "open-task") openTask(task);
    if (action === "cancel-task") {
      jsonRequest("/api/tasks/" + encodeURIComponent(task.id) + "/cancel", "POST", {}).then(function () {
        showToast("已请求取消任务");
        refresh(true);
      }).catch(function (error) { showToast(error.message, true); });
    }
    if (action === "delete-task") {
      if (!window.confirm("删除这条本地任务记录吗？已下载产物不会被删除。")) return;
      request("/api/tasks/" + encodeURIComponent(task.id), { method: "DELETE" }).then(function () {
        showToast("任务记录已删除");
        refresh(true);
      }).catch(function (error) { showToast(error.message, true); });
    }
  }

  function handleCredentialClick(event) {
    var trigger = event.target.closest("button[data-action]");
    if (!trigger) return;
    var action = trigger.dataset.action;
    var keyId = trigger.dataset.keyId;
    if (!action || !keyId) return;
    if (credentialBusy[keyId]) return;
    if (action === "check-key" || action === "refresh-balance") {
      credentialBusy[keyId] = action;
      renderKeys();
      var endpoint = action === "check-key" ? "/check" : "/balance";
      request("/api/keys/" + encodeURIComponent(keyId) + endpoint, { method: "POST" }).then(function (data) {
        if (action === "check-key") {
          showToast(data.key.status === "ready" ? "凭据检测成功，余额已更新" : data.key.status_message, data.key.status !== "ready");
        } else {
          showToast("余额已更新");
        }
        return refresh(true);
      }).catch(function (error) { showToast(error.message, true); }).finally(function () {
        delete credentialBusy[keyId];
        renderKeys();
      });
    }
    if (action === "delete-key") {
      if (!window.confirm("确定删除这个本地凭据吗？")) return;
      credentialBusy[keyId] = action;
      renderKeys();
      request("/api/keys/" + encodeURIComponent(keyId), { method: "DELETE" }).then(function () {
        showToast("凭据已删除");
        refresh(true);
      }).catch(function (error) { showToast(error.message, true); }).finally(function () {
        delete credentialBusy[keyId];
        renderKeys();
      });
    }
  }

  function callElectronAccount(method, account) {
    if (!window.rhElectron || typeof window.rhElectron[method] !== "function") {
      return Promise.reject(new Error("账号托管需要通过 Electron 开发版运行，请使用 npm run electron"));
    }
    return Promise.resolve(window.rhElectron[method](account));
  }

  function handleAccountClick(event) {
    var trigger = event.target.closest("button[data-action]");
    if (!trigger) return;
    var action = trigger.dataset.action;
    var accountId = trigger.dataset.accountId;
    var account = appState.accounts.find(function (item) { return item.id === accountId; });
    if (!account || !action || accountBusy[accountId]) return;
    if (action === "account-login" || action === "account-checkin") {
      accountBusy[accountId] = action;
      renderAccounts();
      var method = action === "account-login" ? "openAccountLogin" : "accountCheckin";
      callElectronAccount(method, account).then(function (result) {
        if (action === "account-login") {
          showToast(result && result.status === "ready" ? "账号已登录，会话已保存在本机" : "已打开账号窗口，请完成登录");
        } else {
          var success = result && result.status === "checked_in";
          showToast(result && result.message ? result.message : "签到状态已更新", !success);
        }
        return refresh(true);
      }).catch(function (error) { showToast(error.message, true); }).finally(function () {
        delete accountBusy[accountId];
        renderAccounts();
      });
      return;
    }
    if (action === "delete-account") {
      if (!window.confirm("确定删除这个本地托管账号记录吗？Electron 中保存的登录会话不会被删除。")) return;
      accountBusy[accountId] = action;
      renderAccounts();
      request("/api/accounts/" + encodeURIComponent(accountId), { method: "DELETE" }).then(function () {
        showToast("托管账号记录已删除");
        return refresh(true);
      }).catch(function (error) { showToast(error.message, true); }).finally(function () {
        delete accountBusy[accountId];
        renderAccounts();
      });
    }
  }

  function addManagedAccount() {
    var button = $("addAccount");
    var name = $("accountName").value.trim();
    var site = $("accountSite").value;
    button.disabled = true;
    jsonRequest("/api/accounts", "POST", { name: name, site: site }).then(function (data) {
      $("accountName").value = "";
      showToast("账号已保存，正在打开登录窗口");
      return refresh(true).then(function () { return callElectronAccount("openAccountLogin", data.account); });
    }).catch(function (error) { showToast(error.message, true); }).finally(function () {
      button.disabled = false;
    });
  }

  function bindEvents() {
    updateThemeToggle();
    var processNav = document.querySelector(".process-nav");
    if (processNav) processNav.addEventListener("click", function (event) {
      var button = event.target.closest("[data-process-step]");
      if (button) jumpToProcessStep(button.dataset.processStep);
    });
    $("themeToggle").addEventListener("click", function () {
      var nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem("rh-workflow-theme", nextTheme); } catch (error) {}
      updateThemeToggle();
    });
    $("workflowFile").addEventListener("change", function () { analyzeFile(this.files[0]); });
    $("remoteWorkflowId").addEventListener("input", function () {
      appState.remoteWorkflowId = this.value.trim();
      scheduleDraftSave();
    });
    $("keySelect").addEventListener("change", scheduleDraftSave);
    var dropzone = $("workflowDropzone");
    ["dragenter", "dragover"].forEach(function (name) { dropzone.addEventListener(name, function (event) { event.preventDefault(); dropzone.classList.add("dragging"); }); });
    ["dragleave", "drop"].forEach(function (name) { dropzone.addEventListener(name, function (event) { event.preventDefault(); dropzone.classList.remove("dragging"); }); });
    dropzone.addEventListener("drop", function (event) { analyzeFile(event.dataTransfer.files[0]); });
    $("workflowInputs").addEventListener("click", function (event) {
      var resolutionPreset = event.target.closest("tr[data-resolution-megapixels]");
      if (resolutionPreset && $("workflowInputs").contains(resolutionPreset)) {
        applyResolutionPreset(resolutionPreset);
        return;
      }
      var trigger = event.target.closest("[data-action]");
      if (!trigger || !$("workflowInputs").contains(trigger)) return;
      var action = trigger.dataset.action;
      var inputId = trigger.dataset.inputId;
      if (action === "toggle-bypass") {
        toggleNodeBypass(trigger.dataset.nodeId || String(inputId || "").split(":", 1)[0]);
        return;
      }
      var inputCard = trigger.closest(".input-card");
      if (inputCard && inputCard.classList.contains("is-bypassed") && ["pick-file", "pick-native-file", "pick-prompt"].indexOf(action) !== -1) return;
      if (action === "jump-input") jumpToInput(inputId);
      if (action === "add-random-noise") addRandomNoiseNode();
      if (action === "pick-file") document.querySelector('.file-picker[data-input-id="' + CSS.escape(inputId) + '"]').click();
      if (action === "pick-native-file") pickNativeInput(inputId, trigger);
      if (action === "pick-prompt") document.querySelector('.prompt-picker[data-input-id="' + CSS.escape(inputId) + '"]').click();
    });
    $("workflowInputs").addEventListener("input", function (event) {
      if (event.target.classList.contains("random-noise-seed") || event.target.classList.contains("resolution-megapixels")) appState.workflowDirty = true;
      scheduleDraftSave();
    });
    $("workflowInputs").addEventListener("change", function (event) {
      if (event.target.classList.contains("random-noise-mode") || event.target.classList.contains("resolution-aspect") || event.target.classList.contains("resolution-megapixels")) appState.workflowDirty = true;
      if (event.target.classList.contains("resolution-aspect")) updateResolutionReference(event.target.closest(".resolution-card"), event.target.value);
      scheduleDraftSave();
    });
    $("workflowInputs").addEventListener("focusin", function (event) {
      if (event.target.classList.contains("resolution-help")) event.target.setAttribute("aria-expanded", "true");
    });
    $("workflowInputs").addEventListener("focusout", function (event) {
      if (event.target.classList.contains("resolution-help")) event.target.setAttribute("aria-expanded", "false");
    });
    $("workflowInputs").addEventListener("keydown", function (event) {
      var resolutionPreset = event.target.closest("tr[data-resolution-megapixels]");
      if (resolutionPreset && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        applyResolutionPreset(resolutionPreset);
        return;
      }
      var zone = event.target.closest(".file-dropzone");
      if (!zone || (event.key !== "Enter" && event.key !== " ")) return;
      var card = zone.closest(".file-input-card");
      if (card && card.classList.contains("is-bypassed")) return;
      event.preventDefault();
      document.querySelector('.file-picker[data-input-id="' + CSS.escape(zone.dataset.inputId) + '"]').click();
    });
    $("workflowInputs").addEventListener("dragenter", function (event) {
      var card = event.target.closest(".file-input-card");
      if (!card || card.classList.contains("is-bypassed")) return;
      event.preventDefault();
      card.classList.add("is-dragging");
      var zone = card.querySelector(".file-dropzone");
      if (zone) zone.classList.add("is-dragging");
    });
    $("workflowInputs").addEventListener("dragover", function (event) {
      var card = event.target.closest(".file-input-card");
      if (!card || card.classList.contains("is-bypassed")) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      card.classList.add("is-dragging");
      var zone = card.querySelector(".file-dropzone");
      if (zone) zone.classList.add("is-dragging");
    });
    $("workflowInputs").addEventListener("dragleave", function (event) {
      var card = event.target.closest(".file-input-card");
      if (!card || card.classList.contains("is-bypassed") || (event.relatedTarget && card.contains(event.relatedTarget))) return;
      card.classList.remove("is-dragging");
      var zone = card.querySelector(".file-dropzone");
      if (zone) zone.classList.remove("is-dragging");
    });
    $("workflowInputs").addEventListener("drop", function (event) {
      var card = event.target.closest(".file-input-card");
      if (!card) return;
      event.preventDefault();
      if (card.classList.contains("is-bypassed")) return;
      card.classList.remove("is-dragging");
      var zone = card.querySelector(".file-dropzone");
      if (zone) zone.classList.remove("is-dragging");
      var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (!file) return;
      card.classList.add("is-loading");
      if (zone) zone.classList.add("is-loading");
      recordInputFileWithEvent(card.dataset.inputId, file, event);
    });
    $("workflowInputs").addEventListener("change", function (event) {
      var inputId = event.target.dataset.inputId;
      if (event.target.classList.contains("file-picker") && event.target.files[0]) {
        if (isNodeBypassed(String(inputId || "").split(":", 1)[0])) return;
        var zone = document.querySelector('.file-dropzone[data-input-id="' + CSS.escape(inputId) + '"]');
        if (zone) zone.classList.add("is-loading");
        recordInputFile(inputId, event.target.files[0]);
      }
      if (event.target.classList.contains("prompt-picker") && event.target.files[0]) {
        if (isNodeBypassed(String(inputId || "").split(":", 1)[0])) return;
        var file = event.target.files[0];
        file.text().then(function (text) {
          var textarea = document.querySelector('.prompt-value[data-input-id="' + CSS.escape(inputId) + '"]');
          if (textarea) textarea.value = text;
          var meta = document.querySelector('[data-prompt-meta-id="' + CSS.escape(inputId) + '"]');
          if (meta) meta.textContent = file.name + " · 已加载，可继续编辑";
          scheduleDraftSave();
        }).catch(function (error) { showToast(error.message, true); });
      }
    });
    $("workflowInputs").addEventListener("blur", function (event) {
      if (event.target.classList.contains("file-path")) {
        previewLocalPath(event.target.dataset.inputId, event.target.value);
        scheduleDraftSave();
      }
    }, true);
    $("submitButton").addEventListener("click", submitTask);
    $("exportWorkflowButton").addEventListener("click", exportWorkflow);
    $("queueList").addEventListener("click", handleQueueClick);
    $("credentialList").addEventListener("click", handleCredentialClick);
    $("accountList").addEventListener("click", handleAccountClick);
    $("accountForm").addEventListener("submit", function (event) {
      event.preventDefault();
      addManagedAccount();
    });
    $("addKey").addEventListener("click", function () {
      var button = this;
      var apiKey = $("keyValue").value.trim();
      if (!apiKey) return showToast("请输入 API Key", true);
      button.disabled = true;
      jsonRequest("/api/keys", "POST", { name: $("keyName").value.trim(), site: $("keySite").value, api_key: apiKey }).then(function (data) {
        $("keyValue").value = "";
        $("keyName").value = "";
        showToast(data.key.status === "ready" ? "凭据已验证并保存，余额已更新" : data.key.status_message, data.key.status !== "ready");
        refresh(true);
      }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; });
    });
    $("refreshKeys").addEventListener("click", function () {
      var button = this;
      button.disabled = true;
      Promise.all(appState.keys.map(function (key) { return request("/api/keys/" + encodeURIComponent(key.id) + "/check", { method: "POST" }); }))
        .then(function () { showToast("凭据已刷新"); return refresh(true); })
        .catch(function (error) { showToast(error.message, true); })
        .finally(function () { button.disabled = false; });
    });
    $("saveOutputDir").addEventListener("click", function () {
      var value = $("outputDir").value.trim();
      jsonRequest("/api/settings", "PATCH", { output_dir: value }).then(function (data) {
        $("outputDir").value = data.output_dir;
        showToast("默认产物目录已保存");
      }).catch(function (error) { showToast(error.message, true); });
    });
    $("savePersonalCapacity").addEventListener("click", function () {
      var value = $("personalCapacity").value.trim();
      jsonRequest("/api/settings", "PATCH", { personal_capacity: value }).then(function (data) {
        $("personalCapacity").value = data.personal_capacity;
        renderKeys();
        showToast("个人并发数已保存");
      }).catch(function (error) { showToast(error.message, true); });
    });
    $("chooseOutputDir").addEventListener("click", function () { pickOutputDirectory(this); });
    $("openSettings").addEventListener("click", function () {
      window.RHMotion.openModal("settingsModal", "outputDir");
    });
    $("closeSettings").addEventListener("click", function () { window.RHMotion.closeModal("settingsModal"); });
    $("settingsModal").addEventListener("click", function (event) { if (event.target === $("settingsModal")) window.RHMotion.closeModal("settingsModal"); });
    $("credentialForm").addEventListener("submit", function (event) { event.preventDefault(); });
    $("closeModal").addEventListener("click", closeTaskModal);
    $("taskModal").addEventListener("click", function (event) { if (event.target === $("taskModal")) closeTaskModal(); });
    document.addEventListener("keydown", function (event) {
      if (event.ctrlKey && event.key === "Enter" && !event.shiftKey && !event.altKey && !event.metaKey && !event.isComposing) {
        if ($("settingsModal").hidden && $("taskModal").hidden) {
          event.preventDefault();
          submitTask();
        }
        return;
      }
      if (event.key !== "Escape") return;
      closeTaskModal();
      window.RHMotion.closeModal("settingsModal");
    });
  }

  bindEvents();
  refresh(false).then(function () {
    restoreDraft();
    applyPendingPrompt();
    openSettingsFromQuery();
  });
  window.addEventListener("beforeunload", function () {
    window.clearTimeout(draftSaveTimer);
    saveDraftNow();
  });
  window.setInterval(function () { refresh(true); }, 1500);
})();
