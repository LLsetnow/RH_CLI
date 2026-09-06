(function () {
  "use strict";

  var state = { references: [], media: null, mode: "depth", polling: {}, pickingMedia: false };
  var imageExtensions = /\.(avif|bmp|gif|jpe?g|png|webp)$/i;
  var mediaExtensions = /\.(avif|bmp|gif|jpe?g|png|webp|avi|flv|m4v|mkv|mov|mp4|webm|wmv)$/i;

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
  }
  function previewUrl(asset) { return String(asset && asset.preview_url || ""); }
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
        zone.innerHTML = '<span class="toolbox-drop-mark" aria-hidden="true">↥</span><span><strong>拖入图片或视频</strong><small>图片处理一张；视频处理全部帧</small></span><span class="toolbox-drop-key">MEDIA</span>';
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
    else if (kind === "video") preview.innerHTML = '<video controls preload="metadata" playsinline src="' + esc(url) + '"></video>';
    else preview.innerHTML = '<img src="' + esc(url) + '" alt="' + esc(asset.name) + '" />';
    if (meta) {
      meta.hidden = false;
      meta.innerHTML = '<span title="' + esc(asset.path) + '">' + esc(asset.name) + '</span><span>' + (kind === "video" ? "VIDEO · 全部帧" : "IMAGE · 单张") + '</span>';
    }
  }
  function setMediaFile(file, event) {
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
  function updateTaskCard(kind, task) {
    var isImage = kind === "codex";
    var statusId = isImage ? "codexTaskStatus" : "mediaTaskStatus";
    var resultId = isImage ? "codexTaskResult" : "mediaTaskResult";
    var title = isImage ? "Codex 图像结果" : "媒体处理结果";
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
        setInlineStatus(kind === "codex" ? "codexTaskStatus" : "mediaTaskStatus", error.message, "error");
      });
    }
    poll();
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
    jsonRequest("/api/toolbox/media", "POST", { mode: state.mode, input: { path: state.media.path, name: state.media.name, mime: state.media.mime } }).then(function (data) {
      var task = data.task || data;
      $("mediaTaskId").textContent = task.id || "";
      pollTask("media", task.id);
      loadRecentTasks();
    }).catch(function (error) {
      setInlineStatus("mediaTaskStatus", error.message, "error");
      toast(error.message, true);
    }).finally(function () { button.disabled = false; });
  }
  function renderRecentTasks(tasks) {
    var container = $("toolboxRecentTasks");
    if (!container) return;
    var filtered = (tasks || []).filter(function (task) {
      var tool = task && task.custom_inputs && task.custom_inputs.tool;
      return ["codex", "media_processor"].indexOf(String(tool || "")) !== -1 || String(task.workflow_name || "").indexOf("工具箱") === 0;
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
      state.mode = button.dataset.mode;
      $("toolboxModeTabs").querySelectorAll("[data-mode]").forEach(function (item) { var active = item === button; item.classList.toggle("active", active); item.setAttribute("aria-selected", active ? "true" : "false"); });
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
    $("referenceGrid").addEventListener("click", function (event) { var button = event.target.closest("[data-remove-reference]"); if (!button) return; state.references.splice(Number(button.dataset.removeReference), 1); renderReferences(); });
    $("submitCodexImage").addEventListener("click", submitCodex);
    $("submitMediaProcess").addEventListener("click", submitMedia);
    $("mediaChooseButton").addEventListener("click", chooseMediaFile);
    $("mediaOpenFolderButton").addEventListener("click", openMediaFolder);
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
    loadRecentTasks();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
