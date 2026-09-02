from pathlib import Path


STATIC_ROOT = Path(__file__).parents[1] / "static"


def test_all_web_pages_load_shared_motion_runtime():
    for page_name in ("index.html", "prompt.html", "outputs.html"):
        page = (STATIC_ROOT / page_name).read_text(encoding="utf-8")
        assert '<script src="/static/motion.js"></script>' in page


def test_all_web_pages_share_the_same_brand_header_structure():
    for page_name in ("index.html", "prompt.html", "outputs.html"):
        page = (STATIC_ROOT / page_name).read_text(encoding="utf-8")
        assert 'class="brand-lockup brand-home-link"' in page
        assert 'class="brand-name">RH Workflow Desk</span>' in page
        assert 'class="brand-subtitle">本地工作流提交台</span>' in page


def test_slide_runtime_covers_page_navigation_and_dialogs():
    motion = (STATIC_ROOT / "motion.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    assert "PAGE_SLIDE_MS = 360" in motion
    assert "PAGE_DIRECTION_KEY" in motion
    assert "motion-page-enter-from-" in motion
    assert "@view-transition { navigation: auto; }" in css
    assert "::view-transition-old(root)" in css
    assert "::view-transition-new(root)" in css
    assert "view-transition-name: page-topbar" in css
    assert "::view-transition-group(page-topbar)" in css
    assert "::view-transition-old(page-topbar)" in css
    assert "::view-transition-new(page-topbar)" in css
    assert ".brand-name, .brand-subtitle { display: block; }" in css
    assert "pageDirectionFor" in motion
    assert "rememberPageDirection" in motion
    assert "prepareShuffleIn" not in motion
    assert "prepareShuffleOut" not in motion
    assert "SHUFFLE_MS" not in motion
    assert "root.classList.add(\"motion-page-leave\"" not in motion
    assert "window.RHMotion" in motion
    assert "prefers-reduced-motion" in motion


def test_primary_page_wires_motion_feedback():
    css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    app = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    assert ".task-card.task-arrival" in css
    assert ".primary-button.is-submitting" in css
    assert 'window.RHMotion.openModal("settingsModal", "outputDir")' in app
    assert 'jumpToProcessStep("queue")' in app


def test_sub_navigation_has_moving_active_indicators():
    prompt_css = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")
    prompt = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    output_css = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")
    output_js = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    output_page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    assert ".library-mode-tabs::before" in prompt_css
    assert ".assembly-view-tabs.is-groups::before" in prompt_css
    assert 'classList.toggle("is-actions", isActions)' in prompt
    assert 'classList.toggle("is-groups", isGroups)' in prompt
    assert ".output-filter-slider" in output_css
    assert "updateFilterSlider" in output_js
    assert 'class="output-filter-slider"' in output_page


def test_action_library_can_import_depth_into_task_load_image():
    prompt_html = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    prompt_css = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")
    prompt_js = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    app_js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'data-import-depth' in prompt_js
    assert "action-card-import" in prompt_js
    assert 'id="depthImportModal"' in prompt_html
    assert ".depth-import-dialog" in prompt_css
    assert "/depth-path" in prompt_js
    assert "target.bypassed" in prompt_js
    assert "is-disabled" in prompt_js
    assert '已保存到任务草稿' in prompt_js
    assert "canonicalWorkflowName" in app_js
    assert "modifiedWorkflowName" in app_js
    assert 'link.download = modifiedWorkflowName(sourceName)' in app_js
    assert 'window.localStorage.getItem("rh-workflow-desk-draft-v1")' in prompt_js
    assert "focusInputFromQuery" in app_js


def test_image_cards_can_import_original_image_into_task_workflow():
    prompt_js = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    prompt_css = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert "data-import-workflow" in prompt_js
    assert "openWorkflowImportFromTrigger" in prompt_js
    assert "/image-path" in prompt_js
    assert "import-workflow-button" in prompt_css


def test_prompt_builder_has_resizable_library_splitter_and_audio_toggle():
    prompt_html = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    prompt_js = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    prompt_css = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'id="promptGridSplitter"' in prompt_html
    assert "initPromptGridSplitter" in prompt_js
    assert "prompt-library-width" in prompt_js
    assert "data-audio-toggle" in prompt_js
    assert "toggleReferenceAudio" in prompt_js
    assert ".reference-audio-player { display: none; }" in prompt_css
    assert ".prompt-grid-splitter" in prompt_css


def test_prompt_library_cards_keep_readable_rows_and_default_width():
    prompt_css = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")
    prompt_js = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    index = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert "var(--prompt-library-width, 420px)" in prompt_css
    assert "align-content: start; grid-auto-rows: max-content" in prompt_css
    assert "min-height: 126px" in prompt_css
    assert "-webkit-line-clamp: 4" in prompt_css
    assert "prompt-library-width-v2" in prompt_js
    assert "savedWidth || 420" in prompt_js
    assert "~/Documents/VideoMake/ref/prompt/library.md" in index
