from pathlib import Path


STATIC_ROOT = Path(__file__).parents[1] / "static"


def test_all_web_pages_load_shared_motion_runtime():
    for page_name in ("index.html", "prompt.html", "outputs.html"):
        page = (STATIC_ROOT / page_name).read_text(encoding="utf-8")
        assert '<script src="/static/motion.js"></script>' in page


def test_shuffle_runtime_covers_page_navigation_and_dialogs():
    motion = (STATIC_ROOT / "motion.js").read_text(encoding="utf-8")
    assert "prepareShuffleIn" in motion
    assert "prepareShuffleOut" in motion
    assert 'surface.style.animation = "none"' in motion
    assert "SHUFFLE_STAGGER" in motion
    assert "window.RHMotion" in motion
    assert "prefers-reduced-motion" in motion


def test_primary_page_wires_shuffle_feedback():
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
