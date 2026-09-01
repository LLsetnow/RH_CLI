(function () {
  "use strict";

  var appState = { workflowId: "", workflow: null, workflowName: "", analysis: null, keys: [], tasks: [], settings: null, loading: false };
  var previewUrls = {};
  var toastTimer = 0;
  var statusLabels = {
    queued: "排队中", submitting: "提交中", running: "执行中", completed: "已完成",
    failed: "失败", cancelled: "已取消", interrupted: "已中断", no_balance: "无余额",
    unchecked: "待检测", error: "检测失败"
  };

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
  function statusLabel(status) { return statusLabels[status] || status || "未知"; }
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
          '<div class="credential-bottom"><span>运行 ' + esc(key.active_tasks) + ' / ' + esc(key.capacity) + ' · ' + esc(key.api_type || "类型待识别") + '</span>' +
          '<span class="credential-actions"><button type="button" data-action="check-key" data-key-id="' + esc(key.id) + '">检测</button>' +
          '<button type="button" data-action="delete-key" data-key-id="' + esc(key.id) + '">删除</button></span></div></div>';
      }).join("");
    }
    var options = '<option value="">自动选择可用 Key</option>';
    appState.keys.filter(function (key) { return key.status === "ready"; }).forEach(function (key) {
      options += '<option value="' + esc(key.id) + '">' + esc(key.name) + ' · ' + esc(key.capacity) + '并发 · ' + esc(key.site) + '</option>';
    });
    select.innerHTML = options;
    if (current && appState.keys.some(function (key) { return key.id === current && key.status === "ready"; })) select.value = current;
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
      var canCancel = ["queued", "submitting", "running"].indexOf(task.status) !== -1;
      var canDelete = ["completed", "failed", "cancelled", "interrupted"].indexOf(task.status) !== -1;
      var statusClass = esc(task.status);
      return '<article class="task-card ' + statusClass + '" data-task-id="' + esc(task.id) + '">' +
        '<div class="task-top"><div class="task-name" title="' + esc(task.workflow_name) + '">' + esc(task.workflow_name) + '</div>' +
        '<span class="task-status ' + statusClass + '">' + statusLabel(task.status) + '</span></div>' +
        '<div class="task-meta"><span>' + esc(task.key_name || "自动调度") + '</span><span>·</span><span>' + formatTime(task.created_at) + '</span></div>' +
        '<div class="task-progress">' + esc(task.progress || "等待调度…") + (task.error ? '<br /><span style="color:var(--danger)">' + esc(task.error) + '</span>' : "") + '</div>' +
        '<div class="task-footer"><span class="task-output-count">' + (outputCount ? outputCount + ' 个产物' : (task.status === "completed" ? "无文件产物" : "")) + '</span>' +
        '<span class="task-actions"><button type="button" data-action="open-task">打开</button>' +
        (canCancel ? '<button type="button" data-action="cancel-task">取消</button>' : "") +
        (canDelete ? '<button type="button" data-action="delete-task">删除</button>' : "") + '</span></div></article>';
    }).join("");
  }

  function renderState(data) {
    appState.keys = data.keys || [];
    appState.tasks = data.tasks || [];
    appState.settings = data.settings || {};
    if (document.activeElement !== $("outputDir")) $("outputDir").value = appState.settings.output_dir || "";
    renderKeys();
    renderTasks();
  }

  function refresh(silent) {
    if (appState.loading) return Promise.resolve();
    appState.loading = true;
    return request("/api/state").then(renderState).catch(function (error) {
      if (!silent) showToast(error.message, true);
    }).finally(function () { appState.loading = false; });
  }

  function setAnalysisStatus(message, isError) {
    var element = $("analysisStatus");
    element.hidden = !message;
    element.className = "inline-status" + (isError ? " error" : "");
    element.textContent = message || "";
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

  function renderAnalysis(analysis) {
    var summary = $("workflowSummary");
    var inputs = $("workflowInputs");
    var files = analysis.file_inputs || [];
    var prompts = analysis.prompt_inputs || [];
    summary.hidden = false;
    summary.innerHTML = '<div class="summary-item"><strong>' + files.length + '</strong> 个文件输入</div>' +
      '<div class="summary-item"><strong>' + prompts.length + '</strong> 个提示词节点</div>' +
      '<div class="summary-item">已完成节点扫描</div>';
    var html = "";
    if (files.length) {
      html += '<div class="section-kicker">文件输入 · 必填</div>';
      files.forEach(function (item) {
        var originalFileValue = String(item.default || "");
        var visibleFileValue = /^(\/|[A-Za-z]:[\\/])/.test(originalFileValue) ? originalFileValue : "";
        html += '<div class="input-card file-input-card"><div class="input-card-head"><div><div class="input-title">' + esc(item.title) + '</div><div class="input-type">' + esc(item.class_type) + '</div></div><span class="field-code">' + esc(item.id) + '</span></div>' +
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
        html += '<div class="input-card"><div class="input-card-head"><div><div class="input-title">' + esc(item.title) + '</div><div class="input-type">' + esc(item.class_type) + '</div></div><span class="field-code">' + esc(item.id) + '</span></div>' +
          '<textarea class="prompt-value" data-input-id="' + esc(item.id) + '" placeholder="可以直接输入，也可以从下方加载 .txt">' + esc(item.default || "") + '</textarea>' +
          '<div class="prompt-tools"><input class="prompt-picker" data-input-id="' + esc(item.id) + '" type="file" accept=".txt,text/plain" hidden /><button class="file-button" data-action="pick-prompt" data-input-id="' + esc(item.id) + '" type="button">加载 TXT</button><span class="file-meta" data-prompt-meta-id="' + esc(item.id) + '">读取内容后仍可继续编辑</span></div></div>';
      });
    }
    if (!files.length && !prompts.length) html = '<div class="empty-queue" style="min-height:130px"><strong>没有识别到可填写输入</strong><span>这个工作流可能只依赖固定节点参数。</span></div>';
    inputs.innerHTML = html;
    inputs.hidden = false;
    $("submitStrip").hidden = false;
  }

  function analyzeFile(file) {
    if (!file) return;
    setAnalysisStatus("正在读取并分析工作流…", false);
    $("workflowFilename").textContent = file.name;
    file.text().then(function (content) {
      return jsonRequest("/api/workflows/analyze", "POST", { filename: file.name, content: content }).then(function (data) {
        return { data: data, workflow: JSON.parse(content) };
      });
    }).then(function (result) {
      var data = result.data;
      appState.workflowId = data.workflow_id;
      appState.workflow = result.workflow;
      appState.workflowName = file.name;
      appState.analysis = data.analysis;
      renderAnalysis(data.analysis);
      $("exportWorkflowButton").hidden = false;
      setAnalysisStatus("工作流已识别，可以准备输入并提交。", false);
      showToast("工作流分析完成");
    }).catch(function (error) {
      appState.workflowId = "";
      appState.workflow = null;
      appState.workflowName = "";
      $("workflowInputs").hidden = true;
      $("submitStrip").hidden = true;
      $("exportWorkflowButton").hidden = true;
      setAnalysisStatus(error.message, true);
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
    var meta = document.querySelector('[data-meta-id="' + CSS.escape(inputId) + '"]');
    var path = document.querySelector('.file-path[data-input-id="' + CSS.escape(inputId) + '"]');
    var zone = document.querySelector('.file-dropzone[data-input-id="' + CSS.escape(inputId) + '"]');
    var selectedPath = droppedFilePath(event, file);
    updateImagePreview(inputId, file);
    if (path) path.value = selectedPath;
    if (zone) {
      zone.classList.remove("is-loading", "is-dragging");
      zone.classList.add("is-ready");
      zone.querySelector(".file-drop-title").textContent = "已选择 " + file.name;
      zone.querySelector(".file-drop-hint").textContent = selectedPath ? "路径已记录，可重新拖入替换" : "预览已显示，请点击旁边的选择文件记录路径";
    }
    if (meta) meta.textContent = selectedPath ? file.name + " · 路径已记录，不会复制文件" : file.name + " · 已显示预览；请点击旁边的选择文件同步绝对路径";
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
      var path = document.querySelector('.file-path[data-input-id="' + CSS.escape(inputId) + '"]');
      var meta = document.querySelector('[data-meta-id="' + CSS.escape(inputId) + '"]');
      var zone = document.querySelector('.file-dropzone[data-input-id="' + CSS.escape(inputId) + '"]');
    var original = button.textContent;
    button.disabled = true;
    button.textContent = "选择中…";
    request("/api/pick-file", { method: "POST" }).then(function (selected) {
      if (path) path.value = selected.path;
      setPathPreview(inputId, selected);
      if (zone) {
        zone.classList.remove("is-loading", "is-dragging");
        zone.classList.add("is-ready");
        zone.querySelector(".file-drop-title").textContent = "已选择 " + selected.name;
        zone.querySelector(".file-drop-hint").textContent = "路径已记录，可重新选择";
      }
      if (meta) meta.textContent = selected.name + " · 路径已记录，不会复制文件";
      showToast("已记录本机文件路径");
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
    return { files: files, prompts: prompts };
  }

  function exportWorkflow() {
    if (!appState.workflow) return showToast("请先导入 API 工作流", true);
    var workflow;
    try {
      workflow = JSON.parse(JSON.stringify(appState.workflow));
    } catch (error) {
      return showToast("当前工作流无法导出", true);
    }
    var values = collectInputs();
    var changes = 0;
    [values.files, values.prompts].forEach(function (group) {
      Object.keys(group).forEach(function (inputId) {
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
    showToast("已导出当前 API 工作流，保留了 " + changes + " 个输入配置");
  }

  function submitTask() {
    if (!appState.workflowId) return showToast("请先导入 API 工作流", true);
    var values = collectInputs();
    var required = (appState.analysis && appState.analysis.file_inputs) || [];
    var missing = required.some(function (item) { return !values.files[item.id]; });
    if (missing) return showToast("请先为所有文件输入选择本地文件", true);
    var button = $("submitButton");
    button.disabled = true;
    jsonRequest("/api/tasks", "POST", {
      workflow_id: appState.workflowId,
      files: values.files,
      prompts: values.prompts,
      key_id: $("keySelect").value || null,
      output_dir: $("outputDir").value.trim() || null
    }).then(function () {
      showToast("任务已加入本地队列");
      refresh(true);
    }).catch(function (error) { showToast(error.message, true); }).finally(function () { button.disabled = false; });
  }

  function openTask(task) {
    $("modalTitle").textContent = task.workflow_name || "任务详情";
    var meta = $("modalMeta");
    meta.innerHTML = '<span>' + statusLabel(task.status) + '</span><span>' + esc(task.key_name || "自动调度") + '</span><span>' + esc(task.remote_task_id || "尚未返回 taskId") + '</span><span>' + formatTime(task.created_at) + '</span>';
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
    $("taskModal").hidden = false;
  }

  function handleQueueClick(event) {
    var action = event.target.dataset.action;
    var card = event.target.closest("[data-task-id]");
    if (!card || !action) return;
    var task = appState.tasks.find(function (item) { return item.id === card.dataset.taskId; });
    if (!task) return;
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
    var action = event.target.dataset.action;
    var keyId = event.target.dataset.keyId;
    if (!action || !keyId) return;
    if (action === "check-key") {
      event.target.disabled = true;
      request("/api/keys/" + encodeURIComponent(keyId) + "/check", { method: "POST" }).then(function (data) {
        showToast(data.key.status === "ready" ? "凭据检测成功" : data.key.status_message, data.key.status !== "ready");
        return refresh(true);
      }).catch(function (error) { showToast(error.message, true); }).finally(function () { event.target.disabled = false; });
    }
    if (action === "delete-key") {
      if (!window.confirm("确定删除这个本地凭据吗？")) return;
      request("/api/keys/" + encodeURIComponent(keyId), { method: "DELETE" }).then(function () {
        showToast("凭据已删除");
        refresh(true);
      }).catch(function (error) { showToast(error.message, true); });
    }
  }

  function bindEvents() {
    updateThemeToggle();
    $("themeToggle").addEventListener("click", function () {
      var nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem("rh-workflow-theme", nextTheme); } catch (error) {}
      updateThemeToggle();
    });
    $("workflowFile").addEventListener("change", function () { analyzeFile(this.files[0]); });
    var dropzone = $("workflowDropzone");
    ["dragenter", "dragover"].forEach(function (name) { dropzone.addEventListener(name, function (event) { event.preventDefault(); dropzone.classList.add("dragging"); }); });
    ["dragleave", "drop"].forEach(function (name) { dropzone.addEventListener(name, function (event) { event.preventDefault(); dropzone.classList.remove("dragging"); }); });
    dropzone.addEventListener("drop", function (event) { analyzeFile(event.dataTransfer.files[0]); });
    $("workflowInputs").addEventListener("click", function (event) {
      var trigger = event.target.closest("[data-action]");
      if (!trigger || !$("workflowInputs").contains(trigger)) return;
      var action = trigger.dataset.action;
      var inputId = trigger.dataset.inputId;
      if (action === "pick-file") document.querySelector('.file-picker[data-input-id="' + CSS.escape(inputId) + '"]').click();
      if (action === "pick-native-file") pickNativeInput(inputId, trigger);
      if (action === "pick-prompt") document.querySelector('.prompt-picker[data-input-id="' + CSS.escape(inputId) + '"]').click();
    });
    $("workflowInputs").addEventListener("keydown", function (event) {
      var zone = event.target.closest(".file-dropzone");
      if (!zone || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault();
      document.querySelector('.file-picker[data-input-id="' + CSS.escape(zone.dataset.inputId) + '"]').click();
    });
    $("workflowInputs").addEventListener("dragenter", function (event) {
      var zone = event.target.closest(".file-dropzone");
      if (!zone) return;
      event.preventDefault();
      zone.classList.add("is-dragging");
    });
    $("workflowInputs").addEventListener("dragover", function (event) {
      var zone = event.target.closest(".file-dropzone");
      if (!zone) return;
      event.preventDefault();
      zone.classList.add("is-dragging");
    });
    $("workflowInputs").addEventListener("dragleave", function (event) {
      var zone = event.target.closest(".file-dropzone");
      if (zone && !zone.contains(event.relatedTarget)) zone.classList.remove("is-dragging");
    });
    $("workflowInputs").addEventListener("drop", function (event) {
      var zone = event.target.closest(".file-dropzone");
      if (!zone) return;
      event.preventDefault();
      zone.classList.remove("is-dragging");
      var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (!file) return;
      zone.classList.add("is-loading");
      recordInputFileWithEvent(zone.dataset.inputId, file, event);
    });
    $("workflowInputs").addEventListener("change", function (event) {
      var inputId = event.target.dataset.inputId;
      if (event.target.classList.contains("file-picker") && event.target.files[0]) {
        var zone = document.querySelector('.file-dropzone[data-input-id="' + CSS.escape(inputId) + '"]');
        if (zone) zone.classList.add("is-loading");
        recordInputFile(inputId, event.target.files[0]);
      }
      if (event.target.classList.contains("prompt-picker") && event.target.files[0]) {
        var file = event.target.files[0];
        file.text().then(function (text) {
          var textarea = document.querySelector('.prompt-value[data-input-id="' + CSS.escape(inputId) + '"]');
          if (textarea) textarea.value = text;
          var meta = document.querySelector('[data-prompt-meta-id="' + CSS.escape(inputId) + '"]');
          if (meta) meta.textContent = file.name + " · 已加载，可继续编辑";
        }).catch(function (error) { showToast(error.message, true); });
      }
    });
    $("submitButton").addEventListener("click", submitTask);
    $("exportWorkflowButton").addEventListener("click", exportWorkflow);
    $("queueList").addEventListener("click", handleQueueClick);
    $("credentialList").addEventListener("click", handleCredentialClick);
    $("addKey").addEventListener("click", function () {
      var button = this;
      var apiKey = $("keyValue").value.trim();
      if (!apiKey) return showToast("请输入 API Key", true);
      button.disabled = true;
      jsonRequest("/api/keys", "POST", { name: $("keyName").value.trim(), site: $("keySite").value, api_key: apiKey }).then(function (data) {
        $("keyValue").value = "";
        $("keyName").value = "";
        showToast(data.key.status === "ready" ? "凭据已验证并保存" : data.key.status_message, data.key.status !== "ready");
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
    $("openSettings").addEventListener("click", function () {
      $("settingsModal").hidden = false;
      window.setTimeout(function () { $("outputDir").focus(); }, 0);
    });
    $("closeSettings").addEventListener("click", function () { $("settingsModal").hidden = true; });
    $("settingsModal").addEventListener("click", function (event) { if (event.target === $("settingsModal")) $("settingsModal").hidden = true; });
    $("credentialForm").addEventListener("submit", function (event) { event.preventDefault(); });
    $("closeModal").addEventListener("click", function () { $("taskModal").hidden = true; });
    $("taskModal").addEventListener("click", function (event) { if (event.target === $("taskModal")) $("taskModal").hidden = true; });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      $("taskModal").hidden = true;
      $("settingsModal").hidden = true;
    });
  }

  bindEvents();
  refresh(false);
  window.setInterval(function () { refresh(true); }, 1500);
})();
