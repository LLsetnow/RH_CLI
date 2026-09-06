(function () {
  "use strict";

  var OUTPUT_PAGE_SIZE = 64;
  var OUTPUT_WORKFLOW_FILTER_MAX_CHARS = 18;
  var UNCLASSIFIED_PROJECT_ID = "__unclassified__";
  var UNBOUND_ACCOUNT_ID = "__unbound__";
  var OUTPUT_TAG_FILTER_TAGS = ["案例", "H"];
  var OUTPUT_TAG_FILTER_MODES = ["off", "include", "exclude"];
  var state = { outputs: [], projects: [], summary: {}, type: "all", rating: 0, tagFilters: { "案例": "off", "H": "off" }, workflowFilter: "", search: "", sort: "newest", page: 1, projectId: "", telegramConfigured: false, contextRangeStart: 0, contextRangeEnd: 0, contextRangeDays: 0, contextAccountId: "", contextWorkflowName: "", contextArtifactId: "", contextProjectId: "", selectedArtifactId: "" };
  var outputImport = { item: null, path: "" };
  var projectMove = { item: null, projectId: "" };
  var projectEditor = { mode: "", projectId: "" };
  var projectDelete = { projectId: "" };
  var draftStorageKey = "rh-workflow-desk-draft-v1";
  var pendingPromptGroupStorageKey = "rh-workflow-desk-pending-prompt-group-v1";
  var outputViewStateKey = "rh-workflow-desk-outputs-v1";
  var outputStatePersistenceReady = false;
  var outputStatePersistenceEnabled = true;
  var ratingBusy = {};
  var tagBusy = {};
  var telegramUploadBusy = {};
  var previewSeekSeconds = 1;

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }
  function request(path, options) {
    var requestOptions = options || {};
    requestOptions.headers = Object.assign({ "Accept": "application/json" }, requestOptions.headers || {});
    return fetch(path, requestOptions).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.message || (requestOptions.method === "DELETE" ? "删除任务失败" : "读取产物失败"));
        return data;
      });
    });
  }
  function showToast(message, isError) {
    var toast = $("outputToast");
    if (window.RHMotion && window.RHMotion.showToast) window.RHMotion.showToast(toast, message, isError);
  }
  function copyTextFallback(text) {
    return new Promise(function (resolve, reject) {
      var input = document.createElement("textarea");
      input.value = text;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.left = "-9999px";
      document.body.appendChild(input);
      input.select();
      var copied = false;
      try { copied = document.execCommand("copy"); } catch (error) { copied = false; }
      input.remove();
      if (copied) resolve();
      else reject(new Error("剪贴板不可用"));
    });
  }
  function copyTextToClipboard(text) {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      return navigator.clipboard.writeText(text).catch(function () { return copyTextFallback(text); });
    }
    return copyTextFallback(text);
  }
  function copyTaskId(taskId, button) {
    var value = String(taskId || "").trim();
    if (!value || !button || button.disabled) return;
    var original = button.textContent;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    copyTextToClipboard(value).then(function () {
      button.textContent = "已复制";
      button.classList.add("is-copied");
      showToast("完整任务 ID 已复制");
      window.setTimeout(function () {
        button.textContent = original;
        button.classList.remove("is-copied");
      }, 1200);
    }).catch(function () {
      showToast("复制任务 ID 失败：剪贴板不可用", true);
    }).finally(function () {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
  }
  function queuePromptGroupSnapshot(group) {
    try {
      if (!group || !Array.isArray(group.items)) {
        localStorage.removeItem(pendingPromptGroupStorageKey);
        return;
      }
      localStorage.setItem(pendingPromptGroupStorageKey, JSON.stringify({ version: 1, group: group }));
    } catch (error) {
      showToast("提示词组状态无法暂存到本机", true);
    }
  }
  function notifySubmitImport(detail) {
    if (window.RHFocus && typeof window.RHFocus.importToSubmit === "function") {
      window.RHFocus.importToSubmit(detail || {});
      return true;
    }
    return false;
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
    if (size < 1024 * 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + " MB";
    return (size / (1024 * 1024 * 1024)).toFixed(1) + " GB";
  }
  function typeLabel(type) {
    return { image: "IMAGE", video: "VIDEO", audio: "AUDIO", text: "TEXT", other: "FILE" }[type] || "FILE";
  }
  function outputStorage() {
    try {
      if (window.localStorage) return window.localStorage;
    } catch (error) {}
    return null;
  }
  function outputContextParams() {
    return new URLSearchParams(window.location.search || "");
  }
  function outputContextParamsHaveValues(params) {
    return Boolean(String(params.get("range_start") || "").trim() || String(params.get("range_end") || "").trim() || String(params.get("range_days") || "").trim() || String(params.get("account_id") || "").trim() || String(params.get("workflow_name") || "").trim());
  }
  function outputUrlHasContext() {
    return outputContextParamsHaveValues(outputContextParams());
  }
  function defaultOutputTagFilters() {
    return { "案例": "off", "H": "off" };
  }
  function restoredOutputTagFilters(value) {
    var filters = defaultOutputTagFilters();
    OUTPUT_TAG_FILTER_TAGS.forEach(function (tag) {
      var mode = value && value[tag];
      if (OUTPUT_TAG_FILTER_MODES.indexOf(mode) !== -1) filters[tag] = mode;
    });
    return filters;
  }
  function restoredOutputType(value) {
    var type = String(value == null ? "" : value);
    return ["all", "image", "video", "audio", "text", "other"].indexOf(type) !== -1 ? type : "all";
  }
  function restoredOutputRating(value) {
    if (value === "unrated") return value;
    var rating = Number(value);
    return rating >= 1 && rating <= 5 ? Math.floor(rating) : 0;
  }
  function restoredOutputSort(value) {
    var sort = String(value == null ? "" : value);
    return ["newest", "oldest", "name"].indexOf(sort) !== -1 ? sort : "newest";
  }
  function restoreOutputViewState() {
    var storage = outputStorage();
    var saved = null;
    if (storage) {
      try {
        saved = JSON.parse(storage.getItem(outputViewStateKey) || "null");
      } catch (error) {
        saved = null;
      }
    }
    if (!saved || saved.version !== 1) {
      outputStatePersistenceReady = true;
      return;
    }
    if (saved.projectId != null) state.projectId = String(saved.projectId);
    if (saved.type != null) state.type = restoredOutputType(saved.type);
    if (saved.rating != null) state.rating = restoredOutputRating(saved.rating);
    if (saved.tagFilters && typeof saved.tagFilters === "object") state.tagFilters = restoredOutputTagFilters(saved.tagFilters);
    if (saved.workflowFilter != null) state.workflowFilter = String(saved.workflowFilter);
    if (saved.search != null) state.search = String(saved.search);
    if (saved.sort != null) state.sort = restoredOutputSort(saved.sort);
    if (saved.page != null) state.page = Math.max(1, Math.floor(Number(saved.page) || 1));
    if (saved.selectedArtifactId != null) state.selectedArtifactId = String(saved.selectedArtifactId);
    var searchInput = $("outputSearch");
    if (searchInput) searchInput.value = state.search;
    var sortInput = $("outputSort");
    if (sortInput) sortInput.value = state.sort;
    outputStatePersistenceReady = true;
  }
  function saveOutputViewState() {
    if (!outputStatePersistenceReady || !outputStatePersistenceEnabled) return;
    var storage = outputStorage();
    if (!storage) return;
    try {
      storage.setItem(outputViewStateKey, JSON.stringify({
        version: 1,
        projectId: String(state.projectId || ""),
        type: restoredOutputType(state.type),
        rating: restoredOutputRating(state.rating),
        tagFilters: restoredOutputTagFilters(state.tagFilters),
        workflowFilter: String(state.workflowFilter || ""),
        search: String(state.search || ""),
        sort: restoredOutputSort(state.sort),
        page: Math.max(1, Math.floor(Number(state.page) || 1)),
        selectedArtifactId: String(state.selectedArtifactId || "")
      }));
    } catch (error) {}
  }
  function resetOutputViewForContext() {
    state.projectId = "";
    state.type = "all";
    state.rating = 0;
    state.tagFilters = defaultOutputTagFilters();
    state.workflowFilter = "";
    state.page = 1;
    state.selectedArtifactId = "";
  }
  function readOutputContext() {
    var params = outputContextParams();
    var hasExplicitContext = outputContextParamsHaveValues(params);
    outputStatePersistenceEnabled = !hasExplicitContext;
    if (hasExplicitContext) resetOutputViewForContext();
    state.contextRangeStart = Number(params.get("range_start") || 0) || 0;
    state.contextRangeEnd = Number(params.get("range_end") || 0) || 0;
    state.contextRangeDays = Number(params.get("range_days") || 0) || 0;
    state.contextAccountId = String(params.get("account_id") || "").trim();
    state.contextWorkflowName = String(params.get("workflow_name") || "").trim();
    if (hasExplicitContext) state.search = state.contextWorkflowName;
    var searchInput = $("outputSearch");
    if (searchInput) searchInput.value = state.search;
    var sortInput = $("outputSort");
    if (sortInput) sortInput.value = state.sort;
  }
  function hasOutputContext() {
    return Boolean(state.contextWorkflowName || state.contextRangeStart || state.contextRangeEnd || state.contextAccountId);
  }
  function contextOutputMatches(item) {
    var createdAt = Number(item && item.task_created_at || 0);
    if (state.contextRangeStart && createdAt < state.contextRangeStart) return false;
    if (state.contextRangeEnd && createdAt >= state.contextRangeEnd) return false;
    if (state.contextAccountId === UNBOUND_ACCOUNT_ID && String(item && item.account_id || "").trim()) return false;
    if (state.contextAccountId && state.contextAccountId !== UNBOUND_ACCOUNT_ID && String(item && item.account_id || "").trim() !== state.contextAccountId) return false;
    return true;
  }
  function normalizedOutputTags(value) {
    var raw = value && Array.isArray(value.tags) ? value.tags : [];
    var tags = [];
    raw.forEach(function (tag) {
      var clean = String(tag || "").trim();
      if (clean && tags.indexOf(clean) === -1) tags.push(clean);
    });
    return tags;
  }
  function hasOutputTag(item, tag) {
    return normalizedOutputTags(item).indexOf(String(tag || "").trim()) !== -1;
  }
  function outputTagFilterMode(tag) {
    var mode = state.tagFilters && state.tagFilters[tag];
    return OUTPUT_TAG_FILTER_MODES.indexOf(mode) === -1 ? "off" : mode;
  }
  function outputTagFilterMatches(item, tag) {
    var mode = outputTagFilterMode(tag);
    if (mode === "include") return hasOutputTag(item, tag);
    if (mode === "exclude") return !hasOutputTag(item, tag);
    return true;
  }
  function matchesOutputTagFilters(item) {
    return OUTPUT_TAG_FILTER_TAGS.every(function (tag) { return outputTagFilterMatches(item, tag); });
  }
  function outputWorkflowName(item) {
    return String(item && (item.task_name || item.workflow_name) || "").trim();
  }
  function outputWorkflowNames() {
    if (!state.projectId) return [];
    var names = {};
    state.outputs.forEach(function (item) {
      if (!contextOutputMatches(item) || !belongsToProject(item, state.projectId)) return;
      var name = outputWorkflowName(item);
      if (name) names[name] = true;
    });
    return Object.keys(names).sort(function (left, right) { return left.localeCompare(right, "zh-CN"); });
  }
  function outputWorkflowFilterLabel(name) {
    var characters = Array.from(String(name || ""));
    if (characters.length <= OUTPUT_WORKFLOW_FILTER_MAX_CHARS) return characters.join("");
    return characters.slice(0, OUTPUT_WORKFLOW_FILTER_MAX_CHARS - 1).join("") + "…";
  }
  function outputWorkflowFilterMatches(item) {
    return !state.workflowFilter || outputWorkflowName(item) === state.workflowFilter;
  }
  function renderWorkflowFilters() {
    var container = $("outputWorkflowFilters");
    if (!container) return;
    var names = outputWorkflowNames();
    if (!state.projectId || !names.length) {
      state.workflowFilter = "";
      container.hidden = true;
      container.innerHTML = "";
      return;
    }
    if (state.workflowFilter && names.indexOf(state.workflowFilter) === -1) state.workflowFilter = "";
    var options = ['<button class="output-workflow-filter' + (!state.workflowFilter ? ' is-selected' : '') + '" type="button" data-output-workflow="" aria-pressed="' + (!state.workflowFilter ? 'true' : 'false') + '" title="显示当前文件夹中的全部工作流">全部</button>'];
    names.forEach(function (name) {
      var active = name === state.workflowFilter;
      options.push('<button class="output-workflow-filter' + (active ? ' is-selected' : '') + '" type="button" data-output-workflow="' + esc(name) + '" aria-pressed="' + (active ? 'true' : 'false') + '" title="' + esc(name) + '">' + esc(outputWorkflowFilterLabel(name)) + '</button>');
    });
    container.hidden = false;
    container.innerHTML = '<span class="output-workflow-filter-label">工作流</span><div class="output-workflow-filter-options" role="group" aria-label="工作流名称">' + options.join("") + '</div>';
  }
  function isMediaOutput(item) {
    return item && item.kind === "file" && ["image", "video", "audio"].indexOf(item.display_type) !== -1;
  }
  function caseMediaOutputs() {
    return filteredOutputs().filter(function (item) { return isMediaOutput(item) && hasOutputTag(item, "案例"); });
  }
  function oneStarOutputs() {
    return filteredOutputs().filter(function (item) { return normalizedRating(item.rating) === 1; });
  }
  function outputActionQuery(projectId) {
    var pairs = [];
    function add(name, value) {
      var clean = String(value == null ? "" : value).trim();
      if (clean) pairs.push(encodeURIComponent(name) + "=" + encodeURIComponent(clean));
    }
    add("project_id", projectId == null ? state.projectId : projectId);
    add("search", state.search);
    if (state.type !== "all") add("type", state.type);
    if (state.rating === "unrated") add("rating", "unrated");
    else if (typeof state.rating === "number" && state.rating) add("rating", state.rating);
    if (state.workflowFilter) add("workflow", state.workflowFilter);
    add("tag_case", outputTagFilterMode("案例") === "off" ? "" : outputTagFilterMode("案例"));
    add("tag_h", outputTagFilterMode("H") === "off" ? "" : outputTagFilterMode("H"));
    if (state.contextRangeStart) add("range_start", state.contextRangeStart);
    if (state.contextRangeEnd) add("range_end", state.contextRangeEnd);
    add("account_id", state.contextAccountId);
    return pairs.length ? "?" + pairs.join("&") : "";
  }
  function outputProjectQuery(projectId) {
    return outputActionQuery(projectId);
  }
  function outputActionKey(item) {
    if (item && item.task_id != null && item.output_index != null) return String(item.task_id) + ":" + String(item.output_index);
    return String(item && item.id || "");
  }
  function outputActionScopeLabel() {
    return state.projectId ? "当前文件夹内的" : "全部成片中的";
  }
  function outputProjectId(item) {
    return String(item && item.project_id || "").trim();
  }
  function belongsToProject(item, projectId) {
    if (!projectId) return true;
    var itemProjectId = outputProjectId(item);
    return projectId === UNCLASSIFIED_PROJECT_ID ? !itemProjectId : itemProjectId === projectId;
  }
  function outputProjectRecords() {
    var records = {};
    state.projects.forEach(function (project) {
      var id = String(project && project.id || "").trim();
      if (!id) return;
      records[id] = {
        id: id,
        name: String(project.name || "").trim() || "未命名项目",
        path: String(project.path || "").trim(),
        outputs: 0,
        taskIds: {},
        taskCount: Number(project.task_count || 0),
        latest: Number(project.updated_at || project.created_at || 0)
      };
    });
    state.outputs.forEach(function (item) {
      if (!contextOutputMatches(item)) return;
      var id = outputProjectId(item) || UNCLASSIFIED_PROJECT_ID;
      if (!records[id]) {
        records[id] = { id: id, name: id === UNCLASSIFIED_PROJECT_ID ? "未归类" : "", path: "", outputs: 0, taskIds: {}, taskCount: 0, latest: 0 };
      }
      var record = records[id];
      if (id !== UNCLASSIFIED_PROJECT_ID && !record.name) record.name = String(item.project_name || "").trim();
      if (id !== UNCLASSIFIED_PROJECT_ID && !record.path) record.path = String(item.project_path || "").trim();
      record.outputs += 1;
      var taskId = String(item.task_id || "").trim();
      if (taskId) record.taskIds[taskId] = true;
      record.latest = Math.max(record.latest, Number(item.modified_at || item.task_completed_at || item.task_created_at || 0));
    });
    return Object.keys(records).map(function (id) {
      var record = records[id];
      record.tasks = Math.max(Number(record.taskCount || 0), Object.keys(record.taskIds).length);
      delete record.taskIds;
      delete record.taskCount;
      if (!record.name) record.name = record.id === UNCLASSIFIED_PROJECT_ID ? "未归类" : "未命名项目";
      return record;
    }).sort(function (left, right) {
      if (left.id === UNCLASSIFIED_PROJECT_ID) return 1;
      if (right.id === UNCLASSIFIED_PROJECT_ID) return -1;
      if (left.latest !== right.latest) return right.latest - left.latest;
      return left.name.localeCompare(right.name, "zh-CN");
    });
  }
  function outputProjectRecord(projectId) {
    return outputProjectRecords().find(function (record) { return record.id === projectId; }) || null;
  }
  function outputProjectTaskCount(projectId) {
    var record = projectId ? outputProjectRecord(projectId) : null;
    if (record) return Number(record.tasks || 0);
    if (!projectId && state.summary && !hasOutputContext()) return Number(state.summary.tasks || 0);
    var taskIds = {};
    state.outputs.forEach(function (item) {
      if (belongsToProject(item, projectId)) {
        var taskId = String(item.task_id || "").trim();
        if (taskId) taskIds[taskId] = true;
      }
    });
    return Object.keys(taskIds).length;
  }
  function outputProjectCard(record, selected) {
    var unclassified = record.id === UNCLASSIFIED_PROJECT_ID;
    var icon = unclassified ? "–" : "▰";
    var detail = record.tasks + " 个任务 · " + record.outputs + " 个成片";
    var context = unclassified ? "" : ' data-output-project-context="' + esc(record.id) + '"';
    var drop = ' data-output-project-drop="' + esc(record.id) + '"';
    var label = (unclassified ? "未归类" : record.name) + "，可接收拖入的成片卡片";
    return '<button class="output-project-card' + (unclassified ? ' is-unclassified' : '') + (selected ? ' is-selected' : '') + '" type="button" data-output-project="' + esc(record.id) + '"' + context + drop + ' aria-pressed="' + (selected ? "true" : "false") + '" aria-label="' + esc(label) + '">' +
      '<span class="output-project-icon" aria-hidden="true">' + icon + '</span>' +
      '<span class="output-project-card-copy"><strong>' + esc(record.name) + '</strong><small>' + esc(detail) + '</small>' + (record.path ? '<span class="output-project-path" title="' + esc(record.path) + '">' + esc(record.path) + '</span>' : '') + '</span>' +
      '<span class="output-project-card-date">' + esc(formatTime(record.latest)) + '</span>' +
      '</button>';
  }
  function renderProjectBrowser() {
    var grid = $("outputProjectGrid");
    var breadcrumb = $("outputProjectBreadcrumb");
    var count = $("outputProjectCount");
    var showAll = $("showAllOutputProjects");
    if (!grid || !breadcrumb || !count) return;
    var records = outputProjectRecords();
    var selected = state.projectId ? outputProjectRecord(state.projectId) : null;
    if (state.projectId && state.projectId !== UNCLASSIFIED_PROJECT_ID && !selected) {
      state.projectId = "";
      selected = null;
    }
    if (selected || state.projectId === UNCLASSIFIED_PROJECT_ID) {
      var current = selected || { id: UNCLASSIFIED_PROJECT_ID, name: "未归类", path: "", outputs: state.outputs.filter(function (item) { return !outputProjectId(item); }).length, tasks: 0, latest: 0 };
      current.tasks = outputProjectTaskCount(state.projectId);
      breadcrumb.innerHTML = '<button class="output-project-back" type="button" data-output-project="">全部成片</button><span class="output-project-separator" aria-hidden="true">/</span><strong title="' + esc(current.name) + '">' + esc(current.name) + '</strong>';
      count.textContent = current.tasks + " 个任务 · " + current.outputs + " 个成片";
      if (showAll) showAll.hidden = false;
      grid.innerHTML = '<div class="output-project-current' + (state.projectId === UNCLASSIFIED_PROJECT_ID ? ' is-unclassified' : '') + '" data-output-project-drop="' + esc(current.id) + '"' + (state.projectId === UNCLASSIFIED_PROJECT_ID ? '' : ' data-output-project-context="' + esc(current.id) + '"') + '><span class="output-project-current-kicker">CURRENT PROJECT</span><strong>' + esc(current.name) + '</strong><span>' + esc(current.tasks + " 个任务 · " + current.outputs + " 个成片") + '</span>' + (current.path ? '<small title="' + esc(current.path) + '">' + esc(current.path) + '</small>' : '') + '</div>';
      return;
    }
    var namedCount = records.filter(function (record) { return record.id !== UNCLASSIFIED_PROJECT_ID; }).length;
    var unclassified = records.find(function (record) { return record.id === UNCLASSIFIED_PROJECT_ID; });
    breadcrumb.innerHTML = '<div class="section-kicker">PROJECT FOLDERS</div><h2 id="outputProjectTitle">全部成片</h2>';
    count.textContent = namedCount + " 个项目" + (unclassified ? " · " + unclassified.outputs + " 未归类成片" : "");
    if (showAll) showAll.hidden = true;
    grid.innerHTML = records.length ? records.map(function (record) { return outputProjectCard(record, false); }).join("") : '<div class="output-project-empty"><strong>还没有项目</strong><span>新建项目后，可从成片右键菜单将任务归入其中。</span></div>';
  }
  function costLabel(item) {
    if (!item.cost) return "";
    return item.cost_type === "money" ? "$" + item.cost : "消耗 " + item.cost + " RH 币";
  }
  function updateFilterSlider() {
    var container = $("outputFilters");
    var slider = container && container.querySelector(".output-filter-slider");
    var active = container && container.querySelector(".output-filter.active");
    if (!slider || !active) return;
    slider.style.width = active.offsetWidth + "px";
    slider.style.height = active.offsetHeight + "px";
    slider.style.transform = "translate3d(" + active.offsetLeft + "px, " + active.offsetTop + "px, 0)";
    slider.classList.add("is-ready");
  }
  function outputUrl(item) {
    return "/api/tasks/" + encodeURIComponent(item.task_id) + "/output/" + encodeURIComponent(item.file_index);
  }
  function filteredOutputs() {
    var query = state.search.trim().toLowerCase();
    var result = state.outputs.filter(function (item) {
      if (!contextOutputMatches(item)) return false;
      if (!belongsToProject(item, state.projectId)) return false;
      if (state.type !== "all" && item.display_type !== state.type) return false;
      if (state.rating === "unrated" && normalizedRating(item.rating) !== 0) return false;
      if (typeof state.rating === "number" && state.rating && normalizedRating(item.rating) !== state.rating) return false;
      if (!matchesOutputTagFilters(item)) return false;
      if (!outputWorkflowFilterMatches(item)) return false;
      if (!query) return true;
      return String(item.name || "").toLowerCase().indexOf(query) !== -1 || String(item.task_name || "").toLowerCase().indexOf(query) !== -1 || String(item.project_name || "").toLowerCase().indexOf(query) !== -1;
    });
    result.sort(function (left, right) {
      if (state.sort === "name") return String(left.name || "").localeCompare(String(right.name || ""), "zh-CN");
      var leftTime = Number(left.modified_at || left.task_completed_at || left.task_created_at || 0);
      var rightTime = Number(right.modified_at || right.task_completed_at || right.task_created_at || 0);
      return state.sort === "oldest" ? leftTime - rightTime : rightTime - leftTime;
    });
    return result;
  }
  function outputPageCount(items) {
    var count = Array.isArray(items) ? items.length : Number(items) || 0;
    return Math.max(1, Math.ceil(count / OUTPUT_PAGE_SIZE));
  }
  function outputPageItems(items) {
    var pageCount = outputPageCount(items);
    state.page = Math.min(Math.max(Number(state.page) || 1, 1), pageCount);
    var start = (state.page - 1) * OUTPUT_PAGE_SIZE;
    return (items || []).slice(start, start + OUTPUT_PAGE_SIZE);
  }
  function resetOutputPage() {
    state.page = 1;
  }
  function pageNumberMarkup(page, currentPage) {
    var active = page === currentPage;
    return '<button class="output-page-number' + (active ? ' active' : '') + '" type="button" data-output-page="' + page + '"' + (active ? ' aria-current="page"' : '') + ' aria-label="第 ' + page + ' 页"' + (active ? ' aria-pressed="true"' : ' aria-pressed="false"') + '>' + page + '</button>';
  }
  function renderPagination(totalItems) {
    var pagination = $("outputPagination");
    if (!pagination) return;
    var totalPages = outputPageCount(totalItems);
    if (totalPages <= 1) {
      pagination.hidden = true;
      pagination.innerHTML = "";
      return;
    }
    state.page = Math.min(Math.max(Number(state.page) || 1, 1), totalPages);
    var currentPage = state.page;
    var pageNumbers = [];
    var start = Math.max(2, currentPage - 2);
    var end = Math.min(totalPages - 1, currentPage + 2);
    pageNumbers.push(pageNumberMarkup(1, currentPage));
    if (start > 2) pageNumbers.push('<span class="output-page-ellipsis" aria-hidden="true">…</span>');
    for (var page = start; page <= end; page += 1) pageNumbers.push(pageNumberMarkup(page, currentPage));
    if (end < totalPages - 1) pageNumbers.push('<span class="output-page-ellipsis" aria-hidden="true">…</span>');
    if (totalPages > 1) pageNumbers.push(pageNumberMarkup(totalPages, currentPage));
    pagination.hidden = false;
    pagination.innerHTML = '<button class="output-page-button" type="button" data-output-page="previous"' + (currentPage === 1 ? ' disabled' : '') + ' aria-label="上一页">上一页</button>' +
      '<div class="output-page-numbers" role="list" aria-label="页码">' + pageNumbers.join("") + '</div>' +
      '<span class="output-page-status">第 ' + currentPage + ' / ' + totalPages + ' 页 · 当前显示 ' + Math.min(OUTPUT_PAGE_SIZE, Math.max(0, totalItems - (currentPage - 1) * OUTPUT_PAGE_SIZE)) + ' 张</span>' +
      '<button class="output-page-button" type="button" data-output-page="next"' + (currentPage === totalPages ? ' disabled' : '') + ' aria-label="下一页">下一页</button>';
  }
  function renderSummary() {
    var summary = scopedOutputSummary();
    var ratingCounts = summary.rating_counts || {};
    var oneStarCount = oneStarOutputs().length;
    $("heroTotal").textContent = String(summary.total || 0);
    $("outputCount").textContent = String(filteredOutputs().length);
    $("heroUpdated").textContent = summary.total ? "来自 " + String(summary.tasks || 0) + " 个本地任务" : "暂无可浏览产物";
    document.querySelectorAll(".output-filter").forEach(function (button) {
      var type = button.dataset.outputType;
      button.querySelector("span").textContent = String(type === "all" ? (summary.total || 0) : (summary[type] || 0));
      var active = type === state.type;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".output-rating-filter").forEach(function (button) {
      var ratingValue = button.dataset.outputRating || "0";
      var rating = ratingValue === "unrated" ? "unrated" : Number(ratingValue);
      var active = rating === state.rating;
      var count = rating === "unrated" ? (ratingCounts.unrated || 0) : (rating ? (ratingCounts[String(rating)] || 0) : (summary.total || 0));
      var countNode = button.querySelector("[data-rating-count]");
      if (countNode) countNode.textContent = rating === 0 ? "全部" : String(count);
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    document.querySelectorAll(".output-tag-filter").forEach(function (button) {
      var tag = button.dataset.outputTag || "";
      var mode = outputTagFilterMode(tag);
      var label = { off: "不启用", include: "包含", exclude: "不包含" }[mode] || "不启用";
      button.dataset.outputTagMode = mode;
      button.textContent = label;
      button.classList.add("active");
      button.setAttribute("aria-label", tag + "：" + label);
      button.setAttribute("aria-pressed", mode === "off" ? "false" : "true");
    });
    var deleteOneStarButton = $("deleteOneStarOutputs");
    if (deleteOneStarButton) {
      deleteOneStarButton.disabled = !oneStarCount;
      $("oneStarOutputCount").textContent = String(oneStarCount);
      deleteOneStarButton.title = oneStarCount ? "删除" + outputActionScopeLabel() + " " + oneStarCount + " 个一星成片" : "没有一星成片可删除";
    }
    var exportCaseButton = $("exportCaseOutputs");
    if (exportCaseButton) {
      var caseCount = caseMediaOutputs().length;
      exportCaseButton.disabled = !caseCount;
      $("caseOutputCount").textContent = String(caseCount);
      exportCaseButton.title = caseCount ? "下载 " + outputActionScopeLabel() + " " + caseCount + " 个案例媒体（ZIP）" : "没有带“案例”标签的媒体文件可导出";
    }
    updateFilterSlider();
  }
  function scopedOutputSummary() {
    var summary = { total: 0, tasks: 0, image: 0, video: 0, audio: 0, other: 0, text: 0, rating_counts: { unrated: 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 }, tag_counts: { "案例": 0, "H": 0 } };
    var taskIds = {};
    state.outputs.forEach(function (item) {
      if (!contextOutputMatches(item) || !belongsToProject(item, state.projectId)) return;
      summary.total += 1;
      var type = String(item.display_type || "other");
      if (summary[type] == null) type = "other";
      summary[type] += 1;
      var rating = normalizedRating(item.rating);
      summary.rating_counts[rating ? String(rating) : "unrated"] += 1;
      normalizedOutputTags(item).forEach(function (tag) {
        summary.tag_counts[tag] = (summary.tag_counts[tag] || 0) + 1;
      });
      var taskId = String(item.task_id || "").trim();
      if (taskId) taskIds[taskId] = true;
    });
    summary.tasks = Object.keys(taskIds).length;
    return summary;
  }
  function rebuildSummary() {
    var summary = { total: state.outputs.length, tasks: 0, image: 0, video: 0, audio: 0, other: 0, text: 0, rating_counts: { unrated: 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 }, tag_counts: { "案例": 0, "H": 0 } };
    var taskIds = {};
    state.outputs.forEach(function (item) {
      var type = String(item.display_type || "other");
      if (summary[type] == null) type = "other";
      summary[type] += 1;
      var rating = Number(item.rating || 0);
      if (rating >= 1 && rating <= 5) summary.rating_counts[String(rating)] += 1;
      else summary.rating_counts.unrated += 1;
      normalizedOutputTags(item).forEach(function (tag) {
        summary.tag_counts[tag] = (summary.tag_counts[tag] || 0) + 1;
      });
      taskIds[String(item.task_id || "")] = true;
    });
    summary.tasks = Object.keys(taskIds).filter(function (taskId) { return taskId; }).length;
    state.summary = summary;
  }
  function mediaMarkup(item) {
    if (item.kind === "text") {
      return '<div class="artifact-media artifact-media-text"><pre>' + esc(item.text || "") + "</pre></div>";
    }
    var url = outputUrl(item);
    if (item.display_type === "image") {
      return '<div class="artifact-media"><img src="' + url + '" alt="' + esc(item.name) + '" loading="lazy" /></div>';
    }
    if (item.display_type === "video") {
      return '<div class="artifact-media">' + window.RHMotion.videoPlayerMarkup(url, false, false) + '</div>';
    }
    if (item.display_type === "audio") {
      return '<div class="artifact-media"><audio src="' + url + '" controls preload="metadata"></audio></div>';
    }
    return '<div class="artifact-media artifact-media-other"><a class="output-link" href="' + url + '" target="_blank" rel="noreferrer">打开或下载文件</a></div>';
  }
  function previewRatingMarkup(item) {
    return '<span class="output-preview-rating-label">评分</span>' + ratingStarsMarkup(item, "output-preview-rating");
  }
  function previewMediaMarkup(item) {
    if (item.kind === "text") return '<div class="output-preview-text"><pre>' + esc(item.text || "") + '</pre></div><div class="output-preview-controls output-preview-controls-standalone">' + previewRatingMarkup(item) + '</div>';
    var url = outputUrl(item);
    if (item.display_type === "video") return window.RHMotion.videoPlayerMarkup(url, true, true, previewRatingMarkup(item));
    var media = item.display_type === "image" ? '<img src="' + url + '" alt="' + esc(item.name) + '" />' : item.display_type === "audio" ? '<audio src="' + url + '" controls autoplay preload="metadata"></audio>' : '<div class="output-preview-other"><a class="output-link" href="' + url + '" target="_blank" rel="noreferrer">打开或下载文件</a></div>';
    return media + '<div class="output-preview-controls output-preview-controls-standalone">' + previewRatingMarkup(item) + '</div>';
  }
  function stopPreviewMedia() {
    var content = $("outputPreviewContent");
    if (!content) return;
    content.querySelectorAll("video, audio").forEach(function (media) {
      try { media.pause(); } catch (error) {}
      media.removeAttribute("src");
      try { media.load(); } catch (error) {}
    });
  }
  function closeOutputPreview() {
    stopPreviewMedia();
    window.RHMotion.closeModal("outputPreviewModal");
  }
  function openOutputPreview(item) {
    if (!item) return;
    selectArtifactCard(item, false);
    stopPreviewMedia();
    $("outputPreviewTitle").textContent = item.name || "产物预览";
    $("outputPreviewMeta").innerHTML = '<span>' + esc(typeLabel(item.display_type)) + '</span><span>任务：' + esc(item.task_name || item.task_id || "当前任务") + '</span><span>' + esc(formatTime(item.modified_at || item.task_completed_at || item.task_created_at)) + '</span>' + (item.kind === "file" ? '<span>' + esc(formatSize(item.size)) + '</span>' : '');
    $("outputPreviewContent").innerHTML = previewMediaMarkup(item);
    window.RHMotion.bindVideoLoopControls($("outputPreviewContent"));
    window.RHMotion.openModal("outputPreviewModal", "closeOutputPreview");
  }
  function normalizedRating(value) {
    var rating = Number(value || 0);
    return rating >= 1 && rating <= 5 ? Math.floor(rating) : 0;
  }
  function ratingStarsMarkup(item, extraClass) {
    var rating = normalizedRating(item && item.rating);
    var stars = "";
    for (var index = 1; index <= 5; index += 1) {
      stars += '<button class="rating-star' + (index <= rating ? ' is-filled' : '') + '" type="button" data-rate-output="' + esc(item && item.id) + '" data-rating="' + index + '" aria-label="评分 ' + index + ' 星">' + (index <= rating ? '★' : '☆') + '</button>';
    }
    return '<div class="artifact-rating' + (extraClass ? " " + extraClass : "") + '" aria-label="' + esc(rating ? "评分 " + rating + " 星" : "未评分") + '"><span class="rating-stars rating-stars-' + rating + '">' + stars + '</span><span class="rating-value">' + (rating ? rating + " / 5" : "未评分") + '</span></div>';
  }
  function artifactTagsMarkup(item) {
    var tags = [];
    if (hasOutputTag(item, "案例")) tags.push('<span class="artifact-tag artifact-tag-case" title="案例" aria-label="案例">案例</span>');
    if (hasOutputTag(item, "H")) tags.push('<span class="artifact-tag artifact-tag-h" title="H" aria-label="H">H</span>');
    return tags.join("");
  }
  function positiveDimension(value) {
    var number = Number(value);
    return Number.isFinite(number) && number > 0 ? Math.round(number) : 0;
  }
  function itemResolution(item) {
    if (!item) return null;
    var width = positiveDimension(item.width || item.video_width || item.media_width || item.natural_width);
    var height = positiveDimension(item.height || item.video_height || item.media_height || item.natural_height);
    return width && height ? { width: width, height: height } : null;
  }
  function formatResolution(resolution) {
    return resolution ? resolution.width + " × " + resolution.height : "";
  }
  function artifactResolutionMarkup(item) {
    if (!item || ["image", "video"].indexOf(item.display_type) === -1) return "";
    var resolution = itemResolution(item);
    return '<span class="artifact-resolution" title="媒体分辨率">' + esc(formatResolution(resolution) || "读取中…") + '</span>';
  }
  function formatVideoDuration(value) {
    var seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    return Math.round(seconds) + "s";
  }
  function artifactDurationMarkup(item) {
    if (!item || item.display_type !== "video") return "";
    return '<span class="artifact-duration" title="视频时长">读取中…</span>';
  }
  function updateArtifactResolution(card, item, media) {
    var resolutionNode = card && card.querySelector(".artifact-resolution");
    var durationNode = card && card.querySelector(".artifact-duration");
    if (!resolutionNode && !durationNode) return;
    var resolution = itemResolution(item);
    if (!resolution && media) {
      var width = media.tagName === "IMG" ? media.naturalWidth : media.videoWidth;
      var height = media.tagName === "IMG" ? media.naturalHeight : media.videoHeight;
      width = positiveDimension(width);
      height = positiveDimension(height);
      if (width && height) {
        item.width = width;
        item.height = height;
        resolution = { width: width, height: height };
      }
    }
    var isLoading = media && (media.tagName === "IMG" ? !media.complete : media.readyState === 0);
    if (resolutionNode) {
      resolutionNode.textContent = formatResolution(resolution) || (isLoading ? "读取中…" : "—");
      resolutionNode.title = resolution ? "媒体分辨率 " + formatResolution(resolution) : "暂时无法读取媒体分辨率";
    }
    if (durationNode) {
      var duration = media && media.tagName === "VIDEO" ? media.duration : Number(item && (item.duration_seconds || item.video_seconds || item.duration));
      var durationLabel = formatVideoDuration(duration);
      durationNode.textContent = durationLabel || (isLoading ? "读取中…" : "—");
      durationNode.title = durationLabel ? "视频时长 " + durationLabel : "暂时无法读取视频时长";
    }
  }
  function bindArtifactResolutionMetadata(container) {
    if (!container) return;
    container.querySelectorAll(".artifact-card").forEach(function (card) {
      var item = artifactById(card.dataset.artifactId);
      var media = card.querySelector("img, video");
      if (!item || !media) return;
      var update = function () { updateArtifactResolution(card, item, media); };
      media.addEventListener(media.tagName === "IMG" ? "load" : "loadedmetadata", update, { once: true });
      update();
    });
  }
  function artifactCardHeadMarkup(item) {
    var size = item && item.kind === "file" ? formatSize(item.size) : "文本";
    return '<div class="artifact-card-head"><div class="artifact-card-labels"><span class="artifact-type ' + esc(item.display_type) + '">' + typeLabel(item.display_type) + '</span>' + artifactTagsMarkup(item) + '</div><span class="artifact-size">' + size + '</span></div>';
  }
  function refreshRatedArtifact(item) {
    var card = null;
    document.querySelectorAll(".artifact-card").forEach(function (candidate) {
      if (String(candidate.dataset.artifactId) === String(item && item.id)) card = candidate;
    });
    if (!card) return;
    var stillVisible = filteredOutputs().some(function (output) { return String(output.id) === String(item.id); });
    if (!stillVisible) {
      refreshFilteredArtifacts();
      return;
    }
    var ratingNode = card.querySelector(".artifact-rating");
    if (ratingNode) ratingNode.outerHTML = ratingStarsMarkup(item);
  }
  function refreshTaggedArtifact(item) {
    var card = artifactCardById(item && item.id);
    if (!card) return;
    var stillVisible = filteredOutputs().some(function (output) { return String(output.id) === String(item.id); });
    if (!stillVisible) {
      refreshFilteredArtifacts();
      return;
    }
    var head = card.querySelector(".artifact-card-head");
    if (head) head.outerHTML = artifactCardHeadMarkup(item);
  }
  function refreshPreviewRating(item) {
    if (!outputPreviewIsOpen() || !item || String(state.selectedArtifactId) !== String(item.id)) return;
    var ratingNode = $("outputPreviewModal").querySelector(".output-preview-rating");
    if (ratingNode) ratingNode.outerHTML = ratingStarsMarkup(item, "output-preview-rating");
  }
  function setOutputRating(item, rating) {
    if (!item) return;
    var nextRating = normalizedRating(rating);
    var key = String(item.id || item.task_id + ":" + item.output_index);
    if (ratingBusy[key] || normalizedRating(item.rating) === nextRating) return;
    ratingBusy[key] = true;
    request("/api/tasks/" + encodeURIComponent(item.task_id) + "/outputs/" + encodeURIComponent(item.output_index), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating: nextRating })
    }).then(function (data) {
      item.rating = normalizedRating(data.output && data.output.rating);
      rebuildSummary();
      renderSummary();
      refreshRatedArtifact(item);
      refreshPreviewRating(item);
      showToast(item.rating ? "已评分 " + item.rating + " 星" : "已清除评分");
    }).catch(function (error) {
      showToast("保存评分失败：" + error.message, true);
    }).finally(function () {
      delete ratingBusy[key];
    });
  }
  function setOutputTags(item, tags, changedTag) {
    if (!item) return;
    var nextTags = normalizedOutputTags({ tags: tags });
    var key = String(item.id || item.task_id + ":" + item.output_index);
    if (tagBusy[key]) return;
    tagBusy[key] = true;
    request("/api/tasks/" + encodeURIComponent(item.task_id) + "/outputs/" + encodeURIComponent(item.output_index), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags: nextTags })
    }).then(function (data) {
      item.tags = normalizedOutputTags(data.output || { tags: nextTags });
      rebuildSummary();
      renderSummary();
      refreshTaggedArtifact(item);
      showToast(hasOutputTag(item, changedTag) ? "已添加“" + changedTag + "”标签" : "已取消“" + changedTag + "”标签");
    }).catch(function (error) {
      showToast("保存标签失败：" + error.message, true);
    }).finally(function () {
      delete tagBusy[key];
    });
  }
  function toggleOutputTag(item, tag) {
    if (!item) return;
    var tags = normalizedOutputTags(item);
    var index = tags.indexOf(tag);
    if (index === -1) tags.push(tag);
    else tags.splice(index, 1);
    setOutputTags(item, tags, tag);
  }
  function toggleCaseTag(item) {
    toggleOutputTag(item, "案例");
  }
  function toggleHTag(item) {
    toggleOutputTag(item, "H");
  }
  function taskWorkflowLabel(item) {
    var taskName = String(item && item.task_name || "未命名任务").trim() || "未命名任务";
    var taskId = String(item && item.task_id || "").trim();
    if (!taskId || (item && item.workflow_available === false)) return esc(taskName);
    return '<button class="artifact-workflow-link" type="button" data-load-task-workflow="' + esc(taskId) + '" title="加载此次工作流草稿" aria-label="加载工作流 ' + esc(taskName) + '">' + esc(taskName) + '</button>';
  }
  function taskIdLabel(item) {
    var taskId = String(item && item.task_id || "").trim();
    if (!taskId) return "";
    var compact = taskId.length > 12 ? taskId.slice(0, 8) + "…" + taskId.slice(-4) : taskId;
    return '<button class="artifact-task-id" type="button" data-copy-task-id="' + esc(taskId) + '" title="点击复制完整任务 ID" aria-label="复制完整任务 ID">' + esc(compact) + '</button>';
  }
  function telegramUploadKey(taskId, outputIndex) {
    return String(taskId || "") + ":" + String(outputIndex);
  }
  function artifactById(artifactId) {
    return state.outputs.find(function (item) { return String(item && item.id) === String(artifactId); }) || null;
  }
  function artifactCardById(artifactId) {
    var card = null;
    document.querySelectorAll("#outputGrid .artifact-card").forEach(function (candidate) {
      if (String(candidate.dataset.artifactId) === String(artifactId)) card = candidate;
    });
    return card;
  }
  function syncArtifactSelection(items) {
    var visibleItems = Array.isArray(items) ? items : filteredOutputs();
    var selected = visibleItems.find(function (item) { return String(item && item.id) === String(state.selectedArtifactId); }) || null;
    if (!selected) selected = visibleItems[0] || null;
    state.selectedArtifactId = selected ? String(selected.id || "") : "";
    document.querySelectorAll("#outputGrid .artifact-card").forEach(function (card) {
      var active = String(card.dataset.artifactId) === state.selectedArtifactId;
      card.classList.toggle("is-selected", active);
      card.setAttribute("aria-selected", active ? "true" : "false");
    });
  }
  function selectArtifactCard(item, focus) {
    if (!item) return;
    state.selectedArtifactId = String(item.id || "");
    syncArtifactSelection();
    saveOutputViewState();
    var card = artifactCardById(state.selectedArtifactId);
    if (!card) return;
    if (focus) {
      card.focus({ preventScroll: true });
      var rect = card.getBoundingClientRect();
      var topInset = 24;
      var bottomInset = 24;
      if (rect.top < topInset) {
        window.scrollTo({ top: Math.max(0, window.scrollY + rect.top - topInset), behavior: "smooth" });
      } else if (rect.bottom > window.innerHeight - bottomInset) {
        window.scrollTo({ top: Math.max(0, window.scrollY + rect.bottom - window.innerHeight + bottomInset), behavior: "smooth" });
      }
    }
  }
  function selectedArtifactCard() {
    return artifactCardById(state.selectedArtifactId);
  }
  function focusSelectedArtifact(afterClick) {
    if (document.querySelector(".modal-backdrop:not([hidden])")) return;
    if (!afterClick && !outputKeyboardNavigationAllowed({ target: document.activeElement })) return;
    var card = selectedArtifactCard();
    if (card && document.activeElement !== card) card.focus({ preventScroll: true });
  }
  function restoreArtifactFocusAfterClick(event) {
    if (!outputKeyboardNavigationAllowed(event)) return;
    // Run after the clicked control, including controls that stop propagation.
    Promise.resolve().then(function () { focusSelectedArtifact(true); });
  }
  function gridColumnCount() {
    var grid = $("outputGrid");
    if (!grid) return 1;
    var columns = window.getComputedStyle(grid).gridTemplateColumns;
    var count = columns && columns !== "none" ? columns.split(/\s+/).filter(Boolean).length : 1;
    return Math.max(1, count);
  }
  function outputKeyboardNavigationAllowed(event) {
    var target = event && event.target;
    if (!target || !target.closest) return true;
    return !target.closest("input, select, textarea, [contenteditable=\"true\"]");
  }
  function navigateSelectedArtifact(direction) {
    var cards = Array.prototype.slice.call(document.querySelectorAll("#outputGrid .artifact-card"));
    if (!cards.length) return false;
    var currentIndex = cards.findIndex(function (card) { return String(card.dataset.artifactId) === String(state.selectedArtifactId); });
    if (currentIndex < 0) {
      var firstItem = artifactById(cards[0].dataset.artifactId);
      selectArtifactCard(firstItem, true);
      return Boolean(firstItem);
    }
    var items = filteredOutputs();
    var currentPage = Math.min(Math.max(Number(state.page) || 1, 1), outputPageCount(items));
    var totalPages = outputPageCount(items);
    var columns = gridColumnCount();
    var targetIndex = currentIndex;
    var targetPage = currentPage;
    var targetPageIndex = -1;
    if (direction === "left" && currentIndex % columns > 0) targetIndex = currentIndex - 1;
    if (direction === "right" && currentIndex % columns < columns - 1 && currentIndex + 1 < cards.length) targetIndex = currentIndex + 1;
    if (direction === "up" || direction === "down") {
      var step = direction === "up" ? -columns : columns;
      var indexedTarget = currentIndex + step;
      if (indexedTarget >= 0 && indexedTarget < cards.length) targetIndex = indexedTarget;
    }
    if (targetIndex === currentIndex && currentPage > 1 && (direction === "left" && currentIndex === 0 || direction === "up" && currentIndex < columns)) {
      targetPage = currentPage - 1;
      targetPageIndex = OUTPUT_PAGE_SIZE - 1;
    } else if (targetIndex === currentIndex && currentPage < totalPages && (direction === "right" && currentIndex === cards.length - 1 || direction === "down" && currentIndex + columns >= cards.length)) {
      targetPage = currentPage + 1;
      targetPageIndex = direction === "down" ? currentIndex % columns : 0;
    }
    if (targetPage !== currentPage) {
      var pageStart = (targetPage - 1) * OUTPUT_PAGE_SIZE;
      var targetPageItems = items.slice(pageStart, pageStart + OUTPUT_PAGE_SIZE);
      var pageTarget = targetPageItems[targetPageIndex] || targetPageItems[0];
      if (!pageTarget) return false;
      closeArtifactContextMenu();
      state.page = targetPage;
      render();
      selectArtifactCard(pageTarget, true);
      return true;
    }
    if (targetIndex === currentIndex) {
      focusSelectedArtifact();
      return true;
    }
    var targetItem = artifactById(cards[targetIndex].dataset.artifactId);
    if (!targetItem) return false;
    closeArtifactContextMenu();
    selectArtifactCard(targetItem, true);
    return true;
  }
  function outputPreviewIsOpen() {
    var modal = $("outputPreviewModal");
    return Boolean(modal && !modal.hidden && modal.classList.contains("is-open"));
  }
  function previewMediaElement() {
    var content = $("outputPreviewContent");
    return content && content.querySelector("video, audio");
  }
  function seekPreviewMedia(delta) {
    var media = previewMediaElement();
    if (!media || !Number.isFinite(media.duration)) return false;
    media.currentTime = Math.max(0, Math.min(media.duration, (Number(media.currentTime) || 0) + delta));
    return true;
  }
  function togglePreviewMedia() {
    var media = previewMediaElement();
    if (!media) return false;
    if (media.paused || media.ended) {
      if (media.ended) media.currentTime = 0;
      var playPromise = media.play();
      if (playPromise && typeof playPromise.catch === "function") playPromise.catch(function () {});
    } else {
      media.pause();
    }
    return true;
  }
  function toggleSelectedVideo() {
    var video = null;
    if (outputPreviewIsOpen()) {
      video = $("outputPreviewContent") && $("outputPreviewContent").querySelector("video");
    } else {
      var card = selectedArtifactCard();
      video = card && card.querySelector(".artifact-media video");
    }
    if (!video) return false;
    if (video.paused || video.ended) {
      if (video.ended) video.currentTime = 0;
      var playPromise = video.play();
      if (playPromise && typeof playPromise.catch === "function") playPromise.catch(function () {});
    } else {
      video.pause();
    }
    return true;
  }
  function positionArtifactContextMenu(menu, event) {
    var left = Math.min(event.clientX, Math.max(8, window.innerWidth - menu.offsetWidth - 8));
    var top = Math.min(event.clientY, Math.max(8, window.innerHeight - menu.offsetHeight - 8));
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }
  function closeArtifactContextMenu() {
    var menu = $("artifactContextMenu");
    if (menu) menu.hidden = true;
    state.contextArtifactId = "";
  }
  function openArtifactContextMenu(event, item) {
    var menu = $("artifactContextMenu");
    if (!menu || !item) return;
    event.preventDefault();
    closeArtifactContextMenu();
    state.contextArtifactId = String(item.id || "");
    var uploadAction = menu.querySelector('[data-artifact-menu-action="upload"]');
    var importAction = menu.querySelector('[data-artifact-menu-action="import"]');
    var folderAction = menu.querySelector('[data-artifact-menu-action="open-folder"]');
    var moveProjectAction = menu.querySelector('[data-artifact-menu-action="move-project"]');
    var canUpload = state.telegramConfigured && item.kind === "file";
    if (uploadAction) {
      var uploadBusy = Boolean(telegramUploadBusy[telegramUploadKey(item.task_id, item.output_index)]);
      uploadAction.hidden = !canUpload;
      uploadAction.disabled = uploadBusy;
      uploadAction.textContent = uploadBusy ? "上传中" : "上传";
      if (uploadBusy) uploadAction.setAttribute("aria-busy", "true");
      else uploadAction.removeAttribute("aria-busy");
    }
    if (importAction) importAction.hidden = item.kind !== "file";
    if (folderAction) folderAction.hidden = item.kind !== "file";
    if (moveProjectAction) moveProjectAction.hidden = !String(item.task_id || "").trim();
    menu.hidden = false;
    positionArtifactContextMenu(menu, event);
    var firstAction = menu.querySelector('button:not([hidden])');
    if (firstAction) firstAction.focus();
  }
  function handleArtifactContextMenu(event) {
    var card = event.target.closest(".artifact-card");
    if (!card || !$("outputGrid").contains(card)) return;
    openArtifactContextMenu(event, artifactById(card.dataset.artifactId));
  }
  function handleArtifactMenuAction(event) {
    var button = event.target.closest("[data-artifact-menu-action]");
    if (!button) return;
    var item = artifactById(state.contextArtifactId);
    var action = button.dataset.artifactMenuAction;
    closeArtifactContextMenu();
    if (!item) return;
    if (action === "upload") uploadArtifactToTelegram(item, button);
    if (action === "import") openOutputImport(item, button);
    if (action === "open-folder") openArtifactFolder(item, button);
    if (action === "move-project") openOutputProjectMove(item);
    if (action === "delete") deleteArtifactTask(item, button);
  }
  function openArtifactFolder(item, button) {
    var taskId = String(item && item.task_id || "").trim();
    if (!taskId || (button && button.disabled)) return;
    var original = button ? button.textContent : "打开所在文件夹";
    if (button) {
      button.disabled = true;
      button.textContent = "打开中…";
      button.setAttribute("aria-busy", "true");
    }
    request("/api/tasks/" + encodeURIComponent(taskId) + "/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    }).then(function (data) {
      showToast(data.message || "已打开媒体所在文件夹");
    }).catch(function (error) {
      showToast("打开文件夹失败：" + error.message, true);
    }).finally(function () {
      if (button) {
        button.disabled = false;
        button.textContent = original;
        button.removeAttribute("aria-busy");
      }
    });
  }
  function uploadArtifactToTelegram(item, button) {
    var taskId = String(item && item.task_id || "").trim();
    var outputIndex = Number(item && item.output_index);
    var key = telegramUploadKey(taskId, outputIndex);
    if (!taskId || !Number.isInteger(outputIndex) || outputIndex < 0 || (button && button.disabled) || telegramUploadBusy[key]) return;
    telegramUploadBusy[key] = true;
    if (button) {
      button.disabled = true;
      button.textContent = "上传中";
      button.setAttribute("aria-busy", "true");
    }
    request("/api/tasks/" + encodeURIComponent(taskId) + "/telegram", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_index: outputIndex })
    }).then(function (data) {
      showToast(data.message || "已加入 Telegram 上传队列");
    }).catch(function (error) {
      showToast("上传到 Telegram 失败：" + error.message, true);
    }).finally(function () {
      delete telegramUploadBusy[key];
      if (button) {
        button.disabled = false;
        button.textContent = "上传";
        button.removeAttribute("aria-busy");
      }
    });
  }
  function taskDraftFromLoadData(data) {
    var task = data && data.task && typeof data.task === "object" ? data.task : {};
    var workflow = data && data.workflow && typeof data.workflow === "object" ? data.workflow : null;
    if (!workflow) throw new Error("任务中没有可加载的 API 工作流");
    var metadata = workflow.__rh_meta__ && typeof workflow.__rh_meta__ === "object" ? workflow.__rh_meta__ : {};
    var accountId = String(task.account_id || "").trim();
    if (accountId === "__general__") accountId = String(metadata.accountId || metadata.account_id || "").trim();
    return {
      version: 1,
      credential: { selectedKeyId: String(task.key_id || "").trim() },
      workflow: {
        id: String(data.workflow_id || "").trim(),
        remoteWorkflowId: String(task.remote_workflow_id || (data.analysis && data.analysis.remote_workflow_id) || "").trim(),
        name: String(task.workflow_name || data.filename || "workflow_api.json").trim() || "workflow_api.json",
        sourceDir: "",
        accountId: accountId,
        data: workflow,
        analysis: data.analysis && typeof data.analysis === "object" ? data.analysis : {},
        inputConfig: data.input_config && typeof data.input_config === "object" ? data.input_config : (task.input_config || null),
        values: {
          files: task.files && typeof task.files === "object" ? task.files : {},
          prompts: task.prompts && typeof task.prompts === "object" ? task.prompts : {},
          customInputs: task.custom_inputs && typeof task.custom_inputs === "object" ? task.custom_inputs : {},
          randomNoise: task.random_noise && typeof task.random_noise === "object" ? task.random_noise : {},
          resolution: task.resolution && typeof task.resolution === "object" ? task.resolution : {},
          bypassedNodes: Array.isArray(task.bypassed_nodes) ? task.bypassed_nodes : (Array.isArray(task.bypassed_inputs) ? task.bypassed_inputs : [])
        },
        savedAt: Date.now()
      }
    };
  }
  function loadTaskWorkflowToSubmit(taskId, button) {
    var original = button ? button.textContent : "";
    if (button) {
      button.disabled = true;
      button.textContent = "读取中…";
    }
    request("/api/tasks/" + encodeURIComponent(taskId) + "/load").then(function (data) {
      var draft = taskDraftFromLoadData(data);
      if (!draft.workflow.id) throw new Error("任务中缺少本地工作流标识");
      queuePromptGroupSnapshot(data.prompt_group);
      window.localStorage.setItem(draftStorageKey, JSON.stringify(draft));
      var focusImport = notifySubmitImport({ kind: "workflow", source: "task" });
      showToast(focusImport ? "工作流已加载，任务提交面板已同步" : "已加载工作流「" + draft.workflow.name + "」，正在打开任务提交页");
      if (!focusImport) window.location.href = "/";
    }).catch(function (error) {
      showToast("加载工作流失败：" + error.message, true);
    }).finally(function () {
      if (button) {
        button.disabled = false;
        button.textContent = original;
      }
    });
  }
  function readTaskDraft() {
    try {
      var raw = window.localStorage.getItem(draftStorageKey);
      var draft = raw ? JSON.parse(raw) : null;
      if (!draft || draft.version !== 1 || !draft.workflow || !draft.workflow.data || typeof draft.workflow.data !== "object") return null;
      return draft;
    } catch (error) {
      return null;
    }
  }
  function filenameFromPath(value) {
    var parts = String(value || "").split(/[\\/]/);
    return parts[parts.length - 1] || "未设置";
  }
  function bypassedNodeMap(values) {
    var raw = values && (values.bypassedNodes || values.bypassed_nodes || values.bypassedInputs || values.bypassed_inputs) || [];
    var result = {};
    if (Array.isArray(raw)) raw.forEach(function (nodeId) { result[String(nodeId)] = true; });
    else if (raw && typeof raw === "object") Object.keys(raw).forEach(function (nodeId) { if (raw[nodeId]) result[String(nodeId)] = true; });
    return result;
  }
  function fileInputMediaKind(item) {
    var classType = String(item && item.class_type || "").toLowerCase();
    var field = String(item && item.field || "").toLowerCase();
    if (classType.indexOf("loadaudio") !== -1 || classType.indexOf("vhs_loadaudio") !== -1 || field === "audio") return "audio";
    if (classType.indexOf("loadvideo") !== -1 || classType.indexOf("vhs_loadvideo") !== -1 || field === "video") return "video";
    if (classType.indexOf("loadimage") !== -1 || field === "image" || field === "mask") return "image";
    return "";
  }
  function taskFileTargets(draft, mediaKind) {
    if (!draft || !draft.workflow) return [];
    var workflow = draft.workflow.data || {};
    var values = draft.workflow.values && typeof draft.workflow.values === "object" ? draft.workflow.values : {};
    var files = values.files && typeof values.files === "object" ? values.files : {};
    var bypassed = bypassedNodeMap(values);
    var descriptors = draft.workflow.analysis && Array.isArray(draft.workflow.analysis.file_inputs) ? draft.workflow.analysis.file_inputs : [];
    if (!descriptors.length) {
      var fileFields = { image: true, mask: true, audio: true, video: true, file: true, filepath: true, file_path: true, image_path: true, audio_path: true, video_path: true, input_file: true, source_file: true };
      var fileClassHints = ["loadimage", "loadaudio", "loadvideo", "loadfile", "load3d", "vhs_load"];
      Object.keys(workflow).forEach(function (nodeId) {
        if (nodeId === "__rh_meta__") return;
        var node = workflow[nodeId];
        if (!node || typeof node !== "object" || !node.inputs || typeof node.inputs !== "object") return;
        var classType = String(node.class_type || "");
        var lowerClass = classType.toLowerCase();
        if (!fileClassHints.some(function (hint) { return lowerClass.indexOf(hint) !== -1; })) return;
        Object.keys(node.inputs).forEach(function (field) {
          if (!fileFields[String(field).toLowerCase()]) return;
          descriptors.push({ id: nodeId + ":" + field, node_id: nodeId, field: field, title: node._meta && node._meta.title || classType, class_type: classType, default: node.inputs[field] });
        });
      });
    }
    return descriptors.map(function (item) {
      var inputId = String(item.id || ((item.node_id || "") + ":" + (item.field || ""))).trim();
      var parts = inputId.split(":");
      var nodeId = String(item.node_id || parts.shift() || "");
      var field = String(item.field || parts.join(":") || "");
      var node = workflow[nodeId];
      var current = Object.prototype.hasOwnProperty.call(files, inputId) ? files[inputId] : (node && node.inputs ? node.inputs[field] : item.default);
      return {
        inputId: inputId,
        nodeId: nodeId,
        field: field,
        title: String(item.title || (node && node._meta && node._meta.title) || (node && node.class_type) || "文件输入"),
        classType: String(item.class_type || (node && node.class_type) || ""),
        expectedKind: fileInputMediaKind({ class_type: item.class_type || (node && node.class_type) || "", field: field }),
        current: String(current == null ? "" : current),
        bypassed: Boolean(bypassed[nodeId])
      };
    }).filter(function (item) {
      return item.inputId && item.nodeId && item.field && (!mediaKind || !item.expectedKind || item.expectedKind === mediaKind);
    });
  }
  function renderOutputImportTargets(draft) {
    var description = $("outputImportDescription");
    var status = $("outputImportStatus");
    var list = $("outputImportTargets");
    var confirm = $("confirmOutputImport");
    var item = outputImport.item;
    status.textContent = item ? "产物：" + String(item.name || "本地产物") + " · 路径：" + outputImport.path : "";
    if (!draft) {
      description.textContent = "没有检测到任务提交页的当前工作流。请先在任务提交页打开一个工作流。";
      list.innerHTML = '<div class="output-import-empty"><strong>先打开一个 API 工作流</strong><span>打开后，这里会列出工作流中的文件输入节点。</span></div>';
      confirm.disabled = true;
      return;
    }
    description.textContent = "选择目标文件输入节点，产物会作为该节点的本机输入路径保存。";
    var mediaKind = String(item && item.display_type || "").toLowerCase();
    if (["image", "video", "audio"].indexOf(mediaKind) === -1) {
      list.innerHTML = '<div class="output-import-empty"><strong>这个产物不是可导入的媒体</strong><span>这里只支持图片、视频或音频产物。</span></div>';
      confirm.disabled = true;
      return;
    }
    var targets = taskFileTargets(draft, mediaKind);
    if (!targets.length) {
      list.innerHTML = '<div class="output-import-empty"><strong>没有找到匹配的文件输入节点</strong><span>当前产物是' + (mediaKind === "video" ? "视频" : (mediaKind === "audio" ? "音频" : "图片")) + '，请确认工作流包含对应的输入节点。</span></div>';
      confirm.disabled = true;
      return;
    }
    var available = targets.filter(function (target) { return !target.bypassed; });
    var selected = available[0] || null;
    list.innerHTML = targets.map(function (target) {
      var disabled = target.bypassed;
      var checked = selected && selected.inputId === target.inputId;
      return '<label class="output-import-target' + (disabled ? ' is-disabled' : '') + '">' +
        '<input type="radio" name="output-import-target" value="' + esc(target.inputId) + '"' + (checked ? ' checked' : '') + (disabled ? ' disabled' : '') + ' />' +
        '<span class="output-import-target-copy"><strong>' + esc(target.title) + '</strong><span><code>' + esc(target.inputId) + '</code> · ' + esc(target.classType) + '</span><small>' + (disabled ? '已旁路，本次提交不会使用' : '当前：' + esc(filenameFromPath(target.current))) + '</small></span>' +
        '</label>';
    }).join("");
    confirm.disabled = !selected;
  }
  function openOutputImport(item, button) {
    if (!item || item.kind !== "file") return showToast("只有本地文件产物可以导入媒体", true);
    if (["image", "video", "audio"].indexOf(String(item.display_type || "").toLowerCase()) === -1) return showToast("只有图片、视频或音频产物可以导入媒体", true);
    var original = button ? button.textContent : "导入媒体";
    if (button) {
      button.disabled = true;
      button.textContent = "读取中…";
    }
    request("/api/tasks/" + encodeURIComponent(item.task_id)).then(function (task) {
      var outputs = (task.outputs || []).filter(function (output) { return output && output.kind === "file"; });
      var output = outputs[Number(item.file_index)];
      var path = String(output && output.path || "").trim();
      if (!/^(\/|[A-Za-z]:[\\/])/.test(path)) throw new Error("找不到产物的本机路径");
      outputImport.item = item;
      outputImport.path = path;
      renderOutputImportTargets(readTaskDraft());
      window.RHMotion.openModal("outputImportModal", "closeOutputImport");
    }).catch(function (error) {
      showToast("打开导入窗口失败：" + error.message, true);
    }).finally(function () {
      if (button) {
        button.disabled = false;
        button.textContent = original;
      }
    });
  }
  function closeOutputImport() {
    outputImport.item = null;
    outputImport.path = "";
    window.RHMotion.closeModal("outputImportModal");
  }
  function projectFolderRecord(projectId) {
    var id = String(projectId || "").trim();
    return state.projects.find(function (project) { return String(project && project.id || "") === id; }) || null;
  }
  function mergeProjectFolder(project) {
    if (!project || !String(project.id || "").trim()) return;
    var id = String(project.id);
    var index = state.projects.findIndex(function (item) { return String(item && item.id || "") === id; });
    if (index >= 0) state.projects[index] = project;
    else state.projects.push(project);
  }
  function outputProjectEditorIsOpen() {
    var modal = $("outputProjectEditorModal");
    return Boolean(modal && !modal.hidden && modal.classList.contains("is-open"));
  }
  function openOutputProjectEditor(mode, project) {
    projectEditor.mode = mode === "rename" ? "rename" : "create";
    projectEditor.projectId = projectEditor.mode === "rename" && project ? String(project.id || "") : "";
    var input = $("outputProjectName");
    var title = $("outputProjectEditorTitle");
    var description = $("outputProjectEditorDescription");
    var confirm = $("confirmOutputProjectEditor");
    if (!input || !title || !description || !confirm) return;
    title.textContent = projectEditor.mode === "rename" ? "重命名项目" : "新建项目";
    description.textContent = projectEditor.mode === "rename"
      ? "只修改成片页中的项目名称，不会重命名磁盘目录或移动媒体文件。"
      : "新项目是成片页的归类文件夹；创建后可从成片右键菜单将任务移入。不会创建或复制媒体文件。";
    confirm.textContent = projectEditor.mode === "rename" ? "保存名称" : "新建项目";
    input.value = projectEditor.mode === "rename" && project ? String(project.name || "") : "";
    input.disabled = false;
    window.RHMotion.openModal("outputProjectEditorModal", "closeOutputProjectEditor");
    window.requestAnimationFrame(function () {
      input.focus();
      if (projectEditor.mode === "rename") input.select();
    });
  }
  function closeOutputProjectEditor() {
    projectEditor.mode = "";
    projectEditor.projectId = "";
    window.RHMotion.closeModal("outputProjectEditorModal");
  }
  function submitOutputProjectEditor() {
    var input = $("outputProjectName");
    var button = $("confirmOutputProjectEditor");
    if (!input || !button || button.disabled) return;
    var name = String(input.value || "").trim();
    if (!name) {
      input.focus();
      showToast("请填写项目名称", true);
      return;
    }
    var rename = projectEditor.mode === "rename";
    var projectId = projectEditor.projectId;
    if (rename && !projectFolderRecord(projectId)) {
      closeOutputProjectEditor();
      return showToast("项目已不存在，请刷新页面", true);
    }
    button.disabled = true;
    input.disabled = true;
    button.textContent = rename ? "保存中…" : "创建中…";
    var path = rename ? "/api/projects/" + encodeURIComponent(projectId) : "/api/projects";
    request(path, {
      method: rename ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name })
    }).then(function (data) {
      var project = data && data.project;
      if (!project) throw new Error(rename ? "项目重命名失败" : "项目创建失败");
      mergeProjectFolder(project);
      if (rename) {
        state.outputs.forEach(function (item) {
          if (outputProjectId(item) !== String(project.id || "")) return;
          item.project_name = String(project.name || "");
          item.project_path = String(project.path || "");
        });
      }
      closeOutputProjectEditor();
      render();
      showToast(rename ? "项目已重命名" : "项目已新建，可从成片右键菜单将任务移入");
    }).catch(function (error) {
      showToast((rename ? "重命名项目失败：" : "新建项目失败：") + error.message, true);
      input.disabled = false;
      input.focus();
      if (rename) input.select();
    }).finally(function () {
      button.disabled = false;
      button.textContent = rename ? "保存名称" : "新建项目";
    });
  }
  function outputProjectDeleteIsOpen() {
    var modal = $("outputProjectDeleteModal");
    return Boolean(modal && !modal.hidden && modal.classList.contains("is-open"));
  }
  function openOutputProjectDelete(project) {
    if (!project) return;
    projectDelete.projectId = String(project.id || "");
    var description = $("outputProjectDeleteDescription");
    var record = outputProjectRecord(projectDelete.projectId) || project;
    if (description) {
      description.textContent = "删除项目「" + String(project.name || "") + "」后，其中 " + Number(record.tasks || 0) + " 个任务会归入未归类。不会删除任务、外部项目目录或任何成片文件。";
    }
    window.RHMotion.openModal("outputProjectDeleteModal", "closeOutputProjectDelete");
  }
  function closeOutputProjectDelete() {
    projectDelete.projectId = "";
    window.RHMotion.closeModal("outputProjectDeleteModal");
  }
  function confirmOutputProjectDelete() {
    var projectId = projectDelete.projectId;
    var project = projectFolderRecord(projectId);
    var button = $("confirmOutputProjectDelete");
    if (!projectId || !project || !button || button.disabled) return;
    button.disabled = true;
    button.textContent = "删除中…";
    request("/api/projects/" + encodeURIComponent(projectId), { method: "DELETE" }).then(function (data) {
      state.projects = state.projects.filter(function (item) { return String(item && item.id || "") !== projectId; });
      state.outputs.forEach(function (item) {
        if (outputProjectId(item) !== projectId) return;
        item.project_id = "";
        item.project_name = "";
        item.project_path = "";
      });
      if (state.projectId === projectId) {
        state.projectId = "";
        state.workflowFilter = "";
      }
      closeOutputProjectDelete();
      resetOutputPage();
      render();
      showToast("项目已删除，" + Number(data && data.affected_task_count || 0) + " 个任务已归入未归类");
    }).catch(function (error) {
      showToast("删除项目失败：" + error.message, true);
    }).finally(function () {
      button.disabled = false;
      button.textContent = "删除项目";
    });
  }
  function closeOutputProjectContextMenu() {
    var menu = $("outputProjectContextMenu");
    if (menu) menu.hidden = true;
    state.contextProjectId = "";
  }
  function openOutputProjectContextMenu(event, projectId) {
    var project = projectFolderRecord(projectId);
    var menu = $("outputProjectContextMenu");
    if (!project || !menu) return;
    event.preventDefault();
    closeArtifactContextMenu();
    closeOutputProjectContextMenu();
    state.contextProjectId = String(project.id || "");
    menu.hidden = false;
    positionArtifactContextMenu(menu, event);
    var firstAction = menu.querySelector("button");
    if (firstAction) firstAction.focus();
  }
  function handleOutputProjectContextMenu(event) {
    var target = event.target.closest("[data-output-project-context]");
    if (!target || !$("outputProjectGrid").contains(target)) return;
    openOutputProjectContextMenu(event, target.dataset.outputProjectContext);
  }
  function handleOutputProjectContextAction(event) {
    var button = event.target.closest("[data-output-project-menu-action]");
    if (!button) return;
    var project = projectFolderRecord(state.contextProjectId);
    var action = button.dataset.outputProjectMenuAction;
    closeOutputProjectContextMenu();
    if (!project) return;
    if (action === "rename") openOutputProjectEditor("rename", project);
    if (action === "delete") openOutputProjectDelete(project);
  }
  function hasOutputTaskDrag(event) {
    var types = event.dataTransfer && event.dataTransfer.types;
    return Boolean(types && Array.prototype.indexOf.call(types, "application/x-rh-output-task") !== -1);
  }
  function outputProjectDropTargetFromEvent(event) {
    var target = event.target.closest("[data-output-project-drop]");
    return target && $("outputProjectGrid").contains(target) ? target : null;
  }
  function clearOutputProjectDropState() {
    $("outputProjectGrid").querySelectorAll(".is-output-project-drop-target").forEach(function (element) {
      element.classList.remove("is-output-project-drop-target");
    });
  }
  function outputTaskPayloadFromDrop(event) {
    if (!event.dataTransfer) return null;
    var raw = "";
    try { raw = event.dataTransfer.getData("application/x-rh-output-task") || ""; } catch (error) {}
    if (!raw) return null;
    try {
      var payload = JSON.parse(raw);
      return payload && String(payload.task_id || "").trim() ? payload : null;
    } catch (error) {
      return null;
    }
  }
  function updateOutputProjectTaskCount(projectId, delta) {
    var normalizedProjectId = String(projectId || "").trim();
    if (!normalizedProjectId || normalizedProjectId === UNCLASSIFIED_PROJECT_ID) return;
    state.projects.forEach(function (project) {
      if (String(project && project.id || "").trim() !== normalizedProjectId) return;
      var count = Number(project.task_count || 0);
      project.task_count = Math.max(0, (Number.isFinite(count) ? count : 0) + delta);
    });
  }
  function updateOutputTaskProjectState(taskId, updated, previousProjectId) {
    var nextProjectId = String(updated && updated.project_id || "").trim();
    state.outputs.forEach(function (output) {
      if (String(output.task_id || "") !== String(taskId || "")) return;
      output.project_id = nextProjectId;
      output.project_name = String(updated.project_name || "");
      output.project_path = String(updated.project_path || "");
    });
    var oldProjectId = String(previousProjectId || "").trim();
    if (oldProjectId !== nextProjectId) {
      updateOutputProjectTaskCount(oldProjectId, -1);
      updateOutputProjectTaskCount(nextProjectId, 1);
    }
  }
  function requestOutputTaskProject(item, targetId) {
    var normalizedTargetId = String(targetId || "").trim();
    var currentId = outputProjectId(item) || UNCLASSIFIED_PROJECT_ID;
    var target = normalizedTargetId === UNCLASSIFIED_PROJECT_ID ? null : outputProjectRecord(normalizedTargetId);
    if (normalizedTargetId !== UNCLASSIFIED_PROJECT_ID && !target) return Promise.reject(new Error("目标项目不存在，请刷新页面"));
    if (normalizedTargetId === currentId) return Promise.resolve({ same: true, project: target });
    var project = target ? { project_id: target.id, project_name: target.name, project_path: target.path } : {};
    return request("/api/tasks/" + encodeURIComponent(item.task_id) + "/project", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: project })
    }).then(function (data) {
      var updated = data.task || {};
      updateOutputTaskProjectState(item.task_id, updated, currentId);
      return { same: false, project: target, updated: updated };
    });
  }
  function handleOutputProjectDragOver(event) {
    if (!hasOutputTaskDrag(event)) return;
    var target = outputProjectDropTargetFromEvent(event);
    if (!target) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    clearOutputProjectDropState();
    target.classList.add("is-output-project-drop-target");
  }
  function handleOutputProjectDragLeave(event) {
    var target = outputProjectDropTargetFromEvent(event);
    if (!target || (event.relatedTarget && target.contains(event.relatedTarget))) return;
    target.classList.remove("is-output-project-drop-target");
  }
  function handleOutputProjectDrop(event) {
    if (!hasOutputTaskDrag(event)) return;
    var target = outputProjectDropTargetFromEvent(event);
    if (!target) return;
    event.preventDefault();
    var payload = outputTaskPayloadFromDrop(event);
    var targetId = String(target.dataset.outputProjectDrop || "").trim();
    clearOutputProjectDropState();
    if (!payload || !targetId) return;
    var item = state.outputs.find(function (output) {
      return String(output.id || "") === String(payload.artifact_id || "") && String(output.task_id || "") === String(payload.task_id || "");
    }) || state.outputs.find(function (output) { return String(output.task_id || "") === String(payload.task_id || ""); });
    if (!item) return showToast("这个成片已不在当前列表，请刷新页面", true);
    var currentId = outputProjectId(item) || UNCLASSIFIED_PROJECT_ID;
    if (targetId === currentId) return showToast("这个任务已经属于该项目");
    target.classList.add("is-output-project-drop-saving");
    requestOutputTaskProject(item, targetId).then(function (result) {
      resetOutputPage();
      render();
      showToast("任务已移动到「" + (result.project ? result.project.name : "未归类") + "」");
    }).catch(function (error) {
      showToast("保存项目归类失败：" + error.message, true);
    }).finally(function () {
      target.classList.remove("is-output-project-drop-saving");
    });
  }
  function outputProjectMoveIsOpen() {
    var modal = $("outputProjectMoveModal");
    return Boolean(modal && !modal.hidden && modal.classList.contains("is-open"));
  }
  function outputProjectMoveOptions() {
    return [{ id: UNCLASSIFIED_PROJECT_ID, name: "未归类", path: "", outputs: 0, tasks: 0 }].concat(outputProjectRecords().filter(function (record) { return record.id !== UNCLASSIFIED_PROJECT_ID; }));
  }
  function renderOutputProjectMoveOptions() {
    var list = $("outputProjectMoveOptions");
    var confirm = $("confirmOutputProjectMove");
    var item = projectMove.item;
    if (!list || !confirm || !item) return;
    var currentId = outputProjectId(item) || UNCLASSIFIED_PROJECT_ID;
    var selectedId = projectMove.projectId || currentId;
    list.innerHTML = outputProjectMoveOptions().map(function (record) {
      var checked = record.id === selectedId;
      var current = record.id === currentId;
      var detail = record.id === UNCLASSIFIED_PROJECT_ID ? "不属于任何项目文件夹" : record.tasks + " 个任务 · " + record.outputs + " 个成片";
      return '<label class="output-project-move-option' + (current ? ' is-current' : '') + '">' +
        '<input type="radio" name="output-project-target" value="' + esc(record.id) + '"' + (checked ? ' checked' : '') + ' />' +
        '<span class="output-project-move-copy"><strong>' + esc(record.name) + '</strong><small>' + esc(detail) + (current ? ' · 当前项目' : '') + '</small>' + (record.path ? '<span title="' + esc(record.path) + '">' + esc(record.path) + '</span>' : '') + '</span>' +
        '</label>';
    }).join("");
    confirm.disabled = selectedId === currentId;
  }
  function openOutputProjectMove(item) {
    if (!item || !String(item.task_id || "").trim()) return showToast("这个产物没有关联的本地任务", true);
    projectMove.item = item;
    var currentId = outputProjectId(item) || UNCLASSIFIED_PROJECT_ID;
    var firstOther = outputProjectMoveOptions().find(function (record) { return record.id !== currentId; });
    projectMove.projectId = firstOther ? firstOther.id : currentId;
    var description = $("outputProjectMoveDescription");
    if (description) description.textContent = "任务「" + String(item.task_name || item.task_id) + "」只会改变项目归类，不会移动或复制成片文件。";
    renderOutputProjectMoveOptions();
    window.RHMotion.openModal("outputProjectMoveModal", "closeOutputProjectMove");
  }
  function closeOutputProjectMove() {
    projectMove.item = null;
    projectMove.projectId = "";
    window.RHMotion.closeModal("outputProjectMoveModal");
  }
  function confirmOutputProjectMove() {
    var item = projectMove.item;
    var targetId = projectMove.projectId;
    if (!item || !targetId) return showToast("请选择一个项目", true);
    var currentId = outputProjectId(item) || UNCLASSIFIED_PROJECT_ID;
    if (targetId === currentId) return showToast("任务已经属于这个项目", true);
    var target = targetId === UNCLASSIFIED_PROJECT_ID ? null : outputProjectRecord(targetId);
    if (targetId !== UNCLASSIFIED_PROJECT_ID && !target) return showToast("目标项目不存在，请刷新页面", true);
    var button = $("confirmOutputProjectMove");
    button.disabled = true;
    button.textContent = "保存中…";
    return requestOutputTaskProject(item, targetId).then(function (result) {
      closeOutputProjectMove();
      rebuildSummary();
      refreshOutputCollection();
      showToast(result.project ? "任务已移动到「" + result.project.name + "」" : "任务已移出项目文件夹");
    }).catch(function (error) {
      showToast("保存项目归类失败：" + error.message, true);
    }).finally(function () {
      button.disabled = false;
      button.textContent = "移动任务";
    });
  }
  function confirmOutputImport() {
    var selected = $("outputImportTargets").querySelector('input[name="output-import-target"]:checked');
    if (!outputImport.path || !selected) return showToast("请先选择一个文件输入节点", true);
    var draft = readTaskDraft();
    var mediaKind = String(outputImport.item && outputImport.item.display_type || "").toLowerCase();
    var target = taskFileTargets(draft, mediaKind).find(function (item) { return item.inputId === selected.value && !item.bypassed; });
    if (!draft || !target) return showToast("任务提交页的工作流已发生变化，请重新打开导入窗口", true);
    var workflow = draft.workflow.data;
    var node = workflow[target.nodeId];
    if (!node || !node.inputs || typeof node.inputs !== "object") return showToast("找不到选中的文件输入节点", true);
    var button = $("confirmOutputImport");
    button.disabled = true;
    button.textContent = "导入中…";
    var itemName = String(outputImport.item && outputImport.item.name || "产物");
    try {
      node.inputs[target.field] = outputImport.path;
      draft.workflow.values = draft.workflow.values && typeof draft.workflow.values === "object" ? draft.workflow.values : {};
      draft.workflow.values.files = draft.workflow.values.files && typeof draft.workflow.values.files === "object" ? draft.workflow.values.files : {};
      draft.workflow.values.files[target.inputId] = outputImport.path;
      draft.workflow.savedAt = Date.now();
      window.localStorage.setItem(draftStorageKey, JSON.stringify(draft));
      var focusImport = notifySubmitImport({ kind: "media", source: "output", inputId: target.inputId });
      closeOutputImport();
      showToast(focusImport ? "已将「" + itemName + "」导入「" + target.title + "」，任务提交面板已同步" : "已将「" + itemName + "」导入「" + target.title + "」");
      if (!focusImport) window.location.href = "/";
    } catch (error) {
      showToast("保存导入结果失败：" + error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "导入媒体";
    }
  }
  function deleteArtifactTask(item, button) {
    var taskId = String(item && item.task_id || "").trim();
    if (!taskId || (button && button.disabled)) return;
    if (!window.confirm("删除这条本地任务记录及其 output 文件夹中的全部产物吗？此操作不可恢复。")) return;
    if (button) button.disabled = true;
    return request("/api/tasks/" + encodeURIComponent(taskId), { method: "DELETE" }).then(function () {
      state.outputs = state.outputs.filter(function (output) { return String(output.task_id || "") !== taskId; });
      rebuildSummary();
      refreshOutputCollection();
      if (outputPreviewIsOpen()) closeOutputPreview();
      showToast("任务记录已删除");
    }).catch(function (error) {
      if (button) button.disabled = false;
      showToast(error.message, true);
    });
  }
  function handleOutputKeyboardShortcut(event) {
    if (event.defaultPrevented || event.isComposing) return;
    if (outputPreviewIsOpen()) {
      if (event.key === "Escape" || event.key === "Enter") {
        event.preventDefault();
        closeOutputPreview();
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        seekPreviewMedia(event.key === "ArrowLeft" ? -previewSeekSeconds : previewSeekSeconds);
        return;
      }
      if (event.key === " ") {
        event.preventDefault();
        togglePreviewMedia();
        return;
      }
      if (event.key === "6") {
        var previewTagItem = artifactById(state.selectedArtifactId);
        if (!previewTagItem) return;
        event.preventDefault();
        toggleCaseTag(previewTagItem);
        return;
      }
      if (String(event.key || "").toLowerCase() === "h") {
        var previewHTagItem = artifactById(state.selectedArtifactId);
        if (!previewHTagItem) return;
        event.preventDefault();
        toggleHTag(previewHTagItem);
        return;
      }
      if (/^[0-5]$/.test(event.key)) {
        var previewItem = artifactById(state.selectedArtifactId);
        if (!previewItem) return;
        event.preventDefault();
        setOutputRating(previewItem, event.key);
        return;
      }
      return;
    }
    if (event.key === "Escape") {
      var hasOverlay = outputPreviewIsOpen() || Boolean($("outputImportModal") && !$("outputImportModal").hidden) || outputProjectMoveIsOpen() || outputProjectEditorIsOpen() || outputProjectDeleteIsOpen() || Boolean($("artifactContextMenu") && !$("artifactContextMenu").hidden) || Boolean($("outputProjectContextMenu") && !$("outputProjectContextMenu").hidden);
      closeOutputPreview();
      closeOutputImport();
      closeOutputProjectMove();
      closeOutputProjectEditor();
      closeOutputProjectDelete();
      closeArtifactContextMenu();
      closeOutputProjectContextMenu();
      if (hasOverlay) event.preventDefault();
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
    if (event.target.closest && event.target.closest("button, a, input, select, textarea, [contenteditable=\"true\"]")) return;
    var hasModal = Boolean(document.querySelector(".modal-backdrop.is-open:not([hidden])"));
    if (hasModal) return;
    var selected = artifactById(state.selectedArtifactId);
    if (!selected) return;
    if (event.key === " ") {
      if (selected.display_type === "video" && toggleSelectedVideo()) {
        event.preventDefault();
      }
      return;
    }
    if (event.key === "Delete" || event.key === "Backspace") {
      if (hasModal && !outputPreviewIsOpen()) return;
      event.preventDefault();
      deleteArtifactTask(selected);
      return;
    }
    if (event.key === "6") {
      if (hasModal || (event.target.closest && event.target.closest("video, audio"))) return;
      event.preventDefault();
      toggleCaseTag(selected);
      return;
    }
    if (String(event.key || "").toLowerCase() === "h") {
      if (hasModal || (event.target.closest && event.target.closest("video, audio"))) return;
      event.preventDefault();
      toggleHTag(selected);
      return;
    }
    if (!/^[0-5]$/.test(event.key) || hasModal || (event.target.closest && event.target.closest("video, audio"))) return;
    var ratingCard = selectedArtifactCard() || document.querySelector(".artifact-card:hover");
    var ratingItem = ratingCard && artifactById(ratingCard.dataset.artifactId);
    if (!ratingItem) return;
    event.preventDefault();
    setOutputRating(ratingItem, event.key);
  }
  function handleOutputArrowNavigation(event) {
    if (event.defaultPrevented || event.isComposing || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
    if (!(event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "ArrowUp" || event.key === "ArrowDown")) return;
    if (document.querySelector(".modal-backdrop:not([hidden])") || !outputKeyboardNavigationAllowed(event)) return;
    if (!navigateSelectedArtifact(event.key.slice(5).toLowerCase())) return;
    event.preventDefault();
    event.stopPropagation();
  }
  function artifactCardMarkup(item, index) {
    var cost = costLabel(item);
    var canCompare = item.display_type === "image" || item.display_type === "video";
    var canProject = Boolean(String(item.task_id || "").trim());
    var canDrag = canCompare || canProject;
    var dragDescription = canCompare && canProject ? "可拖拽到项目文件夹或内容对比" : (canProject ? "可拖拽到项目文件夹" : (canCompare ? "可拖拽到内容对比" : "产物卡片"));
    return '<article class="artifact-card' + (canCompare ? ' is-compare-draggable' : '') + (canProject ? ' is-project-draggable' : '') + '" data-task-id="' + esc(item.task_id) + '" data-artifact-id="' + esc(item.id) + '" data-compare-draggable="' + (canCompare ? 'true' : 'false') + '" data-project-draggable="' + (canProject ? 'true' : 'false') + '" draggable="' + (canDrag ? 'true' : 'false') + '" tabindex="0" role="button" aria-roledescription="' + dragDescription + '" aria-label="放大查看 ' + esc(item.name) + '" style="animation-delay:' + Math.min(index * 35, 350) + 'ms">' +
      artifactCardHeadMarkup(item) +
      mediaMarkup(item) +
      '<div class="artifact-body"><div class="artifact-name-row"><div class="artifact-name" title="' + esc(item.name) + '">' + esc(item.name) + '</div>' + ratingStarsMarkup(item) + '</div><div class="artifact-task" title="点击工作流名称加载到任务提交页"><span class="artifact-task-prefix">任务 ·</span>' + taskWorkflowLabel(item) + taskIdLabel(item) + '</div><div class="artifact-foot"><div class="artifact-foot-info"><span>' + formatTime(item.modified_at || item.task_completed_at || item.task_created_at) + '</span>' + artifactResolutionMarkup(item) + artifactDurationMarkup(item) + '</div>' + (cost ? '<span class="artifact-cost">' + esc(cost) + '</span>' : '') + '</div></div>' +
      '</article>';
  }
  function outputsEmptyMarkup() {
    return '<div class="outputs-empty"><strong>' + (state.outputs.length ? "没有符合筛选条件的产物" : "还没有可浏览的产物") + '</strong><span>' + (state.outputs.length ? "换一个类型或关键词试试。" : "完成任务并保存产物后，它们会自动出现在这里。") + "</span></div>";
  }
  function refreshFilteredArtifacts() {
    var grid = $("outputGrid");
    var items = filteredOutputs();
    var visibleItems = outputPageItems(items);
    var cards = Array.prototype.slice.call(grid.querySelectorAll(".artifact-card"));
    var selectedIndex = cards.findIndex(function (card) { return String(card.dataset.artifactId) === String(state.selectedArtifactId); });
    var selectedCard = cards[selectedIndex];
    var restoreFocus = selectedCard && selectedCard.contains(document.activeElement);
    var visibleIds = new Set(visibleItems.map(function (item) { return String(item.id); }));
    var retained = new Map();
    cards.forEach(function (card) {
      var id = String(card.dataset.artifactId);
      if (visibleIds.has(id)) retained.set(id, card);
      else card.remove();
    });
    if (!visibleItems.length) {
      grid.innerHTML = outputsEmptyMarkup();
    } else {
      var empty = grid.querySelector(".outputs-empty");
      if (empty) empty.remove();
      visibleItems.forEach(function (item, index) {
        var card = retained.get(String(item.id));
        if (!card) {
          var template = document.createElement("template");
          template.innerHTML = artifactCardMarkup(item, 0);
          window.RHMotion.bindVideoLoopControls(template.content);
          bindArtifactResolutionMetadata(template.content);
          card = template.content.firstElementChild;
        }
        // Keep existing media nodes mounted; only insert cards filling a vacant slot.
        if (grid.children[index] !== card) grid.insertBefore(card, grid.children[index] || null);
      });
    }
    if (!visibleIds.has(String(state.selectedArtifactId))) {
      var replacement = visibleItems[Math.min(Math.max(selectedIndex, 0), visibleItems.length - 1)];
      state.selectedArtifactId = replacement ? String(replacement.id) : "";
    }
    syncArtifactSelection(visibleItems);
    if (restoreFocus) {
      var focusedCard = selectedArtifactCard();
      if (focusedCard) focusedCard.focus({ preventScroll: true });
    }
    focusSelectedArtifact();
    renderPagination(items.length);
    saveOutputViewState();
  }
  function render() {
    renderWorkflowFilters();
    renderSummary();
    renderProjectBrowser();
    var items = filteredOutputs();
    if (!items.length) {
      state.selectedArtifactId = "";
      $("outputGrid").innerHTML = outputsEmptyMarkup();
      renderPagination(0);
      saveOutputViewState();
      return;
    }
    var visibleItems = outputPageItems(items);
    $("outputGrid").innerHTML = visibleItems.map(artifactCardMarkup).join("");
    syncArtifactSelection(visibleItems);
    window.RHMotion.bindVideoLoopControls($("outputGrid"));
    bindArtifactResolutionMetadata($("outputGrid"));
    renderPagination(items.length);
    focusSelectedArtifact();
    saveOutputViewState();
  }
  function refreshOutputCollection() {
    renderProjectBrowser();
    renderWorkflowFilters();
    renderSummary();
    refreshFilteredArtifacts();
    saveOutputViewState();
  }
  function loadOutputs(showMessage) {
    $("refreshOutputs").disabled = true;
    Promise.all([request("/api/outputs"), request("/api/state?scope=outputs")]).then(function (results) {
      var data = results[0];
      var settings = results[1] && results[1].settings && results[1].settings.telegram;
      state.outputs = Array.isArray(data.outputs) ? data.outputs : [];
      state.projects = Array.isArray(data.projects) ? data.projects : [];
      state.summary = data.summary || {};
      state.telegramConfigured = Boolean(settings && settings.configured);
      render();
      if (showMessage) showToast("产物列表已刷新");
    }).catch(function (error) {
      $("outputGrid").innerHTML = '<div class="outputs-empty"><strong>读取产物失败</strong><span>' + esc(error.message) + "</span></div>";
      showToast(error.message, true);
    }).finally(function () { $("refreshOutputs").disabled = false; });
  }
  function deleteOneStarOutputs() {
    var projectId = state.projectId;
    var targets = oneStarOutputs().slice();
    var count = targets.length;
    if (!count) return;
    if (!window.confirm("将删除" + outputActionScopeLabel() + " " + count + " 个一星成片，仅删除成片文件和产物记录，不删除任务记录。此操作不可恢复。")) return;
    var button = $("deleteOneStarOutputs");
    button.disabled = true;
    button.classList.add("is-busy");
    var originalLabel = button.innerHTML;
    button.textContent = "删除中…";
    var targetKeys = {};
    targets.forEach(function (item) { targetKeys[outputActionKey(item)] = true; });
    return request("/api/outputs/rating/1" + outputActionQuery(projectId), { method: "DELETE" }).then(function (data) {
      var deleted = Number(data.deleted || 0);
      state.outputs = state.outputs.filter(function (item) { return !targetKeys[outputActionKey(item)]; });
      rebuildSummary();
      refreshOutputCollection();
      showToast("已删除 " + deleted + " 个一星成片");
    }).catch(function (error) {
      showToast("删除一星成片失败：" + error.message, true);
    }).finally(function () {
      button.innerHTML = originalLabel;
      button.classList.remove("is-busy");
      renderSummary();
    });
  }
  function exportCaseOutputs() {
    var count = caseMediaOutputs().length;
    if (!count) return;
    var button = $("exportCaseOutputs");
    button.disabled = true;
    button.classList.add("is-busy");
    button.setAttribute("aria-busy", "true");
    showToast("正在准备 " + count + " 个案例媒体的 ZIP…");
    window.location.href = "/api/outputs/export/case" + outputActionQuery();
    window.setTimeout(function () {
      button.classList.remove("is-busy");
      button.removeAttribute("aria-busy");
      renderSummary();
    }, 1500);
  }
  function bindEvents() {
    var themeButton = $("themeToggle");
    if (themeButton) themeButton.addEventListener("click", function () {
      var nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem("rh-workflow-theme", nextTheme); } catch (error) {}
      var light = nextTheme === "light";
      $("themeToggleIcon").textContent = light ? "☾" : "☀";
      $("themeToggleLabel").textContent = light ? "夜间" : "日间";
      themeButton.setAttribute("aria-label", light ? "切换到夜间模式" : "切换到日间模式");
      themeButton.title = light ? "切换到夜间模式" : "切换到日间模式";
    });
    $("refreshOutputs").addEventListener("click", function () { loadOutputs(true); });
    function chooseOutputProject(event) {
      var button = event.target.closest("[data-output-project]");
      if (!button) return;
      event.preventDefault();
      state.projectId = button.dataset.outputProject || "";
      state.workflowFilter = "";
      resetOutputPage();
      closeArtifactContextMenu();
      closeOutputProjectContextMenu();
      render();
    }
    $("outputProjectGrid").addEventListener("click", chooseOutputProject);
    $("outputProjectBreadcrumb").addEventListener("click", chooseOutputProject);
    $("outputProjectGrid").addEventListener("contextmenu", handleOutputProjectContextMenu);
    $("outputProjectGrid").addEventListener("dragover", handleOutputProjectDragOver);
    $("outputProjectGrid").addEventListener("dragleave", handleOutputProjectDragLeave);
    $("outputProjectGrid").addEventListener("drop", handleOutputProjectDrop);
    $("outputProjectContextMenu").addEventListener("click", handleOutputProjectContextAction);
    $("createOutputProject").addEventListener("click", function () { openOutputProjectEditor("create"); });
    $("showAllOutputProjects").addEventListener("click", function () {
      state.projectId = "";
      state.workflowFilter = "";
      resetOutputPage();
      closeOutputProjectContextMenu();
      render();
    });
    $("exportCaseOutputs").addEventListener("click", exportCaseOutputs);
    $("deleteOneStarOutputs").addEventListener("click", deleteOneStarOutputs);
    $("outputGrid").addEventListener("pointerdown", function (event) {
      if (event.button !== 0 && event.button !== 2) return;
      var card = event.target.closest(".artifact-card");
      if (!card) return;
      selectArtifactCard(artifactById(card.dataset.artifactId), false);
    });
    $("outputGrid").addEventListener("focusin", function (event) {
      var card = event.target.closest(".artifact-card");
      if (card) selectArtifactCard(artifactById(card.dataset.artifactId), false);
    });
    $("outputGrid").addEventListener("dragstart", function (event) {
      var card = event.target.closest('.artifact-card[draggable="true"]');
      if (!card || !event.dataTransfer) return;
      var item = state.outputs.find(function (output) { return String(output.id) === String(card.dataset.artifactId); });
      if (!item) return;
      var canCompare = card.dataset.compareDraggable === "true";
      var canProject = card.dataset.projectDraggable === "true";
      event.dataTransfer.effectAllowed = "copyMove";
      if (canProject) {
        event.dataTransfer.setData("application/x-rh-output-task", JSON.stringify({
          task_id: String(item.task_id || ""),
          artifact_id: String(item.id || ""),
          project_id: outputProjectId(item),
          source: "output"
        }));
      }
      if (canCompare) {
        var comparePayload = JSON.stringify({
          id: String(item.id),
          name: String(item.name || ""),
          display_type: String(item.display_type || ""),
          mime: String(item.mime || ""),
          url: outputUrl(item),
          task_name: String(item.task_name || ""),
          source: "output"
        });
        event.dataTransfer.setData("application/x-rh-compare-asset", comparePayload);
        event.dataTransfer.setData("text/plain", comparePayload);
      } else if (canProject) {
        event.dataTransfer.setData("text/plain", String(item.task_id || ""));
      }
      card.classList.add("is-dragging");
    });
    $("outputGrid").addEventListener("dragend", function (event) {
      var card = event.target.closest(".artifact-card");
      if (card) card.classList.remove("is-dragging");
      clearOutputProjectDropState();
    });
    $("outputGrid").addEventListener("click", function (event) {
      var copyTaskButton = event.target.closest("[data-copy-task-id]");
      if (copyTaskButton) {
        event.preventDefault();
        event.stopPropagation();
        copyTaskId(copyTaskButton.dataset.copyTaskId, copyTaskButton);
        return;
      }
      var ratingButton = event.target.closest("[data-rate-output]");
      if (ratingButton) {
        var ratedItem = state.outputs.find(function (output) { return String(output.id) === String(ratingButton.dataset.rateOutput); });
        setOutputRating(ratedItem, ratingButton.dataset.rating);
        return;
      }
      var loadTaskButton = event.target.closest("[data-load-task-workflow]");
      if (loadTaskButton) {
        if (!loadTaskButton.disabled) loadTaskWorkflowToSubmit(loadTaskButton.dataset.loadTaskWorkflow, loadTaskButton);
        return;
      }
      if (event.target.closest("button, a, audio, video, input, select, textarea")) return;
      var card = event.target.closest(".artifact-card");
      if (!card) return;
      var item = state.outputs.find(function (output) { return String(output.id) === String(card.dataset.artifactId); });
      selectArtifactCard(item, false);
      openOutputPreview(item);
    });
    $("outputGrid").addEventListener("contextmenu", handleArtifactContextMenu);
    $("artifactContextMenu").addEventListener("click", handleArtifactMenuAction);
    $("outputGrid").addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      var card = event.target.closest(".artifact-card");
      if (!card || event.target.closest("button, a, audio, video, input, select, textarea")) return;
      event.preventDefault();
      event.stopPropagation();
      var item = state.outputs.find(function (output) { return String(output.id) === String(card.dataset.artifactId); });
      selectArtifactCard(item, false);
      openOutputPreview(item);
    });
    $("outputSearch").addEventListener("input", function () {
      state.search = this.value;
      if (state.contextWorkflowName) state.contextWorkflowName = state.search.trim();
      resetOutputPage();
      render();
    });
    $("outputSort").addEventListener("change", function () { state.sort = this.value; resetOutputPage(); render(); });
    $("outputFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-output-type]");
      if (!button) return;
      state.type = button.dataset.outputType;
      resetOutputPage();
      render();
    });
    $("outputRatingFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-output-rating]");
      if (!button) return;
      state.rating = button.dataset.outputRating === "unrated" ? "unrated" : normalizedRating(button.dataset.outputRating);
      resetOutputPage();
      render();
    });
    $("outputTagFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-output-tag]");
      if (!button) return;
      var tag = button.dataset.outputTag || "";
      var mode = outputTagFilterMode(tag);
      var currentIndex = OUTPUT_TAG_FILTER_MODES.indexOf(mode);
      if (OUTPUT_TAG_FILTER_TAGS.indexOf(tag) === -1 || currentIndex === -1) return;
      state.tagFilters[tag] = OUTPUT_TAG_FILTER_MODES[(currentIndex + 1) % OUTPUT_TAG_FILTER_MODES.length];
      resetOutputPage();
      render();
    });
    $("outputWorkflowFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-output-workflow]");
      if (!button) return;
      state.workflowFilter = button.dataset.outputWorkflow || "";
      resetOutputPage();
      render();
    });
    $("outputPagination").addEventListener("click", function (event) {
      var button = event.target.closest("[data-output-page]");
      if (!button || button.disabled) return;
      var nextPage = button.dataset.outputPage;
      if (nextPage === "previous") state.page -= 1;
      else if (nextPage === "next") state.page += 1;
      else state.page = Number(nextPage);
      closeArtifactContextMenu();
      render();
    });
    $("closeOutputPreview").addEventListener("click", closeOutputPreview);
    $("outputPreviewModal").addEventListener("click", function (event) {
      var ratingButton = event.target.closest("[data-rate-output]");
      if (ratingButton) {
        setOutputRating(artifactById(ratingButton.dataset.rateOutput), ratingButton.dataset.rating);
        return;
      }
      if (event.target === $("outputPreviewModal")) closeOutputPreview();
    });
    $("closeOutputImport").addEventListener("click", closeOutputImport);
    $("cancelOutputImport").addEventListener("click", closeOutputImport);
    $("confirmOutputImport").addEventListener("click", confirmOutputImport);
    $("outputImportTargets").addEventListener("change", function (event) {
      if (event.target.name === "output-import-target") $("confirmOutputImport").disabled = false;
    });
    $("outputImportModal").addEventListener("click", function (event) { if (event.target === $("outputImportModal")) closeOutputImport(); });
    $("closeOutputProjectMove").addEventListener("click", closeOutputProjectMove);
    $("cancelOutputProjectMove").addEventListener("click", closeOutputProjectMove);
    $("confirmOutputProjectMove").addEventListener("click", confirmOutputProjectMove);
    $("outputProjectMoveOptions").addEventListener("change", function (event) {
      if (event.target.name === "output-project-target") {
        projectMove.projectId = event.target.value;
        renderOutputProjectMoveOptions();
      }
    });
    $("outputProjectMoveModal").addEventListener("click", function (event) { if (event.target === $("outputProjectMoveModal")) closeOutputProjectMove(); });
    $("closeOutputProjectEditor").addEventListener("click", closeOutputProjectEditor);
    $("cancelOutputProjectEditor").addEventListener("click", closeOutputProjectEditor);
    $("confirmOutputProjectEditor").addEventListener("click", submitOutputProjectEditor);
    $("outputProjectName").addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      event.preventDefault();
      submitOutputProjectEditor();
    });
    $("outputProjectEditorModal").addEventListener("click", function (event) { if (event.target === $("outputProjectEditorModal")) closeOutputProjectEditor(); });
    $("closeOutputProjectDelete").addEventListener("click", closeOutputProjectDelete);
    $("cancelOutputProjectDelete").addEventListener("click", closeOutputProjectDelete);
    $("confirmOutputProjectDelete").addEventListener("click", confirmOutputProjectDelete);
    $("outputProjectDeleteModal").addEventListener("click", function (event) { if (event.target === $("outputProjectDeleteModal")) closeOutputProjectDelete(); });
    document.addEventListener("keydown", handleOutputArrowNavigation, true);
    document.addEventListener("keydown", handleOutputKeyboardShortcut);
    document.addEventListener("click", restoreArtifactFocusAfterClick, true);
    document.addEventListener("click", function (event) {
      if (!event.target.closest("#artifactContextMenu")) closeArtifactContextMenu();
      if (!event.target.closest("#outputProjectContextMenu")) closeOutputProjectContextMenu();
    });
    window.addEventListener("resize", updateFilterSlider);
    window.addEventListener("pagehide", saveOutputViewState);
  }
  if (!outputUrlHasContext()) restoreOutputViewState();
  readOutputContext();
  outputStatePersistenceReady = true;
  bindEvents();
  loadOutputs(false);
}());
