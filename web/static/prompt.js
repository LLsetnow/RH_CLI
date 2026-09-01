(function () {
  "use strict";

  var STORAGE_KEY = "rh-workflow-desk-prompt-builder-v1";
  var idCounter = 0;
  var defaultBlocks = [
    { id: "preset-subject", title: "主体细节", text: "主体清晰突出，细节自然，材质纹理真实。", tags: ["画面", "质量"] },
    { id: "preset-light", title: "电影光线", text: "电影感光线，柔和侧光，明暗层次自然，保留高光细节。", tags: ["光线", "风格"] },
    { id: "preset-camera", title: "镜头推进", text: "镜头缓慢向前推进，运动平稳，始终保持主体在视觉中心。", tags: ["镜头", "动作"] },
    { id: "preset-depth", title: "浅景深", text: "浅景深，主体与背景分离，背景自然虚化。", tags: ["镜头", "画面"] },
    { id: "preset-texture", title: "真实质感", text: "真实纹理与细腻表面质感，画面干净，没有多余元素。", tags: ["质量", "画面"] }
  ];
  var state = { customBlocks: [], stage: [], filter: "全部", search: "", draggedIndex: null, draggedLibraryId: "", dragPreviewIndex: null, dragPreviewFrames: [], pointerDrag: null };
  var toastTimer = 0;

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }
  function unique(values) {
    var seen = {};
    return values.filter(function (value) {
      var key = String(value || "").trim();
      if (!key || seen[key]) return false;
      seen[key] = true;
      return true;
    });
  }
  function makeId(prefix) {
    idCounter += 1;
    return prefix + "-" + Date.now().toString(36) + "-" + idCounter;
  }
  function allBlocks() { return defaultBlocks.concat(state.customBlocks); }
  function getPromptText() {
    return state.stage.map(function (item) { return String(item.text || "").trim(); }).filter(Boolean).join("\n\n");
  }
  function showToast(message, isError) {
    var toast = $("promptToast");
    toast.textContent = message;
    toast.className = "toast show" + (isError ? " error" : "");
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { toast.className = "toast"; }, 3200);
  }
  function updateThemeToggle() {
    var button = $("themeToggle");
    var icon = $("themeToggleIcon");
    var label = $("themeToggleLabel");
    if (!button || !icon || !label) return;
    var isLight = document.documentElement.dataset.theme === "light";
    icon.textContent = isLight ? "☾" : "☀";
    label.textContent = isLight ? "夜间" : "日间";
    button.setAttribute("aria-label", isLight ? "切换到夜间模式" : "切换到日间模式");
    button.title = isLight ? "切换到夜间模式" : "切换到日间模式";
  }
  function saveState() {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ customBlocks: state.customBlocks, stage: state.stage }));
    } catch (error) {
      showToast("浏览器未能保存当前积木", true);
    }
  }
  function loadState() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
      state.customBlocks = Array.isArray(saved.customBlocks) ? saved.customBlocks.filter(function (item) {
        return item && item.id && item.title && item.text;
      }) : [];
      state.stage = Array.isArray(saved.stage) ? saved.stage.filter(function (item) {
        return item && (item.kind === "text" || item.kind === "fixed") && item.instanceId;
      }) : [];
    } catch (error) {
      state.customBlocks = [];
      state.stage = [];
    }
  }
  function blockMatches(block) {
    var needle = state.search.toLowerCase();
    var matchesTag = state.filter === "全部" || block.tags.indexOf(state.filter) !== -1;
    var matchesSearch = !needle || [block.title, block.text].concat(block.tags).join(" ").toLowerCase().indexOf(needle) !== -1;
    return matchesTag && matchesSearch;
  }
  function renderFilters() {
    var tags = unique([].concat.apply([], allBlocks().map(function (block) { return block.tags || []; })));
    if (state.filter !== "全部" && tags.indexOf(state.filter) === -1) state.filter = "全部";
    $("tagFilters").innerHTML = ["全部"].concat(tags).map(function (tag) {
      return '<button class="tag-filter' + (state.filter === tag ? " active" : "") + '" type="button" data-filter-tag="' + esc(tag) + '">' + esc(tag) + '</button>';
    }).join("");
  }
  function renderLibrary() {
    var blocks = allBlocks().filter(blockMatches);
    var html = '<button class="free-block-card" type="button" draggable="true" data-add-text-block>' +
      '<span class="block-type-dot text" aria-hidden="true"></span>' +
      '<span class="free-block-copy"><strong>自由文本</strong><span>每次加入一块新的可编辑文本</span></span>' +
      '<span class="free-block-plus" aria-hidden="true">＋</span></button>';
    if (!blocks.length) {
      html += '<div class="library-empty">没有匹配的固定积木。<br />试试其他标签或添加一块新的积木。</div>';
    } else {
      html += blocks.map(function (block, index) {
        var isCustom = block.id.indexOf("custom-") === 0;
        return '<article class="library-block" draggable="true" data-library-block-id="' + esc(block.id) + '" style="animation-delay:' + Math.min(index * 35, 220) + 'ms">' +
          '<div class="library-block-top"><div class="library-block-title"><span class="block-type-dot" aria-hidden="true"></span><span>' + esc(block.title) + '</span></div>' +
          '<span class="library-block-label">固定</span></div>' +
          '<div class="library-block-text">' + esc(block.text) + '</div>' +
          '<div class="block-tags">' + (block.tags || []).map(function (tag) { return '<span class="block-tag">' + esc(tag) + '</span>'; }).join("") + '</div>' +
          '<div class="library-block-footer"><button class="add-block-button" type="button" data-add-block="' + esc(block.id) + '">加入组装台&nbsp;→</button>' +
          (isCustom ? '<button class="delete-block-button" type="button" data-delete-block="' + esc(block.id) + '">删除</button>' : "") + '</div></article>';
      }).join("");
    }
    $("libraryList").innerHTML = html;
    $("libraryCount").textContent = blocks.length + " 个固定积木";
  }
  function stageBlockMarkup(item, index, total) {
    var isText = item.kind === "text";
    var tags = item.tags || [];
    return '<article class="stage-block ' + (isText ? "text" : "fixed") + '" draggable="false" data-stage-index="' + index + '" data-stage-instance-id="' + esc(item.instanceId) + '">' +
      '<div class="stage-block-grip" data-drag-handle title="拖动排序" aria-label="拖动排序">⋮⋮</div>' +
      '<div class="stage-block-main"><div class="stage-block-top"><span class="stage-index">' + String(index + 1).padStart(2, "0") + '</span><span class="stage-type-label">' + (isText ? "自由文本" : "固定积木") + '</span></div>' +
      '<h3>' + esc(item.title || (isText ? "自由文本" : "固定积木")) + '</h3>' +
      (isText ? '<textarea class="stage-text-editor" data-stage-text="' + index + '" placeholder="输入这一块要拼接的文本内容"></textarea>' : '<div class="stage-block-copy">' + esc(item.text) + '</div>') +
      (tags.length ? '<div class="stage-block-tags">' + tags.map(function (tag) { return '<span class="block-tag">' + esc(tag) + '</span>'; }).join("") + '</div>' : "") +
      '</div><div class="stage-block-actions"><button class="stage-action" type="button" data-move-stage="up" data-stage-index="' + index + '" aria-label="上移"' + (index === 0 ? ' disabled' : '') + '>↑</button><button class="stage-action" type="button" data-move-stage="down" data-stage-index="' + index + '" aria-label="下移"' + (index === total - 1 ? ' disabled' : '') + '>↓</button><button class="stage-action remove" type="button" data-remove-stage="' + index + '">移除</button></div></article>';
  }
  function updateStageDom() {
    var cards = $("stageList").querySelectorAll(".stage-block");
    $("stageCount").textContent = state.stage.length + " 个积木";
    cards.forEach(function (card, index) {
      card.dataset.stageIndex = String(index);
      var indexLabel = card.querySelector(".stage-index");
      if (indexLabel) indexLabel.textContent = String(index + 1).padStart(2, "0");
      var upButton = card.querySelector('[data-move-stage="up"]');
      var downButton = card.querySelector('[data-move-stage="down"]');
      var removeButton = card.querySelector("[data-remove-stage]");
      if (upButton) {
        upButton.dataset.stageIndex = String(index);
        upButton.disabled = index === 0;
      }
      if (downButton) {
        downButton.dataset.stageIndex = String(index);
        downButton.disabled = index === state.stage.length - 1;
      }
      if (removeButton) removeButton.dataset.removeStage = String(index);
      var editor = card.querySelector("[data-stage-text]");
      if (editor) editor.dataset.stageText = String(index);
    });
  }
  function renderStage() {
    var list = $("stageList");
    if (!state.stage.length) {
      list.innerHTML = '<div class="stage-empty"><span class="stage-empty-mark">01</span><strong>组装台还是空的</strong><span>从左侧点击固定积木，或加入一块自由文本开始。</span></div>';
      updateStageDom();
      renderOutput();
      return;
    }
    list.innerHTML = state.stage.map(function (item, index) {
      return stageBlockMarkup(item, index, state.stage.length);
    }).join("");
    state.stage.forEach(function (item, index) {
      if (item.kind !== "text") return;
      var editor = document.querySelector('[data-stage-text="' + index + '"]');
      if (editor) editor.value = item.text || "";
    });
    updateStageDom();
    renderOutput();
  }
  function renderOutput() {
    var output = getPromptText();
    $("promptOutput").value = output;
    $("charCount").textContent = output.length + " 字";
    $("lineCount").textContent = output ? output.split(/\n\s*\n/).length + " 段" : "0 段";
  }
  function renderAll() {
    renderFilters();
    renderLibrary();
    renderStage();
  }
  function stageItemFromLibrary(id) {
    if (id === "__free_text__") return { instanceId: makeId("text"), kind: "text", title: "自由文本", text: "", tags: [] };
    var block = allBlocks().find(function (item) { return item.id === id; });
    if (!block) return null;
    return { instanceId: makeId("fixed"), kind: "fixed", sourceId: block.id, title: block.title, text: block.text, tags: block.tags || [] };
  }
  function insertLibraryBlock(id, insertIndex) {
    var item = stageItemFromLibrary(id);
    if (!item) return;
    var safeIndex = Math.max(0, Math.min(Number(insertIndex), state.stage.length));
    state.stage.splice(safeIndex, 0, item);
    saveState();
    var list = $("stageList");
    var empty = list.querySelector(".stage-empty");
    if (empty) empty.remove();
    var card = document.createElement("article");
    card.innerHTML = stageBlockMarkup(item, safeIndex, state.stage.length);
    var newCard = card.firstElementChild;
    list.insertBefore(newCard, list.children[safeIndex] || null);
    if (item.kind === "text") {
      var editor = newCard.querySelector("[data-stage-text]");
      if (editor) editor.value = item.text || "";
    }
    updateStageDom();
    renderOutput();
    if (item.kind === "text") {
      var editor = document.querySelector('[data-stage-text="' + safeIndex + '"]');
      if (editor) editor.focus();
    }
    showToast(item.kind === "text" ? "已加入一块自由文本" : "已加入「" + item.title + "」");
  }
  function addFixedBlock(id) {
    insertLibraryBlock(id, state.stage.length);
  }
  function addTextBlock() {
    insertLibraryBlock("__free_text__", state.stage.length);
  }
  function moveStage(index, direction) {
    var target = index + direction;
    if (target < 0 || target >= state.stage.length) return;
    var item = state.stage.splice(index, 1)[0];
    state.stage.splice(target, 0, item);
    saveState();
    renderStage();
  }
  function removeStage(index) {
    state.stage.splice(index, 1);
    saveState();
    renderStage();
  }
  function parseTags(value) {
    return unique(String(value || "").split(/[,，、\s]+/));
  }
  function stopStageMotion() {
    state.dragPreviewFrames.forEach(function (frame) { window.cancelAnimationFrame(frame); });
    state.dragPreviewFrames = [];
    document.querySelectorAll(".stage-block").forEach(function (card) {
      card.style.removeProperty("transition");
      card.style.removeProperty("transform");
    });
  }
  function clearDropIndicators() {
    stopStageMotion();
    document.querySelectorAll(".drag-placeholder").forEach(function (marker) { marker.remove(); });
    state.dragPreviewIndex = null;
    document.querySelectorAll(".stage-block, .stage-empty").forEach(function (card) {
      card.classList.remove("drop-target", "drop-before", "drop-after");
    });
  }
  function clearDropHighlights() {
    document.querySelectorAll(".stage-block, .stage-empty").forEach(function (card) {
      card.classList.remove("drop-target", "drop-before", "drop-after");
    });
  }
  function captureStagePositions() {
    var positions = {};
    document.querySelectorAll(".stage-block").forEach(function (card) {
      var key = card.dataset.stageInstanceId || card.dataset.stageIndex;
      positions[key] = card.getBoundingClientRect().top;
    });
    return positions;
  }
  function animateStageReflow(previousPositions) {
    document.querySelectorAll(".stage-block").forEach(function (card) {
      var key = card.dataset.stageInstanceId || card.dataset.stageIndex;
      var previousTop = previousPositions[key];
      if (previousTop == null) return;
      var offset = previousTop - card.getBoundingClientRect().top;
      if (Math.abs(offset) < 1) return;
      card.style.transition = "none";
      card.style.transform = "translateY(" + offset + "px)";
      card.offsetHeight;
      var frame = window.requestAnimationFrame(function () {
        card.style.transition = "";
        card.style.transform = "";
        state.dragPreviewFrames = state.dragPreviewFrames.filter(function (item) { return item !== frame; });
      });
      state.dragPreviewFrames.push(frame);
    });
  }
  function setLibraryDropPreview(card, event) {
    var rect = card.getBoundingClientRect();
    var insertAfter = event.clientY > rect.top + rect.height / 2;
    var targetIndex = Number(card.dataset.stageIndex);
    var insertIndex = targetIndex + (insertAfter ? 1 : 0);
    if (state.dragPreviewIndex === insertIndex) return;
    stopStageMotion();
    var previousPositions = captureStagePositions();
    clearDropHighlights();
    document.querySelectorAll(".drag-placeholder").forEach(function (marker) { marker.remove(); });
    var marker = document.createElement("div");
    marker.className = "drag-placeholder";
    marker.setAttribute("aria-hidden", "true");
    marker.innerHTML = '<span class="drag-placeholder-mark">＋</span><span>放在这里</span>';
    var nextCard = document.querySelector('.stage-block[data-stage-index="' + insertIndex + '"]');
    if (nextCard) $("stageList").insertBefore(marker, nextCard);
    else $("stageList").appendChild(marker);
    state.dragPreviewIndex = insertIndex;
    card.classList.add("drop-target", insertAfter ? "drop-after" : "drop-before");
    animateStageReflow(previousPositions);
  }
  function findDraggedStageCard() {
    return document.querySelector('.stage-block[data-stage-index="' + state.draggedIndex + '"]');
  }
  function setStageDropPreview(card, event) {
    var draggedCard = findDraggedStageCard();
    if (!draggedCard || draggedCard === card) return;
    var cards = Array.from($("stageList").querySelectorAll(".stage-block"));
    var sourceIndex = state.draggedIndex;
    var targetIndex = cards.indexOf(card);
    if (sourceIndex < 0 || targetIndex < 0) return;
    var rect = card.getBoundingClientRect();
    var insertAfter = event.clientY > rect.top + rect.height / 2;
    var insertIndex = targetIndex + (insertAfter ? 1 : 0);
    if (sourceIndex < insertIndex) insertIndex -= 1;
    if (state.dragPreviewIndex === insertIndex) {
      clearDropHighlights();
      card.classList.add("drop-target", insertAfter ? "drop-after" : "drop-before");
      return;
    }
    stopStageMotion();
    var previousPositions = captureStagePositions();
    clearDropHighlights();
    card.classList.add("drop-target", insertAfter ? "drop-after" : "drop-before");
    state.dragPreviewIndex = insertIndex;
    document.querySelectorAll(".drag-placeholder").forEach(function (marker) { marker.remove(); });
    if (insertIndex !== sourceIndex) {
      var marker = document.createElement("div");
      marker.className = "drag-placeholder";
      marker.setAttribute("aria-hidden", "true");
      marker.innerHTML = '<span class="drag-placeholder-mark">＋</span><span>放在这里</span>';
      var domInsertIndex = insertIndex + (sourceIndex <= insertIndex ? 1 : 0);
      $("stageList").insertBefore(marker, cards[domInsertIndex] || null);
    }
    animateStageReflow(previousPositions);
  }
  function stageCardAtPointer(event, sourceCard) {
    var sourceRect = sourceCard.getBoundingClientRect();
    if (event.clientX >= sourceRect.left && event.clientX <= sourceRect.right && event.clientY >= sourceRect.top && event.clientY <= sourceRect.bottom) return sourceCard;
    var underPointer = typeof document.elementFromPoint === "function" ? document.elementFromPoint(event.clientX, event.clientY) : null;
    var card = underPointer && underPointer.closest ? underPointer.closest(".stage-block") : null;
    if (card && card !== sourceCard) return card;
    var cards = Array.from($("stageList").querySelectorAll(".stage-block")).filter(function (item) { return item !== sourceCard; });
    var next = cards.find(function (item) {
      var rect = item.getBoundingClientRect();
      return event.clientY < rect.top + rect.height / 2;
    });
    return next || cards[cards.length - 1] || null;
  }
  function stageInsertIndexAtPointer(x, y, sourceCard) {
    var sourceIndex = state.draggedIndex;
    if (sourceIndex == null || sourceIndex < 0) return sourceIndex;
    var targetCard = stageCardAtPointer({ clientX: x, clientY: y }, sourceCard);
    if (!targetCard || targetCard === sourceCard) return sourceIndex;
    var targetIndex = Number(targetCard.dataset.stageIndex);
    if (targetIndex < 0 || targetIndex >= state.stage.length) return sourceIndex;
    var rect = targetCard.getBoundingClientRect();
    var insertIndex = targetIndex + (y > rect.top + rect.height / 2 ? 1 : 0);
    if (sourceIndex < insertIndex) insertIndex -= 1;
    return Math.max(0, Math.min(insertIndex, state.stage.length - 1));
  }
  function finishPointerStageDrag(commit) {
    var drag = state.pointerDrag;
    if (!drag) return;
    var sourceIndex = state.draggedIndex;
    if (drag.card.hasPointerCapture && drag.card.hasPointerCapture(drag.pointerId)) drag.card.releasePointerCapture(drag.pointerId);
    if (typeof sourceIndex !== "number" || sourceIndex < 0 || sourceIndex >= state.stage.length || !drag.card.isConnected) {
      clearDropIndicators();
      state.pointerDrag = null;
      state.draggedIndex = null;
      state.dragPreviewIndex = null;
      return;
    }
    var insertIndex = state.dragPreviewIndex;
    if (insertIndex == null && commit) insertIndex = stageInsertIndexAtPointer(drag.x, drag.y, drag.card);
    if (insertIndex == null) insertIndex = sourceIndex;
    insertIndex = Math.max(0, Math.min(insertIndex, state.stage.length - 1));
    if (commit) {
      var item = state.stage.splice(sourceIndex, 1)[0];
      if (item) state.stage.splice(insertIndex, 0, item);
      clearDropIndicators();
      drag.card.classList.remove("dragging");
      drag.card.remove();
      $("stageList").insertBefore(drag.card, $("stageList").querySelectorAll(".stage-block")[insertIndex] || null);
      updateStageDom();
      saveState();
      renderOutput();
    } else {
      clearDropIndicators();
      drag.card.classList.remove("dragging");
    }
    state.pointerDrag = null;
    state.draggedIndex = null;
    state.dragPreviewIndex = null;
  }
  function openCustomModal() {
    $("customBlockModal").hidden = false;
    window.setTimeout(function () { $("customBlockName").focus(); }, 0);
  }
  function closeCustomModal() {
    $("customBlockModal").hidden = true;
    $("customBlockForm").reset();
  }
  function copyPrompt() {
    var output = getPromptText();
    if (!output) return showToast("组装台还没有可复制的文本", true);
    var copyPromise = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(output) : Promise.reject(new Error("clipboard unavailable"));
    copyPromise.then(function () {
      showToast("提示词已复制到剪贴板");
    }).catch(function () {
      var fallback = document.createElement("textarea");
      fallback.value = output;
      fallback.setAttribute("readonly", "true");
      fallback.style.position = "fixed";
      fallback.style.opacity = "0";
      document.body.appendChild(fallback);
      fallback.select();
      var copied = false;
      try { copied = document.execCommand("copy"); } catch (error) { copied = false; }
      fallback.remove();
      showToast(copied ? "提示词已复制到剪贴板" : "复制失败，请直接选择右侧文本", !copied);
    });
  }
  function downloadPrompt() {
    var output = getPromptText();
    if (!output) return showToast("组装台还没有可导出的文本", true);
    var blob = new Blob(["\ufeff" + output], { type: "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = "prompt-" + new Date().toISOString().slice(0, 10) + ".txt";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
    showToast("TXT 文件已开始下载");
  }
  function bindEvents() {
    updateThemeToggle();
    $("themeToggle").addEventListener("click", function () {
      var nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem("rh-workflow-theme", nextTheme); } catch (error) {}
      updateThemeToggle();
    });
    $("blockSearch").addEventListener("input", function () { state.search = this.value.trim(); renderLibrary(); });
    $("tagFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-filter-tag]");
      if (!button) return;
      state.filter = button.dataset.filterTag;
      renderFilters();
      renderLibrary();
    });
    $("libraryList").addEventListener("click", function (event) {
      var addButton = event.target.closest("[data-add-block]");
      if (addButton) return addFixedBlock(addButton.dataset.addBlock);
      if (event.target.closest("[data-add-text-block]")) return addTextBlock();
      var deleteButton = event.target.closest("[data-delete-block]");
      if (!deleteButton) return;
      if (!window.confirm("删除这块自定义积木吗？已经加入组装台的内容不会改变。")) return;
      state.customBlocks = state.customBlocks.filter(function (block) { return block.id !== deleteButton.dataset.deleteBlock; });
      saveState();
      renderAll();
      showToast("自定义积木已删除");
    });
    $("libraryList").addEventListener("dragstart", function (event) {
      var card = event.target.closest(".library-block");
      var freeCard = event.target.closest("[data-add-text-block]");
      if (!card && !freeCard) return;
      if (card && event.target.closest("button")) return event.preventDefault();
      state.draggedIndex = null;
      state.draggedLibraryId = card ? card.dataset.libraryBlockId : "__free_text__";
      state.dragPreviewIndex = null;
      clearDropIndicators();
      (card || freeCard).classList.add("dragging");
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("text/plain", "library:" + state.draggedLibraryId);
    });
    $("libraryList").addEventListener("dragend", function () {
      state.draggedLibraryId = "";
      clearDropIndicators();
      document.querySelectorAll(".library-block, .free-block-card").forEach(function (card) { card.classList.remove("dragging"); });
    });
    $("stageList").addEventListener("input", function (event) {
      var editor = event.target.closest("[data-stage-text]");
      if (!editor) return;
      var index = Number(editor.dataset.stageText);
      if (!state.stage[index]) return;
      state.stage[index].text = editor.value;
      saveState();
      renderOutput();
    });
    $("stageList").addEventListener("click", function (event) {
      var moveButton = event.target.closest("[data-move-stage]");
      if (moveButton) return moveStage(Number(moveButton.dataset.stageIndex), moveButton.dataset.moveStage === "up" ? -1 : 1);
      var removeButton = event.target.closest("[data-remove-stage]");
      if (removeButton) removeStage(Number(removeButton.dataset.removeStage));
    });
    $("stageList").addEventListener("pointerdown", function (event) {
      var card = event.target.closest(".stage-block");
      if (!card || event.target.closest("button, textarea, input, select, a")) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      state.draggedIndex = Number(card.dataset.stageIndex);
      state.draggedLibraryId = "";
      clearDropIndicators();
      state.pointerDrag = { card: card, pointerId: event.pointerId, x: event.clientX, y: event.clientY };
      card.classList.add("dragging");
      if (card.setPointerCapture) card.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    document.addEventListener("pointermove", function (event) {
      var drag = state.pointerDrag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      drag.x = event.clientX;
      drag.y = event.clientY;
      event.preventDefault();
      var card = stageCardAtPointer(event, drag.card);
      if (card && card !== drag.card) setStageDropPreview(card, event);
    });
    document.addEventListener("pointerup", function (event) {
      if (!state.pointerDrag || state.pointerDrag.pointerId !== event.pointerId) return;
      state.pointerDrag.x = event.clientX;
      state.pointerDrag.y = event.clientY;
      event.preventDefault();
      finishPointerStageDrag(true);
    });
    document.addEventListener("pointercancel", function (event) {
      if (!state.pointerDrag || state.pointerDrag.pointerId !== event.pointerId) return;
      finishPointerStageDrag(false);
    });
    $("stageList").addEventListener("dragover", function (event) {
      var card = event.target.closest("[data-stage-index]");
      var hasLibrarySource = Boolean(state.draggedLibraryId);
      var stageSurface = event.target === event.currentTarget;
      if (!hasLibrarySource) return;
      if (card) {
        event.preventDefault();
        setLibraryDropPreview(card, event);
        return;
      }
      if (stageSurface && state.dragPreviewIndex != null) {
        event.preventDefault();
        return;
      }
      var placeholder = event.target.closest(".drag-placeholder");
      if (hasLibrarySource && placeholder) {
        event.preventDefault();
        placeholder.classList.add("drop-target");
        return;
      }
      var emptyStage = event.target.closest(".stage-empty");
      if (hasLibrarySource && emptyStage) {
        event.preventDefault();
        clearDropIndicators();
        emptyStage.classList.add("drop-target");
      }
    });
    $("stageList").addEventListener("dragleave", function (event) {
      var card = event.target.closest("[data-stage-index]");
      if (card && !card.contains(event.relatedTarget)) card.classList.remove("drop-target");
      var emptyStage = event.target.closest(".stage-empty");
      if (emptyStage && !emptyStage.contains(event.relatedTarget)) emptyStage.classList.remove("drop-target");
    });
    $("stageList").addEventListener("drop", function (event) {
      var card = event.target.closest("[data-stage-index]");
      var placeholder = event.target.closest(".drag-placeholder");
      var emptyStage = event.target.closest(".stage-empty");
      var stageSurface = event.target === event.currentTarget;
      if (state.draggedLibraryId && (card || placeholder || emptyStage || (stageSurface && state.dragPreviewIndex != null))) {
        event.preventDefault();
        var libraryId = state.draggedLibraryId;
        var insertIndex = state.dragPreviewIndex == null ? state.stage.length : state.dragPreviewIndex;
        if (card && state.dragPreviewIndex == null) {
          var libraryDropRect = card.getBoundingClientRect();
          var libraryTargetIndex = Number(card.dataset.stageIndex);
          insertIndex = libraryTargetIndex + (event.clientY > libraryDropRect.top + libraryDropRect.height / 2 ? 1 : 0);
        }
        state.draggedLibraryId = "";
        clearDropIndicators();
        insertLibraryBlock(libraryId, insertIndex);
      }
    });
    $("clearStage").addEventListener("click", function () {
      if (!state.stage.length) return showToast("组装台已经是空的");
      if (!window.confirm("清空当前组装台吗？积木库和自定义积木不会受影响。")) return;
      state.stage = [];
      saveState();
      renderStage();
      showToast("组装台已清空");
    });
    $("copyPrompt").addEventListener("click", copyPrompt);
    $("downloadPrompt").addEventListener("click", downloadPrompt);
    $("addTextStage").addEventListener("click", addTextBlock);
    $("openCustomBlock").addEventListener("click", openCustomModal);
    $("closeCustomBlock").addEventListener("click", closeCustomModal);
    $("cancelCustomBlock").addEventListener("click", closeCustomModal);
    $("customBlockModal").addEventListener("click", function (event) { if (event.target === $("customBlockModal")) closeCustomModal(); });
    $("customBlockForm").addEventListener("submit", function (event) {
      event.preventDefault();
      var name = $("customBlockName").value.trim();
      var text = $("customBlockText").value.trim();
      if (!name || !text) return showToast("请填写积木名称和固定文本", true);
      state.customBlocks.push({ id: makeId("custom"), title: name, text: text, tags: parseTags($("customBlockTags").value) });
      saveState();
      closeCustomModal();
      state.filter = "全部";
      renderAll();
      showToast("自定义固定积木已保存");
    });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape") closeCustomModal(); });
  }

  loadState();
  bindEvents();
  renderAll();
})();
