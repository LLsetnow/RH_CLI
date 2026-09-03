(function () {
  "use strict";

  var state = { days: 7, accountId: "", data: null, loading: false };
  var toastTimer = 0;
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

  function formatTimestamp(value) {
    var timestamp = numeric(value);
    if (!timestamp) return "时间未记录";
    var date = new Date(timestamp);
    if (isNaN(date.getTime())) return "时间未记录";
    return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0") + " " + String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
  }

  function request(url) {
    return fetch(url, { cache: "no-store" }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.message || "请求失败");
        return data;
      });
    });
  }

  function showToast(message, isError) {
    var toast = $("dashboardToast");
    toast.textContent = message;
    toast.classList.toggle("error", Boolean(isError));
    toast.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { toast.classList.remove("show"); }, 2600);
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
    var labels = { 1: "今天", 7: "近 7 天", 30: "近 30 天" };
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
    $("dashboardCoinsMeta").textContent = accountLabel + " · 所选时间范围 · RH 币";
    $("dashboardSubmissions").textContent = String(summary.submissions || 0);
    $("dashboardSubmissionsMeta").textContent = accountLabel + " · 包含成功、失败和取消的提交";
    $("dashboardProcessing").textContent = formatDuration(summary.processing_seconds);
    $("dashboardProcessingMeta").textContent = accountLabel + " · 有记录的任务处理时长总和";
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

  function heatCellMarkup(value, max, label) {
    var ratio = max > 0 ? Math.min(1, numeric(value) / max) : 0;
    var heat = (0.1 + ratio * 0.82).toFixed(2);
    return '<span class="dashboard-heatmap-cell" style="--dashboard-heat:' + heat + '" title="' + esc(label) + '"></span>';
  }

  function dateKey(date) {
    return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0");
  }

  function renderAnnualHeatmap(data) {
    var heatmap = data.heatmap || {};
    var daily = Array.isArray(heatmap.daily) ? heatmap.daily : [];
    var chart = $("dashboardDailyChart");
    if (!daily.length) {
      chart.innerHTML = '<div class="dashboard-recent-empty"><strong>还没有可统计的数据</strong><span>提交工作流后，这里会按天保留消耗和处理记录。</span></div>';
      return;
    }
    var maxCoins = Math.max.apply(null, daily.map(function (item) { return numeric(item.coins); }).concat([0]));
    var maxRuns = Math.max.apply(null, daily.map(function (item) { return numeric(item.submissions); }).concat([0]));
    var maxTime = Math.max.apply(null, daily.map(function (item) { return numeric(item.processing_seconds); }).concat([0]));
    function activityScore(item) {
      var coinRatio = maxCoins > 0 ? numeric(item.coins) / maxCoins : 0;
      var runRatio = maxRuns > 0 ? numeric(item.submissions) / maxRuns : 0;
      var timeRatio = maxTime > 0 ? numeric(item.processing_seconds) / maxTime : 0;
      return Math.min(1, (coinRatio + runRatio + timeRatio) / 3);
    }
    var firstDate = new Date(numeric(heatmap.start));
    var lastDate = new Date(Math.max(numeric(heatmap.start), numeric(heatmap.end) - 86400000));
    if (isNaN(firstDate.getTime()) || isNaN(lastDate.getTime())) return;
    var calendarStart = new Date(firstDate);
    calendarStart.setDate(calendarStart.getDate() - calendarStart.getDay());
    var calendarEnd = new Date(lastDate);
    calendarEnd.setDate(calendarEnd.getDate() + (6 - calendarEnd.getDay()));
    var weekCount = Math.floor((calendarEnd - calendarStart) / 86400000 / 7) + 1;
    var compactLayout = window.matchMedia && window.matchMedia("(max-width: 650px)").matches;
    var rowHeadWidth = compactLayout ? 76 : 98;
    var rowGap = compactLayout ? 9 : 14;
    var calendarChrome = 33;
    var maxVisibleWeeks = Math.max(1, Math.floor((chart.clientWidth - rowHeadWidth - rowGap - calendarChrome + 4) / 14));
    var visibleWeekCount = Math.min(weekCount, maxVisibleWeeks);
    var visibleCalendarStart = new Date(calendarEnd);
    visibleCalendarStart.setDate(visibleCalendarStart.getDate() - (visibleWeekCount * 7 - 1));
    var gridWidth = Math.max(10, visibleWeekCount * 14 - 4);
    var gridStyle = "--dashboard-weeks:" + visibleWeekCount + ";--dashboard-grid-width:" + gridWidth + "px";
    var dailyByDate = {};
    daily.forEach(function (item) { dailyByDate[item.date] = item; });
    var monthLabels = [];
    for (var monthOffset = 0; monthOffset < visibleWeekCount * 7; monthOffset += 1) {
      var monthDate = new Date(visibleCalendarStart);
      monthDate.setDate(visibleCalendarStart.getDate() + monthOffset);
      if (monthOffset !== 0 && monthDate.getDate() !== 1) continue;
      var monthWeekIndex = Math.floor(monthOffset / 7);
      monthLabels.push('<span style="grid-column:' + (monthWeekIndex + 1) + '">' + esc(monthDate.toLocaleDateString("zh-CN", { month: "short" })) + '</span>');
    }
    var monthMarkup = '<div class="dashboard-heatmap-months" style="' + gridStyle + '">' + monthLabels.join("") + '</div>';
    var weekdayMarkup = '<div class="dashboard-heatmap-weekdays"><span>日</span><span></span><span>二</span><span></span><span>四</span><span></span><span>六</span></div>';
    var cells = [];
    for (var week = 0; week < visibleWeekCount; week += 1) {
      for (var weekday = 0; weekday < 7; weekday += 1) {
        var cellDate = new Date(visibleCalendarStart);
        cellDate.setDate(visibleCalendarStart.getDate() + week * 7 + weekday);
        var item = dailyByDate[dateKey(cellDate)];
        if (!item) {
          cells.push('<span class="dashboard-heatmap-cell is-empty" aria-hidden="true"></span>');
          continue;
        }
        var score = activityScore(item);
        var label = item.label + " · 综合活跃度 " + formatNumber(score * 100, 0) + "% · RH 币 " + formatNumber(item.coins, 4) + " · 提交 " + formatNumber(item.submissions, 0) + " 次 · 处理 " + formatDuration(item.processing_seconds);
        cells.push(heatCellMarkup(score, 1, label));
      }
    }
    var activeDays = daily.filter(function (item) { return numeric(item.coins) || numeric(item.submissions) || numeric(item.processing_seconds); }).length;
    var rowMarkup = '<div class="dashboard-heatmap-row combined"><div class="dashboard-heatmap-row-head"><span class="dashboard-heatmap-row-label">综合活动</span><strong class="dashboard-heatmap-row-total">' + esc(activeDays + " 天") + '</strong></div><div class="dashboard-heatmap-calendar" style="' + gridStyle + '">' + weekdayMarkup + '<div class="dashboard-heatmap-grid" style="' + gridStyle + '">' + cells.join("") + '</div></div></div>';
    chart.innerHTML = '<div class="dashboard-annual-heatmap">' + monthMarkup + rowMarkup + '</div>';
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
    renderBalances(data);
    renderAnnualHeatmap(data);
    renderRecent(data);
    $("dashboardSourceLabel").textContent = data.source && data.source.label ? data.source.label : "独立用量记录";
    $("dashboardUpdated").textContent = "更新于 " + formatTimestamp(Date.now());
    if (animate) animateDashboardUpdate();
  }

  function loadDashboard(showMessage, animate) {
    if (state.loading) return;
    state.loading = true;
    $("refreshDashboard").disabled = true;
    request("/api/dashboard?days=" + encodeURIComponent(state.days) + "&account_id=" + encodeURIComponent(state.accountId)).then(function (data) {
      state.data = data;
      render(data, animate);
      if (showMessage) showToast("仪表盘已刷新");
    }).catch(function (error) {
      showToast("读取仪表盘失败：" + error.message, true);
    }).finally(function () {
      state.loading = false;
      $("refreshDashboard").disabled = false;
    });
  }

  function bindEvents() {
    $("themeToggle").addEventListener("click", function () {
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
      loadDashboard(false, true);
    });
    $("dashboardAccountFilter").addEventListener("change", function () {
      state.accountId = this.value;
      loadDashboard(false, true);
    });
    $("refreshDashboard").addEventListener("click", function () { loadDashboard(true); });
    window.addEventListener("resize", function () {
      if (state.data) renderAnnualHeatmap(state.data);
      updateRangeIndicator();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindEvents();
    renderRangeState();
    loadDashboard(false, false);
  });
}());
