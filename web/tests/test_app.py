from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from web import app as web_app
from rh_cli.errors import RhCliError


def test_inspect_workflow_finds_direct_files_and_prompts():
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
        "2": {"class_type": "VHS_LoadVideo", "inputs": {"video": "clip.mp4"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "a quiet room"}},
        "4": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"text": "edit it"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": ["3", 0]}},
    }

    analysis = web_app.inspect_workflow(workflow)

    assert [item["id"] for item in analysis["file_inputs"]] == ["1:image", "2:video"]
    assert [item["id"] for item in analysis["prompt_inputs"]] == ["3:text", "4:text"]
    assert analysis["file_count"] == 2
    assert analysis["prompt_count"] == 2


def test_key_capacity_matches_requested_tiers():
    assert web_app.key_capacity("PERSONAL") == 3
    assert web_app.key_capacity("SHARED") == 100
    assert web_app.key_capacity("ENTERPRISE_PRO") == 100


def test_public_key_masks_secret():
    result = web_app.public_key({"id": "k1", "name": "main", "site": "cn", "api_key": "abcdefgh12345678"})
    assert result["masked_key"] == "abcd••••5678"
    assert "api_key" not in result


def test_native_picker_returns_existing_posix_path(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    source.write_bytes(b"image")
    monkeypatch.setattr(web_app.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(web_app.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)
    monkeypatch.setattr(
        web_app.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=f"{source}\n"),
    )

    assert web_app.native_file_picker_available() is True
    assert web_app.pick_local_file_on_macos() == source.resolve()


def test_local_store_persists_workflow_input_and_task(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(web_app, "WORKFLOW_ROOT", tmp_path / "data" / "workflows")
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", tmp_path / "data" / "outputs")
    monkeypatch.setattr(web_app, "KEYS_PATH", tmp_path / "data" / "keys.json")
    monkeypatch.setattr(web_app, "DB_PATH", tmp_path / "data" / "tasks.sqlite3")
    monkeypatch.setattr(web_app, "default_local_output_dir", lambda: tmp_path / "default-output")

    store = web_app.LocalStore()
    try:
        workflow_id, workflow_path, analysis = store.save_workflow(
            "demo_api.json",
            json.dumps({"1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}}}),
        )
        source = tmp_path / "source.png"
        source.write_bytes(b"png-bytes")
        task_id = "task_test"
        store.create_task(
            {
                "id": task_id,
                "created_at": 1,
                "workflow_path": str(workflow_path),
                "workflow_name": "demo_api.json",
                "files": {"1:image": str(source)},
                "prompts": {},
                "key_id": None,
                "output_dir": str(tmp_path / "out"),
            }
        )
        task = store.task(task_id)
        assert workflow_id.startswith("wf_")
        assert analysis["file_count"] == 1
        assert task["files"]["1:image"] == str(source)
        assert task["status"] == "queued"
        assert not (tmp_path / "data" / "inputs").exists()
    finally:
        store._db.close()


def test_submit_task_requires_all_detected_files(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(web_app, "WORKFLOW_ROOT", tmp_path / "data" / "workflows")
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", tmp_path / "data" / "outputs")
    monkeypatch.setattr(web_app, "KEYS_PATH", tmp_path / "data" / "keys.json")
    monkeypatch.setattr(web_app, "DB_PATH", tmp_path / "data" / "tasks.sqlite3")
    monkeypatch.setattr(web_app, "default_local_output_dir", lambda: tmp_path / "default-output")

    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        workflow_id, _, _ = store.save_workflow(
            "demo_api.json",
            json.dumps({"1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}}}),
        )
        with pytest.raises(RhCliError) as excinfo:
            manager.submit_task(workflow_id, {}, {}, None, None)
        assert excinfo.value.code == "MISSING_INPUT"
    finally:
        manager.close()
        store._db.close()
