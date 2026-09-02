(function () {
  "use strict";

  var STORAGE_KEY = "rh-workflow-desk-prompt-builder-v1";
  var TASK_PROMPT_IMPORT_KEY = "rh-workflow-desk-pending-prompt-v1";
  var idCounter = 0;
  var promptApiReady = false;
  var stateSaveTimer = 0;
  var editingBlockId = "";
  var editingStageIndex = null;
  var depthImportActionId = "";
  var workflowImportAsset = null;
  var GRID_SPLITTER_STORAGE_KEY = "rh-workflow-desk-prompt-library-width-v2";
  var gridSplitterDrag = null;
  var REFERENCE_MODES = ["character", "audio", "background", "clothes"];
  var state = { libraryBlocks: [], actions: [], actionSource: null, references: [], referenceSource: null, libraryMode: "blocks", assemblyView: "stage", stage: [], groups: [], activeGroupId: "", filter: "全部", search: "", draggedIndex: null, draggedLibraryId: "", dragPreviewIndex: null, dragPreviewFrames: [], pointerDrag: null };
  var toastTimer = 0;

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }
  function unique(values) {
    var seen = {};
    return values.filter(function (value) {
      var key = String(value || "").trim();
      if (!key || seen[key]) return false;
      seen[key] = true;
      return true;
    });
  }
  function makeId(prefix) {
    idCounter += 1;
    return prefix + "-" + Date.now().toString(36) + "-" + idCounter;
  }
  function allBlocks() { return state.libraryBlocks; }
  function isReferenceMode() { return REFERENCE_MODES.indexOf(state.libraryMode) !== -1; }
  function currentLibraryEntries() {
    if (state.libraryMode === "pose" || state.libraryMode === "actions") return state.actions;
    if (isReferenceMode()) return state.references.filter(function (item) { return item.kind === state.libraryMode; });
    return allBlocks();
  }
  function getPromptText() {
    return state.stage.map(function (item) { return String(item.text || "").trim(); }).filter(Boolean).join("\n\n");
  }
  function showToast(message, isError) {
    var toast = $("promptToast");
    toast.textContent = message;
    toast.className = "toast show" + (isError ? " error" : "");
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { toast.className = "toast"; }, 3200);
  }
  function jsonRequest(path, method, body) {
    var options = { method: method || "GET", headers: { "Accept": "application/json" } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(path, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.message || "请求失败");
        return data;
      });
    });
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
  function promptGridWidthBounds() {
    var splitter = $("promptGridSplitter");
    var grid = splitter && splitter.parentElement;
    if (!grid) return null;
    var gridWidth = grid.getBoundingClientRect().width;
    var min = 300;
    var max = Math.max(min, Math.min(gridWidth - 620, gridWidth * .6));
    return { grid: grid, min: min, max: max };
  }
  function setPromptLibraryWidth(value, persist) {
    var bounds = promptGridWidthBounds();
    if (!bounds) return;
    var numericValue = Number(value);
    if (!Number.isFinite(numericValue)) numericValue = 420;
    var width = Math.max(bounds.min, Math.min(bounds.max, numericValue));
    bounds.grid.style.setProperty("--prompt-library-width", width + "px");
    var splitter = $("promptGridSplitter");
    if (splitter) {
      splitter.setAttribute("aria-valuenow", String(Math.round(width)));
      splitter.setAttribute("aria-valuemax", String(Math.round(bounds.max)));
    }
    if (persist) {
      try { localStorage.setItem(GRID_SPLITTER_STORAGE_KEY, String(Math.round(width))); } catch (error) {}
    }
  }
  function initPromptGridSplitter() {
    var splitter = $("promptGridSplitter");
    if (!splitter) return;
    var savedWidth = 0;
    try { savedWidth = Number(localStorage.getItem(GRID_SPLITTER_STORAGE_KEY)); } catch (error) {}
    setPromptLibraryWidth(savedWidth || 420, false);
    splitter.addEventListener("pointerdown", function (event) {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      var bounds = promptGridWidthBounds();
      if (!bounds) return;
      gridSplitterDrag = { pointerId: event.pointerId, grid: bounds.grid, gap: parseFloat(window.getComputedStyle(bounds.grid).columnGap) || 16 };
      document.body.classList.add("prompt-grid-resizing");
      if (splitter.setPointerCapture) splitter.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    splitter.addEventListener("keydown", function (event) {
      var current = Number(splitter.getAttribute("aria-valuenow")) || 420;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        setPromptLibraryWidth(current + (event.key === "ArrowLeft" ? -24 : 24), true);
      } else if (event.key === "Home") {
        event.preventDefault();
        var bounds = promptGridWidthBounds();
        if (bounds) setPromptLibraryWidth(bounds.min, true);
      } else if (event.key === "End") {
        event.preventDefault();
        var endBounds = promptGridWidthBounds();
        if (endBounds) setPromptLibraryWidth(endBounds.max, true);
      }
    });
    document.addEventListener("pointermove", function (event) {
      if (!gridSplitterDrag || gridSplitterDrag.pointerId !== event.pointerId) return;
      var rect = gridSplitterDrag.grid.getBoundingClientRect();
      setPromptLibraryWidth(event.clientX - rect.left - gridSplitterDrag.gap, false);
      event.preventDefault();
    });
    function finishGridSplitterDrag(event) {
      if (!gridSplitterDrag || gridSplitterDrag.pointerId !== event.pointerId) return;
      setPromptLibraryWidth(Number(splitter.getAttribute("aria-valuenow")) || 420, true);
      gridSplitterDrag = null;
      document.body.classList.remove("prompt-grid-resizing");
    }
    document.addEventListener("pointerup", finishGridSplitterDrag);
    document.addEventListener("pointercancel", finishGridSplitterDrag);
    window.addEventListener("resize", function () {
      if (!window.matchMedia("(max-width: 820px)").matches) {
        setPromptLibraryWidth(Number(splitter.getAttribute("aria-valuenow")) || 420, false);
      }
    });
  }
  function stageItemToApi(item) {
    var result = { instance_id: item.instanceId, kind: item.kind };
    if (item.kind === "text") {
      result.text = String(item.text || "");
    } else if (item.kind === "action") {
      result.action_id = item.sourceId || "";
      result.snapshot = {
        title: item.title || "",
        text: item.text || "",
        tags: item.tags || [],
        color_image_url: item.colorImageUrl || item.imageUrl || "",
        depth_image_url: item.depthImageUrl || "",
        pair_status: item.pairStatus || "",
      };
    } else if (item.kind === "reference") {
      result.reference_id = item.sourceId || "";
      result.reference_kind = item.referenceKind || "";
      result.snapshot = {
        title: item.title || "",
        text: item.text || "",
        tags: item.tags || [],
        image_url: item.imageUrl || "",
        audio_url: item.audioUrl || "",
        media_type: item.mediaType || "",
      };
    } else {
      result.block_id = item.sourceId || "";
      result.snapshot = { title: item.title || "", text: item.text || "", tags: item.tags || [] };
    }
    return result;
  }
  function stageItemFromApi(item) {
    if (!item || (item.kind !== "text" && item.kind !== "fixed" && item.kind !== "action" && item.kind !== "reference")) return null;
    if (item.kind === "text") return { instanceId: item.instance_id || makeId("text"), kind: "text", title: "自由文本", text: String(item.text || ""), tags: [] };
    var sourceId = item.kind === "action" ? (item.action_id || item.block_id) : (item.kind === "reference" ? item.reference_id : item.block_id);
    var source = item.kind === "action" ? state.actions.find(function (candidate) { return candidate.id === sourceId; }) : (item.kind === "reference" ? state.references.find(function (candidate) { return candidate.id === sourceId; }) : allBlocks().find(function (candidate) { return candidate.id === sourceId; }));
    var snapshot = item.snapshot || {};
    var hasSnapshotTitle = Object.prototype.hasOwnProperty.call(snapshot, "title");
    var hasSnapshotText = Object.prototype.hasOwnProperty.call(snapshot, "text");
    var hasSnapshotTags = Object.prototype.hasOwnProperty.call(snapshot, "tags");
    if (item.kind === "reference") {
      return {
        instanceId: item.instance_id || makeId("reference"),
        kind: "reference",
        sourceId: sourceId || "",
        referenceKind: item.reference_kind || (source && source.kind) || "",
        title: hasSnapshotTitle ? String(snapshot.title || "") : (source ? source.title : "参考资源已不可用"),
        text: hasSnapshotText ? String(snapshot.text || "") : (source ? source.text : ""),
        tags: hasSnapshotTags ? (Array.isArray(snapshot.tags) ? snapshot.tags : []) : (source ? (source.tags || []) : []),
        imageUrl: source ? (source.image_url || "") : (snapshot.image_url || ""),
        audioUrl: source ? (source.audio_url || "") : (snapshot.audio_url || ""),
        mediaType: source ? (source.media_type || "") : (snapshot.media_type || ""),
        missing: !source,
      };
    }
    return {
      instanceId: item.instance_id || makeId(item.kind),
      kind: item.kind,
      sourceId: sourceId || "",
      title: hasSnapshotTitle ? String(snapshot.title || "") : (source ? source.title : (item.kind === "action" ? "动作已不可用" : "已删除积木")),
      text: hasSnapshotText ? String(snapshot.text || "") : (source ? source.text : ""),
      tags: hasSnapshotTags ? (Array.isArray(snapshot.tags) ? snapshot.tags : []) : (source ? (source.tags || []) : []),
      imageUrl: source ? (source.color_image_url || source.image_url || "") : (snapshot.color_image_url || ""),
      colorImageUrl: source ? (source.color_image_url || source.image_url || "") : (snapshot.color_image_url || ""),
      depthImageUrl: source ? (source.depth_image_url || "") : (snapshot.depth_image_url || ""),
      pairStatus: source ? (source.pair_status || "") : (snapshot.pair_status || ""),
      missing: !source,
    };
  }
  function applyPromptSnapshot(snapshot) {
    var library = snapshot && snapshot.library ? snapshot.library : {};
    var promptState = snapshot && snapshot.state ? snapshot.state : {};
    var groups = snapshot && snapshot.groups ? snapshot.groups : {};
    state.libraryBlocks = Array.isArray(library.blocks) ? library.blocks : [];
    state.stage = Array.isArray(promptState.items) ? promptState.items.map(stageItemFromApi).filter(Boolean) : [];
    state.groups = Array.isArray(groups.groups) ? groups.groups : [];
    state.activeGroupId = "";
    promptApiReady = true;
  }
  function applyActionSnapshot(snapshot) {
    state.actions = snapshot && Array.isArray(snapshot.actions) ? snapshot.actions : [];
    state.actionSource = snapshot && snapshot.source_status ? snapshot.source_status : null;
  }
  function applyReferenceSnapshot(snapshot) {
    state.references = snapshot && Array.isArray(snapshot.references) ? snapshot.references : [];
    state.referenceSource = snapshot && snapshot.source_status ? snapshot.source_status : null;
  }
  function refreshActions() {
    return jsonRequest("/api/prompt/actions").then(function (snapshot) {
      applyActionSnapshot(snapshot);
      renderFilters();
      renderLibrary();
      showToast("动作库已重新扫描");
      return snapshot;
    }).catch(function (error) {
      showToast("动作库刷新失败：" + error.message, true);
    });
  }
  function refreshReferences() {
    return jsonRequest("/api/prompt/references").then(function (snapshot) {
      applyReferenceSnapshot(snapshot);
      renderFilters();
      renderLibrary();
      showToast("参考资源库已重新扫描");
      return snapshot;
    }).catch(function (error) {
      showToast("参考资源库刷新失败：" + error.message, true);
    });
  }
  function readLegacyState() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var saved = JSON.parse(raw);
      if (!saved || (!Array.isArray(saved.customBlocks) && !Array.isArray(saved.stage))) return null;
      return { customBlocks: Array.isArray(saved.customBlocks) ? saved.customBlocks : [], stage: Array.isArray(saved.stage) ? saved.stage : [] };
    } catch (error) {
      return null;
    }
  }
  function persistState() {
    if (!promptApiReady) return Promise.resolve();
    return jsonRequest("/api/prompt/state", "PUT", { items: state.stage.map(stageItemToApi) }).catch(function (error) {
      showToast("当前组装状态保存失败：" + error.message, true);
    });
  }
  function saveState() {
    if (!promptApiReady) return;
    window.clearTimeout(stateSaveTimer);
    stateSaveTimer = window.setTimeout(persistState, 180);
  }
  function loadState() {
    return Promise.all([
      jsonRequest("/api/prompt/actions").catch(function () { return { actions: [] }; }),
      jsonRequest("/api/prompt/references").catch(function () { return { references: [] }; }),
      jsonRequest("/api/prompt/state"),
    ]).then(function (snapshots) {
      applyActionSnapshot(snapshots[0]);
      applyReferenceSnapshot(snapshots[1]);
      applyPromptSnapshot(snapshots[2]);
      var legacy = readLegacyState();
      if (!legacy) return snapshots[2];
      return jsonRequest("/api/prompt/migrate", "POST", legacy).then(function (migrated) {
        window.localStorage.removeItem(STORAGE_KEY);
        applyPromptSnapshot(migrated);
        return migrated;
      });
    });
  }
  function blockMatches(block) {
    var needle = state.search.toLowerCase();
    var tags = block.tags || [];
    var matchesTag = state.filter === "全部" || tags.indexOf(state.filter) !== -1;
    var matchesSearch = !needle || [block.title, block.text].concat(tags).join(" ").toLowerCase().indexOf(needle) !== -1;
    return matchesTag && matchesSearch;
  }
  function renderFilters() {
    var entries = currentLibraryEntries();
    var tags = unique([].concat.apply([], entries.map(function (entry) { return entry.tags || []; })));
    if (state.filter !== "全部" && tags.indexOf(state.filter) === -1) state.filter = "全部";
    $("tagFilters").innerHTML = ["全部"].concat(tags).map(function (tag) {
      return '<button class="tag-filter' + (state.filter === tag ? " active" : "") + '" type="button" data-filter-tag="' + esc(tag) + '">' + esc(tag) + '</button>';
    }).join("");
  }
  function animateLibraryModeSwitch() {
    var list = $("libraryList");
    if (!list) return;
    list.classList.remove("library-mode-switching");
    void list.offsetWidth;
    list.classList.add("library-mode-switching");
    window.clearTimeout(list.libraryModeAnimationTimer);
    list.libraryModeAnimationTimer = window.setTimeout(function () {
      list.classList.remove("library-mode-switching");
    }, 360);
  }
  function renderLibraryMode() {
    var modeTabs = document.querySelector(".library-mode-tabs");
    if (!modeTabs) return;
    var isActions = state.libraryMode === "pose" || state.libraryMode === "actions";
    modeTabs.querySelectorAll("[data-library-mode]").forEach(function (button) {
      var active = button.dataset.libraryMode === state.libraryMode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    var actionCount = $("actionModeCount");
    if (actionCount) actionCount.textContent = String(state.actions.length);
    state.references.forEach(function (reference) {
      var count = $(reference.kind + "ModeCount");
      if (count) count.textContent = String(state.references.filter(function (item) { return item.kind === reference.kind; }).length);
    });
    var refreshButton = $("refreshActions");
    if (refreshButton) refreshButton.hidden = state.libraryMode === "blocks";
    modeTabs.classList.toggle("is-actions", isActions);
  }
  function actionMediaMarkup(action, extraClass) {
    var title = action.title || "动作图片";
    var colorUrl = action.color_image_url || action.image_url || "";
    var depthUrl = action.depth_image_url || "";
    var colorAvailable = Boolean(action.color_image_available && colorUrl);
    var depthAvailable = Boolean(action.depth_image_available && depthUrl);
    var color = colorAvailable
      ? '<button class="image-preview-trigger action-media-image is-active" type="button" data-action-media-image="color" data-image-preview="' + esc(colorUrl) + '" data-image-title="' + esc(title + " · 原图") + '" aria-label="放大查看「' + esc(title) + '」原图"><img src="' + esc(colorUrl) + '" alt="' + esc(title) + ' · 原图" loading="lazy" /></button>'
      : '<div class="action-media-missing action-media-image is-active"><span>原图缺失</span></div>';
    var depth = depthAvailable
      ? '<button class="image-preview-trigger action-media-image" type="button" data-action-media-image="depth" data-image-preview="' + esc(depthUrl) + '" data-image-title="' + esc(title + " · 深度图") + '" aria-label="放大查看「' + esc(title) + '」深度图" hidden><img src="' + esc(depthUrl) + '" alt="' + esc(title) + ' · 深度图" loading="lazy" /></button>'
      : '<div class="action-media-missing action-media-image" hidden><span>深度图缺失</span></div>';
    var toggle = depthAvailable
      ? '<button class="action-media-toggle" type="button" data-action-media-toggle data-action-media-current="color" aria-label="当前显示原图，点击切换到深度图" title="当前：原图；点击切换到深度图"></button>'
      : "";
    return '<div class="action-media-shell ' + (extraClass || "") + '" data-action-media>' +
      '<div class="action-media-viewport">' + color + depth + '</div>' +
      toggle +
      '</div>';
  }
  function setActionMediaView(container, kind) {
    if (!container || (kind !== "color" && kind !== "depth")) return;
    var target = container.querySelector('[data-action-media-image="' + kind + '"]');
    if (!target) return;
    container.querySelectorAll("[data-action-media-image]").forEach(function (image) {
      image.hidden = image.dataset.actionMediaImage !== kind;
      image.classList.toggle("is-active", image.dataset.actionMediaImage === kind);
    });
    var toggle = container.querySelector("[data-action-media-toggle]");
    if (toggle) {
      var nextKind = kind === "color" ? "depth" : "color";
      var currentLabel = kind === "color" ? "原图" : "深度图";
      var nextLabel = nextKind === "color" ? "原图" : "深度图";
      toggle.dataset.actionMediaCurrent = kind;
      toggle.classList.toggle("is-depth", kind === "depth");
      toggle.setAttribute("aria-label", "当前显示" + currentLabel + "，点击切换到" + nextLabel);
      toggle.title = "当前：" + currentLabel + "；点击切换到" + nextLabel;
    }
  }
  function actionPairLabel(action) {
    var labels = {
      paired: "原图 + 深度图",
      missing_depth: "缺少深度图",
      missing_color: "缺少原图",
      missing_both: "原图与深度图缺失",
      mismatched: "文件名未匹配",
    };
    return labels[action.pair_status] || action.pair_message || "待检查配对";
  }
  function actionPairClass(action) {
    return action.pair_status === "paired" ? "is-paired" : "is-warning";
  }
  function readTaskDraft() {
    try {
      var raw = window.localStorage.getItem("rh-workflow-desk-draft-v1");
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
  function loadImageTargets(draft) {
    if (!draft || !draft.workflow) return [];
    var workflow = draft.workflow.data || {};
    var values = draft.workflow.values || {};
    var bypassed = values.bypassedNodes || values.bypassed_nodes || [];
    var bypassedMap = {};
    if (Array.isArray(bypassed)) bypassed.forEach(function (nodeId) { bypassedMap[String(nodeId)] = true; });
    else Object.keys(bypassed).forEach(function (nodeId) { if (bypassed[nodeId]) bypassedMap[String(nodeId)] = true; });
    var targets = [];
    Object.keys(workflow).forEach(function (nodeId) {
      if (nodeId === "__rh_meta__") return;
      var node = workflow[nodeId];
      if (!node || typeof node !== "object" || String(node.class_type || "").toLowerCase().indexOf("loadimage") === -1) return;
      var inputs = node.inputs && typeof node.inputs === "object" ? node.inputs : {};
      var title = node._meta && node._meta.title ? node._meta.title : node.class_type || "LoadImage";
      Object.keys(inputs).forEach(function (field) {
        if (String(field).toLowerCase() !== "image") return;
        var inputId = nodeId + ":" + field;
        var current = Object.prototype.hasOwnProperty.call(values.files || {}, inputId) ? values.files[inputId] : inputs[field];
        targets.push({
          inputId: inputId,
          nodeId: String(nodeId),
          field: String(field),
          title: String(title),
          classType: String(node.class_type || "LoadImage"),
          current: String(current == null ? "" : current),
          bypassed: Boolean(bypassedMap[String(nodeId)]),
        });
      });
    });
    return targets;
  }
  function renderDepthImportTargets(draft) {
    var targets = loadImageTargets(draft);
    var description = $("depthImportDescription");
    var status = $("depthImportStatus");
    var list = $("depthImportTargets");
    var confirm = $("confirmDepthImport");
    var assetLabel = workflowImportAsset && workflowImportAsset.label ? workflowImportAsset.label : "图片";
    if (!draft) {
      description.textContent = "还没有检测到任务提交页的当前工作流。请先导入工作流，再回来选择 LoadImage 节点。";
      status.textContent = "任务提交页暂无可用工作流草稿";
      list.innerHTML = '<div class="depth-import-empty"><strong>先导入一个 API 工作流</strong><span>导入后，这里会列出工作流中的全部 LoadImage 节点。</span></div>';
      confirm.disabled = true;
      return;
    }
    description.textContent = "选择任务提交页当前工作流中的 LoadImage 节点，" + assetLabel + "会作为该节点的本机输入。";
    status.textContent = "当前工作流：" + (draft.workflow.name || "workflow_api.json") + " · 找到 " + targets.length + " 个 LoadImage 节点";
    if (!targets.length) {
      list.innerHTML = '<div class="depth-import-empty"><strong>没有找到 LoadImage 节点</strong><span>请确认当前工作流使用的是 API 格式，并包含 LoadImage 节点。</span></div>';
      confirm.disabled = true;
      return;
    }
    var available = targets.filter(function (target) { return !target.bypassed; });
    var selected = available[0] || null;
    list.innerHTML = targets.map(function (target) {
      var disabled = target.bypassed;
      var checked = selected && selected.inputId === target.inputId;
      return '<label class="depth-import-target' + (disabled ? ' is-disabled' : '') + '">' +
        '<input type="radio" name="depth-import-target" value="' + esc(target.inputId) + '"' + (checked ? ' checked' : '') + (disabled ? ' disabled' : '') + ' />' +
        '<span class="depth-import-target-copy"><strong>' + esc(target.title) + '</strong><span><code>' + esc(target.inputId) + '</code> · ' + esc(target.classType) + '</span><small>' + (disabled ? '已旁路，本次提交不会使用' : '当前：' + esc(filenameFromPath(target.current))) + '</small></span>' +
        '</label>';
    }).join("");
    confirm.disabled = !selected;
  }
  function openDepthImport(action) {
    if (!action || !action.depth_image_url || !action.depth_image_available) return showToast("这个动作没有可用的深度图", true);
    depthImportActionId = action.id;
    workflowImportAsset = {
      endpoint: "/api/prompt/actions/" + encodeURIComponent(action.id) + "/depth-path",
      label: "深度图",
      title: action.title + " · 深度图",
      toastLabel: "深度图",
    };
    renderDepthImportTargets(readTaskDraft());
    $("depthImportTitle").textContent = "导入深度图";
    $("depthImportKicker").textContent = "IMPORT DEPTH MAP";
    window.RHMotion.openModal("depthImportModal", "closeDepthImport");
  }
  function openWorkflowImport(asset) {
    if (!asset || !asset.endpoint) return showToast("这个积木没有可用的图片", true);
    depthImportActionId = "";
    workflowImportAsset = asset;
    renderDepthImportTargets(readTaskDraft());
    $("depthImportTitle").textContent = "导入工作流";
    $("depthImportKicker").textContent = "IMPORT TO WORKFLOW";
    window.RHMotion.openModal("depthImportModal", "closeDepthImport");
  }
  function openWorkflowImportFromTrigger(trigger) {
    var kind = trigger.dataset.importWorkflowKind || "";
    var id = trigger.dataset.importWorkflowId || "";
    if (kind === "action") {
      var action = state.actions.find(function (item) { return item.id === id; });
      if (!action || !action.depth_image_available || !action.depth_image_url) return showToast("这个动作没有可用的深度图", true);
      return openWorkflowImport({
        endpoint: "/api/prompt/actions/" + encodeURIComponent(id) + "/depth-path",
        label: "深度图",
        title: action.title + " · 深度图",
        toastLabel: "深度图",
      });
    }
    if (kind === "reference") {
      var reference = state.references.find(function (item) { return item.id === id; });
      if (!reference || !reference.image_available || !reference.image_url) return showToast("这个参考资源没有可用的图片", true);
      return openWorkflowImport({
        endpoint: "/api/prompt/references/" + encodeURIComponent(id) + "/image-path",
        label: "图片",
        title: reference.title + " · 图片",
        toastLabel: "图片",
      });
    }
    showToast("这个积木没有可导入的图片", true);
  }
  function closeDepthImport() {
    depthImportActionId = "";
    workflowImportAsset = null;
    $("depthImportTitle").textContent = "导入深度图";
    $("depthImportKicker").textContent = "IMPORT DEPTH MAP";
    window.RHMotion.closeModal("depthImportModal");
  }
  function confirmWorkflowImport() {
    var selected = $("depthImportTargets").querySelector('input[name="depth-import-target"]:checked');
    if (!workflowImportAsset || !selected) return showToast("请先选择一个 LoadImage 节点", true);
    var button = $("confirmDepthImport");
    var importedLabel = workflowImportAsset.label || "图片";
    var toastLabel = workflowImportAsset.toastLabel || importedLabel;
    button.disabled = true;
    button.textContent = "导入中…";
    jsonRequest(workflowImportAsset.endpoint).then(function (asset) {
      var draft = readTaskDraft();
      var target = loadImageTargets(draft).find(function (item) { return item.inputId === selected.value && !item.bypassed; });
      if (!draft || !target) throw new Error("任务提交页的工作流已发生变化，请重新打开导入窗口");
      var workflow = draft.workflow.data;
      var node = workflow[target.nodeId];
      if (!node || !node.inputs || typeof node.inputs !== "object") throw new Error("找不到选中的 LoadImage 节点");
      node.inputs[target.field] = asset.path;
      draft.workflow.values = draft.workflow.values || {};
      draft.workflow.values.files = draft.workflow.values.files || {};
      draft.workflow.values.files[target.inputId] = asset.path;
      draft.workflow.savedAt = Date.now();
      window.localStorage.setItem("rh-workflow-desk-draft-v1", JSON.stringify(draft));
      closeDepthImport();
      showToast(toastLabel + "已导入「" + target.title + "」，已保存到任务草稿");
    }).catch(function (error) {
      showToast(importedLabel + "导入失败：" + error.message, true);
    }).finally(function () {
      if ($("confirmDepthImport")) {
        $("confirmDepthImport").disabled = false;
        $("confirmDepthImport").textContent = "导入到选中节点";
      }
    });
  }
  function confirmDepthImport() {
    confirmWorkflowImport();
  }
  function renderActionLibrary() {
    var actions = state.actions.filter(blockMatches);
    if (!actions.length) {
      $("libraryList").innerHTML = '<div class="library-empty">没有匹配的动作。<br />试试其他标签或搜索提示词。</div>';
      $("libraryCount").textContent = actions.length + " 个动作";
      $("libraryFooterHint").textContent = state.actionSource ? state.actionSource.paired_count + "/" + state.actionSource.action_count + " 对已配对" : "点击或拖动加入";
      return;
    }
    $("libraryList").innerHTML = actions.map(function (action, index) {
      var importWorkflowButton = action.depth_image_available
        ? '<button class="import-workflow-button action-card-import" type="button" data-import-workflow data-import-workflow-kind="action" data-import-workflow-id="' + esc(action.id) + '" title="选择 LoadImage 节点并导入深度图">导入工作流</button>'
        : "";
      return '<article class="action-library-card" draggable="true" data-action-id="' + esc(action.id) + '" style="animation-delay:' + Math.min(index * 35, 220) + 'ms">' +
        '<div class="action-card-media">' + actionMediaMarkup(action, "") + '</div>' +
        '<div class="action-card-body"><div class="library-block-top action-card-top"><div class="library-block-title"><span class="block-type-dot action" aria-hidden="true"></span><span>' + esc(action.title) + '</span></div><span class="action-card-top-actions">' + importWorkflowButton + '<span class="library-block-label">POSE</span></span></div>' +
        '<div class="block-tags">' + (action.tags || []).map(function (tag) { return '<span class="block-tag">' + esc(tag) + '</span>'; }).join("") + '</div>' +
        '<div class="action-library-text">' + esc(action.text) + '</div>' +
        '<div class="library-block-footer"><span class="action-card-buttons"><button class="add-block-button" type="button" data-add-action="' + esc(action.id) + '">加入组装台&nbsp;→</button></span><span class="action-pair-status ' + actionPairClass(action) + '" title="' + esc(action.pair_message || "") + '">' + esc(actionPairLabel(action)) + '</span></div></div></article>';
    }).join("");
    $("libraryCount").textContent = actions.length + " 个动作";
    $("libraryFooterHint").textContent = state.actionSource ? state.actionSource.paired_count + "/" + state.actionSource.action_count + " 对已配对" : "点击或拖动加入";
  }
  function referenceMediaMarkup(reference, extraClass) {
    var title = reference.title || "参考资源";
    var imageUrl = reference.image_url || reference.imageUrl || "";
    var audioUrl = reference.audio_url || reference.audioUrl || "";
    if (imageUrl && (reference.image_available !== false || reference.imageUrl)) {
      return '<div class="reference-media ' + (extraClass || "") + '"><button class="image-preview-trigger reference-media-image" type="button" data-image-preview="' + esc(imageUrl) + '" data-image-title="' + esc(title) + '" aria-label="放大查看「' + esc(title) + '」"><img src="' + esc(imageUrl) + '" alt="' + esc(title) + '" loading="lazy" /></button></div>';
    }
    if (audioUrl && (reference.audio_available !== false || reference.audioUrl)) {
      return '<div class="reference-media reference-audio-media ' + (extraClass || "") + '"><button class="reference-media-icon reference-audio-toggle" type="button" data-audio-toggle aria-pressed="false" aria-label="播放「' + esc(title) + '」" title="播放/暂停音频">♫</button><audio class="reference-audio-player" preload="none" src="' + esc(audioUrl) + '"></audio></div>';
    }
    return '<div class="reference-media reference-media-missing ' + (extraClass || "") + '"><span>暂无媒体预览</span></div>';
  }
  function syncReferenceAudioButton(audio, isPlaying) {
    var container = audio && audio.closest(".reference-audio-media");
    var button = container && container.querySelector("[data-audio-toggle]");
    if (!button) return;
    button.classList.toggle("is-playing", Boolean(isPlaying));
    button.setAttribute("aria-pressed", isPlaying ? "true" : "false");
    button.setAttribute("aria-label", (isPlaying ? "暂停" : "播放") + "「" + (button.dataset.audioTitle || "音频") + "」");
    button.title = isPlaying ? "暂停音频" : "播放音频";
  }
  function prepareReferenceAudio(audio, title) {
    if (!audio) return;
    var button = audio.closest(".reference-audio-media") && audio.closest(".reference-audio-media").querySelector("[data-audio-toggle]");
    if (button) button.dataset.audioTitle = title || "音频";
    if (audio.dataset.promptListeners) return;
    audio.dataset.promptListeners = "1";
    audio.addEventListener("play", function () { syncReferenceAudioButton(audio, true); });
    audio.addEventListener("pause", function () { syncReferenceAudioButton(audio, false); });
    audio.addEventListener("ended", function () { syncReferenceAudioButton(audio, false); });
  }
  function toggleReferenceAudio(button) {
    var container = button && button.closest(".reference-audio-media");
    var audio = container && container.querySelector(".reference-audio-player");
    if (!audio) return;
    prepareReferenceAudio(audio, button.dataset.audioTitle || "音频");
    document.querySelectorAll(".reference-audio-player").forEach(function (other) {
      if (other === audio) return;
      other.pause();
      other.currentTime = 0;
    });
    if (audio.paused) {
      var promise = audio.play();
      if (promise && promise.catch) promise.catch(function () { showToast("音频播放失败，请检查本机文件路径", true); });
    } else {
      audio.pause();
    }
  }
  function renderReferenceLibrary() {
    var references = currentLibraryEntries().filter(blockMatches);
    if (!references.length) {
      $("libraryList").innerHTML = '<div class="library-empty">没有匹配的' + esc(({ character: "人物", audio: "音频", background: "背景", clothes: "服装" }[state.libraryMode] || "参考资源")) + '。<br />试试其他标签或搜索提示词。</div>';
      $("libraryCount").textContent = "0 个参考资源";
      $("libraryFooterHint").textContent = "点击或拖动加入";
      return;
    }
    $("libraryList").innerHTML = references.map(function (reference, index) {
      var label = reference.kind_label || "参考资源";
      var importWorkflowButton = reference.image_available
        ? '<button class="import-workflow-button" type="button" data-import-workflow data-import-workflow-kind="reference" data-import-workflow-id="' + esc(reference.id) + '" title="选择 LoadImage 节点并导入图片">导入工作流</button>'
        : "";
      return '<article class="reference-library-card" draggable="true" data-reference-id="' + esc(reference.id) + '" style="animation-delay:' + Math.min(index * 35, 220) + 'ms">' +
        referenceMediaMarkup(reference, "reference-card-media") +
        '<div class="reference-card-body"><div class="library-block-top reference-card-top"><div class="library-block-title"><span class="block-type-dot reference" aria-hidden="true"></span><span>' + esc(reference.title) + '</span></div><span class="action-card-top-actions">' + importWorkflowButton + '<span class="library-block-label">' + esc(label) + '</span></span></div>' +
        '<div class="block-tags">' + (reference.tags || []).map(function (tag) { return '<span class="block-tag">' + esc(tag) + '</span>'; }).join("") + '</div>' +
        '<div class="reference-library-text">' + esc(reference.text || "暂无提示词文本") + '</div>' +
        '<div class="library-block-footer"><button class="add-block-button" type="button" data-add-reference="' + esc(reference.id) + '">加入组装台&nbsp;→</button><span class="reference-card-kind">' + esc(reference.media_type === "audio" ? "音频预览" : reference.image_available ? "图片预览" : "文本资源") + '</span></div></div></article>';
    }).join("");
    $("libraryCount").textContent = references.length + " 个参考资源";
    $("libraryFooterHint").textContent = state.referenceSource ? "共 " + state.referenceSource.reference_count + " 个资源" : "点击或拖动加入";
  }
  function renderLibrary() {
    renderLibraryMode();
    if (state.libraryMode === "pose" || state.libraryMode === "actions") return renderActionLibrary();
    if (isReferenceMode()) return renderReferenceLibrary();
    var blocks = allBlocks().filter(blockMatches);
    var html = '<button class="free-block-card" type="button" draggable="true" data-add-text-block>' +
      '<span class="block-type-dot text" aria-hidden="true"></span>' +
      '<span class="free-block-copy"><strong>自由文本</strong><span>每次加入一块新的可编辑文本</span></span>' +
      '<span class="free-block-plus" aria-hidden="true">＋</span></button>';
    if (!blocks.length) {
      html += '<div class="library-empty">没有匹配的固定积木。<br />试试其他标签或添加一块新的积木。</div>';
    } else {
      html += blocks.map(function (block, index) {
        return '<article class="library-block" draggable="true" data-library-block-id="' + esc(block.id) + '" style="animation-delay:' + Math.min(index * 35, 220) + 'ms">' +
          '<div class="library-block-top"><div class="library-block-title"><span class="block-type-dot" aria-hidden="true"></span><span>' + esc(block.title) + '</span></div>' +
          '<span class="library-block-label">JSON</span></div>' +
          '<div class="block-tags">' + (block.tags || []).map(function (tag) { return '<span class="block-tag">' + esc(tag) + '</span>'; }).join("") + '</div>' +
          '<div class="library-block-text">' + esc(block.text) + '</div>' +
          '<div class="library-block-footer"><button class="add-block-button" type="button" data-add-block="' + esc(block.id) + '">加入组装台&nbsp;→</button>' +
          '<span class="library-block-manage-actions"><button class="edit-block-button" type="button" data-edit-block="' + esc(block.id) + '">编辑</button><button class="delete-block-button" type="button" data-delete-block="' + esc(block.id) + '">删除</button></span></div></article>';
      }).join("");
    }
    $("libraryList").innerHTML = html;
    $("libraryCount").textContent = blocks.length + " 个固定积木";
    $("libraryFooterHint").textContent = "点击或拖动加入";
  }
  function stageBlockMarkup(item, index) {
    var isText = item.kind === "text";
    var isAction = item.kind === "action";
    var isReference = item.kind === "reference";
    var tags = item.tags || [];
    var sourceAction = isAction && !item.missing ? state.actions.find(function (action) { return action.id === item.sourceId; }) : null;
    var canImportWorkflow = Boolean(
      (sourceAction && sourceAction.depth_image_available && sourceAction.depth_image_url) ||
      (isReference && !item.missing && item.imageUrl)
    );
    var actionThumb = isAction && !item.missing && (item.colorImageUrl || item.imageUrl || item.depthImageUrl)
      ? actionMediaMarkup({ title: item.title || "动作图片", color_image_url: item.colorImageUrl || item.imageUrl || "", depth_image_url: item.depthImageUrl || "", color_image_available: Boolean(item.colorImageUrl || item.imageUrl), depth_image_available: Boolean(item.depthImageUrl), pair_status: item.pairStatus || "" }, "stage-action-media")
      : "";
    var referenceThumb = isReference && !item.missing && (item.imageUrl || item.audioUrl)
      ? referenceMediaMarkup({ title: item.title, imageUrl: item.imageUrl, audioUrl: item.audioUrl, image_available: Boolean(item.imageUrl), audio_available: Boolean(item.audioUrl), media_type: item.mediaType }, "stage-reference-media")
      : "";
    var referenceLabels = { character: "人物库", audio: "音频库", background: "背景库", clothes: "服装库" };
    var typeLabel = isText ? "自由文本" : (isAction ? (item.missing ? "动作 · 已不可用" : "动作库") : (isReference ? (item.missing ? "参考资源 · 已不可用" : (referenceLabels[item.referenceKind] || "参考资源库")) : (item.missing ? "固定积木 · 已删除" : "固定积木")));
    var importWorkflowButton = canImportWorkflow
      ? '<button class="import-workflow-button stage-workflow-import" type="button" data-import-workflow data-import-workflow-kind="' + (isAction ? "action" : "reference") + '" data-import-workflow-id="' + esc(item.sourceId) + '" title="选择 LoadImage 节点并导入' + (isAction ? "深度图" : "图片") + '">导入工作流</button>'
      : "";
    var textMarkup = isText
      ? '<textarea class="stage-text-editor" data-stage-text="' + index + '" placeholder="输入这一块要拼接的文本内容"></textarea>'
      : '<button class="stage-block-copy stage-block-copy-trigger" type="button" data-edit-stage="' + index + '" title="点击编辑当前积木" aria-label="编辑当前积木：' + esc(item.title || "固定积木") + '">' + esc(item.text) + '</button>';
    return '<article class="stage-block ' + (isText ? "text" : (isAction ? "action" : (isReference ? "reference" : "fixed"))) + (item.missing ? " missing" : "") + '" draggable="false" data-stage-index="' + index + '" data-stage-instance-id="' + esc(item.instanceId) + '">' +
      '<div class="stage-block-grip" data-drag-handle title="拖动排序" aria-label="拖动排序">⋮⋮</div>' +
      '<div class="stage-block-main"><div class="stage-block-copy-content"><div class="stage-block-top"><span class="stage-index">' + String(index + 1).padStart(2, "0") + '</span><span class="stage-type-label">' + typeLabel + '</span></div>' +
      '<h3>' + esc(item.title || (isText ? "自由文本" : "固定积木")) + '</h3>' +
      ((tags.length || importWorkflowButton) ? '<div class="stage-block-tags">' + importWorkflowButton + tags.map(function (tag) { return '<span class="block-tag">' + esc(tag) + '</span>'; }).join("") + '</div>' : "") +
      textMarkup +
      '</div>' + actionThumb + referenceThumb + '</div><div class="stage-block-actions"><button class="stage-action remove" type="button" data-remove-stage="' + index + '">移除</button></div></article>';
  }
  function updateStageDom() {
    var cards = $("stageList").querySelectorAll(".stage-block");
    $("stageCount").textContent = state.stage.length + " 个积木";
    $("stageTabCount").textContent = String(state.stage.length);
    cards.forEach(function (card, index) {
      card.dataset.stageIndex = String(index);
      var indexLabel = card.querySelector(".stage-index");
      if (indexLabel) indexLabel.textContent = String(index + 1).padStart(2, "0");
      var removeButton = card.querySelector("[data-remove-stage]");
      if (removeButton) removeButton.dataset.removeStage = String(index);
      var editor = card.querySelector("[data-stage-text]");
      if (editor) editor.dataset.stageText = String(index);
    });
  }
  function renderStage() {
    var list = $("stageList");
    if (!state.stage.length) {
      list.innerHTML = '<div class="stage-empty"><span class="stage-empty-mark">01</span><strong>组装台还是空的</strong><span>从左侧点击积木或动作，或加入一块自由文本开始。</span></div>';
      updateStageDom();
      renderOutput();
      return;
    }
    list.innerHTML = state.stage.map(function (item, index) {
      return stageBlockMarkup(item, index);
    }).join("");
    state.stage.forEach(function (item, index) {
      if (item.kind !== "text") return;
      var editor = document.querySelector('[data-stage-text="' + index + '"]');
      if (editor) editor.value = item.text || "";
    });
    updateStageDom();
    renderOutput();
  }
  function renderOutput() {
    var output = getPromptText();
    $("promptOutput").value = output;
    $("charCount").textContent = output.length + " 字";
    $("lineCount").textContent = output ? output.split(/\n\s*\n/).length + " 段" : "0 段";
  }
  function renderAssemblyView() {
    var isGroups = state.assemblyView === "groups";
    var stageButton = $("assemblyViewStage");
    var groupsButton = $("assemblyViewGroups");
    var stageView = $("assemblyStageView");
    var groupsView = $("assemblyGroupView");
    if (stageButton) {
      stageButton.classList.toggle("active", !isGroups);
      stageButton.setAttribute("aria-selected", String(!isGroups));
    }
    if (groupsButton) {
      groupsButton.classList.toggle("active", isGroups);
      groupsButton.setAttribute("aria-selected", String(isGroups));
    }
    var viewTabs = document.querySelector(".assembly-view-tabs");
    if (viewTabs) viewTabs.classList.toggle("is-groups", isGroups);
    if (stageView) stageView.hidden = isGroups;
    if (groupsView) groupsView.hidden = !isGroups;
  }
  function renderAll() {
    renderFilters();
    renderLibrary();
    renderStage();
    renderGroups();
    renderAssemblyView();
  }
  function formatGroupTime(value) {
    var timestamp = Number(value || 0);
    if (!timestamp) return "尚未保存";
    var date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return "尚未保存";
    return date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function renderGroups() {
    var list = $("groupList");
    if (!list) return;
    if (!state.groups.length) {
      list.innerHTML = '<div class="group-empty">还没有组状态。<br />给当前组装顺序命名后保存。</div>';
    } else {
      list.innerHTML = state.groups.map(function (group) {
        var active = group.id === state.activeGroupId;
        return '<article class="group-entry' + (active ? " active" : "") + '">' +
          '<div class="group-entry-main"><strong>' + esc(group.name) + '</strong><span>' + (group.items || []).length + ' 个积木 · ' + esc(formatGroupTime(group.updated_at)) + '</span></div>' +
          '<div class="group-entry-actions"><button class="group-action load" type="button" data-load-group="' + esc(group.id) + '">加载</button><button class="group-action delete" type="button" data-delete-group="' + esc(group.id) + '">删除</button></div>' +
          '</article>';
      }).join("");
    }
    var saveButton = $("saveGroup");
    if (saveButton) saveButton.textContent = state.activeGroupId ? "覆盖保存" : "新建并保存";
    var groupCount = $("groupTabCount");
    if (groupCount) groupCount.textContent = String(state.groups.length);
  }
  function stageItemFromLibrary(id) {
    if (id === "__free_text__") return { instanceId: makeId("text"), kind: "text", title: "自由文本", text: "", tags: [] };
    if (state.libraryMode === "pose" || state.libraryMode === "actions") {
      var action = state.actions.find(function (item) { return item.id === id; });
      if (!action) return null;
      return {
        instanceId: makeId("action"),
        kind: "action",
        sourceId: action.id,
        title: action.title,
        text: action.text,
        tags: action.tags || [],
        imageUrl: action.color_image_url || action.image_url || "",
        colorImageUrl: action.color_image_url || action.image_url || "",
        depthImageUrl: action.depth_image_url || "",
        pairStatus: action.pair_status || "",
        missing: false,
      };
    }
    if (isReferenceMode()) {
      var reference = state.references.find(function (item) { return item.id === id && item.kind === state.libraryMode; });
      if (!reference) return null;
      return {
        instanceId: makeId("reference"),
        kind: "reference",
        sourceId: reference.id,
        referenceKind: reference.kind,
        title: reference.title,
        text: reference.text || "",
        tags: reference.tags || [],
        imageUrl: reference.image_url || "",
        audioUrl: reference.audio_url || "",
        mediaType: reference.media_type || "",
        missing: false,
      };
    }
    var block = allBlocks().find(function (item) { return item.id === id; });
    if (!block) return null;
    return { instanceId: makeId("fixed"), kind: "fixed", sourceId: block.id, title: block.title, text: block.text, tags: block.tags || [] };
  }
  function insertLibraryBlock(id, insertIndex) {
    var item = stageItemFromLibrary(id);
    if (!item) return;
    var safeIndex = Math.max(0, Math.min(Number(insertIndex), state.stage.length));
    state.stage.splice(safeIndex, 0, item);
    saveState();
    var list = $("stageList");
    var empty = list.querySelector(".stage-empty");
    if (empty) empty.remove();
    var card = document.createElement("article");
    card.innerHTML = stageBlockMarkup(item, safeIndex);
    var newCard = card.firstElementChild;
    list.insertBefore(newCard, list.children[safeIndex] || null);
    if (item.kind === "text") {
      var editor = newCard.querySelector("[data-stage-text]");
      if (editor) editor.value = item.text || "";
    }
    updateStageDom();
    renderOutput();
    if (item.kind === "text") {
      var editor = document.querySelector('[data-stage-text="' + safeIndex + '"]');
      if (editor) editor.focus();
    }
    showToast(item.kind === "text" ? "已加入一块自由文本" : "已加入「" + item.title + "」");
  }
  function addFixedBlock(id) {
    insertLibraryBlock(id, state.stage.length);
  }
  function addAction(id) {
    insertLibraryBlock(id, state.stage.length);
  }
  function addReference(id) {
    insertLibraryBlock(id, state.stage.length);
  }
  function addTextBlock() {
    insertLibraryBlock("__free_text__", state.stage.length);
  }
  function removeStage(index) {
    state.stage.splice(index, 1);
    saveState();
    renderStage();
  }
  function editStage(index) {
    var item = state.stage[index];
    if (!item || item.kind === "text") return;
    editingBlockId = "";
    editingStageIndex = index;
    $("customBlockTitle").textContent = "编辑组装台积木";
    $("customBlockModal").querySelector(".section-kicker").textContent = "EDIT ASSEMBLY BLOCK";
    $("customBlockDescription").textContent = "这里只修改当前组装台中的这一块，不会改变积木库里的原始内容。";
    $("customBlockForm").querySelector('button[type="submit"]').textContent = "保存到组装台";
    $("customBlockForm").reset();
    $("customBlockName").value = item.title || "";
    $("customBlockText").value = item.text || "";
    $("customBlockTags").value = (item.tags || []).join("，");
    window.RHMotion.openModal("customBlockModal", "customBlockName");
  }
  function parseTags(value) {
    return unique(String(value || "").split(/[,，、\s]+/));
  }
  function stopStageMotion() {
    state.dragPreviewFrames.forEach(function (frame) { window.cancelAnimationFrame(frame); });
    state.dragPreviewFrames = [];
    document.querySelectorAll(".stage-block").forEach(function (card) {
      card.style.removeProperty("transition");
      card.style.removeProperty("transform");
    });
  }
  function clearDropIndicators() {
    stopStageMotion();
    document.querySelectorAll(".drag-placeholder").forEach(function (marker) { marker.remove(); });
    state.dragPreviewIndex = null;
    document.querySelectorAll(".stage-block, .stage-empty").forEach(function (card) {
      card.classList.remove("drop-target", "drop-before", "drop-after");
    });
    clearLibraryDropTarget();
  }
  function clearLibraryDropTarget() {
    document.querySelectorAll(".library-panel.is-stage-drop-target").forEach(function (panel) {
      panel.classList.remove("is-stage-drop-target");
    });
  }
  function libraryDropTargetAtPointer(x, y) {
    var panel = document.querySelector(".library-panel");
    if (!panel) return null;
    var rect = panel.getBoundingClientRect();
    return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom ? panel : null;
  }
  function clearDropHighlights() {
    document.querySelectorAll(".stage-block, .stage-empty").forEach(function (card) {
      card.classList.remove("drop-target", "drop-before", "drop-after");
    });
  }
  function captureStagePositions() {
    var positions = {};
    document.querySelectorAll(".stage-block").forEach(function (card) {
      var key = card.dataset.stageInstanceId || card.dataset.stageIndex;
      positions[key] = card.getBoundingClientRect().top;
    });
    return positions;
  }
  function animateStageReflow(previousPositions) {
    var draggedCard = findDraggedStageCard();
    document.querySelectorAll(".stage-block").forEach(function (card) {
      if (card === draggedCard) return;
      var key = card.dataset.stageInstanceId || card.dataset.stageIndex;
      var previousTop = previousPositions[key];
      if (previousTop == null) return;
      var offset = previousTop - card.getBoundingClientRect().top;
      if (Math.abs(offset) < 1) return;
      card.style.transition = "none";
      card.style.transform = "translateY(" + offset + "px)";
      card.offsetHeight;
      var frame = window.requestAnimationFrame(function () {
        card.style.transition = "";
        card.style.transform = "";
        state.dragPreviewFrames = state.dragPreviewFrames.filter(function (item) { return item !== frame; });
      });
      state.dragPreviewFrames.push(frame);
    });
  }
  function playStageDropAnimation(card) {
    if (!card || !card.isConnected) return;
    card.classList.remove("drop-settling");
    window.requestAnimationFrame(function () {
      if (!card.isConnected) return;
      card.classList.add("drop-settling");
      window.setTimeout(function () { card.classList.remove("drop-settling"); }, 380);
    });
  }
  function setLibraryDropPreview(card, event) {
    var rect = card.getBoundingClientRect();
    var insertAfter = event.clientY > rect.top + rect.height / 2;
    var targetIndex = Number(card.dataset.stageIndex);
    var insertIndex = targetIndex + (insertAfter ? 1 : 0);
    if (state.dragPreviewIndex === insertIndex) return;
    stopStageMotion();
    var previousPositions = captureStagePositions();
    clearDropHighlights();
    document.querySelectorAll(".drag-placeholder").forEach(function (marker) { marker.remove(); });
    var marker = document.createElement("div");
    marker.className = "drag-placeholder";
    marker.setAttribute("aria-hidden", "true");
    marker.innerHTML = '<span class="drag-placeholder-mark">＋</span><span>放在这里</span>';
    var nextCard = document.querySelector('.stage-block[data-stage-index="' + insertIndex + '"]');
    if (nextCard) $("stageList").insertBefore(marker, nextCard);
    else $("stageList").appendChild(marker);
    state.dragPreviewIndex = insertIndex;
    card.classList.add("drop-target", insertAfter ? "drop-after" : "drop-before");
    animateStageReflow(previousPositions);
  }
  function findDraggedStageCard() {
    return document.querySelector('.stage-block[data-stage-index="' + state.draggedIndex + '"]');
  }
  function setStageDropPreview(card, event) {
    var draggedCard = findDraggedStageCard();
    if (!draggedCard || draggedCard === card) return;
    var cards = Array.from($("stageList").querySelectorAll(".stage-block"));
    var sourceIndex = state.draggedIndex;
    var targetIndex = cards.indexOf(card);
    if (sourceIndex < 0 || targetIndex < 0) return;
    var rect = card.getBoundingClientRect();
    var insertAfter = event.clientY > rect.top + rect.height / 2;
    var insertIndex = targetIndex + (insertAfter ? 1 : 0);
    if (sourceIndex < insertIndex) insertIndex -= 1;
    if (state.dragPreviewIndex === insertIndex) {
      clearDropHighlights();
      card.classList.add("drop-target", insertAfter ? "drop-after" : "drop-before");
      return;
    }
    stopStageMotion();
    var previousPositions = captureStagePositions();
    clearDropHighlights();
    card.classList.add("drop-target", insertAfter ? "drop-after" : "drop-before");
    state.dragPreviewIndex = insertIndex;
    document.querySelectorAll(".drag-placeholder").forEach(function (marker) { marker.remove(); });
    if (insertIndex !== sourceIndex) {
      var marker = document.createElement("div");
      marker.className = "drag-placeholder";
      marker.setAttribute("aria-hidden", "true");
      marker.innerHTML = '<span class="drag-placeholder-mark">＋</span><span>放在这里</span>';
      var domInsertIndex = insertIndex + (sourceIndex <= insertIndex ? 1 : 0);
      $("stageList").insertBefore(marker, cards[domInsertIndex] || null);
    }
    animateStageReflow(previousPositions);
  }
  function stageCardAtPointer(event, sourceCard) {
    var sourceRect = sourceCard.getBoundingClientRect();
    if (event.clientX >= sourceRect.left && event.clientX <= sourceRect.right && event.clientY >= sourceRect.top && event.clientY <= sourceRect.bottom) return sourceCard;
    var underPointer = typeof document.elementFromPoint === "function" ? document.elementFromPoint(event.clientX, event.clientY) : null;
    var card = underPointer && underPointer.closest ? underPointer.closest(".stage-block") : null;
    if (card && card !== sourceCard) return card;
    var cards = Array.from($("stageList").querySelectorAll(".stage-block")).filter(function (item) { return item !== sourceCard; });
    var next = cards.find(function (item) {
      var rect = item.getBoundingClientRect();
      return event.clientY < rect.top + rect.height / 2;
    });
    return next || cards[cards.length - 1] || null;
  }
  function stageInsertIndexAtPointer(x, y, sourceCard) {
    var sourceIndex = state.draggedIndex;
    if (sourceIndex == null || sourceIndex < 0) return sourceIndex;
    var targetCard = stageCardAtPointer({ clientX: x, clientY: y }, sourceCard);
    if (!targetCard || targetCard === sourceCard) return sourceIndex;
    var targetIndex = Number(targetCard.dataset.stageIndex);
    if (targetIndex < 0 || targetIndex >= state.stage.length) return sourceIndex;
    var rect = targetCard.getBoundingClientRect();
    var insertIndex = targetIndex + (y > rect.top + rect.height / 2 ? 1 : 0);
    if (sourceIndex < insertIndex) insertIndex -= 1;
    return Math.max(0, Math.min(insertIndex, state.stage.length - 1));
  }
  function finishPointerStageDrag(commit) {
    var drag = state.pointerDrag;
    if (!drag) return;
    var sourceIndex = state.draggedIndex;
    drag.overLibrary = Boolean(libraryDropTargetAtPointer(drag.x, drag.y));
    if (drag.card.hasPointerCapture && drag.card.hasPointerCapture(drag.pointerId)) drag.card.releasePointerCapture(drag.pointerId);
    if (typeof sourceIndex !== "number" || sourceIndex < 0 || sourceIndex >= state.stage.length || !drag.card.isConnected) {
      clearDropIndicators();
      state.pointerDrag = null;
      state.draggedIndex = null;
      state.dragPreviewIndex = null;
      return;
    }
    if (commit && drag.overLibrary) {
      var removed = state.stage.splice(sourceIndex, 1)[0];
      clearDropIndicators();
      drag.card.classList.remove("dragging");
      state.pointerDrag = null;
      state.draggedIndex = null;
      state.dragPreviewIndex = null;
      saveState();
      renderStage();
      showToast(removed ? "已从工作台移除「" + (removed.title || "当前积木") + "」" : "已从工作台移除当前积木");
      return;
    }
    var insertIndex = state.dragPreviewIndex;
    if (insertIndex == null && commit) insertIndex = stageInsertIndexAtPointer(drag.x, drag.y, drag.card);
    if (insertIndex == null) insertIndex = sourceIndex;
    insertIndex = Math.max(0, Math.min(insertIndex, state.stage.length - 1));
    if (commit) {
      var item = state.stage.splice(sourceIndex, 1)[0];
      if (item) state.stage.splice(insertIndex, 0, item);
      clearDropIndicators();
      drag.card.classList.remove("dragging");
      drag.card.remove();
      $("stageList").insertBefore(drag.card, $("stageList").querySelectorAll(".stage-block")[insertIndex] || null);
      updateStageDom();
      playStageDropAnimation(drag.card);
      saveState();
      renderOutput();
    } else {
      clearDropIndicators();
      drag.card.classList.remove("dragging");
    }
    state.pointerDrag = null;
    state.draggedIndex = null;
    state.dragPreviewIndex = null;
  }
  function saveGroup() {
    var input = $("groupName");
    var name = input.value.trim();
    if (!name) return showToast("请先给组状态命名", true);
    var button = $("saveGroup");
    button.disabled = true;
    jsonRequest("/api/prompt/groups", "POST", {
      id: state.activeGroupId,
      name: name,
      items: state.stage.map(stageItemToApi),
    }).then(function (data) {
      var group = data.group;
      var index = state.groups.findIndex(function (item) { return item.id === group.id; });
      if (index === -1) state.groups.unshift(group);
      else state.groups[index] = group;
      state.activeGroupId = group.id;
      input.value = group.name;
      renderGroups();
      showToast(index === -1 ? "组状态已保存" : "组状态已覆盖保存");
    }).catch(function (error) {
      showToast("组状态保存失败：" + error.message, true);
    }).finally(function () {
      button.disabled = false;
    });
  }
  function startNewGroup() {
    state.activeGroupId = "";
    $("groupName").value = "";
    renderGroups();
    $("groupName").focus();
    showToast("已新建组状态，可保存当前组装顺序");
  }
  function loadGroup(groupId) {
    var group = state.groups.find(function (item) { return item.id === groupId; });
    if (!group) return;
    state.activeGroupId = group.id;
    $("groupName").value = group.name;
    state.stage = (group.items || []).map(stageItemFromApi).filter(Boolean);
    saveState();
    renderStage();
    renderGroups();
    state.assemblyView = "stage";
    renderAssemblyView();
    showToast("已加载「" + group.name + "」");
  }
  function deleteGroup(groupId) {
    var group = state.groups.find(function (item) { return item.id === groupId; });
    if (!group || !window.confirm("删除组状态「" + group.name + "」吗？组装台不会改变。")) return;
    jsonRequest("/api/prompt/groups/" + encodeURIComponent(groupId), "DELETE").then(function () {
      state.groups = state.groups.filter(function (item) { return item.id !== groupId; });
      if (state.activeGroupId === groupId) {
        state.activeGroupId = "";
        $("groupName").value = "";
      }
      renderGroups();
      showToast("组状态已删除");
    }).catch(function (error) {
      showToast("组状态删除失败：" + error.message, true);
    });
  }
  function openCustomModal(block) {
    editingBlockId = block ? block.id : "";
    editingStageIndex = null;
    $("customBlockTitle").textContent = editingBlockId ? "编辑固定积木" : "添加固定积木";
    $("customBlockModal").querySelector(".section-kicker").textContent = editingBlockId ? "EDIT BLOCK" : "CUSTOM BLOCK";
    $("customBlockDescription").textContent = "把你经常重复使用的表达保存下来，下次直接从积木库加入；也可以修改已有积木的内容。";
    $("customBlockForm").querySelector('button[type="submit"]').textContent = editingBlockId ? "保存修改" : "保存积木";
    $("customBlockForm").reset();
    if (block) {
      $("customBlockName").value = block.title || "";
      $("customBlockText").value = block.text || "";
      $("customBlockTags").value = (block.tags || []).join("，");
    }
    window.RHMotion.openModal("customBlockModal", "customBlockName");
  }
  function closeCustomModal() {
    window.RHMotion.closeModal("customBlockModal");
    editingBlockId = "";
    editingStageIndex = null;
    $("customBlockTitle").textContent = "添加固定积木";
    $("customBlockModal").querySelector(".section-kicker").textContent = "CUSTOM BLOCK";
    $("customBlockDescription").textContent = "把你经常重复使用的表达保存下来，下次直接从积木库加入；也可以修改已有积木的内容。";
    $("customBlockForm").querySelector('button[type="submit"]').textContent = "保存积木";
    $("customBlockForm").reset();
  }
  function editBlock(blockId) {
    var block = state.libraryBlocks.find(function (item) { return item.id === blockId; });
    if (block) openCustomModal(block);
  }
  function copyPrompt() {
    var output = getPromptText();
    if (!output) return showToast("组装台还没有可复制的文本", true);
    var copyPromise = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(output) : Promise.reject(new Error("clipboard unavailable"));
    copyPromise.then(function () {
      showToast("提示词已复制到剪贴板");
    }).catch(function () {
      var fallback = document.createElement("textarea");
      fallback.value = output;
      fallback.setAttribute("readonly", "true");
      fallback.style.position = "fixed";
      fallback.style.opacity = "0";
      document.body.appendChild(fallback);
      fallback.select();
      var copied = false;
      try { copied = document.execCommand("copy"); } catch (error) { copied = false; }
      fallback.remove();
      showToast(copied ? "提示词已复制到剪贴板" : "复制失败，请直接选择右侧文本", !copied);
    });
  }
  function downloadPrompt() {
    var output = getPromptText();
    if (!output) return showToast("组装台还没有可导出的文本", true);
    var blob = new Blob(["\ufeff" + output], { type: "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = "prompt-" + new Date().toISOString().slice(0, 10) + ".txt";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
    showToast("TXT 文件已开始下载");
  }
  function openImagePreview(src, title) {
    if (!src) return;
    var image = $("imagePreviewImage");
    image.src = src;
    image.alt = title || "动作图片";
    $("imagePreviewTitle").textContent = title || "动作图片";
    $("imagePreviewCaption").textContent = "点击图片外区域、右上角或按 Esc 关闭";
    window.RHMotion.openModal("imagePreviewModal", "closeImagePreview");
  }
  function closeImagePreview() {
    window.RHMotion.closeModal("imagePreviewModal");
  }
  function importPromptToTask() {
    var output = getPromptText();
    if (!output) return showToast("组装台还没有可导入的文本", true);
    try {
      localStorage.setItem(TASK_PROMPT_IMPORT_KEY, JSON.stringify({ version: 1, text: output, createdAt: Date.now() }));
      window.location.href = "/";
    } catch (error) {
      showToast("导入失败：无法保存本机跳转数据", true);
    }
  }
  function bindEvents() {
    updateThemeToggle();
    initPromptGridSplitter();
    $("themeToggle").addEventListener("click", function () {
      var nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem("rh-workflow-theme", nextTheme); } catch (error) {}
      updateThemeToggle();
    });
    $("assemblyViewStage").addEventListener("click", function () {
      state.assemblyView = "stage";
      renderAssemblyView();
    });
    $("assemblyViewGroups").addEventListener("click", function () {
      state.assemblyView = "groups";
      renderGroups();
      renderAssemblyView();
    });
    document.querySelector(".library-mode-tabs").addEventListener("click", function (event) {
      var button = event.target.closest("[data-library-mode]");
      if (!button) return;
      var nextMode = button.dataset.libraryMode;
      if (nextMode === state.libraryMode) return;
      state.libraryMode = nextMode;
      state.filter = "全部";
      renderFilters();
      renderLibrary();
      animateLibraryModeSwitch();
      if (nextMode === "pose" || nextMode === "actions") return refreshActions();
      if (isReferenceMode()) return refreshReferences();
    });
    $("refreshActions").addEventListener("click", function () {
      if (state.libraryMode === "pose" || state.libraryMode === "actions") return refreshActions();
      if (isReferenceMode()) return refreshReferences();
    });
    $("blockSearch").addEventListener("input", function () { state.search = this.value.trim(); renderLibrary(); });
    $("tagFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-filter-tag]");
      if (!button) return;
      state.filter = button.dataset.filterTag;
      renderFilters();
      renderLibrary();
    });
    $("groupList").addEventListener("click", function (event) {
      var loadButton = event.target.closest("[data-load-group]");
      if (loadButton) return loadGroup(loadButton.dataset.loadGroup);
      var deleteButton = event.target.closest("[data-delete-group]");
      if (deleteButton) deleteGroup(deleteButton.dataset.deleteGroup);
    });
    $("libraryList").addEventListener("click", function (event) {
      var mediaToggle = event.target.closest("[data-action-media-toggle]");
      if (mediaToggle) {
        var mediaContainer = mediaToggle.closest("[data-action-media]");
        return setActionMediaView(mediaContainer, mediaToggle.dataset.actionMediaCurrent === "depth" ? "color" : "depth");
      }
      var audioButton = event.target.closest("[data-audio-toggle]");
      if (audioButton) return toggleReferenceAudio(audioButton);
      var previewButton = event.target.closest("[data-image-preview]");
      if (previewButton) return openImagePreview(previewButton.dataset.imagePreview, previewButton.dataset.imageTitle);
      var workflowImportButton = event.target.closest("[data-import-workflow]");
      if (workflowImportButton) return openWorkflowImportFromTrigger(workflowImportButton);
      var importButton = event.target.closest("[data-import-depth]");
      if (importButton) {
        var action = state.actions.find(function (item) { return item.id === importButton.dataset.importDepth; });
        return openDepthImport(action);
      }
      var actionButton = event.target.closest("[data-add-action]");
      if (actionButton) return addAction(actionButton.dataset.addAction);
      var referenceButton = event.target.closest("[data-add-reference]");
      if (referenceButton) return addReference(referenceButton.dataset.addReference);
      var addButton = event.target.closest("[data-add-block]");
      if (addButton) return addFixedBlock(addButton.dataset.addBlock);
      if (event.target.closest("[data-add-text-block]")) return addTextBlock();
      var editButton = event.target.closest("[data-edit-block]");
      if (editButton) return editBlock(editButton.dataset.editBlock);
      var deleteButton = event.target.closest("[data-delete-block]");
      if (!deleteButton) return;
      var blockId = deleteButton.dataset.deleteBlock;
      if (!window.confirm("删除这块积木吗？已经加入组装台的内容不会改变。")) return;
      deleteButton.disabled = true;
      jsonRequest("/api/prompt/library/" + encodeURIComponent(blockId), "DELETE").then(function () {
        state.libraryBlocks = state.libraryBlocks.filter(function (block) { return block.id !== blockId; });
        renderAll();
        showToast("积木已删除");
      }).catch(function (error) {
        deleteButton.disabled = false;
        showToast("积木删除失败：" + error.message, true);
      });
    });
    $("libraryList").addEventListener("dragstart", function (event) {
      var card = event.target.closest(".library-block, .action-library-card, .reference-library-card");
      var freeCard = event.target.closest("[data-add-text-block]");
      if (!card && !freeCard) return;
      if (card && event.target.closest("button")) return event.preventDefault();
      state.draggedIndex = null;
      state.draggedLibraryId = card ? (card.dataset.libraryBlockId || card.dataset.actionId || card.dataset.referenceId) : "__free_text__";
      state.dragPreviewIndex = null;
      clearDropIndicators();
      (card || freeCard).classList.add("dragging");
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("text/plain", "library:" + state.draggedLibraryId);
    });
    $("libraryList").addEventListener("dragend", function () {
      state.draggedLibraryId = "";
      clearDropIndicators();
      document.querySelectorAll(".library-block, .action-library-card, .reference-library-card, .free-block-card").forEach(function (card) { card.classList.remove("dragging"); });
    });
    $("stageList").addEventListener("input", function (event) {
      var editor = event.target.closest("[data-stage-text]");
      if (!editor) return;
      var index = Number(editor.dataset.stageText);
      if (!state.stage[index]) return;
      state.stage[index].text = editor.value;
      saveState();
      renderOutput();
    });
    $("stageList").addEventListener("click", function (event) {
      var mediaToggle = event.target.closest("[data-action-media-toggle]");
      if (mediaToggle) {
        var mediaContainer = mediaToggle.closest("[data-action-media]");
        return setActionMediaView(mediaContainer, mediaToggle.dataset.actionMediaCurrent === "depth" ? "color" : "depth");
      }
      var audioButton = event.target.closest("[data-audio-toggle]");
      if (audioButton) return toggleReferenceAudio(audioButton);
      var previewButton = event.target.closest("[data-image-preview]");
      if (previewButton) return openImagePreview(previewButton.dataset.imagePreview, previewButton.dataset.imageTitle);
      var workflowImportButton = event.target.closest("[data-import-workflow]");
      if (workflowImportButton) return openWorkflowImportFromTrigger(workflowImportButton);
      var importButton = event.target.closest("[data-import-depth]");
      if (importButton) {
        var action = state.actions.find(function (item) { return item.id === importButton.dataset.importDepth; });
        return openDepthImport(action);
      }
      var editButton = event.target.closest("[data-edit-stage]");
      if (editButton) return editStage(Number(editButton.dataset.editStage));
      var removeButton = event.target.closest("[data-remove-stage]");
      if (removeButton) removeStage(Number(removeButton.dataset.removeStage));
    });
    $("stageList").addEventListener("pointerdown", function (event) {
      var card = event.target.closest(".stage-block");
      if (!card || event.target.closest("button, textarea, input, select, a")) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      state.draggedIndex = Number(card.dataset.stageIndex);
      state.draggedLibraryId = "";
      clearDropIndicators();
      state.pointerDrag = { card: card, pointerId: event.pointerId, x: event.clientX, y: event.clientY, overLibrary: false };
      card.classList.add("dragging");
      if (card.setPointerCapture) card.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    document.addEventListener("pointermove", function (event) {
      var drag = state.pointerDrag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      drag.x = event.clientX;
      drag.y = event.clientY;
      event.preventDefault();
      var libraryTarget = libraryDropTargetAtPointer(event.clientX, event.clientY);
      if (libraryTarget) {
        if (!drag.overLibrary) clearDropIndicators();
        drag.overLibrary = true;
        libraryTarget.classList.add("is-stage-drop-target");
        return;
      }
      if (drag.overLibrary) {
        drag.overLibrary = false;
        clearLibraryDropTarget();
      }
      var card = stageCardAtPointer(event, drag.card);
      if (card && card !== drag.card) setStageDropPreview(card, event);
    });
    document.addEventListener("pointerup", function (event) {
      if (!state.pointerDrag || state.pointerDrag.pointerId !== event.pointerId) return;
      state.pointerDrag.x = event.clientX;
      state.pointerDrag.y = event.clientY;
      event.preventDefault();
      finishPointerStageDrag(true);
    });
    document.addEventListener("pointercancel", function (event) {
      if (!state.pointerDrag || state.pointerDrag.pointerId !== event.pointerId) return;
      finishPointerStageDrag(false);
    });
    $("stageList").addEventListener("dragover", function (event) {
      var card = event.target.closest("[data-stage-index]");
      var hasLibrarySource = Boolean(state.draggedLibraryId);
      var stageSurface = event.target === event.currentTarget;
      if (!hasLibrarySource) return;
      if (card) {
        event.preventDefault();
        setLibraryDropPreview(card, event);
        return;
      }
      if (stageSurface && state.dragPreviewIndex != null) {
        event.preventDefault();
        return;
      }
      var placeholder = event.target.closest(".drag-placeholder");
      if (hasLibrarySource && placeholder) {
        event.preventDefault();
        placeholder.classList.add("drop-target");
        return;
      }
      var emptyStage = event.target.closest(".stage-empty");
      if (hasLibrarySource && emptyStage) {
        event.preventDefault();
        clearDropIndicators();
        emptyStage.classList.add("drop-target");
      }
    });
    $("stageList").addEventListener("dragleave", function (event) {
      var card = event.target.closest("[data-stage-index]");
      if (card && !card.contains(event.relatedTarget)) card.classList.remove("drop-target");
      var emptyStage = event.target.closest(".stage-empty");
      if (emptyStage && !emptyStage.contains(event.relatedTarget)) emptyStage.classList.remove("drop-target");
    });
    $("stageList").addEventListener("drop", function (event) {
      var card = event.target.closest("[data-stage-index]");
      var placeholder = event.target.closest(".drag-placeholder");
      var emptyStage = event.target.closest(".stage-empty");
      var stageSurface = event.target === event.currentTarget;
      if (state.draggedLibraryId && (card || placeholder || emptyStage || (stageSurface && state.dragPreviewIndex != null))) {
        event.preventDefault();
        var libraryId = state.draggedLibraryId;
        var insertIndex = state.dragPreviewIndex == null ? state.stage.length : state.dragPreviewIndex;
        if (card && state.dragPreviewIndex == null) {
          var libraryDropRect = card.getBoundingClientRect();
          var libraryTargetIndex = Number(card.dataset.stageIndex);
          insertIndex = libraryTargetIndex + (event.clientY > libraryDropRect.top + libraryDropRect.height / 2 ? 1 : 0);
        }
        state.draggedLibraryId = "";
        clearDropIndicators();
        insertLibraryBlock(libraryId, insertIndex);
      }
    });
    $("clearStage").addEventListener("click", function () {
      if (!state.stage.length) return showToast("组装台已经是空的");
      if (!window.confirm("清空当前组装台吗？积木库和自定义积木不会受影响。")) return;
      state.stage = [];
      saveState();
      renderStage();
      showToast("组装台已清空");
    });
    $("copyPrompt").addEventListener("click", copyPrompt);
    $("importPrompt").addEventListener("click", importPromptToTask);
    $("downloadPrompt").addEventListener("click", downloadPrompt);
    $("addTextStage").addEventListener("click", addTextBlock);
    $("newGroup").addEventListener("click", startNewGroup);
    $("groupForm").addEventListener("submit", function (event) {
      event.preventDefault();
      saveGroup();
    });
    $("openCustomBlock").addEventListener("click", openCustomModal);
    $("closeCustomBlock").addEventListener("click", closeCustomModal);
    $("cancelCustomBlock").addEventListener("click", closeCustomModal);
    $("customBlockModal").addEventListener("click", function (event) { if (event.target === $("customBlockModal")) closeCustomModal(); });
    $("closeImagePreview").addEventListener("click", closeImagePreview);
    $("imagePreviewModal").addEventListener("click", function (event) { if (event.target === $("imagePreviewModal")) closeImagePreview(); });
    $("closeDepthImport").addEventListener("click", closeDepthImport);
    $("cancelDepthImport").addEventListener("click", closeDepthImport);
    $("confirmDepthImport").addEventListener("click", confirmDepthImport);
    $("depthImportTargets").addEventListener("change", function (event) {
      if (event.target.name === "depth-import-target") $("confirmDepthImport").disabled = false;
    });
    $("depthImportModal").addEventListener("click", function (event) { if (event.target === $("depthImportModal")) closeDepthImport(); });
    $("customBlockForm").addEventListener("submit", function (event) {
      event.preventDefault();
      var name = $("customBlockName").value.trim();
      var text = $("customBlockText").value.trim();
      if (!name || !text) return showToast("请填写积木名称和固定文本", true);
      var stageIndex = editingStageIndex;
      if (stageIndex != null) {
        var stageItem = state.stage[stageIndex];
        if (!stageItem) return closeCustomModal();
        stageItem.title = name;
        stageItem.text = text;
        stageItem.tags = parseTags($("customBlockTags").value);
        closeCustomModal();
        saveState();
        renderStage();
        showToast("组装台积木已更新，积木库未改变");
        return;
      }
      var blockId = editingBlockId;
      var submitButton = event.target.querySelector('button[type="submit"]');
      submitButton.disabled = true;
      var endpoint = blockId ? "/api/prompt/library/" + encodeURIComponent(blockId) : "/api/prompt/library";
      var method = blockId ? "PUT" : "POST";
      jsonRequest(endpoint, method, { title: name, text: text, tags: parseTags($("customBlockTags").value) }).then(function (data) {
        if (blockId) {
          var index = state.libraryBlocks.findIndex(function (item) { return item.id === blockId; });
          if (index !== -1) state.libraryBlocks[index] = data.block;
          state.stage.forEach(function (item) {
            if (item.kind === "fixed" && item.sourceId === blockId) {
              item.title = data.block.title;
              item.text = data.block.text;
              item.tags = data.block.tags || [];
              item.missing = false;
            }
          });
        } else {
          state.libraryBlocks.push(data.block);
        }
        closeCustomModal();
        state.filter = "全部";
        renderAll();
        showToast(blockId ? "固定积木已更新" : "固定积木已保存到 JSON 库");
      }).catch(function (error) {
        showToast("积木保存失败：" + error.message, true);
      }).finally(function () {
        submitButton.disabled = false;
      });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      closeCustomModal();
      closeImagePreview();
      closeDepthImport();
    });
  }

  bindEvents();
  loadState().then(function () {
    renderAll();
  }).catch(function (error) {
    renderAll();
    showToast("积木数据读取失败：" + error.message, true);
  });
})();
