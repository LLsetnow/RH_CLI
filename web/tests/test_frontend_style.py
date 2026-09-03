from pathlib import Path
import re


STATIC_ROOT = Path(__file__).parents[1] / "static"
PAGE_STYLES = ("prompt.css", "outputs.css", "workflows.css", "compare.css", "dashboard.css")
PAGE_MARKUP = ("index.html", "prompt.html", "outputs.html", "workflows.html", "compare.html", "dashboard.html")
MODAL_MARKUP = ("index.html", "prompt.html", "outputs.html", "workflows.html")


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
    raw_color = re.compile(r"#[0-9a-fA-F]{3,8}")
    raw_duration = re.compile(
        r"(?:transition|animation)(?:-[\w-]+)?\s*:[^;}]*(?:\d+ms|\d+(?:\.\d+)?s)"
    )

    for filename in PAGE_STYLES:
        css = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        assert not raw_color.search(css), filename
        assert not raw_duration.search(css), filename
        assert "0 30px 100px rgba" not in css, filename


def test_all_dialogs_use_the_shared_dialog_panel():
    for filename in MODAL_MARKUP:
        markup = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        dialog_tags = re.findall(r"<(?:section|div)\b[^>]*role=\"dialog\"[^>]*>", markup)
        assert dialog_tags, filename
        assert all("dialog-panel" in tag for tag in dialog_tags), filename


def test_settings_entry_is_the_same_route_on_every_page():
    for filename in PAGE_MARKUP:
        markup = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        assert 'href="/?openSettings=1"' in markup, filename


def test_public_navigation_has_one_order_and_structure():
    expected_links = ["/workflows", "/prompt", "/", "/outputs", "/dashboard"]

    for filename in PAGE_MARKUP:
        markup = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        links = re.findall(r'<a class="top-nav-link(?: active)?" href="([^"]+)"', markup)
        assert links == expected_links, filename
        assert markup.count('class="top-nav"') == 1, filename


def test_workflow_cards_overwrite_task_submit_workflow():
    script = (STATIC_ROOT / "workflows.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "workflows.css").read_text(encoding="utf-8")

    assert 'data-action="select-workflow"' in script
    assert "workflowDraftFromDetail" in script
    assert 'rh-workflow-desk-draft-v1' in script
    assert "localStorage.setItem(draftStorageKey" in script
    assert 'window.location.href = "/"' in script
    assert "覆盖任务提交页的当前工作流" in script
    assert ".workflow-card-title-button { display: block; flex: 0 1 auto;" in styles
    assert "max-width: calc(100% - 12px);" in styles


def test_prompt_group_library_can_overwrite_from_current_arrangement():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")

    assert 'data-overwrite-group="' in script
    assert "function overwriteGroup(groupId, button)" in script
    assert '"/api/prompt/groups", "POST"' in script
    assert "id: group.id" in script
    assert "items: state.stage.map(stageItemToApi)" in script
    assert "用当前组装台覆盖组状态" in script


def test_action_cards_keep_depth_import_button_visible_when_pair_is_missing():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert "var hasColorImage = Boolean(action.image_available || action.color_image_available);" in script
    assert "暂无可用深度图，请先完成原图与深度图配对" in script
    assert 'class="import-workflow-button action-card-import"' in script
    assert '.import-depth-button:disabled, .import-workflow-button:disabled' in css


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
    assert 'class="task-error"' in app_js
    assert "compact-empty" in app_js
    assert "scrollIntoView" not in app_js
    assert "focus({ preventScroll: true })" in app_js
    assert "function animateTaskInsertion(taskId)" in app_js
    assert "--task-insertion-shift" in app_js
    assert "animateTaskInsertion(data && data.task && data.task.id)" in app_js
    assert ".task-card.task-shift-down" in app_css
    assert "--motion-task-insertion: 500ms;" in app_css
    assert "--ease-task-insertion: cubic-bezier(0.16, .84, .24, 1);" in app_css
    assert "68%" in app_css
    assert "@keyframes task-card-shift-down" in app_css

    for filename in ("prompt.js", "outputs.js"):
        script = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        styles = re.findall(r"style=\"([^\"]+)\"", script)
        assert all(style.startswith("animation-delay:") for style in styles), filename


def test_video_inputs_support_douyin_download_and_cookie_setting():
    markup = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert 'id="douyinCookiePath"' in markup
    assert 'id="chooseDouyinCookie"' in markup
    assert 'id="saveDouyinCookie"' in markup
    assert 'class="douyin-url"' in script
    assert 'data-action="download-douyin"' in script
    assert '"/api/download-douyin", "POST"' in script
    assert '"/api/pick-douyin-cookie", "POST"' in script
    assert "douyin_cookie_path" in script
    assert ".video-source-row { display: flex; align-items: stretch;" in css
    assert ".video-source-row .douyin-download-button { min-height: var(--control-height);" in css


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
