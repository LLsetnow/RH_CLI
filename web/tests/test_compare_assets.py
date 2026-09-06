from pathlib import Path


STATIC_ROOT = Path(__file__).parents[1] / "static"


def test_outputs_page_links_to_the_content_comparison_subpage():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")
    assert 'href="/outputs/compare"' in page
    assert ".outputs-toolbar-actions .secondary-button" in styles

    compare_styles = (STATIC_ROOT / "compare.css").read_text(encoding="utf-8")
    assert ".compare-back-button" in compare_styles
    assert "align-items: center" in compare_styles


def test_compare_subpage_keeps_the_outputs_navigation_context():
    page = (STATIC_ROOT / "compare.html").read_text(encoding="utf-8")
    assert '<title>内容对比 · RH Workflow Desk</title>' in page
    assert 'class="top-nav-link active" href="/outputs"' in page
    assert 'id="compareStage"' in page
    assert 'id="compareFileInput"' in page
    assert 'data-choose-files' in page
    assert '选择本地文件' not in page
    assert 'data-compare-mode="split"' in page
    assert 'data-compare-mode="overlay"' in page
    assert 'id="toggleCompareFullscreen"' in page
    assert 'id="compareFullscreenClose"' in page
    assert "视频会先缩放到同一显示尺寸" in page


def test_compare_runtime_supports_local_files_output_assets_and_drop_zones():
    script = (STATIC_ROOT / "compare.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "compare.css").read_text(encoding="utf-8")
    assert 'application/x-rh-compare-asset' in script
    assert 'event.target.closest("[data-choose-files]")' in script
    assert 'URL.createObjectURL(file)' in script
    assert 'rh-workflow-compare-v1' in script
    assert 'indexedDB' in script
    assert 'restoreSavedSlots' in script
    assert 'window.addEventListener("pagehide", saveCompareState)' in script
    assert 'addEventListener("wheel", zoomAt' in script
    assert 'state.panX' in script and 'state.panY' in script and 'state.zoom' in script
    assert 'state.divider' in script
    assert 'stage.dataset.mode !== "overlay"' in script
    assert 'event.target.closest(".compare-divider")' in script
    assert 'handleVideoShortcut' in script
    assert 'ArrowRight' in script and 'ArrowLeft' in script
    assert '1 / 24' in script
    assert 'D 后退一帧 · F 前进一帧' in script
    assert 'video.play()' in script
    assert 'currentTime' in script
    assert 'video.videoWidth' in script and 'video.videoHeight' in script
    assert 'function syncVideoDisplaySize()' in script
    assert 'Math.max.apply(Math, dimensions.map' in script
    assert '--compare-video-display-width' in script and '--compare-video-display-height' in script
    assert 'loadedmetadata", syncVideoDisplaySize' in script
    assert 'function toggleCompareFullscreen()' in script
    assert 'compare-fullscreen-open' in script
    assert 'event.key === "Escape"' in script
    assert 'compare-divider-handle' not in script
    assert '.compare-divider' in styles
    assert 'pointer-events: auto' in styles
    assert '.compare-divider::before' in styles
    assert '.compare-divider-handle' not in styles
    assert ':root[data-theme="light"] .compare-mode-tabs' in styles
    assert ':root[data-theme="light"] .compare-mode.active' in styles
    assert '.compare-split-view' in styles
    assert '.compare-overlay-layer.is-first' in styles
    assert '.compare-transform > video.is-resolution-normalized' in styles
    assert 'width: var(--compare-video-display-width)' in styles
    assert 'height: var(--compare-video-display-height)' in styles
    assert '.compare-stage.is-fullscreen' in styles
    assert '.compare-fullscreen-close' in styles
    assert '.compare-stage.is-fullscreen .compare-overlay-label.is-second' in styles
    assert 'background: var(--surface-control)' in styles
    assert 'html.compare-fullscreen-open body' in styles or ':root.compare-fullscreen-open body' in styles


def test_output_cards_can_be_dragged_into_content_comparison():
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    assert 'data-compare-draggable="' in script
    assert 'draggable="' in script
    assert 'application/x-rh-compare-asset' in script


def test_compare_source_cards_are_limited_and_paged_at_20_per_page():
    page = (STATIC_ROOT / "compare.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "compare.js").read_text(encoding="utf-8")

    assert 'id="compareAssetPagination"' in page
    assert 'class="output-pagination compare-asset-pagination"' in page
    assert "var COMPARE_PAGE_SIZE = 20;" in script
    assert "function comparePageItems(items)" in script
    assert "slice(start, start + COMPARE_PAGE_SIZE)" in script
    assert "function renderComparePagination(totalItems)" in script
    assert 'data-compare-page="previous"' in script
    assert 'data-compare-page="next"' in script
    assert '$("compareAssetPagination").addEventListener("click"' in script
    assert "resetCompareAssetPage" in script
