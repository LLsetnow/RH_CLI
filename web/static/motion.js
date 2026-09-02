(function () {
  "use strict";

  var MODAL_MS = 220;
  var PAGE_SLIDE_MS = 360;
  var PAGE_DIRECTION_KEY = "rh-motion-page-direction-v1";
  var modalTimers = {};
  var modalReturnFocus = {};
  var pageEnterStarted = false;
  var warmedPages = {};
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

  if (nativePageTransition && prefersReducedMotion()) {
    var reducedMotionStyle = document.createElement("style");
    reducedMotionStyle.textContent = "@view-transition { navigation: none; }";
    document.head.appendChild(reducedMotionStyle);
  }

  function isModifiedClick(event) {
    return event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
  }

  function isInternalPageLink(link) {
    if (!link || link.hasAttribute("download") || link.target && link.target !== "_self") return false;
    if (!link.closest(".top-nav, .brand-home-link")) return false;
    try {
      var url = new URL(link.href, window.location.href);
      return url.origin === window.location.origin && url.pathname !== window.location.pathname;
    } catch (error) {
      return false;
    }
  }

  function warmPage(link) {
    if (!link) return;
    try {
      var url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin || url.pathname === window.location.pathname || warmedPages[url.href]) return;
      warmedPages[url.href] = true;
      window.fetch(url.href, { credentials: "same-origin", cache: "no-store" }).catch(function () {
        delete warmedPages[url.href];
      });
    } catch (error) {}
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
    var pageOrder = ["/", "/prompt", "/outputs", "/workflows"];
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
    warmPage(link);
    rememberPageDirection(pageDirectionFor(link));
    // Let the browser navigate immediately. Native cross-document view
    // transitions keep both page snapshots visible, so there is no blank gap.
    return false;
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
    window.requestAnimationFrame(function () {
      modal.classList.add("is-open");
      syncModalScrollLock();
    });
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

  function videoPlayerMarkup(url, autoplay, showLoop) {
    var loopButton = showLoop === false ? "" : '<button class="video-loop-toggle" type="button" data-video-loop aria-pressed="false" title="开启循环播放">↻ 循环播放</button>';
    return '<div class="video-player" data-video-player><video src="' + url + '" controls' + (autoplay ? ' autoplay' : '') + ' preload="metadata"></video>' + loopButton + '</div>';
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
    document.querySelectorAll(".top-nav a, .brand-home-link").forEach(function (link) {
      link.addEventListener("pointerenter", function () { warmPage(link); }, { once: true, passive: true });
    });
  });

  window.RHMotion = {
    bindVideoLoopControls: bindVideoLoopControls,
    closeModal: closeModal,
    openModal: openModal,
    startPageEnter: startPageEnter,
    prefersReducedMotion: prefersReducedMotion,
    videoPlayerMarkup: videoPlayerMarkup
  };
}());
