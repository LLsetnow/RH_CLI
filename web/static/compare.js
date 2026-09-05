(function () {
  "use strict";

  var COMPARE_PAGE_SIZE = 20;
  var state = {
    outputs: [],
    localAssets: [],
    sourceFilter: "all",
    assetPage: 1,
    slots: { a: null, b: null },
    mode: "split",
    panX: 0,
    panY: 0,
    zoom: 1,
    divider: 50,
    panSession: null,
    dividerSession: null,
    video: { playing: false, duration: 0, raf: 0 },
  };
  var localAssetSequence = 0;
  var statusTimer = 0;
  var compareStateKey = "rh-workflow-compare-v1";
  var compareAssetDbName = "rh-workflow-desk-compare-v1";
  var compareAssetStoreName = "local-assets";
  var persistenceReady = false;

  function $(id) { return document.getElementById(id); }
  function clamp(value, minimum, maximum) { return Math.max(minimum, Math.min(maximum, value)); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }
  function request(path) {
    return fetch(path, { headers: { Accept: "application/json" } }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.message || "读取成片失败");
        return data;
      });
    });
  }
  function readCompareState() {
    try {
      var raw = window.localStorage.getItem(compareStateKey);
      var saved = raw ? JSON.parse(raw) : null;
      return saved && saved.version === 1 ? saved : null;
    } catch (error) {
      return null;
    }
  }
  function persistedAsset(asset) {
    if (!asset) return null;
    return {
      id: String(asset.id || ""),
      name: String(asset.name || ""),
      display_type: String(asset.display_type || ""),
      mime: String(asset.mime || ""),
      task_name: String(asset.task_name || ""),
      source: String(asset.source || "output"),
      size: Number(asset.size || 0),
    };
  }
  function saveCompareState() {
    if (!persistenceReady) return;
    var payload = {
      version: 1,
      slots: { a: persistedAsset(state.slots.a), b: persistedAsset(state.slots.b) },
      mode: state.mode,
      panX: state.panX,
      panY: state.panY,
      zoom: state.zoom,
      divider: state.divider,
    };
    try { window.localStorage.setItem(compareStateKey, JSON.stringify(payload)); } catch (error) {}
    [state.slots.a, state.slots.b].forEach(persistLocalAsset);
  }
  function openCompareAssetDb() {
    if (!window.indexedDB) return Promise.resolve(null);
    return new Promise(function (resolve, reject) {
      var openRequest;
      try { openRequest = window.indexedDB.open(compareAssetDbName, 1); } catch (error) { reject(error); return; }
      openRequest.onupgradeneeded = function (event) {
        var database = event.target.result;
        if (!database.objectStoreNames.contains(compareAssetStoreName)) database.createObjectStore(compareAssetStoreName, { keyPath: "id" });
      };
      openRequest.onsuccess = function () { resolve(openRequest.result); };
      openRequest.onerror = function () { reject(openRequest.error || new Error("无法打开本地素材存储")); };
    });
  }
  function persistLocalAsset(asset) {
    if (!asset || asset.source !== "local" || !asset.file) return;
    openCompareAssetDb().then(function (database) {
      if (!database) return;
      var transaction = database.transaction(compareAssetStoreName, "readwrite");
      transaction.objectStore(compareAssetStoreName).put({
        id: String(asset.id),
        name: String(asset.name || "本地素材"),
        mime: String(asset.mime || ""),
        size: Number(asset.size || 0),
        blob: asset.file,
      });
    }).catch(function () {});
  }
  function restoreLocalAssets(ids) {
    var wanted = (ids || []).filter(function (id, index, list) { return id && list.indexOf(id) === index; });
    if (!wanted.length) return Promise.resolve([]);
    return openCompareAssetDb().then(function (database) {
      if (!database) return [];
      return new Promise(function (resolve) {
        var transaction = database.transaction(compareAssetStoreName, "readonly");
        var store = transaction.objectStore(compareAssetStoreName);
        var restored = [];
        wanted.forEach(function (id) {
          var getRequest = store.get(id);
          getRequest.onsuccess = function () {
            var record = getRequest.result;
            if (!record || !record.blob) return;
            var type = assetTypeFromFilename(record.name, record.mime);
            if (!type) return;
            restored.push({
              id: String(record.id),
              name: String(record.name || "本地素材"),
              display_type: type,
              mime: String(record.mime || ""),
              url: URL.createObjectURL(record.blob),
              file: record.blob,
              task_name: "本地文件",
              source: "local",
              size: Number(record.size || 0),
            });
          };
        });
        transaction.oncomplete = function () { resolve(restored); };
        transaction.onerror = function () { resolve([]); };
      });
    }).catch(function () { return []; });
  }
  function restoreViewState(saved) {
    if (!saved) return;
    state.mode = saved.mode === "overlay" ? "overlay" : "split";
    state.panX = Number.isFinite(Number(saved.panX)) ? Number(saved.panX) : 0;
    state.panY = Number.isFinite(Number(saved.panY)) ? Number(saved.panY) : 0;
    state.zoom = clamp(Number.isFinite(Number(saved.zoom)) ? Number(saved.zoom) : 1, .35, 6);
    state.divider = clamp(Number.isFinite(Number(saved.divider)) ? Number(saved.divider) : 50, 5, 95);
  }
  function restoreSavedSlots(saved) {
    var savedSlots = saved && saved.slots && typeof saved.slots === "object" ? saved.slots : {};
    var localIds = [savedSlots.a, savedSlots.b].map(function (asset) {
      return asset && asset.source === "local" ? String(asset.id || "") : "";
    }).filter(Boolean);
    return restoreLocalAssets(localIds).then(function (restored) {
      state.localAssets = restored.concat(state.localAssets);
      var restoreAsset = function (descriptor) {
        if (!descriptor || !descriptor.id) return null;
        return findAsset(descriptor.id) || null;
      };
      state.slots.a = restoreAsset(savedSlots.a);
      state.slots.b = restoreAsset(savedSlots.b);
      if (state.slots.a && state.slots.b && state.slots.a.display_type !== state.slots.b.display_type) state.slots.b = null;
      if ([state.slots.a, state.slots.b].some(function (asset) { return asset && asset.display_type === "video"; })) state.mode = "overlay";
      var missing = [savedSlots.a, savedSlots.b].filter(function (descriptor) { return descriptor && descriptor.id && !findAsset(descriptor.id); });
      return missing;
    });
  }
  function showStatus(message, kind) {
    var status = $("compareStatus");
    if (!status) return;
    window.clearTimeout(statusTimer);
    status.textContent = message || "";
    status.className = "compare-status" + (kind ? " is-" + kind : "");
    if (message) {
      statusTimer = window.setTimeout(function () {
        status.textContent = "";
        status.className = "compare-status";
      }, 4200);
    }
  }
  function typeLabel(type) { return type === "video" ? "VIDEO" : "IMAGE"; }
  function outputUrl(item) {
    return "/api/tasks/" + encodeURIComponent(item.task_id) + "/output/" + encodeURIComponent(item.file_index);
  }
  function assetFromOutput(item) {
    return {
      id: String(item.id),
      name: String(item.name || "未命名成片"),
      display_type: String(item.display_type || ""),
      mime: String(item.mime || ""),
      url: outputUrl(item),
      task_name: String(item.task_name || ""),
      source: "output",
      size: Number(item.size || 0),
    };
  }
  function assetTypeFromFilename(name, mime) {
    var lowerName = String(name || "").toLowerCase();
    var lowerMime = String(mime || "").toLowerCase();
    if (lowerMime === "video" || lowerMime === "image") return lowerMime;
    if (lowerMime.indexOf("video/") === 0 || /\.(mp4|m4v|mov|webm|ogv|avi|mkv|wmv)$/i.test(lowerName)) return "video";
    if (lowerMime.indexOf("image/") === 0 || /\.(png|jpe?g|webp|gif|avif|bmp|tiff?)$/i.test(lowerName)) return "image";
    return "";
  }
  function localAssetFromFile(file) {
    var type = assetTypeFromFilename(file.name, file.type);
    if (!type) return null;
    var asset = {
      id: "local:" + Date.now().toString(36) + ":" + (++localAssetSequence),
      name: file.name || "本地素材",
      display_type: type,
      mime: file.type || "",
      url: URL.createObjectURL(file),
      file: file,
      task_name: "本地文件",
      source: "local",
      size: Number(file.size || 0),
    };
    state.localAssets.unshift(asset);
    persistLocalAsset(asset);
    return asset;
  }
  function normalizeExternalAsset(value) {
    if (!value || typeof value !== "object") return null;
    var type = assetTypeFromFilename(value.name, value.mime || value.type || value.display_type);
    var url = String(value.url || "").trim();
    if (!url || !type) return null;
    return {
      id: String(value.id || "external:" + url),
      name: String(value.name || "外部素材"),
      display_type: type,
      mime: String(value.mime || ""),
      url: url,
      task_name: String(value.task_name || "成片库"),
      source: String(value.source || "output"),
      size: Number(value.size || 0),
    };
  }
  function allAssets() {
    return state.localAssets.concat(state.outputs.map(assetFromOutput));
  }
  function findAsset(id) {
    var target = String(id || "");
    return allAssets().find(function (asset) { return String(asset.id) === target; }) || null;
  }
  function transferAsset(dataTransfer) {
    if (!dataTransfer) return null;
    var raw = "";
    try { raw = dataTransfer.getData("application/x-rh-compare-asset") || dataTransfer.getData("text/plain") || ""; } catch (error) {}
    if (!raw) return null;
    try {
      var parsed = JSON.parse(raw);
      var known = parsed && parsed.id ? findAsset(parsed.id) : null;
      return known || normalizeExternalAsset(parsed);
    } catch (error) {
      return findAsset(raw);
    }
  }
  function mediaMarkup(asset, className) {
    if (!asset) return '<div class="compare-media-placeholder">等待素材</div>';
    var safeUrl = esc(asset.url);
    if (asset.display_type === "video") {
      return '<video class="' + (className || "") + '" src="' + safeUrl + '" muted playsinline preload="metadata" disablepictureinpicture></video>';
    }
    return '<img class="' + (className || "") + '" src="' + safeUrl + '" alt="' + esc(asset.name) + '" draggable="false" />';
  }
  function formatTime(seconds) {
    var value = Math.max(0, Number(seconds) || 0);
    var minutes = Math.floor(value / 60);
    var remainder = Math.floor(value % 60);
    return minutes + ":" + String(remainder).padStart(2, "0");
  }
  function formatSize(value) {
    var size = Number(value) || 0;
    if (!size) return "";
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
    return (size / (1024 * 1024)).toFixed(1) + " MB";
  }
  function comparePageCount(items) {
    return Math.max(1, Math.ceil((Array.isArray(items) ? items.length : Number(items) || 0) / COMPARE_PAGE_SIZE));
  }
  function comparePageItems(items) {
    var pageCount = comparePageCount(items);
    state.assetPage = Math.min(Math.max(Number(state.assetPage) || 1, 1), pageCount);
    var start = (state.assetPage - 1) * COMPARE_PAGE_SIZE;
    return (items || []).slice(start, start + COMPARE_PAGE_SIZE);
  }
  function resetCompareAssetPage() {
    state.assetPage = 1;
  }
  function comparePageNumberMarkup(page, currentPage) {
    var active = page === currentPage;
    return '<button class="output-page-number' + (active ? ' active' : '') + '" type="button" data-compare-page="' + page + '"' + (active ? ' aria-current="page"' : '') + ' aria-label="第 ' + page + ' 页"' + (active ? ' aria-pressed="true"' : ' aria-pressed="false"') + '>' + page + '</button>';
  }
  function renderComparePagination(totalItems) {
    var pagination = $("compareAssetPagination");
    if (!pagination) return;
    var totalPages = comparePageCount(totalItems);
    if (totalPages <= 1) {
      pagination.hidden = true;
      pagination.innerHTML = "";
      return;
    }
    state.assetPage = Math.min(Math.max(Number(state.assetPage) || 1, 1), totalPages);
    var currentPage = state.assetPage;
    var pageNumbers = [];
    var start = Math.max(2, currentPage - 2);
    var end = Math.min(totalPages - 1, currentPage + 2);
    pageNumbers.push(comparePageNumberMarkup(1, currentPage));
    if (start > 2) pageNumbers.push('<span class="output-page-ellipsis" aria-hidden="true">…</span>');
    for (var page = start; page <= end; page += 1) pageNumbers.push(comparePageNumberMarkup(page, currentPage));
    if (end < totalPages - 1) pageNumbers.push('<span class="output-page-ellipsis" aria-hidden="true">…</span>');
    if (totalPages > 1) pageNumbers.push(comparePageNumberMarkup(totalPages, currentPage));
    pagination.hidden = false;
    pagination.innerHTML = '<button class="output-page-button" type="button" data-compare-page="previous"' + (currentPage === 1 ? ' disabled' : '') + ' aria-label="上一页">上一页</button>' +
      '<div class="output-page-numbers" role="list" aria-label="页码">' + pageNumbers.join("") + '</div>' +
      '<span class="output-page-status">第 ' + currentPage + ' / ' + totalPages + ' 页 · 当前显示 ' + Math.min(COMPARE_PAGE_SIZE, Math.max(0, totalItems - (currentPage - 1) * COMPARE_PAGE_SIZE)) + ' 张</span>' +
      '<button class="output-page-button" type="button" data-compare-page="next"' + (currentPage === totalPages ? ' disabled' : '') + ' aria-label="下一页">下一页</button>';
  }
  function assetCardMarkup(asset) {
    var sourceLabel = asset.source === "local" ? "本地文件" : (asset.task_name || "成片库");
    return '<article class="compare-asset-card" draggable="true" tabindex="0" data-asset-id="' + esc(asset.id) + '" aria-label="拖拽 ' + esc(asset.name) + ' 到对比槽">' +
      '<div class="compare-asset-thumb">' + mediaMarkup(asset) + '</div>' +
      '<div class="compare-asset-copy"><div class="compare-asset-type ' + esc(asset.display_type) + '"><span>' + typeLabel(asset.display_type) + '</span><span>' + esc(formatSize(asset.size)) + '</span></div>' +
      '<strong title="' + esc(asset.name) + '">' + esc(asset.name) + '</strong><small title="' + esc(sourceLabel) + '">' + esc(sourceLabel) + '</small>' +
      '<div class="compare-asset-actions"><button type="button" data-add-slot="a">放入 A</button><button type="button" data-add-slot="b">放入 B</button></div></div></article>';
  }
  function renderAssets() {
    var list = $("compareAssetList");
    var filteredAssets = allAssets().filter(function (asset) {
      return state.sourceFilter === "all" || asset.display_type === state.sourceFilter;
    });
    var assets = comparePageItems(filteredAssets);
    $("compareSourceCount").textContent = String(allAssets().length);
    renderComparePagination(filteredAssets.length);
    if (!assets.length) {
      list.innerHTML = '<div class="compare-asset-empty">还没有可用的' + (state.sourceFilter === "video" ? "视频" : state.sourceFilter === "image" ? "图片" : "图片或视频") + '。<br />从成片库拖入，或选择本地文件。</div>';
      return;
    }
    list.innerHTML = assets.map(assetCardMarkup).join("");
  }
  function updateModeTabs() {
    var hasVideo = [state.slots.a, state.slots.b].some(function (asset) { return asset && asset.display_type === "video"; });
    document.querySelectorAll("[data-compare-mode]").forEach(function (button) {
      var mode = button.dataset.compareMode;
      var disabled = mode === "split" && hasVideo;
      button.disabled = disabled;
      button.classList.toggle("active", state.mode === mode);
      button.setAttribute("aria-selected", state.mode === mode ? "true" : "false");
      button.title = disabled ? "视频对比使用覆盖模式" : "";
    });
  }
  function updateHeader() {
    var a = state.slots.a;
    var b = state.slots.b;
    var title = $("compareTitle");
    var subtitle = $("compareSubtitle");
    if (!a && !b) {
      title.textContent = "选择两份同类型素材";
      subtitle.textContent = "拖入 A / 图1 和 B / 图2，开始比较。";
      return;
    }
    if (a && b && a.display_type !== b.display_type) {
      title.textContent = "素材类型需要一致";
      subtitle.textContent = "请放入两张图片，或两个视频。图片和视频不能混合比较。";
      return;
    }
    if (!a || !b) {
      title.textContent = "等待第二份素材";
      subtitle.textContent = "再拖入一份" + ((a || b).display_type === "video" ? "视频" : "图片") + "，开始比较。";
      return;
    }
    if (a.display_type === "video") {
      title.textContent = "视频覆盖对比";
      subtitle.textContent = "两段视频已静音同步播放；拖动中央分割线检查分辨率差异。";
    } else if (state.mode === "split") {
      title.textContent = "图片左右对比";
      subtitle.textContent = "在任一画面上拖动或滚动，A / 图1 与 B / 图2 会保持相同位置和缩放。";
    } else {
      title.textContent = "图片覆盖对比";
      subtitle.textContent = "拖动中央分割线，比较图1与图2的细节差异。";
    }
  }
  function renderSlots() {
    ["a", "b"].forEach(function (slotName) {
      var slot = document.querySelector('.compare-slot[data-slot="' + slotName + '"]');
      var content = slot.querySelector(".compare-slot-content");
      var clear = slot.querySelector("[data-clear-slot]");
      var label = slot.querySelector(".compare-slot-label small");
      var asset = state.slots[slotName];
      slot.classList.toggle("is-empty", !asset);
      slot.classList.toggle("is-filled", Boolean(asset));
      clear.hidden = !asset;
      if (!asset) {
        label.textContent = slotName === "a" ? "拖入第一份" : "拖入第二份";
        content.innerHTML = "";
        return;
      }
      label.textContent = asset.display_type === "video" ? "视频" : "图片";
      content.innerHTML = mediaMarkup(asset, "compare-slot-preview") + '<div class="compare-slot-name" title="' + esc(asset.name) + '">' + esc(asset.name) + '<small>' + esc(asset.source === "local" ? "本地文件" : (asset.task_name || "成片库")) + '</small></div>';
    });
  }
  function stageMediaMarkup(asset, emptyLabel) {
    if (!asset) return '<div class="compare-media-placeholder">' + esc(emptyLabel) + '</div>';
    return '<div class="compare-transform">' + mediaMarkup(asset) + '</div>';
  }
  function renderStage() {
    var stage = $("compareStage");
    var a = state.slots.a;
    var b = state.slots.b;
    var hasVideo = [a, b].some(function (asset) { return asset && asset.display_type === "video"; });
    if (hasVideo && state.mode === "split") state.mode = "overlay";
    stage.dataset.mode = state.mode;
    updateModeTabs();
    updateHeader();
    if (!a && !b) {
      stage.innerHTML = '<div class="compare-stage-empty"><span class="compare-stage-empty-mark">A&nbsp;＋&nbsp;B</span><strong>把两份素材放进来</strong><span>图片支持同步平移和缩放；视频会以覆盖方式对齐播放。</span></div>';
      $("compareControls").hidden = true;
      applyTransform();
      return;
    }
    if (a && b && a.display_type !== b.display_type) {
      stage.innerHTML = '<div class="compare-stage-empty"><span class="compare-stage-empty-mark">A&nbsp;≠&nbsp;B</span><strong>请使用相同类型的素材</strong><span>图片与图片，或视频与视频。</span></div>';
      $("compareControls").hidden = true;
      return;
    }
    if (state.mode === "overlay") {
      stage.innerHTML = '<div class="compare-overlay-view">' +
        '<div class="compare-overlay-layer is-second">' + stageMediaMarkup(b, "等待 B / 图2") + '</div>' +
        '<div class="compare-overlay-layer is-first">' + stageMediaMarkup(a, "等待 A / 图1") + '</div>' +
        '<span class="compare-overlay-label is-first">A / 图1</span><span class="compare-overlay-label is-second">B / 图2</span>' +
        '<div class="compare-divider" role="separator" aria-label="对比分割线"></div>' +
        '</div>';
    } else {
      stage.innerHTML = '<div class="compare-split-view">' +
        '<div class="compare-pane" data-pane="a"><span class="compare-pane-tag">A / 图1</span>' + stageMediaMarkup(a, "拖入 A / 图1") + '</div>' +
        '<div class="compare-pane" data-pane="b"><span class="compare-pane-tag">B / 图2</span>' + stageMediaMarkup(b, "拖入 B / 图2") + '</div>' +
        '</div>';
    }
    bindVideoElements();
    applyTransform();
    if (a && b && a.display_type === "video") renderVideoControls();
    else $("compareControls").hidden = true;
  }
  function applyTransform() {
    var stage = $("compareStage");
    if (!stage) return;
    stage.style.setProperty("--pan-x", state.panX + "px");
    stage.style.setProperty("--pan-y", state.panY + "px");
    stage.style.setProperty("--zoom", state.zoom.toFixed(3));
    stage.style.setProperty("--divider", state.divider.toFixed(2) + "%");
  }
  function resetTransform() {
    state.panX = 0;
    state.panY = 0;
    state.zoom = 1;
    state.divider = 50;
    applyTransform();
    saveCompareState();
  }
  function compatibleWithSlot(asset, slotName) {
    var other = state.slots[slotName === "a" ? "b" : "a"];
    return !other || !asset || asset.display_type === other.display_type;
  }
  function setSlot(slotName, asset) {
    if (!asset || (asset.display_type !== "image" && asset.display_type !== "video")) return false;
    if (!compatibleWithSlot(asset, slotName)) {
      showStatus("两份素材必须是相同类型。", "error");
      return false;
    }
    state.slots[slotName] = asset;
    if (asset.display_type === "video") state.mode = "overlay";
    resetTransform();
    resetVideoState();
    renderSlots();
    renderStage();
    renderAssets();
    saveCompareState();
    showStatus("已将「" + asset.name + "」放入 " + (slotName === "a" ? "A / 图1" : "B / 图2"), "success");
    return true;
  }
  function nextSlot() {
    return state.slots.a ? (state.slots.b ? "a" : "b") : "a";
  }
  function addAsset(asset, targetSlot) {
    if (!asset) return false;
    var slotName = targetSlot || nextSlot();
    if (setSlot(slotName, asset)) return true;
    if (!targetSlot && state.slots.a && state.slots.b) return setSlot("a", asset);
    return false;
  }
  function addFiles(files, targetSlot) {
    var mediaFiles = Array.from(files || []).filter(function (file) { return Boolean(assetTypeFromFilename(file.name, file.type)); });
    if (!mediaFiles.length) {
      showStatus("这里只接受图片或视频文件。", "error");
      return;
    }
    var types = mediaFiles.map(function (file) { return assetTypeFromFilename(file.name, file.type); }).filter(function (type, index, list) { return list.indexOf(type) === index; });
    if (types.length > 1) {
      showStatus("一次只能比较两张图片，或两个视频，请不要混合选择。", "error");
      return;
    }
    var assets = mediaFiles.map(localAssetFromFile).filter(Boolean);
    if (targetSlot) {
      addAsset(assets[0], targetSlot);
      if (assets[1]) addAsset(assets[1], targetSlot === "a" ? "b" : "a");
      return;
    }
    assets.slice(0, 2).forEach(function (asset) { addAsset(asset); });
    if (assets.length > 2) showStatus("已添加前两份素材，其余文件留在素材库中。", "success");
  }
  function handleDrop(event, targetSlot) {
    event.preventDefault();
    event.stopPropagation();
    var target = event.currentTarget;
    if (target) target.classList.remove("is-dragging");
    var files = event.dataTransfer && event.dataTransfer.files;
    if (files && files.length) {
      addFiles(files, targetSlot);
      return;
    }
    addAsset(transferAsset(event.dataTransfer), targetSlot);
  }
  function bindDropTarget(element, targetSlot) {
    if (!element) return;
    ["dragenter", "dragover"].forEach(function (eventName) {
      element.addEventListener(eventName, function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
        element.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach(function (eventName) {
      element.addEventListener(eventName, function (event) {
        if (eventName === "drop") handleDrop(event, targetSlot);
        else if (!element.contains(event.relatedTarget)) element.classList.remove("is-dragging");
      });
    });
  }
  function beginPan(event) {
    if (event.button !== 0 || state.dividerSession || event.target.closest(".compare-divider, button, input")) return;
    if (!state.slots.a && !state.slots.b) return;
    var stage = $("compareStage");
    state.panSession = { x: event.clientX, y: event.clientY };
    stage.classList.add("is-panning");
    stage.setPointerCapture(event.pointerId);
    event.preventDefault();
  }
  function movePan(event) {
    if (!state.panSession || state.dividerSession) return;
    state.panX += event.clientX - state.panSession.x;
    state.panY += event.clientY - state.panSession.y;
    state.panSession.x = event.clientX;
    state.panSession.y = event.clientY;
    applyTransform();
  }
  function finishPointer(event) {
    var stage = $("compareStage");
    var changed = Boolean(state.panSession || state.dividerSession);
    if (state.panSession) {
      state.panSession = null;
      stage.classList.remove("is-panning");
    }
    if (state.dividerSession) {
      state.dividerSession = false;
      stage.classList.remove("is-dividing");
    }
    try { stage.releasePointerCapture(event.pointerId); } catch (error) {}
    if (changed) saveCompareState();
  }
  function updateDividerFromPointer(clientX) {
    var stage = $("compareStage");
    var rect = stage.getBoundingClientRect();
    if (!rect.width) return;
    state.divider = clamp((clientX - rect.left) / rect.width * 100, 5, 95);
    applyTransform();
  }
  function beginDivider(event) {
    var stage = $("compareStage");
    var divider = event.target.closest && event.target.closest(".compare-divider");
    if (stage.dataset.mode !== "overlay" || event.button !== 0 || !divider || event.target.closest("button, input, select, textarea, a")) return;
    state.panSession = null;
    state.dividerSession = true;
    stage.classList.add("is-dividing");
    stage.setPointerCapture(event.pointerId);
    updateDividerFromPointer(event.clientX);
    event.preventDefault();
    event.stopPropagation();
  }
  function moveDivider(event) {
    if (!state.dividerSession) return;
    updateDividerFromPointer(event.clientX);
  }
  function zoomAt(event) {
    if (!state.slots.a && !state.slots.b) return;
    event.preventDefault();
    var stage = $("compareStage");
    var rect = stage.getBoundingClientRect();
    var localX = event.clientX - rect.left - rect.width / 2;
    var localY = event.clientY - rect.top - rect.height / 2;
    var previousZoom = state.zoom;
    var nextZoom = clamp(previousZoom * (event.deltaY < 0 ? 1.1 : .9), .35, 6);
    state.panX = localX - (localX - state.panX) * nextZoom / previousZoom;
    state.panY = localY - (localY - state.panY) * nextZoom / previousZoom;
    state.zoom = nextZoom;
    applyTransform();
    saveCompareState();
  }
  function renderVideoControls() {
    var controls = $("compareControls");
    controls.hidden = false;
    controls.innerHTML = '<button class="compare-video-button" type="button" data-video-play aria-label="播放对比视频">▶</button>' +
      '<div class="compare-video-progress"><input type="range" min="0" max="0" step="0.01" value="0" data-video-seek aria-label="视频进度" /><span class="compare-video-time" data-video-time>0:00 / 0:00</span></div>' +
      '<span class="compare-video-note">空格播放 · ←→ 1 秒 · D 后退一帧 · F 前进一帧</span>';
    controls.querySelector("[data-video-play]").addEventListener("click", toggleVideoPlayback);
    controls.querySelector("[data-video-seek]").addEventListener("input", function () {
      var value = Number(this.value) || 0;
      getVideos().forEach(function (video) { try { video.currentTime = value; } catch (error) {} });
      updateVideoControls();
    });
    updateVideoControls();
  }
  function getVideos() { return Array.from($("compareStage").querySelectorAll("video")); }
  function resetVideoState() {
    if (state.video.raf) window.cancelAnimationFrame(state.video.raf);
    state.video = { playing: false, duration: 0, raf: 0 };
  }
  function bindVideoElements() {
    getVideos().forEach(function (video) {
      video.addEventListener("loadedmetadata", updateVideoDuration);
      video.addEventListener("timeupdate", updateVideoControls);
      video.addEventListener("ended", function () {
        pauseVideos();
        updateVideoControls();
      });
    });
  }
  function updateVideoDuration() {
    var durations = getVideos().map(function (video) { return Number(video.duration); }).filter(function (duration) { return isFinite(duration) && duration > 0; });
    state.video.duration = durations.length ? Math.min.apply(Math, durations) : 0;
    updateVideoControls();
  }
  function updateVideoControls() {
    var controls = $("compareControls");
    if (!controls || controls.hidden) return;
    var videos = getVideos();
    var current = videos.length ? Math.min.apply(Math, videos.map(function (video) { return Number(video.currentTime) || 0; })) : 0;
    var seek = controls.querySelector("[data-video-seek]");
    var time = controls.querySelector("[data-video-time]");
    var play = controls.querySelector("[data-video-play]");
    if (seek) {
      seek.max = String(state.video.duration || 0);
      seek.value = String(Math.min(current, state.video.duration || current || 0));
    }
    if (time) time.textContent = formatTime(current) + " / " + formatTime(state.video.duration);
    if (play) {
      play.textContent = state.video.playing ? "Ⅱ" : "▶";
      play.setAttribute("aria-label", state.video.playing ? "暂停对比视频" : "播放对比视频");
    }
  }
  function videoTick() {
    if (!state.video.playing) return;
    var videos = getVideos();
    if (videos.length > 1) {
      var masterTime = Number(videos[0].currentTime) || 0;
      videos.slice(1).forEach(function (video) {
        if (Math.abs((Number(video.currentTime) || 0) - masterTime) > .045) {
          try { video.currentTime = masterTime; } catch (error) {}
        }
      });
    }
    updateVideoControls();
    state.video.raf = window.requestAnimationFrame(videoTick);
  }
  function playVideos() {
    var videos = getVideos();
    if (videos.length < 2) return;
    var current = Math.min.apply(Math, videos.map(function (video) { return Number(video.currentTime) || 0; }));
    videos.forEach(function (video) {
      try { video.currentTime = current; } catch (error) {}
      video.play().catch(function () { showStatus("浏览器阻止了视频播放，请再点击一次播放。", "error"); });
    });
    state.video.playing = true;
    updateVideoControls();
    if (!state.video.raf) state.video.raf = window.requestAnimationFrame(videoTick);
  }
  function pauseVideos() {
    getVideos().forEach(function (video) { try { video.pause(); } catch (error) {} });
    state.video.playing = false;
    if (state.video.raf) window.cancelAnimationFrame(state.video.raf);
    state.video.raf = 0;
    updateVideoControls();
  }
  function toggleVideoPlayback() { if (state.video.playing) pauseVideos(); else playVideos(); }
  function seekVideos(delta, pauseAfter) {
    var videos = getVideos();
    if (videos.length < 2) return;
    var wasPlaying = state.video.playing;
    if (pauseAfter) pauseVideos();
    var current = Math.min.apply(Math, videos.map(function (video) { return Number(video.currentTime) || 0; }));
    var durations = videos.map(function (video) { return Number(video.duration); }).filter(function (duration) { return isFinite(duration) && duration > 0; });
    var limit = state.video.duration || (durations.length ? Math.min.apply(Math, durations) : Infinity);
    var next = clamp(current + delta, 0, limit);
    videos.forEach(function (video) { try { video.currentTime = next; } catch (error) {} });
    updateVideoControls();
    if (wasPlaying && !pauseAfter) {
      videos.forEach(function (video) { video.play().catch(function () {}); });
      state.video.playing = true;
      if (!state.video.raf) state.video.raf = window.requestAnimationFrame(videoTick);
    }
  }
  function isTypingTarget(target) {
    var element = target && target.nodeType === 1 ? target : null;
    if (!element) return false;
    return ["INPUT", "TEXTAREA", "SELECT"].indexOf(element.tagName) !== -1 || element.isContentEditable;
  }
  function handleVideoShortcut(event) {
    var controlTarget = event.target && event.target.closest && event.target.closest("button, a, select, [role=button], [data-asset-id]");
    if (getVideos().length < 2 || isTypingTarget(event.target) || controlTarget || event.metaKey || event.ctrlKey || event.altKey) return;
    var key = event.key;
    if (key === " ") {
      event.preventDefault();
      toggleVideoPlayback();
      return;
    }
    if (key === "ArrowRight") {
      event.preventDefault();
      seekVideos(1, false);
      return;
    }
    if (key === "ArrowLeft") {
      event.preventDefault();
      seekVideos(-1, false);
      return;
    }
    if (key === "d" || key === "D") {
      event.preventDefault();
      seekVideos(-1 / 24, true);
      return;
    }
    if (key === "f" || key === "F") {
      event.preventDefault();
      seekVideos(1 / 24, true);
    }
  }
  function setThemeToggle() {
    var themeButton = $("themeToggle");
    themeButton.addEventListener("click", function () {
      var nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem("rh-workflow-theme", nextTheme); } catch (error) {}
      var light = nextTheme === "light";
      $("themeToggleIcon").textContent = light ? "☾" : "☀";
      $("themeToggleLabel").textContent = light ? "夜间" : "日间";
      themeButton.setAttribute("aria-label", light ? "切换到夜间模式" : "切换到日间模式");
      themeButton.title = light ? "切换到夜间模式" : "切换到日间模式";
    });
  }
  function bindEvents() {
    setThemeToggle();
    var sourceDrop = $("compareSourceDrop");
    bindDropTarget(sourceDrop);
    bindDropTarget($("compareStage"));
    document.querySelectorAll(".compare-slot").forEach(function (slot) { bindDropTarget(slot, slot.dataset.slot); });
    $("compareFileInput").addEventListener("change", function () {
      addFiles(this.files);
      this.value = "";
    });
    sourceDrop.addEventListener("click", function (event) {
      if (event.target.closest("[data-choose-files]")) $("compareFileInput").click();
    });
    sourceDrop.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        $("compareFileInput").click();
      }
    });
    $("compareSourceFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-source-filter]");
      if (!button) return;
      state.sourceFilter = button.dataset.sourceFilter;
      resetCompareAssetPage();
      document.querySelectorAll("[data-source-filter]").forEach(function (item) {
        var active = item === button;
        item.classList.toggle("active", active);
        item.setAttribute("aria-selected", active ? "true" : "false");
      });
      renderAssets();
    });
    $("compareAssetPagination").addEventListener("click", function (event) {
      var button = event.target.closest("[data-compare-page]");
      if (!button || button.disabled) return;
      var nextPage = button.dataset.comparePage;
      if (nextPage === "previous") state.assetPage -= 1;
      else if (nextPage === "next") state.assetPage += 1;
      else state.assetPage = Number(nextPage);
      renderAssets();
    });
    $("compareAssetList").addEventListener("dragstart", function (event) {
      var card = event.target.closest("[data-asset-id]");
      if (!card || !event.dataTransfer) return;
      var asset = findAsset(card.dataset.assetId);
      if (!asset) return;
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("application/x-rh-compare-asset", JSON.stringify(asset));
      event.dataTransfer.setData("text/plain", JSON.stringify(asset));
      card.classList.add("is-dragging");
    });
    $("compareAssetList").addEventListener("dragend", function (event) {
      var card = event.target.closest("[data-asset-id]");
      if (card) card.classList.remove("is-dragging");
    });
    $("compareAssetList").addEventListener("click", function (event) {
      var button = event.target.closest("[data-add-slot]");
      var card = event.target.closest("[data-asset-id]");
      if (!card) return;
      var asset = findAsset(card.dataset.assetId);
      if (button) {
        event.stopPropagation();
        addAsset(asset, button.dataset.addSlot);
        return;
      }
      addAsset(asset);
    });
    $("compareAssetList").addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (event.target.closest("button")) return;
      var card = event.target.closest("[data-asset-id]");
      if (!card) return;
      event.preventDefault();
      addAsset(findAsset(card.dataset.assetId));
    });
    $("compareModeTabs").addEventListener("click", function (event) {
      var button = event.target.closest("[data-compare-mode]");
      if (!button || button.disabled) return;
      state.mode = button.dataset.compareMode;
      resetTransform();
      renderStage();
      saveCompareState();
    });
    $("resetCompareTransform").addEventListener("click", resetTransform);
    $("clearCompare").addEventListener("click", function () {
      pauseVideos();
      state.slots.a = null;
      state.slots.b = null;
      state.mode = "split";
      resetTransform();
      renderSlots();
      renderStage();
      renderAssets();
      saveCompareState();
      showStatus("已清空当前对比", "success");
    });
    document.querySelectorAll("[data-clear-slot]").forEach(function (button) {
      button.addEventListener("click", function (event) {
        event.stopPropagation();
        pauseVideos();
        state.slots[button.dataset.clearSlot] = null;
        if (!state.slots.a && !state.slots.b) state.mode = "split";
        resetTransform();
        renderSlots();
        renderStage();
        renderAssets();
        saveCompareState();
      });
    });
    var stage = $("compareStage");
    stage.addEventListener("pointerdown", beginDivider);
    stage.addEventListener("pointerdown", beginPan);
    stage.addEventListener("pointermove", moveDivider);
    stage.addEventListener("pointermove", movePan);
    stage.addEventListener("pointerup", finishPointer);
    stage.addEventListener("pointercancel", finishPointer);
    stage.addEventListener("wheel", zoomAt, { passive: false });
    stage.addEventListener("keydown", function (event) {
      if (event.key === "0") resetTransform();
    });
    document.addEventListener("keydown", handleVideoShortcut);
    window.addEventListener("pagehide", saveCompareState);
    window.addEventListener("beforeunload", function () {
      saveCompareState();
      state.localAssets.forEach(function (asset) { try { URL.revokeObjectURL(asset.url); } catch (error) {} });
    });
  }
  function finishLoading(saved, loadError) {
    restoreSavedSlots(saved).then(function (missing) {
      persistenceReady = true;
      renderSlots();
      renderStage();
      renderAssets();
      if (loadError) {
        showStatus(loadError, "error");
      } else if (missing.length) {
        showStatus("上次对比中的部分成片已不可用，请重新添加。", "error");
      }
      var queryAssetId = new URLSearchParams(window.location.search).get("add");
      if (queryAssetId) addAsset(findAsset(queryAssetId), "a");
    });
  }
  function loadOutputs(saved) {
    request("/api/outputs").then(function (data) {
      state.outputs = Array.isArray(data.outputs) ? data.outputs.filter(function (item) { return item && (item.display_type === "image" || item.display_type === "video"); }) : [];
      finishLoading(saved, "");
    }).catch(function (error) {
      finishLoading(saved, "成片库读取失败：" + error.message + "；仍可拖入本地文件。");
    });
  }
  var savedCompareState = readCompareState();
  restoreViewState(savedCompareState);
  bindEvents();
  renderSlots();
  renderStage();
  loadOutputs(savedCompareState);
}());
