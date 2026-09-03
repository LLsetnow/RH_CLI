from pathlib import Path
import re


STATIC_ROOT = Path(__file__).parents[1] / "static"
PAGE_STYLES = ("prompt.css", "outputs.css", "workflows.css", "compare.css", "dashboard.css", "settings.css")
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
        assert all("dialog-panel" in tag or "image-preview-backdrop" in tag for tag in dialog_tags), filename


def test_settings_entry_is_the_same_route_on_every_page():
    for filename in PAGE_MARKUP:
        markup = (STATIC_ROOT / filename).read_text(encoding="utf-8")
        assert 'href="/settings"' in markup, filename


def test_settings_switches_share_one_capsule_control():
    markup = (STATIC_ROOT / "settings.html").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "settings.css").read_text(encoding="utf-8")
    assert len(re.findall(r'class="settings-switch(?:\s|\")', markup)) == 3
    assert 'class="settings-switch-track"' in markup
    assert ".settings-switch input:checked + .settings-switch-track" in styles
    assert ".settings-switch input:focus-visible + .settings-switch-track" in styles


def test_telegram_switches_put_the_label_before_the_pill_control():
    app_css = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    settings_css = (STATIC_ROOT / "settings.css").read_text(encoding="utf-8")

    assert ".telegram-enabled-toggle > span:last-child { order: -1; }" in app_css
    assert ".settings-toggle-row .settings-switch-label," in settings_css
    assert ".telegram-inbound-panel .settings-switch-label { order: -1; }" in settings_css


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
    assert '"Control+Left"' in main
    assert '"Control+Right"' in main
    assert '"rh-global-page-navigation"' in main
    assert "onGlobalPageNavigation" in preload
    assert "event.ctrlKey" in motion
    assert 'event.key !== "ArrowLeft"' in motion
    assert 'event.key !== "ArrowRight"' in motion
    assert "topLevelNavigationLinks" in motion
    assert "target.click()" in motion
    for filename in PAGE_MARKUP:
        assert '/static/motion.js' in (STATIC_ROOT / filename).read_text(encoding="utf-8"), filename


def test_workflow_cards_overwrite_task_submit_workflow():
    script = (STATIC_ROOT / "workflows.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "workflows.css").read_text(encoding="utf-8")

    assert 'data-action="select-workflow"' in script
    assert "workflowDraftFromDetail" in script
    assert 'rh-workflow-desk-draft-v1' in script
    assert "localStorage.setItem(draftStorageKey" in script
    load_workflow = script[script.index("function loadWorkflowIntoSubmit"):script.index("var configKinds")]
    assert 'showToast("已覆盖任务提交页工作流草稿：" + draft.workflow.name)' in load_workflow
    assert 'window.location.href' not in load_workflow
    assert "覆盖任务提交页的当前工作流" in script
    assert ".workflow-card-title-button { display: block; flex: 0 1 auto;" in styles
    assert "max-width: calc(100% - 12px);" in styles


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
    assert 'pending-prompt-group-v1' in outputs_script
    assert "queuePromptGroupSnapshot(data.prompt_group)" in outputs_script
    assert 'pending-prompt-group-v1' in prompt_script
    assert "applyPendingTaskPromptGroup" in prompt_script
    assert "state.stage = group.items.map(stageItemFromApi).filter(Boolean)" in prompt_script


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


def test_prompt_output_imports_write_to_the_task_draft_without_navigating():
    markup = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")

    assert 'id="importPrompt"' in markup
    assert '>导入提示词</button>' in markup
    import_prompt = script[script.index("function importPromptToTask()"):script.index("function bindEvents()")]
    assert 'localStorage.setItem(TASK_PROMPT_IMPORT_KEY' in import_prompt
    assert 'showToast("提示词已写入任务提交页草稿")' in import_prompt
    assert 'window.location.href' not in import_prompt

    import_media = script[script.index("function importMinimaxMediaToTask()"):script.index("function filenameFromPath")]
    assert 'window.localStorage.setItem(draftStorageKey, JSON.stringify(draft))' in import_media
    assert 'window.location.href' not in import_media


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


def test_task_input_file_picker_is_labeled_select_file():
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert '>选择文件</button></div>' in script
    assert '点击“选择文件”查看图片' in script
