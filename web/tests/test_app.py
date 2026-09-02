from __future__ import annotations

import base64
import json
import threading
from types import SimpleNamespace
from pathlib import Path

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
        "__rh_meta__": {"workflowId": "123456", "bypassedNodes": ["1"]},
        "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
    }

    analysis = web_app.inspect_workflow(workflow)

    assert analysis["remote_workflow_id"] == "123456"
    assert analysis["bypassed_nodes"] == ["1"]
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


def test_inspect_workflow_finds_resolution_selector_inputs():
    workflow = {
        "16": {
            "class_type": "ResolutionSelector",
            "inputs": {
                "aspect_ratio": "9:16 (Portrait Widescreen)",
                "megapixels": 0.4,
                "multiple": 32,
            },
            "_meta": {"title": "一采分辨率"},
        }
    }

    analysis = web_app.inspect_workflow(workflow)

    assert analysis["resolution_count"] == 1
    assert analysis["resolution_inputs"][0]["id"] == "16"
    assert analysis["resolution_inputs"][0]["title"] == "一采分辨率"
    assert analysis["resolution_inputs"][0]["aspect_ratio"] == "9:16 (Portrait Widescreen)"
    assert analysis["resolution_inputs"][0]["megapixels"] == "0.4"
    assert len(analysis["resolution_inputs"][0]["aspect_ratio_options"]) == 8


def test_normalize_resolution_inputs_validates_range_and_forces_multiple():
    workflow = {
        "16": {
            "class_type": "ResolutionSelector",
            "inputs": {"aspect_ratio": "1:1 (Square)", "megapixels": 0.4, "multiple": 64},
        }
    }

    values = web_app.normalize_resolution_inputs(
        workflow,
        {"16": {"aspect_ratio": "16:9 (Widescreen)", "megapixels": "2.5"}},
    )
    web_app.apply_resolution_inputs(workflow, values)

    assert values == {"16": {"aspect_ratio": "16:9 (Widescreen)", "megapixels": 2.5, "multiple": 32}}
    assert workflow["16"]["inputs"] == {
        "aspect_ratio": "16:9 (Widescreen)",
        "megapixels": 2.5,
        "multiple": 32,
    }
    with pytest.raises(RhCliError) as excinfo:
        web_app.normalize_resolution_inputs(
            workflow,
            {"16": {"aspect_ratio": "16:9 (Widescreen)", "megapixels": "4.1"}},
        )
    assert excinfo.value.code == "INVALID_RESOLUTION"


def test_normalize_random_noise_inputs_rejects_unknown_mode():
    workflow = {"7": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0, "mode": "randomize"}}}

    with pytest.raises(RhCliError) as excinfo:
        web_app.normalize_random_noise_inputs(workflow, {"7": {"seed": "12", "mode": "increment"}})

    assert excinfo.value.code == "INVALID_RANDOM_NOISE"


def test_normalize_bypassed_nodes_rejects_unknown_node():
    workflow = {"1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}}}

    with pytest.raises(RhCliError) as excinfo:
        web_app.normalize_bypassed_nodes(workflow, ["9"])

    assert excinfo.value.code == "INVALID_BYPASS"


def test_apply_bypassed_nodes_removes_nodes_and_direct_output_links():
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
        "2": {"class_type": "ImageScale", "inputs": {"image": ["1", 0], "width": 512}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
    }

    removed = web_app.apply_bypassed_nodes(workflow, {"1"})

    assert removed == ["1"]
    assert "1" not in workflow
    assert "image" not in workflow["2"]["inputs"]
    assert workflow["3"]["inputs"]["images"] == ["2", 0]


def test_key_capacity_matches_requested_tiers():
    assert web_app.key_capacity("PERSONAL") == 3
    assert web_app.key_capacity("SHARED") == 100
    assert web_app.key_capacity("ENTERPRISE_PRO") == 100
    assert web_app.key_capacity("WALLET") == 100


def test_personal_capacity_setting_changes_personal_keys_but_not_shared_keys(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        assert store.personal_capacity() == 3
        assert store.set_personal_capacity(2) == 2
        store.save_keys([_saved_key()])

        assert store.personal_capacity() == 2
        assert store.keys()[0]["capacity"] == 2
        assert web_app.key_capacity("SHARED", 2) == 100
        with pytest.raises(RhCliError) as excinfo:
            store.set_personal_capacity(4)
        assert excinfo.value.code == "INVALID_PERSONAL_CAPACITY"
    finally:
        store._db.close()


def test_action_resources_path_setting_persists_and_validates(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    source = tmp_path / "pose.md"
    source.write_text("## pose\n", encoding="utf-8")
    store = web_app.LocalStore()
    try:
        assert store.action_resources_path() == ""
        assert store.set_action_resources_path(str(source)) == str(source.resolve())
        assert store.action_resources_path() == str(source.resolve())
        with pytest.raises(RhCliError) as excinfo:
            store.set_action_resources_path(str(tmp_path / "missing.md"))
        assert excinfo.value.code == "INVALID_ACTION_RESOURCES_PATH"
    finally:
        store._db.close()


def test_prompt_resource_paths_setting_persists_all_library_sources(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    library = tmp_path / "library.json"
    library.write_text(json.dumps({"version": 1, "blocks": []}), encoding="utf-8")
    sources = {}
    for kind in ("character", "audio", "background", "clothes"):
        source = tmp_path / f"{kind}.md"
        source.write_text(f"## {kind}\n", encoding="utf-8")
        sources[kind] = source

    store = web_app.LocalStore()
    try:
        assert store.set_prompt_library_path(str(library)) == str(library.resolve())
        assert store.prompt_library_path() == str(library.resolve())
        assert store.set_reference_resources_paths({kind: str(path) for kind, path in sources.items()}) == {
            kind: str(path.resolve()) for kind, path in sources.items()
        }
        assert store.reference_resources_paths() == {
            kind: str(path.resolve()) for kind, path in sources.items()
        }
    finally:
        store._db.close()


def test_public_key_masks_secret():
    result = web_app.public_key({"id": "k1", "name": "main", "site": "cn", "api_key": "abcdefgh12345678"})
    assert result["masked_key"] == "abcd••••5678"
    assert "api_key" not in result
    assert result["balance_checked_at"] == 0


def test_managed_accounts_persist_site_and_never_store_credentials(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("我的 AI 账号", "ai")
        assert account["site"] == "ai"
        assert store.get_account(account["id"])["status"] == "login_required"

        updated = store.update_account(
            account["id"],
            {
                "status": "checked_in",
                "status_message": "网站已返回今日登录奖励：100 RH 币",
                "daily_coin": 100,
                "last_checkin_at": 123,
                "token": "must-not-be-written",
            },
        )
        assert web_app.public_account(updated)["daily_coin"] == "100"
        raw = (tmp_path / "data" / "accounts.json").read_text(encoding="utf-8")
        assert "must-not-be-written" not in raw
        assert "password" not in raw.lower()
        assert "token" not in raw.lower()
    finally:
        store._db.close()


def _configure_web_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(web_app, "WORKFLOW_ROOT", tmp_path / "data" / "workflows")
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", tmp_path / "data" / "outputs")
    monkeypatch.setattr(web_app, "KEYS_PATH", tmp_path / "data" / "keys.json")
    monkeypatch.setattr(web_app, "ACCOUNTS_PATH", tmp_path / "data" / "accounts.json")
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


def test_current_account_filters_keys_and_migrates_legacy_prefixes(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        account_172 = store.add_account("172", "cn")
        account_akai = store.add_account("akai", "ai")
        store.save_keys(
            [
                {**_saved_key(), "id": "key_cn", "name": "cn-rh", "site": "cn"},
                {**_saved_key(), "id": "key_cn_wallet", "name": "cn-wallet", "site": "cn"},
                {**_saved_key(), "id": "key_ai", "name": "ai-rh", "site": "ai"},
                {**_saved_key(), "id": "key_ai_wallet", "name": "ai-wallet", "site": "ai"},
            ]
        )

        ownership = {item["name"]: item["account_id"] for item in store.keys()}
        assert ownership["cn-rh"] == account_172["id"]
        assert ownership["cn-wallet"] == account_172["id"]
        assert ownership["ai-rh"] == account_akai["id"]
        assert ownership["ai-wallet"] == account_akai["id"]
        assert store.current_account_id() == account_172["id"]
        assert [item["name"] for item in manager.public_keys(account_172["id"])] == ["cn-rh", "cn-wallet"]

        store.set_current_account(account_akai["id"])
        assert [item["name"] for item in manager.public_keys(account_akai["id"])] == ["ai-rh", "ai-wallet"]
        store.set_current_account(web_app.GENERAL_ACCOUNT_ID)
        assert [item["name"] for item in manager.public_keys(web_app.GENERAL_ACCOUNT_ID)] == [
            "cn-rh", "cn-wallet", "ai-rh", "ai-wallet"
        ]
        assert [item["name"] for item in manager._keys_for_task({"account_id": web_app.GENERAL_ACCOUNT_ID}, store.keys())] == [
            "cn-rh", "cn-wallet", "ai-rh", "ai-wallet"
        ]
    finally:
        manager.close()
        store._db.close()


def test_new_key_is_bound_to_current_account_and_site(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        account = store.add_account("172", "cn")
        monkeypatch.setattr(manager, "check_key", lambda key_id: web_app.public_key(store.get_key(key_id)))
        record = manager.add_key("新 Key", "ai", "new-key-value")
        assert record["account_id"] == account["id"]
        assert record["site"] == "cn"
    finally:
        manager.close()
        store._db.close()


def test_submit_task_rejects_key_from_other_account(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        account_172 = store.add_account("172", "cn")
        account_akai = store.add_account("akai", "ai")
        store.save_keys(
            [
                {**_saved_key(), "id": "key_cn", "name": "cn-rh", "site": "cn", "account_id": account_172["id"]},
                {**_saved_key(), "id": "key_ai", "name": "ai-rh", "site": "ai", "account_id": account_akai["id"]},
            ]
        )
        workflow_id, _, _ = store.save_workflow("demo_api.json", json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}))
        with pytest.raises(RhCliError) as excinfo:
            manager.submit_task(workflow_id, {}, {}, "key_ai", None, "123456")
        assert excinfo.value.code == "KEY_ACCOUNT_MISMATCH"
    finally:
        manager.close()
        store._db.close()


def test_imported_workflow_cannot_be_rebound_to_another_account(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account_172 = store.add_account("172", "cn")
        account_akai = store.add_account("akai", "ai")
        content = json.dumps(
            {
                "1": {"class_type": "SaveImage", "inputs": {}},
                "__rh_meta__": {"accountId": account_akai["id"]},
            }
        )
        with pytest.raises(RhCliError) as excinfo:
            store.save_workflow("akai_api.json", content, account_id=account_172["id"], register=False)
        assert excinfo.value.code == "WORKFLOW_ACCOUNT_MISMATCH"
    finally:
        store._db.close()


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


def test_workflow_library_records_can_be_bound_updated_and_deleted(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("账号 A", "cn")
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        workflow_id, workflow_path, analysis = store.save_workflow(
            "demo_api.json",
            json.dumps(
                {
                    "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
                    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
                }
            ),
            account_id=account["id"],
            remote_workflow_id="123456",
            source_dir=str(source_dir),
        )

        record = store.workflow_record(workflow_id)
        assert record["name"] == "demo_api.json"
        assert record["account_id"] == account["id"]
        assert record["account_name"] == "账号 A"
        assert record["site"] == "cn"
        assert record["remote_workflow_id"] == "123456"
        assert record["source_dir"] == str(source_dir)
        assert record["file_count"] == 1
        assert analysis["file_count"] == 1

        detail = store.workflow_detail(workflow_id)
        assert detail["workflow"]["1"]["class_type"] == "LoadImage"

        updated = store.update_workflow(
            workflow_id,
            {"name": "人物修复.json", "account_id": account["id"], "remote_workflow_id": "654321"},
        )
        assert updated["name"] == "人物修复.json"
        assert updated["remote_workflow_id"] == "654321"
        saved = json.loads(workflow_path.read_text(encoding="utf-8"))
        assert saved["__rh_meta__"] == {"workflowId": "654321", "accountId": account["id"]}

        store.delete_workflow(workflow_id)
        assert not workflow_path.exists()
        assert store.workflows() == []
    finally:
        store._db.close()


def test_library_workflow_input_config_is_saved_and_replayed_in_task_snapshot(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        workflow_id, workflow_path, _ = store.save_workflow(
            "sampler_api.json",
            json.dumps({"2": {"class_type": "KSampler", "inputs": {"steps": 20, "cfg": 7.0}}}),
            remote_workflow_id="987654",
        )
        config = {
            "mode": "manual",
            "items": [
                {"id": "2:steps", "label": "采样步数", "kind": "number", "required": True},
            ],
        }
        updated = store.update_workflow(workflow_id, {"input_config": config})
        assert updated["input_config"]["mode"] == "manual"
        assert updated["input_config"]["items"][0]["id"] == "2:steps"
        assert "input_config" in json.loads((store._workflow_registry_path()).read_text(encoding="utf-8"))["workflows"][0]
        assert json.loads(workflow_path.read_text(encoding="utf-8"))["2"]["inputs"]["steps"] == 20

        task = manager.submit_task(
            workflow_id,
            {},
            {},
            None,
            str(tmp_path / "out"),
            remote_workflow_id="987654",
            custom_inputs={"2:steps": "12"},
        )
        loaded = store.load_task_workflow(task["id"])
        assert loaded["task"]["input_config"]["mode"] == "manual"
        assert loaded["task"]["custom_inputs"] == {"2:steps": 12}
        assert loaded["analysis"]["custom_inputs"][0]["label"] == "采样步数"
        assert loaded["workflow"]["2"]["inputs"]["steps"] == 12
    finally:
        manager.close()
        store._db.close()


def test_task_workflow_copy_is_not_added_to_workflow_library(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        workflow_id, workflow_path, _ = store.save_workflow(
            "task_only_api.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
            register=False,
        )

        assert workflow_path.is_file()
        assert store.workflows() == []
        with pytest.raises(RhCliError) as excinfo:
            store.workflow_record(workflow_id)
        assert excinfo.value.code == "WORKFLOW_NOT_FOUND"
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
        snapshot = Path(tmp_path / "out" / "task_load" / "workflow_api.json")
        assert snapshot.is_file()
        assert loaded["task"]["workflow_snapshot_path"] == str(snapshot.resolve())
    finally:
        store._db.close()


def test_startup_recovery_marks_existing_local_outputs_completed(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    output_dir = tmp_path / "out"
    task_id = "task_recover"
    store = web_app.LocalStore()
    store.create_task(
        {
            "id": task_id,
            "created_at": 1,
            "workflow_path": str(tmp_path / "workflow.json"),
            "workflow_name": "recover.json",
            "files": {},
            "prompts": {},
            "key_id": None,
            "remote_workflow_id": "123456",
            "output_dir": str(output_dir),
        }
    )
    store.update_task(task_id, status="running", remote_task_id="remote-1")
    task_folder = output_dir / task_id
    task_folder.mkdir(parents=True)
    (task_folder / "workflow_api.json").write_text("{}", encoding="utf-8")
    (task_folder / "output_1.png").write_bytes(b"png")
    (task_folder / ".output_2.mp4.part").write_bytes(b"partial")
    store._db.close()

    recovered_store = web_app.LocalStore()
    manager = web_app.TaskManager.__new__(web_app.TaskManager)
    manager.store = recovered_store
    try:
        manager._recover_tasks_on_startup()
        recovered = recovered_store.task(task_id)
        assert recovered["status"] == "completed"
        assert recovered["outputs"][0]["name"] == "output_1.png"
        assert len(recovered["outputs"]) == 1
        assert "本地产物恢复" in recovered["progress"]
    finally:
        recovered_store._db.close()


def test_startup_recovery_queues_remote_poll_when_no_local_output(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    store.create_task(
        {
            "id": "task_remote_recover",
            "created_at": 1,
            "workflow_path": str(tmp_path / "workflow.json"),
            "workflow_name": "recover.json",
            "files": {},
            "prompts": {},
            "key_id": None,
            "remote_workflow_id": "123456",
            "output_dir": str(tmp_path / "out"),
        }
    )
    store.update_task("task_remote_recover", status="running", remote_task_id="remote-2")
    store._db.close()

    recovered_store = web_app.LocalStore()
    manager = web_app.TaskManager.__new__(web_app.TaskManager)
    manager.store = recovered_store
    try:
        manager._recover_tasks_on_startup()
        recovered = recovered_store.task("task_remote_recover")
        assert recovered["status"] == "recovering"
        assert "恢复远程轮询" in recovered["progress"]
    finally:
        recovered_store._db.close()


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
        assert loaded["workflow"]["2"]["inputs"] == {"noise_seed": 789, "mode": "fixed"}
        snapshot = Path(task["output_dir"]) / task["id"] / "workflow_api.json"
        assert snapshot.is_file()
        assert loaded["task"]["workflow_snapshot_path"] == str(snapshot.resolve())
    finally:
        manager.close()
        store._db.close()


def test_workflow_name_stays_clean_after_repeated_export_and_submit(tmp_path, monkeypatch):
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
            workflow_data={"2": {"class_type": "SaveImage", "inputs": {}}},
            workflow_name="184de3c55c53_e2fd13581177_10Eros二采0901_api_modified_api_modified_api.json",
        )

        assert task["workflow_name"] == "10Eros二采0901_api.json"
        assert Path(task["workflow_path"]).name.endswith("_10Eros二采0901_api.json")
        loaded = store.load_task_workflow(task["id"])
        assert loaded["filename"] == "10Eros二采0901_api.json"
    finally:
        manager.close()
        store._db.close()


def test_submit_task_saves_modified_resolution_selector(tmp_path, monkeypatch):
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
            workflow_data={
                "16": {
                    "class_type": "ResolutionSelector",
                    "inputs": {"aspect_ratio": "1:1 (Square)", "megapixels": 0.4, "multiple": 64},
                }
            },
            workflow_name="resolution_api.json",
            resolution={"16": {"aspect_ratio": "21:9 (Ultrawide)", "megapixels": "3.2"}},
        )

        loaded = store.load_task_workflow(task["id"])

        assert loaded["task"]["resolution"] == {
            "16": {"aspect_ratio": "21:9 (Ultrawide)", "megapixels": 3.2, "multiple": 32}
        }
        assert loaded["workflow"]["16"]["inputs"] == {
            "aspect_ratio": "21:9 (Ultrawide)",
            "megapixels": 3.2,
            "multiple": 32,
        }
    finally:
        manager.close()
        store._db.close()


def test_submit_task_bypasses_current_input_values_in_snapshot(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        workflow = {
            "1": {"class_type": "LoadImage", "inputs": {"image": "original.png"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "original prompt"}},
            "3": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1, "mode": "randomize"}},
        }
        task = manager.submit_task(
            "unused",
            {"1:image": str(tmp_path / "missing-current.png")},
            {"2:text": "current prompt"},
            None,
            None,
            remote_workflow_id="123456",
            random_noise={"3": {"seed": "99", "mode": "fixed"}},
            bypassed_nodes=["1", "3"],
            workflow_data=workflow,
            workflow_name="bypass_api.json",
        )

        loaded = store.load_task_workflow(task["id"])
        snapshot = loaded["workflow"]

        assert loaded["task"]["bypassed_nodes"] == ["1", "3"]
        assert snapshot["1"]["inputs"]["image"] == "original.png"
        assert snapshot["2"]["inputs"]["text"] == "current prompt"
        assert snapshot["3"]["inputs"] == {"noise_seed": 1, "mode": "randomize"}
        assert loaded["task"]["files"]["1:image"].endswith("missing-current.png")
    finally:
        manager.close()
        store._db.close()


def test_personal_queue_keeps_fourth_task_until_slot_is_free(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        manager._stop.set()
        manager._wake.set()
        manager._dispatcher.join(timeout=1)
        monkeypatch.setattr(manager._executor, "submit", lambda *args, **kwargs: None)
        store.save_keys([_saved_key()])
        workflow_id, _, _ = store.save_workflow("queue_api.json", json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}))

        task_ids = [
            manager.submit_task(workflow_id, {}, {}, "key_test", None, remote_workflow_id="123456")["id"]
            for _ in range(4)
        ]

        manager._dispatch_once()
        states = {task["id"]: task["status"] for task in store.tasks()}
        assert sum(status == "submitting" for status in states.values()) == 3
        waiting = [task_id for task_id in task_ids if states[task_id] == "queued"]
        assert len(waiting) == 1
        assert "本地等待队列" in store.task(waiting[0])["progress"]
        assert next(task["queue_position"] for task in manager.public_tasks() if task["id"] == waiting[0]) == 1

        manager._active_by_key["key_test"] = 2
        manager._dispatch_once()

        assert store.task(waiting[0])["status"] == "submitting"
    finally:
        manager.close()


def test_public_tasks_records_elapsed_time_from_queue_entry(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        task_id = "task_elapsed"
        store.create_task(
            {
                "id": task_id,
                "created_at": 1_000,
                "workflow_path": str(tmp_path / "workflow.json"),
                "workflow_name": "elapsed_api.json",
                "files": {},
                "prompts": {},
                "key_id": None,
                "output_dir": str(tmp_path / "outputs"),
            }
        )
        store.update_task(task_id, status="completed", completed_at=4_600)

        public_task = manager.public_tasks()[0]

        assert public_task["elapsed_ms"] == 3_600
    finally:
        manager.close()
        store._db.close()


def test_submit_task_records_instance_type(tmp_path, monkeypatch):
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
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
            workflow_name="ultra_api.json",
            instance_type="ultra",
        )

        assert task["instance_type"] == "ultra"
        assert store.task(task["id"])["instance_type"] == "ultra"
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


def test_save_pasted_image_persists_a_local_input_copy(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(web_server, "DATA_ROOT", tmp_path / "data")
    raw = b"clipboard-png"

    result = web_server.save_pasted_image(
        {
            "name": "Screenshot.png",
            "mime": "image/png",
            "data": base64.b64encode(raw).decode("ascii"),
        }
    )

    target = Path(result["path"])
    assert target.parent == tmp_path / "data" / "pasted-inputs"
    assert target.suffix == ".png"
    assert target.read_bytes() == raw
    assert result["preview_url"].startswith("data:image/png;base64,")


def test_save_pasted_image_rejects_non_image_payload(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(web_server, "DATA_ROOT", tmp_path / "data")

    with pytest.raises(RhCliError) as excinfo:
        web_server.save_pasted_image({"mime": "text/plain", "data": "aGVsbG8="})

    assert excinfo.value.code == "INVALID_PASTED_IMAGE"


def test_public_outputs_lists_available_files_and_text(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        task_id = "task_outputs"
        output_dir = tmp_path / "out"
        store.create_task(
            {
                "id": task_id,
                "created_at": 1,
                "workflow_path": str(tmp_path / "workflow.json"),
                "workflow_name": "demo_api.json",
                "files": {},
                "prompts": {},
                "key_id": None,
                "output_dir": str(output_dir),
            }
        )
        task_folder = output_dir / task_id
        task_folder.mkdir(parents=True)
        image = task_folder / "preview.png"
        image.write_bytes(b"png")
        store.update_task(
            task_id,
            status="completed",
            completed_at=2,
            outputs_json=json.dumps(
                [
                    {"kind": "file", "path": str(image), "name": image.name, "mime": "image/png"},
                    {"kind": "text", "node_id": "3", "text": "result text"},
                    {"kind": "file", "path": str(tmp_path / "missing.mp4"), "name": "missing.mp4", "mime": "video/mp4"},
                ],
                ensure_ascii=False,
            ),
        )

        manager = SimpleNamespace(public_tasks=lambda: [store.task(task_id)])
        result = web_app.public_outputs(store, manager)

        assert result["summary"]["total"] == 2
        assert result["summary"]["image"] == 1
        assert result["summary"]["text"] == 1
        assert [item["name"] for item in result["outputs"]] == ["preview.png", "文本输出 · 3"]
        assert result["outputs"][0]["file_index"] == 0
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
            manager.submit_task(workflow_id, {}, {}, None, None, "123456")
        assert excinfo.value.code == "MISSING_INPUT"
    finally:
        manager.close()
        store._db.close()


def test_submit_task_allows_bypassed_required_file_without_local_path(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        workflow_id, _, _ = store.save_workflow(
            "demo_api.json",
            json.dumps({"1": {"class_type": "LoadImage", "inputs": {"image": "original.png"}}}),
        )

        task = manager.submit_task(
            workflow_id,
            {},
            {},
            None,
            None,
            "123456",
            bypassed_nodes=["1"],
        )

        assert task["bypassed_nodes"] == ["1"]
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
                {"workflow_id": workflow_id, "workflow": json.loads(workflow_json), "instance_type": kwargs.get("instance_type")}
            ) or "remote-task-1",
        )
        monkeypatch.setattr(web_app, "_poll_outputs", lambda *args, **kwargs: [])

        manager._run_task(task["id"], _saved_key(), threading.Event())

        assert submitted["workflow_id"] == "987654"
        assert "__rh_meta__" not in submitted["workflow"]
        assert submitted["instance_type"] == "default"
        assert store.task(task["id"])["status"] == "completed"
    finally:
        manager.close()
        store._db.close()


def test_run_task_removes_bypassed_nodes_from_submitted_workflow(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        workflow = {
            "__rh_meta__": {"workflowId": "987654"},
            "1": {"class_type": "LoadImage", "inputs": {"image": "original.png"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "original prompt"}},
            "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        task = manager.submit_task(
            "unused",
            {"1:image": str(tmp_path / "not-uploaded.png")},
            {"2:text": "not-applied prompt"},
            None,
            None,
            bypassed_nodes=["1", "2"],
            workflow_data=workflow,
            workflow_name="bypass_run_api.json",
        )
        submitted = {}
        uploaded = {}

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(web_app, "RhHttpClient", lambda *args, **kwargs: FakeClient())
        monkeypatch.setattr(web_app, "_site_urls", lambda site: ("upload", "create", "outputs"))
        monkeypatch.setattr(
            web_app,
            "_apply_file_args",
            lambda client, workflow, args, upload_url: uploaded.update({"args": list(args)}) or [],
        )
        monkeypatch.setattr(
            web_app,
            "_submit",
            lambda client, api_key, workflow_id, workflow_json, **kwargs: submitted.update(
                {"workflow_id": workflow_id, "workflow": json.loads(workflow_json)}
            ) or "remote-task-bypass",
        )
        monkeypatch.setattr(web_app, "_poll_outputs", lambda *args, **kwargs: [])

        manager._run_task(task["id"], _saved_key(), threading.Event())

        assert uploaded["args"] == []
        assert "1" not in submitted["workflow"]
        assert "2" not in submitted["workflow"]
        assert "images" not in submitted["workflow"]["3"]["inputs"]
        assert store.task(task["id"])["status"] == "completed"
    finally:
        manager.close()
        store._db.close()
