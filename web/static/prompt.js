(function () {
  "use strict";

  var STORAGE_KEY = "rh-workflow-desk-prompt-builder-v1";
  var TASK_PROMPT_IMPORT_KEY = "rh-workflow-desk-pending-prompt-v1";
  var TASK_PROMPT_GROUP_IMPORT_KEY = "rh-workflow-desk-pending-prompt-group-v1";
  var draftStorageKey = "rh-workflow-desk-draft-v1";
  var idCounter = 0;
  var promptApiReady = false;
  var stateSaveTimer = 0;
  var editingBlockId = "";
  var editingStageIndex = null;
  var editingResourceKind = "";
  var editingResourceId = "";
  var pendingResourceMedia = {};
  var activeResourceMediaRole = "image";
  var depthGenerationBusy = false;
  var visionRecognitionBusy = false;
  var depthImportActionId = "";
  var workflowImportAsset = null;
  var GRID_SPLITTER_STORAGE_KEY = "rh-workflow-desk-prompt-library-width-v2";
  var gridSplitterDrag = null;
  var referenceSuggest = { open: false, editor: null, item: null, range: null, query: "", candidates: [], selectedIndex: 0 };
  var referenceHover = { token: null, candidate: null };
  var referenceComposing = false;
  var mediaBlockPickerBusy = false;
  var activeMediaStageIndex = null;
  var mediaPreviewRequests = {};
  var REFERENCE_SENTINEL_PREFIX = "__RH_REF_";
  var REFERENCE_MODES = ["character", "audio", "background", "clothes"];
  var RESOURCE_LABELS = { action: "动作", character: "人物", audio: "音频", background: "背景", clothes: "服装" };
  var PROMPT_STRUCTURE_FIELDS = ["subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music"];
  var RESOURCE_MEDIA_SLOTS = {
    action: [
      { role: "color", label: "原图", accept: "image/*", pathId: "resourceImagePath", paste: true },
      { role: "depth", label: "深度图", accept: "image/*", pathId: "resourceDepthPath", paste: true, autoDepth: true },
    ],
    character: [{ role: "image", label: "人物图片", accept: "image/*", pathId: "resourceImagePath", paste: true }],
    background: [{ role: "image", label: "背景图片", accept: "image/*", pathId: "resourceImagePath", paste: true }],
    clothes: [{ role: "image", label: "服装图片", accept: "image/*", pathId: "resourceImagePath", paste: true }],
    audio: [{ role: "audio", label: "音频文件", accept: "audio/*", pathId: "resourceAudioPath", paste: false }],
  };
  var state = { libraryBlocks: [], actions: [], actionSource: null, references: [], referenceSource: null, libraryMode: "blocks", assemblyView: "stage", libraryExpanded: false, stage: [], groups: [], activeGroupId: "", categoryFilter: "全部", filter: "全部", search: "", draggedIndex: null, draggedLibraryId: "", dragPreviewIndex: null, dragPreviewFrames: [], pointerDrag: null };

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
  function referenceSnapshot(candidate) {
    if (!candidate) return {};
    return {
      title: candidate.title || "",
      text: candidate.text || "",
      tags: Array.isArray(candidate.tags) ? candidate.tags.slice() : [],
      category: candidate.category || "",
      color_image_url: candidate.colorImageUrl || candidate.imageUrl || "",
      depth_image_url: candidate.depthImageUrl || "",
      pair_status: candidate.pairStatus || "",
      color_image_path: candidate.colorImagePath || "",
      depth_image_path: candidate.depthImagePath || "",
      image_url: candidate.imageUrl || "",
      image_path: candidate.imagePath || "",
      audio_url: candidate.audioUrl || "",
      audio_path: candidate.audioPath || "",
      media_type: candidate.mediaType || "",
      reference_kind: candidate.referenceKind || "",
    };
  }
  function referenceCandidate(sourceType, sourceId) {
    var id = String(sourceId || "");
    if (!id) return null;
    if (sourceType === "block") {
      var block = allBlocks().find(function (item) { return String(item.id) === id; });
      if (!block) return null;
      return { key: "block:" + id, sourceType: "block", sourceId: id, title: block.title, text: block.text || "", tags: block.tags || [], category: block.category || "未分类", sourceLabel: "基础积木" };
    }
    if (sourceType === "action") {
      var action = state.actions.find(function (item) { return String(item.id) === id; });
      if (!action) return null;
      return { key: "action:" + id, sourceType: "action", sourceId: id, title: action.title, text: action.text || "", tags: action.tags || [], category: action.category || "未分类", colorImageUrl: action.color_image_url || action.image_url || "", depthImageUrl: action.depth_image_url || "", colorImagePath: action.color_image_path || action.image_path || "", depthImagePath: action.depth_image_path || "", pairStatus: action.pair_status || "", sourceLabel: "动作库" };
    }
    var reference = state.references.find(function (item) { return String(item.id) === id; });
    if (!reference) return null;
    return { key: "reference:" + id, sourceType: "reference", sourceId: id, referenceKind: reference.kind || "", title: reference.title, text: reference.text || "", tags: reference.tags || [], category: reference.category || "未分类", imageUrl: reference.image_url || "", imagePath: reference.image_path || "", audioUrl: reference.audio_url || "", audioPath: reference.audio_path || "", mediaType: reference.media_type || "", sourceLabel: RESOURCE_LABELS[reference.kind] || "参考资源库" };
  }
  function referenceCandidates() {
    var candidates = [];
    allBlocks().forEach(function (block) {
      var candidate = referenceCandidate("block", block.id);
      if (candidate) candidates.push(candidate);
    });
    state.actions.forEach(function (action) {
      var candidate = referenceCandidate("action", action.id);
      if (candidate) candidates.push(candidate);
    });
    state.references.forEach(function (reference) {
      var candidate = referenceCandidate("reference", reference.id);
      if (candidate) candidates.push(candidate);
    });
    return candidates;
  }
  function normalizeSegments(value) {
    if (!Array.isArray(value)) return null;
    return value.map(function (segment) {
      if (!segment || segment.type === "text") return { type: "text", text: String(segment && segment.text || "") };
      if (segment.type !== "reference") return null;
      var sourceType = String(segment.source_type || segment.sourceType || "");
      var sourceId = String(segment.source_id || segment.sourceId || "");
      if (["block", "action", "reference"].indexOf(sourceType) === -1 || !sourceId) return null;
      return {
        type: "reference",
        sourceType: sourceType,
        sourceId: sourceId,
        label: String(segment.label || ""),
        snapshot: segment.snapshot && typeof segment.snapshot === "object" ? segment.snapshot : {},
      };
    }).filter(Boolean);
  }
  function itemSegments(item) {
    if (Array.isArray(item.segments)) return item.segments;
    return item.text ? [{ type: "text", text: String(item.text) }] : [];
  }
  function segmentCandidate(segment) {
    if (!segment || segment.type !== "reference") return null;
    return referenceCandidate(segment.sourceType || segment.source_type, segment.sourceId || segment.source_id) || {
      key: (segment.sourceType || segment.source_type || "reference") + ":" + (segment.sourceId || segment.source_id || ""),
      sourceType: segment.sourceType || segment.source_type || "reference",
      sourceId: segment.sourceId || segment.source_id || "",
      referenceKind: segment.snapshot && segment.snapshot.reference_kind || "",
      title: segment.label || segment.snapshot && segment.snapshot.title || "参考卡片已不可用",
      text: segment.snapshot && segment.snapshot.text || "",
      tags: segment.snapshot && Array.isArray(segment.snapshot.tags) ? segment.snapshot.tags : [],
      colorImagePath: segment.snapshot && (segment.snapshot.color_image_path || segment.snapshot.image_path) || "",
      depthImagePath: segment.snapshot && segment.snapshot.depth_image_path || "",
      imageUrl: segment.snapshot && (segment.snapshot.image_url || segment.snapshot.color_image_url) || "",
      imagePath: segment.snapshot && segment.snapshot.image_path || "",
      audioUrl: segment.snapshot && segment.snapshot.audio_url || "",
      audioPath: segment.snapshot && segment.snapshot.audio_path || "",
      mediaType: segment.snapshot && segment.snapshot.media_type || "",
      sourceLabel: "历史快照",
      missing: true,
    };
  }
  function segmentLabel(segment) {
    var candidate = segmentCandidate(segment);
    return candidate && candidate.title || segment.label || "参考卡片";
  }
  function segmentPromptText(segment) {
    var candidate = segmentCandidate(segment);
    return candidate ? String(candidate.text || "") : "";
  }
  function sourceTextFromSegments(segments) {
    return (segments || []).map(function (segment) {
      return segment.type === "reference" ? "@" + segmentLabel(segment) : String(segment.text || "");
    }).join("");
  }
  function literalTextFromItem(item) {
    if (Array.isArray(item.segments)) return item.segments.filter(function (segment) { return segment.type === "text"; }).map(function (segment) { return String(segment.text || ""); }).join("").trim();
    return String(item.text || "").trim();
  }
  function translationTemplate(item) {
    if (!Array.isArray(item.segments)) return String(item.text || "");
    return item.segments.map(function (segment, index) {
      return segment.type === "reference" ? REFERENCE_SENTINEL_PREFIX + index + "__" : String(segment.text || "");
    }).join("");
  }
  function resolveTranslationTemplate(item, value) {
    var output = String(value || "");
    if (!Array.isArray(item.segments)) return output;
    item.segments.forEach(function (segment, index) {
      var token = REFERENCE_SENTINEL_PREFIX + index + "__";
      output = output.split(token).join(segmentPromptText(segment));
    });
    return output;
  }
  function promptTextForStageItem(item) {
    if (item.kind !== "text") return String(item.text || "");
    if (item.translationDisabled) return Array.isArray(item.segments) ? resolveTranslationTemplate(item, translationTemplate(item)) : String(item.text || "");
    if (item.translatedText) return resolveTranslationTemplate(item, item.translatedText);
    if (literalTextFromItem(item)) return "";
    return sourceTextFromSegments(item.segments || []);
  }
  function getPromptText() {
    return state.stage.map(function (item) {
      return promptTextForStageItem(item).trim();
    }).filter(Boolean).join("\n\n");
  }
  function pendingTranslationItems() {
    return state.stage.filter(function (item) {
      return item.kind === "text" && !item.translationDisabled && literalTextFromItem(item) && !String(item.translatedText || "").trim();
    });
  }
  function promptTextForAction(action) {
    var pending = pendingTranslationItems();
    if (pending.length) {
      showToast("请先翻译所有自由文本，再" + action + "提示词", true);
      return "";
    }
    var output = getPromptText();
    if (!output) showToast("组装台还没有可" + action + "的文本", true);
    return output;
  }
  function showToast(message, isError) {
    var toast = $("promptToast");
    if (window.RHMotion && window.RHMotion.showToast) window.RHMotion.showToast(toast, message, isError);
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
  function setLibraryExpanded(expanded) {
    if (window.matchMedia && window.matchMedia("(max-width: 820px)").matches) return;
    state.libraryExpanded = Boolean(expanded);
    var grid = $("promptBuilderGrid");
    var toggle = $("toggleLibraryExpand");
    if (grid) grid.classList.toggle("is-library-expanded", state.libraryExpanded);
    if (toggle) {
      toggle.setAttribute("aria-expanded", state.libraryExpanded ? "true" : "false");
      toggle.setAttribute("title", state.libraryExpanded ? "恢复提示词工作台和成品提示词" : "展开积木库");
    }
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
  function segmentToApi(segment) {
    if (!segment || segment.type !== "reference") {
      return { type: "text", text: String(segment && segment.text || "") };
    }
    var result = {
      type: "reference",
      source_type: String(segment.sourceType || segment.source_type || ""),
      source_id: String(segment.sourceId || segment.source_id || ""),
      label: String(segment.label || ""),
    };
    if (segment.snapshot && typeof segment.snapshot === "object") result.snapshot = segment.snapshot;
    return result;
  }
  function stageItemToApi(item) {
    var result = { instance_id: item.instanceId, kind: item.kind };
    if (item.kind === "text") {
      result.text = Array.isArray(item.segments) ? sourceTextFromSegments(item.segments) : String(item.text || "");
      result.translated_text = String(item.translatedText || "");
      if (item.translationDisabled) result.translation_disabled = true;
      if (item.generatedType) result.generated_type = String(item.generatedType);
      if (Array.isArray(item.segments)) result.segments = item.segments.map(segmentToApi);
    } else if (item.kind === "media") {
      result.media_path = String(item.mediaPath || "");
      result.media_name = String(item.mediaName || item.title || "媒体积木");
      result.media_kind = String(item.mediaKind || item.previewKind || "");
      result.media_mime = String(item.mediaMime || "");
    } else if (item.kind === "action") {
      result.action_id = item.sourceId || "";
      result.snapshot = {
        category: item.category || "",
        title: item.title || "",
        text: item.text || "",
        tags: item.tags || [],
        color_image_url: item.colorImageUrl || item.imageUrl || "",
        color_image_path: item.colorImagePath || item.imagePath || "",
        depth_image_url: item.depthImageUrl || "",
        depth_image_path: item.depthImagePath || "",
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
        image_path: item.imagePath || "",
        audio_url: item.audioUrl || "",
        audio_path: item.audioPath || "",
        media_type: item.mediaType || "",
      };
    } else {
      result.block_id = item.sourceId || "";
      result.snapshot = { title: item.title || "", text: item.text || "", tags: item.tags || [] };
    }
    return result;
  }
  function stageItemFromApi(item) {
    if (!item || (item.kind !== "text" && item.kind !== "fixed" && item.kind !== "action" && item.kind !== "reference" && item.kind !== "media")) return null;
    if (item.kind === "text") {
      var segments = normalizeSegments(item.segments);
      var generatedType = String(item.generated_type || item.generatedType || "");
      var textItem = { instanceId: item.instance_id || makeId("text"), kind: "text", title: "自由文本", text: String(item.text || ""), translatedText: String(item.translated_text || item.translatedText || ""), translationDisabled: Boolean(item.translation_disabled || item.translationDisabled || generatedType === "subject_definitions"), generatedType: generatedType, tags: [] };
      if (segments) textItem.segments = segments;
      return textItem;
    }
    if (item.kind === "media") {
      var mediaPath = String(item.media_path || item.mediaPath || item.path || "");
      var mediaName = String(item.media_name || item.mediaName || item.name || filenameFromPath(mediaPath) || "媒体积木");
      var mediaKind = String(item.media_kind || item.mediaKind || item.media_type || item.mediaType || mediaKindFromPath(mediaPath) || "");
      return {
        instanceId: item.instance_id || makeId("media"),
        kind: "media",
        title: mediaName,
        mediaPath: mediaPath,
        mediaName: mediaName,
        mediaKind: mediaKind,
        mediaMime: String(item.media_mime || item.mediaMime || item.mime || ""),
        previewKind: mediaKind,
        previewUrl: "",
      };
    }
    var sourceId = item.kind === "action" ? (item.action_id || item.block_id) : (item.kind === "reference" ? item.reference_id : item.block_id);
    var source = item.kind === "action" ? state.actions.find(function (candidate) { return candidate.id === sourceId; }) : (item.kind === "reference" ? state.references.find(function (candidate) { return candidate.id === sourceId; }) : allBlocks().find(function (candidate) { return candidate.id === sourceId; }));
    var snapshot = item.snapshot || {};
    var hasSnapshotCategory = Object.prototype.hasOwnProperty.call(snapshot, "category");
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
        imagePath: source ? (source.image_path || "") : (snapshot.image_path || ""),
        audioUrl: source ? (source.audio_url || "") : (snapshot.audio_url || ""),
        audioPath: source ? (source.audio_path || "") : (snapshot.audio_path || ""),
        mediaType: source ? (source.media_type || "") : (snapshot.media_type || ""),
        missing: !source,
      };
    }
    return {
      instanceId: item.instance_id || makeId(item.kind),
      kind: item.kind,
      sourceId: sourceId || "",
      category: item.kind === "action"
        ? (hasSnapshotCategory ? String(snapshot.category || "未分类") : (source ? source.category || "未分类" : "未分类"))
        : "",
      title: hasSnapshotTitle ? String(snapshot.title || "") : (source ? source.title : (item.kind === "action" ? "动作已不可用" : "已删除积木")),
      text: hasSnapshotText ? String(snapshot.text || "") : (source ? source.text : ""),
      tags: hasSnapshotTags ? (Array.isArray(snapshot.tags) ? snapshot.tags : []) : (source ? (source.tags || []) : []),
      imageUrl: source ? (source.color_image_url || source.image_url || "") : (snapshot.color_image_url || ""),
      colorImageUrl: source ? (source.color_image_url || source.image_url || "") : (snapshot.color_image_url || ""),
      imagePath: source ? (source.color_image_path || source.image_path || "") : (snapshot.color_image_path || snapshot.image_path || ""),
      colorImagePath: source ? (source.color_image_path || source.image_path || "") : (snapshot.color_image_path || snapshot.image_path || ""),
      depthImageUrl: source ? (source.depth_image_url || "") : (snapshot.depth_image_url || ""),
      depthImagePath: source ? (source.depth_image_path || "") : (snapshot.depth_image_path || ""),
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
  function applyPendingTaskPromptGroup() {
    var pending = null;
    try {
      var raw = window.localStorage.getItem(TASK_PROMPT_GROUP_IMPORT_KEY);
      if (raw) pending = JSON.parse(raw);
    } catch (error) {
      pending = null;
    }
    var group = pending && pending.version === 1 && pending.group && typeof pending.group === "object" ? pending.group : null;
    if (!group || !Array.isArray(group.items)) return false;
    state.stage = group.items.map(stageItemFromApi).filter(Boolean);
    state.activeGroupId = "";
    state.assemblyView = "stage";
    if ($("groupName")) $("groupName").value = String(group.name || "任务提交时组装台");
    saveState();
    try { window.localStorage.removeItem(TASK_PROMPT_GROUP_IMPORT_KEY); } catch (error) {}
    showToast("已从任务快照加载组状态「" + (group.name || "任务提交时组装台") + "」");
    return true;
  }
  function blockMatches(block) {
    var needle = state.search.toLowerCase();
    var tags = block.tags || [];
    var category = String(block.category || "未分类");
    var supportsCategory = state.libraryMode === "blocks" || state.libraryMode === "pose" || state.libraryMode === "actions" || isReferenceMode();
    var matchesCategory = !supportsCategory || state.categoryFilter === "全部" || category === state.categoryFilter;
    var matchesTag = state.filter === "全部" || tags.indexOf(state.filter) !== -1;
    var matchesSearch = !needle || [category, block.title, block.text].concat(tags).join(" ").toLowerCase().indexOf(needle) !== -1;
    return matchesCategory && matchesTag && matchesSearch;
  }
  function searchEntryRank(entry) {
    var needle = String(state.search || "").trim().toLocaleLowerCase();
    if (!needle) return 0;
    var category = String(entry.category || "未分类").toLocaleLowerCase();
    var title = String(entry.title || "").toLocaleLowerCase();
    var tags = (entry.tags || []).map(function (tag) { return String(tag).toLocaleLowerCase(); });
    var text = String(entry.text || "").toLocaleLowerCase();
    if (category.indexOf(needle) === 0) return 0;
    if (category.indexOf(needle) !== -1) return 1;
    if (title.indexOf(needle) === 0) return 2;
    if (title.indexOf(needle) !== -1) return 3;
    if (tags.some(function (tag) { return tag.indexOf(needle) === 0; })) return 4;
    if (tags.some(function (tag) { return tag.indexOf(needle) !== -1; })) return 5;
    if (text.indexOf(needle) !== -1) return 6;
    if ([category, title].concat(tags, [text]).some(function (value) { return fuzzyMatchScore(needle, value) >= 0; })) return 7;
    return 99;
  }
  function sortSearchEntries(entries) {
    return entries.map(function (entry, index) { return { entry: entry, rank: searchEntryRank(entry), index: index }; }).sort(function (left, right) {
      return left.rank - right.rank || left.index - right.index;
    }).map(function (item) { return item.entry; });
  }
  function renderCategoryFilters() {
    var container = $("categoryFilters");
    if (!container) return;
    if (state.libraryMode !== "blocks" && state.libraryMode !== "pose" && state.libraryMode !== "actions" && !isReferenceMode()) {
      container.innerHTML = "";
      return;
    }
    var categories = unique(currentLibraryEntries().map(function (entry) { return String(entry.category || "未分类"); }));
    if (state.categoryFilter !== "全部" && categories.indexOf(state.categoryFilter) === -1) state.categoryFilter = "全部";
    container.innerHTML = ["全部"].concat(categories).map(function (category) {
      return '<button class="category-filter' + (state.categoryFilter === category ? " active" : "") + '" type="button" data-filter-category="' + esc(category) + '">' + esc(category) + '</button>';
    }).join("");
  }
  function renderResourceCategoryOptions(kind) {
    var select = $("customBlockCategorySelect");
    if (!select) return;
    var entries = kind === "blocks"
      ? state.libraryBlocks
      : kind === "action"
        ? state.actions
        : state.references.filter(function (item) { return item.kind === kind; });
    var categories = unique(["未分类"].concat(entries.map(function (entry) { return String(entry.category || "未分类"); })));
    select.innerHTML = '<option value="">选择已有分类</option>' + categories.map(function (category) { return '<option value="' + esc(category) + '">' + esc(category) + '</option>'; }).join("");
  }
  function setEditorCategory(category) {
    var value = String(category || "未分类").trim() || "未分类";
    var select = $("customBlockCategorySelect");
    var input = $("customBlockCategory");
    if (!select || !input) return;
    var hasOption = Array.from(select.options).some(function (option) { return option.value === value; });
    select.value = hasOption ? value : "";
    input.value = hasOption ? "" : value;
  }
  function renderFilters() {
    var entries = currentLibraryEntries();
    if ((state.libraryMode === "blocks" || state.libraryMode === "pose" || state.libraryMode === "actions" || isReferenceMode()) && state.categoryFilter !== "全部") {
      entries = entries.filter(function (entry) { return String(entry.category || "未分类") === state.categoryFilter; });
    }
    var tags = unique([].concat.apply([], entries.map(function (entry) {
      var category = String(entry.category || "");
      return (entry.tags || []).filter(function (tag) {
        return String(tag).trim() !== category;
      });
    })));
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
    var addButton = $("openCustomBlock");
    if (addButton) {
      var addLabel = state.libraryMode === "blocks" ? "自定义固定积木" : RESOURCE_LABELS[state.libraryMode === "pose" ? "action" : state.libraryMode] || "参考资源";
      addButton.setAttribute("aria-label", "添加" + addLabel);
      addButton.title = "添加" + addLabel;
    }
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
  function textPreviewMarkup(text, title, className) {
    var value = String(text || "暂无提示词文本");
    return '<button class="text-preview-trigger ' + (className || "") + '" type="button" data-text-preview data-text-title="' + esc(title || "文本预览") + '" data-text-content="' + esc(value) + '">' + esc(value) + '</button>';
  }
  function libraryPromptMarkup(text, className) {
    var value = String(text || "暂无提示词文本");
    return '<div class="' + (className || "") + '">' + esc(value) + '</div>';
  }
  function resourceTagsMarkup(resource, kind) {
    var tags = resource.tags || [];
    var content = tags.length
      ? '<span class="block-tags">' + tags.map(function (tag) { return '<span class="block-tag">' + esc(tag) + '</span>'; }).join("") + '</span>'
      : "";
    return '<button class="resource-tags-edit' + (tags.length ? "" : " is-empty") + '" type="button" data-edit-resource data-resource-kind="' + esc(kind) + '" data-resource-id="' + esc(resource.id) + '" aria-label="编辑「' + esc(resource.title || "资源") + '」标签">' + content + '</button>';
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
  function isAbsoluteLocalPath(value) {
    return /^(\/|[A-Za-z]:[\\/])/.test(String(value || "").trim());
  }
  function minimaxConditioningNodes(workflow) {
    if (!workflow || typeof workflow !== "object") return [];
    return Object.keys(workflow).filter(function (nodeId) {
      if (nodeId === "__rh_meta__") return false;
      var node = workflow[nodeId];
      var classType = String(node && node.class_type || "").toLowerCase();
      return classType === "minimaxh3audioconditioningt8" || classType.indexOf("minimaxh3audioconditioning") !== -1;
    });
  }
  function currentWorkflowContext() {
    var draft = readTaskDraft();
    var workflow = draft && draft.workflow && draft.workflow.data && typeof draft.workflow.data === "object" ? draft.workflow.data : null;
    var nodeIds = minimaxConditioningNodes(workflow);
    return { draft: draft, workflow: workflow, minimaxNodeIds: nodeIds, hasMiniMax: nodeIds.length > 0 };
  }
  function referenceMediaDescriptor(candidate) {
    if (!candidate || candidate.sourceType === "block") return null;
    var sourceId = String(candidate.sourceId || "");
    if (candidate.sourceType === "media") {
      var mediaPath = String(candidate.mediaPath || candidate.path || "");
      var mediaKind = String(candidate.mediaKind || candidate.mediaType || "").toLowerCase();
      if (!mediaKind) mediaKind = mediaKindFromPath(mediaPath);
      if (!mediaPath || ["image", "audio"].indexOf(mediaKind) === -1) return null;
      return { kind: mediaKind, path: mediaPath, endpoint: "", label: candidate.title || candidate.mediaName || "参考媒体", sourceType: "media", sourceId: sourceId || mediaPath };
    }
    var referenceKind = String(candidate.referenceKind || "").toLowerCase();
    var mediaType = String(candidate.mediaType || "").toLowerCase();
    var isAction = candidate.sourceType === "action";
    var isAudio = candidate.sourceType === "reference" && (referenceKind === "audio" || mediaType === "audio" || (!candidate.imagePath && candidate.audioPath));
    if (isAction && !candidate.depthImagePath && !candidate.depthImageUrl) return null;
    var kind = isAudio ? "audio" : "image";
    var path = isAudio
      ? String(candidate.audioPath || "")
      : (isAction ? String(candidate.depthImagePath || "") : String(candidate.colorImagePath || candidate.imagePath || ""));
    var endpoint = "";
    if (candidate.sourceType === "action" && sourceId) {
      endpoint = "/api/prompt/actions/" + encodeURIComponent(sourceId) + "/depth-path";
    } else if (candidate.sourceType === "reference" && sourceId) {
      endpoint = "/api/prompt/references/" + encodeURIComponent(sourceId) + "/" + kind + "-path";
    }
    if (!path && !endpoint) return null;
    return { kind: kind, path: path, endpoint: endpoint, label: candidate.title || "参考媒体", sourceType: candidate.sourceType, sourceId: sourceId };
  }
  function stageReferenceCandidate(item) {
    if (item && item.kind === "media") {
      return {
        sourceType: "media",
        sourceId: item.instanceId || item.mediaPath || "",
        title: item.mediaName || item.title || "参考媒体",
        mediaPath: item.mediaPath || "",
        mediaKind: item.mediaKind || item.previewKind || mediaKindFromPath(item.mediaPath),
        mediaType: item.mediaKind || item.previewKind || "",
      };
    }
    if (!item || (item.kind !== "action" && item.kind !== "reference")) return null;
    return referenceCandidate(item.kind, item.sourceId) || {
      sourceType: item.kind,
      sourceId: item.sourceId || "",
      referenceKind: item.referenceKind || "",
      title: item.title || "参考媒体",
      text: item.text || "",
      imagePath: item.imagePath || "",
      colorImagePath: item.colorImagePath || item.imagePath || "",
      audioPath: item.audioPath || "",
      mediaType: item.mediaType || "",
    };
  }
  function stageTextEditorSegments(item, index) {
    var editor = document.querySelector('[data-stage-text="' + index + '"]');
    if (!editor) return itemSegments(item);
    var segments = [];
    collectEditorSegments(editor, segments);
    return segments;
  }
  function usedReferenceMedia() {
    var result = [];
    var seen = {};
    function add(candidate) {
      var media = referenceMediaDescriptor(candidate);
      if (!media) return;
      var key = media.sourceType + ":" + media.sourceId + ":" + media.kind;
      if (seen[key]) return;
      seen[key] = true;
      result.push({ candidate: candidate, media: media });
    }
    state.stage.forEach(function (item, index) {
      // A media card can be present directly or referenced from a free-text card with @.
      add(stageReferenceCandidate(item));
      if (item.kind === "text") stageTextEditorSegments(item, index).forEach(function (segment) { add(segmentCandidate(segment)); });
    });
    return result;
  }
  function subjectDefinitionMedia() {
    return usedReferenceMedia();
  }
  function refreshSubjectDefinitionsButton() {
    var button = $("generateSubjectDefinitions");
    if (!button) return;
    var count = subjectDefinitionMedia().length;
    button.disabled = !count;
    button.textContent = "插入对象定义";
    button.title = count
      ? "按当前工作台顺序插入 " + count + " 个媒体对象定义，不调用翻译或大模型"
      : "请先在工作台加入至少一个图片或音频素材";
  }
  function subjectDefinitionRole(entry) {
    var media = entry.media || {};
    var candidate = entry.candidate || {};
    var referenceKind = String(candidate.referenceKind || "").toLowerCase();
    if (media.kind === "audio") {
      if (referenceKind === "character") return "the character's voice";
      if (referenceKind === "audio") return "the background music or environmental sound";
      return "the reference audio";
    }
    if (media.sourceType === "media") return "the target video's first frame";
    if (media.sourceType === "action" || referenceKind === "action") return "the character's pose";
    if (referenceKind === "character") return "the character's face";
    if (referenceKind === "background") return "the background";
    if (referenceKind === "clothes") return "the character's clothing";
    return "the reference subject";
  }
  function subjectDefinitionLine(entry, counters) {
    var media = entry.media || {};
    var role = subjectDefinitionRole(entry);
    if (media.kind === "audio") {
      counters.audio += 1;
      return { key: media.sourceType + ":" + media.sourceId + ":" + media.kind, line: "<Audio " + counters.audio + "> is a reference for " + role + "." };
    }
    counters.image += 1;
    if (media.sourceType === "media") {
      return {
        key: media.sourceType + ":" + media.sourceId + ":" + media.kind,
        line: "<Subject " + counters.image + "> is the first frame of [Shot 1] defined by <Picture " + counters.image + ">, the opening still of the target video; the exact environment, background, lighting, camera composition, and the woman's appearance and pose all begin from this image and must be preserved without change at 0.00 seconds.",
      };
    }
    return {
      key: media.sourceType + ":" + media.sourceId + ":" + media.kind,
      line: "<Subject " + counters.image + "> is a reference for " + role + ", defined by <Picture " + counters.image + ">.",
    };
  }
  function subjectDefinitionStageKeys(item, index) {
    var keys = [];
    function add(candidate) {
      var media = referenceMediaDescriptor(candidate);
      if (!media) return;
      var key = media.sourceType + ":" + media.sourceId + ":" + media.kind;
      if (keys.indexOf(key) === -1) keys.push(key);
    }
    add(stageReferenceCandidate(item));
    if (item && item.kind === "text") stageTextEditorSegments(item, index).forEach(function (segment) { add(segmentCandidate(segment)); });
    return keys;
  }
  function subjectDefinitionBlock(value, includeHeader) {
    var text = (includeHeader ? "subject_definitions:\n" : "") + value;
    return {
      instanceId: makeId("subject-definition"),
      kind: "text",
      title: "自由文本",
      text: text,
      translatedText: text,
      translationDisabled: true,
      generatedType: "subject_definitions",
      segments: [{ type: "text", text: text }],
      tags: [],
    };
  }
  function insertSubjectDefinitions(entries) {
    var counters = { image: 0, audio: 0 };
    var definitions = entries.map(function (entry) { return subjectDefinitionLine(entry, counters); });
    var definitionsByKey = {};
    definitions.forEach(function (definition, index) {
      definition.entry = entries[index];
      definitionsByKey[definition.key] = definition;
    });
    var sourceStage = state.stage.filter(function (item) { return item.generatedType !== "subject_definitions"; });
    var inserted = {};
    var nextStage = [];
    sourceStage.forEach(function (item, index) {
      subjectDefinitionStageKeys(item, index).forEach(function (key) {
        var definition = definitionsByKey[key];
        if (!definition || inserted[key]) return;
        nextStage.push(subjectDefinitionBlock(definition.line, Object.keys(inserted).length === 0));
        inserted[key] = true;
      });
      nextStage.push(item);
    });
    definitions.forEach(function (definition) {
      if (inserted[definition.key]) return;
      nextStage.push(subjectDefinitionBlock(definition.line, Object.keys(inserted).length === 0));
      inserted[definition.key] = true;
    });
    var firstDefinitionIndex = nextStage.findIndex(function (item) { return item.generatedType === "subject_definitions"; });
    if (firstDefinitionIndex > 0) {
      var firstDefinition = nextStage.splice(firstDefinitionIndex, 1)[0];
      var firstKey = definitions[0] && definitions[0].key;
      var nextItem = nextStage[firstDefinitionIndex];
      var nextItemIndex = sourceStage.indexOf(nextItem);
      if (firstKey && nextItem && subjectDefinitionStageKeys(nextItem, nextItemIndex).indexOf(firstKey) !== -1) {
        firstDefinition = [firstDefinition, nextStage.splice(firstDefinitionIndex, 1)[0]];
      } else {
        firstDefinition = [firstDefinition];
      }
      nextStage.unshift.apply(nextStage, firstDefinition);
    }
    state.stage = nextStage;
    saveState();
    renderStage();
  }
  function generateSubjectDefinitions() {
    var entries = subjectDefinitionMedia();
    if (!entries.length) return showToast("请先在工作台加入至少一个图片或音频素材", true);
    insertSubjectDefinitions(entries);
    showToast("已按媒体顺序插入对象定义（纯算法）");
  }
  function resolveUsedReferenceMedia(entries) {
    return Promise.all(entries.map(function (entry) {
      var media = entry.media;
      var directPath = isAbsoluteLocalPath(media.path) ? media.path : "";
      var request = directPath
        ? Promise.resolve({ path: directPath, name: filenameFromPath(directPath) })
        : (media.endpoint ? jsonRequest(media.endpoint) : Promise.reject(new Error("找不到本机媒体路径")));
      return request.then(function (asset) {
        var path = String(asset && asset.path || "").trim();
        if (!isAbsoluteLocalPath(path)) throw new Error("媒体路径不是本机绝对路径");
        return { kind: media.kind, path: path, name: asset.name || filenameFromPath(path), label: media.label, sourceType: media.sourceType, sourceId: media.sourceId };
      }).catch(function (error) {
        throw new Error("无法读取「" + media.label + "」的媒体路径：" + error.message);
      });
    }));
  }
  function workflowLink(value) {
    return Array.isArray(value) && value.length >= 2 && (typeof value[0] === "string" || typeof value[0] === "number") ? value : null;
  }
  function collectUpstreamWorkflowNodes(workflow, nodeId, collected) {
    var id = String(nodeId || "");
    if (!id || collected[id] || !workflow[id]) return;
    collected[id] = true;
    var inputs = workflow[id].inputs && typeof workflow[id].inputs === "object" ? workflow[id].inputs : {};
    Object.keys(inputs).forEach(function (field) {
      var link = workflowLink(inputs[field]);
      if (link) collectUpstreamWorkflowNodes(workflow, link[0], collected);
    });
  }
  function removeDetachedWorkflowNodes(workflow, candidates, protectedNodes) {
    var changed = true;
    while (changed) {
      changed = false;
      Object.keys(candidates).forEach(function (nodeId) {
        if (!workflow[nodeId] || protectedNodes[nodeId]) return;
        var referenced = Object.keys(workflow).some(function (otherId) {
          if (otherId === nodeId || otherId === "__rh_meta__") return false;
          var inputs = workflow[otherId] && workflow[otherId].inputs && typeof workflow[otherId].inputs === "object" ? workflow[otherId].inputs : {};
          return Object.keys(inputs).some(function (field) {
            var link = workflowLink(inputs[field]);
            return link && String(link[0]) === String(nodeId);
          });
        });
        if (!referenced) {
          delete workflow[nodeId];
          delete candidates[nodeId];
          changed = true;
        }
      });
    }
  }
  function nextWorkflowNodeId(workflow) {
    var max = 0;
    Object.keys(workflow || {}).forEach(function (nodeId) {
      if (/^\d+$/.test(nodeId)) max = Math.max(max, Number(nodeId));
    });
    var next = max + 1;
    while (workflow[String(next)]) next += 1;
    return String(next);
  }
  function buildMinimaxMediaWorkflow(sourceWorkflow, minimaxNodeIds, mediaAssets) {
    var workflow = JSON.parse(JSON.stringify(sourceWorkflow || {}));
    var protectedNodes = {};
    minimaxNodeIds.forEach(function (nodeId) { protectedNodes[String(nodeId)] = true; });
    var detached = {};
    minimaxNodeIds.forEach(function (nodeId) {
      var node = workflow[String(nodeId)];
      if (!node || !node.inputs || typeof node.inputs !== "object") return;
      Object.keys(node.inputs).forEach(function (field) {
        if (!/^(ref_images\.ref_image_|ref_audios\.ref_audio_)/.test(field)) return;
        var link = workflowLink(node.inputs[field]);
        if (link) collectUpstreamWorkflowNodes(workflow, link[0], detached);
        delete node.inputs[field];
      });
    });
    removeDetachedWorkflowNodes(workflow, detached, protectedNodes);
    var fileInputs = [];
    var nextId = function () {
      var id = nextWorkflowNodeId(workflow);
      while (workflow[id]) id = String(Number(id) + 1);
      return id;
    };
    var imageIndex = 0;
    var audioIndex = 0;
    mediaAssets.forEach(function (asset) {
      var nodeId = nextId();
      var title = "Prompt Workbench · " + asset.label;
      workflow[nodeId] = asset.kind === "audio"
        ? { inputs: { audio: asset.path, audioUI: "" }, class_type: "LoadAudio", _meta: { title: title, rh_prompt_media: true } }
        : { inputs: { image: asset.path }, class_type: "LoadImage", _meta: { title: title, rh_prompt_media: true } };
      var field = asset.kind === "audio" ? "audio" : "image";
      fileInputs.push({ id: nodeId + ":" + field, node_id: nodeId, field: field, title: title, class_type: workflow[nodeId].class_type, kind: "file", default: asset.path });
      if (asset.kind === "audio") {
        minimaxNodeIds.forEach(function (minimaxNodeId) {
          workflow[String(minimaxNodeId)].inputs["ref_audios.ref_audio_" + audioIndex] = [nodeId, 0];
        });
        audioIndex += 1;
      } else {
        minimaxNodeIds.forEach(function (minimaxNodeId) {
          workflow[String(minimaxNodeId)].inputs["ref_images.ref_image_" + imageIndex] = [nodeId, 0];
        });
        imageIndex += 1;
      }
    });
    return { workflow: workflow, fileInputs: fileInputs };
  }
  function workflowValuesWithMedia(draft, workflow, fileInputs, mediaAssets) {
    var previous = draft && draft.workflow && draft.workflow.values && typeof draft.workflow.values === "object" ? draft.workflow.values : {};
    var values = JSON.parse(JSON.stringify(previous));
    values.files = {};
    Object.keys(previous.files || {}).forEach(function (inputId) {
      var nodeId = String(inputId).split(":", 1)[0];
      if (workflow[nodeId]) values.files[inputId] = previous.files[inputId];
    });
    fileInputs.forEach(function (item) { values.files[item.id] = item.default; });
    values.bypassedNodes = (previous.bypassedNodes || previous.bypassed_nodes || []).filter(function (nodeId) { return Boolean(workflow[String(nodeId)]); });
    return values;
  }
  function workflowInputConfigWithMedia(config, workflow, fileInputs) {
    if (!config || config.mode !== "manual" || !Array.isArray(config.items)) return config;
    var items = config.items.filter(function (item) {
      var nodeId = String(item && item.node_id || "");
      var field = String(item && item.field || "");
      return workflow[nodeId] && workflow[nodeId].inputs && (item.kind === "resolution" || item.kind === "random_noise" || Object.prototype.hasOwnProperty.call(workflow[nodeId].inputs, field));
    });
    var existing = {};
    items.forEach(function (item) { existing[String(item.id || (item.node_id + ":" + item.field))] = true; });
    fileInputs.forEach(function (item) {
      if (existing[item.id]) return;
      items.push({ id: item.id, node_id: item.node_id, field: item.field, title: item.title, label: item.title, class_type: item.class_type, kind: "file", required: true });
    });
    return { mode: "manual", items: items };
  }
  function importMinimaxMediaToTask() {
    var context = currentWorkflowContext();
    if (!context.hasMiniMax || !context.draft || !context.workflow) return showToast("请先在任务提交页导入或加载包含 MiniMax H3 节点的工作流", true);
    var entries = usedReferenceMedia().filter(function (entry) { return entry.media.kind === "image" || entry.media.kind === "audio"; });
    if (!entries.length) return showToast("当前组装台没有可导入的参考图片或参考音频", true);
    var button = $("importMedia");
    if (button) {
      button.disabled = true;
      button.textContent = "构建中…";
    }
    resolveUsedReferenceMedia(entries).then(function (mediaAssets) {
      var built = buildMinimaxMediaWorkflow(context.workflow, context.minimaxNodeIds, mediaAssets);
      return jsonRequest("/api/workflows/analyze", "POST", {
        filename: context.draft.workflow.name || "workflow_api.json",
        content: JSON.stringify(built.workflow),
        source_dir: context.draft.workflow.sourceDir || "",
        account_id: context.draft.workflow.accountId || "",
        remote_workflow_id: context.draft.workflow.remoteWorkflowId || "",
      }).then(function (analysisResult) {
        var draft = JSON.parse(JSON.stringify(context.draft));
        draft.workflow.data = built.workflow;
        draft.workflow.analysis = analysisResult.analysis || {};
        draft.workflow.values = workflowValuesWithMedia(draft, built.workflow, built.fileInputs, mediaAssets);
        draft.workflow.inputConfig = workflowInputConfigWithMedia(draft.workflow.inputConfig, built.workflow, built.fileInputs);
        draft.workflow.remoteWorkflowId = String(analysisResult.remote_workflow_id || draft.workflow.remoteWorkflowId || "").trim();
        if (!draft.workflow.id) draft.workflow.id = String(analysisResult.workflow_id || "");
        draft.workflow.savedAt = Date.now();
        window.localStorage.setItem(draftStorageKey, JSON.stringify(draft));
        showToast("已导入 " + mediaAssets.filter(function (item) { return item.kind === "image"; }).length + " 张参考图和 " + mediaAssets.filter(function (item) { return item.kind === "audio"; }).length + " 条参考音频，任务提交页草稿已更新");
      });
    }).catch(function (error) {
      showToast("导入媒体失败：" + error.message, true);
    }).finally(function () {
      if (button && button.isConnected) {
        button.disabled = false;
        button.textContent = "导入媒体";
      }
    });
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
    $("depthImportTitle").textContent = "导入任务";
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
        $("confirmDepthImport").textContent = "导入任务";
      }
    });
  }
  function confirmDepthImport() {
    confirmWorkflowImport();
  }
  function renderActionLibrary() {
    var actions = sortSearchEntries(state.actions.filter(blockMatches));
    if (!actions.length) {
      $("libraryList").innerHTML = '<div class="library-empty">没有匹配的动作。<br />试试其他标签或搜索提示词。</div>';
      $("libraryCount").textContent = actions.length + " 个动作";
      $("libraryFooterHint").textContent = state.actionSource ? state.actionSource.paired_count + "/" + state.actionSource.action_count + " 对已配对" : "拖动卡片加入";
      return;
    }
    $("libraryList").innerHTML = actions.map(function (action, index) {
      var hasColorImage = Boolean(action.image_available || action.color_image_available);
      var hasDepthImage = Boolean(action.depth_image_available);
      var importWorkflowButton = hasColorImage
        ? '<button class="import-workflow-button action-card-import" type="button" data-import-workflow data-import-workflow-kind="action" data-import-workflow-id="' + esc(action.id) + '" title="' + esc(hasDepthImage ? "选择 LoadImage 节点并导入深度图" : "暂无可用深度图，请先完成原图与深度图配对") + '"' + (hasDepthImage ? "" : ' disabled aria-disabled="true"') + '>导入任务</button>'
        : "";
      var category = action.category || "未分类";
      return '<article class="action-library-card" draggable="true" data-action-id="' + esc(action.id) + '" style="animation-delay:' + Math.min(index * 35, 220) + 'ms">' +
        '<div class="action-card-media">' + actionMediaMarkup(action, "") + '</div>' +
        '<div class="action-card-body"><div class="library-block-top action-card-top"><button class="library-block-title resource-title-edit" type="button" data-edit-resource data-resource-kind="action" data-resource-id="' + esc(action.id) + '" aria-label="编辑「' + esc(action.title) + '」"><span class="block-type-dot action" aria-hidden="true"></span><span>' + esc(action.title) + '</span></button></div>' +
        '<div class="library-card-meta-row"><span class="action-card-top-actions">' + importWorkflowButton + '<span class="action-card-category" title="一级分类">' + esc(category) + '</span><span class="library-block-label">POSE</span></span></div>' +
        resourceTagsMarkup(action, "action") +
        libraryPromptMarkup(action.text, "action-library-text") +
        (action.pair_status === "paired" ? "" : '<div class="library-block-footer"><span class="action-pair-status ' + actionPairClass(action) + '" title="' + esc(action.pair_message || "") + '">' + esc(actionPairLabel(action)) + '</span></div>') +
        '</div></article>';
    }).join("");
    $("libraryCount").textContent = actions.length + " 个动作";
    $("libraryFooterHint").textContent = state.actionSource ? state.actionSource.paired_count + "/" + state.actionSource.action_count + " 对已配对" : "拖动卡片加入";
  }
  function referenceMediaMarkup(reference, extraClass) {
    var title = reference.title || "参考资源";
    var imageUrl = reference.image_url || reference.imageUrl || "";
    var audioUrl = reference.audio_url || reference.audioUrl || "";
    if (imageUrl && (reference.image_available !== false || reference.imageUrl)) {
      return '<div class="reference-media ' + (extraClass === "reference-card-media" ? "reference-card-image " : "") + (extraClass || "") + '"><button class="image-preview-trigger reference-media-image" type="button" data-image-preview="' + esc(imageUrl) + '" data-image-title="' + esc(title) + '" aria-label="放大查看「' + esc(title) + '」"><img src="' + esc(imageUrl) + '" alt="' + esc(title) + '" loading="lazy" /></button></div>';
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
    var references = sortSearchEntries(currentLibraryEntries().filter(blockMatches));
    if (!references.length) {
      $("libraryList").innerHTML = '<div class="library-empty">没有匹配的' + esc(({ character: "人物", audio: "音频", background: "背景", clothes: "服装" }[state.libraryMode] || "参考资源")) + '。<br />试试其他标签或搜索提示词。</div>';
      $("libraryCount").textContent = "0 个参考资源";
      $("libraryFooterHint").textContent = "拖动卡片加入";
      return;
    }
    $("libraryList").innerHTML = references.map(function (reference, index) {
      var label = reference.kind_label || "参考资源";
      var importWorkflowButton = reference.image_available
        ? '<button class="import-workflow-button" type="button" data-import-workflow data-import-workflow-kind="reference" data-import-workflow-id="' + esc(reference.id) + '" title="选择 LoadImage 节点并导入图片">导入任务</button>'
        : "";
      return '<article class="reference-library-card" draggable="true" data-reference-id="' + esc(reference.id) + '" style="animation-delay:' + Math.min(index * 35, 220) + 'ms">' +
        referenceMediaMarkup(reference, "reference-card-media") +
        '<div class="reference-card-body"><div class="library-block-top reference-card-top"><button class="library-block-title resource-title-edit" type="button" data-edit-resource data-resource-kind="' + esc(reference.kind) + '" data-resource-id="' + esc(reference.id) + '" aria-label="编辑「' + esc(reference.title) + '」"><span class="block-type-dot reference" aria-hidden="true"></span><span>' + esc(reference.title) + '</span></button></div>' +
        '<div class="library-card-meta-row"><span class="action-card-top-actions">' + importWorkflowButton + '<span class="library-block-label">' + esc(label) + '</span></span></div>' +
        resourceTagsMarkup(reference, reference.kind) +
        libraryPromptMarkup(reference.text, "reference-library-text") +
        '</div></article>';
    }).join("");
    $("libraryCount").textContent = references.length + " 个参考资源";
    $("libraryFooterHint").textContent = state.referenceSource ? "共 " + state.referenceSource.reference_count + " 个资源" : "拖动卡片加入";
  }
  function renderLibrary() {
    renderLibraryMode();
    if (state.libraryMode === "pose" || state.libraryMode === "actions") return renderActionLibrary();
    if (isReferenceMode()) return renderReferenceLibrary();
    var blocks = sortSearchEntries(allBlocks().filter(blockMatches));
    var html = '<button class="free-block-card" type="button" draggable="true" data-add-text-block>' +
      '<span class="block-type-dot text" aria-hidden="true">T</span>' +
      '<span class="free-block-copy"><strong>自由文本</strong><span>每次加入一块新的可编辑文本</span></span>' +
      '<span class="free-block-plus" aria-hidden="true">＋</span></button>' +
      '<button class="free-block-card media-block-card" type="button" draggable="true" data-add-media-block aria-label="将媒体积木拖入提示词工作台后添加媒体">' +
      '<span class="block-type-dot media" aria-hidden="true">↗</span>' +
      '<span class="free-block-copy"><strong>媒体积木</strong><span>拖入提示词工作台后，选择、拖入或粘贴媒体</span></span>' +
      '<span class="free-block-plus" aria-hidden="true">↗</span></button>';
    if (!blocks.length) {
      html += '<div class="library-empty">没有匹配的固定积木。<br />试试其他标签或添加一块新的积木。</div>';
    } else {
      html += blocks.map(function (block, index) {
        return '<article class="library-block" draggable="true" data-library-block-id="' + esc(block.id) + '" style="animation-delay:' + Math.min(index * 35, 220) + 'ms">' +
          '<div class="library-block-top"><button class="library-block-title library-block-title-button" type="button" data-edit-block="' + esc(block.id) + '" aria-label="编辑「' + esc(block.title) + '」"><span class="block-type-dot" aria-hidden="true"></span><span>' + esc(block.title) + '</span></button>' +
          '<span class="library-block-label">JSON</span></div>' +
          '<div class="block-tags">' + (block.tags || []).map(function (tag) { return '<span class="block-tag">' + esc(tag) + '</span>'; }).join("") + '</div>' +
          '<div class="library-block-text">' + esc(block.text) + '</div>' +
          '<div class="library-block-footer">' +
          '<span class="library-block-manage-actions"><button class="delete-block-button" type="button" data-delete-block="' + esc(block.id) + '">删除</button></span></div></article>';
      }).join("");
    }
    $("libraryList").innerHTML = html;
    $("libraryCount").textContent = blocks.length + " 个固定积木";
    $("libraryFooterHint").textContent = "拖动卡片加入";
  }
  function fuzzyMatchScore(needle, value) {
    if (!needle) return 0;
    var position = 0;
    var gaps = 0;
    for (var index = 0; index < needle.length; index += 1) {
      var found = value.indexOf(needle[index], position);
      if (found === -1) return -1;
      gaps += found - position;
      position = found + 1;
    }
    return gaps;
  }
  function rankedReferenceCandidates(query) {
    var needle = String(query || "").trim().toLocaleLowerCase();
    return referenceCandidates().map(function (candidate, index) {
      var title = String(candidate.title || "").toLocaleLowerCase();
      var category = String(candidate.category || "未分类").toLocaleLowerCase();
      var tags = (candidate.tags || []).map(function (tag) { return String(tag).toLocaleLowerCase(); });
      var text = String(candidate.text || "").toLocaleLowerCase();
      var rank = 0;
      var detail = 0;
      if (needle) {
        var titlePrefix = title.indexOf(needle) === 0;
        var categoryPrefix = category.indexOf(needle) === 0;
        var categoryContains = category.indexOf(needle) !== -1;
        var tagPrefix = tags.some(function (tag) { return tag.indexOf(needle) === 0; });
        var titleContains = title.indexOf(needle) !== -1;
        var tagOrTextContains = tags.some(function (tag) { return tag.indexOf(needle) !== -1; }) || text.indexOf(needle) !== -1;
        var fuzzyValues = [category, title].concat(tags, [text]).map(function (value) { return fuzzyMatchScore(needle, value); }).filter(function (score) { return score >= 0; });
        if (categoryPrefix) rank = 0;
        else if (categoryContains) rank = 1;
        else if (titlePrefix) rank = 2;
        else if (titleContains) rank = 3;
        else if (tagPrefix) rank = 4;
        else if (tagOrTextContains) rank = 5;
        else if (fuzzyValues.length) { rank = 6; detail = Math.min.apply(Math, fuzzyValues); }
        else rank = 99;
      }
      return { candidate: candidate, rank: rank, detail: detail, index: index };
    }).filter(function (entry) { return entry.rank < 99; }).sort(function (left, right) {
      return left.rank - right.rank || left.detail - right.detail || left.index - right.index;
    }).slice(0, 10).map(function (entry) { return entry.candidate; });
  }
  function renderEditorSegments(item) {
    return itemSegments(item).map(function (segment) {
      if (segment.type !== "reference") return esc(segment.text || "");
      var candidate = segmentCandidate(segment);
      var sourceType = String(segment.sourceType || segment.source_type || "reference");
      var sourceId = String(segment.sourceId || segment.source_id || "");
      var label = segmentLabel(segment);
      var promptText = candidate ? String(candidate.text || "") : String(segment.snapshot && segment.snapshot.text || "");
      return '<span class="prompt-reference-token' + (candidate && candidate.missing ? " is-missing" : "") + '" contenteditable="false" data-ref-source-type="' + esc(sourceType) + '" data-ref-source-id="' + esc(sourceId) + '" data-ref-label="' + esc(label) + '" data-ref-text="' + esc(promptText) + '">@' + esc(label) + '</span>';
    }).join("");
  }
  function appendEditorText(segments, value) {
    if (!value) return;
    var last = segments[segments.length - 1];
    if (last && last.type === "text") last.text += value;
    else segments.push({ type: "text", text: value });
  }
  function collectEditorSegments(node, segments) {
    Array.prototype.slice.call(node.childNodes || []).forEach(function (child, index, children) {
      if (child.nodeType === 3) {
        appendEditorText(segments, child.nodeValue || "");
        return;
      }
      if (child.nodeType !== 1) return;
      if (child.classList.contains("prompt-reference-token")) {
        var sourceType = child.dataset.refSourceType || "reference";
        var sourceId = child.dataset.refSourceId || "";
        if (sourceId) {
          var current = referenceCandidate(sourceType, sourceId);
          segments.push({
            type: "reference",
            sourceType: sourceType,
            sourceId: sourceId,
            label: child.dataset.refLabel || (current && current.title) || "参考卡片",
            snapshot: referenceSnapshot(current || { title: child.dataset.refLabel || "参考卡片", text: child.dataset.refText || "" }),
          });
        } else {
          appendEditorText(segments, child.textContent || "");
        }
        return;
      }
      if (child.tagName === "BR") {
        appendEditorText(segments, "\n");
        return;
      }
      collectEditorSegments(child, segments);
      if (/^(DIV|P|LI)$/.test(child.tagName) && index < children.length - 1) appendEditorText(segments, "\n");
    });
  }
  function syncTextItemFromEditor(item, editor) {
    var previousSegments = Array.isArray(item.segments) ? item.segments : [];
    var segments = [];
    collectEditorSegments(editor, segments);
    segments.forEach(function (segment) {
      if (segment.type !== "reference") return;
      var previous = previousSegments.find(function (candidate) {
        return candidate.type === "reference" && String(candidate.sourceType || candidate.source_type) === String(segment.sourceType) && String(candidate.sourceId || candidate.source_id) === String(segment.sourceId);
      });
      if (previous && previous.snapshot) segment.snapshot = Object.assign({}, previous.snapshot, segment.snapshot || {});
    });
    item.segments = segments;
    item.text = sourceTextFromSegments(segments);
    item.translatedText = item.translationDisabled || item.generatedType ? item.text : "";
  }
  function mentionAtCaret(editor) {
    var selection = window.getSelection && window.getSelection();
    if (!selection || !selection.rangeCount || !selection.isCollapsed) return null;
    var node = selection.anchorNode;
    var offset = selection.anchorOffset;
    if (!node || !editor.contains(node) || node.nodeType !== 3) return null;
    var before = String(node.nodeValue || "").slice(0, offset);
    var match = before.match(/@([^\s@，。！？；：、]*)$/);
    if (!match) return null;
    var range = document.createRange();
    range.setStart(node, offset - match[0].length);
    range.setEnd(node, offset);
    return { range: range, query: match[1] || "" };
  }
  function suggestionMediaMarkup(candidate) {
    var imageUrl = candidate.colorImageUrl || candidate.imageUrl || candidate.depthImageUrl || "";
    if (imageUrl) return '<img src="' + esc(imageUrl) + '" alt="' + esc(candidate.title) + '" loading="lazy" />';
    if (candidate.audioUrl) return '<span class="prompt-reference-preview-icon">♫</span>';
    return '<span class="prompt-reference-preview-icon">Aa</span>';
  }
  function candidateHasMedia(candidate) {
    return Boolean(candidate && (candidate.colorImageUrl || candidate.imageUrl || candidate.depthImageUrl || candidate.audioUrl));
  }
  function closeReferenceSuggestions() {
    var popup = $("promptReferenceSuggest");
    if (popup) popup.hidden = true;
    referenceSuggest = { open: false, editor: null, item: null, range: null, query: "", candidates: [], selectedIndex: 0 };
  }
  function closeReferenceHover() {
    var popup = $("promptReferenceHover");
    if (popup) popup.hidden = true;
    referenceHover = { token: null, candidate: null };
  }
  function positionReferenceHover() {
    var popup = $("promptReferenceHover");
    if (!popup || popup.hidden || !referenceHover.token) return;
    var tokenRect = referenceHover.token.getBoundingClientRect();
    var width = Math.min(300, Math.max(220, window.innerWidth - 24));
    popup.style.width = width + "px";
    var height = popup.getBoundingClientRect().height || 160;
    var left = Math.max(12, Math.min(tokenRect.left, window.innerWidth - width - 12));
    var top = tokenRect.bottom + 8;
    if (top + height > window.innerHeight - 12) top = Math.max(12, tokenRect.top - height - 8);
    popup.style.left = left + "px";
    popup.style.top = top + "px";
  }
  function showReferenceHover(token) {
    if (!token || referenceSuggest.open) return;
    var sourceType = token.dataset.refSourceType || "reference";
    var sourceId = token.dataset.refSourceId || "";
    var candidate = referenceCandidate(sourceType, sourceId) || {
      sourceType: sourceType,
      sourceId: sourceId,
      title: token.dataset.refLabel || "参考卡片",
      text: token.dataset.refText || "",
      imageUrl: "",
      audioUrl: "",
    };
    var popup = $("promptReferenceHover");
    if (!popup) return;
    referenceHover = { token: token, candidate: candidate };
    if (candidateHasMedia(candidate)) {
      popup.innerHTML = '<div class="prompt-reference-hover-media">' + suggestionMediaMarkup(candidate) + '</div>';
    } else {
      popup.innerHTML = '<p class="prompt-reference-hover-text">' + esc(candidate.text || "暂无提示词内容") + '</p>';
    }
    popup.hidden = false;
    positionReferenceHover();
  }
  function positionReferenceSuggestions() {
    var popup = $("promptReferenceSuggest");
    if (!popup || popup.hidden || !referenceSuggest.range) return;
    var anchor = referenceSuggest.range.getBoundingClientRect();
    var editorRect = referenceSuggest.editor && referenceSuggest.editor.getBoundingClientRect();
    var left = anchor.left || (editorRect && editorRect.left) || 12;
    var top = (anchor.bottom || (editorRect && editorRect.bottom) || 12) + 8;
    var width = Math.min(440, Math.max(300, window.innerWidth - 24));
    popup.style.width = width + "px";
    var popupHeight = popup.getBoundingClientRect().height || 280;
    if (top + popupHeight > window.innerHeight - 12) top = Math.max(12, (anchor.top || 12) - popupHeight - 8);
    left = Math.max(12, Math.min(left, window.innerWidth - width - 12));
    popup.style.left = left + "px";
    popup.style.top = top + "px";
  }
  function renderReferenceSuggestions() {
    var popup = $("promptReferenceSuggest");
    var list = $("promptReferenceSuggestionList");
    var preview = $("promptReferenceSuggestionPreview");
    if (!popup || !list || !preview) return;
    if (!referenceSuggest.candidates.length) {
      list.innerHTML = '<div class="prompt-reference-suggestion-empty">没有匹配卡片<br /><span>继续输入标题、标签或提示词内容</span></div>';
      preview.classList.remove("is-media");
      preview.innerHTML = '<div class="prompt-reference-suggestion-empty">暂无预览</div>';
      popup.hidden = false;
      positionReferenceSuggestions();
      return;
    }
    referenceSuggest.selectedIndex = Math.max(0, Math.min(referenceSuggest.selectedIndex, referenceSuggest.candidates.length - 1));
    list.innerHTML = referenceSuggest.candidates.map(function (candidate, index) {
      var selected = index === referenceSuggest.selectedIndex;
      return '<button class="prompt-reference-suggestion' + (selected ? " is-selected" : "") + '" type="button" role="option" aria-selected="' + String(selected) + '" data-reference-suggestion-index="' + index + '">' +
        '<span class="prompt-reference-suggestion-dot" aria-hidden="true"></span><span class="prompt-reference-suggestion-copy"><strong>' + esc(candidate.title) + '</strong><span>' + esc(candidate.sourceLabel) + (candidate.category ? " · " + esc(candidate.category) : "") + '</span></span></button>';
    }).join("");
    var selectedOption = list.querySelector('[data-reference-suggestion-index="' + referenceSuggest.selectedIndex + '"]');
    if (selectedOption) {
      var optionTop = selectedOption.offsetTop;
      var optionBottom = optionTop + selectedOption.offsetHeight;
      var visibleTop = list.scrollTop;
      var visibleBottom = visibleTop + list.clientHeight;
      if (optionTop < visibleTop) list.scrollTop = optionTop;
      else if (optionBottom > visibleBottom) list.scrollTop = optionBottom - list.clientHeight;
    }
    var selectedCandidate = referenceSuggest.candidates[referenceSuggest.selectedIndex];
    var hasMedia = candidateHasMedia(selectedCandidate);
    preview.classList.toggle("is-media", hasMedia);
    preview.classList.toggle("is-block", selectedCandidate.sourceType === "block");
    var mediaPreview = '<div class="prompt-reference-preview-media">' + suggestionMediaMarkup(selectedCandidate) + '</div>';
    if (hasMedia) {
      preview.innerHTML = mediaPreview;
    } else if (selectedCandidate.sourceType === "block") {
      preview.innerHTML = '<strong>' + esc(selectedCandidate.title) + '</strong><span class="prompt-reference-preview-source">基础积木</span><p>' + esc(selectedCandidate.text || "暂无提示词内容") + '</p>';
    } else {
      preview.innerHTML = mediaPreview + '<strong>' + esc(selectedCandidate.title) + '</strong><span class="prompt-reference-preview-source">' + esc(selectedCandidate.sourceLabel) + '</span><p>' + esc(selectedCandidate.text || "暂无提示词内容") + '</p>';
    }
    popup.hidden = false;
    positionReferenceSuggestions();
  }
  function refreshReferenceSuggestions(editor, item) {
    if (referenceComposing) return;
    var mention = mentionAtCaret(editor);
    if (!mention) return closeReferenceSuggestions();
    referenceSuggest = { open: true, editor: editor, item: item, range: mention.range, query: mention.query, candidates: rankedReferenceCandidates(mention.query), selectedIndex: 0 };
    renderReferenceSuggestions();
  }
  function commitReferenceSuggestion(candidate) {
    var target = referenceSuggest;
    if (!target.open || !candidate || !target.range || !target.editor || !target.editor.isConnected) return closeReferenceSuggestions();
    var segment = { type: "reference", sourceType: candidate.sourceType, sourceId: candidate.sourceId, label: candidate.title, snapshot: referenceSnapshot(candidate) };
    var token = document.createElement("span");
    token.className = "prompt-reference-token";
    token.contentEditable = "false";
    token.dataset.refSourceType = segment.sourceType;
    token.dataset.refSourceId = segment.sourceId;
    token.dataset.refLabel = segment.label;
    token.dataset.refText = candidate.text || "";
    token.textContent = "@" + candidate.title;
    target.range.deleteContents();
    target.range.insertNode(token);
    var spacer = document.createTextNode(" ");
    token.parentNode.insertBefore(spacer, token.nextSibling);
    var index = Number(target.item && target.item.dataset && target.item.dataset.stageText);
    if (!Number.isFinite(index)) index = Number(target.editor.dataset.stageText);
    var item = state.stage[index];
    if (item) syncTextItemFromEditor(item, target.editor);
    closeReferenceSuggestions();
    target.editor.focus();
    var selection = window.getSelection();
    var range = document.createRange();
    range.setStart(spacer, spacer.nodeValue.length);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
    saveState();
    renderOutput();
  }
  function removePreviousReferenceToken(editor) {
    var selection = window.getSelection && window.getSelection();
    if (!selection || !selection.rangeCount || !selection.isCollapsed) return false;
    var range = selection.getRangeAt(0);
    if (!editor.contains(range.startContainer)) return false;
    var node = range.startContainer;
    var offset = range.startOffset;
    var previous = null;
    if (node.nodeType === 3 && offset === 0) previous = node.previousSibling;
    if (node.nodeType === 1 && offset > 0) previous = node.childNodes[offset - 1];
    if (!previous || previous.nodeType !== 1 || !previous.classList.contains("prompt-reference-token")) return false;
    var parent = previous.parentNode;
    previous.remove();
    var nextRange = document.createRange();
    nextRange.setStart(parent, Array.prototype.indexOf.call(parent.childNodes, node));
    nextRange.collapse(true);
    selection.removeAllRanges();
    selection.addRange(nextRange);
    return true;
  }
  function promptStructureField(item) {
    var content = [item && item.generatedType, item && item.title, item && item.text].join(" ").toLowerCase();
    return PROMPT_STRUCTURE_FIELDS.find(function (field) { return content.indexOf(field) !== -1; }) || "";
  }
  function stageBlockMarkup(item, index) {
    var isText = item.kind === "text";
    var isRawText = isText && item.translationDisabled;
    var isAction = item.kind === "action";
    var isReference = item.kind === "reference";
    var isMedia = item.kind === "media";
    var structureField = promptStructureField(item);
    var tags = item.tags || [];
    var sourceAction = isAction && !item.missing ? state.actions.find(function (action) { return action.id === item.sourceId; }) : null;
    var canImportWorkflow = Boolean(
      (sourceAction && sourceAction.depth_image_available && sourceAction.depth_image_url) ||
      (isReference && !item.missing && item.imageUrl)
    );
    var hasStagePreview = isAction || isReference || isMedia;
    var stagePreviewMarkup = "";
    if (isAction) {
      stagePreviewMarkup = actionMediaMarkup({ title: item.title || "动作图片", color_image_url: item.colorImageUrl || item.imageUrl || "", depth_image_url: item.depthImageUrl || "", color_image_available: Boolean(item.colorImageUrl || item.imageUrl), depth_image_available: Boolean(item.depthImageUrl), pair_status: item.pairStatus || "" }, "stage-action-media");
    } else if (isReference) {
      stagePreviewMarkup = referenceMediaMarkup({ title: item.title, imageUrl: item.imageUrl, audioUrl: item.audioUrl, image_available: Boolean(item.imageUrl), audio_available: Boolean(item.audioUrl), media_type: item.mediaType }, "stage-reference-media");
    } else if (isMedia) {
      stagePreviewMarkup = promptMediaPreviewMarkup(item, index);
    }
    var referenceLabels = { character: "人物库", audio: "音频库", background: "背景库", clothes: "服装库" };
    var typeLabel = isText ? "自由文本" : (isMedia ? "媒体积木 · " + mediaTypeLabel(item.mediaKind || item.previewKind) : (isAction ? (item.missing ? "动作 · 已不可用" : "动作库") : (isReference ? (item.missing ? "参考资源 · 已不可用" : (referenceLabels[item.referenceKind] || "参考资源库")) : (item.missing ? "固定积木 · 已删除" : "固定积木"))));
    var stageTitle = isText ? "自由文本" : (item.title || (isMedia ? item.mediaName : "固定积木") || "固定积木");
    var stageTitleButton = '<button class="stage-block-title-button" type="button" data-edit-stage="' + index + '" title="编辑组装台积木" aria-label="编辑组装台积木：' + esc(stageTitle) + '"><h3>' + esc(stageTitle) + '</h3></button>';
    var importWorkflowButton = canImportWorkflow
      ? '<button class="import-workflow-button stage-workflow-import" type="button" data-import-workflow data-import-workflow-kind="' + (isAction ? "action" : "reference") + '" data-import-workflow-id="' + esc(item.sourceId) + '" title="选择 LoadImage 节点并导入' + (isAction ? "深度图" : "图片") + '">导入任务</button>'
      : "";
    var mediaControlsMarkup = isMedia
      ? '<div class="stage-media-controls"><div class="stage-media-control-buttons"><button class="stage-media-select" type="button" data-open-media-stage data-media-stage-index="' + index + '">选择文件</button><button class="stage-media-paste" type="button" data-paste-media-stage data-media-stage-index="' + index + '">粘贴图片</button></div><span class="stage-media-drop-hint">拖入图片、音频或视频到卡片任意位置可替换</span></div>'
      : "";
    var textEditorMarkup = '<label class="stage-text-field"><span class="stage-text-label">输入内容 <span class="stage-text-hint">输入 @ 搜索并插入卡片</span></span><div class="stage-text-editor" data-stage-text="' + index + '" contenteditable="true" role="textbox" aria-multiline="true" spellcheck="false" data-placeholder="输入这一块要拼接的文本内容">' + renderEditorSegments(item) + '</div></label>';
    var textMarkup = isText
      ? (isRawText
        ? '<div class="stage-text-fields raw-text-fields">' + textEditorMarkup + '</div>'
        : '<div class="stage-text-fields">' + textEditorMarkup + '<label class="stage-text-field stage-text-translation-field"><span class="stage-text-label"><span>英文翻译结果</span><span class="stage-translation-status' + (item.translatedText ? " is-ready" : "") + '" data-stage-translation-status="' + index + '">' + (item.translatedText ? "已翻译" : "等待翻译") + '</span></span><textarea class="stage-text-translation" data-stage-translation="' + index + '" readonly placeholder="点击右上角“翻译”后显示英文结果">' + esc(resolveTranslationTemplate(item, item.translatedText || "")) + '</textarea></label></div>')
      : isMedia
        ? mediaControlsMarkup + '<div class="stage-block-copy stage-media-path" title="' + esc(item.mediaPath || "") + '">' + esc(mediaTypeLabel(item.mediaKind || item.previewKind) + " · " + (item.mediaName || item.title || "媒体文件")) + '</div>'
      : '<div class="stage-block-copy">' + esc(item.text) + '</div>';
    var titleMarkup = isText
      ? (isRawText
        ? '<div class="stage-block-title-row">' + stageTitleButton + '<span class="stage-raw-text-label">原文直出</span></div>'
        : '<div class="stage-block-title-row">' + stageTitleButton + '<button class="secondary-button button-compact translate-text-button" type="button" data-translate-stage="' + index + '" title="将输入内容翻译为英文"><span>翻译</span></button></div>')
      : stageTitleButton;
    return '<article class="stage-block ' + (isText ? "text" : (isMedia ? "media" : (isAction ? "action" : (isReference ? "reference" : "fixed")))) + (hasStagePreview ? " stage-block--with-preview" : " stage-block--content-only") + (structureField ? " stage-block--prompt-structure" : "") + (isRawText ? " raw-text" : "") + (item.missing ? " missing" : "") + '" draggable="false" data-stage-index="' + index + '" data-stage-instance-id="' + esc(item.instanceId) + (structureField ? '" data-prompt-structure-field="' + esc(structureField) : '"') + (isMedia ? '" data-media-stage-dropzone aria-label="媒体积木，可在卡片任意位置拖入媒体文件替换"' : '"') + '>' +
      '<div class="stage-block-content"><div class="stage-block-copy-content"><div class="stage-block-top"><span class="stage-index">' + String(index + 1).padStart(2, "0") + '</span><span class="stage-type-label">' + typeLabel + '</span></div>' +
      titleMarkup +
      ((tags.length || importWorkflowButton) ? '<div class="stage-block-tags">' + importWorkflowButton + tags.map(function (tag) { return '<span class="block-tag">' + esc(tag) + '</span>'; }).join("") + '</div>' : "") +
      textMarkup +
      '</div></div>' + (hasStagePreview ? '<div class="stage-block-preview" data-stage-preview>' + stagePreviewMarkup + '</div>' : '') + '<div class="stage-block-actions"><button class="stage-action remove" type="button" data-remove-stage="' + index + '">移除</button></div></article>';
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
      var editButton = card.querySelector("[data-edit-stage]");
      if (editButton) editButton.dataset.editStage = String(index);
      var editor = card.querySelector("[data-stage-text]");
      if (editor) editor.dataset.stageText = String(index);
    });
  }
  function stageEmptyMarkup() {
    return '<div class="stage-empty"><span class="stage-empty-mark">01</span><strong>组装台还是空的</strong><span>从左侧点击积木或动作，拖动媒体积木，或加入一块自由文本开始。</span></div>';
  }
  function renderStage() {
    var list = $("stageList");
    if (!state.stage.length) {
      list.innerHTML = stageEmptyMarkup();
      updateStageDom();
      renderOutput();
      return;
    }
    list.innerHTML = state.stage.map(function (item, index) {
      return stageBlockMarkup(item, index);
    }).join("");
    updateStageDom();
    renderOutput();
    hydrateMediaStagePreviews();
  }
  function renderOutput() {
    var output = getPromptText();
    var pending = pendingTranslationItems();
    $("promptOutput").value = output;
    $("promptOutput").placeholder = pending.length ? "还有 " + pending.length + " 块自由文本未翻译；翻译后才会进入英文提示词" : "加入积木后，组合文本会在这里出现";
    $("charCount").textContent = output.length + " 字";
    $("lineCount").textContent = output ? output.split(/\n\s*\n/).length + " 段" : "0 段";
    ["copyPrompt", "importPrompt", "downloadPrompt"].forEach(function (id) {
      var button = $(id);
      if (button) button.disabled = pending.length > 0 || !output;
    });
    var importMediaButton = $("importMedia");
    if (importMediaButton) {
      var workflowContext = currentWorkflowContext();
      var hasImportableMedia = usedReferenceMedia().some(function (entry) { return entry.media.kind === "image" || entry.media.kind === "audio"; });
      importMediaButton.hidden = !workflowContext.hasMiniMax || !hasImportableMedia;
      importMediaButton.title = !workflowContext.hasMiniMax
        ? "当前工作流不包含 MiniMax H3 节点"
        : (hasImportableMedia ? "按当前组装台的参考媒体重建 MiniMax H3 输入" : "请先把媒体积木加入提示词工作台");
    }
    refreshSubjectDefinitionsButton();
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
    renderCategoryFilters();
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
          '<div class="group-entry-actions"><button class="group-action load" type="button" data-load-group="' + esc(group.id) + '">加载</button><button class="group-action overwrite" type="button" data-overwrite-group="' + esc(group.id) + '" title="用当前组装台覆盖此组状态">覆盖</button><button class="group-action delete" type="button" data-delete-group="' + esc(group.id) + '">删除</button></div>' +
          '</article>';
      }).join("");
    }
    var saveButton = $("saveGroup");
    if (saveButton) saveButton.textContent = state.activeGroupId ? "覆盖保存" : "新建并保存";
    var groupCount = $("groupTabCount");
    if (groupCount) groupCount.textContent = String(state.groups.length);
  }
  function stageItemFromLibrary(id) {
    if (id === "__free_text__") return { instanceId: makeId("text"), kind: "text", title: "自由文本", text: "", translatedText: "", segments: [], tags: [] };
    if (id === "__media__") return { instanceId: makeId("media"), kind: "media", title: "媒体积木", mediaPath: "", mediaName: "", mediaKind: "", mediaMime: "", previewKind: "", previewUrl: "" };
    if (state.libraryMode === "pose" || state.libraryMode === "actions") {
      var action = state.actions.find(function (item) { return item.id === id; });
      if (!action) return null;
      return {
        instanceId: makeId("action"),
        kind: "action",
        sourceId: action.id,
        category: action.category || "未分类",
        title: action.title,
        text: action.text,
        tags: action.tags || [],
        imageUrl: action.color_image_url || action.image_url || "",
        colorImageUrl: action.color_image_url || action.image_url || "",
        imagePath: action.color_image_path || action.image_path || "",
        colorImagePath: action.color_image_path || action.image_path || "",
        depthImageUrl: action.depth_image_url || "",
        depthImagePath: action.depth_image_path || "",
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
        imagePath: reference.image_path || "",
        audioUrl: reference.audio_url || "",
        audioPath: reference.audio_path || "",
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
    updateStageDom();
    renderOutput();
    if (item.kind === "text") {
      var editor = document.querySelector('[data-stage-text="' + safeIndex + '"]');
      if (editor) editor.focus();
    } else if (item.kind === "media") {
      var mediaZone = document.querySelector('[data-media-stage-index="' + safeIndex + '"]');
      if (mediaZone) {
        activeMediaStageIndex = safeIndex;
        var mediaSelect = mediaZone.querySelector("[data-open-media-stage]");
        if (mediaSelect) mediaSelect.focus();
      }
    }
    showToast(item.kind === "text" ? "已加入一块自由文本" : (item.kind === "media" ? "已加入媒体积木，请选择媒体" : "已加入「" + item.title + "」"));
  }
  function addTextBlock() {
    insertLibraryBlock("__free_text__", state.stage.length);
  }
  function updateTranslationDom(index) {
    var item = state.stage[index];
    if (!item || item.kind !== "text") return;
    var card = document.querySelector('.stage-block[data-stage-index="' + index + '"]');
    if (!card) return;
    var translation = card.querySelector('[data-stage-translation]');
    if (translation) translation.value = resolveTranslationTemplate(item, item.translatedText || "");
    var status = card.querySelector('[data-stage-translation-status]');
    if (status) {
      status.textContent = item.translatedText ? "已翻译" : "等待翻译";
      status.classList.toggle("is-ready", Boolean(item.translatedText));
    }
  }
  function preserveTranslationWhitespace(source, translated) {
    var value = String(translated || "").trim();
    var leading = (String(source || "").match(/^\s*/) || [""])[0];
    var trailing = (String(source || "").match(/\s*$/) || [""])[0];
    return leading + value + trailing;
  }
  function translateTextSegments(item) {
    if (!Array.isArray(item.segments)) {
      return jsonRequest("/api/prompt/translate", "POST", { text: String(item.text || "") }).then(function (data) {
        return String(data.translated_text || "").trim();
      });
    }
    return Promise.all(item.segments.map(function (segment) {
      if (segment.type === "reference") return Promise.resolve(null);
      var source = String(segment.text || "");
      if (!source.trim()) return Promise.resolve(source);
      return jsonRequest("/api/prompt/translate", "POST", { text: source }).then(function (data) {
        var translated = String(data.translated_text || "").trim();
        if (!translated) throw new Error("阿里云没有返回英文结果");
        return preserveTranslationWhitespace(source, translated);
      });
    })).then(function (translatedSegments) {
      return item.segments.map(function (segment, index) {
        return segment.type === "reference" ? REFERENCE_SENTINEL_PREFIX + index + "__" : String(translatedSegments[index] == null ? segment.text || "" : translatedSegments[index]);
      }).join("");
    });
  }
  function translateTextBlock(index) {
    var item = state.stage[index];
    if (!item || item.kind !== "text") return;
    if (item.translationDisabled) return;
    var sourceText = String(item.text || "").trim();
    if (!sourceText) return showToast("请先在输入框中填写自由文本", true);
    var button = document.querySelector('.translate-text-button[data-translate-stage="' + index + '"]');
    if (!button || button.disabled) return;
    button.disabled = true;
    button.classList.add("is-loading");
    button.querySelector("span").textContent = "翻译中…";
    var hasReferences = Array.isArray(item.segments) && item.segments.some(function (segment) { return segment.type === "reference"; });
    if (hasReferences && !literalTextFromItem(item)) {
      item.translatedText = translationTemplate(item);
      saveState();
      updateTranslationDom(index);
      renderOutput();
      button.disabled = false;
      button.classList.remove("is-loading");
      button.querySelector("span").textContent = "翻译";
      return showToast("卡片引用已加入提示词");
    }
    translateTextSegments(item).then(function (translatedText) {
      var currentIndex = state.stage.indexOf(item);
      if (currentIndex === -1 || String(item.text || "").trim() !== sourceText) return;
      item.translatedText = String(translatedText || "").trim();
      if (!item.translatedText) throw new Error("阿里云没有返回英文结果");
      saveState();
      updateTranslationDom(currentIndex);
      renderOutput();
      showToast("自由文本已翻译为英文");
    }).catch(function (error) {
      showToast("翻译失败：" + error.message, true);
    }).finally(function () {
      var currentButton = document.querySelector('.stage-block[data-stage-instance-id="' + CSS.escape(String(item.instanceId)) + '"] .translate-text-button');
      if (!currentButton) return;
      currentButton.disabled = false;
      currentButton.classList.remove("is-loading");
      currentButton.querySelector("span").textContent = "翻译";
    });
  }
  function updateStageAfterRemoval(index, card) {
    var list = $("stageList");
    if (card && card.isConnected) card.remove();
    if (!state.stage.length) {
      list.innerHTML = stageEmptyMarkup();
    }
    updateStageDom();
    renderOutput();
  }
  function removeStage(index) {
    var removed = state.stage[index];
    if (!removed) return;
    var card = $("stageList").querySelector('.stage-block[data-stage-index="' + index + '"]');
    state.stage.splice(index, 1);
    saveState();
    updateStageAfterRemoval(index, card);
    showToast("已从工作台移除「" + (removed.title || "当前积木") + "」");
  }
  function editStage(index) {
    var item = state.stage[index];
    if (!item) return;
    editingBlockId = "";
    editingStageIndex = index;
    $("customBlockCategoryField").hidden = true;
    $("customBlockTitle").textContent = "编辑组装台积木";
    $("customBlockModal").querySelector(".section-kicker").textContent = "EDIT ASSEMBLY BLOCK";
    $("customBlockDescription").textContent = "这里只修改当前组装台中的这一块，不会改变积木库里的原始内容。";
    $("customBlockForm").querySelector('button[type="submit"]').textContent = "保存到组装台";
    $("customBlockForm").reset();
    var textField = $("customBlockText").closest(".field-group");
    var tagsField = $("customBlockTags").closest(".field-group");
    if (textField) textField.hidden = item.kind === "media";
    if (tagsField) tagsField.hidden = item.kind === "media";
    $("customBlockNameLabel").textContent = item.kind === "media" ? "媒体名称" : "积木名称";
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
      updateStageAfterRemoval(sourceIndex, drag.card);
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
  function overwriteGroup(groupId, button) {
    var group = state.groups.find(function (item) { return item.id === groupId; });
    if (!group || !window.confirm("用当前组装台覆盖组状态「" + group.name + "」吗？")) return;
    button.disabled = true;
    jsonRequest("/api/prompt/groups", "POST", {
      id: group.id,
      name: group.name,
      items: state.stage.map(stageItemToApi),
    }).then(function (data) {
      var updated = data.group;
      var index = state.groups.findIndex(function (item) { return item.id === updated.id; });
      if (index === -1) state.groups.unshift(updated);
      else state.groups[index] = updated;
      state.activeGroupId = updated.id;
      $("groupName").value = updated.name;
      renderGroups();
      showToast("组状态「" + updated.name + "」已覆盖");
    }).catch(function (error) {
      showToast("组状态覆盖失败：" + error.message, true);
    }).finally(function () {
      button.disabled = false;
    });
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
  function resourceMediaSlots(kind) {
    return RESOURCE_MEDIA_SLOTS[kind] || [];
  }
  function resourceMediaFileName(file) {
    return String(file && file.name || "clipboard-image").trim() || "clipboard-image";
  }
  function mediaKindFromPath(path) {
    var value = String(path || "").toLowerCase().split(/[?#]/, 1)[0];
    if (/\.(avif|bmp|gif|jpeg|jpg|png|webp)$/.test(value)) return "image";
    if (/\.(aac|flac|m4a|mp3|ogg|wav)$/.test(value)) return "audio";
    if (/\.(avi|flv|mkv|mov|mp4|m4v|webm|wmv)$/.test(value)) return "video";
    return "";
  }
  function mediaKindFromFile(file) {
    var type = String(file && file.type || "").toLowerCase().split(";", 1)[0];
    if (type.indexOf("image/") === 0) return "image";
    if (type.indexOf("audio/") === 0) return "audio";
    if (type.indexOf("video/") === 0) return "video";
    return mediaKindFromPath(file && file.name);
  }
  function mediaTypeLabel(kind) {
    return ({ image: "图片", audio: "音频", video: "视频" }[kind] || "媒体");
  }
  function localPathForMediaFile(file, event) {
    if (window.rhElectron && typeof window.rhElectron.getPathForFile === "function") {
      try {
        var electronPath = String(window.rhElectron.getPathForFile(file) || "").trim();
        if (isAbsoluteLocalPath(electronPath)) return electronPath;
      } catch (error) {}
    }
    var directPath = String(file && file.path || "").trim();
    if (isAbsoluteLocalPath(directPath)) return directPath;
    var transfer = event && event.dataTransfer;
    if (!transfer || typeof transfer.getData !== "function") return "";
    var uri = String(transfer.getData("text/uri-list") || "").split(/\r?\n/).find(function (item) { return item.indexOf("file://") === 0; });
    if (!uri) return "";
    try {
      var path = decodeURIComponent(new URL(uri).pathname);
      return isAbsoluteLocalPath(path) ? path : "";
    } catch (error) {
      return "";
    }
  }
  function promptMediaPreviewMarkup(item, index) {
    var kind = item.mediaKind || item.previewKind || mediaKindFromPath(item.mediaPath);
    var previewUrl = String(item.previewUrl || "");
    var title = item.mediaName || item.title || "媒体文件";
    if (!previewUrl && !item.mediaPath) return '<div class="stage-media-preview stage-media-missing stage-media-empty-preview"><span>暂无媒体预览</span></div>';
    if (!previewUrl) return '<div class="stage-media-preview stage-media-missing"><span>' + (item.previewUnavailable ? "暂无媒体预览" : "加载预览中…") + '</span></div>';
    if (kind === "image") {
      return '<button class="stage-media-preview stage-media-image image-preview-trigger" type="button" data-image-preview="' + esc(previewUrl) + '" data-image-title="' + esc(title) + '" aria-label="放大查看「' + esc(title) + '」"><img src="' + esc(previewUrl) + '" alt="' + esc(title) + '" loading="lazy" /></button>';
    }
    if (kind === "video") {
      return '<div class="stage-media-preview stage-media-video"><video controls preload="metadata" playsinline src="' + esc(previewUrl) + '"></video></div>';
    }
    if (kind === "audio") {
      return referenceMediaMarkup({ title: title, audioUrl: previewUrl, audio_available: true, media_type: "audio" }, "stage-media-audio");
    }
    return '<div class="stage-media-preview stage-media-missing"><span>无法识别媒体类型</span></div>';
  }
  function materializePromptMedia(file, event) {
    var kind = mediaKindFromFile(file);
    if (!kind) return Promise.reject(new Error("仅支持图片、音频或视频文件"));
    var localPath = localPathForMediaFile(file, event);
    if (localPath) {
      return jsonRequest("/api/preview-file", "POST", { path: localPath }).then(function (asset) {
        return Object.assign({}, asset, { display_name: resourceMediaFileName(file), media_kind: asset.media_kind || kind });
      });
    }
    return fileToBase64(file).then(function (data) {
      return jsonRequest("/api/prompt/media", "POST", {
        name: resourceMediaFileName(file),
        mime: String(file && file.type || ""),
        data: data,
      });
    });
  }
  function setMediaBlockLoading(stageIndex, loading) {
    var card = stageIndex == null ? null : document.querySelector('.stage-block[data-stage-index="' + stageIndex + '"]');
    if (!card) return;
    card.classList.toggle("is-loading", Boolean(loading));
    card.setAttribute("aria-busy", loading ? "true" : "false");
  }
  function addMediaFile(file, event, stageIndex) {
    if (mediaBlockPickerBusy) return;
    if (!file || !mediaKindFromFile(file)) return showToast("请选择图片、音频或视频文件", true);
    var targetIndex = Number.isInteger(stageIndex) ? stageIndex : activeMediaStageIndex;
    var existing = targetIndex != null ? state.stage[targetIndex] : null;
    if (targetIndex == null || !existing || existing.kind !== "media") return showToast("请先把媒体积木加入提示词工作台", true);
    mediaBlockPickerBusy = true;
    setMediaBlockLoading(targetIndex, true);
    materializePromptMedia(file, event).then(function (asset) {
      var mediaKind = asset.media_kind || asset.preview_kind || mediaKindFromFile(file);
      var mediaName = asset.display_name || asset.name || resourceMediaFileName(file);
      state.stage[targetIndex] = {
        instanceId: existing.instanceId || makeId("media"),
        kind: "media",
        title: mediaName,
        mediaPath: String(asset.path || ""),
        mediaName: mediaName,
        mediaKind: mediaKind,
        mediaMime: String(asset.mime || file.type || ""),
        previewKind: asset.preview_kind || mediaKind,
        previewUrl: String(asset.preview_url || ""),
      };
      saveState();
      renderStage();
      showToast("已加入「" + mediaName + "」媒体积木");
    }).catch(function (error) {
      showToast("媒体导入失败：" + error.message, true);
    }).finally(function () {
      mediaBlockPickerBusy = false;
      activeMediaStageIndex = null;
      setMediaBlockLoading(targetIndex, false);
    });
  }
  function openMediaBlockPicker(stageIndex) {
    var picker = $("mediaBlockPicker");
    if (!picker || mediaBlockPickerBusy) return;
    activeMediaStageIndex = Number.isInteger(stageIndex) ? stageIndex : activeMediaStageIndex;
    if (activeMediaStageIndex == null || !state.stage[activeMediaStageIndex] || state.stage[activeMediaStageIndex].kind !== "media") return showToast("请先把媒体积木加入提示词工作台", true);
    picker.value = "";
    picker.click();
  }
  function pasteMediaStageImage(stageIndex) {
    var item = Number.isInteger(stageIndex) ? state.stage[stageIndex] : null;
    if (!item || item.kind !== "media") return showToast("请先把媒体积木加入提示词工作台", true);
    activeMediaStageIndex = stageIndex;
    readClipboardImage().then(function (file) {
      addMediaFile(file, undefined, stageIndex);
    }).catch(function (error) {
      showToast(error.message, true);
    });
  }
  function hydrateMediaStagePreviews() {
    state.stage.forEach(function (item) {
      if (!item || item.kind !== "media" || !item.mediaPath || item.previewUrl || item.previewUnavailable) return;
      var key = String(item.instanceId || item.mediaPath);
      if (mediaPreviewRequests[key]) return;
      mediaPreviewRequests[key] = true;
      jsonRequest("/api/preview-file", "POST", { path: item.mediaPath }).then(function (asset) {
        if (state.stage.indexOf(item) === -1) return;
        item.previewUrl = String(asset.preview_url || "");
        item.previewKind = asset.preview_kind || item.mediaKind;
        item.mediaMime = asset.mime || item.mediaMime;
        item.previewUnavailable = !item.previewUrl;
        renderStage();
      }).catch(function () {
        if (state.stage.indexOf(item) === -1) return;
        item.previewUnavailable = true;
        renderStage();
      }).finally(function () {
        delete mediaPreviewRequests[key];
      });
    });
  }
  function fileToBase64(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () {
        var result = String(reader.result || "");
        var separator = result.indexOf(",");
        if (separator < 0) return reject(new Error("无法读取文件"));
        resolve(result.slice(separator + 1));
      };
      reader.onerror = function () { reject(new Error("无法读取文件")); };
      reader.readAsDataURL(file);
    });
  }
  function readClipboardImage() {
    if (!navigator.clipboard || typeof navigator.clipboard.read !== "function") return Promise.reject(new Error("当前环境不支持直接读取剪贴板，请按 ⌘V / Ctrl+V"));
    return navigator.clipboard.read().then(function (items) {
      for (var i = 0; i < items.length; i += 1) {
        var types = items[i].types || [];
        for (var j = 0; j < types.length; j += 1) {
          if (String(types[j]).indexOf("image/") === 0) return items[i].getType(types[j]);
        }
      }
      throw new Error("剪贴板中没有图片，请先复制图片");
    });
  }
  function clipboardImageFromEvent(event) {
    var items = event && event.clipboardData && event.clipboardData.items;
    if (!items) return null;
    for (var i = 0; i < items.length; i += 1) {
      var item = items[i];
      if (item && item.kind === "file" && String(item.type || "").indexOf("image/") === 0) {
        var file = item.getAsFile();
        if (file) return file;
      }
    }
    return null;
  }
  function resourceMediaMatches(slot, file) {
    var type = String(file && file.type || "").toLowerCase();
    var name = resourceMediaFileName(file).toLowerCase();
    if (slot.role === "audio") return type.indexOf("audio/") === 0 || /\.(aac|flac|m4a|mp3|ogg|wav|webm)$/i.test(name);
    return type.indexOf("image/") === 0 || /\.(avif|bmp|gif|jpeg|jpg|png|webp)$/i.test(name);
  }
  function resourceMediaPath(pathId) {
    var input = $(pathId);
    return input ? String(input.value || "").trim() : "";
  }
  function resourceMediaPathName(path) {
    var value = String(path || "").trim();
    return value ? value.split(/[\\/]/).pop() : "";
  }
  function renderResourceMediaSlots() {
    var container = $("resourceMediaSlots");
    if (!container) return;
    var slots = resourceMediaSlots(editingResourceKind);
    container.innerHTML = slots.map(function (slot) {
      var selected = pendingResourceMedia[slot.role];
      var existing = resourceMediaPath(slot.pathId);
      var status = selected ? "待保存 · " + resourceMediaFileName(selected) : (existing ? "已关联 · " + resourceMediaPathName(existing) : "尚未选择素材");
      var statusClass = selected || existing ? " is-ready" : "";
      var pasteButton = slot.paste
        ? '<button class="resource-media-slot-button" type="button" data-resource-media-paste="' + esc(slot.role) + '">粘贴图片</button>'
        : "";
      var generateButton = slot.autoDepth
        ? '<button class="resource-media-slot-button resource-media-generate-button" type="button" data-resource-media-generate="depth"' + (depthGenerationBusy ? " disabled" : "") + '>' + (depthGenerationBusy ? "生成中…" : "自动生成") + '</button>'
        : "";
      return '<div class="resource-media-slot' + (selected ? " is-selected" : "") + '" tabindex="0" role="group" data-resource-media-slot="' + esc(slot.role) + '">' +
        '<div class="resource-media-slot-head"><span class="resource-media-slot-label">' + esc(slot.label) + '</span><span class="resource-media-slot-status' + statusClass + '" title="' + esc(status) + '">' + esc(status) + '</span></div>' +
        '<div class="resource-media-slot-actions"><button class="resource-media-slot-button" type="button" data-resource-media-pick="' + esc(slot.role) + '" data-resource-media-accept="' + esc(slot.accept) + '">选择文件</button>' + pasteButton + generateButton + '</div>' +
        '<span class="resource-media-slot-drop-hint">也可以把文件拖到这里' + (slot.paste ? "，或直接按 ⌘V / Ctrl+V" : "") + "。</span>" +
        '</div>';
    }).join("");
  }
  function setResourceMediaFile(role, file) {
    var slot = resourceMediaSlots(editingResourceKind).find(function (item) { return item.role === role; });
    if (!slot || !file) return;
    if (!resourceMediaMatches(slot, file)) return showToast(slot.role === "audio" ? "请选择音频文件" : "请选择图片文件", true);
    activeResourceMediaRole = role;
    pendingResourceMedia[role] = file;
    if (role === "color" && pendingResourceMedia.depth && pendingResourceMedia.depth.generated) delete pendingResourceMedia.depth;
    renderResourceMediaSlots();
  }
  function chooseResourceMedia(role) {
    var slot = resourceMediaSlots(editingResourceKind).find(function (item) { return item.role === role; });
    var picker = $("resourceMediaPicker");
    if (!slot || !picker) return;
    activeResourceMediaRole = role;
    picker.accept = slot.accept;
    picker.value = "";
    picker.click();
  }
  function pasteResourceMedia(role) {
    var slot = resourceMediaSlots(editingResourceKind).find(function (item) { return item.role === role; });
    if (!slot) return;
    if (!slot.paste) return showToast("音频请使用选择文件或拖拽方式添加", true);
    activeResourceMediaRole = role;
    readClipboardImage().then(function (file) {
      setResourceMediaFile(role, file);
    }).catch(function (error) {
      showToast(error.message, true);
    });
  }
  function generateActionDepth() {
    if (editingResourceKind !== "action" || depthGenerationBusy) return;
    var colorFile = pendingResourceMedia.color || null;
    var sourcePath = colorFile ? "" : resourceMediaPath("resourceImagePath");
    if (!colorFile && !sourcePath) return showToast("请先选择原图", true);
    depthGenerationBusy = true;
    renderResourceMediaSlots();
    var sourcePromise = colorFile
      ? (colorFile.data_url
        ? Promise.resolve({ name: resourceMediaFileName(colorFile), mime: String(colorFile.type || "image/png"), data: String(colorFile.data_url).split(",").pop() })
        : fileToBase64(colorFile).then(function (data) { return { name: resourceMediaFileName(colorFile), mime: String(colorFile.type || ""), data: data }; }))
      : Promise.resolve(null);
    sourcePromise.then(function (source) {
      return jsonRequest("/api/prompt/actions/generate-depth", "POST", source ? { source: source } : { source_path: sourcePath });
    }).then(function (generated) {
      if (colorFile && pendingResourceMedia.color !== colorFile) {
        throw new Error("原图已变化，请重新生成深度图");
      }
      pendingResourceMedia.depth = {
        name: generated.name || "generated_depth.png",
        type: generated.mime || "image/png",
        data_url: generated.data_url || "data:image/png;base64," + String(generated.data || ""),
        generated: true,
      };
      $("resourceDepthPath").value = "";
      renderResourceMediaSlots();
      showToast("深度图已生成，保存动作后写入媒体库");
    }).catch(function (error) {
      showToast("深度图生成失败：" + error.message, true);
    }).finally(function () {
      depthGenerationBusy = false;
      renderResourceMediaSlots();
    });
  }
  function handleResourceMediaPaste(event) {
    if (!editingResourceKind || $("customBlockModal").hidden) return;
    var file = clipboardImageFromEvent(event);
    if (!file) return;
    var slot = resourceMediaSlots(editingResourceKind).find(function (item) { return item.role === activeResourceMediaRole; }) || resourceMediaSlots(editingResourceKind)[0];
    if (!slot || !slot.paste) return;
    event.preventDefault();
    setResourceMediaFile(slot.role, file);
  }
  function resourceMediaPayload() {
    var entries = Object.keys(pendingResourceMedia).filter(function (role) { return pendingResourceMedia[role]; });
    return Promise.all(entries.map(function (role) {
      var file = pendingResourceMedia[role];
      if (file.data_url) {
        return Promise.resolve({ role: role, name: resourceMediaFileName(file), mime: String(file.type || "image/png"), data: String(file.data_url).split(",").pop() });
      }
      return fileToBase64(file).then(function (data) {
        return { role: role, name: resourceMediaFileName(file), mime: String(file.type || ""), data: data };
      });
    }));
  }
  function resetResourceEditor() {
    editingResourceKind = "";
    editingResourceId = "";
    pendingResourceMedia = {};
    activeResourceMediaRole = "image";
    depthGenerationBusy = false;
    visionRecognitionBusy = false;
    $("resourceMediaFields").hidden = true;
    $("resourceVisionButton").hidden = true;
    $("resourceImagePath").value = "";
    $("resourceDepthPath").value = "";
    $("resourceAudioPath").value = "";
    $("resourceMediaSlots").innerHTML = "";
    var textField = $("customBlockText").closest(".field-group");
    var tagsField = $("customBlockTags").closest(".field-group");
    if (textField) textField.hidden = false;
    if (tagsField) tagsField.hidden = false;
    $("customBlockNameLabel").textContent = "积木名称";
    $("customBlockTextLabel").textContent = "固定文本";
  }
  function openCustomModal(block) {
    editingBlockId = block ? block.id : "";
    editingStageIndex = null;
    resetResourceEditor();
    $("customBlockCategoryField").hidden = false;
    $("customBlockTitle").textContent = editingBlockId ? "编辑固定积木" : "添加固定积木";
    $("customBlockModal").querySelector(".section-kicker").textContent = editingBlockId ? "EDIT BLOCK" : "CUSTOM BLOCK";
    $("customBlockDescription").textContent = "把你经常重复使用的表达保存下来，下次直接从积木库加入；保存修改会同步当前基础积木源文件。";
    $("customBlockForm").querySelector('button[type="submit"]').textContent = editingBlockId ? "保存修改" : "保存积木";
    $("customBlockForm").reset();
    renderResourceCategoryOptions("blocks");
    if (block) {
      setEditorCategory(block.category || "未分类");
      $("customBlockName").value = block.title || "";
      $("customBlockText").value = block.text || "";
      $("customBlockTags").value = (block.tags || []).join("，");
    }
    window.RHMotion.openModal("customBlockModal", "customBlockName");
  }
  function openResourceModal(kind, resourceId) {
    var resource = kind === "action"
      ? state.actions.find(function (item) { return item.id === resourceId; })
      : state.references.find(function (item) { return item.id === resourceId; });
    if (resourceId && !resource) return;
    editingBlockId = "";
    editingStageIndex = null;
    editingResourceKind = kind;
    editingResourceId = resource ? resource.id : "";
    $("customBlockCategoryField").hidden = false;
    $("customBlockTitle").textContent = (resource ? "编辑" : "添加") + (RESOURCE_LABELS[kind] || "参考资源");
    $("customBlockModal").querySelector(".section-kicker").textContent = resource ? "EDIT RESOURCE" : "NEW RESOURCE";
    $("customBlockDescription").textContent = "保存后会把选择的素材复制到 ref 对应目录，并将相对路径回写到 Markdown 文件。";
    $("customBlockForm").querySelector('button[type="submit"]').textContent = resource ? "保存修改" : "保存资源";
    $("customBlockNameLabel").textContent = (RESOURCE_LABELS[kind] || "资源") + "名称";
    $("customBlockTextLabel").textContent = "文本内容";
    $("customBlockForm").reset();
    var resourceCategory = resource ? resource.category || "未分类" : "未分类";
    $("customBlockName").value = resource ? resource.title || "" : "";
    $("customBlockText").value = resource ? resource.text || "" : "";
    $("customBlockTags").value = resource ? (resource.source_tags || resource.tags || []).join("，") : "";
    $("resourceMediaFields").hidden = false;
    $("resourceVisionButton").hidden = kind === "audio";
    $("resourceVisionButton").disabled = false;
    $("resourceVisionButton").textContent = "AI 识图填充";
    $("resourceImagePath").value = resource ? resource.image_path || resource.color_image_path || "" : "";
    $("resourceDepthPath").value = resource ? resource.depth_image_path || "" : "";
    $("resourceAudioPath").value = resource ? resource.audio_path || "" : "";
    pendingResourceMedia = {};
    activeResourceMediaRole = resourceMediaSlots(kind)[0] ? resourceMediaSlots(kind)[0].role : "image";
    renderResourceMediaSlots();
    $("resourceMediaHint").textContent = kind === "action"
      ? "动作素材会自动复制到 ref/pose/color 和 ref/pose/depth，并保持原图与深度图的文件名配对。"
      : "素材会自动复制到 ref/" + kind + "；已有素材无需重新选择，选择新文件即可替换。";
    renderResourceCategoryOptions(kind);
    setEditorCategory(resourceCategory);
    window.RHMotion.openModal("customBlockModal", "customBlockName");
  }
  function recognizeResourceImage() {
    if (visionRecognitionBusy || !editingResourceKind || editingResourceKind === "audio") return;
    var sourceFile = editingResourceKind === "action" ? pendingResourceMedia.color : pendingResourceMedia.image;
    var sourcePath = sourceFile ? "" : resourceMediaPath("resourceImagePath");
    if (!sourceFile && !sourcePath) return showToast("请先选择原图，再使用 AI 识图填充", true);
    var sourceIdentity = sourceFile || sourcePath;
    var button = $("resourceVisionButton");
    visionRecognitionBusy = true;
    button.disabled = true;
    button.textContent = "识图中…";
    var request = sourceFile
      ? fileToBase64(sourceFile).then(function (data) { return { image: "data:" + String(sourceFile.type || "image/png") + ";base64," + data }; })
      : Promise.resolve({ image_path: sourcePath });
    request.then(function (payload) {
      payload.kind = editingResourceKind;
      return jsonRequest("/api/prompt/vision", "POST", payload);
    }).then(function (response) {
      var currentSource = editingResourceKind === "action" ? pendingResourceMedia.color || resourceMediaPath("resourceImagePath") : pendingResourceMedia.image || resourceMediaPath("resourceImagePath");
      if (sourceIdentity !== currentSource) throw new Error("原图已变化，请重新识图");
      var result = response.recognition || {};
      if (result.title) $("customBlockName").value = result.title;
      if (result.text) $("customBlockText").value = result.text;
      if (Array.isArray(result.tags)) $("customBlockTags").value = result.tags.join("，");
      showToast("已填充标题、文本和标签；一级分类请自行选择或填写");
    }).catch(function (error) {
      showToast("AI 识图失败：" + error.message, true);
    }).finally(function () {
      visionRecognitionBusy = false;
      button.disabled = false;
      button.textContent = "AI 识图填充";
    });
  }
  function closeCustomModal() {
    window.RHMotion.closeModal("customBlockModal");
    editingBlockId = "";
    editingStageIndex = null;
    resetResourceEditor();
    $("customBlockCategoryField").hidden = false;
    $("customBlockTitle").textContent = "添加固定积木";
    $("customBlockModal").querySelector(".section-kicker").textContent = "CUSTOM BLOCK";
    $("customBlockDescription").textContent = "把你经常重复使用的表达保存下来，下次直接从积木库加入；保存修改会同步当前基础积木源文件。";
    $("customBlockForm").querySelector('button[type="submit"]').textContent = "保存积木";
    $("customBlockNameLabel").textContent = "积木名称";
    $("customBlockTextLabel").textContent = "固定文本";
    $("customBlockForm").reset();
  }
  function editBlock(blockId) {
    var block = state.libraryBlocks.find(function (item) { return item.id === blockId; });
    if (block) openCustomModal(block);
  }
  function copyPrompt() {
    var output = promptTextForAction("复制");
    if (!output) return;
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
    var output = promptTextForAction("导出");
    if (!output) return;
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
    $("closeImagePreview").setAttribute("aria-label", "关闭图片预览：" + (title || "动作图片"));
    window.RHMotion.openModal("imagePreviewModal", "closeImagePreview");
  }
  function closeImagePreview() {
    window.RHMotion.closeModal("imagePreviewModal");
  }
  function openTextPreview(content, title) {
    $("textPreviewTitle").textContent = title || "文本预览";
    $("textPreviewContent").textContent = content || "暂无文本内容";
    window.RHMotion.openModal("textPreviewModal", "closeTextPreview");
  }
  function closeTextPreview() {
    window.RHMotion.closeModal("textPreviewModal");
  }
  function importPromptToTask() {
    var output = promptTextForAction("导入");
    if (!output) return;
    try {
      localStorage.setItem(TASK_PROMPT_IMPORT_KEY, JSON.stringify({ version: 1, text: output, createdAt: Date.now() }));
      showToast("提示词已写入任务提交页草稿");
    } catch (error) {
      showToast("导入失败：无法保存本机跳转数据", true);
    }
  }
  function bindEvents() {
    updateThemeToggle();
    initPromptGridSplitter();
    $("toggleLibraryExpand").addEventListener("click", function () {
      setLibraryExpanded(!state.libraryExpanded);
    });
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
      state.categoryFilter = "全部";
      state.filter = "全部";
      renderCategoryFilters();
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
    $("categoryFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-filter-category]");
      if (!button) return;
      state.categoryFilter = button.dataset.filterCategory;
      state.filter = "全部";
      renderCategoryFilters();
      renderFilters();
      renderLibrary();
    });
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
      var overwriteButton = event.target.closest("[data-overwrite-group]");
      if (overwriteButton) return overwriteGroup(overwriteButton.dataset.overwriteGroup, overwriteButton);
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
      var textPreviewButton = event.target.closest("[data-text-preview]");
      if (textPreviewButton) return openTextPreview(textPreviewButton.dataset.textContent, textPreviewButton.dataset.textTitle);
      var resourceEditButton = event.target.closest("[data-edit-resource]");
      if (resourceEditButton) return openResourceModal(resourceEditButton.dataset.resourceKind, resourceEditButton.dataset.resourceId);
      var workflowImportButton = event.target.closest("[data-import-workflow]");
      if (workflowImportButton) return openWorkflowImportFromTrigger(workflowImportButton);
      var importButton = event.target.closest("[data-import-depth]");
      if (importButton) {
        var action = state.actions.find(function (item) { return item.id === importButton.dataset.importDepth; });
        return openDepthImport(action);
      }
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
      var mediaCard = event.target.closest("[data-add-media-block]");
      if (!card && !freeCard && !mediaCard) return;
      if (card && event.target.closest("button")) return event.preventDefault();
      state.draggedIndex = null;
      state.draggedLibraryId = card ? (card.dataset.libraryBlockId || card.dataset.actionId || card.dataset.referenceId) : (freeCard ? "__free_text__" : "__media__");
      state.dragPreviewIndex = null;
      clearDropIndicators();
      (card || freeCard || mediaCard).classList.add("dragging");
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
      syncTextItemFromEditor(state.stage[index], editor);
      saveState();
      updateTranslationDom(index);
      renderOutput();
      refreshReferenceSuggestions(editor, state.stage[index]);
    });
    $("stageList").addEventListener("compositionstart", function (event) {
      if (event.target.closest("[data-stage-text]")) referenceComposing = true;
    });
    $("stageList").addEventListener("compositionend", function (event) {
      var editor = event.target.closest("[data-stage-text]");
      if (!editor) return;
      referenceComposing = false;
      window.setTimeout(function () { refreshReferenceSuggestions(editor, state.stage[Number(editor.dataset.stageText)]); }, 0);
    });
    $("stageList").addEventListener("keydown", function (event) {
      var editor = event.target.closest("[data-stage-text]");
      if (!editor || event.isComposing || referenceComposing) return;
      if (referenceSuggest.open && referenceSuggest.editor === editor) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          var delta = event.key === "ArrowDown" ? 1 : -1;
          var total = referenceSuggest.candidates.length;
          if (total) {
            referenceSuggest.selectedIndex = (referenceSuggest.selectedIndex + delta + total) % total;
            renderReferenceSuggestions();
          }
          return;
        }
        if (event.key === "Enter" || event.key === "Tab") {
          if (!referenceSuggest.candidates.length) return;
          event.preventDefault();
          return commitReferenceSuggestion(referenceSuggest.candidates[referenceSuggest.selectedIndex]);
        }
        if (event.key === "Escape") {
          event.preventDefault();
          return closeReferenceSuggestions();
        }
      }
      if (event.key === "Backspace" && removePreviousReferenceToken(editor)) {
        event.preventDefault();
        var index = Number(editor.dataset.stageText);
        if (state.stage[index]) {
          syncTextItemFromEditor(state.stage[index], editor);
          saveState();
          renderOutput();
        }
      }
    });
    $("stageList").addEventListener("click", function (event) {
      var mediaStageButton = event.target.closest("[data-open-media-stage]");
      if (mediaStageButton) return openMediaBlockPicker(Number(mediaStageButton.dataset.mediaStageIndex));
      var mediaPasteButton = event.target.closest("[data-paste-media-stage]");
      if (mediaPasteButton) return pasteMediaStageImage(Number(mediaPasteButton.dataset.mediaStageIndex));
      var translateButton = event.target.closest("[data-translate-stage]");
      if (translateButton) return translateTextBlock(Number(translateButton.dataset.translateStage));
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
    $("promptReferenceSuggest").addEventListener("pointerdown", function (event) {
      var option = event.target.closest("[data-reference-suggestion-index]");
      if (!option) return;
      event.preventDefault();
      commitReferenceSuggestion(referenceSuggest.candidates[Number(option.dataset.referenceSuggestionIndex)]);
    });
    $("promptReferenceSuggest").addEventListener("wheel", function (event) {
      if (!referenceSuggest.open) return;
      var list = $("promptReferenceSuggestionList");
      if (!list) return;
      event.preventDefault();
      event.stopPropagation();
      list.scrollTop += event.deltaY;
    }, { passive: false });
    $("stageList").addEventListener("pointerover", function (event) {
      var token = event.target.closest && event.target.closest(".prompt-reference-token");
      if (token) showReferenceHover(token);
    });
    $("stageList").addEventListener("pointerout", function (event) {
      var token = event.target.closest && event.target.closest(".prompt-reference-token");
      if (!token || (event.relatedTarget && token.contains(event.relatedTarget))) return;
      closeReferenceHover();
    });
    $("stageList").addEventListener("pointerdown", function (event) {
      var card = event.target.closest(".stage-block");
      if (!card || event.target.closest("button, textarea, input, select, a, [contenteditable], .stage-block-preview, .stage-media-preview, .stage-action-media, .stage-reference-media")) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      state.draggedIndex = Number(card.dataset.stageIndex);
      state.draggedLibraryId = "";
      if (card.classList.contains("media")) {
        activeMediaStageIndex = Number(card.dataset.stageIndex);
      }
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
      var mediaZone = event.target.closest("[data-media-stage-dropzone]");
      if (mediaZone && !state.draggedLibraryId) {
        event.preventDefault();
        mediaZone.classList.add("is-dragging");
        if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
        return;
      }
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
      var mediaZone = event.target.closest("[data-media-stage-dropzone]");
      if (mediaZone && !mediaZone.contains(event.relatedTarget)) mediaZone.classList.remove("is-dragging");
      var card = event.target.closest("[data-stage-index]");
      if (card && !card.contains(event.relatedTarget)) card.classList.remove("drop-target");
      var emptyStage = event.target.closest(".stage-empty");
      if (emptyStage && !emptyStage.contains(event.relatedTarget)) emptyStage.classList.remove("drop-target");
    });
    $("stageList").addEventListener("drop", function (event) {
      var mediaZone = event.target.closest("[data-media-stage-dropzone]");
      if (mediaZone && !state.draggedLibraryId) {
        event.preventDefault();
        mediaZone.classList.remove("is-dragging");
        var mediaFile = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
        if (mediaFile) addMediaFile(mediaFile, event, Number(mediaZone.dataset.mediaStageIndex));
        return;
      }
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
    $("importMedia").addEventListener("click", importMinimaxMediaToTask);
    $("downloadPrompt").addEventListener("click", downloadPrompt);
    $("generateSubjectDefinitions").addEventListener("click", generateSubjectDefinitions);
    $("addTextStage").addEventListener("click", addTextBlock);
    $("newGroup").addEventListener("click", startNewGroup);
    $("groupForm").addEventListener("submit", function (event) {
      event.preventDefault();
      saveGroup();
    });
    $("openCustomBlock").addEventListener("click", function () {
      if (state.libraryMode === "blocks") return openCustomModal();
      openResourceModal(state.libraryMode === "pose" || state.libraryMode === "actions" ? "action" : state.libraryMode);
    });
    $("closeCustomBlock").addEventListener("click", closeCustomModal);
    $("cancelCustomBlock").addEventListener("click", closeCustomModal);
    $("customBlockModal").addEventListener("click", function (event) { if (event.target === $("customBlockModal")) closeCustomModal(); });
    $("resourceMediaSlots").addEventListener("click", function (event) {
      var pickButton = event.target.closest("[data-resource-media-pick]");
      if (pickButton) return chooseResourceMedia(pickButton.dataset.resourceMediaPick);
      var pasteButton = event.target.closest("[data-resource-media-paste]");
      if (pasteButton) return pasteResourceMedia(pasteButton.dataset.resourceMediaPaste);
      var generateButton = event.target.closest("[data-resource-media-generate]");
      if (generateButton) return generateActionDepth();
      var slot = event.target.closest("[data-resource-media-slot]");
      if (slot) chooseResourceMedia(slot.dataset.resourceMediaSlot);
    });
    $("resourceMediaSlots").addEventListener("keydown", function (event) {
      var slot = event.target.closest("[data-resource-media-slot]");
      if (!slot || event.target.closest("button")) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        chooseResourceMedia(slot.dataset.resourceMediaSlot);
      }
    });
    $("resourceMediaSlots").addEventListener("dragover", function (event) {
      var slot = event.target.closest("[data-resource-media-slot]");
      if (!slot) return;
      event.preventDefault();
      slot.classList.add("is-dragging");
    });
    $("resourceMediaSlots").addEventListener("dragleave", function (event) {
      var slot = event.target.closest("[data-resource-media-slot]");
      if (slot && !slot.contains(event.relatedTarget)) slot.classList.remove("is-dragging");
    });
    $("resourceMediaSlots").addEventListener("drop", function (event) {
      var slot = event.target.closest("[data-resource-media-slot]");
      if (!slot) return;
      event.preventDefault();
      slot.classList.remove("is-dragging");
      var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) setResourceMediaFile(slot.dataset.resourceMediaSlot, file);
    });
    $("resourceMediaPicker").addEventListener("change", function (event) {
      var file = event.target.files && event.target.files[0];
      if (file) setResourceMediaFile(activeResourceMediaRole, file);
      event.target.value = "";
    });
    $("mediaBlockPicker").addEventListener("change", function (event) {
      var file = event.target.files && event.target.files[0];
      var stageIndex = activeMediaStageIndex;
      if (file) addMediaFile(file, undefined, stageIndex);
      event.target.value = "";
    });
    $("resourceVisionButton").addEventListener("click", recognizeResourceImage);
    $("customBlockCategorySelect").addEventListener("change", function () {
      if (this.value) $("customBlockCategory").value = "";
    });
    $("customBlockCategory").addEventListener("input", function () {
      if (this.value.trim()) $("customBlockCategorySelect").value = "";
    });
    document.addEventListener("paste", handleResourceMediaPaste);
    $("closeImagePreview").addEventListener("click", closeImagePreview);
    $("imagePreviewModal").addEventListener("click", function (event) { if (event.target === $("imagePreviewModal")) closeImagePreview(); });
    $("closeTextPreview").addEventListener("click", closeTextPreview);
    $("textPreviewModal").addEventListener("click", function (event) { if (event.target === $("textPreviewModal")) closeTextPreview(); });
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
      var category = $("customBlockCategory").value.trim() || $("customBlockCategorySelect").value.trim() || "未分类";
      if (!name) return showToast("请填写名称", true);
      var stageIndex = editingStageIndex;
      if (stageIndex != null) {
        var stageItem = state.stage[stageIndex];
        if (!stageItem) return closeCustomModal();
        if (stageItem.kind !== "media" && !text) return showToast("请填写积木文本", true);
        stageItem.title = name;
        if (stageItem.kind === "media") {
          stageItem.mediaName = name;
        } else {
          stageItem.text = text;
          stageItem.tags = parseTags($("customBlockTags").value);
        }
        closeCustomModal();
        saveState();
        renderStage();
        showToast("组装台积木已更新，积木库未改变");
        return;
      }
      if (editingResourceKind) {
        var resourceKind = editingResourceKind;
        var resourceId = editingResourceId;
        var resourcePayload = {
          kind: resourceKind,
          category: category,
          title: name,
          text: text,
          tags: parseTags($("customBlockTags").value),
          image_path: $("resourceImagePath").value.trim(),
          audio_path: $("resourceAudioPath").value.trim(),
          color_image_path: $("resourceImagePath").value.trim(),
          depth_image_path: $("resourceDepthPath").value.trim(),
        };
        var resourceSubmitButton = event.target.querySelector('button[type="submit"]');
        resourceSubmitButton.disabled = true;
        var resourceEndpoint = resourceKind === "action"
          ? "/api/prompt/actions" + (resourceId ? "/" + encodeURIComponent(resourceId) : "")
          : "/api/prompt/references" + (resourceId ? "/" + encodeURIComponent(resourceId) : "");
        var resourceMethod = resourceId ? "PUT" : "POST";
        resourceMediaPayload().then(function (media) {
          if (media.length) resourcePayload.media = media;
          if (!text && !resourcePayload.image_path && !resourcePayload.audio_path && !media.length) {
            throw new Error("请填写文本或添加媒体文件");
          }
          return jsonRequest(resourceEndpoint, resourceMethod, resourcePayload);
        }).then(function (data) {
          var updated = resourceKind === "action" ? data.action : data.reference;
          if (resourceKind === "action") {
            if (resourceId) {
              var actionIndex = state.actions.findIndex(function (item) { return item.id === resourceId; });
              if (actionIndex !== -1) state.actions[actionIndex] = updated;
            } else {
              state.actions.push(updated);
            }
          } else if (resourceId) {
            var referenceIndex = state.references.findIndex(function (item) { return item.id === resourceId; });
            if (referenceIndex !== -1) state.references[referenceIndex] = updated;
          } else {
            state.references.push(updated);
          }
          state.stage.forEach(function (item) {
            if (item.sourceId !== resourceId || item.kind !== (resourceKind === "action" ? "action" : "reference")) return;
            item.title = updated.title;
            item.text = updated.text || "";
            item.tags = updated.tags || [];
            item.missing = false;
          });
          closeCustomModal();
          renderAll();
          showToast((resourceId ? "资源已更新并同步 Markdown" : "资源已添加并同步 Markdown"));
        }).catch(function (error) {
          showToast("资源保存失败：" + error.message, true);
        }).finally(function () {
          resourceSubmitButton.disabled = false;
        });
        return;
      }
      if (!text) return showToast("请填写积木名称和固定文本", true);
      var blockId = editingBlockId;
      var submitButton = event.target.querySelector('button[type="submit"]');
      submitButton.disabled = true;
      var endpoint = blockId ? "/api/prompt/library/" + encodeURIComponent(blockId) : "/api/prompt/library";
      var method = blockId ? "PUT" : "POST";
      jsonRequest(endpoint, method, { category: category, title: name, text: text, tags: parseTags($("customBlockTags").value) }).then(function (data) {
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
        showToast(blockId ? "固定积木已更新并同步源文件" : "固定积木已保存");
      }).catch(function (error) {
        showToast("积木保存失败：" + error.message, true);
      }).finally(function () {
        submitButton.disabled = false;
      });
    });
    document.addEventListener("keydown", function (event) {
      if (referenceSuggest.open && event.key === "Escape") {
        event.preventDefault();
        closeReferenceSuggestions();
        return;
      }
      if (event.key !== "Escape") return;
      closeCustomModal();
      closeImagePreview();
      closeTextPreview();
      closeDepthImport();
    });
    document.addEventListener("pointerdown", function (event) {
      if (referenceSuggest.open) {
        if (!event.target.closest("#promptReferenceSuggest, [data-stage-text]")) closeReferenceSuggestions();
      }
      if (referenceHover.token && !event.target.closest("#promptReferenceHover, .prompt-reference-token")) closeReferenceHover();
    });
    window.addEventListener("resize", function () {
      positionReferenceHover();
      positionReferenceSuggestions();
    });
  }

  bindEvents();
  loadState().then(function () {
    applyPendingTaskPromptGroup();
    renderAll();
  }).catch(function (error) {
    renderAll();
    showToast("积木数据读取失败：" + error.message, true);
  });
  window.addEventListener("storage", function (event) {
    if (event.key === "rh-workflow-desk-draft-v1") renderOutput();
  });
})();
