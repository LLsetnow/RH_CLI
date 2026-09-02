from pathlib import Path


STATIC_ROOT = Path(__file__).parents[1] / "static"


def test_output_task_delete_removes_cards_without_reloading_the_grid():
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")

    assert 'data-task-id="' in script
    assert "card.remove()" in script
    assert "return loadOutputs(false)" not in script


def test_output_cards_open_a_keyboard_accessible_preview_modal():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'id="outputPreviewModal"' in page
    assert 'id="closeOutputPreview"' in page
    assert 'data-artifact-id="' in script
    assert 'tabindex="0" role="button"' in script
    assert "openOutputPreview" in script
    assert 'event.key !== " "' in script
    assert ".output-preview-dialog" in styles
    assert "videoPlayerMarkup" in script
    assert "bindVideoLoopControls" in script


def test_video_players_expose_a_loop_toggle():
    motion = (STATIC_ROOT / "motion.js").read_text(encoding="utf-8")
    app = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    outputs = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert "data-video-loop" in motion
    assert "video.loop = !video.loop" in motion
    assert "videoPlayerMarkup(url, false, false)" in app
    assert "videoPlayerMarkup(url, false, false)" in outputs
    assert "videoPlayerMarkup(url, true)" in outputs
    assert ".video-loop-toggle" in styles


def test_output_file_cards_can_import_into_a_task_file_input():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'id="outputImportModal"' in page
    assert 'data-import-output="' in script
    assert "taskFileTargets" in script
    assert "rh-workflow-desk-draft-v1" in script
    assert "output.path" in script
    assert "window.localStorage.setItem(draftStorageKey" in script
    assert ".artifact-import-task" in styles


def test_output_grid_favors_four_portrait_cards_on_desktop():
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in styles
    assert "height: clamp(270px, 28vw, 370px)" in styles
    assert "@media (min-width: 981px) and (max-width: 1180px)" in styles


def test_workflow_submit_exposes_all_instance_type_options():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="instanceType"' in page
    assert 'value="default">Standard · 24GB · ¥4.70572 / 小时' in page
    assert 'value="plus">Plus · 48GB · ¥6.05020 / 小时' in page
    assert 'value="ultra">Ultra · 84GB · ¥9.07531 / 小时' in page


def test_settings_exposes_configurable_action_library_source_and_help():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'id="actionResourcesPath"' in page
    assert 'data-tooltip="读取该文件中从 ## pose' in page
    assert ".field-help::after" in styles
    assert "/api/pick-action-resources" in script
    assert "action_resources_path" in script


def test_settings_modal_uses_one_control_font_size():
    styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert ".settings-modal-panel input" in styles
    assert ".settings-modal-panel select" in styles
    assert ".settings-modal-panel .secondary-button" in styles
    assert ".settings-modal-panel .credential-action" in styles
    assert "font-size: 11px;" in styles


def test_settings_exposes_all_six_prompt_library_sources_and_mode_transition():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    prompt_script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    prompt_styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    for field_id in (
        "promptLibraryPath", "actionResourcesPath", "characterResourcesPath",
        "audioResourcesPath", "backgroundResourcesPath", "clothesResourcesPath",
    ):
        assert f'id="{field_id}"' in page
    assert "reference_resources_paths" in script
    assert "/api/pick-prompt-resource" in script
    assert "animateLibraryModeSwitch" in prompt_script
    assert "library-mode-switching" in prompt_styles
    assert "@keyframes library-mode-switch" in prompt_styles


def test_prompt_action_cards_preserve_the_full_image_in_card_preview():
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert ".action-media-image img { object-fit: contain; }" in styles
    assert ".action-media-image:hover img { transform: none; }" in styles
    assert ".image-preview-frame img" in styles and "object-fit: contain" in styles


def test_workflow_file_inputs_support_clipboard_image_paste():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'data-action="paste-file"' in script
    assert 'addEventListener("paste", pasteClipboardImageFromEvent)' in script
    assert "clipboardData.items" in script
    assert "/api/paste-file" in script
    assert "拖入、⌘V 粘贴" in script
