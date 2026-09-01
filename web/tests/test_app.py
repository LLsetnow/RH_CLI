from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from web import app as web_app
from web import server as web_server
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


def test_inspect_workflow_reads_remote_id_without_treating_metadata_as_node():
    workflow = {
        "__rh_meta__": {"workflowId": "123456"},
        "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
    }

    analysis = web_app.inspect_workflow(workflow)

    assert analysis["remote_workflow_id"] == "123456"
    assert [item["id"] for item in analysis["file_inputs"]] == ["1:image"]


def test_inspect_workflow_finds_random_noise_inputs():
    workflow = {
        "7": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": 123, "mode": "fixed"},
            "_meta": {"title": "采样随机种子"},
        }
    }

    analysis = web_app.inspect_workflow(workflow)

    assert analysis["random_noise_count"] == 1
    assert analysis["random_noise_inputs"][0]["id"] == "7"
    assert analysis["random_noise_inputs"][0]["seed"] == "123"
    assert analysis["random_noise_inputs"][0]["mode"] == "fixed"


def test_normalize_random_noise_inputs_rejects_unknown_mode():
    workflow = {"7": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0, "mode": "randomize"}}}

    with pytest.raises(RhCliError) as excinfo:
        web_app.normalize_random_noise_inputs(workflow, {"7": {"seed": "12", "mode": "increment"}})

    assert excinfo.value.code == "INVALID_RANDOM_NOISE"


def test_key_capacity_matches_requested_tiers():
    assert web_app.key_capacity("PERSONAL") == 3
    assert web_app.key_capacity("SHARED") == 100
    assert web_app.key_capacity("ENTERPRISE_PRO") == 100


def test_public_key_masks_secret():
    result = web_app.public_key({"id": "k1", "name": "main", "site": "cn", "api_key": "abcdefgh12345678"})
    assert result["masked_key"] == "abcd••••5678"
    assert "api_key" not in result
    assert result["balance_checked_at"] == 0


def _configure_web_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(web_app, "WORKFLOW_ROOT", tmp_path / "data" / "workflows")
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", tmp_path / "data" / "outputs")
    monkeypatch.setattr(web_app, "KEYS_PATH", tmp_path / "data" / "keys.json")
    monkeypatch.setattr(web_app, "DB_PATH", tmp_path / "data" / "tasks.sqlite3")


def _saved_key() -> dict[str, object]:
    return {
        "id": "key_test",
        "name": "测试 Key",
        "site": "cn",
        "api_key": "abcdefgh12345678",
        "status": "ready",
        "status_message": "检测成功",
        "api_type": "PERSONAL",
        "capacity": 3,
        "active_tasks": 0,
        "balance": "1.25",
        "coins": "10",
        "symbol": "¥",
        "checked_at": 111,
        "balance_checked_at": 222,
    }


def test_check_key_updates_balance_and_query_time(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        store.save_keys([_saved_key()])
        monkeypatch.setattr(
            manager,
            "_fetch_account_data",
            lambda record: {"remainMoney": "8.50", "remainCoins": "42", "apiType": "SHARED"},
        )

        result = manager.check_key("key_test")
        saved = store.get_key("key_test")

        assert result["status"] == "ready"
        assert result["balance"] == "8.50"
        assert result["coins"] == "42"
        assert result["balance_checked_at"] > 222
        assert result["checked_at"] > 111
        assert saved["api_type"] == "SHARED"
        assert saved["capacity"] == 100
    finally:
        manager.close()
        store._db.close()


def test_refresh_balance_only_updates_balance_fields(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        store.save_keys([_saved_key()])
        monkeypatch.setattr(
            manager,
            "_fetch_account_data",
            lambda record: {"remainMoney": "9.75", "remainCoins": "88", "apiType": "ENTERPRISE_PRO"},
        )

        result = manager.refresh_balance("key_test")
        saved = store.get_key("key_test")

        assert result["balance"] == "9.75"
        assert result["coins"] == "88"
        assert result["balance_checked_at"] > 222
        assert result["status"] == "ready"
        assert result["api_type"] == "PERSONAL"
        assert result["capacity"] == 3
        assert result["checked_at"] == 111
        assert saved["status_message"] == "检测成功"
    finally:
        manager.close()
        store._db.close()


def test_refresh_balance_failure_preserves_previous_values(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        store.save_keys([_saved_key()])
        monkeypatch.setattr(
            manager,
            "_fetch_account_data",
            lambda record: (_ for _ in ()).throw(RhCliError("API_ERROR", "余额接口暂时不可用")),
        )

        with pytest.raises(RhCliError, match="余额接口暂时不可用"):
            manager.refresh_balance("key_test")

        saved = store.get_key("key_test")
        assert saved["balance"] == "1.25"
        assert saved["coins"] == "10"
        assert saved["status"] == "ready"
        assert saved["checked_at"] == 111
        assert saved["balance_checked_at"] == 222
    finally:
        manager.close()
        store._db.close()


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


def test_native_directory_picker_returns_existing_posix_path(tmp_path, monkeypatch):
    source = tmp_path / "outputs"
    source.mkdir()
    monkeypatch.setattr(web_app.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(web_app.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None)
    monkeypatch.setattr(
        web_app.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=f"{source}\n"),
    )

    assert web_app.pick_local_directory_on_macos() == source.resolve()


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
                "random_noise": {"2": {"seed": 123, "mode": "fixed"}},
                "key_id": None,
                "remote_workflow_id": "123456",
                "output_dir": str(tmp_path / "out"),
            }
        )
        task = store.task(task_id)
        assert workflow_id.startswith("wf_")
        assert analysis["file_count"] == 1
        assert task["files"]["1:image"] == str(source)
        assert task["remote_workflow_id"] == "123456"
        assert task["random_noise"] == {"2": {"seed": 123, "mode": "fixed"}}
        assert task["status"] == "queued"
        assert task["stage_logs"][0]["stage"] == "queue"
        store.append_stage_log(
            task_id,
            "submit",
            "提交失败",
            level="error",
            detail={"apiKey": "secret-key", "message": "apiKey=secret-key"},
        )
        store.set_error_detail(task_id, {"code": "SUBMIT_FAILED", "detail": {"apiKey": "secret-key"}})
        diagnosed = store.task(task_id)
        assert diagnosed["stage_logs"][-1]["detail"]["apiKey"] == "[已脱敏]"
        assert "secret-key" not in json.dumps(diagnosed["error_detail"], ensure_ascii=False)
        assert not (tmp_path / "data" / "inputs").exists()
    finally:
        store._db.close()


def test_load_task_workflow_returns_saved_workflow_and_task_inputs(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        workflow_id, workflow_path, analysis = store.save_workflow(
            "noise_api.json",
            json.dumps({"2": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0, "mode": "randomize"}}}),
        )
        store.create_task(
            {
                "id": "task_load",
                "created_at": 1,
                "workflow_path": str(workflow_path),
                "workflow_name": "noise_api.json",
                "files": {},
                "prompts": {},
                "random_noise": {"2": {"seed": 456, "mode": "fixed"}},
                "key_id": None,
                "remote_workflow_id": "123456",
                "output_dir": str(tmp_path / "out"),
            }
        )

        loaded = store.load_task_workflow("task_load")

        assert loaded["workflow_id"] == workflow_id
        assert loaded["workflow"]["2"]["class_type"] == "RandomNoise"
        assert loaded["analysis"]["random_noise_count"] == 1
        assert loaded["task"]["random_noise"]["2"]["mode"] == "fixed"
    finally:
        store._db.close()


def test_submit_task_saves_modified_workflow_with_random_noise(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="123456",
            workflow_data={"2": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0, "mode": "randomize"}}},
            workflow_name="modified_noise.json",
            random_noise={"2": {"seed": "789", "mode": "fixed"}},
        )

        loaded = store.load_task_workflow(task["id"])

        assert loaded["task"]["random_noise"] == {"2": {"seed": 789, "mode": "fixed"}}
        assert loaded["workflow"]["2"]["inputs"] == {"noise_seed": 0, "mode": "randomize"}
    finally:
        manager.close()
        store._db.close()


def test_local_file_preview_reads_image_without_copying(tmp_path):
    source = tmp_path / "existing.png"
    source.write_bytes(b"png-bytes")

    result = web_server.local_file_preview(str(source))

    assert result["path"] == str(source.resolve())
    assert result["name"] == "existing.png"
    assert result["preview_url"].startswith("data:image/png;base64,")
    assert not (web_app.WEB_ROOT / "data" / "inputs" / source.name).exists()


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
            manager.submit_task(workflow_id, {}, {}, None, None, "123456")
        assert excinfo.value.code == "MISSING_INPUT"
    finally:
        manager.close()
        store._db.close()


def test_submit_task_requires_remote_workflow_id(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        workflow_id, _, _ = store.save_workflow(
            "demo_api.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
        )
        with pytest.raises(RhCliError) as excinfo:
            manager.submit_task(workflow_id, {}, {}, None, None)
        assert excinfo.value.code == "MISSING_WORKFLOW_ID"
    finally:
        manager.close()
        store._db.close()


def test_run_task_uses_remote_id_and_strips_web_metadata(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        workflow_id, _, _ = store.save_workflow(
            "demo_api.json",
            json.dumps(
                {
                    "__rh_meta__": {"workflowId": "987654"},
                    "1": {"class_type": "SaveImage", "inputs": {}},
                }
            ),
        )
        task = manager.submit_task(workflow_id, {}, {}, None, None)
        submitted = {}

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(web_app, "RhHttpClient", lambda *args, **kwargs: FakeClient())
        monkeypatch.setattr(web_app, "_site_urls", lambda site: ("upload", "create", "outputs"))
        monkeypatch.setattr(
            web_app,
            "_submit",
            lambda client, api_key, workflow_id, workflow_json, **kwargs: submitted.update(
                {"workflow_id": workflow_id, "workflow": json.loads(workflow_json)}
            ) or "remote-task-1",
        )
        monkeypatch.setattr(web_app, "_poll_outputs", lambda *args, **kwargs: [])

        manager._run_task(task["id"], _saved_key(), threading.Event())

        assert submitted["workflow_id"] == "987654"
        assert "__rh_meta__" not in submitted["workflow"]
        assert store.task(task["id"])["status"] == "completed"
    finally:
        manager.close()
        store._db.close()
