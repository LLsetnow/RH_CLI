(function () {
  "use strict";

  var state = { outputs: [], summary: {}, type: "all", rating: 0, search: "", sort: "newest", telegramConfigured: false };
  var outputImport = { item: null, path: "" };
  var draftStorageKey = "rh-workflow-desk-draft-v1";
  var pendingPromptGroupStorageKey = "rh-workflow-desk-pending-prompt-group-v1";
  var ratingBusy = {};
  var telegramUploadBusy = {};

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
      if (state.type !== "all" && item.display_type !== state.type) return false;
      if (state.rating === "unrated" && normalizedRating(item.rating) !== 0) return false;
      if (typeof state.rating === "number" && state.rating && normalizedRating(item.rating) !== state.rating) return false;
      if (!query) return true;
      return String(item.name || "").toLowerCase().indexOf(query) !== -1 || String(item.task_name || "").toLowerCase().indexOf(query) !== -1;
    });
    result.sort(function (left, right) {
      if (state.sort === "name") return String(left.name || "").localeCompare(String(right.name || ""), "zh-CN");
      var leftTime = Number(left.modified_at || left.task_completed_at || left.task_created_at || 0);
      var rightTime = Number(right.modified_at || right.task_completed_at || right.task_created_at || 0);
      return state.sort === "oldest" ? leftTime - rightTime : rightTime - leftTime;
    });
    return result;
  }
  function renderSummary() {
    var summary = state.summary || {};
    var ratingCounts = summary.rating_counts || {};
    var oneStarCount = Number(ratingCounts["1"] || 0);
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
    var deleteOneStarButton = $("deleteOneStarOutputs");
    if (deleteOneStarButton) {
      deleteOneStarButton.disabled = !oneStarCount;
      $("oneStarOutputCount").textContent = String(oneStarCount);
      deleteOneStarButton.title = oneStarCount ? "删除全部 " + oneStarCount + " 个一星成片" : "没有一星成片可删除";
    }
    updateFilterSlider();
  }
  function rebuildSummary() {
    var summary = { total: state.outputs.length, tasks: 0, image: 0, video: 0, audio: 0, other: 0, text: 0, rating_counts: { unrated: 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 } };
    var taskIds = {};
    state.outputs.forEach(function (item) {
      var type = String(item.display_type || "other");
      if (summary[type] == null) type = "other";
      summary[type] += 1;
      var rating = Number(item.rating || 0);
      if (rating >= 1 && rating <= 5) summary.rating_counts[String(rating)] += 1;
      else summary.rating_counts.unrated += 1;
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
  function previewMediaMarkup(item) {
    if (item.kind === "text") return '<div class="output-preview-text"><pre>' + esc(item.text || "") + "</pre></div>";
    var url = outputUrl(item);
    if (item.display_type === "image") return '<img src="' + url + '" alt="' + esc(item.name) + '" />';
    if (item.display_type === "video") return window.RHMotion.videoPlayerMarkup(url, true);
    if (item.display_type === "audio") return '<audio src="' + url + '" controls autoplay preload="metadata"></audio>';
    return '<div class="output-preview-other"><a class="output-link" href="' + url + '" target="_blank" rel="noreferrer">打开或下载文件</a></div>';
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
  function ratingStarsMarkup(item) {
    var rating = normalizedRating(item && item.rating);
    var stars = "";
    for (var index = 1; index <= 5; index += 1) {
      stars += '<button class="rating-star' + (index <= rating ? ' is-filled' : '') + '" type="button" data-rate-output="' + esc(item && item.id) + '" data-rating="' + index + '" aria-label="评分 ' + index + ' 星">' + (index <= rating ? '★' : '☆') + '</button>';
    }
    return '<div class="artifact-rating" aria-label="' + esc(rating ? "评分 " + rating + " 星" : "未评分") + '"><span class="rating-stars rating-stars-' + rating + '">' + stars + '</span><span class="rating-value">' + (rating ? rating + " / 5" : "未评分") + '</span></div>';
  }
  function refreshRatedArtifact(item) {
    var card = null;
    document.querySelectorAll(".artifact-card").forEach(function (candidate) {
      if (String(candidate.dataset.artifactId) === String(item && item.id)) card = candidate;
    });
    if (!card) return;
    var stillVisible = filteredOutputs().some(function (output) { return String(output.id) === String(item.id); });
    if (!stillVisible) {
      card.remove();
      if (!filteredOutputs().length) {
        var message = state.outputs.length ? "没有符合筛选条件的产物" : "还没有可浏览的产物";
        var hint = state.outputs.length ? "换一个类型或关键词试试。" : "完成任务并保存产物后，它们会自动出现在这里。";
        $("outputGrid").innerHTML = '<div class="outputs-empty"><strong>' + message + '</strong><span>' + hint + "</span></div>";
      }
      return;
    }
    var ratingNode = card.querySelector(".artifact-rating");
    if (ratingNode) ratingNode.outerHTML = ratingStarsMarkup(item);
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
      showToast(item.rating ? "已评分 " + item.rating + " 星" : "已清除评分");
    }).catch(function (error) {
      showToast("保存评分失败：" + error.message, true);
    }).finally(function () {
      delete ratingBusy[key];
    });
  }
  function taskWorkflowLabel(item) {
    var taskName = String(item && item.task_name || "未命名任务").trim() || "未命名任务";
    var taskId = String(item && item.task_id || "").trim();
    if (!taskId || (item && item.workflow_available === false)) return esc(taskName);
    return '<button class="artifact-workflow-link" type="button" data-load-task-workflow="' + esc(taskId) + '" title="加载此次工作流草稿" aria-label="加载工作流 ' + esc(taskName) + '">' + esc(taskName) + '</button>';
  }
  function telegramUploadKey(taskId, outputIndex) {
    return String(taskId || "") + ":" + String(outputIndex);
  }
  function updateTelegramUploadButtons(key, busy) {
    document.querySelectorAll("[data-upload-key]").forEach(function (button) {
      if (button.dataset.uploadKey !== key) return;
      button.disabled = busy;
      button.textContent = busy ? "上传中" : "上传";
      if (busy) button.setAttribute("aria-busy", "true");
      else button.removeAttribute("aria-busy");
    });
  }
  function telegramUploadButtonMarkup(item) {
    var taskId = String(item && item.task_id || "");
    var outputIndex = Number(item && item.output_index);
    var key = telegramUploadKey(taskId, outputIndex);
    var busy = Boolean(telegramUploadBusy[key]);
    return '<button class="artifact-upload-task" type="button" data-upload-task="' + esc(taskId) + '" data-upload-output-index="' + esc(item.output_index) + '" data-upload-key="' + esc(key) + '"' + (busy ? ' disabled aria-busy="true"' : '') + '>' + (busy ? "上传中" : "上传") + '</button>';
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
      showToast("已加载工作流「" + draft.workflow.name + "」，正在打开任务提交页");
      window.location.href = "/";
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
  function taskFileTargets(draft) {
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
        current: String(current == null ? "" : current),
        bypassed: Boolean(bypassed[nodeId])
      };
    }).filter(function (item) { return item.inputId && item.nodeId && item.field; });
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
    var targets = taskFileTargets(draft);
    if (!targets.length) {
      list.innerHTML = '<div class="output-import-empty"><strong>没有找到文件输入节点</strong><span>请确认当前工作流包含 LoadImage、LoadVideo、LoadAudio 或其他文件输入节点。</span></div>';
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
    if (!item || item.kind !== "file") return showToast("只有本地文件产物可以导入任务节点", true);
    var original = button ? button.textContent : "导入任务";
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
  function confirmOutputImport() {
    var selected = $("outputImportTargets").querySelector('input[name="output-import-target"]:checked');
    if (!outputImport.path || !selected) return showToast("请先选择一个文件输入节点", true);
    var draft = readTaskDraft();
    var target = taskFileTargets(draft).find(function (item) { return item.inputId === selected.value && !item.bypassed; });
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
      closeOutputImport();
      showToast("已将「" + itemName + "」导入「" + target.title + "」");
    } catch (error) {
      showToast("保存导入结果失败：" + error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "导入任务";
    }
  }
  function render() {
    renderSummary();
    var items = filteredOutputs();
    if (!items.length) {
      $("outputGrid").innerHTML = '<div class="outputs-empty"><strong>' + (state.outputs.length ? "没有符合筛选条件的产物" : "还没有可浏览的产物") + '</strong><span>' + (state.outputs.length ? "换一个类型或关键词试试。" : "完成任务并保存产物后，它们会自动出现在这里。") + "</span></div>";
      return;
    }
    $("outputGrid").innerHTML = items.map(function (item, index) {
      var cost = costLabel(item);
      var size = item.kind === "file" ? formatSize(item.size) : "文本";
      var taskLabel = "删除任务「" + String(item.task_name || item.task_id || "当前任务") + "」";
      var canCompare = item.display_type === "image" || item.display_type === "video";
      return '<article class="artifact-card' + (canCompare ? ' is-compare-draggable' : '') + '" data-task-id="' + esc(item.task_id) + '" data-artifact-id="' + esc(item.id) + '" data-compare-draggable="' + (canCompare ? 'true' : 'false') + '" draggable="' + (canCompare ? 'true' : 'false') + '" tabindex="0" role="button" aria-roledescription="' + (canCompare ? '可拖拽到内容对比' : '产物卡片') + '" aria-label="放大查看 ' + esc(item.name) + '" style="animation-delay:' + Math.min(index * 35, 350) + 'ms">' +
        '<div class="artifact-card-head"><span class="artifact-type ' + esc(item.display_type) + '">' + typeLabel(item.display_type) + '</span><span class="artifact-size">' + size + '</span></div>' +
        mediaMarkup(item) +
        '<div class="artifact-body"><div class="artifact-name-row"><div class="artifact-name" title="' + esc(item.name) + '">' + esc(item.name) + '</div>' + ratingStarsMarkup(item) + '</div><div class="artifact-task" title="点击工作流名称加载到任务提交页">任务 · ' + taskWorkflowLabel(item) + '</div><div class="artifact-foot"><span>' + formatTime(item.modified_at || item.task_completed_at || item.task_created_at) + '</span><span class="artifact-foot-actions">' + (cost ? '<span class="artifact-cost">' + esc(cost) + '</span>' : '') + '<span class="artifact-task-actions">' + (state.telegramConfigured && item.kind === "file" ? telegramUploadButtonMarkup(item) : '') + (item.kind === "file" ? '<button class="artifact-import-task" type="button" data-import-output="' + esc(item.id) + '">导入任务</button>' : '') + '<button class="artifact-delete-task" type="button" data-delete-task="' + esc(item.task_id) + '" aria-label="' + esc(taskLabel) + '" title="' + esc(taskLabel) + '">删除任务</button></span></span></div></div>' +
        '</article>';
    }).join("");
    window.RHMotion.bindVideoLoopControls($("outputGrid"));
  }
  function loadOutputs(showMessage) {
    $("refreshOutputs").disabled = true;
    Promise.all([request("/api/outputs"), request("/api/state")]).then(function (results) {
      var data = results[0];
      var settings = results[1] && results[1].settings && results[1].settings.telegram;
      state.outputs = Array.isArray(data.outputs) ? data.outputs : [];
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
    var ratingCounts = (state.summary && state.summary.rating_counts) || {};
    var count = Number(ratingCounts["1"] || 0);
    if (!count) return;
    if (!window.confirm("将删除全部 " + count + " 个一星成片，仅删除成片文件和产物记录，不删除任务记录。此操作不可恢复。")) return;
    var button = $("deleteOneStarOutputs");
    button.disabled = true;
    button.classList.add("is-busy");
    var originalLabel = button.innerHTML;
    button.textContent = "删除中…";
    request("/api/outputs/rating/1", { method: "DELETE" }).then(function (data) {
      var deleted = Number(data.deleted || 0);
      state.outputs = state.outputs.filter(function (item) { return normalizedRating(item.rating) !== 1; });
      rebuildSummary();
      render();
      showToast("已删除 " + deleted + " 个一星成片");
    }).catch(function (error) {
      showToast("删除一星成片失败：" + error.message, true);
    }).finally(function () {
      button.innerHTML = originalLabel;
      button.classList.remove("is-busy");
      renderSummary();
    });
  }
  function bindEvents() {
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
    $("refreshOutputs").addEventListener("click", function () { loadOutputs(true); });
    $("deleteOneStarOutputs").addEventListener("click", deleteOneStarOutputs);
    $("outputGrid").addEventListener("dragstart", function (event) {
      var card = event.target.closest('.artifact-card[data-compare-draggable="true"]');
      if (!card || !event.dataTransfer) return;
      var item = state.outputs.find(function (output) { return String(output.id) === String(card.dataset.artifactId); });
      if (!item) return;
      var payload = JSON.stringify({
        id: String(item.id),
        name: String(item.name || ""),
        display_type: String(item.display_type || ""),
        mime: String(item.mime || ""),
        url: outputUrl(item),
        task_name: String(item.task_name || ""),
        source: "output"
      });
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("application/x-rh-compare-asset", payload);
      event.dataTransfer.setData("text/plain", payload);
      card.classList.add("is-dragging");
    });
    $("outputGrid").addEventListener("dragend", function (event) {
      var card = event.target.closest(".artifact-card");
      if (card) card.classList.remove("is-dragging");
    });
    $("outputGrid").addEventListener("click", function (event) {
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
      var uploadButton = event.target.closest("[data-upload-task]");
      if (uploadButton) {
        var uploadTaskId = String(uploadButton.dataset.uploadTask || "").trim();
        var outputIndex = Number(uploadButton.dataset.uploadOutputIndex);
        var uploadKey = telegramUploadKey(uploadTaskId, outputIndex);
        if (!uploadTaskId || !Number.isInteger(outputIndex) || outputIndex < 0 || uploadButton.disabled || telegramUploadBusy[uploadKey]) return;
        telegramUploadBusy[uploadKey] = true;
        updateTelegramUploadButtons(uploadKey, true);
        request("/api/tasks/" + encodeURIComponent(uploadTaskId) + "/telegram", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ output_index: outputIndex })
        }).then(function (data) {
          showToast(data.message || "已加入 Telegram 上传队列");
        }).catch(function (error) {
          showToast("上传到 Telegram 失败：" + error.message, true);
        }).finally(function () {
          delete telegramUploadBusy[uploadKey];
          updateTelegramUploadButtons(uploadKey, false);
        });
        return;
      }
      var importButton = event.target.closest("[data-import-output]");
      if (importButton) {
        var importItem = state.outputs.find(function (output) { return String(output.id) === String(importButton.dataset.importOutput); });
        openOutputImport(importItem, importButton);
        return;
      }
      var button = event.target.closest("[data-delete-task]");
      if (button) {
        if (button.disabled) return;
        var taskId = String(button.dataset.deleteTask || "").trim();
        if (!taskId || !window.confirm("删除这条本地任务记录及其 output 文件夹中的全部产物吗？此操作不可恢复。")) return;
        button.disabled = true;
        request("/api/tasks/" + encodeURIComponent(taskId), { method: "DELETE" }).then(function () {
          state.outputs = state.outputs.filter(function (item) { return String(item.task_id || "") !== taskId; });
          rebuildSummary();
          document.querySelectorAll('.artifact-card[data-task-id="' + CSS.escape(taskId) + '"]').forEach(function (card) { card.remove(); });
          renderSummary();
          if (!filteredOutputs().length) render();
          showToast("任务记录已删除");
        }).catch(function (error) {
          button.disabled = false;
          showToast(error.message, true);
        });
        return;
      }
      if (event.target.closest("button, a, audio, video, input, select, textarea")) return;
      var card = event.target.closest(".artifact-card");
      if (!card) return;
      var item = state.outputs.find(function (output) { return String(output.id) === String(card.dataset.artifactId); });
      openOutputPreview(item);
    });
    $("outputGrid").addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      var card = event.target.closest(".artifact-card");
      if (!card || event.target.closest("button, a, audio, video, input, select, textarea")) return;
      event.preventDefault();
      var item = state.outputs.find(function (output) { return String(output.id) === String(card.dataset.artifactId); });
      openOutputPreview(item);
    });
    $("outputSearch").addEventListener("input", function () { state.search = this.value; render(); });
    $("outputSort").addEventListener("change", function () { state.sort = this.value; render(); });
    $("outputFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-output-type]");
      if (!button) return;
      state.type = button.dataset.outputType;
      render();
    });
    $("outputRatingFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-output-rating]");
      if (!button) return;
      state.rating = button.dataset.outputRating === "unrated" ? "unrated" : normalizedRating(button.dataset.outputRating);
      render();
    });
    $("closeOutputPreview").addEventListener("click", closeOutputPreview);
    $("outputPreviewModal").addEventListener("click", function (event) { if (event.target === $("outputPreviewModal")) closeOutputPreview(); });
    $("closeOutputImport").addEventListener("click", closeOutputImport);
    $("cancelOutputImport").addEventListener("click", closeOutputImport);
    $("confirmOutputImport").addEventListener("click", confirmOutputImport);
    $("outputImportTargets").addEventListener("change", function (event) {
      if (event.target.name === "output-import-target") $("confirmOutputImport").disabled = false;
    });
    $("outputImportModal").addEventListener("click", function (event) { if (event.target === $("outputImportModal")) closeOutputImport(); });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeOutputPreview();
        closeOutputImport();
      }
      if (!/^[0-5]$/.test(event.key) || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (event.target.closest && event.target.closest("input, select, textarea, video, audio")) return;
      if (document.querySelector(".modal-backdrop.is-open:not([hidden])")) return;
      var card = document.querySelector(".artifact-card:hover");
      if (!card) return;
      var item = state.outputs.find(function (output) { return String(output.id) === String(card.dataset.artifactId); });
      if (!item) return;
      event.preventDefault();
      setOutputRating(item, event.key);
    });
    window.addEventListener("resize", updateFilterSlider);
  }
  bindEvents();
  loadOutputs(false);
}());
