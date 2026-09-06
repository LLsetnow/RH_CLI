(function () {
  "use strict";

  var MODAL_MS = 220;
  var PAGE_SLIDE_MS = 360;
  var PAGE_DIRECTION_KEY = "rh-motion-page-direction-v1";
  var modalTimers = {};
  var modalReturnFocus = {};
  var pageEnterStarted = false;
  var pendingPageDirection = "forward";
  var nativePageTransition = Boolean(
    window.CSS &&
    typeof window.CSS.supports === "function" &&
    window.CSS.supports("view-transition-name: root")
  );

  try {
    var storedDirection = window.sessionStorage.getItem(PAGE_DIRECTION_KEY);
    if (storedDirection === "backward") pendingPageDirection = "backward";
    window.sessionStorage.removeItem(PAGE_DIRECTION_KEY);
  } catch (error) {}

  function prefersReducedMotion() {
    return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  var TOAST_MAX_VISIBLE = 3;
  var TOAST_DURATION_MS = 3200;
  var TOAST_EXIT_MS = 220;

  function nextFrame(callback) {
    if (window.requestAnimationFrame) window.requestAnimationFrame(callback);
    else window.setTimeout(callback, 0);
  }

  function toastStateFor(stack) {
    if (stack.__rhToastState) return stack.__rhToastState;
    stack.classList.remove("toast");
    stack.classList.add("toast-stack");
    stack.textContent = "";
    stack.setAttribute("role", "status");
    stack.setAttribute("aria-live", "polite");
    stack.setAttribute("aria-atomic", "false");
    stack.__rhToastState = { visible: [], pending: [], nextId: 0 };
    return stack.__rhToastState;
  }

  function toastPositions(state) {
    var positions = {};
    state.visible.forEach(function (record) {
      if (!record.leaving) positions[record.id] = record.element.getBoundingClientRect().top;
    });
    return positions;
  }

  function animateToastReflow(state, previousPositions) {
    if (prefersReducedMotion()) return;
    state.visible.forEach(function (record) {
      var previousTop = previousPositions[record.id];
      if (previousTop == null || record.leaving) return;
      var nextTop = record.element.getBoundingClientRect().top;
      var delta = previousTop - nextTop;
      if (Math.abs(delta) < 1) return;
      record.element.style.transform = "translate3d(0, " + delta + "px, 0)";
      nextFrame(function () {
        record.element.style.transform = "";
      });
    });
  }

  function appendToast(stack, state, payload) {
    state.nextId += 1;
    var element = document.createElement("div");
    var record = { id: state.nextId, element: element, leaving: false, timer: 0 };
    element.className = "toast toast-item" + (payload.isError ? " error" : "");
    element.textContent = payload.message;
    element.setAttribute("role", "status");
    stack.appendChild(element);
    state.visible.push(record);
    nextFrame(function () {
      if (!record.leaving) element.classList.add("show");
    });
    record.timer = window.setTimeout(function () {
      removeToast(state, record);
    }, payload.duration);
  }

  function removeToast(state, record) {
    if (!record || record.leaving) return;
    record.leaving = true;
    window.clearTimeout(record.timer);
    var previousPositions = toastPositions(state);
    record.element.classList.remove("show");
    record.element.classList.add("is-leaving");
    window.setTimeout(function () {
      var index = state.visible.indexOf(record);
      if (index !== -1) state.visible.splice(index, 1);
      if (record.element.parentNode) record.element.parentNode.removeChild(record.element);
      while (state.visible.length < TOAST_MAX_VISIBLE && state.pending.length) {
        appendToast(state.stack, state, state.pending.shift());
      }
      animateToastReflow(state, previousPositions);
      if (state.pending.length && state.visible.length >= TOAST_MAX_VISIBLE) {
        removeToast(state, state.visible[0]);
      }
    }, prefersReducedMotion() ? 0 : TOAST_EXIT_MS);
  }

  function showToast(stack, message, isError) {
    if (!stack) return;
    var state = toastStateFor(stack);
    state.stack = stack;
    var previousPositions = toastPositions(state);
    state.pending.push({ message: String(message || ""), isError: Boolean(isError), duration: TOAST_DURATION_MS });
    while (state.visible.length < TOAST_MAX_VISIBLE && state.pending.length) {
      appendToast(stack, state, state.pending.shift());
    }
    animateToastReflow(state, previousPositions);
    if (state.pending.length && state.visible.length >= TOAST_MAX_VISIBLE) {
      removeToast(state, state.visible[0]);
    }
  }

  if (nativePageTransition && prefersReducedMotion()) {
    var reducedMotionStyle = document.createElement("style");
    reducedMotionStyle.textContent = "@view-transition { navigation: none; }";
    document.head.appendChild(reducedMotionStyle);
  }

  function isModifiedClick(event) {
    return event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
  }

  function isInternalPageLink(link) {
    if (document.body.classList.contains("focus-body")) return false;
    if (!link || link.hasAttribute("download") || link.target && link.target !== "_self") return false;
    if (!link.closest(".top-nav, .brand-home-link")) return false;
    try {
      var url = new URL(link.href, window.location.href);
      return url.origin === window.location.origin && url.pathname !== window.location.pathname;
    } catch (error) {
      return false;
    }
  }

  function startPageEnter() {
    var root = document.documentElement;
    if (pageEnterStarted || prefersReducedMotion() || nativePageTransition) return;
    pageEnterStarted = true;
    root.classList.add("motion-page-enter", "motion-page-enter-from-" + pendingPageDirection);
    window.requestAnimationFrame(function () {
      root.classList.add("motion-page-enter-active");
    });
    window.setTimeout(function () {
      root.classList.remove("motion-page-enter", "motion-page-enter-active", "motion-page-enter-from-forward", "motion-page-enter-from-backward");
    }, PAGE_SLIDE_MS + 100);
  }

  function navigationPath(pathname) {
    var normalized = String(pathname || "").replace(/\/+$/, "");
    return normalized || "/";
  }

  function pageDirectionFor(link) {
    var pageOrder = ["/workflows", "/prompt", "/", "/outputs", "/dashboard", "/settings", "/outputs/compare"];
    var currentIndex = pageOrder.indexOf(navigationPath(window.location.pathname));
    var targetIndex = pageOrder.indexOf(navigationPath(new URL(link.href, window.location.href).pathname));
    if (currentIndex === -1 || targetIndex === -1 || currentIndex === targetIndex) return "forward";
    return targetIndex > currentIndex ? "forward" : "backward";
  }

  function rememberPageDirection(direction) {
    try { window.sessionStorage.setItem(PAGE_DIRECTION_KEY, direction); } catch (error) {}
  }

  function startPageLeave(link, event) {
    if (!isInternalPageLink(link) || isModifiedClick(event) || prefersReducedMotion()) return false;
    rememberPageDirection(pageDirectionFor(link));
    // Let the browser navigate immediately. Native cross-document view
    // transitions keep both page snapshots visible, so there is no blank gap.
    return false;
  }

  function requestGlobalTaskSubmit() {
    if (document.body.classList.contains("focus-body")) {
      document.dispatchEvent(new CustomEvent("rh-submit-task"));
      return;
    }
    if (navigationPath(window.location.pathname) === "/") {
      document.dispatchEvent(new CustomEvent("rh-submit-task"));
      return;
    }
    var target = new URL("/", window.location.href);
    target.searchParams.set("autoSubmit", "1");
    window.location.href = target.href;
  }

  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented || event.isComposing || !event.ctrlKey || event.shiftKey || event.altKey || event.metaKey || event.key !== "Enter") return;
    if (document.querySelector(".modal-backdrop.is-open:not([hidden])")) return;
    event.preventDefault();
    requestGlobalTaskSubmit();
  });

  function topLevelNavigationLinks() {
    if (document.body.classList.contains("focus-body")) return [];
    return Array.prototype.slice.call(document.querySelectorAll(".top-nav-link"));
  }

  function currentTopLevelNavigationIndex(links) {
    var activeIndex = links.findIndex(function (link) { return link.classList.contains("active"); });
    if (activeIndex >= 0) return activeIndex;
    var pathname = navigationPath(window.location.pathname);
    return links.findIndex(function (link) {
      try {
        return navigationPath(new URL(link.href, window.location.href).pathname) === pathname;
      } catch (error) {
        return false;
      }
    });
  }

  function navigateTopLevelPage(delta) {
    var links = topLevelNavigationLinks();
    if (links.length < 2) return false;
    var currentIndex = currentTopLevelNavigationIndex(links);
    if (currentIndex < 0) return false;
    var targetIndex = (currentIndex + delta + links.length) % links.length;
    var target = links[targetIndex];
    if (!target) return false;
    target.click();
    return true;
  }

  function handleGlobalPageNavigation(direction) {
    navigateTopLevelPage(direction === "previous" ? -1 : 1);
  }

  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented || event.isComposing || !event.altKey || event.ctrlKey || event.shiftKey || event.metaKey) return;
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    if (document.body.classList.contains("focus-body")) return;
    event.preventDefault();
    event.stopPropagation();
    handleGlobalPageNavigation(event.key === "ArrowLeft" ? "previous" : "next");
  }, true);

  if (window.rhElectron && typeof window.rhElectron.onGlobalPageNavigation === "function") {
    window.rhElectron.onGlobalPageNavigation(handleGlobalPageNavigation);
  }

  function elementById(id) {
    return id ? document.getElementById(id) : null;
  }

  function syncModalScrollLock() {
    var hasOpenModal = Boolean(document.querySelector(".modal-backdrop.is-open:not([hidden])"));
    document.documentElement.classList.toggle("modal-open", hasOpenModal);
  }

  function finishClose(id, modal) {
    modal.hidden = true;
    modal.classList.remove("is-open", "is-closing");
    syncModalScrollLock();
    var returnFocus = modalReturnFocus[id];
    delete modalReturnFocus[id];
    delete modalTimers[id];
    if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
  }

  function openModal(id, focusId) {
    var modal = elementById(id);
    if (!modal) return;
    if (modalTimers[id]) window.clearTimeout(modalTimers[id]);
    if (modal.hidden) {
      var active = document.activeElement;
      if (active && active !== document.body) modalReturnFocus[id] = active;
    }
    modal.hidden = false;
    modal.classList.remove("is-closing");
    function activateModal() {
      if (modal.hidden || modal.classList.contains("is-closing")) return;
      modal.classList.add("is-open");
      syncModalScrollLock();
    }
    window.requestAnimationFrame(function () {
      activateModal();
    });
    // Electron can temporarily suspend requestAnimationFrame while a window
    // is being restored or a native page transition is settling. Keep the
    // modal usable even when that frame never arrives.
    window.setTimeout(activateModal, 80);
    if (focusId) {
      window.setTimeout(function () {
        var target = elementById(focusId);
        if (target && !modal.hidden) target.focus();
      }, 0);
    }
  }

  function closeModal(id) {
    var modal = elementById(id);
    if (!modal || modal.hidden) return;
    modal.classList.remove("is-open");
    modal.classList.add("is-closing");
    if (modalTimers[id]) window.clearTimeout(modalTimers[id]);
    if (prefersReducedMotion()) {
      finishClose(id, modal);
      return;
    }
    modalTimers[id] = window.setTimeout(function () { finishClose(id, modal); }, MODAL_MS);
  }

  function videoPlayerMarkup(url, autoplay, showLoop, leadingControlsMarkup) {
    var loopButton = showLoop === false ? "" : '<button class="video-loop-toggle" type="button" data-video-loop aria-pressed="false" title="开启循环播放">↻ 循环播放</button>';
    var leadingControls = leadingControlsMarkup || "";
    var controls = loopButton || leadingControls ? '<div class="video-player-controls">' + leadingControls + loopButton + '</div>' : "";
    return '<div class="video-player" data-video-player><video src="' + url + '" controls' + (autoplay ? ' autoplay' : '') + ' preload="metadata"></video>' + controls + '</div>';
  }

  function syncVideoLoopButton(button, video) {
    var enabled = Boolean(video && video.loop);
    button.classList.toggle("is-active", enabled);
    button.setAttribute("aria-pressed", enabled ? "true" : "false");
    button.setAttribute("aria-label", enabled ? "关闭循环播放" : "开启循环播放");
    button.title = enabled ? "关闭循环播放" : "开启循环播放";
    button.textContent = enabled ? "↻ 已循环" : "↻ 循环播放";
  }

  function bindVideoLoopControls(container) {
    if (!container) return;
    container.querySelectorAll("[data-video-loop]").forEach(function (button) {
      if (button.dataset.loopBound === "true") return;
      button.dataset.loopBound = "true";
      var player = button.closest("[data-video-player]");
      var video = player && player.querySelector("video");
      if (!video) return;
      syncVideoLoopButton(button, video);
      button.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        video.loop = !video.loop;
        syncVideoLoopButton(button, video);
      });
    });
  }

  var videoContextMenu = null;
  var videoContextTarget = null;

  function closeVideoContextMenu() {
    if (!videoContextMenu) return;
    videoContextMenu.hidden = true;
    videoContextTarget = null;
  }

  function ensureVideoContextMenu() {
    if (videoContextMenu) return videoContextMenu;
    videoContextMenu = document.createElement("div");
    videoContextMenu.className = "video-context-menu";
    videoContextMenu.hidden = true;
    videoContextMenu.setAttribute("role", "menu");
    videoContextMenu.setAttribute("aria-label", "视频操作");
    videoContextMenu.innerHTML = '<div class="video-context-menu-time">当前播放位置 <strong data-video-context-time>0.0s</strong></div><button type="button" data-capture-video-frame role="menuitem">截取当前帧</button>';
    document.body.appendChild(videoContextMenu);
    videoContextMenu.querySelector("[data-capture-video-frame]").addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      captureVideoFrame(videoContextTarget, this);
    });
    return videoContextMenu;
  }

  function showVideoContextMenu(video, event) {
    var menu = ensureVideoContextMenu();
    videoContextTarget = video;
    var time = menu.querySelector("[data-video-context-time]");
    if (time) time.textContent = (Number(video.currentTime || 0)).toFixed(2) + "s";
    menu.hidden = false;
    var rect = menu.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(event.clientX, window.innerWidth - rect.width - 8)) + "px";
    menu.style.top = Math.max(8, Math.min(event.clientY, window.innerHeight - rect.height - 8)) + "px";
    var button = menu.querySelector("[data-capture-video-frame]");
    if (button) button.focus();
  }

  function captureVideoFrame(video, button) {
    if (!video || !video.videoWidth || !video.videoHeight) {
      closeVideoContextMenu();
      showToast(document.querySelector(".toast-stack"), "视频画面还没有加载完成，请稍后再试", true);
      return;
    }
    if (button) {
      button.disabled = true;
      button.textContent = "正在截取…";
    }
    var canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    var context = canvas.getContext("2d");
    if (!context) {
      closeVideoContextMenu();
      showToast(document.querySelector(".toast-stack"), "当前环境无法截取视频帧", true);
      return;
    }
    try { context.drawImage(video, 0, 0, canvas.width, canvas.height); } catch (error) {
      closeVideoContextMenu();
      showToast(document.querySelector(".toast-stack"), "视频帧读取失败，请确认素材来自本地工作台", true);
      return;
    }
    var dataUrl;
    try { dataUrl = canvas.toDataURL("image/png"); } catch (error) {
      closeVideoContextMenu();
      showToast(document.querySelector(".toast-stack"), "视频帧导出失败", true);
      return;
    }
    var base64 = dataUrl.split(",")[1] || "";
    fetch("/api/paste-file", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ name: "video-frame-" + Date.now() + ".png", mime: "image/png", data: base64 })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.message || "截帧保存失败");
        return data;
      });
    }).then(function (asset) {
      video.dispatchEvent(new CustomEvent("rh:video-frame-captured", { bubbles: true, detail: { video: video, asset: asset } }));
      showToast(document.querySelector(".toast-stack"), "已保存当前帧，可作为图片输入使用");
      closeVideoContextMenu();
    }).catch(function (error) {
      showToast(document.querySelector(".toast-stack"), error.message, true);
      if (button) { button.disabled = false; button.textContent = "截取当前帧"; }
    });
  }

  document.addEventListener("contextmenu", function (event) {
    var target = event.target;
    var video = target && target.closest ? target.closest("video") : null;
    if (!video) return;
    event.preventDefault();
    event.stopPropagation();
    showVideoContextMenu(video, event);
  }, true);
  document.addEventListener("pointerdown", function (event) {
    if (videoContextMenu && !videoContextMenu.hidden && !videoContextMenu.contains(event.target)) closeVideoContextMenu();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeVideoContextMenu();
  });

  if (!prefersReducedMotion()) {
    document.documentElement.classList.add("motion-page-enter-from-" + pendingPageDirection);
    if (!nativePageTransition) document.documentElement.classList.add("motion-page-enter");
  }
  document.addEventListener("DOMContentLoaded", function () {
    startPageEnter();
    document.addEventListener("click", function (event) {
      var link = event.target.closest && event.target.closest("a");
      if (link) startPageLeave(link, event);
    });
  });

  window.RHMotion = {
    bindVideoLoopControls: bindVideoLoopControls,
    closeModal: closeModal,
    openModal: openModal,
    navigateTopLevelPage: navigateTopLevelPage,
    showToast: showToast,
    captureVideoFrame: captureVideoFrame,
    closeVideoContextMenu: closeVideoContextMenu,
    startPageEnter: startPageEnter,
    prefersReducedMotion: prefersReducedMotion,
    videoPlayerMarkup: videoPlayerMarkup
  };
}());
