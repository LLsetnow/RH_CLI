(function () {
  "use strict";

  var state = { workflows: [], accounts: [], search: "", accountFilter: "all", editor: null, jsonWorkflow: null, configEditor: null };
  var toastTimer = 0;

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }
  function request(path, options) {
    return fetch(path, options || {}).then(function (response) {
      return response.text().then(function (raw) {
        var data = {};
        try { data = raw ? JSON.parse(raw) : {}; } catch (error) {}
        if (!response.ok) throw new Error(data.message || data.error || "请求失败");
        return data;
      });
    });
  }
  function jsonRequest(path, method, body) {
    return request(path, { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }
  function showToast(message, isError) {
    var toast = $("workflowToast");
    if (!toast) return;
    window.clearTimeout(toastTimer);
    toast.textContent = message || "";
    toast.className = "toast show" + (isError ? " error" : "");
    toastTimer = window.setTimeout(function () { toast.className = "toast"; }, 3200);
  }
  function formatTime(timestamp) {
    if (!timestamp) return "—";
    var date = new Date(Number(timestamp));
    if (isNaN(date.getTime())) return "—";
    return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  function formatSize(value) {
    var size = Number(value) || 0;
    if (size < 1024) return size + " B";
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
    return (size / (1024 * 1024)).toFixed(1) + " MB";
  }
  function siteLabel(site) {
    return site === "cn" ? "runninghub.cn" : (site === "ai" ? "runninghub.ai" : "站点未指定");
  }
  function accountLabel(record) {
    return record.account_name ? record.account_name + " · " + siteLabel(record.site) : "未绑定账号";
  }
  function safeFilename(value) {
    var name = String(value || "workflow_api.json").replace(/[\\/:*?"<>|]+/g, "_").trim() || "workflow_api.json";
    return /\.json$/i.test(name) ? name : name + ".json";
  }
  function workflowMatches(record) {
    if (state.accountFilter !== "all") {
      if (state.accountFilter === "unbound" ? record.account_id : record.account_id !== state.accountFilter) return false;
    }
    var query = state.search.toLowerCase();
    if (!query) return true;
    return [record.name, record.id, record.remote_workflow_id, record.account_name, siteLabel(record.site)].join(" ").toLowerCase().indexOf(query) !== -1;
  }
  function renderAccountFilter() {
    var select = $("workflowAccountFilter");
    var current = state.accountFilter;
    select.innerHTML = '<option value="all">全部账号</option>' +
      state.accounts.map(function (account) {
        return '<option value="' + esc(account.id) + '">' + esc(account.name) + " · " + esc(siteLabel(account.site)) + "</option>";
      }).join("") +
      '<option value="unbound">未绑定账号</option>';
    select.value = current;
    if (select.value !== current) {
      state.accountFilter = "all";
      select.value = "all";
    }
  }
  function renderAccountOptions(selected) {
    var select = $("workflowRecordAccount");
    select.innerHTML = '<option value="">未绑定账号</option>' + state.accounts.map(function (account) {
      return '<option value="' + esc(account.id) + '">' + esc(account.name) + " · " + esc(siteLabel(account.site)) + "</option>";
    }).join("");
    select.value = selected || "";
  }
  function workflowCard(record) {
    var bound = Boolean(record.account_id);
    var submitUrl = "/?workflow=" + encodeURIComponent(record.id);
    var summary = [
      ["节点", record.node_count],
      ["文件", record.file_count],
      ["提示词", record.prompt_count],
      ["尺寸", record.resolution_count],
      ["RN", record.random_noise_count]
    ].filter(function (item) { return Number(item[1]) > 0; }).map(function (item) {
      return "<span>" + esc(item[0]) + " <strong>" + esc(item[1]) + "</strong></span>";
    }).join("");
    var status = record.analysis_error ? "JSON 异常" : (bound ? "已绑定账号" : "待绑定账号");
    return '<article class="workflow-card' + (bound ? "" : " is-unbound") + '">' +
      '<div class="workflow-card-top"><button class="workflow-card-title workflow-card-title-button" type="button" data-action="edit-workflow" data-workflow-id="' + esc(record.id) + '" aria-label="编辑工作流：' + esc(record.name) + '" title="编辑工作流"><strong title="' + esc(record.name) + '">' + esc(record.name) + '</strong><small title="' + esc(record.id) + '">' + esc(record.id) + '</small></button><span class="workflow-status' + (bound ? "" : " unbound") + '">' + status + '</span></div>' +
      '<a class="workflow-card-body" href="' + esc(submitUrl) + '" aria-label="提交工作流：' + esc(record.name) + '">' +
      '<div class="workflow-card-meta">' +
      '<div class="workflow-meta-row"><span>所属账号</span><span title="' + esc(accountLabel(record)) + '">' + esc(accountLabel(record)) + '</span></div>' +
      '<div class="workflow-meta-row"><span>workflowId</span><span><code>' + esc(record.remote_workflow_id || "未设置") + '</code></span></div>' +
      '<div class="workflow-meta-row"><span>文件大小</span><span>' + esc(formatSize(record.file_size)) + '</span></div>' +
      '</div>' +
      (summary ? '<div class="workflow-node-summary">' + summary + "</div>" : "") +
      '</a>' +
      '<div class="workflow-card-footer"><span class="workflow-card-time">更新于 ' + esc(formatTime(record.updated_at)) + '</span><span class="workflow-card-actions">' +
      '<button class="workflow-card-action primary" type="button" data-action="configure-workflow" data-workflow-id="' + esc(record.id) + '">配置输入</button>' +
      '<button class="workflow-card-action" type="button" data-action="view-workflow" data-workflow-id="' + esc(record.id) + '">查看 JSON</button>' +
      '<button class="workflow-card-action" type="button" data-action="export-workflow" data-workflow-id="' + esc(record.id) + '">导出</button>' +
      '<button class="workflow-card-action danger" type="button" data-action="delete-workflow" data-workflow-id="' + esc(record.id) + '">删除</button>' +
      '</span></div></article>';
  }
  function renderWorkflows() {
    var visible = state.workflows.filter(workflowMatches);
    $("workflowCount").textContent = String(visible.length);
    $("workflowTotal").textContent = String(state.workflows.length);
    $("workflowBound").textContent = String(state.workflows.filter(function (item) { return Boolean(item.account_id); }).length);
    $("accountTotal").textContent = String(state.accounts.length);
    if (!visible.length) {
      $("workflowGroups").innerHTML = '<div class="workflow-empty"><strong>' + (state.workflows.length ? "没有匹配的工作流" : "工作流库还是空的") + '</strong><span>' + (state.workflows.length ? "试试其他搜索词或账号筛选。" : "导入一个 API JSON 后，它会出现在这里。") + "</span></div>";
      return;
    }
    var groups = {};
    visible.forEach(function (record) {
      var key = record.account_id || "__unbound__";
      if (!groups[key]) groups[key] = [];
      groups[key].push(record);
    });
    var order = state.accounts.map(function (account) { return account.id; }).concat(["__unbound__"]);
    $("workflowGroups").innerHTML = Object.keys(groups).sort(function (left, right) {
      var leftIndex = order.indexOf(left);
      var rightIndex = order.indexOf(right);
      return (leftIndex === -1 ? 9999 : leftIndex) - (rightIndex === -1 ? 9999 : rightIndex);
    }).map(function (key) {
      var records = groups[key];
      var account = state.accounts.find(function (item) { return item.id === key; });
      var title = account ? account.name : "未绑定账号";
      var subtitle = account ? siteLabel(account.site) : "导入后可在编辑中绑定";
      return '<section class="workflow-group"><div class="workflow-group-heading"><div><strong>' + esc(title) + '</strong><span>' + esc(subtitle) + '</span></div><code>' + records.length + " 个工作流</code></div><div class=\"workflow-group-cards\">" + records.map(workflowCard).join("") + "</div></section>";
    }).join("");
  }
  function refreshWorkflows() {
    return Promise.all([request("/api/workflows"), request("/api/state")]).then(function (results) {
      state.workflows = results[0].workflows || [];
      state.accounts = results[1].accounts || [];
      renderAccountFilter();
      renderWorkflows();
    }).catch(function (error) { showToast(error.message, true); });
  }
  function openEditor(record, imported) {
    state.editor = {
      mode: record ? "edit" : "import",
      id: record ? record.id : "",
      content: imported ? imported.content : "",
      sourceDir: imported ? imported.sourceDir : ""
    };
    $("workflowEditorTitle").textContent = record ? "编辑工作流资料" : "导入工作流";
    $("workflowEditorHint").textContent = record ? "修改工作流的本地显示信息，不会改变任务历史。" : "工作流 JSON 已读取，保存后会写入本机工作流库。";
    $("workflowRecordName").value = record ? record.name : imported.filename;
    $("workflowRecordRemoteId").value = record ? (record.remote_workflow_id || "") : (imported.remoteWorkflowId || "");
    renderAccountOptions(record ? record.account_id : "");
    window.RHMotion.openModal("workflowEditorModal", "workflowRecordName");
  }
  function importWorkflowFile(file) {
    if (!file) return;
    file.text().then(function (content) {
      var workflow = JSON.parse(content);
      if (!workflow || typeof workflow !== "object" || Array.isArray(workflow)) throw new Error("工作流顶层必须是 API 节点对象");
      var sourceDir = "";
      if (window.rhElectron && typeof window.rhElectron.getPathForFile === "function") {
        try {
          var path = String(window.rhElectron.getPathForFile(file) || "");
          sourceDir = path.replace(/[\\/][^\\/]*$/, "");
        } catch (error) {}
      }
      var metadata = workflow.__rh_meta__ && typeof workflow.__rh_meta__ === "object" ? workflow.__rh_meta__ : {};
      openEditor(null, { content: content, filename: file.name, sourceDir: sourceDir, remoteWorkflowId: metadata.workflowId || metadata.workflow_id || "" });
    }).catch(function (error) { showToast("读取工作流失败：" + error.message, true); });
  }
  function saveWorkflowRecord(event) {
    event.preventDefault();
    if (!state.editor) return;
    var button = $("saveWorkflowRecord");
    var name = $("workflowRecordName").value.trim();
    if (!name) return showToast("请填写工作流名称", true);
    button.disabled = true;
    var payload = {
      name: name,
      account_id: $("workflowRecordAccount").value,
      remote_workflow_id: $("workflowRecordRemoteId").value.trim()
    };
    var promise;
    if (state.editor.mode === "edit") {
      promise = jsonRequest("/api/workflows/" + encodeURIComponent(state.editor.id), "PATCH", payload);
    } else {
      payload.filename = name;
      payload.content = state.editor.content;
      payload.source_dir = state.editor.sourceDir;
      promise = jsonRequest("/api/workflows", "POST", payload);
    }
    promise.then(function () {
      window.RHMotion.closeModal("workflowEditorModal");
      showToast(state.editor.mode === "edit" ? "工作流资料已更新" : "工作流已保存到本机");
      state.editor = null;
      return refreshWorkflows();
    }).catch(function (error) {
      showToast("保存工作流失败：" + error.message, true);
    }).finally(function () { button.disabled = false; });
  }
  function fetchWorkflow(id) {
    return request("/api/workflows/" + encodeURIComponent(id));
  }

  var configKinds = [
    ["file", "文件"], ["prompt", "提示词"], ["text", "文本"], ["number", "数字"],
    ["select", "下拉选项"], ["boolean", "布尔值"], ["resolution", "尺寸"], ["random_noise", "RandomNoise"]
  ];
  function configKindLabel(kind) {
    var match = configKinds.find(function (item) { return item[0] === kind; });
    return match ? match[1] : kind || "文本";
  }
  function configItemFromCatalog(item) {
    return {
      id: String(item.id || ""), node_id: String(item.node_id || ""), field: String(item.field || ""),
      title: String(item.title || item.class_type || item.node_id || ""), class_type: String(item.class_type || ""),
      label: String(item.label || item.title || item.field || item.node_id || ""), kind: String(item.kind || "text"),
      required: Boolean(item.required), options: Array.isArray(item.options) ? item.options.slice() : [], order: 0,
      virtual: Boolean(item.virtual), default: item.default == null ? "" : item.default
    };
  }
  function defaultConfigItems(catalog) {
    return (catalog || []).filter(function (item) {
      return ["file", "prompt", "resolution", "random_noise"].indexOf(String(item.kind || "")) !== -1;
    }).map(configItemFromCatalog);
  }
  function openConfig(id) {
    fetchWorkflow(id).then(function (data) {
      var record = data.record || {};
      var catalog = data.analysis && Array.isArray(data.analysis.input_catalog) ? data.analysis.input_catalog : [];
      var saved = record.input_config && record.input_config.mode === "manual" ? record.input_config : null;
      state.configEditor = {
        id: id, record: record, catalog: catalog, mode: saved ? "manual" : "auto",
        items: saved ? saved.items.map(configItemFromCatalog) : defaultConfigItems(catalog)
      };
      $("workflowConfigTitle").textContent = "配置输入节点 · " + (record.name || "工作流");
      $("workflowConfigHint").textContent = "只保存这份工作流的输入配置，不会修改 API JSON 文件，也不会影响历史任务。";
      renderConfigBuilder();
      window.RHMotion.openModal("workflowConfigModal", "workflowConfigMode");
    }).catch(function (error) { showToast("读取输入配置失败：" + error.message, true); });
  }
  function renderConfigBuilder() {
    var editor = state.configEditor;
    if (!editor) return;
    var mode = $("workflowConfigMode");
    var builder = $("workflowConfigBuilder");
    var items = $("workflowConfigItems");
    mode.value = editor.mode;
    builder.classList.toggle("workflow-config-disabled", editor.mode !== "manual");
    items.classList.toggle("workflow-config-disabled", editor.mode !== "manual");
    var nodeSelect = $("workflowConfigNode");
    var nodeIds = [];
    editor.catalog.forEach(function (item) { if (nodeIds.indexOf(item.node_id) === -1) nodeIds.push(item.node_id); });
    var currentNode = nodeSelect.value || nodeIds[0] || "";
    nodeSelect.innerHTML = nodeIds.map(function (nodeId) {
      var item = editor.catalog.find(function (entry) { return entry.node_id === nodeId; }) || {};
      return '<option value="' + esc(nodeId) + '">' + esc(nodeId + " · " + (item.title || item.class_type || "节点")) + '</option>';
    }).join("");
    nodeSelect.value = nodeIds.indexOf(currentNode) !== -1 ? currentNode : (nodeIds[0] || "");
    renderConfigFieldOptions();
    items.innerHTML = editor.items.length ? editor.items.map(function (item, index) {
      var kindOptions = configKinds.filter(function (entry) {
        return item.virtual ? ["resolution", "random_noise"].indexOf(entry[0]) !== -1 : ["resolution", "random_noise"].indexOf(entry[0]) === -1;
      }).map(function (entry) {
        return '<option value="' + esc(entry[0]) + '"' + (item.kind === entry[0] ? " selected" : "") + '>' + esc(entry[1]) + '</option>';
      }).join("");
      var options = item.kind === "select" ? '<label class="workflow-config-options field-group"><span class="field-label">下拉选项（用逗号或换行分隔）</span><input data-config-action="options" data-config-index="' + index + '" type="text" value="' + esc((item.options || []).join(", ")) + '" placeholder="例如：low, medium, high" /></label>' : "";
      return '<div class="workflow-config-item"><div class="workflow-config-item-head"><div><div class="workflow-config-item-title" title="' + esc(item.title) + '">' + esc(item.label || item.title) + '</div><code class="workflow-config-item-id" title="' + esc(item.id) + '">' + esc(item.id) + '</code></div><button class="workflow-config-remove" type="button" data-config-action="remove" data-config-index="' + index + '">移除</button></div>' +
        '<div class="workflow-config-item-grid"><label class="field-group"><span class="field-label">显示名称</span><input data-config-action="label" data-config-index="' + index + '" type="text" value="' + esc(item.label) + '" maxlength="160" /></label><label class="field-group"><span class="field-label">输入类型</span><select data-config-action="kind" data-config-index="' + index + '">' + kindOptions + '</select></label><label class="workflow-config-required"><input data-config-action="required" data-config-index="' + index + '" type="checkbox"' + (item.required ? " checked" : "") + ' /><span>必填</span></label></div>' + options + '</div>';
    }).join("") : '<div class="workflow-config-empty">还没有选择输入字段。请从上方选择节点和字段后添加。</div>';
  }
  function renderConfigFieldOptions() {
    var editor = state.configEditor;
    if (!editor) return;
    var nodeId = $("workflowConfigNode").value;
    var fieldSelect = $("workflowConfigField");
    var fields = editor.catalog.filter(function (item) { return item.node_id === nodeId; });
    fieldSelect.innerHTML = fields.map(function (item) {
      return '<option value="' + esc(item.id) + '">' + esc(item.field ? item.field + " · " + (item.kind === "text" ? "文本" : configKindLabel(item.kind)) : configKindLabel(item.kind)) + '</option>';
    }).join("");
  }
  function addConfigItem() {
    var editor = state.configEditor;
    if (!editor || editor.mode !== "manual") return;
    var item = editor.catalog.find(function (entry) { return entry.id === $("workflowConfigField").value; });
    if (!item) return showToast("当前节点没有可配置字段", true);
    if (editor.items.some(function (entry) { return entry.id === item.id; })) return showToast("这个输入字段已经添加", true);
    editor.items.push(configItemFromCatalog(item));
    renderConfigBuilder();
  }
  function saveWorkflowConfig() {
    var editor = state.configEditor;
    if (!editor) return;
    var button = $("saveWorkflowConfig");
    button.disabled = true;
    var items = editor.items.map(function (item, index) {
      return Object.assign({}, item, { order: index });
    });
    jsonRequest("/api/workflows/" + encodeURIComponent(editor.id), "PATCH", {
      input_config: { mode: editor.mode, items: editor.mode === "manual" ? items : [] }
    }).then(function () {
      window.RHMotion.closeModal("workflowConfigModal");
      state.configEditor = null;
      showToast(editor.mode === "manual" ? "工作流输入配置已保存" : "已恢复自动识别输入");
      return refreshWorkflows();
    }).catch(function (error) { showToast("保存输入配置失败：" + error.message, true); }).finally(function () { button.disabled = false; });
  }
  function viewWorkflow(id) {
    fetchWorkflow(id).then(function (data) {
      state.jsonWorkflow = data.workflow;
      $("workflowJsonTitle").textContent = (data.record && data.record.name) || "工作流内容";
      $("workflowJsonContent").textContent = JSON.stringify(data.workflow, null, 2);
      window.RHMotion.openModal("workflowJsonModal", "closeWorkflowJson");
    }).catch(function (error) { showToast("读取工作流失败：" + error.message, true); });
  }
  function exportWorkflow(id) {
    fetchWorkflow(id).then(function (data) {
      var workflow = JSON.parse(JSON.stringify(data.workflow || {}));
      var record = data.record || {};
      var metadata = workflow.__rh_meta__ && typeof workflow.__rh_meta__ === "object" ? workflow.__rh_meta__ : {};
      if (record.remote_workflow_id) metadata.workflowId = record.remote_workflow_id;
      else delete metadata.workflowId;
      if (record.account_id) metadata.accountId = record.account_id;
      else delete metadata.accountId;
      if (Object.keys(metadata).length) workflow.__rh_meta__ = metadata;
      else delete workflow.__rh_meta__;
      var blob = new Blob([JSON.stringify(workflow, null, 2) + "\n"], { type: "application/json;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = safeFilename(record.name);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      showToast("工作流已导出，workflowId 已保留");
    }).catch(function (error) { showToast("导出工作流失败：" + error.message, true); });
  }
  function handleWorkflowAction(event) {
    var button = event.target.closest("[data-action]");
    if (!button) return;
    var id = button.dataset.workflowId;
    var action = button.dataset.action;
    if (action === "open-workflow") window.location.href = "/?workflow=" + encodeURIComponent(id);
    if (action === "configure-workflow") openConfig(id);
    if (action === "view-workflow") viewWorkflow(id);
    if (action === "export-workflow") exportWorkflow(id);
    if (action === "edit-workflow") {
      fetchWorkflow(id).then(function (data) { openEditor(data.record, null); }).catch(function (error) { showToast("打开编辑失败：" + error.message, true); });
    }
    if (action === "delete-workflow") {
      if (!window.confirm("确定删除这个工作流库副本吗？任务历史、任务快照和产物不会删除。")) return;
      button.disabled = true;
      request("/api/workflows/" + encodeURIComponent(id), { method: "DELETE" }).then(function () {
        showToast("工作流库副本已删除");
        return refreshWorkflows();
      }).catch(function (error) { showToast("删除工作流失败：" + error.message, true); }).finally(function () { button.disabled = false; });
    }
  }
  function updateThemeToggle() {
    var button = $("themeToggle");
    var icon = $("themeToggleIcon");
    var label = $("themeToggleLabel");
    var isLight = document.documentElement.dataset.theme === "light";
    icon.textContent = isLight ? "☾" : "☀";
    label.textContent = isLight ? "夜间" : "日间";
    button.setAttribute("aria-label", isLight ? "切换到夜间模式" : "切换到日间模式");
  }
  function bindEvents() {
    updateThemeToggle();
    $("themeToggle").addEventListener("click", function () {
      var nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem("rh-workflow-theme", nextTheme); } catch (error) {}
      updateThemeToggle();
    });
    $("workflowSearch").addEventListener("input", function () { state.search = this.value.trim(); renderWorkflows(); });
    $("workflowAccountFilter").addEventListener("change", function () { state.accountFilter = this.value; renderWorkflows(); });
    $("refreshWorkflows").addEventListener("click", function () {
      this.disabled = true;
      refreshWorkflows().finally(function () { $("refreshWorkflows").disabled = false; });
    });
    $("importWorkflowButton").addEventListener("click", function () { $("workflowImportFile").click(); });
    $("workflowImportFile").addEventListener("change", function () { importWorkflowFile(this.files[0]); this.value = ""; });
    $("workflowImportDropzone").addEventListener("dragenter", function (event) { event.preventDefault(); this.classList.add("dragging"); });
    $("workflowImportDropzone").addEventListener("dragover", function (event) { event.preventDefault(); this.classList.add("dragging"); });
    $("workflowImportDropzone").addEventListener("dragleave", function (event) { if (!event.relatedTarget || !this.contains(event.relatedTarget)) this.classList.remove("dragging"); });
    $("workflowImportDropzone").addEventListener("drop", function (event) {
      event.preventDefault();
      this.classList.remove("dragging");
      importWorkflowFile(event.dataTransfer.files[0]);
    });
    $("workflowGroups").addEventListener("click", handleWorkflowAction);
    $("workflowEditorForm").addEventListener("submit", saveWorkflowRecord);
    $("closeWorkflowEditor").addEventListener("click", function () { state.editor = null; window.RHMotion.closeModal("workflowEditorModal"); });
    $("cancelWorkflowEditor").addEventListener("click", function () { state.editor = null; window.RHMotion.closeModal("workflowEditorModal"); });
    $("workflowEditorModal").addEventListener("click", function (event) { if (event.target === this) window.RHMotion.closeModal("workflowEditorModal"); });
    $("closeWorkflowJson").addEventListener("click", function () { window.RHMotion.closeModal("workflowJsonModal"); });
    $("closeWorkflowJsonBottom").addEventListener("click", function () { window.RHMotion.closeModal("workflowJsonModal"); });
    $("workflowJsonModal").addEventListener("click", function (event) { if (event.target === this) window.RHMotion.closeModal("workflowJsonModal"); });
    $("workflowConfigMode").addEventListener("change", function () {
      if (!state.configEditor) return;
      state.configEditor.mode = this.value === "manual" ? "manual" : "auto";
      renderConfigBuilder();
    });
    $("workflowConfigNode").addEventListener("change", renderConfigFieldOptions);
    $("addWorkflowConfigItem").addEventListener("click", addConfigItem);
    $("saveWorkflowConfig").addEventListener("click", saveWorkflowConfig);
    $("closeWorkflowConfig").addEventListener("click", function () { state.configEditor = null; window.RHMotion.closeModal("workflowConfigModal"); });
    $("cancelWorkflowConfig").addEventListener("click", function () { state.configEditor = null; window.RHMotion.closeModal("workflowConfigModal"); });
    $("workflowConfigModal").addEventListener("click", function (event) { if (event.target === this) { state.configEditor = null; window.RHMotion.closeModal("workflowConfigModal"); } });
    $("workflowConfigItems").addEventListener("input", function (event) {
      var index = Number(event.target.dataset.configIndex);
      var editor = state.configEditor;
      if (!editor || !Number.isInteger(index) || !editor.items[index]) return;
      var action = event.target.dataset.configAction;
      if (action === "label") editor.items[index].label = event.target.value;
      if (action === "options") editor.items[index].options = event.target.value.split(/[\n,]/).map(function (value) { return value.trim(); }).filter(Boolean);
    });
    $("workflowConfigItems").addEventListener("change", function (event) {
      var index = Number(event.target.dataset.configIndex);
      var editor = state.configEditor;
      if (!editor || !Number.isInteger(index) || !editor.items[index]) return;
      var action = event.target.dataset.configAction;
      if (action === "kind") {
        editor.items[index].kind = event.target.value;
        if (event.target.value !== "select") editor.items[index].options = [];
        renderConfigBuilder();
      }
      if (action === "required") editor.items[index].required = event.target.checked;
    });
    $("workflowConfigItems").addEventListener("click", function (event) {
      var button = event.target.closest('[data-config-action="remove"]');
      if (!button || !state.configEditor) return;
      var index = Number(button.dataset.configIndex);
      if (Number.isInteger(index)) {
        state.configEditor.items.splice(index, 1);
        renderConfigBuilder();
      }
    });
    $("copyWorkflowJson").addEventListener("click", function () {
      var content = $("workflowJsonContent").textContent;
      if (!navigator.clipboard) return showToast("当前环境不支持复制，请手动选择 JSON", true);
      navigator.clipboard.writeText(content).then(function () { showToast("JSON 已复制"); }).catch(function () { showToast("复制失败，请手动选择 JSON", true); });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      window.RHMotion.closeModal("workflowEditorModal");
      window.RHMotion.closeModal("workflowJsonModal");
      window.RHMotion.closeModal("workflowConfigModal");
    });
  }
  bindEvents();
  refreshWorkflows();
})();
