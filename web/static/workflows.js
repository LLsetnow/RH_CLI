(function () {
  "use strict";

  var state = { workflows: [], folders: [], accounts: [], promptGroups: [], promptGroupFolders: [], telegram: {}, activeFolderId: "", editingFolderId: "", folderSavingId: "", activePromptGroupFolderId: "", editingPromptGroupFolderId: "", promptGroupFolderSavingId: "", contextFolderId: "", contextWorkflowId: "", contextPromptGroupFolderId: "", contextPromptGroupId: "", selectedWorkflowId: "", loadingWorkflowId: "", editor: null, configEditor: null };
  var draftStorageKey = "rh-workflow-desk-draft-v1";
  var pendingPromptGroupStorageKey = "rh-workflow-desk-pending-prompt-group-v1";

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
    if (window.RHMotion && window.RHMotion.showToast) window.RHMotion.showToast(toast, message, isError);
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
  function renderAccountOptions(selected) {
    var select = $("workflowRecordAccount");
    select.innerHTML = '<option value="">未绑定账号</option>' + state.accounts.map(function (account) {
      return '<option value="' + esc(account.id) + '">' + esc(account.name) + " · " + esc(siteLabel(account.site)) + "</option>";
    }).join("");
    select.value = selected || "";
  }
  function renderPromptGroupOptions(selected) {
    var select = $("workflowRecordPromptGroup");
    if (!select) return;
    var options = '<option value="">不关联提示词组</option>' + state.promptGroups.map(function (group) {
      return '<option value="' + esc(group.id) + '">' + esc(group.name) + '</option>';
    }).join("");
    select.innerHTML = options;
    select.value = selected || "";
  }
  function workflowCard(record) {
    var bound = Boolean(record.account_id);
    var selected = state.selectedWorkflowId === record.id;
    var loading = state.loadingWorkflowId === record.id;
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
    return '<article class="workflow-card' + (bound ? "" : " is-unbound") + (selected ? " is-selected" : "") + '" draggable="true" data-workflow-drag-id="' + esc(record.id) + '">' +
      '<div class="workflow-card-top"><button class="workflow-card-title workflow-card-title-button" type="button" data-action="edit-workflow" data-workflow-id="' + esc(record.id) + '" aria-label="编辑工作流：' + esc(record.name) + '" title="编辑工作流"><strong title="' + esc(record.name) + '">' + esc(record.name) + '</strong><small title="' + esc(record.id) + '">' + esc(record.id) + '</small></button><span class="workflow-status' + (bound ? "" : " unbound") + '">' + status + '</span></div>' +
      '<button class="workflow-card-body" type="button" data-action="select-workflow" data-workflow-id="' + esc(record.id) + '" aria-pressed="' + (selected ? "true" : "false") + '" aria-busy="' + (loading ? "true" : "false") + '"' + (loading ? " disabled" : "") + ' aria-label="选择并加载工作流与提示词组：' + esc(record.name) + '" title="加载到任务提交页和提示词工坊">' +
      '<div class="workflow-card-meta">' +
      '<div class="workflow-meta-row"><span>所属账号</span><span title="' + esc(accountLabel(record)) + '">' + esc(accountLabel(record)) + '</span></div>' +
      '<div class="workflow-meta-row"><span>workflowId</span><span><code>' + esc(record.remote_workflow_id || "未设置") + '</code></span></div>' +
      '<div class="workflow-meta-row"><span>提示词组</span><span title="' + esc(record.prompt_group_name || "未关联") + '">' + esc(record.prompt_group_name || "未关联") + '</span></div>' +
      '<div class="workflow-meta-row"><span>文件大小</span><span>' + esc(formatSize(record.file_size)) + '</span></div>' +
      '</div>' +
      (summary ? '<div class="workflow-node-summary">' + summary + "</div>" : "") +
      '</button>' +
      '<div class="workflow-card-footer"><span class="workflow-card-time">更新于 ' + esc(formatTime(record.updated_at)) + '</span></div></article>';
  }
  function folderRecord(folderId) {
    return state.folders.find(function (folder) { return folder.id === folderId; }) || null;
  }
  function recordsInFolder(folderId) {
    return state.workflows.filter(function (record) { return String(record.folder_id || "") === String(folderId || ""); });
  }
  function promptGroupFolderRecord(folderId) {
    return state.promptGroupFolders.find(function (folder) { return folder.id === folderId; }) || null;
  }
  function promptGroupsInFolder(folderId) {
    return state.promptGroups.filter(function (group) { return String(group.folder_id || "") === String(folderId || ""); });
  }
  function workflowFolderCard(folder) {
    var records = recordsInFolder(folder.id);
    var folderId = esc(folder.id);
    if (state.editingFolderId === folder.id) {
      return '<article class="workflow-folder-card is-editing" data-folder-id="' + folderId + '" data-folder-drop-id="' + folderId + '">' +
        '<div class="workflow-folder-edit"><span class="workflow-folder-icon" aria-hidden="true">▰</span><input class="workflow-folder-name-input" type="text" maxlength="80" autocomplete="off" spellcheck="false" data-folder-edit-id="' + folderId + '" value="' + esc(folder.name) + '" aria-label="文件夹名称"' + (state.folderSavingId === folder.id ? ' disabled' : '') + ' /></div></article>';
    }
    return '<article class="workflow-folder-card" data-folder-id="' + folderId + '" data-folder-drop-id="' + folderId + '">' +
      '<button class="workflow-folder-open" type="button" data-action="open-folder" data-folder-id="' + folderId + '" aria-label="打开文件夹：' + esc(folder.name) + '">' +
      '<span class="workflow-folder-icon" aria-hidden="true">▰</span><span class="workflow-folder-card-copy"><strong>' + esc(folder.name) + '</strong><small>' + records.length + ' 个工作流</small></span></button></article>';
  }
  function workflowCollection(title, subtitle, records, dropId, emptyMessage) {
    var dropAttribute = dropId == null ? "" : ' data-folder-drop-id="' + esc(dropId) + '"';
    var collectionClass = dropId === "" ? " workflow-unclassified-collection" : "";
    var cards = records.length ? '<div class="workflow-group-cards">' + records.map(workflowCard).join("") + '</div>' : '<div class="workflow-empty"><strong>' + esc(emptyMessage) + '</strong><span>将工作流拖到这里即可归类。</span></div>';
    return '<section class="workflow-group workflow-folder-collection' + collectionClass + '"' + dropAttribute + '><div class="workflow-group-heading"><div><strong>' + esc(title) + '</strong><span>' + esc(subtitle) + '</span></div><code>' + records.length + ' 个工作流</code></div>' + cards + '</section>';
  }
  function workflowUnclassifiedDropzone() {
    return '<div class="workflow-unclassified-drop" data-folder-drop-id=""><span aria-hidden="true">↓</span><span>拖到这里移出文件夹，归入未分类</span></div>';
  }
  function renderWorkflows() {
    var activeFolder = folderRecord(state.activeFolderId);
    var visible = activeFolder ? recordsInFolder(activeFolder.id) : state.workflows;
    $("workflowCount").textContent = String(visible.length);
    $("workflowTotal").textContent = String(state.workflows.length);
    $("workflowBound").textContent = String(state.workflows.filter(function (item) { return Boolean(item.account_id); }).length);
    $("accountTotal").textContent = String(state.accounts.length);
    var folderHeader = activeFolder ? '<div class="workflow-folder-breadcrumb"><button class="workflow-folder-back" type="button" data-action="back-to-folders">文件夹</button><span aria-hidden="true">/</span><strong>' + esc(activeFolder.name) + '</strong></div>' : '<div class="workflow-folder-browser-title"><strong>文件夹</strong><span>将工作流按用途整理到不同文件夹</span></div>';
    var folderHeaderActions = activeFolder ? "" : '<div class="workflow-folder-header-actions"><span class="workflow-folder-count">' + state.folders.length + ' 个文件夹</span><button id="createWorkflowFolder" class="secondary-button button-compact workflow-folder-create-button" type="button"><span aria-hidden="true">＋</span> 新建文件夹</button></div>';
    var folderCards = state.folders.length ? state.folders.map(workflowFolderCard).join("") : '<div class="workflow-folder-empty">还没有文件夹。点击“新建文件夹”开始整理。</div>';
    if (activeFolder) {
      $("workflowGroups").innerHTML = '<div class="workflow-folder-browser"><div class="workflow-folder-browser-head">' + folderHeader + '</div>' + workflowCollection(activeFolder.name, "当前文件夹", visible, activeFolder.id, "这个文件夹还是空的") + workflowUnclassifiedDropzone() + '</div>';
      return;
    }
    $("workflowGroups").innerHTML = '<div class="workflow-folder-browser"><div class="workflow-folder-browser-head">' + folderHeader + folderHeaderActions + '</div><div class="workflow-folder-grid">' + folderCards + '</div>' + workflowCollection("未分类", "尚未放入文件夹的工作流", recordsInFolder(""), "", state.workflows.length ? "所有工作流都已归类" : "工作流库还是空的") + '</div>';
    if (state.editingFolderId) focusFolderNameInput();
  }
  function promptGroupCard(group) {
    var items = Array.isArray(group.items) ? group.items : [];
    var kinds = {};
    items.forEach(function (item) {
      var kind = item && item.kind === "text" ? "自由文本" : (item && item.kind === "media" ? "媒体" : "积木");
      kinds[kind] = (kinds[kind] || 0) + 1;
    });
    var summary = Object.keys(kinds).map(function (kind) {
      return "<span>" + esc(kind) + " <strong>" + esc(kinds[kind]) + "</strong></span>";
    }).join("");
    return '<article class="workflow-card prompt-group-card" draggable="true" data-prompt-group-drag-id="' + esc(group.id) + '">' +
      '<div class="workflow-card-top"><button class="workflow-card-title workflow-card-title-button" type="button" data-action="load-prompt-group" data-prompt-group-id="' + esc(group.id) + '" aria-label="加载提示词组：' + esc(group.name) + '" title="加载到提示词工作台"><strong title="' + esc(group.name) + '">' + esc(group.name) + '</strong><small title="' + esc(group.id) + '">' + esc(group.id) + '</small></button><span class="workflow-status">可加载</span></div>' +
      '<button class="workflow-card-body" type="button" data-action="load-prompt-group" data-prompt-group-id="' + esc(group.id) + '" aria-label="加载提示词组：' + esc(group.name) + '" title="加载到提示词工作台">' +
      '<div class="workflow-card-meta"><div class="workflow-meta-row"><span>组装内容</span><span>' + items.length + ' 个积木</span></div><div class="workflow-meta-row"><span>保存时间</span><span>' + esc(formatTime(group.updated_at)) + '</span></div><div class="workflow-meta-row"><span>保存位置</span><span>本机组状态库</span></div></div>' +
      (summary ? '<div class="workflow-node-summary">' + summary + '</div>' : '') +
      '</button></article>';
  }
  function promptGroupFolderCard(folder) {
    var groups = promptGroupsInFolder(folder.id);
    var folderId = esc(folder.id);
    if (state.editingPromptGroupFolderId === folder.id) {
      return '<article class="workflow-folder-card is-editing prompt-group-folder-card" data-prompt-group-folder-id="' + folderId + '" data-prompt-group-folder-drop-id="' + folderId + '">' +
        '<div class="workflow-folder-edit"><span class="workflow-folder-icon" aria-hidden="true">▰</span><input class="workflow-folder-name-input" type="text" maxlength="80" autocomplete="off" spellcheck="false" data-prompt-group-folder-edit-id="' + folderId + '" value="' + esc(folder.name) + '" aria-label="提示词组文件夹名称"' + (state.promptGroupFolderSavingId === folder.id ? ' disabled' : '') + ' /></div></article>';
    }
    return '<article class="workflow-folder-card prompt-group-folder-card" data-prompt-group-folder-id="' + folderId + '" data-prompt-group-folder-drop-id="' + folderId + '">' +
      '<button class="workflow-folder-open" type="button" data-action="open-prompt-group-folder" data-prompt-group-folder-id="' + folderId + '" aria-label="打开提示词组文件夹：' + esc(folder.name) + '">' +
      '<span class="workflow-folder-icon" aria-hidden="true">▰</span><span class="workflow-folder-card-copy"><strong>' + esc(folder.name) + '</strong><small>' + groups.length + ' 个提示词组</small></span></button></article>';
  }
  function promptGroupCollection(title, subtitle, groups, dropId, emptyMessage) {
    var dropAttribute = dropId == null ? "" : ' data-prompt-group-folder-drop-id="' + esc(dropId) + '"';
    var collectionClass = dropId === "" ? " workflow-unclassified-collection" : "";
    var cards = groups.length ? '<div class="workflow-group-cards">' + groups.map(promptGroupCard).join("") + '</div>' : '<div class="workflow-empty"><strong>' + esc(emptyMessage) + '</strong><span>将提示词组拖到这里即可归类。</span></div>';
    return '<section class="workflow-group workflow-folder-collection' + collectionClass + '"' + dropAttribute + '><div class="workflow-group-heading"><div><strong>' + esc(title) + '</strong><span>' + esc(subtitle) + '</span></div><code>' + groups.length + ' 个提示词组</code></div>' + cards + '</section>';
  }
  function promptGroupUnclassifiedDropzone() {
    return '<div class="workflow-unclassified-drop" data-prompt-group-folder-drop-id=""><span aria-hidden="true">↓</span><span>拖到这里移出文件夹，归入未分类</span></div>';
  }
  function focusPromptGroupFolderNameInput() {
    var input = $("promptGroupGroups").querySelector("[data-prompt-group-folder-edit-id]");
    if (!input) return;
    window.requestAnimationFrame(function () {
      if (!input.isConnected) return;
      input.focus();
      input.select();
    });
  }
  function nextPromptGroupFolderName() {
    var existing = state.promptGroupFolders.map(function (folder) { return String(folder.name || "").trim().toLowerCase(); });
    var candidate = "新文件夹";
    var suffix = 1;
    while (existing.indexOf(candidate.toLowerCase()) !== -1) {
      suffix += 1;
      candidate = "新文件夹 " + suffix;
    }
    return candidate;
  }
  function renderPromptGroups() {
    var container = $("promptGroupGroups");
    if (!container) return;
    var activeFolder = promptGroupFolderRecord(state.activePromptGroupFolderId);
    var visible = activeFolder ? promptGroupsInFolder(activeFolder.id) : state.promptGroups;
    var count = $("promptGroupCount");
    if (count) count.textContent = String(state.promptGroups.length);
    var folderHeader = activeFolder
      ? '<div class="workflow-folder-breadcrumb"><button class="workflow-folder-back" type="button" data-action="back-to-prompt-group-folders">文件夹</button><span aria-hidden="true">/</span><strong>' + esc(activeFolder.name) + '</strong></div>'
      : '<div class="workflow-folder-browser-title"><strong>文件夹</strong><span>将提示词组按项目整理</span></div>';
    var folderHeaderActions = activeFolder ? "" : '<div class="workflow-folder-header-actions"><span class="workflow-folder-count">' + state.promptGroupFolders.length + ' 个文件夹</span><button id="createPromptGroupFolder" class="secondary-button button-compact workflow-folder-create-button" type="button"><span aria-hidden="true">＋</span> 新建文件夹</button></div>';
    if (activeFolder) {
      container.innerHTML = '<div class="workflow-folder-browser"><div class="workflow-folder-browser-head">' + folderHeader + '</div>' + promptGroupCollection(activeFolder.name, "当前文件夹", visible, activeFolder.id, "这个文件夹还是空的") + promptGroupUnclassifiedDropzone() + '</div>';
      return;
    }
    var folderCards = state.promptGroupFolders.length ? state.promptGroupFolders.map(promptGroupFolderCard).join("") : '<div class="workflow-folder-empty">还没有文件夹。点击“新建文件夹”开始整理。</div>';
    container.innerHTML = '<div class="workflow-folder-browser"><div class="workflow-folder-browser-head">' + folderHeader + folderHeaderActions + '</div><div class="workflow-folder-grid">' + folderCards + '</div>' + promptGroupCollection("未分类", "尚未放入文件夹的提示词组", promptGroupsInFolder(""), "", state.promptGroups.length ? "所有提示词组都已归类" : "还没有提示词组") + '</div>';
    if (state.editingPromptGroupFolderId) focusPromptGroupFolderNameInput();
  }
  function refreshWorkflows() {
    return Promise.all([request("/api/workflows"), request("/api/workflow-folders"), request("/api/state"), request("/api/prompt/groups")]).then(function (results) {
      state.workflows = results[0].workflows || [];
      state.folders = results[1].folders || [];
      state.accounts = results[2].accounts || [];
      state.telegram = results[2].settings && results[2].settings.telegram || {};
      state.promptGroups = results[3].groups || [];
      state.promptGroupFolders = results[3].folders || [];
      if (state.activeFolderId && !folderRecord(state.activeFolderId)) state.activeFolderId = "";
      if (state.activePromptGroupFolderId && !promptGroupFolderRecord(state.activePromptGroupFolderId)) state.activePromptGroupFolderId = "";
      renderWorkflows();
      renderPromptGroups();
    }).catch(function (error) { showToast(error.message, true); });
  }
  function refreshWorkflowLibrary() {
    var button = $("refreshWorkflows");
    if (button) button.disabled = true;
    return refreshWorkflows().finally(function () {
      if (button) button.disabled = false;
    });
  }
  window.addEventListener("rh-workflow-library-refresh", refreshWorkflowLibrary);
  function openEditor(record, imported) {
    var rawContent = imported && imported.content != null ? String(imported.content) : "";
    var content = rawContent;
    try { content = JSON.stringify(JSON.parse(rawContent), null, 2); } catch (error) {}
    state.editor = {
      mode: record ? "edit" : "import",
      id: record ? record.id : "",
      content: content,
      savedContent: content,
      sourceDir: imported ? imported.sourceDir : ""
    };
    $("workflowEditorTitle").textContent = record ? "编辑工作流资料" : "导入工作流";
    $("workflowEditorHint").textContent = record ? "修改工作流资料，也可以更新它关联的提示词组；不会改变任务历史。" : "工作流 JSON 已读取，保存时可以一起关联提示词组。";
    $("workflowRecordName").value = record ? record.name : imported.filename;
    $("workflowRecordRemoteId").value = record ? (record.remote_workflow_id || "") : (imported.remoteWorkflowId || "");
    $("workflowEditorJson").value = content;
    renderAccountOptions(record ? record.account_id : "");
    renderPromptGroupOptions(record ? record.prompt_group_id : "");
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
  function readEditorJson() {
    var raw = $("workflowEditorJson").value;
    var workflow;
    try {
      workflow = JSON.parse(raw);
    } catch (error) {
      showToast("JSON 格式无效：" + error.message, true);
      return null;
    }
    if (!workflow || typeof workflow !== "object" || Array.isArray(workflow)) {
      showToast("工作流 JSON 顶层必须是 API 节点对象", true);
      return null;
    }
    return { workflow: workflow, content: JSON.stringify(workflow, null, 2) };
  }
  function saveWorkflowJson() {
    var editor = state.editor;
    if (!editor) return;
    var parsed = readEditorJson();
    if (!parsed) return;
    var button = $("saveWorkflowJson");
    button.disabled = true;
    if (editor.mode === "import") {
      editor.content = parsed.content;
      editor.savedContent = parsed.content;
      $("workflowEditorJson").value = parsed.content;
      showToast("JSON 已更新，点击“保存工作流”写入本机工作流库");
      button.disabled = false;
      return;
    }
    jsonRequest("/api/workflows/" + encodeURIComponent(editor.id), "PATCH", { content: parsed.content }).then(function () {
      return fetchWorkflow(editor.id);
    }).then(function (data) {
      var content = JSON.stringify(data.workflow, null, 2);
      editor.content = content;
      editor.savedContent = content;
      $("workflowEditorJson").value = content;
      showToast("工作流 JSON 已保存");
      return refreshWorkflows();
    }).catch(function (error) {
      showToast("保存工作流 JSON 失败：" + error.message, true);
    }).finally(function () { button.disabled = false; });
  }
  function restoreWorkflowJson() {
    if (!state.editor) return;
    $("workflowEditorJson").value = state.editor.savedContent || state.editor.content || "";
    showToast("已复原到最近一次保存的 JSON");
  }
  function saveWorkflowRecord(event) {
    event.preventDefault();
    if (!state.editor) return;
    var button = $("saveWorkflowRecord");
    var name = $("workflowRecordName").value.trim();
    if (!name) return showToast("请填写工作流名称", true);
    var parsed = readEditorJson();
    if (!parsed) return;
    button.disabled = true;
    var payload = {
      name: name,
      account_id: $("workflowRecordAccount").value,
      remote_workflow_id: $("workflowRecordRemoteId").value.trim(),
      prompt_group_id: $("workflowRecordPromptGroup").value,
      content: parsed.content
    };
    var promise;
    if (state.editor.mode === "edit") {
      promise = jsonRequest("/api/workflows/" + encodeURIComponent(state.editor.id), "PATCH", payload);
    } else {
      payload.filename = name;
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

  function promptGroupSnapshot(group) {
    if (group && Array.isArray(group.items)) return group;
    return { id: "", name: "未关联提示词组", updated_at: Date.now(), items: [] };
  }

  function queuePromptGroupSnapshot(group) {
    var snapshot = promptGroupSnapshot(group);
    window.localStorage.setItem(pendingPromptGroupStorageKey, JSON.stringify({ version: 1, group: snapshot }));
    return Boolean(group && Array.isArray(group.items));
  }

  function syncPromptWorkbenchState(group) {
    return jsonRequest("/api/prompt/state", "PUT", { items: group.items }).catch(function (error) {
      showToast("提示词工作台状态同步失败：" + error.message, true);
    });
  }

  function notifySubmitImport(detail) {
    if (window.RHFocus && typeof window.RHFocus.importToSubmit === "function") {
      window.RHFocus.importToSubmit(detail || {});
      return true;
    }
    return false;
  }

  function workflowDraftFromDetail(data) {
    var record = data && data.record && typeof data.record === "object" ? data.record : {};
    var workflow = data && data.workflow && typeof data.workflow === "object" ? data.workflow : null;
    var analysis = data && data.analysis && typeof data.analysis === "object" ? data.analysis : null;
    if (!record.id || !workflow || !analysis) throw new Error("工作流详情不完整，无法加载");
    var bypassedNodes = Array.isArray(analysis.bypassed_nodes) ? analysis.bypassed_nodes.slice() : [];
    return {
      version: 1,
      // Leave the credential untouched on the task page; only replace the workflow.
      credential: { selectedKeyId: "" },
      workflow: {
        id: String(record.id),
        remoteWorkflowId: String(record.remote_workflow_id || analysis.remote_workflow_id || "").trim(),
        name: String(record.name || "workflow_api.json").trim() || "workflow_api.json",
        sourceDir: String(record.source_dir || ""),
        accountId: String(record.account_id || "").trim(),
        data: workflow,
        analysis: analysis,
        inputConfig: record.input_config && typeof record.input_config === "object" ? record.input_config : null,
        values: {
          files: {},
          prompts: {},
          customInputs: {},
          randomNoise: {},
          resolution: {},
          bypassedNodes: bypassedNodes
        },
        savedAt: Date.now()
      }
    };
  }

  function loadWorkflowIntoSubmit(id) {
    var recordId = String(id || "").trim();
    if (!recordId || state.loadingWorkflowId === recordId) return;
    state.selectedWorkflowId = recordId;
    state.loadingWorkflowId = recordId;
    renderWorkflows();
    fetchWorkflow(recordId).then(function (data) {
      var draft = workflowDraftFromDetail(data);
      window.localStorage.setItem(draftStorageKey, JSON.stringify(draft));
      var promptGroup = promptGroupSnapshot(data.prompt_group);
      var hasPromptGroup = queuePromptGroupSnapshot(data.prompt_group);
      var focusImport = notifySubmitImport({ kind: "workflow", draft: draft, promptGroup: promptGroup, hasPromptGroup: hasPromptGroup });
      return syncPromptWorkbenchState(promptGroup).then(function () {
        var promptLabel = hasPromptGroup ? "工作流和提示词组已加载" : "工作流已加载，提示词工作台已清空";
        showToast(focusImport
          ? promptLabel + "，任务提交面板已同步"
          : promptLabel + "，正在打开任务提交页");
        if (!focusImport) window.location.href = "/";
      });
    }).catch(function (error) {
      showToast("加载工作流失败：" + error.message, true);
    }).finally(function () {
      if (state.loadingWorkflowId === recordId) {
        state.loadingWorkflowId = "";
        renderWorkflows();
      }
    });
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
      virtual: Boolean(item.virtual), default: item.default == null ? "" : item.default,
      default_value: Object.prototype.hasOwnProperty.call(item, "default_value") ? item.default_value : (item.default == null ? "" : item.default)
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
        items: saved ? saved.items.map(function (item) {
          var catalogItem = catalog.find(function (entry) { return entry.id === item.id; });
          var merged = Object.assign({}, catalogItem || {}, item);
          if (catalogItem && Object.prototype.hasOwnProperty.call(catalogItem, "default_value")) merged.default_value = catalogItem.default_value;
          if (catalogItem && Object.prototype.hasOwnProperty.call(catalogItem, "default")) merged.default = catalogItem.default;
          return configItemFromCatalog(merged);
        }) : defaultConfigItems(catalog)
      };
      $("workflowConfigTitle").textContent = "配置输入节点 · " + (record.name || "工作流");
      $("workflowConfigHint").textContent = "自动识别模式只读取工作流原始输入；切换到手动选择后，已添加的节点可编辑默认值。";
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
      var value = item.default_value == null ? "" : item.default_value;
      var defaultControl;
      if (item.kind === "boolean") {
        defaultControl = '<span class="workflow-config-default-check"><input data-config-action="default" data-config-index="' + index + '" type="checkbox"' + ((value === true || value === "true" || value === 1) ? " checked" : "") + ' /><span>启用</span></span>';
      } else if (item.kind === "prompt" || (typeof value === "string" && value.length > 140)) {
        defaultControl = '<textarea data-config-action="default" data-config-index="' + index + '" rows="2">' + esc(value) + '</textarea>';
      } else {
        defaultControl = '<input data-config-action="default" data-config-index="' + index + '" type="' + (item.kind === "number" ? "number" : "text") + '"' + (item.kind === "number" ? ' step="any"' : "") + ' value="' + esc(value) + '" />';
      }
      return '<div class="workflow-config-item"><div class="workflow-config-item-head"><div><div class="workflow-config-item-title" title="' + esc(item.title) + '">' + esc(item.label || item.title) + '</div><code class="workflow-config-item-id" title="' + esc(item.id) + '">' + esc(item.id) + '</code></div><button class="workflow-config-remove" type="button" data-config-action="remove" data-config-index="' + index + '">移除</button></div>' +
        '<div class="workflow-config-item-grid"><div class="workflow-config-name-field field-group"><span class="field-label">显示名称</span><div class="workflow-config-name-control"><label class="workflow-config-required"><span class="workflow-config-required-label">必填</span><input data-config-action="required" data-config-index="' + index + '" type="checkbox"' + (item.required ? " checked" : "") + ' /><span class="workflow-config-required-track" aria-hidden="true"></span></label><input data-config-action="label" data-config-index="' + index + '" type="text" value="' + esc(item.label) + '" maxlength="160" /></div></div><label class="field-group"><span class="field-label">输入类型</span><select data-config-action="kind" data-config-index="' + index + '">' + kindOptions + '</select></label><label class="field-group"><span class="field-label">默认值</span>' + defaultControl + '</label></div>' + options + '</div>';
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
      input_config: { mode: editor.mode, items: editor.mode === "manual" ? items : [] },
      input_defaults: editor.mode === "manual" ? items.filter(function (item) { return !item.virtual && item.field; }).map(function (item) {
        return { node_id: item.node_id, field: item.field, default: item.default_value };
      }) : []
    }).then(function () {
      window.RHMotion.closeModal("workflowConfigModal");
      state.configEditor = null;
      showToast(editor.mode === "manual" ? "工作流输入配置已保存" : "已恢复自动识别输入");
      return refreshWorkflows();
    }).catch(function (error) { showToast("保存输入配置失败：" + error.message, true); }).finally(function () { button.disabled = false; });
  }
  function updateConfigDefault(target, index, editor) {
    if (!editor.items[index]) return;
    var item = editor.items[index];
    var next = target.type === "checkbox" ? target.checked : target.value;
    if (item.kind === "number" && String(next).trim() !== "" && Number.isFinite(Number(next))) next = Number(next);
    item.default_value = next;
    item.default = next;
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
  function focusFolderNameInput() {
    var input = $("workflowGroups").querySelector("[data-folder-edit-id]");
    if (!input) return;
    window.requestAnimationFrame(function () {
      if (!input.isConnected) return;
      input.focus();
      input.select();
    });
  }
  function nextWorkflowFolderName() {
    var existing = state.folders.map(function (folder) { return String(folder.name || "").trim().toLowerCase(); });
    var candidate = "新文件夹";
    var suffix = 1;
    while (existing.indexOf(candidate.toLowerCase()) !== -1) {
      suffix += 1;
      candidate = "新文件夹 " + suffix;
    }
    return candidate;
  }
  function createWorkflowFolder() {
    var button = $("createWorkflowFolder");
    if (button) button.disabled = true;
    var defaultName = nextWorkflowFolderName();
    jsonRequest("/api/workflow-folders", "POST", { name: defaultName }).then(function (data) {
      state.editingFolderId = data.folder && data.folder.id || "";
      return refreshWorkflows();
    }).catch(function (error) {
      showToast("创建文件夹失败：" + error.message, true);
    }).finally(function () { if (button) button.disabled = false; });
  }
  function beginWorkflowFolderRename(folderId) {
    if (!folderRecord(folderId)) return;
    closeFolderContextMenu();
    state.editingFolderId = folderId;
    renderWorkflows();
  }
  function cancelWorkflowFolderRename() {
    state.editingFolderId = "";
    state.folderSavingId = "";
    renderWorkflows();
  }
  function saveWorkflowFolderName(folderId, name) {
    var folder = folderRecord(folderId);
    if (!folder || state.folderSavingId === folderId) return;
    var cleanName = String(name || "").trim();
    if (!cleanName || cleanName === folder.name) {
      cancelWorkflowFolderRename();
      return;
    }
    state.folderSavingId = folderId;
    var input = Array.prototype.find.call($("workflowGroups").querySelectorAll("[data-folder-edit-id]"), function (element) {
      return element.dataset.folderEditId === folderId;
    });
    if (input) input.disabled = true;
    jsonRequest("/api/workflow-folders/" + encodeURIComponent(folderId), "PATCH", { name: cleanName }).then(function () {
      state.editingFolderId = "";
      showToast("文件夹已重命名");
      return refreshWorkflows();
    }).catch(function (error) {
      showToast("重命名文件夹失败：" + error.message, true);
      if (input) {
        input.disabled = false;
        input.focus();
        input.select();
      }
    }).finally(function () { state.folderSavingId = ""; });
  }
  function deleteWorkflowFolder(folderId) {
    var folder = folderRecord(folderId);
    if (!folder || !window.confirm("确定删除文件夹“" + folder.name + "”吗？其中的工作流会归入未分类。")) return;
    closeFolderContextMenu();
    request("/api/workflow-folders/" + encodeURIComponent(folderId), { method: "DELETE" }).then(function () {
      if (state.activeFolderId === folderId) state.activeFolderId = "";
      if (state.editingFolderId === folderId) state.editingFolderId = "";
      showToast("文件夹已删除，工作流已归入未分类");
      return refreshWorkflows();
    }).catch(function (error) { showToast("删除文件夹失败：" + error.message, true); });
  }
  function closeContextMenus() {
    var folderMenu = $("workflowFolderContextMenu");
    var workflowMenu = $("workflowCardContextMenu");
    var promptGroupFolderMenu = $("promptGroupFolderContextMenu");
    var promptGroupMenu = $("promptGroupCardContextMenu");
    if (folderMenu) folderMenu.hidden = true;
    if (workflowMenu) workflowMenu.hidden = true;
    if (promptGroupFolderMenu) promptGroupFolderMenu.hidden = true;
    if (promptGroupMenu) promptGroupMenu.hidden = true;
    state.contextFolderId = "";
    state.contextWorkflowId = "";
    state.contextPromptGroupFolderId = "";
    state.contextPromptGroupId = "";
  }
  function closeFolderContextMenu() {
    closeContextMenus();
  }
  function positionContextMenu(menu, event) {
    var left = Math.min(event.clientX, Math.max(8, window.innerWidth - menu.offsetWidth - 8));
    var top = Math.min(event.clientY, Math.max(8, window.innerHeight - menu.offsetHeight - 8));
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }
  function openFolderContextMenu(event, folderId) {
    var menu = $("workflowFolderContextMenu");
    if (!menu || !folderRecord(folderId)) return;
    event.preventDefault();
    closeContextMenus();
    state.contextFolderId = folderId;
    var inbound = state.telegram && state.telegram.inbound_enabled && state.telegram.inbound_mode === "folder_random" && state.telegram.inbound_folder_id === folderId;
    var inboundAction = menu.querySelector('[data-folder-menu-action="set-telegram-inbound"]');
    if (inboundAction) {
      inboundAction.textContent = inbound ? "取消入站" : "设为入站";
      inboundAction.dataset.enabled = inbound ? "false" : "true";
    }
    menu.hidden = false;
    positionContextMenu(menu, event);
    var firstAction = menu.querySelector('[data-folder-menu-action="rename"]');
    if (firstAction) firstAction.focus();
  }
  function openWorkflowContextMenu(event, workflowId) {
    var menu = $("workflowCardContextMenu");
    var record = state.workflows.find(function (item) { return item.id === workflowId; });
    if (!menu || !record) return;
    event.preventDefault();
    closeContextMenus();
    state.contextWorkflowId = workflowId;
    var inbound = state.telegram && state.telegram.inbound_enabled && state.telegram.inbound_mode !== "folder_random" && state.telegram.inbound_workflow_id === workflowId;
    var inboundAction = menu.querySelector('[data-workflow-menu-action="set-telegram-inbound"]');
    if (inboundAction) {
      inboundAction.textContent = inbound ? "取消入站" : "设为入站";
      inboundAction.dataset.enabled = inbound ? "false" : "true";
    }
    menu.hidden = false;
    positionContextMenu(menu, event);
    var firstAction = menu.querySelector('[data-workflow-menu-action="configure-workflow"]');
    if (firstAction) firstAction.focus();
  }
  function openPromptGroupFolderContextMenu(event, folderId) {
    var menu = $("promptGroupFolderContextMenu");
    if (!menu || !promptGroupFolderRecord(folderId)) return;
    event.preventDefault();
    closeContextMenus();
    state.contextPromptGroupFolderId = folderId;
    menu.hidden = false;
    positionContextMenu(menu, event);
    var firstAction = menu.querySelector("[data-prompt-group-folder-menu-action]");
    if (firstAction) firstAction.focus();
  }
  function openPromptGroupContextMenu(event, groupId) {
    var menu = $("promptGroupCardContextMenu");
    var group = state.promptGroups.find(function (item) { return item.id === groupId; });
    if (!menu || !group) return;
    event.preventDefault();
    closeContextMenus();
    state.contextPromptGroupId = groupId;
    menu.hidden = false;
    positionContextMenu(menu, event);
    var firstAction = menu.querySelector("[data-prompt-group-menu-action]");
    if (firstAction) firstAction.focus();
  }
  function handleWorkflowContextMenu(event) {
    var promptGroupFolderCard = event.target.closest(".prompt-group-folder-card");
    if (promptGroupFolderCard && $("promptGroupGroups") && $("promptGroupGroups").contains(promptGroupFolderCard) && !promptGroupFolderCard.classList.contains("is-editing")) {
      openPromptGroupFolderContextMenu(event, promptGroupFolderCard.dataset.promptGroupFolderId || "");
      return;
    }
    var promptGroupCard = event.target.closest(".prompt-group-card");
    if (promptGroupCard && $("promptGroupGroups") && $("promptGroupGroups").contains(promptGroupCard)) {
      openPromptGroupContextMenu(event, promptGroupCard.dataset.promptGroupDragId || "");
      return;
    }
    var card = event.target.closest(".workflow-folder-card");
    if (card && $("workflowGroups").contains(card) && !card.classList.contains("is-editing")) {
      openFolderContextMenu(event, card.dataset.folderId || "");
      return;
    }
    var workflowCard = event.target.closest(".workflow-card");
    if (workflowCard && $("workflowGroups").contains(workflowCard)) {
      openWorkflowContextMenu(event, workflowCard.dataset.workflowDragId || "");
    }
  }
  function handleFolderNameKeydown(event) {
    var input = event.target.closest("[data-folder-edit-id]");
    if (!input) return;
    if (event.key === "Escape") {
      event.preventDefault();
      cancelWorkflowFolderRename();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      saveWorkflowFolderName(input.dataset.folderEditId, input.value);
    }
  }
  function handleFolderNameBlur(event) {
    var input = event.target.closest("[data-folder-edit-id]");
    if (input) saveWorkflowFolderName(input.dataset.folderEditId, input.value);
  }
  function handleFolderMenuAction(event) {
    var button = event.target.closest("[data-folder-menu-action]");
    if (!button) return;
    var folderId = state.contextFolderId;
    var action = button.dataset.folderMenuAction;
    closeFolderContextMenu();
    if (action === "rename") beginWorkflowFolderRename(folderId);
    if (action === "set-telegram-inbound") setTelegramInboundFolder(folderId, button.dataset.enabled === "true", button);
    if (action === "delete") deleteWorkflowFolder(folderId);
  }
  function setTelegramInboundFolder(folderId, enabled, trigger) {
    if (!folderId) return;
    if (trigger) trigger.disabled = true;
    jsonRequest("/api/settings", "PATCH", {
      telegram_inbound_workflow_id: "",
      telegram_inbound_mode: "folder_random",
      telegram_inbound_folder_id: folderId,
      telegram_inbound_enabled: Boolean(enabled)
    }).then(function (data) {
      state.telegram = data.telegram || {};
      showToast(enabled ? "已设置为 Telegram 图片入站文件夹" : "已关闭 Telegram 图片入站");
      renderWorkflows();
    }).catch(function (error) {
      showToast("设置 Telegram 入站文件夹失败：" + error.message, true);
    }).finally(function () { if (trigger) trigger.disabled = false; });
  }
  function setTelegramInboundWorkflow(workflowId, enabled, trigger) {
    if (!workflowId) return;
    if (trigger) trigger.disabled = true;
    jsonRequest("/api/settings", "PATCH", {
      telegram_inbound_workflow_id: workflowId,
      telegram_inbound_mode: "fixed",
      telegram_inbound_enabled: Boolean(enabled)
    }).then(function (data) {
      state.telegram = data.telegram || {};
      showToast(state.telegram.inbound_enabled ? "已设置为 Telegram 图片入站工作流" : "已关闭 Telegram 图片入站");
      renderWorkflows();
    }).catch(function (error) {
      showToast("设置 Telegram 入站失败：" + error.message, true);
    }).finally(function () { if (trigger) trigger.disabled = false; });
  }
  function deleteWorkflowLibraryRecord(workflowId, trigger) {
    if (!workflowId || !window.confirm("确定删除这个工作流库副本吗？任务历史、任务快照和产物不会删除。")) return;
    if (trigger) trigger.disabled = true;
    request("/api/workflows/" + encodeURIComponent(workflowId), { method: "DELETE" }).then(function () {
      showToast("工作流库副本已删除");
      return refreshWorkflows();
    }).catch(function (error) { showToast("删除工作流失败：" + error.message, true); }).finally(function () { if (trigger) trigger.disabled = false; });
  }
  function createPromptGroupFolder() {
    var button = $("createPromptGroupFolder");
    if (button) button.disabled = true;
    jsonRequest("/api/prompt/group-folders", "POST", { name: nextPromptGroupFolderName() }).then(function (data) {
      state.editingPromptGroupFolderId = data.folder && data.folder.id || "";
      return refreshWorkflows();
    }).catch(function (error) {
      showToast("创建提示词组文件夹失败：" + error.message, true);
    }).finally(function () { if (button) button.disabled = false; });
  }
  function beginPromptGroupFolderRename(folderId) {
    if (!promptGroupFolderRecord(folderId)) return;
    closeContextMenus();
    state.editingPromptGroupFolderId = folderId;
    renderPromptGroups();
  }
  function cancelPromptGroupFolderRename() {
    state.editingPromptGroupFolderId = "";
    state.promptGroupFolderSavingId = "";
    renderPromptGroups();
  }
  function savePromptGroupFolderName(folderId, name) {
    var folder = promptGroupFolderRecord(folderId);
    if (!folder || state.promptGroupFolderSavingId === folderId) return;
    var cleanName = String(name || "").trim();
    if (!cleanName || cleanName === folder.name) {
      cancelPromptGroupFolderRename();
      return;
    }
    state.promptGroupFolderSavingId = folderId;
    var input = Array.prototype.find.call($("promptGroupGroups").querySelectorAll("[data-prompt-group-folder-edit-id]"), function (element) {
      return element.dataset.promptGroupFolderEditId === folderId;
    });
    if (input) input.disabled = true;
    jsonRequest("/api/prompt/group-folders/" + encodeURIComponent(folderId), "PATCH", { name: cleanName }).then(function () {
      state.editingPromptGroupFolderId = "";
      showToast("提示词组文件夹已重命名");
      return refreshWorkflows();
    }).catch(function (error) {
      showToast("重命名提示词组文件夹失败：" + error.message, true);
      if (input) {
        input.disabled = false;
        input.focus();
        input.select();
      }
    }).finally(function () { state.promptGroupFolderSavingId = ""; });
  }
  function deletePromptGroupFolder(folderId) {
    var folder = promptGroupFolderRecord(folderId);
    if (!folder || !window.confirm("确定删除文件夹“" + folder.name + "”吗？其中的提示词组会归入未分类。")) return;
    closeContextMenus();
    request("/api/prompt/group-folders/" + encodeURIComponent(folderId), { method: "DELETE" }).then(function () {
      if (state.activePromptGroupFolderId === folderId) state.activePromptGroupFolderId = "";
      if (state.editingPromptGroupFolderId === folderId) state.editingPromptGroupFolderId = "";
      showToast("提示词组文件夹已删除，组状态已归入未分类");
      return refreshWorkflows();
    }).catch(function (error) { showToast("删除提示词组文件夹失败：" + error.message, true); });
  }
  function loadPromptGroupIntoWorkbench(groupId) {
    var group = state.promptGroups.find(function (item) { return item.id === groupId; });
    if (!group) return;
    if (window.RHFocus && window.RHFocus.isFocusMode && typeof window.RHFocus.exitToTaskSubmit === "function") {
      window.RHFocus.exitToTaskSubmit();
      return;
    }
    window.location.href = "/prompt?group_id=" + encodeURIComponent(group.id);
  }
  function deletePromptGroup(groupId, trigger) {
    var group = state.promptGroups.find(function (item) { return item.id === groupId; });
    if (!group || !window.confirm("删除提示词组“" + group.name + "”吗？组装台不会改变。")) return;
    if (trigger) trigger.disabled = true;
    request("/api/prompt/groups/" + encodeURIComponent(groupId), { method: "DELETE" }).then(function () {
      showToast("提示词组已删除");
      return refreshWorkflows();
    }).catch(function (error) { showToast("删除提示词组失败：" + error.message, true); }).finally(function () { if (trigger) trigger.disabled = false; });
  }
  function handlePromptGroupFolderMenuAction(event) {
    var button = event.target.closest("[data-prompt-group-folder-menu-action]");
    if (!button) return;
    var folderId = state.contextPromptGroupFolderId;
    var action = button.dataset.promptGroupFolderMenuAction;
    closeContextMenus();
    if (action === "rename") beginPromptGroupFolderRename(folderId);
    if (action === "delete") deletePromptGroupFolder(folderId);
  }
  function handlePromptGroupMenuAction(event) {
    var button = event.target.closest("[data-prompt-group-menu-action]");
    if (!button) return;
    var groupId = state.contextPromptGroupId;
    var action = button.dataset.promptGroupMenuAction;
    closeContextMenus();
    if (action === "delete") deletePromptGroup(groupId, button);
  }
  function handlePromptGroupFolderNameKeydown(event) {
    var input = event.target.closest("[data-prompt-group-folder-edit-id]");
    if (!input) return;
    if (event.key === "Escape") {
      event.preventDefault();
      cancelPromptGroupFolderRename();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      savePromptGroupFolderName(input.dataset.promptGroupFolderEditId, input.value);
    }
  }
  function handlePromptGroupFolderNameBlur(event) {
    var input = event.target.closest("[data-prompt-group-folder-edit-id]");
    if (input) savePromptGroupFolderName(input.dataset.promptGroupFolderEditId, input.value);
  }
  function handleWorkflowMenuAction(event) {
    var button = event.target.closest("[data-workflow-menu-action]");
    if (!button) return;
    var workflowId = state.contextWorkflowId;
    var action = button.dataset.workflowMenuAction;
    closeContextMenus();
    if (action === "configure-workflow") {
      openConfig(workflowId);
      return;
    }
    if (action === "set-telegram-inbound") {
      setTelegramInboundWorkflow(workflowId, button.dataset.enabled === "true", button);
      return;
    }
    if (action === "export-workflow") {
      exportWorkflow(workflowId);
      return;
    }
    if (action === "delete-workflow") deleteWorkflowLibraryRecord(workflowId, button);
  }
  function moveWorkflowToFolder(workflowId, folderId) {
    if (!workflowId) return;
    jsonRequest("/api/workflows/" + encodeURIComponent(workflowId), "PATCH", { folder_id: folderId || "" }).then(function () {
      showToast(folderId ? "工作流已移入文件夹" : "工作流已移出文件夹，归入未分类");
      return refreshWorkflows();
    }).catch(function (error) {
      showToast("移动工作流失败：" + error.message, true);
    });
  }
  function dropTargetFromEvent(event) {
    var target = event.target.closest("[data-folder-drop-id]");
    return target && $("workflowGroups").contains(target) ? target : null;
  }
  function clearFolderDropState() {
    $("workflowGroups").querySelectorAll(".is-folder-drop-target").forEach(function (element) {
      element.classList.remove("is-folder-drop-target");
    });
  }
  function handleWorkflowDragStart(event) {
    var card = event.target.closest("[data-workflow-drag-id]");
    if (!card) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.workflowDragId || "");
    card.classList.add("is-dragging");
  }
  function handleWorkflowDragEnd() {
    $("workflowGroups").querySelectorAll(".is-dragging").forEach(function (element) {
      element.classList.remove("is-dragging");
    });
    clearFolderDropState();
  }
  function handleFolderDragOver(event) {
    if (event.dataTransfer.types && Array.prototype.indexOf.call(event.dataTransfer.types, "text/x-rh-prompt-group") !== -1) return;
    var target = dropTargetFromEvent(event);
    if (!target) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    clearFolderDropState();
    target.classList.add("is-folder-drop-target");
  }
  function handleFolderDragLeave(event) {
    var target = dropTargetFromEvent(event);
    if (!target || (event.relatedTarget && target.contains(event.relatedTarget))) return;
    target.classList.remove("is-folder-drop-target");
  }
  function handleFolderDrop(event) {
    if (event.dataTransfer.types && Array.prototype.indexOf.call(event.dataTransfer.types, "text/x-rh-prompt-group") !== -1) return;
    var target = dropTargetFromEvent(event);
    if (!target) return;
    event.preventDefault();
    var workflowId = event.dataTransfer.getData("text/plain");
    var folderId = target.dataset.folderDropId || "";
    clearFolderDropState();
    moveWorkflowToFolder(workflowId, folderId);
  }
  function movePromptGroupToFolder(groupId, folderId) {
    var group = state.promptGroups.find(function (item) { return item.id === groupId; });
    if (!group) return;
    jsonRequest("/api/prompt/groups", "POST", {
      id: group.id,
      name: group.name,
      items: group.items || [],
      folder_id: folderId || "",
    }).then(function () {
      showToast(folderId ? "提示词组已移入文件夹" : "提示词组已移出文件夹，归入未分类");
      return refreshWorkflows();
    }).catch(function (error) {
      showToast("移动提示词组失败：" + error.message, true);
    });
  }
  function promptGroupDropTargetFromEvent(event) {
    var target = event.target.closest("[data-prompt-group-folder-drop-id]");
    return target && $("promptGroupGroups").contains(target) ? target : null;
  }
  function clearPromptGroupDropState() {
    $("promptGroupGroups").querySelectorAll(".is-folder-drop-target").forEach(function (element) {
      element.classList.remove("is-folder-drop-target");
    });
  }
  function handlePromptGroupDragStart(event) {
    var card = event.target.closest("[data-prompt-group-drag-id]");
    if (!card) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/x-rh-prompt-group", card.dataset.promptGroupDragId || "");
    event.dataTransfer.setData("text/plain", card.dataset.promptGroupDragId || "");
    card.classList.add("is-dragging");
  }
  function handlePromptGroupDragEnd() {
    $("promptGroupGroups").querySelectorAll("[data-prompt-group-drag-id].is-dragging").forEach(function (element) {
      element.classList.remove("is-dragging");
    });
    clearPromptGroupDropState();
  }
  function handlePromptGroupDragOver(event) {
    if (!event.dataTransfer.types || Array.prototype.indexOf.call(event.dataTransfer.types, "text/x-rh-prompt-group") === -1) return;
    var target = promptGroupDropTargetFromEvent(event);
    if (!target) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    clearPromptGroupDropState();
    target.classList.add("is-folder-drop-target");
  }
  function handlePromptGroupDragLeave(event) {
    var target = promptGroupDropTargetFromEvent(event);
    if (!target || (event.relatedTarget && target.contains(event.relatedTarget))) return;
    target.classList.remove("is-folder-drop-target");
  }
  function handlePromptGroupDrop(event) {
    if (!event.dataTransfer.types || Array.prototype.indexOf.call(event.dataTransfer.types, "text/x-rh-prompt-group") === -1) return;
    var target = promptGroupDropTargetFromEvent(event);
    if (!target) return;
    event.preventDefault();
    var groupId = event.dataTransfer.getData("text/x-rh-prompt-group") || event.dataTransfer.getData("text/plain");
    var folderId = target.dataset.promptGroupFolderDropId || "";
    clearPromptGroupDropState();
    movePromptGroupToFolder(groupId, folderId);
  }
  function handlePromptGroupAction(event) {
    var button = event.target.closest("[data-action]");
    if (!button) return;
    var action = button.dataset.action;
    if (action === "open-prompt-group-folder") {
      state.activePromptGroupFolderId = button.dataset.promptGroupFolderId || "";
      renderPromptGroups();
      return;
    }
    if (action === "back-to-prompt-group-folders") {
      state.activePromptGroupFolderId = "";
      renderPromptGroups();
      return;
    }
    if (action === "load-prompt-group") {
      loadPromptGroupIntoWorkbench(button.dataset.promptGroupId || "");
    }
  }
  function handleWorkflowAction(event) {
    var button = event.target.closest("[data-action]");
    if (!button) return;
    var id = button.dataset.workflowId;
    var action = button.dataset.action;
    if (action === "open-folder") {
      state.activeFolderId = button.dataset.folderId || "";
      renderWorkflows();
      return;
    }
    if (action === "back-to-folders") {
      state.activeFolderId = "";
      renderWorkflows();
      return;
    }
    if (action === "select-workflow") {
      loadWorkflowIntoSubmit(id);
      return;
    }
    if (action === "configure-workflow") openConfig(id);
    if (action === "set-telegram-inbound") {
      setTelegramInboundWorkflow(id, button.dataset.enabled === "true", button);
      return;
    }
    if (action === "export-workflow") exportWorkflow(id);
    if (action === "edit-workflow") {
      fetchWorkflow(id).then(function (data) {
        openEditor(data.record, { content: JSON.stringify(data.workflow, null, 2) });
      }).catch(function (error) { showToast("打开编辑失败：" + error.message, true); });
    }
    if (action === "delete-workflow") {
      deleteWorkflowLibraryRecord(id, button);
    }
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
  }
  function bindEvents() {
    updateThemeToggle();
    var workflowsThemeToggle = $("themeToggle");
    if (workflowsThemeToggle) workflowsThemeToggle.addEventListener("click", function () {
      var nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem("rh-workflow-theme", nextTheme); } catch (error) {}
      updateThemeToggle();
    });
    $("refreshWorkflows").addEventListener("click", function () {
      refreshWorkflowLibrary();
    });
    $("workflowGroups").addEventListener("click", handleWorkflowAction);
    $("workflowGroups").addEventListener("keydown", handleFolderNameKeydown);
    $("workflowGroups").addEventListener("blur", handleFolderNameBlur, true);
    $("workflowGroups").addEventListener("contextmenu", handleWorkflowContextMenu);
    $("workflowFolderContextMenu").addEventListener("click", handleFolderMenuAction);
    $("workflowCardContextMenu").addEventListener("click", handleWorkflowMenuAction);
    $("promptGroupGroups").addEventListener("click", handlePromptGroupAction);
    $("promptGroupGroups").addEventListener("keydown", handlePromptGroupFolderNameKeydown);
    $("promptGroupGroups").addEventListener("blur", handlePromptGroupFolderNameBlur, true);
    $("promptGroupGroups").addEventListener("contextmenu", handleWorkflowContextMenu);
    $("promptGroupFolderContextMenu").addEventListener("click", handlePromptGroupFolderMenuAction);
    $("promptGroupCardContextMenu").addEventListener("click", handlePromptGroupMenuAction);
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
    $("workflowGroups").addEventListener("dragstart", handleWorkflowDragStart);
    $("workflowGroups").addEventListener("dragend", handleWorkflowDragEnd);
    $("workflowGroups").addEventListener("dragover", handleFolderDragOver);
    $("workflowGroups").addEventListener("dragleave", handleFolderDragLeave);
    $("workflowGroups").addEventListener("drop", handleFolderDrop);
    $("promptGroupGroups").addEventListener("dragstart", handlePromptGroupDragStart);
    $("promptGroupGroups").addEventListener("dragend", handlePromptGroupDragEnd);
    $("promptGroupGroups").addEventListener("dragover", handlePromptGroupDragOver);
    $("promptGroupGroups").addEventListener("dragleave", handlePromptGroupDragLeave);
    $("promptGroupGroups").addEventListener("drop", handlePromptGroupDrop);
    $("workflowEditorForm").addEventListener("submit", saveWorkflowRecord);
    $("closeWorkflowEditor").addEventListener("click", function () { state.editor = null; window.RHMotion.closeModal("workflowEditorModal"); });
    $("cancelWorkflowEditor").addEventListener("click", function () { state.editor = null; window.RHMotion.closeModal("workflowEditorModal"); });
    $("workflowEditorModal").addEventListener("click", function (event) { if (event.target === this) window.RHMotion.closeModal("workflowEditorModal"); });
    $("saveWorkflowJson").addEventListener("click", saveWorkflowJson);
    $("restoreWorkflowJson").addEventListener("click", restoreWorkflowJson);
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
    $("workflowConfigModal").addEventListener("input", function (event) {
      var index = Number(event.target.dataset.configIndex);
      var editor = state.configEditor;
      var action = event.target.dataset.configAction;
      if (!editor || !Number.isInteger(index) || !editor.items[index]) return;
      if (action === "label") editor.items[index].label = event.target.value;
      if (action === "options") editor.items[index].options = event.target.value.split(/[\n,]/).map(function (value) { return value.trim(); }).filter(Boolean);
      if (action === "default") updateConfigDefault(event.target, index, editor);
    });
    $("workflowConfigModal").addEventListener("change", function (event) {
      var index = Number(event.target.dataset.configIndex);
      var editor = state.configEditor;
      var action = event.target.dataset.configAction;
      if (!editor || !Number.isInteger(index) || !editor.items[index]) return;
      if (action === "default") {
        updateConfigDefault(event.target, index, editor);
        return;
      }
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
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      closeContextMenus();
      window.RHMotion.closeModal("workflowEditorModal");
      window.RHMotion.closeModal("workflowConfigModal");
    });
    document.addEventListener("click", function (event) {
      if (!event.target.closest("#workflowFolderContextMenu, #workflowCardContextMenu, #promptGroupFolderContextMenu, #promptGroupCardContextMenu")) closeContextMenus();
    });
    $("workflowGroups").addEventListener("click", function (event) {
      var button = event.target.closest("#createWorkflowFolder");
      if (button) createWorkflowFolder();
    });
    $("promptGroupGroups").addEventListener("click", function (event) {
      var button = event.target.closest("#createPromptGroupFolder");
      if (button) createPromptGroupFolder();
    });
  }
  bindEvents();
  refreshWorkflows();
})();
