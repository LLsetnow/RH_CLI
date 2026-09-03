import re
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


def test_output_workflow_names_load_the_task_draft_and_open_submit_page():
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'data-load-task-workflow="' in script
    assert "taskDraftFromLoadData" in script
    assert '/api/tasks/" + encodeURIComponent(taskId) + "/load' in script
    assert 'window.localStorage.setItem(draftStorageKey, JSON.stringify(draft))' in script
    assert 'window.location.href = "/"' in script
    assert ".finally(function ()" in script
    assert ".artifact-workflow-link" in styles
    assert '>导入任务</button>' in script


def test_output_grid_favors_four_portrait_cards_on_desktop():
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in styles
    assert "height: clamp(270px, 28vw, 370px)" in styles
    assert "@media (min-width: 981px) and (max-width: 1180px)" in styles


def test_output_cards_support_star_ratings_and_rating_filters():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'id="outputRatingFilters"' in page
    assert 'data-output-rating="unrated"' in page
    assert 'aria-label="筛选未评分"' in page
    assert 'id="deleteOneStarOutputs"' in page
    assert 'data-output-rating="5"' in page
    assert "ratingStarsMarkup" in script
    assert "setOutputRating" in script
    assert 'document.querySelector(".artifact-card:hover")' in script
    assert '"/outputs/"' in script
    assert "refreshRatedArtifact" in script
    assert "deleteOneStarOutputs" in script
    assert "/api/outputs/rating/1" in script
    assert "ratingNode.outerHTML" in script
    assert 'state.rating === "unrated"' in script
    assert "rating_counts.unrated" in script
    assert ".artifact-name-row" in styles
    assert ".artifact-card:hover { border-color:" in styles
    assert ".rating-stars-1" in styles
    assert ".rating-stars-5" in styles
    assert "var(--rating-gray)" in styles
    assert "var(--rating-yellow)" in styles


def test_dashboard_page_exposes_usage_range_and_independent_ledger():
    page = (STATIC_ROOT / "dashboard.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "dashboard.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8")

    assert 'href="/dashboard" aria-current="page"' in page
    assert 'data-dashboard-days="1"' in page
    assert 'data-dashboard-days="7"' in page
    assert 'data-dashboard-days="30"' in page
    assert 'id="dashboardAccountFilter"' in page
    assert 'id="dashboardDailyChart"' in page
    assert 'id="dashboardCoinBalance"' in page
    assert 'id="dashboardAccountBalances"' in page
    assert '"/api/dashboard?days="' in script
    assert "account_id" in script
    assert "renderAccountOptions" in script
    assert "account_count" in script
    assert "account_name" in script
    assert "dashboard-range-tabs::before" in styles
    assert "dashboard-data-switching" in script
    assert "独立用量记录" in page
    assert ".dashboard-daily-chart" in styles
    assert "dashboard-heatmap" in script
    assert "renderAnnualHeatmap" in script
    assert "heatmap.daily" in script
    assert "activityScore" in script
    assert "综合活跃度" in script
    assert "maxVisibleWeeks" in script
    assert "visibleCalendarStart" in script
    assert ".dashboard-heatmap-calendar" in styles
    assert ".dashboard-heatmap-grid" in styles
    assert ".dashboard-annual-heatmap" in styles
    assert "overflow: hidden" in styles
    assert ".dashboard-metric-processing" in styles
    assert ".dashboard-recent-item" in styles


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


def test_prompt_library_exposes_category_then_tag_filters():
    page = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'id="categoryFilters"' in page
    assert "一级分类" in page
    assert "二级标签" in page
    assert "categoryFilter" in script
    assert "renderCategoryFilters" in script
    assert 'data-filter-category=' in script
    assert "block.category" in script
    assert "isReferenceMode()" in script
    assert "state.libraryMode === \"pose\"" in script
    assert "state.libraryMode === \"actions\"" in script
    assert "entry.category" in script
    assert "var category = String(entry.category || \"\")" in script
    assert "(entry.tags || []).filter" in script
    assert "String(tag).trim() !== category" in script
    assert "action-card-category" in script
    assert ".category-filter" in styles
    assert ".action-card-category" in styles


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


def test_workflow_video_inputs_render_a_streamed_local_preview():
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert "isVideoFileInput" in script
    assert "expectedPreviewKind" in script
    assert "mediaKindFromFile" in script
    assert "filePreviewMarkup" in script
    assert "var mediaMarkup = isVideo ?" in script
    assert 'accept="' in script
    assert '<video controls preload="metadata" playsinline></video>' in script
    assert '<img alt="" draggable="false" />' in script
    assert "mediaMarkup" in script
    assert "</figure></div></div>" in script
    assert "</figure></div></div>" in script
    assert 'preview_kind' in script
    assert "expectedKind !== detectedKind" in script
    assert "video.src = selected.preview_url" in script
    assert ".file-preview img, .file-preview video" in styles


def test_task_queue_notifies_once_when_a_task_becomes_completed():
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "taskStatusSnapshot" in script
    assert "completedTaskNotices" in script
    assert "detectCompletedTasks" in script
    assert 'task.status !== "completed"' in script
    assert "taskCompletionNotice" in script
    assert 'showToast("任务完成：" + taskName)' in script


def test_prompt_workbench_removes_a_card_without_rerendering_the_whole_stage():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    match = re.search(r"function removeStage\(index\) \{(?P<body>.*?)\n  \}", script, re.S)

    assert match
    assert "updateStageAfterRemoval" in match.group("body")
    assert "renderOutput()" not in match.group("body")
    assert "renderStage()" not in match.group("body")
    assert "showToast" in match.group("body")
    assert "data-edit-stage" in script
