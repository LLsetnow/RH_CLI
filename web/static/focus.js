(function () {
  "use strict";

  var stage = document.getElementById("focusStage");
  // Keep the keyboard order independent from DOM/query order.  The six pages
  // are one horizontal workspace and must always follow the normal navigation:
  // workflows -> prompt -> submit -> outputs -> dashboard -> settings.
  var focusPageOrder = ["workflows", "prompt", "submit", "outputs", "dashboard", "settings"];
  var panelByPage = {};
  Array.prototype.slice.call(document.querySelectorAll("[data-focus-panel][data-focus-page]")).forEach(function (panel) {
    panelByPage[panel.dataset.focusPage] = panel;
  });
  var panels = focusPageOrder.map(function (pageId) { return panelByPage[pageId]; });
  var linkByPage = {};
  Array.prototype.slice.call(document.querySelectorAll("[data-focus-index][data-focus-page]")).forEach(function (link) {
    linkByPage[link.dataset.focusPage] = link;
  });
  var links = focusPageOrder.map(function (pageId) { return linkByPage[pageId]; });
  var dividers = Array.prototype.slice.call(document.querySelectorAll("[data-focus-divider]"));
  dividers.sort(function (left, right) {
    return Number(left.dataset.focusDivider) - Number(right.dataset.focusDivider);
  });
  var focusedPanelIndex = 0;
  var centeredMode = false;
  var reducedMotion = Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var horizontalWheelFrame = 0;
  var horizontalWheelTarget = 0;
  var resizeState = null;
  var pageNavigationPaths = ["/workflows", "/prompt", "/", "/outputs", "/dashboard", "/settings", "/focus", "/outputs/compare"];

  if (!stage || panels.length !== focusPageOrder.length || panels.some(function (panel) { return !panel; }) || links.some(function (link) { return !link; })) return;

  function requestFrame(callback) {
    return window.requestAnimationFrame ? window.requestAnimationFrame(callback) : window.setTimeout(callback, 16);
  }

  function maxHorizontalScroll() {
    return Math.max(0, stage.scrollWidth - stage.clientWidth);
  }

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function cancelHorizontalWheel() {
    if (!horizontalWheelFrame) return;
    if (window.cancelAnimationFrame) window.cancelAnimationFrame(horizontalWheelFrame);
    else window.clearTimeout(horizontalWheelFrame);
    horizontalWheelFrame = 0;
  }

  function setActive(index) {
    links.forEach(function (link, linkIndex) {
      var visible = !panels[linkIndex].classList.contains("is-hidden");
      link.classList.toggle("active", visible);
      link.classList.toggle("is-hidden", !visible);
      link.classList.toggle("is-focus-target", linkIndex === index);
      link.setAttribute("aria-pressed", visible ? "true" : "false");
      var pageLabel = panels[linkIndex].getAttribute("aria-label") || focusPageOrder[linkIndex];
      link.setAttribute("title", (visible ? "隐藏" : "显示") + pageLabel);
      var icon = link.querySelector(".focus-nav-icon");
      if (icon) icon.textContent = visible ? "●" : "○";
    });
  }

  function panelIsVisible(index) {
    return Boolean(panels[index]) && !panels[index].classList.contains("is-hidden");
  }

  function visiblePanelIndices() {
    return panels.reduce(function (indices, panel, index) {
      if (panelIsVisible(index)) indices.push(index);
      return indices;
    }, []);
  }

  function visibleDividerPanels(index) {
    if (!panelIsVisible(index)) return null;
    for (var nextIndex = index + 1; nextIndex < panels.length; nextIndex += 1) {
      if (panelIsVisible(nextIndex)) {
        return { previousIndex: index, nextIndex: nextIndex };
      }
    }
    return null;
  }

  function syncVisibleDividers() {
    dividers.forEach(function (divider, index) {
      var pair = visibleDividerPanels(index);
      divider.classList.toggle("is-hidden", !pair);
      if (pair) updateDividerValue(divider, panels[pair.previousIndex]);
    });
  }

  function nearestVisiblePanel(index) {
    var visible = visiblePanelIndices();
    if (!visible.length) return -1;
    for (var distance = 1; distance < panels.length; distance += 1) {
      var next = (index + distance) % panels.length;
      if (panelIsVisible(next)) return next;
      var previous = (index - distance + panels.length) % panels.length;
      if (panelIsVisible(previous)) return previous;
    }
    return visible[0];
  }

  function setFocusedPanel(index) {
    if (!panelIsVisible(index)) return;
    focusedPanelIndex = index;
    panels.forEach(function (panel, panelIndex) {
      panel.classList.toggle("is-focused", panelIndex === index);
    });
    setActive(index);
  }

  function focusPanel(index, options) {
    var panel = panels[index];
    if (!panel || !panelIsVisible(index)) return;
    cancelHorizontalWheel();
    setFocusedPanel(index);
    if (centeredMode) {
      stage.scrollTo({ left: 0, behavior: "auto" });
      return;
    }
    stage.scrollTo({
      left: Math.max(0, panel.offsetLeft - stage.offsetLeft),
      behavior: options && options.behavior ? options.behavior : (reducedMotion ? "auto" : "smooth"),
    });
  }

  function togglePanelVisibility(index) {
    if (!panels[index]) return;
    var willHide = panelIsVisible(index);
    if (willHide && visiblePanelIndices().length === 1) return;
    panels[index].classList.toggle("is-hidden", willHide);
    syncVisibleDividers();
    if (!willHide && centeredMode) {
      focusPanel(index);
      return;
    }
    if (willHide && focusedPanelIndex === index) {
      var replacement = nearestVisiblePanel(index);
      if (replacement !== -1) focusPanel(replacement, { behavior: "auto" });
      return;
    }
    setActive(focusedPanelIndex);
  }

  // All focus-mode pages share one document.  Standalone pages use a full
  // navigation to hand data to the task page; focus mode must instead refresh
  // the already-mounted task/prompt panels in place.
  window.RHFocus = window.RHFocus || {};
  window.RHFocus.isFocusMode = true;
  window.RHFocus.pageNavigationBlocked = true;
  window.RHFocus.focusPanel = focusPanel;
  window.RHFocus.exitToTaskSubmit = function () {
    window.location.href = "/";
  };
  window.RHFocus.importToSubmit = function (detail) {
    var payload = detail && typeof detail === "object" ? detail : {};
    window.dispatchEvent(new CustomEvent("rh-focus-submit-update", { detail: payload }));
    window.dispatchEvent(new CustomEvent("rh-focus-prompt-update", { detail: payload }));
  };

  function isPageNavigationLink(link) {
    if (!link || !link.hasAttribute("href") || link.hasAttribute("download")) return false;
    try {
      var url = new URL(link.href, window.location.href);
      return url.origin === window.location.origin && pageNavigationPaths.indexOf(url.pathname.replace(/\/+$/, "") || "/") !== -1;
    } catch (error) {
      return false;
    }
  }

  // The focus workspace is a single document.  Keep route links inert here;
  // standalone pages retain their normal navigation behavior.
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[href]");
    if (!isPageNavigationLink(link)) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);

  function loadScript(filename) {
    return new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = "/static/" + filename;
      script.async = false;
      script.onload = resolve;
      script.onerror = function () { reject(new Error("无法载入页面脚本：" + filename)); };
      document.body.appendChild(script);
    });
  }

  function loadFragments() {
    return fetch("/api/focus/fragments").then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.message || "无法读取专注模式页面");
        return Array.isArray(data.pages) ? data.pages : [];
      });
    }).then(function (pages) {
      if (pages.length !== panels.length) throw new Error("专注模式页面数量不完整");
      var pagesById = {};
      pages.forEach(function (page) { pagesById[page.id] = page; });
      var orderedPages = focusPageOrder.map(function (pageId) { return pagesById[pageId]; });
      if (orderedPages.some(function (page) { return !page; })) throw new Error("专注模式页面顺序不完整");
      orderedPages.forEach(function (page) {
        var slot = document.querySelector('[data-focus-slot="' + CSS.escape(page.id) + '"]');
        if (slot) slot.innerHTML = page.html || "";
      });
      return orderedPages.reduce(function (chain, page) {
        return chain.then(function () { return loadScript(page.script); });
      }, Promise.resolve());
    });
  }

  links.forEach(function (link) {
    link.addEventListener("click", function () {
      togglePanelVisibility(focusPageOrder.indexOf(link.dataset.focusPage));
    });
  });

  function moveFocusedPanel(delta) {
    var visible = visiblePanelIndices();
    if (visible.length < 2) return;
    var currentPosition = visible.indexOf(focusedPanelIndex);
    if (currentPosition === -1) currentPosition = 0;
    var nextPosition = (currentPosition + delta + visible.length) % visible.length;
    focusPanel(visible[nextPosition]);
  }

  function handleFocusedPageNavigation(direction) {
    moveFocusedPanel(direction === "previous" ? -1 : 1);
  }

  function focusDirectionForKey(key) {
    if (key === "ArrowLeft" || key === "ArrowUp") return "previous";
    if (key === "ArrowRight" || key === "ArrowDown") return "next";
    return "";
  }

  function scrollOutputsPanel(direction, target) {
    var panel = target && target.closest ? target.closest("[data-focus-panel]") : null;
    if (!panel || panel.dataset.focusPage !== "outputs") panel = panels[focusedPanelIndex];
    if (!panel || panel.dataset.focusPage !== "outputs") return false;
    var maximum = Math.max(0, panel.scrollHeight - panel.clientHeight);
    if (!maximum) return false;
    var distance = Math.max(120, Math.round(panel.clientHeight * 0.72));
    var nextTop = clamp(panel.scrollTop + direction * distance, 0, maximum);
    if (nextTop === panel.scrollTop) return false;
    panel.scrollTo({ top: nextTop, behavior: reducedMotion ? "auto" : "smooth" });
    return true;
  }

  function isEditableTarget(target) {
    if (!target) return false;
    var tagName = String(target.tagName || "").toLowerCase();
    return tagName === "input" || tagName === "textarea" || tagName === "select" || Boolean(target.closest && target.closest("[contenteditable=\"true\"]"));
  }

  function setCenteredMode(next) {
    centeredMode = Boolean(next);
    stage.classList.toggle("is-centered", centeredMode);
    stage.setAttribute("aria-label", centeredMode ? "当前聚焦页面" : "六个页面横向工作区");
    cancelHorizontalWheel();
    if (centeredMode) {
      horizontalWheelTarget = 0;
      stage.scrollTo({ left: 0, behavior: "auto" });
      stage.scrollLeft = 0;
      return;
    }
    // Wait for the hidden panels to re-enter layout before measuring the
    // focused panel.  Restore instantly so a previous smooth scroll cannot
    // leave the viewport between pages.
    requestFrame(function () {
      if (!centeredMode) focusPanel(focusedPanelIndex, { behavior: "auto" });
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented || event.isComposing || event.metaKey) return;
    if (document.querySelector(".modal-backdrop.is-open:not([hidden])")) return;
    var focusDirection = focusDirectionForKey(event.key);
    if (event.altKey && !event.ctrlKey && !event.shiftKey && focusDirection) {
      event.preventDefault();
      event.stopPropagation();
      handleFocusedPageNavigation(focusDirection);
      return;
    }
    if (event.ctrlKey && !event.altKey && !event.shiftKey && String(event.key || "").toLowerCase() === "m" && !isEditableTarget(event.target)) {
      event.preventDefault();
      event.stopPropagation();
      setCenteredMode(!centeredMode);
      return;
    }
    if (!event.altKey && !event.ctrlKey && !event.shiftKey && !isEditableTarget(event.target)) {
      var scrollDirection = event.key === "ArrowDown" ? 1 : (event.key === "ArrowUp" ? -1 : 0);
      if (scrollDirection && scrollOutputsPanel(scrollDirection, event.target)) {
        event.preventDefault();
        event.stopPropagation();
      }
    }
  }, true);

  if (window.rhElectron && typeof window.rhElectron.onGlobalPageNavigation === "function") {
    window.rhElectron.onGlobalPageNavigation(handleFocusedPageNavigation);
  }

  function animateHorizontalWheel() {
    var current = stage.scrollLeft;
    var distance = horizontalWheelTarget - current;
    if (Math.abs(distance) < 0.5) {
      stage.scrollLeft = horizontalWheelTarget;
      horizontalWheelFrame = 0;
      return;
    }
    stage.scrollLeft = current + distance * (reducedMotion ? 1 : 0.22);
    horizontalWheelFrame = requestFrame(animateHorizontalWheel);
  }

  // Coalesce high-frequency trackpad events into one eased animation.
  stage.addEventListener("wheel", function (event) {
    if (!event.shiftKey) return;
    var delta = Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
    if (!delta) return;
    if (event.deltaMode === 1) delta *= 16;
    if (event.deltaMode === 2) delta *= stage.clientWidth;
    event.preventDefault();
    var base = horizontalWheelFrame ? horizontalWheelTarget : stage.scrollLeft;
    horizontalWheelTarget = clamp(base + delta, 0, maxHorizontalScroll());
    if (!horizontalWheelFrame) horizontalWheelFrame = requestFrame(animateHorizontalWheel);
  }, { passive: false });

  function updateDividerValue(divider, previousPanel) {
    divider.setAttribute("aria-valuenow", String(Math.round(previousPanel.getBoundingClientRect().width)));
  }

  function setAdjacentPanelWidths(index, previousWidth, nextWidth) {
    var pair = visibleDividerPanels(index);
    if (!pair) return;
    var previousPanel = panels[pair.previousIndex];
    var nextPanel = panels[pair.nextIndex];
    previousPanel.style.setProperty("--focus-panel-width", Math.round(previousWidth) + "px");
    nextPanel.style.setProperty("--focus-panel-width", Math.round(nextWidth) + "px");
    updateDividerValue(dividers[index], previousPanel);
  }

  function adjustDivider(divider, delta) {
    var index = Number(divider.dataset.focusDivider);
    var pair = visibleDividerPanels(index);
    if (!pair) return;
    var previousPanel = panels[pair.previousIndex];
    var nextPanel = panels[pair.nextIndex];
    var previousWidth = previousPanel.getBoundingClientRect().width;
    var nextWidth = nextPanel.getBoundingClientRect().width;
    var total = previousWidth + nextWidth;
    var minimum = Math.min(420, total / 2 - 1);
    var nextPreviousWidth = clamp(previousWidth + delta, minimum, total - minimum);
    setAdjacentPanelWidths(index, nextPreviousWidth, total - nextPreviousWidth);
  }

  function finishDividerResize(event) {
    if (!resizeState) return;
    var divider = resizeState.divider;
    if (event && divider.hasPointerCapture && divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
    divider.classList.remove("is-dragging");
    document.body.classList.remove("is-focus-resizing");
    resizeState = null;
  }

  dividers.forEach(function (divider, index) {
    var pair = visibleDividerPanels(index);
    if (pair) updateDividerValue(divider, panels[pair.previousIndex]);
    divider.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) return;
      var currentPair = visibleDividerPanels(index);
      if (!currentPair) return;
      var previousPanel = panels[currentPair.previousIndex];
      var nextPanel = panels[currentPair.nextIndex];
      event.preventDefault();
      resizeState = {
        divider: divider,
        index: index,
        startX: event.clientX,
        previousWidth: previousPanel.getBoundingClientRect().width,
        nextWidth: nextPanel.getBoundingClientRect().width,
      };
      divider.classList.add("is-dragging");
      document.body.classList.add("is-focus-resizing");
      if (divider.setPointerCapture) divider.setPointerCapture(event.pointerId);
    });
    divider.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      adjustDivider(divider, event.key === "ArrowRight" ? 24 : -24);
    });
  });

  window.addEventListener("pointermove", function (event) {
    if (!resizeState) return;
    var total = resizeState.previousWidth + resizeState.nextWidth;
    var minimum = Math.min(420, total / 2 - 1);
    var previousWidth = clamp(resizeState.previousWidth + event.clientX - resizeState.startX, minimum, total - minimum);
    setAdjacentPanelWidths(resizeState.index, previousWidth, total - previousWidth);
  });
  window.addEventListener("pointerup", finishDividerResize);
  window.addEventListener("pointercancel", finishDividerResize);

  var themeToggle = document.getElementById("focusThemeToggle");
  var themeIcon = document.getElementById("focusThemeToggleIcon");
  var themeLabel = document.getElementById("focusThemeToggleLabel");
  var exitButton = document.getElementById("focusExit");
  if (exitButton) exitButton.addEventListener("click", function () {
    window.RHFocus.exitToTaskSubmit();
  });
  function updateTheme() {
    var light = document.documentElement.dataset.theme !== "dark";
    if (themeIcon) themeIcon.textContent = light ? "☾" : "☀";
    if (themeLabel) themeLabel.textContent = light ? "夜间" : "日间";
    if (themeToggle) themeToggle.setAttribute("aria-label", light ? "切换到夜间模式" : "切换到日间模式");
  }
  if (themeToggle) themeToggle.addEventListener("click", function () {
    var next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("rh-workflow-theme", next); } catch (error) {}
    updateTheme();
  });

  syncVisibleDividers();
  setFocusedPanel(0);
  updateTheme();
  loadFragments().catch(function (error) {
    document.querySelectorAll(".focus-loading").forEach(function (element) {
      element.textContent = error.message || "专注模式载入失败";
      element.classList.add("is-error");
    });
  });
}());
