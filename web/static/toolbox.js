(function () {
  "use strict";

  var state = { references: [], media: null, mode: "depth", polling: {}, pickingMedia: false, ttsVoices: [], ttsVoicesPromise: null, pendingTtsVoiceId: "", restoringDraft: false };
  var imageExtensions = /\.(avif|bmp|gif|jpe?g|png|webp)$/i;
  var mediaExtensions = /\.(avif|bmp|gif|jpe?g|png|webp|avi|flv|m4v|mkv|mov|mp4|webm|wmv)$/i;
  var MEDIA_PREVIEW_FPS = 24;
  var toolboxDraftStorageKey = "rh-workflow-desk-toolbox-draft-v1";
  var toolboxDraftSaveTimer = 0;

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
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
    return request(path, { method: method || "GET", headers: { "Accept": "application/json", "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  }
  function toast(message, isError) {
    var toast = $("toolboxToast") || $("toast");
    if (window.RHMotion && window.RHMotion.showToast) window.RHMotion.showToast(toast, message, isError);
  }
  function fileToDataUrl(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || "")); };
      reader.onerror = function () { reject(new Error("读取文件失败")); };
      reader.readAsDataURL(file);
    });
  }
  function localPathForFile(file, event) {
    try {
      if (window.rhElectron && typeof window.rhElectron.getPathForFile === "function") {
        var nativePath = window.rhElectron.getPathForFile(file);
        if (nativePath) return String(nativePath);
      }
    } catch (error) {}
    if (file && file.path) return String(file.path);
    var transfer = event && event.dataTransfer;
    var uri = transfer && transfer.getData ? transfer.getData("text/uri-list") : "";
    if (uri && uri.indexOf("file://") === 0) {
      try { return decodeURIComponent(new URL(uri.trim()).pathname); } catch (error) {}
    }
    return "";
  }
  function fileNameForValidation(file) {
    return String(file && (file.name || file.path) || "");
  }
  function fileDisplayName(file) {
    var name = String(file && file.name || "").trim();
    if (name) return name;
    var path = String(file && file.path || "");
    return path.split(/[\\/]/).pop() || "媒体文件";
  }
  function bytesStartWith(bytes, signature, offset) {
    var start = offset || 0;
    return signature.every(function (value, index) { return bytes[start + index] === value; });
  }
  function readFileSignature(file) {
    return new Promise(function (resolve) {
      if (!file || typeof file.slice !== "function" || typeof FileReader === "undefined") { resolve(null); return; }
      var reader = new FileReader();
      reader.onload = function () { resolve(new Uint8Array(reader.result || new ArrayBuffer(0))); };
      reader.onerror = function () { resolve(null); };
      reader.readAsArrayBuffer(file.slice(0, 32));
    });
  }
  function inferFileDescriptor(file) {
    var name = fileDisplayName(file);
    var mime = String(file && file.type || "").toLowerCase();
    if (imageExtensions.test(name) || mime.indexOf("image/") === 0) return Promise.resolve({ name: name, mime: mime || "image/png", kind: "image" });
    if (mediaExtensions.test(name) || mime.indexOf("video/") === 0) return Promise.resolve({ name: name, mime: mime || "video/mp4", kind: "video" });
    return readFileSignature(file).then(function (bytes) {
      if (!bytes) return null;
      if (bytesStartWith(bytes, [0x89, 0x50, 0x4e, 0x47])) return { name: "toolbox-media.png", mime: "image/png", kind: "image" };
      if (bytesStartWith(bytes, [0xff, 0xd8, 0xff])) return { name: "toolbox-media.jpg", mime: "image/jpeg", kind: "image" };
      if (bytesStartWith(bytes, [0x47, 0x49, 0x46, 0x38])) return { name: "toolbox-media.gif", mime: "image/gif", kind: "image" };
      if (bytesStartWith(bytes, [0x52, 0x49, 0x46, 0x46]) && bytesStartWith(bytes, [0x57, 0x45, 0x42, 0x50], 8)) return { name: "toolbox-media.webp", mime: "image/webp", kind: "image" };
      if (bytesStartWith(bytes, [0x52, 0x49, 0x46, 0x46]) && bytesStartWith(bytes, [0x41, 0x56, 0x49, 0x20], 8)) return { name: "toolbox-media.avi", mime: "video/avi", kind: "video" };
      if (bytesStartWith(bytes, [0x1a, 0x45, 0xdf, 0xa3])) return { name: "toolbox-media.webm", mime: "video/webm", kind: "video" };
      if (bytesStartWith(bytes, [0x66, 0x74, 0x79, 0x70], 4)) return { name: "toolbox-media.mp4", mime: "video/mp4", kind: "video" };
      return null;
    });
  }
  function materializeFile(file, event, descriptor) {
    var fileName = descriptor && descriptor.name || fileDisplayName(file);
    var fileMime = descriptor && descriptor.mime || String(file && file.type || "");
    var localPath = localPathForFile(file, event);
    if (localPath) {
      return jsonRequest("/api/preview-file", "POST", { path: localPath }).then(function (asset) {
        if (!asset || !asset.path) throw new Error("本地媒体没有可提交的路径");
        return Object.assign({}, asset, { display_name: fileName || asset.name || "媒体文件" });
      });
    }
    return fileToDataUrl(file).then(function (dataUrl) {
      return jsonRequest("/api/prompt/media", "POST", { name: fileName || "toolbox-media.png", mime: fileMime, data_url: dataUrl });
    }).then(function (asset) {
      if (!asset || !asset.path) throw new Error("媒体保存后没有返回本地路径");
      return asset;
    });
  }
  function mediaKind(asset, file) {
    var kind = String(asset && (asset.media_kind || asset.preview_kind) || "");
    if (kind) return kind;
    var mime = String(file && file.type || "");
    if (mime.indexOf("video/") === 0) return "video";
    if (mime.indexOf("image/") === 0) return "image";
    if (imageExtensions.test(fileNameForValidation(file))) return "image";
    if (mediaExtensions.test(fileNameForValidation(file))) return "video";
    return "";
  }
  function setMediaAsset(asset) {
    var kind = mediaKind(asset, null);
    if (kind !== "image" && kind !== "video") throw new Error("请选择图片或视频文件");
    if (!asset || !asset.path) throw new Error("本地媒体没有可提交的路径");
    state.media = {
      path: String(asset.path),
      name: String(asset.display_name || asset.name || "媒体文件"),
      mime: String(asset.mime || (kind === "video" ? "video/mp4" : "image/png")),
      kind: kind,
      preview_url: previewUrl(asset)
    };
    renderMedia();
    scheduleToolboxDraftSave();
  }
  function previewUrl(asset) { return String(asset && asset.preview_url || ""); }
  function readToolboxDraft() {
    try {
      var raw = window.localStorage.getItem(toolboxDraftStorageKey);
      if (!raw) return null;
      var draft = JSON.parse(raw);
      return draft && draft.version === 1 ? draft : null;
    } catch (error) {
      return null;
    }
  }
  function draftAsset(asset) {
    if (!asset || !asset.path) return null;
    return {
      path: String(asset.path),
      name: String(asset.display_name || asset.name || "媒体文件"),
      mime: String(asset.mime || ""),
      kind: String(asset.kind || asset.media_kind || asset.preview_kind || "")
    };
  }
  function toolboxDraftSnapshot() {
    var codexPrompt = $("codexPrompt");
    var codexResolution = $("codexImageResolution");
    var codexSize = $("codexImageSize");
    var mediaResolution = $("mediaResolution");
    var mediaDuration = $("mediaDuration");
    var ttsText = $("ttsText");
    var ttsVoice = $("ttsVoice");
    return {
      version: 1,
      codex: {
        prompt: String(codexPrompt && codexPrompt.value || ""),
        resolution: String(codexResolution && codexResolution.value || "1k"),
        size: String(codexSize && codexSize.value || "9:16"),
        references: state.references.map(draftAsset).filter(Boolean)
      },
      media: {
        mode: state.mode,
        resolution: String(mediaResolution && mediaResolution.value || "original"),
        duration_seconds: String(mediaDuration && mediaDuration.value || ""),
        start_frame: mediaStartFrameValue(),
        asset: draftAsset(state.media)
      },
      tts: {
        voice: String(ttsVoice && ttsVoice.value || state.pendingTtsVoiceId || ""),
        text: String(ttsText && ttsText.value || "")
      }
    };
  }
  function saveToolboxDraftNow() {
    if (state.restoringDraft) return;
    try {
      window.localStorage.setItem(toolboxDraftStorageKey, JSON.stringify(toolboxDraftSnapshot()));
    } catch (error) {}
  }
  function scheduleToolboxDraftSave() {
    if (state.restoringDraft) return;
    window.clearTimeout(toolboxDraftSaveTimer);
    toolboxDraftSaveTimer = window.setTimeout(saveToolboxDraftNow, 120);
  }
  function restoreDraftAsset(item) {
    var path = String(item && item.path || "").trim();
    if (!path) return Promise.resolve(null);
    return jsonRequest("/api/preview-file", "POST", { path: path }).then(function (asset) {
      return Object.assign({}, asset, {
        display_name: String(item.name || asset.name || "媒体文件"),
        media_kind: String(item.kind || asset.media_kind || asset.preview_kind || "")
      });
    });
  }
  function restoreToolboxDraft() {
    var draft = readToolboxDraft();
    if (!draft) return Promise.resolve();
    var codex = draft.codex && typeof draft.codex === "object" ? draft.codex : {};
    var media = draft.media && typeof draft.media === "object" ? draft.media : {};
    var tts = draft.tts && typeof draft.tts === "object" ? draft.tts : {};
    state.restoringDraft = true;
    if ($("codexPrompt")) $("codexPrompt").value = String(codex.prompt || "");
    if ($("codexImageResolution")) $("codexImageResolution").value = String(codex.resolution || "1k");
    if ($("codexImageSize")) $("codexImageSize").value = String(codex.size || "9:16");
    setMode(String(media.mode || "depth"));
    if ($("mediaResolution")) $("mediaResolution").value = String(media.resolution || "original");
    if ($("mediaDuration")) $("mediaDuration").value = media.duration_seconds == null ? "" : String(media.duration_seconds);
    if ($("mediaStartFrame")) $("mediaStartFrame").value = media.start_frame == null ? "0" : String(media.start_frame);
    if ($("ttsText")) $("ttsText").value = String(tts.text || "");
    state.pendingTtsVoiceId = String(tts.voice || "");
    var references = Array.isArray(codex.references) ? codex.references : [];
    var referencePromise = Promise.all(references.map(function (item) {
      return restoreDraftAsset(item).catch(function () { return null; });
    })).then(function (items) {
      state.references = items.filter(Boolean).map(function (asset) {
        return { path: String(asset.path || ""), name: String(asset.display_name || asset.name || "参考图"), mime: String(asset.mime || "image/png"), preview_url: previewUrl(asset) };
      });
      renderReferences();
    });
    var mediaPromise = restoreDraftAsset(media.asset).catch(function () { return null; }).then(function (asset) {
      if (asset) setMediaAsset(asset);
    });
    return Promise.all([referencePromise, mediaPromise]).then(function () {
      state.restoringDraft = false;
      saveToolboxDraftNow();
    }).catch(function () {
      state.restoringDraft = false;
    });
  }
  function bindToolboxDraftInputs() {
    ["codexPrompt", "codexImageResolution", "codexImageSize", "mediaResolution", "mediaStartFrame", "mediaDuration", "ttsText"].forEach(function (id) {
      var input = $(id);
      if (!input) return;
      ["input", "change"].forEach(function (eventName) {
        input.addEventListener(eventName, scheduleToolboxDraftSave);
      });
    });
  }
  function mediaStartFrameValue() {
    var input = $("mediaStartFrame");
    var numeric = Number(input && input.value || 0);
    return Number.isFinite(numeric) ? Math.max(0, Math.floor(numeric)) : 0;
  }
  function mediaDurationValue() {
    var input = $("mediaDuration");
    var raw = String(input && input.value || "").trim();
    if (!raw) return null;
    var numeric = Number(raw);
    return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
  }
  function formatMediaPreviewSeconds(value) {
    var numeric = Number(value);
    if (!Number.isFinite(numeric)) return "0";
    return numeric.toFixed(3).replace(/\.?(0+)$/, "").replace(/\.$/, "") || "0";
  }
  function mediaPreviewWindow(video) {
    var duration = Number(video && video.duration);
    var startFrame = mediaStartFrameValue();
    var requestedStart = startFrame / MEDIA_PREVIEW_FPS;
    var requestedDuration = mediaDurationValue();
    if (!Number.isFinite(duration) || duration <= 0) {
      return { duration: null, startFrame: startFrame, start: requestedStart, end: null, requestedDuration: requestedDuration, outOfRange: false };
    }
    var frameDuration = 1 / MEDIA_PREVIEW_FPS;
    var outOfRange = requestedStart >= duration;
    var start = Math.min(requestedStart, Math.max(0, duration - frameDuration));
    var end = requestedDuration == null ? duration : Math.min(duration, requestedStart + requestedDuration);
    end = Math.min(duration, Math.max(start + frameDuration, end));
    return { duration: duration, startFrame: startFrame, start: start, end: end, requestedDuration: requestedDuration, outOfRange: outOfRange };
  }
  function updateMediaPreviewMeta(video, previewWindow) {
    var meta = $("mediaMeta");
    if (!meta || !video || !previewWindow) return;
    var detail = meta.querySelector("[data-media-preview-meta]");
    if (!detail) return;
    var parts = ["VIDEO · " + MEDIA_PREVIEW_FPS + " FPS", "第 " + previewWindow.startFrame + " 帧起"];
    if (previewWindow.outOfRange) {
      parts.push("已定位到末帧");
    } else if (previewWindow.requestedDuration == null) {
      parts.push("全片");
    } else {
      parts.push(formatMediaPreviewSeconds(Math.max(0, previewWindow.end - previewWindow.start)) + " 秒");
    }
    detail.textContent = parts.join(" · ");
  }
  function syncMediaPreview(video, resetPosition) {
    video = video || (($("mediaPreview") && $("mediaPreview").querySelector("video")) || null);
    if (!video) return null;
    var previewWindow = mediaPreviewWindow(video);
    video.dataset.previewStart = String(previewWindow.start);
    if (previewWindow.end == null) delete video.dataset.previewEnd;
    else video.dataset.previewEnd = String(previewWindow.end);
    updateMediaPreviewMeta(video, previewWindow);
    if (previewWindow.end == null) return previewWindow;
    var current = Number(video.currentTime);
    if (resetPosition || !Number.isFinite(current) || current < previewWindow.start - 0.02 || current >= previewWindow.end - 0.02) {
      try { video.currentTime = previewWindow.start; } catch (error) {}
    }
    return previewWindow;
  }
  function handleMediaPreviewPlay(event) {
    var video = event.currentTarget;
    var previewWindow = syncMediaPreview(video, false);
    if (!previewWindow || previewWindow.end == null) return;
    var current = Number(video.currentTime);
    if (!Number.isFinite(current) || current < previewWindow.start || current >= previewWindow.end) {
      try { video.currentTime = previewWindow.start; } catch (error) {}
    }
  }
  function handleMediaPreviewTimeUpdate(event) {
    var video = event.currentTarget;
    var previewWindow = syncMediaPreview(video, false);
    if (!previewWindow || previewWindow.end == null) return;
    if (Number(video.currentTime) >= previewWindow.end - 0.02) {
      video.pause();
      try { video.currentTime = previewWindow.start; } catch (error) {}
    }
  }
  function bindMediaPreview(video) {
    if (!video) return;
    video.addEventListener("loadedmetadata", function () { syncMediaPreview(video, true); });
    video.addEventListener("durationchange", function () { syncMediaPreview(video, true); });
    video.addEventListener("play", handleMediaPreviewPlay);
    video.addEventListener("timeupdate", handleMediaPreviewTimeUpdate);
    if (video.readyState >= 1) syncMediaPreview(video, true);
  }
  function bindMediaPreviewInputs() {
    ["mediaStartFrame", "mediaDuration"].forEach(function (id) {
      var input = $(id);
      if (!input) return;
      ["input", "change"].forEach(function (eventName) {
        input.addEventListener(eventName, function () { syncMediaPreview(null, true); });
      });
    });
  }
  function renderReferences() {
    var grid = $("referenceGrid");
    if (!grid) return;
    $("referenceCount").textContent = String(state.references.length);
    var zone = $("referenceDropzone");
    if (zone) zone.classList.toggle("is-ready", state.references.length > 0);
    if (!state.references.length) {
      grid.innerHTML = '<div class="toolbox-empty-hint">暂未添加参考图</div>';
      return;
    }
    grid.innerHTML = state.references.map(function (item, index) {
      var image = item.preview_url ? '<img src="' + esc(item.preview_url) + '" alt="参考图 ' + (index + 1) + '" loading="lazy" />' : '<div class="toolbox-empty-hint">预览不可用</div>';
      return '<figure class="toolbox-reference-card">' + image + '<figcaption title="' + esc(item.name) + '">' + esc(item.name || ("参考图 " + (index + 1))) + '</figcaption><button class="toolbox-reference-remove" type="button" data-remove-reference="' + index + '" aria-label="移除第 ' + (index + 1) + ' 张参考图" title="移除">×</button></figure>';
    }).join("");
  }
  function addReferenceFiles(files, event) {
    var candidates = Array.prototype.slice.call(files || []);
    if (!candidates.length) { toast("请选择图片参考图", true); return; }
    var zone = $("referenceDropzone");
    if (zone) zone.classList.add("is-loading");
    candidates.reduce(function (chain, file) {
      return chain.then(function () {
        return inferFileDescriptor(file).then(function (descriptor) {
          if (!descriptor || descriptor.kind !== "image") return;
          return materializeFile(file, event, descriptor).then(function (asset) {
            state.references.push({ path: String(asset.path || ""), name: String(asset.display_name || asset.name || descriptor.name || "参考图"), mime: String(asset.mime || descriptor.mime || "image/png"), preview_url: previewUrl(asset) });
            renderReferences();
            scheduleToolboxDraftSave();
          });
        });
      });
    }, Promise.resolve()).catch(function (error) {
      toast("参考图导入失败：" + error.message, true);
    }).finally(function () {
      if (zone) zone.classList.remove("is-loading");
    });
  }
  function renderMedia() {
    var preview = $("mediaPreview");
    var meta = $("mediaMeta");
    var zone = $("mediaDropzone");
    var pathLabel = $("mediaPathLabel");
    var openFolderButton = $("mediaOpenFolderButton");
    if (!preview) return;
    if (!state.media) {
      if (zone) {
        zone.classList.remove("is-ready");
        zone.innerHTML = '<span class="toolbox-drop-mark" aria-hidden="true">↥</span><span><strong>拖入图片或视频</strong><small>图片处理一张；视频按 24 FPS 处理</small></span><span class="toolbox-drop-key">MEDIA</span>';
      }
      if (pathLabel) { pathLabel.textContent = "尚未选择本地媒体"; pathLabel.title = "尚未选择本地媒体"; pathLabel.classList.remove("is-ready"); }
      if (openFolderButton) openFolderButton.hidden = true;
      preview.innerHTML = '<div class="toolbox-empty-hint">等待输入媒体</div>';
      if (meta) { meta.hidden = true; meta.textContent = ""; }
      return;
    }
    var asset = state.media;
    var url = previewUrl(asset);
    var kind = asset.kind;
    if (zone) {
      zone.classList.add("is-ready");
      zone.innerHTML = '<span class="toolbox-drop-mark" aria-hidden="true">✓</span><span><strong>已识别 ' + esc(asset.name || "媒体文件") + '</strong><small>已加载本地路径，可重新选择</small></span><span class="toolbox-drop-key">' + (kind === "video" ? "VIDEO" : "IMAGE") + '</span>';
    }
    if (pathLabel) {
      pathLabel.textContent = asset.path || asset.name || "已选择媒体";
      pathLabel.title = asset.path || asset.name || "已选择媒体";
      pathLabel.classList.add("is-ready");
    }
    if (openFolderButton) openFolderButton.hidden = !asset.path;
    if (!url) preview.innerHTML = '<div class="toolbox-empty-hint">预览不可用，但仍可尝试处理</div>';
    else if (kind === "video") preview.innerHTML = '<video controls preload="metadata" playsinline data-media-preview="true" src="' + esc(url) + '"></video>';
    else preview.innerHTML = '<img src="' + esc(url) + '" alt="' + esc(asset.name) + '" />';
    if (meta) {
      meta.hidden = false;
      meta.innerHTML = '<span title="' + esc(asset.path) + '">' + esc(asset.name) + '</span><span data-media-preview-meta>' + (kind === "video" ? "VIDEO · 24 FPS · 预览全片" : "IMAGE · 单张") + '</span>';
    }
    if (kind === "video") bindMediaPreview(preview.querySelector("video"));
  }
  function setMediaFile(file, event) {
    var selectedFile = file && file.name ? file : file && file[0];
    file = selectedFile || null;
    if (!file) { toast("请选择图片或视频文件", true); return; }
    var zone = $("mediaDropzone");
    if (zone) zone.classList.add("is-loading");
    inferFileDescriptor(file).then(function (descriptor) {
      if (!descriptor) throw new Error("请选择图片或视频文件");
      return materializeFile(file, event, descriptor).then(setMediaAsset);
    }).catch(function (error) {
      toast("媒体导入失败：" + error.message, true);
    }).finally(function () {
      if (zone) zone.classList.remove("is-loading");
    });
  }
  function chooseMediaFile() {
    if (state.pickingMedia) return;
    var button = $("mediaChooseButton");
    var zone = $("mediaDropzone");
    state.pickingMedia = true;
    if (button) {
      button.disabled = true;
      button.dataset.originalLabel = button.textContent;
      button.textContent = "选择中…";
    }
    if (zone) zone.classList.add("is-loading");
    jsonRequest("/api/pick-file", "POST").then(setMediaAsset).catch(function (error) {
      if (String(error && error.message || "") !== "已取消选择文件。") toast("媒体导入失败：" + error.message, true);
    }).finally(function () {
      state.pickingMedia = false;
      if (button) {
        button.disabled = false;
        button.textContent = button.dataset.originalLabel || "选择文件";
        delete button.dataset.originalLabel;
      }
      if (zone) zone.classList.remove("is-loading");
    });
  }
  function statusLabel(status) {
    return { queued: "排队中", running: "处理中", completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断" }[String(status || "")] || "未开始";
  }
  function setMode(mode) {
    var next = ["depth", "skeleton", "depth_skeleton"].indexOf(String(mode || "")) !== -1 ? String(mode) : "depth";
    state.mode = next;
    scheduleToolboxDraftSave();
    var tabs = $("toolboxModeTabs");
    if (!tabs) return;
    tabs.querySelectorAll("[data-mode]").forEach(function (item) {
      var active = item.dataset.mode === next;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", active ? "true" : "false");
    });
  }
  function setInlineStatus(id, message, status) {
    var node = $(id);
    if (!node) return;
    node.textContent = message || "";
    node.className = "toolbox-inline-status" + (status ? " is-" + status : "");
  }
  function renderTaskResult(id, task, title) {
    var box = $(id);
    if (!box) return;
    var outputs = (task && task.outputs || []).filter(function (item) { return item && item.kind === "file"; });
    if (!outputs.length) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    box.innerHTML = '<div class="toolbox-task-result-head"><span class="toolbox-task-result-title">' + esc(title) + '</span><span class="toolbox-task-result-id">' + esc(task.id) + '</span></div><div class="toolbox-task-output-list">' + outputs.map(function (item, index) {
      return '<a class="toolbox-task-output-link" href="/api/tasks/' + encodeURIComponent(task.id) + '/output/' + index + '" target="_blank" rel="noreferrer">' + esc(item.name || "打开结果") + ' ↗</a>';
    }).join("") + '</div>';
  }
  function selectedTtsVoice() {
    var select = $("ttsVoice");
    var id = String(select && select.value || "").trim();
    return state.ttsVoices.find(function (voice) { return String(voice && voice.id || "") === id; }) || null;
  }
  function renderTtsVoiceMeta() {
    var select = $("ttsVoice");
    var meta = $("ttsVoiceMeta");
    var audioBox = $("ttsReferenceAudio");
    var voice = selectedTtsVoice();
    if (!voice) {
      if (meta) meta.innerHTML = "<strong>—</strong><small>暂未选择人物</small>";
      if (audioBox) { audioBox.hidden = true; audioBox.innerHTML = ""; }
      return Promise.resolve();
    }
    if (meta) meta.innerHTML = "<strong>" + esc(voice.name) + "</strong><small>参考音频 · " + esc(voice.reference_name || "WAV") + "</small>";
    if (!audioBox) return Promise.resolve();
    audioBox.hidden = false;
    audioBox.innerHTML = '<div class="toolbox-empty-hint">正在读取参考音频…</div>';
    return jsonRequest("/api/preview-file", "POST", { path: voice.reference_path }).then(function (asset) {
      audioBox.innerHTML = asset.preview_url ? '<audio controls preload="none" src="' + esc(asset.preview_url) + '"></audio>' : '<div class="toolbox-empty-hint">参考音频暂不可试听</div>';
    }).catch(function () {
      audioBox.innerHTML = '<div class="toolbox-empty-hint">参考音频暂不可试听</div>';
    });
  }
  function loadTtsVoices() {
    return jsonRequest("/api/tts/voices", "GET").then(function (data) {
      state.ttsVoices = Array.isArray(data.voices) ? data.voices : [];
      var select = $("ttsVoice");
      if (select) {
        select.innerHTML = state.ttsVoices.length ? state.ttsVoices.map(function (voice) {
          return '<option value="' + esc(voice.id) + '">' + esc(voice.name) + '</option>';
        }).join("") : '<option value="">没有可用人物</option>';
        var preferredVoice = state.ttsVoices.find(function (voice) { return String(voice && voice.id || "") === state.pendingTtsVoiceId; });
        if (preferredVoice) select.value = preferredVoice.id;
        else if (state.ttsVoices.length) select.value = state.ttsVoices[0].id;
      }
      if ($("ttsRuntimeStatus")) $("ttsRuntimeStatus").textContent = state.ttsVoices.length + " 个本地人物可用 · 生成时自动加载对应模型";
      return renderTtsVoiceMeta().then(function () { return state.ttsVoices; });
    }).catch(function (error) {
      state.ttsVoices = [];
      if ($("ttsRuntimeStatus")) $("ttsRuntimeStatus").textContent = error.message;
      if ($("ttsVoice")) $("ttsVoice").innerHTML = '<option value="">人物读取失败</option>';
      renderTtsVoiceMeta();
      return [];
    });
  }
  function updateTaskCard(kind, task) {
    var config = {
      codex: { status: "codexTaskStatus", result: "codexTaskResult", title: "Codex 图像结果" },
      media: { status: "mediaTaskStatus", result: "mediaTaskResult", title: "媒体处理结果" },
      tts: { status: "ttsTaskStatus", result: "ttsTaskResult", title: "角色语音结果" }
    }[kind] || { status: "mediaTaskStatus", result: "mediaTaskResult", title: "工具箱结果" };
    var statusId = config.status;
    var resultId = config.result;
    var title = config.title;
    var status = String(task && task.status || "");
    setInlineStatus(statusId, (task && task.progress) || statusLabel(status), status === "completed" ? "complete" : status === "failed" ? "error" : (status === "running" || status === "queued") ? "running" : "");
    renderTaskResult(resultId, task, title);
    if (status === "completed") toast(title + "已完成");
    if (status === "failed") toast((task.error || title + "失败"), true);
    return status;
  }
  function pollTask(kind, taskId) {
    if (!taskId) return;
    var previous = state.polling[kind];
    if (previous) window.clearTimeout(previous);
    function poll() {
      jsonRequest("/api/tasks/" + encodeURIComponent(taskId)).then(function (task) {
        var status = updateTaskCard(kind, task);
        if (status === "completed" || status === "failed" || status === "cancelled" || status === "interrupted") {
          delete state.polling[kind];
          loadRecentTasks();
          return;
        }
        state.polling[kind] = window.setTimeout(poll, 900);
      }).catch(function (error) {
        setInlineStatus(kind === "codex" ? "codexTaskStatus" : kind === "tts" ? "ttsTaskStatus" : "mediaTaskStatus", error.message, "error");
      });
    }
    poll();
  }
  function replayTask(data) {
    var task = data && data.task && typeof data.task === "object" ? data.task : {};
    var toolbox = data && data.toolbox && typeof data.toolbox === "object" ? data.toolbox : {};
    var custom = toolbox.custom_inputs && typeof toolbox.custom_inputs === "object" ? toolbox.custom_inputs : (task.custom_inputs || {});
    var files = toolbox.files && typeof toolbox.files === "object" ? toolbox.files : (task.files || {});
    var prompts = toolbox.prompts && typeof toolbox.prompts === "object" ? toolbox.prompts : (task.prompts || {});
    var tool = String(toolbox.tool || custom.tool || "").trim();
    if (tool === "codex") {
      $("codexPrompt").value = String(prompts.prompt || "");
      $("codexImageResolution").value = String(custom.resolution || "1k");
      $("codexImageSize").value = String(custom.aspect_ratio || "9:16");
      var references = Object.keys(files).filter(function (key) { return /^reference_\d+$/.test(key); }).sort(function (left, right) {
        return Number(left.split("_").pop()) - Number(right.split("_").pop());
      });
      return Promise.all(references.map(function (key) {
        var path = files[key] && typeof files[key] === "object" ? files[key].path : files[key];
        return jsonRequest("/api/preview-file", "POST", { path: path }).then(function (asset) {
          return {
            path: String(asset.path || path || ""),
            name: String(asset.name || key),
            mime: String(asset.mime || "image/png"),
            preview_url: previewUrl(asset)
          };
        });
      })).then(function (items) {
        state.references = items;
        renderReferences();
        scheduleToolboxDraftSave();
        setInlineStatus("codexTaskStatus", "已载入任务参数，可再次生成", "complete");
        toast("已恢复 Codex 图像任务参数");
      });
    }
    if (tool === "tts") {
      return (state.ttsVoicesPromise || Promise.resolve()).then(function () {
        var voiceId = String(custom.voice || "").trim();
        var select = $("ttsVoice");
        if (!selectedTtsVoice() || String(select && select.value || "") !== voiceId) {
          if (select) select.value = voiceId;
        }
        if (!selectedTtsVoice()) throw new Error("任务中的角色音色当前不可用");
        $("ttsText").value = String(prompts.text || "");
        return renderTtsVoiceMeta().then(function () {
          scheduleToolboxDraftSave();
          setInlineStatus("ttsTaskStatus", "已载入任务参数，可再次生成", "complete");
          toast("已恢复角色语音任务参数");
        });
      });
    }
    setMode(String(custom.mode || toolbox.mode || "depth"));
    var resolutionSelect = $("mediaResolution");
    if (resolutionSelect) resolutionSelect.value = String(custom.resolution || "original");
    var durationInput = $("mediaDuration");
    if (durationInput) durationInput.value = custom.duration_seconds == null ? "" : String(custom.duration_seconds);
    var startFrameInput = $("mediaStartFrame");
    if (startFrameInput) startFrameInput.value = custom.start_frame == null ? "0" : String(custom.start_frame);
    var input = files.input && typeof files.input === "object" ? files.input.path : files.input;
    if (!input) return Promise.reject(new Error("任务中没有保存输入媒体路径"));
    return jsonRequest("/api/preview-file", "POST", { path: input }).then(function (asset) {
      setMediaAsset(asset);
      setInlineStatus("mediaTaskStatus", "已载入任务参数，可再次处理", "complete");
      toast("已恢复深度与骨骼任务参数");
    });
  }
  function submitCodex() {
    var prompt = String($("codexPrompt").value || "").trim();
    if (!prompt) { toast("请输入图像生成要求", true); $("codexPrompt").focus(); return; }
    var button = $("submitCodexImage");
    button.disabled = true;
    setInlineStatus("codexTaskStatus", "正在创建本地任务…", "running");
    jsonRequest("/api/toolbox/image", "POST", {
      prompt: prompt,
      resolution: String($("codexImageResolution").value || "1k"),
      size: String($("codexImageSize").value || "9:16"),
      references: state.references.map(function (item) { return { path: item.path, name: item.name, mime: item.mime }; })
    }).then(function (data) {
      var task = data.task || data;
      $("codexTaskId").textContent = task.id || "";
      pollTask("codex", task.id);
      loadRecentTasks();
    }).catch(function (error) {
      setInlineStatus("codexTaskStatus", error.message, "error");
      toast(error.message, true);
    }).finally(function () { button.disabled = false; });
  }
  function submitMedia() {
    if (!state.media) { toast("请先导入图片或视频", true); $("mediaDropzone").focus(); return; }
    var button = $("submitMediaProcess");
    button.disabled = true;
    setInlineStatus("mediaTaskStatus", "正在创建本地任务…", "running");
    jsonRequest("/api/toolbox/media", "POST", {
      mode: state.mode,
      resolution: String($("mediaResolution").value || "original"),
      duration_seconds: String($("mediaDuration").value || "").trim() || null,
      start_frame: mediaStartFrameValue(),
      input: { path: state.media.path, name: state.media.name, mime: state.media.mime }
    }).then(function (data) {
      var task = data.task || data;
      $("mediaTaskId").textContent = task.id || "";
      pollTask("media", task.id);
      loadRecentTasks();
    }).catch(function (error) {
      setInlineStatus("mediaTaskStatus", error.message, "error");
      toast(error.message, true);
    }).finally(function () { button.disabled = false; });
  }
  function submitTts() {
    var voice = selectedTtsVoice();
    var text = String($("ttsText").value || "").trim();
    if (!voice) { toast("请先选择人物音色", true); $("ttsVoice").focus(); return; }
    if (!text) { toast("请输入语音内容", true); $("ttsText").focus(); return; }
    var button = $("submitTts");
    button.disabled = true;
    setInlineStatus("ttsTaskStatus", "正在创建本地语音任务…", "running");
    jsonRequest("/api/toolbox/tts", "POST", { voice: voice.id, text: text }).then(function (data) {
      var task = data.task || data;
      $("ttsTaskId").textContent = task.id || "";
      pollTask("tts", task.id);
      loadRecentTasks();
    }).catch(function (error) {
      setInlineStatus("ttsTaskStatus", error.message, "error");
      toast(error.message, true);
    }).finally(function () { button.disabled = false; });
  }
  function renderRecentTasks(tasks) {
    var container = $("toolboxRecentTasks");
    if (!container) return;
    var filtered = (tasks || []).filter(function (task) {
      var tool = task && task.custom_inputs && task.custom_inputs.tool;
      return ["codex", "media_processor", "tts"].indexOf(String(tool || "")) !== -1 || String(task.workflow_name || "").indexOf("工具箱") === 0;
    }).slice(0, 8);
    if (!filtered.length) { container.innerHTML = '<div class="toolbox-empty-hint">还没有工具箱任务</div>'; return; }
    container.innerHTML = filtered.map(function (task) {
      var status = String(task.status || "");
      return '<div class="toolbox-recent-task"><span class="toolbox-recent-task-title" title="' + esc(task.workflow_name) + '">' + esc(task.workflow_name || "工具箱任务") + '</span><span class="toolbox-recent-task-id" title="' + esc(task.id) + '">' + esc(task.id) + '</span><span class="toolbox-recent-task-progress" title="' + esc(task.progress) + '">' + esc(task.progress || statusLabel(status)) + '</span><span class="toolbox-recent-task-status is-' + esc(status) + '">' + esc(statusLabel(status)) + '</span></div>';
    }).join("");
  }
  function loadRecentTasks() {
    return jsonRequest("/api/state?scope=submit").then(function (data) { renderRecentTasks(data.tasks || []); return data; }).catch(function (error) { renderRecentTasks([]); toast("任务历史读取失败：" + error.message, true); });
  }
  function bindDropzone(zone, picker, handler, openPicker) {
    if (!zone || !picker) return;
    var open = openPicker || function () { picker.click(); };
    zone.addEventListener("click", open);
    zone.addEventListener("keydown", function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
    picker.addEventListener("change", function (event) { handler(event.target.files, event); picker.value = ""; });
    ["dragenter", "dragover"].forEach(function (name) { zone.addEventListener(name, function (event) { event.preventDefault(); zone.classList.add("is-dragging"); }); });
    ["dragleave", "drop"].forEach(function (name) { zone.addEventListener(name, function (event) { event.preventDefault(); zone.classList.remove("is-dragging"); }); });
    zone.addEventListener("drop", function (event) { handler(event.dataTransfer && event.dataTransfer.files, event); });
  }
  function bindModeTabs() {
    $("toolboxModeTabs").addEventListener("click", function (event) {
      var button = event.target.closest("[data-mode]");
      if (!button) return;
      setMode(button.dataset.mode);
    });
  }
  function openMediaFolder() {
    if (!state.media || !state.media.path) return;
    jsonRequest("/api/open-file-folder", "POST", { path: state.media.path }).then(function (data) {
      toast(data.message || "已打开文件所在文件夹");
    }).catch(function (error) { toast(error.message, true); });
  }
  function init() {
    if (!$("toolboxBlocks") && !$("submitWorkspacePanelCodex")) return;
    bindDropzone($("referenceDropzone"), $("referencePicker"), addReferenceFiles);
    bindDropzone($("mediaDropzone"), $("mediaPicker"), setMediaFile, chooseMediaFile);
    $("referenceGrid").addEventListener("click", function (event) { var button = event.target.closest("[data-remove-reference]"); if (!button) return; state.references.splice(Number(button.dataset.removeReference), 1); renderReferences(); scheduleToolboxDraftSave(); });
    $("submitCodexImage").addEventListener("click", submitCodex);
    $("submitMediaProcess").addEventListener("click", submitMedia);
    $("submitTts").addEventListener("click", submitTts);
    $("ttsVoice").addEventListener("change", function () { state.pendingTtsVoiceId = String($("ttsVoice").value || ""); renderTtsVoiceMeta(); scheduleToolboxDraftSave(); });
    $("mediaChooseButton").addEventListener("click", chooseMediaFile);
    $("mediaOpenFolderButton").addEventListener("click", openMediaFolder);
    bindToolboxDraftInputs();
    bindMediaPreviewInputs();
    window.addEventListener("pagehide", saveToolboxDraftNow);
    bindModeTabs();
    document.addEventListener("rh:video-frame-captured", function (event) {
      var detail = event.detail || {};
      if (!detail.video || !detail.asset || !detail.video.closest(".toolbox-media-preview")) return;
      state.media = { path: String(detail.asset.path || ""), name: String(detail.asset.name || "截取帧.png"), mime: "image/png", kind: "image", preview_url: String(detail.asset.preview_url || "") };
      renderMedia();
      toast("已将当前帧作为工具箱输入");
    });
    if ($("toolboxRuntimeStatus")) $("toolboxRuntimeStatus").textContent = "自动配置";
    if ($("toolboxTaskStatus")) $("toolboxTaskStatus").textContent = "将按输入内容准备本地任务";
    var pendingToolboxReplay = window.__rhPendingToolboxReplay;
    if (!pendingToolboxReplay) {
      restoreToolboxDraft().catch(function () { state.restoringDraft = false; });
    }
    state.ttsVoicesPromise = loadTtsVoices();
    window.RHToolbox = { replayTask: replayTask };
    window.addEventListener("rh:toolbox-replay", function (event) {
      replayTask(event && event.detail || {}).catch(function (error) { toast("恢复工具箱任务失败：" + error.message, true); });
    });
    if (pendingToolboxReplay) {
      var pending = pendingToolboxReplay;
      delete window.__rhPendingToolboxReplay;
      replayTask(pending).catch(function (error) { toast("恢复工具箱任务失败：" + error.message, true); });
    }
    loadRecentTasks();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
