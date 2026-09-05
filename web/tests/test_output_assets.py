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
    assert 'event.key === " "' in script
    assert ".output-preview-dialog" in styles
    assert "videoPlayerMarkup" in script
    assert "bindVideoLoopControls" in script


def test_output_cards_support_local_selection_and_media_shortcuts():
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert "selectedArtifactId" in script
    assert "selectArtifactCard" in script
    assert "navigateSelectedArtifact" in script
    assert 'event.key === "ArrowLeft"' in script
    assert 'event.key === "ArrowRight"' in script
    assert 'event.key === "ArrowUp"' in script
    assert 'event.key === "ArrowDown"' in script
    assert "function outputKeyboardNavigationAllowed(event)" in script
    assert 'target.closest(".artifact-card")' in script
    assert 'target.closest("input, select, textarea, [contenteditable=\\"true\\"], audio, video")' in script
    assert "targetPage = currentPage - 1" in script
    assert "targetPage = currentPage + 1" in script
    assert "state.page = targetPage" in script
    assert "toggleSelectedVideo" in script
    assert "video.play()" in script
    assert "previewMediaElement" in script
    assert "seekPreviewMedia" in script
    assert "togglePreviewMedia" in script
    assert "previewSeekSeconds" in script
    assert "var previewSeekSeconds = 1;" in script
    assert "previewRatingMarkup" in script
    assert "setOutputRating(previewItem, event.key)" in script
    assert 'event.key === "Enter"' in script
    keyboard = script[script.index("function handleOutputKeyboardShortcut"):script.index("function render()")]
    assert 'event.key === "Escape" || event.key === "Enter"' in keyboard
    assert 'event.key === "ArrowLeft" || event.key === "ArrowRight"' in keyboard
    assert 'selected.display_type === "image"' not in keyboard
    card_start = script.index('$("outputGrid").addEventListener("keydown"')
    card_end = script.index('$("outputSearch")', card_start)
    card_keyboard = script[card_start:card_end]
    assert "event.stopPropagation();" in card_keyboard
    assert 'event.key === "Delete"' in script
    assert 'event.key === "Backspace"' in script
    assert "deleteArtifactTask(selected)" in script
    assert "outputPreviewIsOpen" in script
    assert ".artifact-card.is-selected" in styles
    assert "outline-offset: -4px" in styles


def test_output_render_selects_the_first_visible_card_when_selection_is_missing():
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")

    selection = script[script.index("function syncArtifactSelection"):script.index("function selectArtifactCard")]
    assert "visibleItems[0]" in selection
    assert "syncArtifactSelection(items)" in script


def test_output_media_cards_show_native_resolution_beside_time():
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert "function artifactResolutionMarkup(item)" in script
    assert "naturalWidth" in script
    assert "videoWidth" in script
    assert 'media.addEventListener(media.tagName === "IMG" ? "load" : "loadedmetadata"' in script
    assert "artifact-foot-info" in script
    assert ".artifact-foot-info" in styles
    assert ".artifact-resolution" in styles


def test_video_players_expose_a_loop_toggle():
    motion = (STATIC_ROOT / "motion.js").read_text(encoding="utf-8")
    app = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    outputs = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert "data-video-loop" in motion
    assert "video.loop = !video.loop" in motion
    assert "videoPlayerMarkup(url, false, false)" in app
    assert "videoPlayerMarkup(url, false, false)" in outputs
    assert "videoPlayerMarkup(url, true, true" in outputs
    assert "video-player-controls" in motion
    assert ".video-loop-toggle" in styles


def test_output_file_cards_can_import_into_a_task_file_input():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'id="outputImportModal"' in page
    assert 'data-artifact-menu-action="import"' in page
    assert "taskFileTargets" in script
    assert "rh-workflow-desk-draft-v1" in script
    assert "output.path" in script
    assert "window.localStorage.setItem(draftStorageKey" in script
    assert "notifySubmitImport({ kind: \"media\", source: \"output\"" in script
    assert "任务提交面板已同步" in script
    assert "fileInputMediaKind" in script
    assert "没有找到匹配的文件输入节点" in script
    assert "只有图片、视频或音频产物可以导入媒体" in script
    assert 'id="artifactContextMenu"' in page
    assert 'data-artifact-menu-action="upload"' in page
    assert 'data-artifact-menu-action="import"' in page
    assert 'data-artifact-menu-action="open-folder"' in page
    assert 'data-artifact-menu-action="delete"' in page
    assert "handleArtifactContextMenu" in script
    assert "handleArtifactMenuAction" in script
    assert "openArtifactFolder" in script
    assert "/open-folder" in script
    assert ".artifact-context-menu" in styles
    assert ".artifact-import-task" not in script


def test_output_telegram_upload_locks_one_artifact_until_request_finishes():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")

    assert "telegramUploadBusy" in script
    assert 'data-artifact-menu-action="upload"' in page
    assert 'uploadAction.textContent = uploadBusy ? "上传中" : "上传"' in script
    assert "delete telegramUploadBusy[key]" in script
    assert "telegramUploadBusy[key]" in script


def test_output_workflow_names_load_the_task_draft_and_open_submit_page():
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'data-load-task-workflow="' in script
    assert "taskDraftFromLoadData" in script
    assert '/api/tasks/" + encodeURIComponent(taskId) + "/load' in script
    assert 'window.localStorage.setItem(draftStorageKey, JSON.stringify(draft))' in script
    assert 'notifySubmitImport({ kind: "workflow", source: "task" })' in script
    assert 'window.location.href = "/"' in script
    assert ".finally(function ()" in script
    assert ".artifact-workflow-link" in styles
    assert "导入媒体" in script
    assert "queuePromptGroupSnapshot(data.prompt_group)" in script


def test_output_workflow_names_show_a_compact_copyable_task_id():
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert "function taskIdLabel(item)" in script
    assert 'data-copy-task-id="' in script
    assert "copyTextToClipboard" in script
    assert 'event.stopPropagation();' in script
    assert "完整任务 ID 已复制" in script
    assert ".artifact-task-id" in styles
    assert ".artifact-task-prefix" in styles
    assert ".artifact-task .artifact-workflow-link" in styles


def test_outputs_are_browsable_by_project_folder_and_reclassifiable_per_task():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'id="outputProjectGrid"' in page
    assert 'id="outputProjectMoveModal"' in page
    assert 'data-artifact-menu-action="move-project"' in page
    assert "UNCLASSIFIED_PROJECT_ID" in script
    assert "outputProjectRecords" in script
    assert "belongsToProject" in script
    assert 'data-output-project="' in script
    assert '"/api/tasks/" + encodeURIComponent(item.task_id) + "/project"' in script
    assert "只会改变项目归类，不会移动或复制成片文件" in script
    assert ".output-project-card" in styles
    assert ".output-project-move-option" in styles


def test_output_projects_can_be_created_and_managed_without_an_all_outputs_folder_card():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'id="createOutputProject"' in page
    assert 'id="outputProjectEditorModal"' in page
    assert 'id="outputProjectDeleteModal"' in page
    assert 'id="outputProjectContextMenu"' in page
    assert 'data-output-project-menu-action="rename"' in page
    assert 'data-output-project-menu-action="delete"' in page
    assert "createProjectFolder" not in script
    assert "openOutputProjectEditor" in script
    assert "openOutputProjectContextMenu" in script
    assert "confirmOutputProjectDelete" in script
    assert '"/api/projects"' in script
    assert '"/api/projects/" + encodeURIComponent(projectId)' in script
    assert "outputProjectCard(allRecord" not in script
    assert ".output-project-context-menu" in styles
    assert ".output-project-empty" in styles


def test_output_cards_can_be_dragged_to_project_folder_drop_targets():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'data-output-project-drop="' in script
    assert 'data-project-draggable="' in script
    assert 'application/x-rh-output-task' in script
    assert "handleOutputProjectDragOver" in script
    assert "handleOutputProjectDragLeave" in script
    assert "handleOutputProjectDrop" in script
    assert "requestOutputTaskProject" in script
    assert 'addEventListener("dragover", handleOutputProjectDragOver)' in script
    assert 'addEventListener("drop", handleOutputProjectDrop)' in script
    assert "可拖拽到项目文件夹" in script
    assert ".is-output-project-drop-target" in styles
    assert ".is-output-project-drop-saving" in styles
    assert "不会移动或复制成片文件" in page


def test_output_grid_favors_four_portrait_cards_on_desktop():
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in styles
    assert "height: clamp(270px, 28vw, 370px)" in styles
    assert "@media (min-width: 981px) and (max-width: 1180px)" in styles


def test_output_page_limits_cards_and_exposes_pagination_controls():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'id="outputPagination"' in page
    assert "var OUTPUT_PAGE_SIZE = 64;" in script
    assert "function outputPageItems(items)" in script
    assert "slice(start, start + OUTPUT_PAGE_SIZE)" in script
    assert "function renderPagination(totalItems)" in script
    assert 'data-output-page="previous"' in script
    assert 'data-output-page="next"' in script
    assert '$("outputPagination").addEventListener("click"' in script
    assert ".output-pagination" in styles
    assert ".output-page-number.active" in styles


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
    assert "output-preview-rating" in script
    assert "output-preview-rating" in styles


def test_output_cards_support_case_tags_and_three_state_case_filter():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'id="outputTagFilters"' in page
    assert 'data-output-tag="案例"' in page
    assert 'data-output-tag="H"' in page
    assert 'aria-label="案例标签筛选"' in page
    assert 'aria-label="H 标签筛选"' in page
    tag_start = page.index('<div id="outputTagFilters"')
    tag_end = page.index("</div>", tag_start) + len("</div>")
    tag_markup = page[tag_start:tag_end]
    assert ">全部</button>" not in tag_markup
    assert 'data-output-tag=""' not in tag_markup
    assert "全部标签" not in tag_markup
    assert "data-tag-count" not in tag_markup
    assert "output-tag-filter-label" in tag_markup
    assert "output-tag-filter-hint" not in tag_markup
    for tag in ("案例", "H"):
        assert tag_markup.count(f'data-output-tag="{tag}"') == 1
        assert f'data-output-tag="{tag}" data-output-tag-mode="off"' in tag_markup
    assert tag_markup.count(">不启用</button>") == 2
    assert ">包含</button>" not in tag_markup
    assert ">不包含</button>" not in tag_markup
    assert page.index('<div id="outputTagFilters"') > page.index('<div id="outputFilters"')
    assert "tag_counts" in script
    assert "normalizedOutputTags" in script
    assert "filteredOutputs" in script and "state.tagFilters" in script
    assert "outputTagFilterMode" in script
    assert "outputTagFilterMatches" in script
    assert "matchesOutputTagFilters" in script
    assert 'state.tags.every(function (tag)' not in script
    assert "OUTPUT_TAG_FILTER_MODES[(currentIndex + 1) % OUTPUT_TAG_FILTER_MODES.length]" in script
    assert 'button.textContent = label' in script
    assert "setOutputTags" in script
    assert 'body: JSON.stringify({ tags: nextTags })' in script
    assert "toggleCaseTag" in script
    assert "toggleHTag" in script
    assert 'event.key === "6"' in script
    assert 'String(event.key || "").toLowerCase() === "h"' in script
    assert "toggleCaseTag(previewTagItem)" in script
    assert "toggleCaseTag(selected)" in script
    assert "toggleHTag(previewHTagItem)" in script
    assert "toggleHTag(selected)" in script
    assert "refreshTaggedArtifact" in script
    assert "artifact-tag-case" in script
    assert "artifact-tag-h" in script
    assert ".artifact-tag" in styles
    assert ".output-tag-filter" in styles
    assert ".output-tag-filter-group" in styles
    assert ".output-tag-filter-options" in styles
    assert 'data-output-tag-mode="off"' in styles
    assert 'data-output-tag-mode="include"' in styles
    assert 'data-output-tag-mode="exclude"' in styles
    assert "background: var(--panel-glass)" in styles
    assert "color: var(--accent)" in styles
    assert "color: var(--danger)" in styles
    assert "@media (max-width: 650px)" in styles


def test_output_toolbar_can_export_all_case_media():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'id="exportCaseOutputs"' in page
    assert 'id="caseOutputCount"' in page
    assert "caseMediaOutputs" in script
    assert "exportCaseOutputs" in script
    assert 'window.location.href = "/api/outputs/export/case"' in script
    assert "下载 " in script
    assert ".case-export-button" in styles


def test_dashboard_page_exposes_usage_range_and_independent_ledger():
    page = (STATIC_ROOT / "dashboard.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "dashboard.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8")

    assert 'href="/dashboard" aria-current="page"' in page
    assert 'data-dashboard-days="1"' in page
    assert 'data-dashboard-days="7"' in page
    assert 'data-dashboard-days="30"' in page
    assert 'id="dashboardAccountFilter"' in page
    assert 'id="dashboardDailyChart"' not in page
    assert 'id="dashboardResponseRate"' in page
    assert 'id="dashboardWallClock"' in page
    assert 'id="dashboardVideoSeconds"' in page
    assert 'id="dashboardVideoDuration"' in page
    assert page.index('id="dashboardProcessing"') < page.index('id="dashboardVideoDuration"') < page.index('id="dashboardSuccess"')
    assert 'id="dashboardCoinBalance"' in page
    assert 'id="dashboardMoneySpent"' in page
    assert 'id="dashboardAccountBalances"' in page
    assert '"/api/dashboard?days="' in script
    assert '"/api/dashboard/refresh-balances"' in script
    assert 'method: "POST"' in script
    assert "refreshBalanceSnapshots" in script
    assert "余额已更新" in script
    assert "account_id" in script
    assert "renderAccountOptions" in script
    assert "account_count" in script
    assert "account_name" in script
    assert "money_spent" in script
    assert "近 24 小时" in script
    assert "近24小时" in page
    assert "dashboard-range-tabs::before" in styles
    assert "dashboard-data-switching" in script
    assert "独立用量记录" in page
    assert "renderResponse" in script
    assert "response_seconds_per_video_second" in script
    assert "wall_clock_seconds" in script
    assert "video_seconds" in script
    assert "dashboard-heatmap" not in script
    assert "dashboard-heatmap" not in styles
    assert ".dashboard-response-panel" in styles
    assert ".dashboard-metric-processing" in styles
    assert ".dashboard-money-spent" in styles
    assert ".dashboard-recent-item" in styles


def test_dashboard_exposes_registered_workflow_score_ranking():
    page = (STATIC_ROOT / "dashboard.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "dashboard.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "dashboard.css").read_text(encoding="utf-8")

    assert 'id="dashboardTopWorkflows"' in page
    assert 'id="dashboardWorkflowRatedCount"' in page
    assert 'id="dashboardWorkflowRegistryCount"' in page
    assert "去成片评分" not in page
    assert "workflow_scores" in script
    assert "total_score" in script
    assert "rated_output_count" in script
    assert "workflowOutputsUrl" in script
    assert "workflowSearchName" in script
    assert 'replace(/\\.json$/i, "")' in script
    assert 'registered_workflow_id' not in script
    assert 'params.set("workflow_name", searchName)' in script
    assert 'range_start' in script
    assert 'range_end' in script
    assert '<a class="dashboard-workflow-score-item"' in script
    assert "dashboard-workflow-score-item" in styles
    assert "dashboard-workflow-score-empty" in styles


def test_outputs_accept_dashboard_workflow_context_and_keep_range_filter():
    page = (STATIC_ROOT / "outputs.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "outputs.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "outputs.css").read_text(encoding="utf-8")

    assert 'id="outputContextFilter"' not in page
    assert 'id="clearOutputContextFilter"' not in page
    assert 'new URLSearchParams(window.location.search || "")' in script
    assert 'state.search = state.contextWorkflowName' in script
    assert 'params.get("registered_workflow_id")' not in script
    assert 'params.get("range_start")' in script
    assert 'params.get("range_end")' in script
    assert 'params.get("account_id")' in script
    assert "contextOutputMatches" in script
    assert "contextRangeEnd" in script
    assert "task_created_at" in script
    assert "range_start" in script
    assert "range_end" in script
    assert "renderOutputContext" not in script
    assert "clearOutputContextFilter" not in script
    assert ".output-context-filter" not in styles


def test_workflow_submit_exposes_all_instance_type_options():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="instanceType"' in page
    assert 'value="default">Standard · 24GB · ¥4.70572 / 小时' in page
    assert 'value="plus">Plus · 48GB · ¥6.05020 / 小时' in page
    assert 'value="ultra">Ultra · 84GB · ¥9.07531 / 小时' in page


def test_workflow_submit_can_rename_save_and_export_current_workflow():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'id="renameWorkflowLibraryButton"' in page
    assert 'id="saveWorkflowLibraryButton"' in page
    assert 'id="overwriteWorkflowLibraryButton"' not in page
    assert '>导出</button>' in page
    assert 'function buildCurrentWorkflow()' in script
    assert 'function openWorkflowRename()' in script
    assert 'function saveWorkflowRename()' in script
    assert 'jsonRequest("/api/workflows/" + encodeURIComponent(appState.workflowId), "PATCH", { name: name })' in script
    assert 'function saveWorkflowLibrary()' in script
    assert 'jsonRequest("/api/workflows", "POST", payload)' in script
    assert 'input_config' in script
    assert "payload.include_current_prompt_group = true" in script
    assert "已保存工作流和当前提示词组" in script
    assert 'window.dispatchEvent(new CustomEvent("rh-workflow-library-refresh"' in script


def test_settings_exposes_configurable_media_library_root_and_help():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    settings_page = (STATIC_ROOT / "settings.html").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "settings.js").read_text(encoding="utf-8")

    assert 'id="mediaLibraryRoot"' in page
    assert 'id="mediaLibraryRoot"' in settings_page
    assert 'id="actionResourcesPath"' not in settings_page
    assert 'id="characterResourcesPath"' not in settings_page
    assert 'data-tooltip="统一读取 ref/pose/pose.json' in page
    assert ".field-help::after" in styles
    assert "/api/pick-media-root" in script
    assert "media_library_root" in script


def test_settings_exposes_api_key_dispatch_strategy():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    settings_page = (STATIC_ROOT / "settings.html").read_text(encoding="utf-8")
    app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    settings_script = (STATIC_ROOT / "settings.js").read_text(encoding="utf-8")

    for markup in (page, settings_page):
        assert 'id="apiKeyStrategy"' in markup
        assert 'value="personal_only"' in markup
        assert 'value="personal_then_shared"' in markup
        assert 'value="shared_only"' in markup
        assert "任务提交页不再选择 API Key" in markup
    assert "api_key_strategy" in app_script
    assert "saveApiKeyStrategy" in app_script
    assert "api_key_strategy" in settings_script


def test_settings_exposes_pose_media_import_type():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    settings_page = (STATIC_ROOT / "settings.html").read_text(encoding="utf-8")
    app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    settings_script = (STATIC_ROOT / "settings.js").read_text(encoding="utf-8")
    prompt_script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")

    for markup in (page, settings_page):
        assert 'id="poseMediaImportType"' in markup
        assert 'value="depth"' in markup
        assert 'value="skeleton"' in markup
        assert "动作导入媒体" in markup
    assert "savePoseMediaImportType" in app_script
    assert "pose_media_import_type" in app_script
    assert "pose_media_import_type" in settings_script
    assert "applySettingsSnapshot" in prompt_script
    assert "actionPoseImportInfo" in prompt_script
    assert '"/skeleton-path"' in prompt_script


def test_task_submission_does_not_expose_api_key_selector():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'id="keySelect"' not in page
    assert "调度 API Key" not in page
    assert "keySelect" not in script


def test_settings_modal_uses_one_control_font_size():
    styles = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert ".settings-modal-panel input" in styles
    assert ".settings-modal-panel select" in styles
    assert ".settings-modal-panel .secondary-button" in styles
    assert ".settings-modal-panel .credential-action" in styles
    assert "font-size: 11px;" in styles


def test_settings_exposes_media_library_source_and_mode_transition():
    page = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    settings_page = (STATIC_ROOT / "settings.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "settings.js").read_text(encoding="utf-8")
    prompt_script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    prompt_styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'id="promptLibraryPath"' not in page
    assert 'id="promptLibraryPath"' not in settings_page
    assert 'id="mediaLibraryRoot"' in page
    assert 'id="mediaLibraryRoot"' in settings_page
    assert "基础积木 JSON 文件" not in page
    assert "基础积木 JSON 文件" not in settings_page
    assert "prompt_library_path" not in script
    assert "media_library_root" in script
    assert "/api/pick-media-root" in script
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


def test_prompt_library_title_toggles_a_full_width_animated_view():
    page = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'id="toggleLibraryExpand"' in page
    assert 'aria-controls="promptBuilderGrid"' in page
    assert "libraryExpanded" in script
    assert "setLibraryExpanded" in script
    assert 'classList.toggle("is-library-expanded"' in script
    assert ".prompt-builder-grid.is-library-expanded" in styles
    assert ".prompt-builder-grid.is-library-expanded .prompt-main-stack" in styles
    assert "grid-template-columns var(--motion-expand) var(--ease-out)" in styles
    assert "translate3d(18px, 0, 0)" in styles


def test_prompt_library_expanded_cards_use_responsive_two_to_four_column_grid_without_add_buttons():
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert "data-add-action" not in script
    assert "data-add-reference" not in script
    assert "data-add-block" not in script
    assert "add-block-button" not in script
    assert "拖动卡片加入" in script
    assert "repeat(auto-fill, minmax(280px, 1fr))" in styles
    assert ".prompt-builder-grid.is-library-expanded .library-list > .action-library-card" in styles
    assert ".prompt-builder-grid.is-library-expanded .library-list > .reference-library-card" in styles


def test_prompt_library_basic_cards_edit_from_title_and_image_preview_is_image_only():
    page = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'class="library-block-title library-block-title-button"' in script
    assert 'data-edit-block="' in script
    assert 'var method = blockId ? "PUT" : "POST"' in script
    assert 'class="modal-backdrop image-preview-backdrop"' in page
    assert 'class="image-preview-surface"' in page
    assert 'id="imagePreviewTitle"' not in page
    assert 'id="imagePreviewCaption"' not in page
    assert ".image-preview-surface img" in styles
    assert "box-shadow:" in styles
    assert "height: 176px" in styles


def test_prompt_library_images_open_copy_context_menu_and_write_binary_clipboard_data():
    page = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'id="libraryImageContextMenu"' in page
    assert 'data-library-image-menu-action="copy"' in page
    assert 'addEventListener("contextmenu"' in script
    assert "openLibraryImageContextMenu" in script
    assert "copyLibraryImageToClipboard" in script
    assert "navigator.clipboard.write" in script
    assert "new ClipboardItem" in script
    assert "imageBlobAsPng" in script
    assert ".library-image-context-menu" in styles


def test_prompt_library_resource_cards_support_preview_tag_edit_and_contextual_creation():
    page = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'id="textPreviewModal"' in page
    assert "data-text-preview" in script
    assert "data-edit-resource" in script
    assert "openResourceModal" in script
    assert "/api/prompt/actions" in script
    assert "/api/prompt/references" in script
    assert 'state.libraryMode === "blocks"' in script
    assert "RESOURCE_LABELS" in script
    assert "@container prompt-library (min-width: 380px)" in styles
    assert "@container prompt-library (min-width: 500px)" in styles
    assert "repeat(3, minmax(0, 1fr))" in styles


def test_prompt_resource_creation_imports_media_without_manual_relative_paths():
    page = (STATIC_ROOT / "prompt.html").read_text(encoding="utf-8")
    script = (STATIC_ROOT / "prompt.js").read_text(encoding="utf-8")
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert 'id="resourceMediaPicker"' in page
    assert 'type="hidden"' in page
    assert "RESOURCE_MEDIA_SLOTS" in script
    assert "data-resource-media-pick" in script
    assert "data-resource-media-paste" in script
    assert 'addEventListener("drop"' in script
    assert "handleResourceMediaPaste" in script
    assert "resourceMediaPayload" in script
    assert "media.length" in script
    assert "/api/prompt/actions" in script and "/api/prompt/references" in script
    assert "不会复制媒体" not in page
    assert ".resource-media-slot.is-dragging" in styles


def test_prompt_action_cards_preserve_the_full_image_in_card_preview():
    styles = (STATIC_ROOT / "prompt.css").read_text(encoding="utf-8")

    assert ".action-media-image img { object-fit: contain; }" in styles
    assert ".action-media-image:hover img { transform: none; }" in styles
    assert ".image-preview-surface img" in styles and "object-fit: contain" in styles


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
    assert "var mediaMarkup = kind === \"video\" ?" in script
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
