(function () {
  "use strict";

  var MODAL_MS = 220;
  var SHUFFLE_MS = 360;
  var SHUFFLE_LEAVE_MS = 430;
  var SHUFFLE_STAGGER = 10;
  var SHUFFLE_MAX_STAGGER = 50;
  var modalTimers = {};
  var modalReturnFocus = {};
  var activeShuffle = null;
  var shuffleSelectors = [
    ".app-shell > .topbar .top-nav",
    ".app-shell > .intro-block",
    ".app-shell > .process-nav",
    ".app-shell > .workspace .queue-column > .process-nav",
    ".app-shell > .workspace .panel",
    ".app-shell > main > .prompt-intro",
    ".app-shell > main > .prompt-builder-grid .panel",
    ".app-shell > main > .outputs-hero",
    ".app-shell > main > .outputs-toolbar",
    ".app-shell > main > .output-grid"
  ];

  function prefersReducedMotion() {
    return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
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

  function shuffleSurfaces() {
    var surfaces = [];
    shuffleSelectors.forEach(function (selector) {
      document.querySelectorAll(selector).forEach(function (surface) {
        if (surfaces.indexOf(surface) !== -1) return;
        var rect = surface.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) surfaces.push(surface);
      });
    });
    return surfaces;
  }

  function layoutRect(surface) {
    var transform = surface.style.transform;
    var transition = surface.style.transition;
    surface.style.transition = "none";
    surface.style.transform = "none";
    var rect = surface.getBoundingClientRect();
    surface.style.transform = transform;
    surface.style.transition = transition;
    return rect;
  }

  function centerTransform(rect, index, scale) {
    var x = window.innerWidth / 2 - (rect.left + rect.width / 2);
    var y = window.innerHeight / 2 - (rect.top + rect.height / 2);
    var direction = index % 2 === 0 ? 1 : -1;
    return "translate3d(" + Math.round(x) + "px, " + Math.round(y) + "px, 0) scale(" + scale + ") rotate(" + (direction * (1.6 + index * .25)).toFixed(2) + "deg)";
  }

  function restoreSurface(surface, original) {
    surface.style.transform = original.transform;
    surface.style.opacity = original.opacity;
    surface.style.transition = original.transition;
    surface.style.transitionDelay = original.transitionDelay;
    surface.style.transformOrigin = original.transformOrigin;
    surface.style.willChange = original.willChange;
    surface.style.animation = original.animation;
    surface.style.animationDelay = original.animationDelay;
    surface.style.animationPlayState = original.animationPlayState;
  }

  function cancelActiveShuffle() {
    if (!activeShuffle) return;
    if (activeShuffle.frame) window.cancelAnimationFrame(activeShuffle.frame);
    if (activeShuffle.timer) window.clearTimeout(activeShuffle.timer);
    activeShuffle.cancelled = true;
    activeShuffle = null;
  }

  function prepareShuffleIn(surfaces) {
    var originals = surfaces.map(function (surface, index) {
      var original = {
        opacity: surface.style.opacity,
        transition: surface.style.transition,
        transitionDelay: surface.style.transitionDelay,
        transform: surface.style.transform,
        transformOrigin: surface.style.transformOrigin,
        willChange: surface.style.willChange,
        animation: surface.style.animation,
        animationDelay: surface.style.animationDelay,
        animationPlayState: surface.style.animationPlayState
      };
      surface.style.transformOrigin = "center center";
      surface.style.willChange = "transform, opacity";
      surface.style.animation = "none";
      surface.style.animationDelay = "0ms";
      surface.style.animationPlayState = "paused";
      surface.style.transition = "none";
      surface.style.transitionDelay = "0ms";
      surface.style.opacity = ".86";
      surface.style.transform = centerTransform(layoutRect(surface), index, ".9");
      return original;
    });
    activeShuffle = { frame: 0, timer: 0, cancelled: false, surfaces: surfaces, originals: originals };
    activeShuffle.frame = window.requestAnimationFrame(function () {
      if (!activeShuffle || activeShuffle.cancelled) return;
      surfaces.forEach(function (surface, index) {
        surface.style.transition = "transform " + SHUFFLE_MS + "ms var(--ease-in-out), opacity 260ms var(--ease-out)";
        surface.style.transitionDelay = Math.min(index * SHUFFLE_STAGGER, SHUFFLE_MAX_STAGGER) + "ms";
        surface.style.opacity = "1";
        surface.style.transform = "none";
      });
    });
    activeShuffle.timer = window.setTimeout(function () {
      if (!activeShuffle || activeShuffle.cancelled) return;
      surfaces.forEach(function (surface, index) { restoreSurface(surface, originals[index]); });
      activeShuffle = null;
    }, SHUFFLE_MS + SHUFFLE_MAX_STAGGER + 100);
  }

  function prepareShuffleOut(surfaces) {
    surfaces.forEach(function (surface, index) {
      surface.style.transformOrigin = "center center";
      surface.style.willChange = "transform, opacity";
      surface.style.animation = "none";
      surface.style.animationDelay = "0ms";
      surface.style.animationPlayState = "paused";
      surface.style.transition = "transform " + SHUFFLE_MS + "ms var(--ease-in-out), opacity 280ms var(--ease-out)";
      surface.style.transitionDelay = Math.min(index * SHUFFLE_STAGGER, SHUFFLE_MAX_STAGGER) + "ms";
      surface.style.opacity = ".78";
      surface.style.transform = centerTransform(layoutRect(surface), index, ".9");
    });
  }

  function startPageEnter() {
    var root = document.documentElement;
    if (prefersReducedMotion()) return;
    cancelActiveShuffle();
    var surfaces = shuffleSurfaces();
    if (surfaces.length) prepareShuffleIn(surfaces);
    root.classList.add("motion-page-enter");
    window.requestAnimationFrame(function () {
      root.classList.add("motion-page-enter-active");
    });
    window.setTimeout(function () {
      root.classList.remove("motion-page-enter", "motion-page-enter-active");
    }, 460);
  }

  function startPageLeave(link, event) {
    if (!isInternalPageLink(link) || isModifiedClick(event) || prefersReducedMotion()) return false;
    var root = document.documentElement;
    if (root.classList.contains("motion-page-leave")) return true;
    event.preventDefault();
    cancelActiveShuffle();
    root.classList.remove("motion-page-enter", "motion-page-enter-active");
    root.classList.add("motion-page-leave", "motion-shuffle-active");
    prepareShuffleOut(shuffleSurfaces());
    window.setTimeout(function () { window.location.assign(link.href); }, SHUFFLE_LEAVE_MS);
    return true;
  }

  function elementById(id) {
    return id ? document.getElementById(id) : null;
  }

  function finishClose(id, modal) {
    modal.hidden = true;
    modal.classList.remove("is-open", "is-closing");
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
    window.requestAnimationFrame(function () { modal.classList.add("is-open"); });
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

  if (!prefersReducedMotion()) document.documentElement.classList.add("motion-page-enter");
  document.addEventListener("DOMContentLoaded", function () {
    startPageEnter();
    document.addEventListener("click", function (event) {
      var link = event.target.closest && event.target.closest("a");
      if (link) startPageLeave(link, event);
    });
  });

  window.RHMotion = {
    closeModal: closeModal,
    openModal: openModal,
    prefersReducedMotion: prefersReducedMotion
  };
}());
