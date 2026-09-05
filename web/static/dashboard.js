(function () {
  "use strict";

  var state = { days: 7, accountId: "", data: null, loading: false };
  var switchTimer = 0;

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }

  function numeric(value) {
    var parsed = Number(String(value == null ? "" : value).replace(/,/g, "").trim());
    return isFinite(parsed) ? parsed : 0;
  }

  function formatNumber(value, decimals) {
    var number = numeric(value);
    var fixed = number.toFixed(decimals == null ? 2 : decimals).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
    return fixed === "-0" ? "0" : fixed;
  }

  function formatDuration(seconds) {
    var total = Math.max(0, Math.round(numeric(seconds)));
    if (!total) return "0 秒";
    if (total < 60) return total + " 秒";
    var minutes = Math.floor(total / 60);
    var remainSeconds = total % 60;
    if (minutes < 60) return minutes + " 分" + (remainSeconds ? " " + remainSeconds + " 秒" : "");
    var hours = Math.floor(minutes / 60);
    minutes %= 60;
    return hours + " 小时" + (minutes ? " " + minutes + " 分" : "");
  }

  function formatMoneySpent(items) {
    if (!Array.isArray(items) || !items.length) return "0";
    return items.map(function (item) {
      var symbol = String(item && item.symbol || "");
      return symbol + formatNumber(item && item.value, 4);
    }).join(" · ");
  }

  function formatTimestamp(value) {
    var timestamp = numeric(value);
    if (!timestamp) return "时间未记录";
    var date = new Date(timestamp);
    if (isNaN(date.getTime())) return "时间未记录";
    return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0") + " " + String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
  }

  function request(url, options) {
    var fetchOptions = Object.assign({ cache: "no-store" }, options || {});
    return fetch(url, fetchOptions).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.message || "请求失败");
        return data;
      });
    });
  }

  function showToast(message, isError) {
    var toast = $("dashboardToast");
    if (window.RHMotion && window.RHMotion.showToast) window.RHMotion.showToast(toast, message, isError);
  }

  function statusLabel(status) {
    return { completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断", queued: "排队中", submitting: "提交中", running: "执行中", recovering: "恢复中" }[status] || "已记录";
  }

  function siteLabel(site) {
    return site === "cn" ? "runninghub.cn" : "runninghub.ai";
  }

  function renderAccountOptions(data) {
    var select = $("dashboardAccountFilter");
    var accounts = Array.isArray(data.accounts) ? data.accounts : [];
    var options = ['<option value="">全部账号</option>'];
    accounts.forEach(function (account) {
      var label = String(account.name || "未命名账号");
      if (account.site) label += " · " + siteLabel(account.site);
      options.push('<option value="' + esc(account.id || "") + '">' + esc(label) + '</option>');
    });
    select.innerHTML = options.join("");
    select.value = state.accountId;
    if (select.value !== state.accountId) {
      state.accountId = "";
      select.value = "";
    }
  }

  function renderRangeState() {
    var labels = { 1: "近 24 小时", 7: "近 7 天", 30: "近 30 天" };
    $("dashboardRangeLabel").textContent = labels[state.days] || "近 7 天";
    document.querySelectorAll("[data-dashboard-days]").forEach(function (button) {
      var active = Number(button.dataset.dashboardDays) === state.days;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    updateRangeIndicator();
  }

  function updateRangeIndicator() {
    var tabs = $("dashboardRangeTabs");
    var active = tabs && tabs.querySelector(".dashboard-range-tab.active");
    if (!tabs || !active) return;
    tabs.style.setProperty("--dashboard-range-left", active.offsetLeft + "px");
    tabs.style.setProperty("--dashboard-range-width", active.offsetWidth + "px");
  }

  function animateDashboardUpdate() {
    var main = document.querySelector(".dashboard-main");
    if (!main) return;
    main.classList.remove("dashboard-data-switching");
    void main.offsetWidth;
    main.classList.add("dashboard-data-switching");
    window.clearTimeout(switchTimer);
    switchTimer = window.setTimeout(function () {
      main.classList.remove("dashboard-data-switching");
    }, 420);
  }

  function renderSummary(data) {
    var summary = data.summary || {};
    var accountLabel = data.account_filter_name || "全部账号";
    $("dashboardCoinsSpent").textContent = formatNumber(summary.coins_spent, 4);
    $("dashboardMoneySpent").textContent = formatMoneySpent(summary.money_spent);
    $("dashboardCoinsMeta").textContent = accountLabel + " · 所选时间范围 · RH 币";
    $("dashboardSubmissions").textContent = String(summary.submissions || 0);
    $("dashboardSubmissionsMeta").textContent = accountLabel + " · 包含成功、失败和取消的提交";
    $("dashboardProcessing").textContent = formatDuration(summary.processing_seconds);
    $("dashboardProcessingMeta").textContent = accountLabel + " · 有记录的任务处理时长总和";
    var videoSeconds = numeric(summary.video_seconds);
    $("dashboardVideoDuration").textContent = videoSeconds > 0 ? formatDuration(videoSeconds) : "—";
    $("dashboardVideoDurationMeta").textContent = videoSeconds > 0 ? accountLabel + " · 可识别视频产物总时长" : "暂无可识别视频产物";
    $("dashboardSuccess").textContent = summary.submissions ? formatNumber(summary.success_rate, 1) + "%" : "—";
    $("dashboardSuccessMeta").textContent = "完成 " + String(summary.completed || 0) + " · 失败 " + String(summary.failed || 0);
    $("dashboardCompleted").textContent = String(summary.completed || 0);
    $("dashboardFailed").textContent = String(summary.failed || 0);
    $("dashboardOutputs").textContent = String(summary.outputs || 0);
  }

  function renderBalances(data) {
    var balances = data.balances || {};
    var keys = Array.isArray(balances.keys) ? balances.keys : [];
    $("dashboardCoinBalance").textContent = balances.coins == null || balances.coins === "" ? "—" : formatNumber(balances.coins, 4);
    var accountCount = Number(balances.account_count || keys.length);
    var keyCount = Number(balances.key_count || keys.length);
    $("dashboardBalanceKeyCount").textContent = accountCount + " 个账号 · " + keyCount + " Key 去重";
    $("dashboardAccountBalances").innerHTML = keys.length ? keys.map(function (item) {
      var coins = item.coins == null || item.coins === "" ? "—" : formatNumber(item.coins, 4);
      var balance = item.balance == null || item.balance === "" ? "—" : String(item.symbol || "") + formatNumber(item.balance, 4);
      return '<div class="dashboard-account-balance" title="余额取自 ' + esc(item.key_name || item.name || "该账号") + '"><div class="dashboard-account-balance-meta"><strong>' + esc(item.account_name || "未绑定账号") + '</strong><span>' + esc(siteLabel(item.site)) + '</span></div><div class="dashboard-account-balance-values"><span class="dashboard-account-balance-value"><strong>' + esc(coins) + '</strong><small>RH 币</small></span><span class="dashboard-account-balance-value"><strong>' + esc(balance) + '</strong><small>余额</small></span></div></div>';
    }).join("") : '<div class="dashboard-account-balance-empty">暂无可用账号余额</div>';
    var checkedAt = numeric(balances.latest_checked_at);
    $("dashboardBalanceNote").textContent = checkedAt ? "最近查询：" + formatTimestamp(checkedAt) : "余额尚未成功查询，请到设置中刷新 API Key。";
  }

  function renderResponse(data) {
    var summary = data.summary || {};
    var videoSeconds = numeric(summary.video_seconds);
    var wallClockSeconds = numeric(summary.wall_clock_seconds);
    var responseRate = numeric(summary.response_seconds_per_video_second);
    var hasRate = videoSeconds > 0 && wallClockSeconds > 0 && responseRate > 0;
    $("dashboardResponseRate").textContent = hasRate ? formatNumber(responseRate, 3) : "—";
    $("dashboardWallClock").textContent = wallClockSeconds > 0 ? formatDuration(wallClockSeconds) : "—";
    $("dashboardVideoSeconds").textContent = videoSeconds > 0 ? formatNumber(videoSeconds, 3) + " 秒" : "—";
    $("dashboardVideoTasks").textContent = String(summary.video_task_count || 0);
    $("dashboardResponseProcessing").textContent = formatDuration(summary.processing_seconds);
    if (hasRate) {
      $("dashboardResponseNote").textContent = "按 " + formatDuration(wallClockSeconds) + " 并发墙钟 ÷ " + formatNumber(videoSeconds, 3) + " 秒视频时长计算；重叠任务只计一次。";
    } else if (videoSeconds > 0) {
      $("dashboardResponseNote").textContent = "已识别视频时长，但当前时间范围没有可用的任务墙钟区间。";
    } else {
      $("dashboardResponseNote").textContent = "完成视频任务后，这里会按并发合并后的实际墙钟时间计算。";
    }
  }

  function workflowSearchName(value) {
    return String(value == null ? "" : value).trim().replace(/\.json$/i, "");
  }

  function workflowOutputsUrl(item, data) {
    var params = new URLSearchParams();
    if (data && data.range_start != null) params.set("range_start", String(data.range_start));
    if (data && data.range_end != null) params.set("range_end", String(data.range_end));
    if (data && data.range_days != null) params.set("range_days", String(data.range_days));
    if (data && data.account_filter) params.set("account_id", String(data.account_filter));
    var searchName = workflowSearchName(item && item.name);
    if (searchName) params.set("workflow_name", searchName);
    return "/outputs?" + params.toString();
  }

  function renderWorkflowScores(data) {
    var scores = data.workflow_scores || {};
    var items = Array.isArray(scores.items) ? scores.items : [];
    var registeredCount = Number(scores.registered_count || 0);
    var ratedCount = Number(scores.rated_count || 0);
    $("dashboardWorkflowRegistryCount").textContent = registeredCount + " 个已登记";
    $("dashboardWorkflowRatedCount").textContent = String(ratedCount);
    if (!items.length) {
      var emptyMessage = registeredCount
        ? "当前范围还没有已评分的注册工作流"
        : "还没有注册工作流";
      var emptyHint = registeredCount
        ? "去成片页为产物评分后，这里会按总得分显示前 5 个工作流。"
        : "先在工作流页面保存工作流，之后即可在成片评分后查看排行。";
      $("dashboardWorkflowScoreNote").textContent = emptyHint;
      $("dashboardTopWorkflows").innerHTML = '<div class="dashboard-workflow-score-empty"><strong>' + esc(emptyMessage) + '</strong><span>' + esc(emptyHint) + '</span></div>';
      return;
    }
    $("dashboardWorkflowScoreNote").textContent = "按当前时间范围和账号筛选；总得分 = 成片页可见产物的已评分星级之和。";
    $("dashboardTopWorkflows").innerHTML = items.map(function (item, index) {
      var ratedOutputs = Number(item.rated_output_count || 0);
      var runCount = Number(item.run_count || 0);
      var average = formatNumber(item.average_rating, 1);
      var detail = ratedOutputs + " 个已评分成片 · 平均 " + average + " 星";
      if (runCount > 0) detail += " · " + runCount + " 次运行";
      return '<a class="dashboard-workflow-score-item" href="' + esc(workflowOutputsUrl(item, data)) + '" title="打开成片页并筛选该注册工作流的产物"><span class="dashboard-workflow-score-rank" aria-label="第 ' + (index + 1) + ' 名">' + String(index + 1).padStart(2, "0") + '</span><div class="dashboard-workflow-score-identity"><strong title="' + esc(item.name) + '">' + esc(item.name) + '</strong><span>' + esc(detail) + '</span></div><div class="dashboard-workflow-score-value"><strong>' + esc(item.total_score) + '</strong><small>总得分</small></div></a>';
    }).join("");
  }

  function recentCost(record) {
    var cost = String(record.cost || "").trim();
    if (!cost) return "费用未返回";
    return record.cost_type === "coins" ? formatNumber(cost, 4) + " RH 币" : formatNumber(cost, 4);
  }

  function renderRecent(data) {
    var recent = Array.isArray(data.recent) ? data.recent : [];
    $("dashboardRecordCount").textContent = String((data.source && data.source.record_count) || 0);
    if (!recent.length) {
      $("dashboardRecent").innerHTML = '<div class="dashboard-recent-empty"><strong>这个时间范围还没有记录</strong><span>切换到 30D，或提交新的工作流后再来查看。</span></div>';
      return;
    }
    $("dashboardRecent").innerHTML = recent.map(function (record) {
      var availability = record.task_available ? "任务记录仍在库" : "原任务已删除，统计保留";
      var outputs = numeric(record.output_count);
      return '<article class="dashboard-recent-item"><div><div class="dashboard-recent-name" title="' + esc(record.workflow_name) + '">' + esc(record.workflow_name) + '</div><span class="dashboard-recent-meta">' + esc(formatTimestamp(record.created_at)) + " · " + esc(availability) + '</span></div><span class="dashboard-status ' + esc(record.status) + '">' + esc(statusLabel(record.status)) + '</span><span class="dashboard-recent-stat">' + esc(recentCost(record)) + '</span><span class="dashboard-recent-stat">' + esc(formatDuration(record.duration_seconds)) + (outputs ? " · " + outputs + " 个产物" : "") + '</span></article>';
    }).join("");
  }

  function render(data, animate) {
    renderAccountOptions(data);
    renderRangeState();
    renderSummary(data);
    renderResponse(data);
    renderBalances(data);
    renderWorkflowScores(data);
    renderRecent(data);
    $("dashboardSourceLabel").textContent = data.source && data.source.label ? data.source.label : "独立用量记录";
    $("dashboardUpdated").textContent = "更新于 " + formatTimestamp(Date.now());
    if (animate) animateDashboardUpdate();
  }

  function refreshBalanceSnapshots() {
    return request("/api/dashboard/refresh-balances", { method: "POST" });
  }

  function balanceRefreshMessage(result) {
    if (result && result.error) return "仪表盘已刷新，余额刷新失败：" + result.error.message;
    var refreshed = Number(result && result.refreshed || 0);
    var failed = Number(result && result.failed || 0);
    if (failed) return "仪表盘已刷新，余额更新 " + refreshed + " 个，" + failed + " 个失败";
    return "仪表盘已刷新，余额已更新";
  }

  function loadDashboard(showMessage, animate, shouldRefreshBalances) {
    if (state.loading) return;
    state.loading = true;
    $("refreshDashboard").disabled = true;
    var balanceRequest = shouldRefreshBalances
      ? refreshBalanceSnapshots().then(function (result) { return result; }).catch(function (error) { return { error: error }; })
      : Promise.resolve(null);
    balanceRequest.then(function (balanceResult) {
      return request("/api/dashboard?days=" + encodeURIComponent(state.days) + "&account_id=" + encodeURIComponent(state.accountId)).then(function (data) {
        state.data = data;
        render(data, animate);
        if (showMessage) showToast(balanceRefreshMessage(balanceResult), Boolean(balanceResult && (balanceResult.error || Number(balanceResult.failed || 0))));
      });
    }).catch(function (error) {
      showToast("读取仪表盘失败：" + error.message, true);
    }).finally(function () {
      state.loading = false;
      $("refreshDashboard").disabled = false;
    });
  }

  function bindEvents() {
    var dashboardThemeToggle = $("themeToggle");
    if (dashboardThemeToggle) dashboardThemeToggle.addEventListener("click", function () {
      var nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem("rh-workflow-theme", nextTheme); } catch (error) {}
      var light = nextTheme === "light";
      $("themeToggleIcon").textContent = light ? "☾" : "☀";
      $("themeToggleLabel").textContent = light ? "夜间" : "日间";
      $("themeToggle").setAttribute("aria-label", light ? "切换到夜间模式" : "切换到日间模式");
    });
    $("dashboardRangeTabs").addEventListener("click", function (event) {
      var button = event.target.closest("[data-dashboard-days]");
      if (!button) return;
      state.days = Number(button.dataset.dashboardDays) || 7;
      loadDashboard(false, true, false);
    });
    $("dashboardAccountFilter").addEventListener("change", function () {
      state.accountId = this.value;
      loadDashboard(false, true, false);
    });
    $("refreshDashboard").addEventListener("click", function () { loadDashboard(true, true, true); });
    window.addEventListener("resize", function () { updateRangeIndicator(); });
  }

  function initializeDashboard() {
    bindEvents();
    renderRangeState();
    loadDashboard(false, false, false);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initializeDashboard);
  else initializeDashboard();
}());
