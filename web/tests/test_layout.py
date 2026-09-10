from pathlib import Path


WEB_ROOT = Path(__file__).parents[1]
CODE_SUFFIXES = {
    ".bash",
    ".cjs",
    ".css",
    ".html",
    ".js",
    ".mjs",
    ".py",
    ".sh",
    ".ts",
    ".tsx",
}


def test_web_root_does_not_contain_source_code_files():
    exposed_sources = sorted(
        path.name
        for path in WEB_ROOT.iterdir()
        if path.is_file() and path.suffix.lower() in CODE_SUFFIXES
    )

    assert exposed_sources == []

