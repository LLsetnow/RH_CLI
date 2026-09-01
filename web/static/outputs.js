(function () {
  "use strict";

  var state = { outputs: [], summary: {}, type: "all", search: "", sort: "newest" };
  var toastTimer = 0;

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }
  function request(path) {
    return fetch(path, { headers: { "Accept": "application/json" } }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.message || "读取产物失败");
        return data;
      });
    });
  }
  function showToast(message, isError) {
    var toast = $("outputToast");
    toast.textContent = message;
    toast.className = "toast show" + (isError ? " error" : "");
    window.clearTimeout(toastTimer);
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
  function outputUrl(item) {
    return "/api/tasks/" + encodeURIComponent(item.task_id) + "/output/" + encodeURIComponent(item.file_index);
  }
  function filteredOutputs() {
    var query = state.search.trim().toLowerCase();
    var result = state.outputs.filter(function (item) {
      if (state.type !== "all" && item.display_type !== state.type) return false;
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
  }
  function mediaMarkup(item) {
    if (item.kind === "text") {
      return '<div class="artifact-media artifact-media-text"><pre>' + esc(item.text || "") + "</pre></div>";
    }
    var url = outputUrl(item);
    if (item.display_type === "image") {
      return '<div class="artifact-media"><a class="artifact-media-link" href="' + url + '" target="_blank" rel="noreferrer"><img src="' + url + '" alt="' + esc(item.name) + '" loading="lazy" /></a></div>';
    }
    if (item.display_type === "video") {
      return '<div class="artifact-media"><video src="' + url + '" controls preload="metadata"></video></div>';
    }
    if (item.display_type === "audio") {
      return '<div class="artifact-media"><audio src="' + url + '" controls preload="metadata"></audio></div>';
    }
    return '<div class="artifact-media artifact-media-other"><a class="output-link" href="' + url + '" target="_blank" rel="noreferrer">打开或下载文件</a></div>';
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
      return '<article class="artifact-card" style="animation-delay:' + Math.min(index * 35, 350) + 'ms">' +
        '<div class="artifact-card-head"><span class="artifact-type ' + esc(item.display_type) + '">' + typeLabel(item.display_type) + '</span><span class="artifact-size">' + size + '</span></div>' +
        mediaMarkup(item) +
        '<div class="artifact-body"><div class="artifact-name" title="' + esc(item.name) + '">' + esc(item.name) + '</div><div class="artifact-task" title="' + esc(item.task_name) + '">任务 · ' + esc(item.task_name) + '</div><div class="artifact-foot"><span>' + formatTime(item.modified_at || item.task_completed_at || item.task_created_at) + '</span><span class="artifact-cost">' + esc(cost) + '</span></div></div>' +
        '</article>';
    }).join("");
  }
  function loadOutputs(showMessage) {
    $("refreshOutputs").disabled = true;
    request("/api/outputs").then(function (data) {
      state.outputs = Array.isArray(data.outputs) ? data.outputs : [];
      state.summary = data.summary || {};
      render();
      if (showMessage) showToast("产物列表已刷新");
    }).catch(function (error) {
      $("outputGrid").innerHTML = '<div class="outputs-empty"><strong>读取产物失败</strong><span>' + esc(error.message) + "</span></div>";
      showToast(error.message, true);
    }).finally(function () { $("refreshOutputs").disabled = false; });
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
    $("outputSearch").addEventListener("input", function () { state.search = this.value; render(); });
    $("outputSort").addEventListener("change", function () { state.sort = this.value; render(); });
    $("outputFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-output-type]");
      if (!button) return;
      state.type = button.dataset.outputType;
      render();
    });
  }
  bindEvents();
  loadOutputs(false);
}());
