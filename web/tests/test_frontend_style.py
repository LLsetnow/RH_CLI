from pathlib import Path
import re


STATIC_ROOT = Path(__file__).parents[1] / "static"
PAGE_STYLES = ("prompt.css", "outputs.css", "workflows.css", "compare.css", "dashboard.css", "settings.css", "focus.css")
PAGE_MARKUP = ("index.html", "prompt.html", "outputs.html", "workflows.html", "compare.html", "dashboard.html", "settings.html")
MODAL_MARKUP = ("index.html", "prompt.html", "outputs.html", "workflows.html", "settings.html")


def test_task_controls_share_one_font_size_and_line_height_token():
    css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert "--control-font-size: 11px;" in css
    assert "--control-line-height: 1.25;" in css
    assert "input, select, textarea {" in css
    assert "font-size: var(--control-font-size);" in css
    assert "input, select { min-height:" in css
    assert "padding: 0 12px;" in css
    assert "line-height: var(--control-line-height);" in css


def test_workspace_collapses_from_its_available_width():
    css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert "container: workflow-shell / inline-size;" in css
    assert "@container workflow-shell (max-width: 960px)" in css
    assert "@container workflow-shell (max-width: 620px)" in css
    assert ".queue-column > .process-nav { position: static; margin: 0 0 14px; }" in css
    assert ".topbar { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; }" in css


def test_shared_button_and_dialog_tokens_are_defined_once():
    css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    for token in (
        "--control-height: 40px;",
        "--control-compact-height: 34px;",
        "--button-height: 40px;",
        "--button-compact-height: 34px;",
        "--dialog-padding: 25px;",
        "--dialog-radius: 20px;",
        "--motion-interaction: 180ms;",
        "--motion-modal: 220ms;",
    ):
        assert token in css

    assert ".dialog-panel {" in css
    assert ".button-compact {" in css


def test_page_styles_do_not_duplicate_shared_colors_or_motion_durations():
    raw_color = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)")
    raw_duration = re.compile(
        r"(?:transition|animation)(?:-[\w-]+)?\s*:[^;}]*(?:\d+ms|\d+(?:\.\d+)?s)"
    )

    for filename in PAGE_STYLES:
        css = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        assert not raw_color.search(css), filename
        assert not raw_duration.search(css), filename
        assert "0 30px 100px rgba" not in css, filename


def test_shared_color_values_live_only_in_the_global_token_layer():
    raw_color = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)")
    app_css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    for line in app_css.splitlines():
        if raw_color.search(line):
            assert line.lstrip().startswith("--"), line

    for token in (
        "--accent-ghost",
        "--warm-ghost",
        "--danger-ghost",
        "--reference-soft",
        "--surface-deep",
        "--shadow-medium",
        "--scrim",
        "--log-surface",
    ):
        assert token in app_css


def test_all_dialogs_use_the_shared_dialog_panel():
    for filename in MODAL_MARKUP:
        markup = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        dialog_tags = re.findall(r"<(?:section|div)\b[^>]*role=\"dialog\"[^>]*>", markup)
        assert dialog_tags, filename
        assert all("dialog-panel" in tag or "image-preview-backdrop" in tag for tag in dialog_tags), filename


def test_settings_entry_is_the_same_route_on_every_page():
    for filename in PAGE_MARKUP:
        markup = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        assert 'href="/settings"' in markup, filename


def test_page_loaders_show_progress_and_request_only_page_state():
    scoped_requests = {
        "index.html": ("app.js", "/api/state?scope=submit"),
        "workflows.html": ("workflows.js", "/api/state?scope=workflows"),
        "prompt.html": ("prompt.js", "/api/state?scope=prompt"),
        "outputs.html": ("outputs.js", "/api/state?scope=outputs"),
        "settings.html": ("settings.js", "/api/state?scope=settings"),
    }
    for page_name, (script_name, state_url) in scoped_requests.items():
        markup = (STATIC_ROOT / page_name).read_text(encoding="utf-8")
        script = (STATIC_ROOT / script_name).read_text(encoding="utf-8")
        assert "page-loading-state" in markup, page_name
        assert state_url in script, script_name

    motion_css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    assert "page-loading-shimmer" in motion_css
    assert "translate3d(24px, 0, 0)" in motion_css
    assert "translate3d(-24px, 0, 0)" in motion_css


def test_settings_exposes_shortcut_and_color_reference():
    markup = (STATIC_ROOT / "settings.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "settings.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "settings.css").read_text(encoding="utf-8")

    assert 'data-settings-section="reference"' in markup
    assert 'data-settings-panel="reference"' in markup
    assert 'id="shortcutReference"' in markup
    assert 'id="colorReference"' in markup
    assert "var shortcutGroups" in script
    assert "var colorGroups" in script
    assert 'var allowed = ["ai", "platform", "plugin", "extension", "reference", "logs"];' in script
    assert "renderInteractionReference();" in script
    assert ".settings-shortcut-row { display: grid;" in styles
    assert ".settings-color-row { display: grid;" in styles
    assert len(re.findall(r'(?:colorToken|derivedColor)\("(--[\w-]+)"', script)) == 55
    for token in ("--accent-ghost", "--warm-ghost", "--surface-modal", "--prompt-accent-soft"):
        assert token not in script
    for token in ("--accent", "--warm", "--danger", "--reference-accent", "--type-accent", "--rating-yellow"):
        assert token in script


def test_ui_removes_horizontal_section_dividers():
    index_markup = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    app_css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    dashboard_css = (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8")
    prompt_css = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")
    workflows_css = (STATIC_ROOT / "workflows.css").read_text(encoding="utf-8")
    settings_css = (STATIC_ROOT / "settings.css").read_text(encoding="utf-8")
    focus_css = (STATIC_ROOT / "focus.css").read_text(encoding="utf-8")

    assert 'class="divider' not in index_markup
    assert ".divider {" not in app_css
    for css, selectors in (
        (app_css, (".submit-strip", ".error-detail")),
        (dashboard_css, (".dashboard-coin-balance", ".dashboard-account-balance", ".dashboard-recent-item")),
        (prompt_css, (".category-filters", ".output-foot", ".resource-media-fields")),
        (workflows_css, (".workflow-editor-json",)),
        (settings_css, (".settings-toggle-row", ".settings-shortcut-row", ".settings-color-row")),
        (focus_css, (".focus-header",)),
    ):
        for selector in selectors:
            rule = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
            assert rule, selector
            assert "border-top:" not in rule.group(1)
            assert "border-bottom:" not in rule.group(1)

    assert ".note-line { display: none; }" in prompt_css
    assert ".empty-line { width: 44px; height: 0;" in app_css


def test_settings_switches_share_one_capsule_control():
    markup = (STATIC_ROOT / "settings.html").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "settings.css").read_text(encoding="utf-8")
    assert len(re.findall(r'class="settings-switch(?:\s|\")', markup)) == 4
    assert 'class="settings-switch-track"' in markup
    assert ".settings-switch input:checked + .settings-switch-track" in styles
    assert ".settings-switch input:focus-visible + .settings-switch-track" in styles


def test_telegram_switches_put_the_label_before_the_pill_control():
    app_css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    settings_css = (STATIC_ROOT / "settings.css").read_text(encoding="utf-8")

    assert ".telegram-enabled-toggle > span:last-child { order: -1; }" in app_css
    assert ".settings-toggle-row .settings-switch-label { width: 102px; order: -1; white-space: nowrap; }" in settings_css
    assert ".settings-toggle-row .settings-switch { width: 152px; }" in settings_css


def test_telegram_push_chat_id_fields_describe_comma_separated_targets():
    settings_markup = (STATIC_ROOT / "settings.html").read_text(encoding="utf-8")
    submit_markup = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    for markup in (settings_markup, submit_markup):
        assert "Chat ID（多个用逗号分隔）" in markup
        assert "例如 -1001234567890, -1009876543210" in markup


def test_telegram_inbound_settings_offer_fixed_and_folder_random_modes():
    markup = (STATIC_ROOT / "settings.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "settings.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "settings.css").read_text(encoding="utf-8")

    assert 'id="telegramInboundMode"' in markup
    assert 'value="fixed">固定工作流' in markup
    assert 'value="folder_random">文件夹随机' in markup
    assert 'id="telegramInboundWorkflow"' in markup
    assert 'id="telegramInboundFolder"' in markup
    assert "telegram_inbound_mode" in script
    assert "telegram_inbound_folder_id" in script
    assert "state.inboundWorkflows" in script
    assert ".telegram-inbound-config { display: grid;" in styles
    assert 'class="settings-toggle-row telegram-inbound-toggle-row"' in markup
    assert 'class="settings-switch-label">启用图片入站</span>' in markup
    assert 'class="telegram-inbound-panel is-disabled"' in markup
    assert 'classList.toggle("is-disabled", !inboundEnabled)' in script
    assert 'id="telegramVideoInboundEnabled"' in markup
    assert 'id="telegramVideoInboundWorkflow"' in markup
    assert "telegram_video_inbound_enabled" in script
    assert "telegram_video_inbound_workflow_id" in script
    assert "state.videoInboundWorkflows" in script


def test_prompt_resource_editor_supports_vision_fill_and_new_categories():
    markup = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    settings_markup = (STATIC_ROOT / "settings.html").read_text(encoding="utf-8")
    settings_script = (STATIC_ROOT / "settings.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'id="customBlockCategorySelect"' in markup
    assert 'placeholder="或输入新分类"' in markup
    assert 'id="resourceVisionButton"' in markup
    assert 'id="generateSubjectDefinitions"' in markup
    assert 'id="aliyunVisionApiKey"' in settings_markup
    assert "aliyun_vision_api_key" in settings_script
    assert "renderResourceCategoryOptions" in script
    assert 'jsonRequest("/api/prompt/vision"' in script
    assert "subjectDefinitionMedia" in script
    assert "subjectDefinitionLine" in script
    assert "translationDisabled: true" in script
    assert "resolveTranslationTemplate(item, translationTemplate(item))" in script
    assert "function stageTextEditorSegments(item, index)" in script
    assert 'document.querySelector(\'[data-stage-text="\' + index + \'\"]\')' in script
    assert 'if (item.kind === "text") stageTextEditorSegments(item, index).forEach' in script
    assert "character's pose" in script
    assert "target video's first frame" in script
    assert "defined by <Picture " in script
    assert 'jsonRequest("/api/prompt/subject-definitions"' not in script
    assert "generatedType: \"subject_definitions\"" in script
    assert "AI 识图填充" in script
    assert ".resource-ai-button" in styles


def test_prompt_library_image_previews_use_a_shared_fixed_size_well():
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert ".action-card-media { align-self: center; width: 124px; height: 164px; min-height: 164px;" in styles
    assert ".reference-card-media { align-self: center; width: 124px; height: 164px; min-height: 164px;" in styles
    assert ".action-library-card {\n  grid-column: 1 / -1;\n  min-height: 164px;" in styles
    assert ".action-card-media {\n  height: 164px;\n  min-height: 164px;" in styles
    assert ".action-media-shell { position: relative; width: 100%; height: 164px; min-height: 164px;" in styles
    assert ".reference-media-image { width: 100%; height: 164px; min-height: 164px;" in styles
    assert ".action-media-image img { object-fit: contain; }" in styles
    assert ".reference-media-image img { display: block; width: 100%; height: 100%; border-radius: 10px; background: transparent; object-fit: contain; object-position: center; }" in styles
    assert ".library-list > .action-library-card .action-card-media," in styles
    assert "width: 100%;" in styles


def test_prompt_library_resource_cards_keep_controls_below_title_and_prompt_area_draggable():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert "编辑标签" not in script
    assert "libraryPromptMarkup(action.text" in script
    assert "libraryPromptMarkup(reference.text" in script
    assert "<div class=\"library-card-meta-row\"><span class=\"action-card-top-actions\">" in script
    assert ".library-card-meta-row { display: flex; align-items: center;" in styles
    assert ".action-card-body > .action-library-text, .reference-card-body > .reference-library-text { cursor: grab; }" in styles
    assert ".resource-tags-edit.is-empty { display: none; }" in styles
    assert ".action-card-media { align-self: center;" in styles
    assert ".reference-card-media { align-self: center;" in styles
    assert "object-position: center;" in styles
    assert ".prompt-builder-grid:not(.is-library-expanded) .action-card-media," in styles
    assert ".prompt-builder-grid:not(.is-library-expanded) .reference-card-image { background: transparent; }" in styles
    assert ".prompt-builder-grid:not(.is-library-expanded) .action-media-image img," in styles
    assert ".prompt-builder-grid:not(.is-library-expanded) .reference-card-image img { border-radius: 10px; }" in styles
    expected_first_frame = 'line: "<Subject " + counters.image + "> is the first frame of [Shot 1] defined by <Picture " + counters.image + ">, the opening still of the target video; the exact environment, background, lighting, camera composition, and the woman\'s appearance and pose all begin from this image and must be preserved without change at 0.00 seconds.",'
    assert expected_first_frame in script


def test_prompt_library_cards_support_numeric_json_ratings():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")
    server = (STATIC_ROOT.parent / "server.py").read_text(encoding="utf-8")

    assert "function libraryRatingFromTags" in script
    assert "function setLibraryCardRating" in script
    assert 'document.querySelector(".library-block:hover, .action-library-card:hover, .reference-library-card:hover")' in script
    assert "setLibraryCardRating(ratingCard, event.key)" in script
    assert '"/rating"' in script
    assert "library-rating-tag" in script
    assert ".library-rating-tag" in styles
    assert 'path.startswith("/api/prompt/library/") and path.endswith("/rating")' in server
    assert "update_block_rating" in server
    assert "update_action_rating" in server
    assert "update_reference_rating" in server


def test_prompt_library_keeps_mode_and_filters_visible_while_scrolling_and_across_visits():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'var LIBRARY_VIEW_STORAGE_KEY = "rh-workflow-desk-prompt-library-view-v1";' in script
    assert "function restoreLibraryViewState" in script
    assert "function persistLibraryViewState" in script
    assert "window.localStorage.getItem(LIBRARY_VIEW_STORAGE_KEY)" in script
    assert "window.localStorage.setItem(LIBRARY_VIEW_STORAGE_KEY" in script
    assert "restoreLibraryViewState();" in script
    assert "persistLibraryViewState();" in script
    assert ".library-mode-tabs-sticky { position: sticky;" in styles
    assert "function syncLibraryModeSticky" in script
    assert 'sticky.classList.toggle("is-stuck"' in script
    assert ".library-mode-tabs-sticky.is-stuck::before" in styles
    assert "top: -22px;" in styles
    assert "top: 0;" in styles
    assert "z-index: 5;" in styles


def test_public_navigation_has_one_order_and_structure():
    expected_order = ["/workflows", "/prompt", "/", "/outputs", "/dashboard", "/settings"]

    for filename in PAGE_MARKUP:
        markup = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        links = re.findall(r'<a class="top-nav-link(?: active)?" href="([^"]+)"', markup)
        assert links == expected_order, filename
        assert len(links) == len(expected_order), filename
        assert markup.count('class="top-nav"') == 1, filename


def test_global_page_navigation_shortcuts_are_wired_across_electron_and_pages():
    main = (STATIC_ROOT.parent / "electron" / "main.cjs").read_text(encoding="utf-8")
    preload = (STATIC_ROOT.parent / "electron" / "preload.cjs").read_text(encoding="utf-8")
    motion = (STATIC_ROOT / "motion.js").read_text(encoding="utf-8")

    assert "globalShortcut" in main
    assert '"Alt+Left"' in main
    assert '"Alt+Right"' in main
    assert '"rh-global-page-navigation"' in main
    assert "onGlobalPageNavigation" in preload
    assert "event.altKey" in motion
    assert 'event.key !== "ArrowLeft"' in motion
    assert 'event.key !== "ArrowRight"' in motion
    assert "topLevelNavigationLinks" in motion
    assert "target.click()" in motion
    for filename in PAGE_MARKUP:
        assert '/static/motion.js' in (STATIC_ROOT / filename).read_text(encoding="utf-8"), filename


def test_focus_mode_tiles_six_pages_and_converts_shift_wheel_to_horizontal_scroll():
    page = (STATIC_ROOT / "focus.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "focus.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "focus.css").read_text(encoding="utf-8")
    shared_motion = (STATIC_ROOT / "motion.js").read_text(encoding="utf-8")
    shared_styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    server = (STATIC_ROOT.parent / "server.py").read_text(encoding="utf-8")
    expected_focus_order = ["workflows", "prompt", "submit", "outputs", "dashboard", "settings"]
    assert re.findall(r'data-focus-page="([^"]+)"', page) == expected_focus_order * 2
    assert 'var focusPageOrder = ["workflows", "prompt", "submit", "outputs", "dashboard", "settings"];' in script
    assert "focusPageOrder.indexOf(link.dataset.focusPage)" in script
    assert "var orderedPages = focusPageOrder.map" in script
    assert "return orderedPages.reduce" in script

    assert page.count('class="focus-panel"') == 6
    assert page.count('class="focus-panel-divider"') == 5
    assert page.count('class="focus-page-slot"') == 6
    assert "<iframe" not in page
    assert 'href="/focus"' not in page
    for filename in ("app.css", "prompt.css", "outputs.css", "dashboard.css", "workflows.css", "settings.css"):
        assert '/static/' + filename in page
    assert 'id="focusNavigation"' in page
    assert 'class="top-nav-link focus-nav-link active"' in page
    assert 'aria-pressed="true"' in page
    assert 'class="focus-nav-icon" aria-hidden="true">●</span>' in page
    assert '<kbd>Alt</kbd> + <kbd>←/→</kbd> 聚焦' in page
    assert '<kbd>Ctrl</kbd> + <kbd>M</kbd> 居中' in page
    assert "导航按钮显示/隐藏页面" in page
    assert "event.shiftKey" in script
    assert "horizontalWheelTarget" in script
    assert "stage.scrollLeft = current + distance" in script
    assert "event.deltaMode" in script
    assert "adjustDivider" in script
    assert "pointerdown" in script
    assert "ArrowLeft" in script
    assert "loadFragments" in script
    assert 'fetch("/api/focus/fragments")' in script
    assert "loadScript(page.script)" in script
    assert "passive: false" in script
    assert "focusedPanelIndex" in script
    assert "setFocusedPanel(index)" in script
    assert "handleFocusedPageNavigation" in script
    assert "function focusDirectionForKey(key)" in script
    assert "function scrollOutputsPanel(direction, target)" in script
    assert 'panel.dataset.focusPage !== "outputs"' in script
    assert 'target.closest("[data-focus-panel]")' in script
    assert 'panel.scrollTo({ top: nextTop' in script
    assert "function togglePanelVisibility(index)" in script
    assert "function visiblePanelIndices()" in script
    assert 'classList.toggle("is-hidden", willHide)' in script
    assert 'link.classList.toggle("is-hidden", !visible)' in script
    assert 'icon.textContent = visible ? "●" : "○"' in script
    assert "function visibleDividerPanels(index)" in script
    assert "for (var nextIndex = index + 1; nextIndex < panels.length; nextIndex += 1)" in script
    assert "var pair = visibleDividerPanels(index)" in script
    assert "var nextPanel = panels[pair.nextIndex]" in script
    assert 'key === "ArrowUp"' in script
    assert 'key === "ArrowDown"' in script
    assert 'String(event.key || "").toLowerCase() === "m"' in script
    assert "setCenteredMode(!centeredMode)" in script
    assert "function cancelHorizontalWheel()" in script
    assert 'stage.scrollTo({ left: 0, behavior: "auto" });' in script
    assert 'focusPanel(focusedPanelIndex, { behavior: "auto" })' in script
    assert "onGlobalPageNavigation(handleFocusedPageNavigation)" in script
    assert "overflow-x: auto" in styles
    assert "overflow-y: auto" in styles
    assert "gap: 0" in styles
    assert "flex: 0 0 var(--focus-panel-width, clamp(540px, calc(50vw - 28px), 820px))" in styles
    assert ".focus-panel.is-focused::before" in styles
    assert "background: var(--type-accent)" in styles
    assert ".focus-stage.is-centered" in styles
    assert ".focus-stage.is-centered > .focus-panel:not(.is-focused)" in styles
    assert ".focus-stage.is-centered > .focus-panel.is-focused { width: min(1440px, 100%); min-width: min(1440px, 100%); flex-basis: min(1440px, 100%); }" in styles
    assert ".focus-panel.is-hidden, .focus-panel-divider.is-hidden { display: none; }" in styles
    assert ".focus-nav-icon { color: var(--accent);" in styles
    assert ".focus-navigation .focus-nav-link.is-hidden { color: var(--subtle);" in styles
    assert ".focus-page-slot .intro-block," in styles
    assert ".focus-page-slot .settings-hero { display: none; }" in styles
    assert ".focus-page-slot .settings-main { margin-top: 0; }" in styles
    assert "scroll-snap-type" not in styles
    assert "postMessage" not in shared_motion
    assert "is-focus-embedded" not in shared_motion
    assert "is-focus-embedded" not in shared_styles
    assert 'path == "/api/focus/fragments"' in server
    assert 'relative in {"focus", "focus/"}' in server
    for filename in PAGE_MARKUP:
        assert 'href="/focus"' in (STATIC_ROOT / filename).read_text(encoding="utf-8"), filename


def test_focus_mode_keeps_all_route_navigation_inert():
    page = (STATIC_ROOT / "focus.html").read_text(encoding="utf-8")
    focus_script = (STATIC_ROOT / "focus.js").read_text(encoding="utf-8")
    focus_styles = (STATIC_ROOT / "focus.css").read_text(encoding="utf-8")
    motion = (STATIC_ROOT / "motion.js").read_text(encoding="utf-8")
    settings = (STATIC_ROOT / "settings.js").read_text(encoding="utf-8")
    app = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'href="/"' not in page
    assert 'id="focusExit"' in page
    assert 'aria-label="退出专注模式"' in page
    assert 'window.location.href = "/"' in focus_script
    assert ".focus-exit { flex: 0 0 auto; white-space: nowrap; }" in focus_styles
    assert '.focus-panel[data-focus-page="submit"] .workspace' in focus_styles
    assert 'grid-template-columns: minmax(0, 1.55fr) minmax(340px, .9fr);' in focus_styles
    assert "pageNavigationBlocked = true" in focus_script
    assert 'exitToTaskSubmit = function ()' in focus_script
    assert 'window.RHFocus.exitToTaskSubmit();' in focus_script
    assert "pageNavigationPaths" in focus_script
    assert 'event.stopPropagation();' in focus_script
    assert "focusPanel(2)" not in focus_script
    assert 'if (document.body.classList.contains("focus-body")) return false;' in motion
    assert 'if (document.body.classList.contains("focus-body")) return [];' in motion
    assert 'if (document.body.classList.contains("focus-body")) {' in motion
    assert 'if (document.body.classList.contains("focus-body")) return;' in motion
    assert 'if (document.body.classList.contains("focus-body")) return;' in settings
    assert 'if (document.body.classList.contains("focus-body")) return;' in app


def test_all_page_scrollbars_stay_hidden_while_content_remains_scrollable():
    app_styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    focus_styles = (STATIC_ROOT / "focus.css").read_text(encoding="utf-8")
    settings_styles = (STATIC_ROOT / "settings.css").read_text(encoding="utf-8")

    assert "scrollbar-width: none" in app_styles
    assert "::-webkit-scrollbar { display: none; width: 0; height: 0; }" in app_styles
    assert "scrollbar-width: thin" not in focus_styles
    assert ".focus-panel::-webkit-scrollbar { display: none; width: 0; height: 0; }" in focus_styles
    assert "scrollbar-width: thin" not in settings_styles
    assert ".logs-viewport::-webkit-scrollbar { display: none; width: 0; height: 0; }" in settings_styles


def test_workflow_cards_overwrite_task_submit_workflow():
    script = (STATIC_ROOT / "workflows.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "workflows.css").read_text(encoding="utf-8")

    assert 'data-action="select-workflow"' in script
    assert "workflowDraftFromDetail" in script
    assert 'rh-workflow-desk-draft-v1' in script
    assert "localStorage.setItem(draftStorageKey" in script
    load_workflow = script[script.index("function loadWorkflowIntoSubmit"):script.index("var configKinds")]
    assert "function promptGroupSnapshot(group)" in script
    assert 'jsonRequest("/api/prompt/state", "PUT", { items: group.items })' in script
    assert "queuePromptGroupSnapshot(data.prompt_group)" in load_workflow
    assert "notifySubmitImport({ kind: \"workflow\", draft: draft, promptGroup: promptGroup, hasPromptGroup: hasPromptGroup })" in load_workflow
    assert "任务提交面板已同步" in load_workflow
    assert 'window.location.href = "/"' in load_workflow
    assert "加载到任务提交页和提示词工坊" in script
    assert ".workflow-card-title-button { display: block; flex: 0 1 auto;" in styles
    assert "max-width: calc(100% - 12px);" in styles


def test_workflow_library_uses_folder_hierarchy_and_drag_targets():
    markup = (STATIC_ROOT / "workflows.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "workflows.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "workflows.css").read_text(encoding="utf-8")
    assert 'id="workflowFolderName"' not in markup
    assert 'id="createWorkflowFolder"' in script
    assert 'id="workflowFolderContextMenu"' in markup
    assert 'data-folder-menu-action="rename"' in markup
    assert 'data-folder-menu-action="set-telegram-inbound"' in markup
    assert 'data-folder-menu-action="delete"' in markup
    assert 'id="workflowCardContextMenu"' in markup
    assert 'data-workflow-menu-action="configure-workflow"' in markup
    assert 'data-workflow-menu-action="set-telegram-inbound"' in markup
    assert 'data-workflow-menu-action="export-workflow"' in markup
    assert 'data-workflow-menu-action="delete-workflow"' in markup
    assert '<div class="workflow-import-row">' in markup
    assert ".workflow-import-row { display: flex;" in styles
    assert 'request("/api/workflow-folders")' in script
    assert 'jsonRequest("/api/workflow-folders", "POST"' in script
    assert 'jsonRequest("/api/workflow-folders/" + encodeURIComponent(folderId), "PATCH"' in script
    assert 'request("/api/workflow-folders/" + encodeURIComponent(folderId), { method: "DELETE" })' in script
    assert 'function setTelegramInboundFolder(folderId, enabled, trigger)' in script
    assert 'telegram_inbound_mode: "folder_random"' in script
    assert "handleWorkflowContextMenu" in script
    assert "openWorkflowContextMenu" in script
    assert "handleWorkflowMenuAction" in script
    assert "focusFolderNameInput" in script
    assert "workflow-card-action" not in script
    assert "workflow-card-actions" not in styles
    assert 'data-action="open-folder"' in script
    assert 'data-folder-drop-id="' in script
    assert "handleWorkflowDragStart" in script
    assert "moveWorkflowToFolder" in script
    assert ".workflow-folder-grid { display: grid;" in styles
    assert ".workflow-folder-context-menu, .workflow-card-context-menu { position: fixed;" in styles
    assert "function hasExternalWorkflowFileDrag(event)" in script
    assert "function handleWorkflowLibraryDrop(event)" in script
    assert 'importWorkflowFile(file);' in script
    assert 'classList.add("is-external-file-drop-target")' in script
    assert ".workflow-groups.is-external-file-drop-target {" in styles
    assert "松开鼠标导入 API JSON 工作流" in styles
    assert "可将 API JSON 直接拖到本区域添加" in script
    assert ".workflow-card { display: flex;" in styles
    assert ".workflow-card.is-selected { border-color: var(--type-accent);" in styles
    assert ".workflow-card-body.is-selected" not in styles
    assert "box-shadow: 0 0 0 1px var(--type-strong);" in styles
    assert '(selected ? " is-selected" : "")' in script
    assert "workflowSearch" not in markup
    assert "workflowAccountFilter" not in markup


def test_queue_running_cards_are_highlighted_and_main_area_loads_task():
    app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    app_styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    app_markup = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    assert 'function isActiveTask(task)' in app_script
    assert 'function activeTaskCount(tasks)' in app_script
    assert "var TASK_PAGE_SIZE = 20;" in app_script
    assert 'id="queuePagination"' in app_markup
    assert "function renderTaskPagination(totalItems)" in app_script
    assert "slice(start, start + TASK_PAGE_SIZE)" in app_script
    assert "function handleQueuePagination(event)" in app_script
    assert 'queuePagination").addEventListener("click", handleQueuePagination)' in app_script
    assert ".queue-pagination" in app_styles
    assert ".queue-page-button, .queue-page-number" in app_styles
    assert '$("queueCount").textContent = activeTaskCount(tasks);' in app_script
    assert 'class="task-card-main" data-action="load-task"' in app_script
    assert 'class="task-load-button"' not in app_script
    assert 'var trigger = event.target.closest("[data-action]");' in app_script
    assert 'queueList").addEventListener("keydown", handleQueueKeydown)' in app_script
    assert ".task-card.running {" in app_styles
    assert "background: var(--warm-fill)" in app_styles
    assert ":root[data-theme=\"light\"] .task-card.running" in app_styles
    assert ":root[data-theme=\"light\"] .task-card.running {\n  border-color: var(--warm-opaque);\n  background: var(--warm-soft);" in app_styles
    assert ".task-card-main:hover" not in app_styles


def test_submit_page_json_badge_opens_an_editor_with_apply_and_restore_actions():
    markup = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert 'id="openWorkflowJsonButton"' in markup
    assert 'id="workflowJsonModal"' in markup
    assert 'id="workflowJsonEditor"' in markup
    assert 'id="applyWorkflowJson"' in markup
    assert 'id="restoreWorkflowJsonButton"' in markup
    assert '$("openWorkflowJsonButton").addEventListener("click", openWorkflowJson);' in script
    assert 'jsonRequest("/api/workflows/analyze", "POST"' in script
    assert ".workflow-json-button:hover" in styles
    assert ".workflow-json-editor:focus" in styles


def test_workflow_json_actions_are_horizontal_and_equal_width():
    markup = (STATIC_ROOT / "workflows.html").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "workflows.css").read_text(encoding="utf-8")

    assert 'id="restoreWorkflowJson"' in markup
    assert 'id="saveWorkflowJson"' in markup
    assert ".workflow-editor-json-heading > div:first-child" in styles
    assert ".workflow-editor-json-actions { display: flex; flex-direction: row;" in styles
    assert ".workflow-editor-json-actions > button { width: 68px; min-width: 68px;" in styles


def test_workflow_input_defaults_are_limited_to_manual_items():
    markup = (STATIC_ROOT / "workflows.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "workflows.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "workflows.css").read_text(encoding="utf-8")
    assert "自动识别模式只读取工作流原始输入" in markup
    assert 'data-config-action="default"' in script
    assert 'input_defaults: editor.mode === "manual"' in script
    assert '<span class="field-label">默认值</span>' in script
    assert "workflow-config-name-control" in script
    assert "workflow-config-required-track" in script
    assert ".workflow-config-name-control > input" in styles
    assert ".workflow-config-item-grid { display: grid; grid-template-columns:" in styles


def test_workflow_library_keeps_prompt_group_with_workflow_and_loads_both_drafts():
    markup = (STATIC_ROOT / "workflows.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "workflows.js").read_text(encoding="utf-8")
    server = (STATIC_ROOT.parent / "server.py").read_text(encoding="utf-8")
    app_source = (STATIC_ROOT.parent / "app.py").read_text(encoding="utf-8")

    assert 'id="workflowRecordPromptGroup"' in markup
    assert "关联提示词组" in markup
    assert 'request("/api/prompt/groups")' in script
    assert 'prompt_group_id: $("workflowRecordPromptGroup").value' in script
    assert 'pendingPromptGroupStorageKey = "rh-workflow-desk-pending-prompt-group-v1"' in script
    assert "queuePromptGroupSnapshot(data.prompt_group)" in script
    assert 'window.location.href = "/"' in script
    assert 'path == "/api/prompt/groups"' in server
    assert "prompt_group=prompt_group" in server
    assert "include_current_prompt_group" in server
    assert "task_group_snapshot()" in server
    assert "WORKFLOW_PROMPT_GROUP_SUFFIX" in app_source
    assert 'window.addEventListener("rh-workflow-library-refresh", refreshWorkflowLibrary)' in script


def test_prompt_group_library_lives_on_workflow_page_and_loads_into_prompt_workbench():
    workflow_markup = (STATIC_ROOT / "workflows.html").read_text(encoding="utf-8")
    workflow_script = (STATIC_ROOT / "workflows.js").read_text(encoding="utf-8")
    workflow_styles = (STATIC_ROOT / "workflows.css").read_text(encoding="utf-8")
    prompt_markup = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    prompt_script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    server = (STATIC_ROOT.parent / "server.py").read_text(encoding="utf-8")

    assert 'id="promptGroupGroups"' in workflow_markup
    assert 'id="promptGroupFolderContextMenu"' in workflow_markup
    assert 'data-prompt-group-menu-action="delete"' in workflow_markup
    assert "function renderPromptGroups()" in workflow_script
    assert "function loadPromptGroupIntoWorkbench(groupId)" in workflow_script
    assert 'window.location.href = "/prompt?group_id="' in workflow_script
    group_loader_start = workflow_script.index("function loadPromptGroupIntoWorkbench")
    group_loader_end = workflow_script.index("function deletePromptGroup(groupId", group_loader_start)
    group_loader = workflow_script[group_loader_start:group_loader_end]
    assert "window.RHFocus.isFocusMode" in group_loader
    assert "exitToTaskSubmit" in group_loader
    assert group_loader.index("exitToTaskSubmit") < group_loader.index('window.location.href = "/prompt?group_id="')
    assert 'jsonRequest("/api/prompt/group-folders", "POST"' in workflow_script
    assert 'request("/api/prompt/groups/" + encodeURIComponent(groupId), { method: "DELETE" })' in workflow_script
    assert "data-prompt-group-folder-drop-id" in workflow_script
    assert 'var collectionClass = dropId === "" ? " workflow-unclassified-collection" : "";' in workflow_script
    assert ".workflow-unclassified-collection { padding: 0; border-color: transparent;" in workflow_styles
    assert ':root[data-theme="light"] .workflow-unclassified-collection {' in workflow_styles
    assert ".prompt-group-library { display: grid;" in workflow_styles
    assert 'id="groupName"' in prompt_markup
    assert 'placeholder="输入新建提示词组名称"' in prompt_markup
    assert 'id="saveGroup"' in prompt_markup
    assert 'id="groupList"' not in prompt_markup
    assert "function loadPromptGroupFromQuery()" in prompt_script
    assert "loadPromptGroupFromQuery();" in prompt_script
    assert 'path == "/api/prompt/group-folders"' in server
    assert "prompt_group_folders()" in server


def test_workflow_light_theme_uses_white_panel_surfaces():
    styles = (STATIC_ROOT / "workflows.css").read_text(encoding="utf-8")

    assert ':root[data-theme="light"] .workflow-hero-stat,' in styles
    assert ':root[data-theme="light"] .workflow-folder-collection,' in styles
    assert ':root[data-theme="light"] .workflow-editor-note {' in styles
    assert "background: var(--light-strong);" in styles
    assert ':root[data-theme="light"] .workflow-card:hover,' in styles
    assert "background: var(--surface-control);" in styles


def test_telegram_submission_passes_workflow_prompt_group_to_task_snapshot():
    app_source = (STATIC_ROOT.parent / "app.py").read_text(encoding="utf-8")

    assert 'prompt_group = detail.get("prompt_group")' in app_source
    assert '"id": f"telegram-{workflow_id}"' in app_source


def test_task_submit_exposes_the_workflow_input_configuration_editor():
    markup = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    server = (STATIC_ROOT.parent / "server.py").read_text(encoding="utf-8")
    app_source = (STATIC_ROOT.parent / "app.py").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/static/workflows.css" />' in markup
    assert 'id="configureWorkflowLibraryButton"' in markup
    assert "configureWorkflowLibraryButton" in script
    assert markup.index('id="configureWorkflowLibraryButton"') < markup.index('id="renameWorkflowLibraryButton"')
    assert "summary.innerHTML = '<div class=\"summary-item\">" in script
    assert 'id="workflowConfigModal"' in markup
    assert 'id="workflowConfigMode"' in markup
    assert 'id="saveWorkflowConfig"' in markup
    assert "function openWorkflowConfig()" in script
    assert "function renderWorkflowConfigBuilder()" in script
    assert 'jsonRequest("/api/workflows/" + encodeURIComponent(appState.workflowId), "PATCH"' in script
    assert 'analysis["input_catalog"] = workflow_input_catalog(saved_workflow, analysis)' in server
    assert '"input_catalog": workflow_input_catalog(workflow)' in app_source


def test_prompt_group_library_can_overwrite_from_current_arrangement():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")

    assert 'data-overwrite-group="' in script
    assert "function overwriteGroup(groupId, button)" in script
    assert '"/api/prompt/groups", "POST"' in script
    assert "id: group.id" in script
    assert "items: state.stage.map(stageItemToApi)" in script
    assert "用当前组装台覆盖组状态" in script


def test_task_workbench_group_snapshot_round_trips_through_task_loading():
    app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    outputs_script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    prompt_script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")

    assert 'pending-prompt-group-v1' in app_script
    assert "queuePromptGroupSnapshot(data.prompt_group)" in app_script
    assert "function promptGroupSnapshot(group)" in app_script
    assert "function notifyPromptWorkbench(group)" in app_script
    assert 'new CustomEvent("rh-focus-prompt-update"' in app_script
    assert "notifyPromptWorkbench(promptGroup)" in app_script
    assert 'pending-prompt-group-v1' in outputs_script
    assert "queuePromptGroupSnapshot(data.prompt_group)" in outputs_script
    assert 'pending-prompt-group-v1' in prompt_script
    assert "function applyPromptGroup(group, notify)" in prompt_script
    assert "applyPendingTaskPromptGroup" in prompt_script
    assert "state.stage = group.items.map(stageItemFromApi).filter(Boolean)" in prompt_script


def test_task_queue_adapts_card_count_to_available_width():
    css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert ".queue-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));" in css
    assert ".queue-list { grid-template-columns: minmax(0, 1fr); }" not in css
    assert ".queue-list { grid-template-columns: 1fr; }" not in css
    assert ".queue-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }" not in css


def test_action_cards_keep_depth_import_button_visible_when_pair_is_missing():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert "var hasColorImage = Boolean(action.image_available || action.color_image_available);" in script
    assert "暂无可用深度图，请先完成原图与深度图配对" in script
    assert 'class="import-workflow-button action-card-import"' in script
    assert '.import-depth-button:disabled, .import-workflow-button:disabled' in css


def test_prompt_workbench_builds_minimax_media_inputs_from_current_stage():
    markup = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    server = (STATIC_ROOT.parent / "server.py").read_text(encoding="utf-8")

    assert 'id="importMedia"' in markup
    assert "function currentWorkflowContext()" in script
    assert "MiniMax H3 节点" in script
    assert "function usedReferenceMedia()" in script
    assert "function buildMinimaxMediaWorkflow" in script
    assert 'class_type: "LoadImage"' in script
    assert 'class_type: "LoadAudio"' in script
    assert 'ref_images.ref_image_' in script
    assert 'ref_audios.ref_audio_' in script
    assert 'jsonRequest("/api/workflows/analyze", "POST"' in script
    assert "window.localStorage.setItem(draftStorageKey" in script
    assert 'path.endswith("/audio-path")' in server


def test_library_uses_one_outer_scroll_container():
    css = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert "overflow-x: hidden; overflow-y: auto;" in css
    assert "scrollbar-color: transparent transparent; scrollbar-width: none;" in css
    assert ".library-panel::-webkit-scrollbar { display: none; width: 0; height: 0; }" in css
    assert ".library-list { display: grid; align-content: start; grid-auto-rows: max-content; min-height: 0; flex: 0 0 auto; gap: 8px; overflow: visible;" in css
    assert ".library-list::-webkit-scrollbar" not in css


def test_dynamic_fixed_states_use_css_classes_and_focus_does_not_scroll():
    app_js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    app_css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert "style=\"color:var(--danger)\"" not in app_js
    assert "style=\"min-height:130px\"" not in app_js
    assert 'class="task-error task-error-copy"' in app_js
    assert "compact-empty" in app_js
    assert "scrollIntoView" not in app_js
    assert "focus({ preventScroll: true })" in app_js
    assert "function animateTaskInsertion(taskId)" in app_js
    assert "--task-insertion-shift" in app_js
    assert "animateTaskInsertion(data && data.task && data.task.id)" in app_js
    assert 'data-action="copy-task-error"' in app_js
    assert "function taskErrorSummary(value)" in app_js
    assert "function copyTaskError(task)" in app_js
    assert "navigator.clipboard.writeText" in app_js
    assert "完整错误信息已复制" in app_js
    assert ".task-card.task-shift-down" in app_css
    assert ".task-error-copy" in app_css
    assert "text-overflow: ellipsis" in app_css
    assert "--motion-task-insertion: 500ms;" in app_css
    assert "--ease-task-insertion: cubic-bezier(0.16, .84, .24, 1);" in app_css
    assert "68%" in app_css
    assert "@keyframes task-card-shift-down" in app_css

    for filename in ("prompt.js", "outputs.js"):
        script = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        styles = re.findall(r"style=\"([^\"]+)\"", script)
        assert all(style.startswith("animation-delay:") for style in styles), filename


def test_video_inputs_support_social_video_download_and_cookie_setting():
    markup = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert 'id="douyinCookiePath"' in markup
    assert 'id="chooseDouyinCookie"' in markup
    assert 'id="saveDouyinCookie"' in markup
    assert 'class="social-video-url"' in script
    assert 'data-action="download-social-video"' in script
    assert '"/api/download-social-video", "POST"' in script
    assert '"/api/pick-douyin-cookie", "POST"' in script
    assert "douyin_cookie_path" in script
    assert ".video-source-row { display: flex; align-items: stretch;" in css
    assert ".video-source-row .social-video-download-button { align-self: flex-start; height: var(--control-height); min-height: var(--control-height);" in css


def test_prompt_free_text_has_english_translation_flow():
    markup = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    index_markup = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'id="copyPrompt"' in markup
    assert 'id="downloadPrompt"' in markup
    assert 'data-translate-stage' in script
    assert 'data-stage-translation' in script
    assert '"/api/prompt/translate"' in script
    assert 'item.translatedText' in script
    assert 'result.translated_text' in script
    assert 'id="aliyunTranslationAccessKeyId"' in index_markup
    assert 'id="aliyunTranslationAccessKeySecret"' in index_markup
    assert '"/api/settings", "PATCH"' in app_script


def test_prompt_free_text_has_chinese_ai_prompt_writer():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")
    server = (STATIC_ROOT.parent / "server.py").read_text(encoding="utf-8")

    assert 'data-ai-prompt-stage' in script
    assert '>AI提示词</span>' in script
    assert 'jsonRequest("/api/prompt/ai-prompt", "POST"' in script
    assert "function aiPromptWorkbenchContext" in script
    assert "function generateAiPrompt" in script
    assert "媒体类型：" in script
    assert ".ai-prompt-button" in styles
    assert "from .prompt_writer import AliyunPromptWriter" in server
    assert 'path == "/api/prompt/ai-prompt"' in server


def test_prompt_free_text_supports_persistent_at_card_references_and_protected_translation():
    markup = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")
    store = (Path(__file__).parents[2] / "web" / "prompt_store.py").read_text(encoding="utf-8")

    assert 'id="promptReferenceSuggest"' in markup
    assert 'contenteditable="true"' in script
    assert "rankedReferenceCandidates" in script
    assert "ArrowDown" in script and "commitReferenceSuggestion" in script
    assert "REFERENCE_SENTINEL_PREFIX" in script
    assert "result.segments" in script
    assert "prompt-reference-token" in styles
    assert "def _segments" in store


def test_prompt_search_prioritizes_category_before_title_and_other_fields():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")

    category_prefix = script.index("if (category.indexOf(needle) === 0) return 0;")
    category_contains = script.index("if (category.indexOf(needle) !== -1) return 1;")
    title_prefix = script.index("if (title.indexOf(needle) === 0) return 2;")
    tag_prefix = script.index("if (tags.some(function (tag) { return tag.indexOf(needle) === 0; })) return 4;")
    assert category_prefix < category_contains < title_prefix < tag_prefix
    assert "if (categoryPrefix) rank = 0;" in script
    assert "else if (titlePrefix) rank = 2;" in script


def test_prompt_reference_preview_is_contained_and_translation_skips_media_segments():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert "function candidateHasMedia" in script
    assert "var hasMedia = candidateHasMedia(selectedCandidate);" in script
    assert 'selectedCandidate.sourceType === "block"' in script
    assert 'preview.classList.toggle("is-block", selectedCandidate.sourceType === "block");' in script
    assert "function translateTextSegments" in script
    assert 'if (segment.type === "reference") return Promise.resolve(null);' in script
    assert "object-fit: contain" in styles
    assert ".prompt-reference-suggest-preview.is-block { align-content: start;" in styles


def test_prompt_reference_tokens_have_media_or_text_hover_preview():
    markup = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'id="promptReferenceHover"' in markup
    assert "showReferenceHover" in script
    assert "addEventListener(\"pointerover\"" in script
    assert "prompt-reference-hover-media" in styles
    assert "prompt-reference-hover-text" in styles


def test_prompt_reference_panel_keeps_wheel_scrolling_inside_candidate_list():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")

    assert 'addEventListener("wheel"' in script
    assert "event.preventDefault();" in script
    assert "list.scrollTop += event.deltaY;" in script


def test_task_prompt_nodes_translate_chinese_in_place():
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'data-action="translate-prompt"' in script
    assert 'class="file-button translate-prompt-button"' in script
    assert "function chinesePromptSegments(text)" in script
    assert "function translatePromptNode(inputId, button)" in script
    assert '"/api/prompt/translate", "POST"' in script
    assert "data.translated_text" in script
    assert "textarea.value = translatedText" in script
    assert "提示词已被修改，未覆盖最新内容" in script


def test_prompt_media_block_supports_picker_clipboard_drop_and_stage_persistence():
    markup = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")
    store = (Path(__file__).parents[2] / "web" / "prompt_store.py").read_text(encoding="utf-8")
    server = (STATIC_ROOT.parent / "server.py").read_text(encoding="utf-8")

    assert 'id="mediaBlockPicker"' in markup
    assert "data-add-media-block" in script
    assert "data-media-stage-dropzone" in script
    assert "data-open-media-stage" in script
    assert "data-paste-media-stage" in script
    assert "data-media-block-dropzone" not in script
    assert "function pasteMediaStageImage" in script
    assert 'jsonRequest("/api/prompt/media", "POST"' in script
    assert 'kind: "media"' in script
    assert "media_path" in script
    assert "mediaKindFromFile" in script
    assert ".media-block-card" in styles
    assert ".media-block-card { border-color:" in styles and "cursor: grab;" in styles
    assert 'kind == "media"' in store
    assert 'path == "/api/prompt/media"' in server
    assert '"audio" if mime.startswith("audio/")' in server
    assert 'var draftStorageKey = "rh-workflow-desk-draft-v1";' in script
    assert "hasImportableMedia" in script
    assert "请先把媒体积木加入提示词工作台" in script


def test_stage_block_interactions_split_title_edit_media_preview_and_drag_regions():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert "stage-block-title-button" in script
    assert 'data-edit-stage="' in script
    assert 'class="stage-block-copy">' in script
    assert "data-paste-media-stage" in script
    assert "stage-block--with-preview" in script
    assert "stage-block--content-only" in script
    assert 'class="stage-block-content"' in script
    assert 'class="stage-block-preview"' in script
    assert "stage-block-grip" not in script
    assert "stage-block-main" not in script
    assert ".stage-block-title-button" in styles
    assert ".stage-block-grip" not in styles
    assert "stage-block-leading-space" not in script
    assert ".stage-block-main" not in styles
    assert ".stage-block--content-only { grid-template-columns: minmax(0, 1fr) var(--stage-actions-width); }" in styles
    assert ".stage-block--with-preview { grid-template-columns: minmax(0, 1fr) minmax(var(--stage-preview-min-width), var(--stage-preview-width)) var(--stage-actions-width); column-gap: var(--stage-card-gap); }" in styles
    assert ".stage-block-preview" in styles


def test_prompt_structure_fields_receive_a_distinct_stage_outline():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    for field in ("subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music"):
        assert '"' + field + '"' in script
    assert "function promptStructureField(item)" in script
    assert 'var content = String(item && item.text || "").toLowerCase();' in script
    assert "item && item.generatedType" not in script[script.index("function promptStructureField"):script.index("function stageBlockMarkup")]
    assert "stage-block--prompt-structure" in script
    assert 'data-prompt-structure-field=' in script
    assert "--stage-structure-accent: var(--type-accent);" in styles
    assert ".stage-block--prompt-structure { border-color: var(--stage-structure-accent);" in styles
    assert ':root[data-theme="light"] .stage-block--prompt-structure,' in styles


def test_prompt_output_imports_sync_the_task_panel_and_keep_standalone_fallback():
    markup = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")

    assert 'id="importPrompt"' in markup
    assert '>导入提示词</button>' in markup
    import_prompt = script[script.index("function importPromptToTask()"):script.index("function bindEvents()")]
    assert 'localStorage.setItem(TASK_PROMPT_IMPORT_KEY' in import_prompt
    assert 'notifySubmitImport({ kind: "prompt" })' in import_prompt
    assert "任务提交面板" in import_prompt
    assert 'if (!focusImport) window.location.href = "/"' in import_prompt

    import_media = script[script.index("function importMinimaxMediaToTask()"):script.index("function filenameFromPath")]
    assert 'window.localStorage.setItem(draftStorageKey, JSON.stringify(draft))' in import_media
    assert 'notifySubmitImport({ kind: "media", mediaCount: mediaAssets.length })' in import_media
    assert 'if (!focusImport) window.location.href = "/"' in import_media

    app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    focus_script = (STATIC_ROOT / "focus.js").read_text(encoding="utf-8")
    assert 'window.addEventListener("rh-focus-submit-update"' in app_script
    assert 'window.RHFocus.importToSubmit' in focus_script
    assert 'rh-focus-prompt-update' in focus_script


def test_prompt_output_media_import_uses_action_depth_images():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    descriptor = script[script.index("function referenceMediaDescriptor"):script.index("function stageReferenceCandidate")]

    assert 'var isAction = candidate.sourceType === "action";' in descriptor
    assert 'String(candidate.depthImagePath || "")' in descriptor
    assert '"/depth-path"' in descriptor
    assert '"/image-path"' not in descriptor


def test_prompt_image_previews_do_not_add_a_letterbox_background_panel():
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert ".action-card-media { align-self: center; width: 124px; height: 164px; min-height: 164px; overflow: hidden; background: transparent; }" in styles
    assert ".reference-media { display: flex; align-items: center; justify-content: center; min-width: 0; overflow: hidden; background: transparent; }" in styles
    assert ".stage-block-preview .stage-media-preview { align-self: center; width: 100%; height: var(--stage-preview-height); min-height: var(--stage-preview-height); overflow: hidden; border-radius: 10px; color: var(--subtle); background: transparent; }" in styles
    assert ".stage-block-preview .stage-media-image img { border-radius: 10px; background: transparent; object-fit: contain; }" in styles
    assert ":root[data-theme=\"light\"] .reference-media {\n  background: transparent;\n}" in styles
    assert ":root[data-theme=\"light\"] .action-card-media {\n  background: transparent;\n}" in styles


def test_free_text_library_card_uses_aligned_text_icon():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert '<span class="block-type-dot text" aria-hidden="true">T</span>' in script
    assert ".block-type-dot.text { display: grid; place-items: center; width: 20px; height: 20px;" in styles


def test_task_input_file_picker_and_social_video_download_are_labeled():
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'class="file-button native-file-button" data-action="pick-native-file"' in script
    assert '点击“选择文件”查看图片' in script
    assert '<button class="file-button" data-action="pick-file" data-input-id="' not in script
    assert 'data-action="open-file-folder"' in script
    assert '>打开文件</button>' in script
    assert 'function openInputFileFolder(inputId, button)' in script
    assert '"/api/open-file-folder"' in script
    assert 'class="social-video-url"' in script
    assert 'aria-label="视频链接"' in script
    assert '社交平台视频链接' not in script
    assert '粘贴 Bilibili、X 或抖音视频链接（可选）' in script
    assert 'data-action="download-social-video"' in script
    assert '>下载视频</button>' in script
    assert 'function downloadSocialVideo(inputId, button)' in script
    assert '"/api/download-social-video"' in script
    assert 'downloadDouyinVideo' not in script


def test_task_input_images_use_prompt_workshop_preview_modal():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    prompt_styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'class="modal-backdrop image-preview-backdrop"' in page
    assert 'id="imagePreviewImage"' in page
    assert 'class="image-preview-trigger file-preview-image-trigger"' in script
    assert "function openImagePreview" in script
    assert "closeImagePreview" in script
    assert 'id="workflowImageContextMenu"' in page
    assert "function openInputImageContextMenu" in script
    assert "copyInputImageToClipboard" in script
    assert ".workflow-image-context-menu" in styles
    assert ".image-preview-surface img" in styles
    assert ".image-preview-surface img" in prompt_styles


def test_task_audio_inputs_render_audio_picker_preview_and_type_safe_dragging():
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert "function isAudioFileInput" in script
    assert "function mediaKindForInput" in script
    assert '<audio controls preload="metadata"></audio>' in script
    assert 'mime.indexOf("audio/") === 0' in script
    assert 'audio:not([hidden])' in script
    assert "目标输入需要" in script
    assert ".file-preview audio { display: block; width: 100%; }" in styles
