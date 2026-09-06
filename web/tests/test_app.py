from __future__ import annotations

import base64
import json
import threading
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path

import pytest

from web import app as web_app
from web import server as web_server
from rh_cli.errors import RhCliError


def test_public_state_supports_page_scopes_without_changing_full_snapshot():
    class FakeStore:
        def current_account_id(self):
            return ""

        def get_account(self, _account_id):
            return None

        def output_dir(self):
            return "/tmp/output"

        def douyin_cookie_path(self):
            return ""

        def personal_capacity(self):
            return 3

        def api_key_strategy(self):
            return "personal_then_shared"

        def pose_media_import_type(self):
            return "depth"

        def toolbox_codex_command(self):
            return "this must not be exposed"

        def aliyun_translation_settings(self):
            return {}

        def aliyun_vision_settings(self):
            return {}

        def telegram_settings(self):
            return {}

        def accounts(self):
            return []

        def project_folders(self):
            return [{"id": "project", "name": "样片", "path": ""}]

    class FakeManager:
        def public_keys(self, _account_id):
            return [{"id": "key"}]

        def public_tasks(self):
            return [{"id": "task"}]

        def telegram_inbound_workflows(self):
            return [{"id": "image"}]

        def telegram_video_inbound_workflows(self):
            return [{"id": "video"}]

    store = FakeStore()
    manager = FakeManager()
    expected = {
        "workflows": ({"settings", "accounts"}, {"keys", "tasks", "telegram_inbound_workflows", "telegram_video_inbound_workflows"}),
        "prompt": ({"settings"}, {"accounts", "keys", "tasks", "telegram_inbound_workflows", "telegram_video_inbound_workflows"}),
        "outputs": ({"settings"}, {"accounts", "keys", "tasks", "telegram_inbound_workflows", "telegram_video_inbound_workflows"}),
        "submit": ({"settings", "accounts", "keys", "tasks", "projects"}, {"telegram_inbound_workflows", "telegram_video_inbound_workflows"}),
        "settings": ({"settings", "accounts", "keys", "telegram_inbound_workflows", "telegram_video_inbound_workflows"}, {"tasks"}),
    }

    for scope, (required, omitted) in expected.items():
        snapshot = web_app.public_state(store, manager, scope=scope)
        assert required <= snapshot.keys()
        assert not (set(snapshot) & omitted)
        assert "toolbox_codex_command" not in snapshot["settings"]

    full = web_app.public_state(store, manager)
    assert {"settings", "accounts", "keys", "tasks", "telegram_inbound_workflows", "telegram_video_inbound_workflows"} <= full.keys()


def test_public_workflow_name_migrates_legacy_toolbox_labels_for_display():
    assert web_app.public_workflow_name("工具箱 · 本地 Codex 图像生成") == "Codex 图像生成"
    assert web_app.public_workflow_name("工具箱 _ 本地 Codex 图像生成") == "Codex 图像生成"
    assert web_app.public_workflow_name("工具箱 · 深度图处理") == "深度图处理"
    assert web_app.public_workflow_name("workflow_api.json") == "workflow_api.json"


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


def test_api_key_strategy_persists_and_rejects_unknown_values(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        assert store.api_key_strategy() == "personal_then_shared"
        assert store.set_api_key_strategy("shared_only") == "shared_only"
        assert store.api_key_strategy() == "shared_only"
        with pytest.raises(RhCliError) as excinfo:
            store.set_api_key_strategy("anything_else")
        assert excinfo.value.code == "INVALID_API_KEY_STRATEGY"
        assert store.api_key_strategy() == "shared_only"
    finally:
        store._db.close()


def test_pose_media_import_type_defaults_to_depth_and_persists_skeleton(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        assert store.pose_media_import_type() == "depth"
        assert store.set_pose_media_import_type("skeleton") == "skeleton"
        assert store.pose_media_import_type() == "skeleton"
        with pytest.raises(RhCliError) as excinfo:
            store.set_pose_media_import_type("color")
        assert excinfo.value.code == "INVALID_POSE_MEDIA_IMPORT_TYPE"
        assert store.pose_media_import_type() == "skeleton"
    finally:
        store._db.close()


def test_api_key_strategy_prefers_personal_and_falls_back_then_returns(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        personal = {**_saved_key(), "id": "key_personal", "name": "个人 Key", "api_type": "PERSONAL"}
        shared = {**_saved_key(), "id": "key_shared", "name": "共享 Key", "api_type": "SHARED", "capacity": 100}
        store.save_keys([personal, shared])
        keys = store.keys()
        records = {item["id"]: item for item in keys}
        manager._active_by_key["key_personal"] = 2

        assert manager._select_key({}, keys, records)["id"] == "key_personal"

        manager._active_by_key["key_personal"] = 3
        assert manager._select_key({}, keys, records)["id"] == "key_shared"

        store.set_api_key_strategy("personal_only")
        assert manager._select_key({}, keys, records) is None

        store.set_api_key_strategy("shared_only")
        assert manager._select_key({}, keys, records)["id"] == "key_shared"

        store.set_api_key_strategy("personal_then_shared")
        store._db.execute(
            "INSERT INTO remote_queue_cooldowns "
            "(key_id,retry_after,attempts,wait_for_predecessors,probe_task_id,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("key_personal", 0, 1, 1, "", web_app.now_ms()),
        )
        store._db.commit()
        manager._active_by_key["key_personal"] = 1
        assert manager._select_key({}, keys, records)["id"] == "key_shared"
    finally:
        manager.close()
        store._db.close()


def test_local_dispatch_is_fifo_and_queue_positions_match(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    submitted = []
    try:
        manager._stop.set()
        manager._wake.set()
        manager._dispatcher.join(timeout=1)
        monkeypatch.setattr(manager._executor, "submit", lambda fn, *args, **kwargs: submitted.append(args[0]))
        store.set_personal_capacity(1)
        store.save_keys([_saved_key()])
        workflow_id, _, _ = store.save_workflow(
            "fifo_api.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
        )

        task_ids = [
            manager.submit_task(workflow_id, {}, {}, "key_test", None, remote_workflow_id="123456")["id"]
            for _ in range(3)
        ]

        manager._dispatch_once()

        assert submitted == [task_ids[0]]
        assert store.task(task_ids[0])["status"] == "submitting"
        assert store.task(task_ids[1])["status"] == "queued"
        assert store.task(task_ids[2])["status"] == "queued"
        positions = {item["id"]: item["queue_position"] for item in manager.public_tasks()}
        assert positions[task_ids[1]] == 1
        assert positions[task_ids[2]] == 2
    finally:
        manager.close()
        store._db.close()


def test_refresh_balance_reenables_key_after_funds_are_added(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        key = {**_saved_key(), "status": "no_balance", "balance": "0", "coins": "0"}
        store.save_keys([key])
        monkeypatch.setattr(
            manager,
            "_fetch_account_data",
            lambda record: {"remainMoney": "9.75", "remainCoins": "88", "apiType": "PERSONAL"},
        )

        result = manager.refresh_balance("key_test")

        assert result["status"] == "ready"
        assert manager._automatic_candidates(store.keys())[0]["id"] == "key_test"
    finally:
        manager.close()
        store._db.close()


def test_runtime_key_failure_quarantines_key_and_wakes_queue(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        store.save_keys([_saved_key()])

        manager._mark_runtime_key_failure(_saved_key(), "AUTH_FAILED")

        saved = store.get_key("key_test")
        assert saved["status"] == "error"
        assert "重新检测" in saved["status_message"]
        assert manager._automatic_candidates(store.keys()) == []
    finally:
        manager.close()
        store._db.close()


def test_cannot_remove_key_referenced_by_waiting_task(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        manager._stop.set()
        manager._wake.set()
        manager._dispatcher.join(timeout=1)
        store.save_keys([_saved_key()])
        workflow_id, _, _ = store.save_workflow(
            "queued_key_api.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
        )
        manager.submit_task(workflow_id, {}, {}, "key_test", None, remote_workflow_id="123456")

        with pytest.raises(RhCliError) as excinfo:
            manager.remove_key("key_test")

        assert excinfo.value.code == "KEY_IN_QUEUE"
    finally:
        manager.close()
        store._db.close()


def test_telegram_inbound_settings_bind_one_image_workflow(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("入站账号", "ai")
        workflow = {
            "__rh_meta__": {"workflowId": "2075188854994329602"},
            "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
            "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "output"}},
        }
        workflow_id, _, _ = store.save_workflow(
            "Qwen单图编辑.json",
            json.dumps(workflow),
            account_id=account["id"],
            remote_workflow_id="2075188854994329602",
        )
        store._write_json_file({"telegram_bot_token": "123456:secret", "telegram_chat_id": "5468961835"})

        settings = store.set_telegram_inbound_settings(workflow_id, True)

        assert settings["inbound_enabled"] is True
        assert settings["inbound_mode"] == "fixed"
        assert settings["inbound_workflow_id"] == workflow_id
        assert settings["inbound_file_input_id"] == "1:image"
    finally:
        store._db.close()


def test_telegram_inbound_settings_allows_optional_file_inputs(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("入站账号", "ai")
        workflow = {
            "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
            "2": {"class_type": "LoadImage", "inputs": {"image": "reference.png"}},
            "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        workflow_id, _, _ = store.save_workflow(
            "optional-input-api.json",
            json.dumps(workflow),
            account_id=account["id"],
            remote_workflow_id="2075188854994329602",
            input_config={
                "mode": "manual",
                "items": [
                    {"id": "1:image", "kind": "file", "required": True},
                    {"id": "2:image", "kind": "file", "required": False},
                ],
            },
        )
        store._write_json_file({"telegram_bot_token": "123456:secret", "telegram_chat_id": "5468961835"})

        settings = store.set_telegram_inbound_settings(workflow_id, True)

        assert settings["inbound_enabled"] is True
        assert settings["inbound_workflow_id"] == workflow_id
        assert settings["inbound_file_input_id"] == "1:image"
    finally:
        store._db.close()


def test_telegram_video_inbound_settings_bind_one_video_workflow(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("视频入站账号", "ai")
        workflow = {
            "__rh_meta__": {"workflowId": "2075188854994329603"},
            "1": {"class_type": "VHS_LoadVideo", "inputs": {"video": "input.mp4"}},
            "2": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "output"}},
        }
        workflow_id, _, _ = store.save_workflow(
            "视频入站.json",
            json.dumps(workflow),
            account_id=account["id"],
            remote_workflow_id="2075188854994329603",
        )
        store._write_json_file({"telegram_bot_token": "123456:secret", "telegram_chat_id": "5468961835"})

        settings = store.set_telegram_video_inbound_settings(workflow_id, True)

        assert settings["video_inbound_enabled"] is True
        assert settings["video_inbound_workflow_id"] == workflow_id
        assert settings["video_inbound_file_input_id"] == "1:video"
        assert {item["id"] for item in store.telegram_video_inbound_workflows()} == {workflow_id}
        assert store.telegram_inbound_workflows() == []
    finally:
        store._db.close()


def test_telegram_video_inbound_submits_downloaded_social_video(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("视频入站账号", "ai")
        workflow_id, _, _ = store.save_workflow(
            "视频入站.json",
            json.dumps({
                "1": {"class_type": "LoadVideo", "inputs": {"video": "input.mp4"}},
                "2": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "output"}},
                "14": {"class_type": "PrimitiveFloat", "inputs": {"value": 15}},
            }),
            account_id=account["id"],
            remote_workflow_id="123456",
        )
        downloaded = tmp_path / "downloaded.mp4"
        downloaded.write_bytes(b"video")
        monkeypatch.setattr(web_app, "download_social_video", lambda url, data_root: downloaded)
        monkeypatch.setattr(web_app, "_probe_video_duration", lambda path: 8.9376)
        submitted = {}

        class InboundNotifier:
            def message_text(self, update):
                return "https://www.bilibili.com/video/BV1xx"

            def send_message(self, *args, **kwargs):
                return None

        manager = web_app.TaskManager.__new__(web_app.TaskManager)
        manager.store = store
        manager._telegram_notifier = InboundNotifier()
        manager._log_stage = lambda *args, **kwargs: None

        def submit_task(**kwargs):
            submitted.update(kwargs)
            return {"id": "task-video-1", "workflow_name": "视频入站"}

        manager.submit_task = submit_task
        settings = {
            "chat_id": "chat",
            "video_inbound_enabled": True,
            "video_inbound_workflow_id": workflow_id,
            "video_inbound_file_input_id": "1:video",
        }

        task_id = manager._handle_telegram_update(
            {"update_id": 702, "message": {"chat": {"id": "chat"}}},
            settings,
        )

        assert task_id == "task-video-1"
        assert submitted["workflow_id"] == workflow_id
        assert submitted["files"] == {"1:video": str(downloaded)}
        assert submitted["workflow_data"]["14"]["inputs"]["value"] == 8.938
        assert submitted["submission_source"] == "telegram"
        assert submitted["project"]["name"] == "Telegrame"
    finally:
        store._db.close()


def test_telegram_video_inbound_persists_measured_duration_in_task_snapshot(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("视频快照账号", "ai")
        workflow = {
            "1": {"class_type": "LoadVideo", "inputs": {"video": "input.mp4"}},
            "2": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "output"}},
            "14": {"class_type": "PrimitiveFloat", "inputs": {"value": 15}},
        }
        workflow_id, workflow_path, _ = store.save_workflow(
            "视频快照.json",
            json.dumps(workflow),
            account_id=account["id"],
            remote_workflow_id="123456",
        )
        downloaded = tmp_path / "downloaded.mp4"
        downloaded.write_bytes(b"video")
        monkeypatch.setattr(web_app, "download_social_video", lambda url, data_root: downloaded)
        monkeypatch.setattr(web_app, "_probe_video_duration", lambda path: 8.9376)

        class InboundNotifier:
            def message_text(self, update):
                return "https://www.bilibili.com/video/BV1xx"

            def send_message(self, *args, **kwargs):
                return None

        manager = web_app.TaskManager.__new__(web_app.TaskManager)
        manager.store = store
        manager._telegram_notifier = InboundNotifier()
        manager._wake = threading.Event()
        manager._log_stage = lambda *args, **kwargs: None
        settings = {
            "chat_id": "chat",
            "video_inbound_enabled": True,
            "video_inbound_workflow_id": workflow_id,
            "video_inbound_file_input_id": "1:video",
        }

        task_id = manager._handle_telegram_update(
            {"update_id": 703, "message": {"chat": {"id": "chat"}}},
            settings,
        )

        task = store.task(task_id)
        assert task is not None
        snapshot = Path(task["output_dir"]) / task_id / "workflow_api.json"
        assert json.loads(snapshot.read_text(encoding="utf-8"))["14"]["inputs"]["value"] == 8.938
        assert json.loads(workflow_path.read_text(encoding="utf-8"))["14"]["inputs"]["value"] == 15
    finally:
        store._db.close()


def test_telegram_inbound_folder_mode_validates_and_selects_a_random_workflow(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("随机入站账号", "ai")
        folder = store.create_workflow_folder("图片入站轮换")
        workflow_ids = []
        for index in (1, 2):
            workflow = {
                "__rh_meta__": {"workflowId": f"207518885499432960{index}"},
                "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
                "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": f"output-{index}"}},
            }
            workflow_id, _, _ = store.save_workflow(
                f"轮换工作流-{index}.json",
                json.dumps(workflow),
                account_id=account["id"],
                remote_workflow_id=f"207518885499432960{index}",
            )
            store.set_workflow_folder(workflow_id, folder["id"])
            workflow_ids.append(workflow_id)
        empty_folder = store.create_workflow_folder("空的随机文件夹")
        store._write_json_file({"telegram_bot_token": "123456:secret", "telegram_chat_id": "5468961835"})

        settings = store.set_telegram_inbound_settings("", True, mode="folder_random", folder_id=folder["id"])

        assert settings["inbound_enabled"] is True
        assert settings["inbound_mode"] == "folder_random"
        assert settings["inbound_folder_id"] == folder["id"]
        assert settings["inbound_folder_name"] == "图片入站轮换"
        assert {item["id"] for item in store.telegram_inbound_workflows(folder["id"])} == set(workflow_ids)

        manager = web_app.TaskManager.__new__(web_app.TaskManager)
        manager.store = store
        available_folders = manager._telegram_switchable_folders()
        assert [(item["id"], item["workflow_count"]) for item in available_folders] == [(folder["id"], 2)]
        assert empty_folder["id"] not in {item["id"] for item in available_folders}
        monkeypatch.setattr(web_app.random, "choice", lambda candidates: candidates[-1])
        selected = manager._select_telegram_inbound_workflow(settings)
        assert selected["id"] == workflow_ids[1]
    finally:
        store._db.close()


def test_telegram_folder_random_selects_again_for_each_inbound_task(monkeypatch):
    class FolderStore:
        def telegram_inbound_workflows(self, folder_id=""):
            assert folder_id == "wff-images"
            return [
                {"id": "wf-a", "name": "工作流 A"},
                {"id": "wf-b", "name": "工作流 B"},
            ]

    manager = web_app.TaskManager.__new__(web_app.TaskManager)
    manager.store = FolderStore()
    selected_indexes = iter((0, 1))
    monkeypatch.setattr(
        web_app.random,
        "choice",
        lambda candidates: candidates[next(selected_indexes)],
    )
    settings = {"inbound_mode": "folder_random", "inbound_folder_id": "wff-images"}

    assert manager._select_telegram_inbound_workflow(settings)["id"] == "wf-a"
    assert manager._select_telegram_inbound_workflow(settings)["id"] == "wf-b"


def test_telegram_inbound_submits_a_fresh_random_workflow_for_each_message(monkeypatch):
    workflows = {
        "wf-a": {"name": "工作流 A", "remote_workflow_id": "remote-a"},
        "wf-b": {"name": "工作流 B", "remote_workflow_id": "remote-b"},
    }

    class InboundStore:
        def telegram_inbound_workflows(self, folder_id=""):
            assert folder_id == "wff-images"
            return [
                {"id": workflow_id, "name": record["name"], "file_input_id": "1:image"}
                for workflow_id, record in workflows.items()
            ]

        def workflow_detail(self, workflow_id):
            record = workflows[workflow_id]
            return {
                "record": {
                    "name": record["name"],
                    "remote_workflow_id": record["remote_workflow_id"],
                    "account_id": "account-1",
                },
                "workflow": {"1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}}},
            }

    class InboundNotifier:
        def image_file_reference(self, update):
            return {"file_id": str(update["update_id"]), "name": "input.png", "mime": "image/png"}

        def download_image(self, update_id, reference, destination):
            return Path(f"/tmp/telegram-input-{update_id}.png")

        def send_message(self, *args, **kwargs):
            return None

    manager = web_app.TaskManager.__new__(web_app.TaskManager)
    manager.store = InboundStore()
    manager._telegram_notifier = InboundNotifier()
    selected_workflows = []
    submitted_prompt_groups = []

    def submit_task(**kwargs):
        selected_workflows.append(kwargs["workflow_id"])
        submitted_prompt_groups.append(kwargs["prompt_group"])
        return {"id": f"task-{len(selected_workflows)}", "workflow_name": workflows[kwargs["workflow_id"]]["name"]}

    manager.submit_task = submit_task
    manager._log_stage = lambda *args, **kwargs: None
    selected_indexes = iter((0, 1))
    monkeypatch.setattr(
        web_app.random,
        "choice",
        lambda candidates: candidates[next(selected_indexes)],
    )
    settings = {
        "chat_id": "chat",
        "inbound_enabled": True,
        "inbound_mode": "folder_random",
        "inbound_folder_id": "wff-images",
    }

    for update_id in (101, 102):
        assert manager._handle_telegram_update(
            {"update_id": update_id, "message": {"chat": {"id": "chat"}}},
            settings,
        ) == f"task-{update_id - 100}"

    assert selected_workflows == ["wf-a", "wf-b"]
    assert [group["id"] for group in submitted_prompt_groups] == ["telegram-wf-a", "telegram-wf-b"]
    assert all(group["items"] == [] for group in submitted_prompt_groups)


def test_telegram_inbound_saves_selected_workflow_and_prompt_group_to_task_folder(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("Telegram 入站账号", "ai")
        group = {
            "id": "telegram-group",
            "name": "Telegram 提示词",
            "updated_at": 123456,
            "items": [{"instance_id": "item-1", "kind": "text", "text": "保持电影感"}],
        }
        workflow_id, _, _ = store.save_workflow(
            "telegram-inbound.json",
            json.dumps({
                "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
                "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
            }),
            account_id=account["id"],
            remote_workflow_id="123456",
            prompt_group=group,
        )

        class InboundNotifier:
            def image_file_reference(self, update):
                return {"file_id": "telegram-file", "name": "input.png", "mime": "image/png"}

            def download_image(self, update_id, reference, destination):
                path = tmp_path / "telegram-input.png"
                path.write_bytes(b"image")
                return path

            def send_message(self, *args, **kwargs):
                return None

        manager = web_app.TaskManager.__new__(web_app.TaskManager)
        manager.store = store
        manager._telegram_notifier = InboundNotifier()
        manager._wake = threading.Event()
        manager._log_stage = lambda *args, **kwargs: None
        settings = {
            "chat_id": "chat",
            "inbound_enabled": True,
            "inbound_mode": "fixed",
            "inbound_workflow_id": workflow_id,
            "inbound_file_input_id": "1:image",
        }

        task_id = manager._handle_telegram_update(
            {"update_id": 701, "message": {"chat": {"id": "chat"}}},
            settings,
        )

        task = store.task(task_id)
        assert task is not None
        task_folder = Path(task["output_dir"]) / task_id
        workflow_snapshot = task_folder / "workflow_api.json"
        prompt_group_snapshot = task_folder / "prompt_group.json"
        assert workflow_snapshot.is_file()
        assert prompt_group_snapshot.is_file()
        assert json.loads(prompt_group_snapshot.read_text(encoding="utf-8"))["group"] == group
        assert store.load_task_workflow(task_id)["prompt_group"] == group
        assert task["project_name"] == "Telegrame"
        assert task["project_id"] == store.telegram_project()["id"]
        assert store.project_folder(task["project_id"])["name"] == "Telegrame"
    finally:
        store._db.close()


def test_telegram_inbound_folder_mode_rejects_empty_folder(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        store.create_workflow_folder("空入站文件夹")
        folder = store.workflow_folders()[0]
        store._write_json_file({"telegram_bot_token": "123456:secret", "telegram_chat_id": "5468961835"})

        with pytest.raises(RhCliError) as excinfo:
            store.set_telegram_inbound_settings("", True, mode="folder_random", folder_id=folder["id"])

        assert excinfo.value.code == "INVALID_TELEGRAM_INBOUND_FOLDER"
    finally:
        store._db.close()


def test_telegram_task_failure_notification_contains_task_context(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        manager = web_app.TaskManager.__new__(web_app.TaskManager)
        manager.store = store
        manager._wake = threading.Event()
        sent = []

        class FailureNotifier:
            def send_message(self, message):
                sent.append(message)

        class InlineExecutor:
            def submit(self, callback, *args):
                callback(*args)

        manager._telegram_notifier = FailureNotifier()
        manager._telegram_executor = InlineExecutor()
        manager._log_stage = lambda *args, **kwargs: None
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="123456",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
            workflow_name="失败测试.json",
            submission_source="telegram",
        )

        assert task["project_name"] == "Telegrame"
        assert task["project_id"] == store.telegram_project()["id"]

        manager._queue_telegram_task_failure(task["id"], "远程节点执行失败")

        assert sent == [
            "❌ Telegram 入站任务处理失败\n"
            f"工作流：失败测试.json\n"
            f"任务 ID：{task['id']}\n"
            "原因：远程节点执行失败"
        ]
    finally:
        store._db.close()


def test_manual_telegram_upload_rejects_duplicate_while_in_flight():
    output = {"kind": "file", "name": "result.png", "path": "/tmp/result.png"}

    class UploadStore:
        def task(self, task_id):
            return {"id": task_id, "outputs": [output]}

    class UploadNotifier:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def settings(self):
            return {"configured": True}

        def notify_task(self, task_id, outputs, **kwargs):
            self.started.set()
            assert self.release.wait(2)
            return {"status": "sent", "sent": 1, "failed": 0}

    manager = web_app.TaskManager.__new__(web_app.TaskManager)
    manager.store = UploadStore()
    manager._telegram_notifier = UploadNotifier()
    manager._telegram_executor = web_app.ThreadPoolExecutor(max_workers=1)
    manager._telegram_upload_lock = threading.Lock()
    manager._telegram_uploading = set()
    manager._log_stage = lambda *args, **kwargs: None
    result = {}
    error = {}

    def upload_first():
        try:
            result["value"] = manager.upload_task_to_telegram("task-1", 0)
        except Exception as exc:  # pragma: no cover - failure assertion below
            error["value"] = exc

    worker = threading.Thread(target=upload_first)
    worker.start()
    try:
        assert manager._telegram_notifier.started.wait(1)
        with pytest.raises(RhCliError) as excinfo:
            manager.upload_task_to_telegram("task-1", 0)
        assert excinfo.value.code == "TELEGRAM_UPLOAD_IN_PROGRESS"
    finally:
        manager._telegram_notifier.release.set()
        worker.join(timeout=2)
        manager._telegram_executor.shutdown(wait=True)

    assert "value" in result
    assert "value" not in error


def test_telegram_delivery_claim_is_atomic_across_store_connections(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    first = web_app.LocalStore()
    second = web_app.LocalStore()
    try:
        assert first.claim_telegram_delivery("task-1", "output-1", "owner-1", lease_ms=60_000)
        assert not second.claim_telegram_delivery("task-1", "output-1", "owner-2", lease_ms=60_000)
        first.finish_telegram_delivery("task-1", "output-1", "owner-1", "sent")
        assert not second.claim_telegram_delivery("task-1", "output-1", "owner-2", lease_ms=60_000)
    finally:
        first._db.close()
        second._db.close()


def test_telegram_inbound_loop_uses_current_settings_for_each_update():
    class InboundStore:
        def __init__(self):
            self.finished = []

        def claim_telegram_inbound_update(self, update_id):
            return True

        def finish_telegram_inbound_update(self, update_id, status, task_id="", detail=""):
            self.finished.append((update_id, status, task_id, detail))

    class InboundNotifier:
        def __init__(self):
            self.settings_calls = 0

        def settings(self):
            self.settings_calls += 1
            workflow_id = "old-workflow" if self.settings_calls == 1 else "new-workflow"
            return {
                "configured": True,
                "inbound_enabled": True,
                "inbound_workflow_id": workflow_id,
                "chat_id": "chat",
            }

        def poll_updates(self, offset):
            return [{"update_id": 1}]

    manager = web_app.TaskManager.__new__(web_app.TaskManager)
    manager.store = InboundStore()
    manager._telegram_notifier = InboundNotifier()
    manager._stop = threading.Event()
    received_settings = []

    def handle_update(update, settings):
        received_settings.append(settings["inbound_workflow_id"])
        manager._stop.set()
        return "task-1"

    manager._handle_telegram_update = handle_update
    manager._telegram_inbound_loop()

    assert received_settings == ["new-workflow"]
    assert manager.store.finished == [(1, "submitted", "task-1", "")]


def test_telegram_switch_callback_updates_workflow_and_refreshes_menu():
    class SwitchStore:
        def __init__(self):
            self.selected = ""

        def set_telegram_inbound_settings(self, workflow_id, enabled):
            self.selected = workflow_id
            return {}

    class SwitchNotifier:
        def __init__(self):
            self.answers = []
            self.deleted = []
            self.sent = []

        def answer_callback_query(self, callback_id, text="", show_alert=False):
            self.answers.append((callback_id, text, show_alert))

        def settings(self):
            return {"inbound_workflow_id": "wf-new"}

        def delete_message(self, chat_id, message_id):
            self.deleted.append((chat_id, message_id))

        def send_message(self, text, chat_id=None, reply_markup=None):
            self.sent.append((text, chat_id, reply_markup))

    manager = web_app.TaskManager.__new__(web_app.TaskManager)
    manager.store = SwitchStore()
    manager._telegram_notifier = SwitchNotifier()
    manager._telegram_switchable_workflows = lambda: [{"id": "wf-new", "name": "新工作流", "account_name": "账号 A"}]
    update = {
        "callback_query": {
            "id": "callback-1",
            "data": "rh_switch:wf-new",
            "message": {"message_id": 8, "chat": {"id": "chat"}},
        }
    }

    assert manager._handle_telegram_update(update, {"chat_id": "chat"}) == ""
    assert manager.store.selected == "wf-new"
    assert manager._telegram_notifier.answers == [("callback-1", "", False)]
    assert manager._telegram_notifier.deleted == [("chat", 8)]
    assert manager._telegram_notifier.sent == [("已切换到：新工作流", "chat", None)]


def test_telegram_switch_menu_includes_random_folder_and_usable_workflows():
    workflows = [
        {"id": "wf-a", "name": "工作流 A", "account_name": "账号 A"},
        {"id": "wf-b", "name": "工作流 B", "account_name": "账号 B"},
    ]
    folders = [{"id": "wff-images", "name": "图片轮换", "workflow_count": 2}]

    text, reply_markup = web_app.TaskManager._telegram_switch_menu(
        workflows,
        "wf-a",
        folders=folders,
        current_mode="fixed",
    )

    assert "当前：工作流 A" in text
    assert reply_markup == {
        "inline_keyboard": [
            [{"text": "文件夹随机", "callback_data": "rh_switch:folder_random"}],
            [{"text": "✓ 工作流 A · 账号 A", "callback_data": "rh_switch:wf-a"}],
            [{"text": "工作流 B · 账号 B", "callback_data": "rh_switch:wf-b"}],
            [{"text": "取消", "callback_data": "rh_switch:cancel"}],
        ]
    }

    folder_text, folder_markup = web_app.TaskManager._telegram_switch_folder_menu(
        folders,
        "",
    )
    assert "当前：未选择" in folder_text
    assert folder_markup["inline_keyboard"] == [
        [{"text": "图片轮换 · 2 个可用工作流", "callback_data": "rh_switch_folder:wff-images"}],
        [{"text": "← 返回固定工作流", "callback_data": "rh_switch:back"}],
        [{"text": "取消", "callback_data": "rh_switch:cancel"}],
    ]

    _, empty_folder_markup = web_app.TaskManager._telegram_switch_menu(workflows, "wf-a")
    assert empty_folder_markup["inline_keyboard"][0] == [
        {"text": "文件夹随机（暂无可用文件夹）", "callback_data": "rh_switch:folder_random"}
    ]


def test_telegram_switch_cancel_does_not_change_settings():
    class CancelStore:
        def __init__(self):
            self.changed = False

        def set_telegram_inbound_settings(self, *args, **kwargs):
            self.changed = True

    class CancelNotifier:
        def __init__(self):
            self.answers = []
            self.deleted = []

        def answer_callback_query(self, callback_id, text="", show_alert=False):
            self.answers.append((callback_id, text, show_alert))

        def delete_message(self, chat_id, message_id):
            self.deleted.append((chat_id, message_id))

    manager = web_app.TaskManager.__new__(web_app.TaskManager)
    manager.store = CancelStore()
    manager._telegram_notifier = CancelNotifier()
    update = {
        "callback_query": {
            "id": "callback-cancel",
            "data": "rh_switch:cancel",
            "message": {"message_id": 8, "chat": {"id": "chat"}},
        }
    }

    assert manager._handle_telegram_update(update, {"chat_id": "chat"}) == ""
    assert manager.store.changed is False
    assert manager._telegram_notifier.answers == [("callback-cancel", "", False)]
    assert manager._telegram_notifier.deleted == [("chat", 8)]


def test_telegram_switch_callback_selects_random_folder():
    class FolderStore:
        def __init__(self):
            self.selected = None

        def set_telegram_inbound_settings(self, workflow_id, enabled, mode=None, folder_id=""):
            self.selected = (workflow_id, enabled, mode, folder_id)
            return {}

    class FolderNotifier:
        def __init__(self):
            self.answers = []
            self.edits = []
            self.deleted = []
            self.sent = []

        def answer_callback_query(self, callback_id, text="", show_alert=False):
            self.answers.append((callback_id, text, show_alert))

        def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
            self.edits.append((chat_id, message_id, text, reply_markup))

        def delete_message(self, chat_id, message_id):
            self.deleted.append((chat_id, message_id))

        def send_message(self, text, chat_id=None, reply_markup=None):
            self.sent.append((text, chat_id, reply_markup))

    manager = web_app.TaskManager.__new__(web_app.TaskManager)
    manager.store = FolderStore()
    manager._telegram_notifier = FolderNotifier()
    manager._telegram_switchable_workflows = lambda: [
        {"id": "wf-a", "name": "工作流 A", "account_name": "账号 A"}
    ]
    manager._telegram_switchable_folders = lambda: [
        {"id": "wff-images", "name": "图片轮换", "workflow_count": 1}
    ]
    message = {"message_id": 8, "chat": {"id": "chat"}}

    manager._handle_telegram_update(
        {"callback_query": {"id": "callback-folder", "data": "rh_switch:folder_random", "message": message}},
        {"chat_id": "chat"},
    )
    assert manager._telegram_notifier.answers == [("callback-folder", "", False)]
    assert manager._telegram_notifier.edits[0][3]["inline_keyboard"] == [
        [{"text": "图片轮换 · 1 个可用工作流", "callback_data": "rh_switch_folder:wff-images"}],
        [{"text": "← 返回固定工作流", "callback_data": "rh_switch:back"}],
        [{"text": "取消", "callback_data": "rh_switch:cancel"}],
    ]

    manager._handle_telegram_update(
        {"callback_query": {"id": "callback-select", "data": "rh_switch_folder:wff-images", "message": message}},
        {"chat_id": "chat"},
    )

    assert manager.store.selected == ("", True, "folder_random", "wff-images")
    assert manager._telegram_notifier.answers[-1] == ("callback-select", "", False)
    assert manager._telegram_notifier.deleted == [("chat", 8)]
    assert manager._telegram_notifier.sent == [("已切换到文件夹随机：图片轮换", "chat", None)]


def test_action_resources_path_setting_persists_and_validates(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    source = tmp_path / "pose.json"
    source.write_text(json.dumps({"version": 6, "actions": []}), encoding="utf-8")
    store = web_app.LocalStore()
    try:
        assert store.action_resources_path() == ""
        assert store.set_action_resources_path(str(source)) == str(source.resolve())
        assert store.action_resources_path() == str(source.resolve())
        with pytest.raises(RhCliError) as excinfo:
            store.set_action_resources_path(str(tmp_path / "missing.json"))
        assert excinfo.value.code == "INVALID_ACTION_RESOURCES_PATH"
    finally:
        store._db.close()


def test_douyin_cookie_path_setting_persists_and_can_be_cleared(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    cookie = tmp_path / "douyin-cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    store = web_app.LocalStore()
    try:
        assert store.douyin_cookie_path() == ""
        assert store.set_douyin_cookie_path(str(cookie)) == str(cookie.resolve())
        assert store.douyin_cookie_path() == str(cookie.resolve())
        assert store.set_douyin_cookie_path("") == ""
        assert store.douyin_cookie_path() == ""
        with pytest.raises(RhCliError) as excinfo:
            store.set_douyin_cookie_path(str(tmp_path / "missing-cookies.txt"))
        assert excinfo.value.code == "INVALID_DOUYIN_COOKIE_PATH"
    finally:
        store._db.close()


def test_reference_resource_paths_setting_persists_all_library_sources(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    sources = {}
    for kind in ("character", "audio", "background", "clothes"):
        source = tmp_path / f"{kind}.json"
        source.write_text(json.dumps({"version": 6, "references": []}), encoding="utf-8")
        sources[kind] = source

    store = web_app.LocalStore()
    try:
        assert store.set_reference_resources_paths({kind: str(path) for kind, path in sources.items()}) == {
            kind: str(path.resolve()) for kind, path in sources.items()
        }
        assert store.reference_resources_paths() == {
            kind: str(path.resolve()) for kind, path in sources.items()
        }
    finally:
        store._db.close()


def test_prompt_library_path_prefers_resources_index(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    ref_root = tmp_path / "ref"
    prompt_root = ref_root / "prompt"
    prompt_root.mkdir(parents=True)
    indexed_library = prompt_root / "library.json"
    indexed_library.write_text(json.dumps({"version": 1, "blocks": []}), encoding="utf-8")
    (ref_root / "Resources.json").write_text(json.dumps({
        "version": 1,
        "media_root": ".",
        "sources": {"prompt": "prompt/library.json"},
    }), encoding="utf-8")
    legacy_library = tmp_path / "legacy-library.json"
    legacy_library.write_text(json.dumps({"version": 1, "blocks": []}), encoding="utf-8")

    store = web_app.LocalStore()
    try:
        store.set_prompt_library_path(str(legacy_library))
        store.set_media_library_root(str(ref_root))
        assert store.prompt_library_path() == str(indexed_library.resolve())
    finally:
        store._db.close()


def test_media_library_root_setting_replaces_legacy_resource_paths(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    root = tmp_path / "ref"
    root.mkdir()
    store = web_app.LocalStore()
    try:
        assert store.set_media_library_root(str(root)) == str(root.resolve())
        assert store.media_library_root() == str(root.resolve())
        assert store._read_json_file().get("action_resources_path") is None
        assert store._read_json_file().get("reference_resources_paths") is None
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


def test_dashboard_uses_one_balance_key_per_account(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account_cn = store.add_account("中文账号", "cn")
        account_ai = store.add_account("AI 账号", "ai")
        store.save_keys(
            [
                {**_saved_key(), "id": "key_cn_old", "name": "cn-old", "account_id": account_cn["id"], "balance": "1", "coins": "10", "balance_checked_at": 100},
                {**_saved_key(), "id": "key_cn_new", "name": "cn-new", "account_id": account_cn["id"], "balance": "1", "coins": "20", "balance_checked_at": 200},
                {**_saved_key(), "id": "key_ai", "name": "ai-main", "site": "ai", "account_id": account_ai["id"], "balance": "2", "coins": "30", "symbol": "$", "balance_checked_at": 150},
            ]
        )
        manager = SimpleNamespace(public_tasks=lambda: [], public_keys=lambda: store.keys())

        result = web_app.public_dashboard(store, manager, days=1, current_time=web_app.now_ms())

        assert result["balances"]["account_count"] == 2
        assert result["balances"]["key_count"] == 3
        assert result["balances"]["coins"] == "50"
        assert sorted(result["balances"]["money"], key=lambda item: item["site"]) == [
            {"site": "ai", "symbol": "$", "value": "2"},
            {"site": "cn", "symbol": "¥", "value": "1"},
        ]
        assert {item["key_name"] for item in result["balances"]["keys"]} == {"cn-new", "ai-main"}
    finally:
        store._db.close()


def test_dashboard_can_filter_usage_ledger_by_account(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account_cn = store.add_account("中文账号", "cn")
        account_ai = store.add_account("AI 账号", "ai")
        now = web_app.now_ms()
        day_start = datetime.fromtimestamp(now / 1000).replace(hour=0, minute=0, second=0, microsecond=0)
        created_at = int(day_start.timestamp() * 1000) + 1000
        for task_id, account_id, cost in (
            ("task_cn_usage", account_cn["id"], "5"),
            ("task_ai_usage", account_ai["id"], "7"),
        ):
            store.create_task(
                {
                    "id": task_id,
                    "created_at": created_at,
                    "account_id": account_id,
                    "workflow_path": str(tmp_path / f"{task_id}.json"),
                    "workflow_name": f"{task_id}.json",
                    "files": {},
                    "prompts": {},
                    "output_dir": str(tmp_path / "outputs"),
                }
            )
            store.update_task(task_id, cost_type="coins", cost=cost, status="completed", duration="3")
        manager = SimpleNamespace(public_tasks=lambda: [], public_keys=lambda: [])

        all_accounts = web_app.public_dashboard(store, manager, days=1, current_time=now)
        cn_only = web_app.public_dashboard(store, manager, days=1, account_id=account_cn["id"], current_time=now)

        assert all_accounts["account_filter_name"] == "全部账号"
        assert all_accounts["summary"]["coins_spent"] == "12"
        assert all_accounts["summary"]["submissions"] == 2
        assert all_accounts["summary"]["video_seconds"] == "0"
        assert {item["name"] for item in all_accounts["accounts"]} == {"中文账号", "AI 账号"}
        assert cn_only["account_filter"] == account_cn["id"]
        assert cn_only["account_filter_name"] == "中文账号"
        assert cn_only["summary"]["coins_spent"] == "5"
        assert cn_only["summary"]["submissions"] == 1
    finally:
        store._db.close()


def test_dashboard_ranks_registered_workflows_by_output_total_score(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        now = web_app.now_ms()
        workflow = {"1": {"class_type": "SaveImage", "inputs": {}}}
        workflow_a, _, _ = store.save_workflow("workflow-a.json", json.dumps(workflow))
        workflow_b, _, _ = store.save_workflow("workflow-b.json", json.dumps(workflow))
        task_ids = []

        def add_rated_task(task_id, workflow_id, ratings):
            output_dir = tmp_path / "data" / "outputs" / task_id
            output_dir.mkdir(parents=True)
            outputs = []
            for index, rating in enumerate(ratings):
                output_path = output_dir / f"output-{index}.png"
                output_path.write_bytes(b"png")
                outputs.append({"kind": "file", "path": str(output_path), "mime": "image/png", "rating": rating})
            store.create_task(
                {
                    "id": task_id,
                    "created_at": now - 1000,
                    "workflow_path": str(tmp_path / f"{task_id}.json"),
                    "workflow_name": f"{task_id}.json",
                    "registered_workflow_id": workflow_id,
                    "files": {},
                    "prompts": {},
                    "output_dir": str(output_dir),
                }
            )
            store.update_task(
                task_id,
                status="completed",
                completed_at=now,
                outputs_json=json.dumps(outputs, ensure_ascii=False),
            )
            task_ids.append(task_id)

        add_rated_task("task_workflow_a", workflow_a, [4, 5])
        add_rated_task("task_workflow_b", workflow_b, [5])
        add_rated_task("task_unregistered", "", [5])
        manager = SimpleNamespace(
            public_tasks=lambda: [store.task(task_id) for task_id in task_ids],
            public_keys=lambda: [],
        )

        result = web_app.public_dashboard(store, manager, days=1, current_time=now)

        scores = result["workflow_scores"]
        assert scores["registered_count"] == 2
        assert scores["rated_count"] == 2
        assert [item["id"] for item in scores["items"]] == [workflow_a, workflow_b]
        assert scores["items"][0]["total_score"] == 9
        assert scores["items"][0]["rated_output_count"] == 2
        assert scores["items"][0]["average_rating"] == 4.5
        assert scores["items"][0]["run_count"] == 1
    finally:
        store._db.close()


def test_submit_task_records_registered_workflow_id(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        workflow = {"1": {"class_type": "SaveImage", "inputs": {}}}
        workflow_id, _, _ = store.save_workflow(
            "registered.json",
            json.dumps(workflow),
            remote_workflow_id="remote-registered",
        )

        task = manager.submit_task(
            workflow_id,
            {},
            {},
            None,
            None,
            remote_workflow_id="remote-registered",
            workflow_data=workflow,
            workflow_name="registered.json",
        )

        assert task["registered_workflow_id"] == workflow_id
        assert task["task_type"] == "workflow"
        assert store.task(task["id"])["registered_workflow_id"] == workflow_id
    finally:
        manager.close()
        store._db.close()


def test_dashboard_calculates_video_response_time_after_merging_concurrent_tasks(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        now = int(datetime(2026, 9, 4, 0, 4).timestamp() * 1000)
        created_at = now - 240_000
        for index in range(10):
            task_id = f"task_video_response_{index}"
            store.create_task(
                {
                    "id": task_id,
                    "created_at": created_at,
                    "workflow_path": str(tmp_path / f"{task_id}.json"),
                    "workflow_name": "video.json",
                    "files": {},
                    "prompts": {},
                    "output_dir": str(tmp_path / "outputs"),
                }
            )
            store.update_task(
                task_id,
                status="completed",
                started_at=created_at,
                completed_at=now,
                outputs_json=json.dumps(
                    [{
                        "kind": "file",
                        "path": str(tmp_path / "outputs" / task_id / "output.mp4"),
                        "mime": "video/mp4",
                        "duration_seconds": 15,
                    }]
                ),
            )

        result = web_app.public_dashboard(
            store,
            SimpleNamespace(public_tasks=lambda: [], public_keys=lambda: []),
            days=1,
            current_time=now,
        )

        assert result["summary"]["video_task_count"] == 10
        assert result["summary"]["video_seconds"] == "150"
        assert result["summary"]["wall_clock_seconds"] == "240"
        assert result["summary"]["response_seconds_per_video_second"] == "1.6"
        assert store.usage_records()[0]["video_seconds"] == "15"
    finally:
        store._db.close()


def test_dashboard_counts_rolling_telegram_usage_and_money_spend(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("Telegram 账号", "ai")
        now = int(datetime(2026, 9, 4, 0, 4).timestamp() * 1000)
        created_at = int(datetime(2026, 9, 3, 23, 42).timestamp() * 1000)
        task_id = "task_telegram_usage"
        store.create_task(
            {
                "id": task_id,
                "created_at": created_at,
                "account_id": account["id"],
                "dispatch_key_site": "ai",
                "submission_source": "telegram",
                "workflow_path": str(tmp_path / "workflow.json"),
                "workflow_name": "telegram.json",
                "files": {},
                "prompts": {},
                "output_dir": str(tmp_path / "outputs"),
            }
        )
        store.update_task(task_id, status="completed", cost_type="money", cost="1.25", duration="4")
        manager = SimpleNamespace(public_tasks=lambda: [], public_keys=lambda: [])

        result = web_app.public_dashboard(store, manager, days=1, account_id=account["id"], current_time=now)

        assert result["summary"]["submissions"] == 1
        assert result["summary"]["coins_spent"] == "0"
        assert result["summary"]["money_spent"] == [{"site": "ai", "symbol": "$", "value": "1.25"}]
        assert store.usage_records()[0]["site"] == "ai"
    finally:
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
        assert result["api_type"] == "ENTERPRISE_PRO"
        assert result["capacity"] == 100
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


def test_refresh_balances_updates_all_keys_and_reports_partial_failures(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        first = _saved_key()
        second = {**_saved_key(), "id": "key_failed", "name": "失败 Key", "coins": "20"}
        store.save_keys([first, second])

        def fetch(record):
            if record["id"] == "key_failed":
                raise RhCliError("API_ERROR", "余额接口暂时不可用")
            return {"remainMoney": "9.75", "remainCoins": "88"}

        monkeypatch.setattr(manager, "_fetch_account_data", fetch)

        result = manager.refresh_balances()

        assert result["refreshed"] == 1
        assert result["failed"] == 1
        assert [item["id"] for item in result["keys"]] == ["key_test"]
        assert result["errors"] == [{"id": "key_failed", "name": "失败 Key", "message": "余额接口暂时不可用"}]
        assert store.get_key("key_test")["coins"] == "88"
        assert store.get_key("key_failed")["coins"] == "20"
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


def test_local_store_persists_output_rating_and_can_clear_it(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    output_dir = tmp_path / "data" / "outputs" / "task_rating"
    output_dir.mkdir(parents=True)
    output_path = output_dir / "output.mp4"
    output_path.write_bytes(b"video")
    try:
        store.create_task(
            {
                "id": "task_rating",
                "created_at": 1,
                "workflow_path": str(tmp_path / "workflow.json"),
                "workflow_name": "workflow.json",
                "files": {},
                "prompts": {},
                "random_noise": {},
                "remote_workflow_id": "123456",
                "output_dir": str(output_dir),
            }
        )
        store.update_task(
            "task_rating",
            outputs_json=json.dumps(
                [
                    {"kind": "file", "path": str(output_path), "mime": "video/mp4"},
                    {"kind": "text", "text": "finished"},
                ]
            ),
        )

        rated = store.update_output_rating("task_rating", 0, 5)

        assert rated["rating"] == 5
        assert store.task("task_rating")["outputs"][0]["rating"] == 5

        store.update_output_rating("task_rating", 0, 0)

        assert "rating" not in store.task("task_rating")["outputs"][0]
        with pytest.raises(RhCliError) as excinfo:
            store.update_output_rating("task_rating", 0, 6)
        assert excinfo.value.code == "INVALID_OUTPUT_RATING"
    finally:
        store._db.close()


def test_local_store_persists_output_tags_and_public_outputs_counts_them(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    output_dir = tmp_path / "data" / "outputs" / "task_tags"
    output_dir.mkdir(parents=True)
    output_path = output_dir / "case.mp4"
    output_path.write_bytes(b"video")
    try:
        store.create_task(
            {
                "id": "task_tags",
                "created_at": 1,
                "workflow_path": str(tmp_path / "workflow.json"),
                "workflow_name": "workflow.json",
                "files": {},
                "prompts": {},
                "random_noise": {},
                "remote_workflow_id": "123456",
                "output_dir": str(output_dir),
            }
        )
        store.update_task(
            "task_tags",
            outputs_json=json.dumps([{"kind": "file", "path": str(output_path), "mime": "video/mp4"}]),
        )

        tagged = store.update_output_tags("task_tags", 0, ["案例", "案例", "  "])

        assert tagged["tags"] == ["案例"]
        assert store.task("task_tags")["outputs"][0]["tags"] == ["案例"]
        public = web_app.public_outputs(store, SimpleNamespace(public_tasks=lambda: [store.task("task_tags")]))
        assert public["outputs"][0]["tags"] == ["案例"]
        assert public["summary"]["tag_counts"] == {"案例": 1}

        cleared = store.update_output_tags("task_tags", 0, [])

        assert "tags" not in cleared
        assert "tags" not in store.task("task_tags")["outputs"][0]
        with pytest.raises(RhCliError) as excinfo:
            store.update_output_tags("task_tags", 0, "案例")
        assert excinfo.value.code == "INVALID_OUTPUT_TAGS"
    finally:
        store._db.close()


def test_delete_outputs_by_rating_removes_only_matching_outputs_and_preserves_ledger(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    output_dir = tmp_path / "data" / "outputs"
    task_id = "task_bulk_rating_delete"
    task_folder = output_dir / task_id
    task_folder.mkdir(parents=True)
    one_star_path = task_folder / "one-star.png"
    two_star_path = task_folder / "two-star.png"
    one_star_path.write_bytes(b"one")
    two_star_path.write_bytes(b"two")
    try:
        store.create_task(
            {
                "id": task_id,
                "created_at": 1,
                "workflow_path": str(tmp_path / "workflow.json"),
                "workflow_name": "workflow.json",
                "files": {},
                "prompts": {},
                "output_dir": str(output_dir),
            }
        )
        store.update_task(
            task_id,
            status="completed",
            outputs_json=json.dumps(
                [
                    {"kind": "file", "path": str(one_star_path), "rating": 1},
                    {"kind": "file", "path": str(two_star_path), "rating": 2},
                    {"kind": "text", "text": "keep", "rating": 1},
                ]
            ),
        )

        result = store.delete_outputs_by_rating(1)

        assert result == {"deleted": 2, "tasks_updated": 1}
        assert not one_star_path.exists()
        assert two_star_path.exists()
        assert store.task(task_id)["outputs"] == [{"kind": "file", "path": str(two_star_path), "rating": 2}]
        assert store.usage_records()[0]["output_count"] == 3
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
        renamed_path = tmp_path / "data" / "workflows" / f"{workflow_id}.json"
        assert renamed_path == workflow_path
        assert renamed_path.exists()
        saved = json.loads(renamed_path.read_text(encoding="utf-8"))
        assert saved["__rh_meta__"] == {"workflowId": "654321", "accountId": account["id"]}

        replacement = {
            "1": {"class_type": "LoadImage", "inputs": {"image": "edited.png"}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        content_updated = store.update_workflow(workflow_id, {"content": json.dumps(replacement)})
        assert content_updated["file_count"] == 1
        assert content_updated["name"] == "人物修复.json"
        assert content_updated["account_id"] == account["id"]
        saved = json.loads(renamed_path.read_text(encoding="utf-8"))
        assert saved["1"]["inputs"]["image"] == "edited.png"
        assert saved["__rh_meta__"] == {"workflowId": "654321", "accountId": account["id"]}

        with pytest.raises(RhCliError) as excinfo:
            store.update_workflow(workflow_id, {"content": "not json"})
        assert excinfo.value.code == "INVALID_WORKFLOW"

        store.delete_workflow(workflow_id)
        assert not renamed_path.exists()
        assert store.workflows() == []
    finally:
        store._db.close()


def test_workflow_library_saves_and_loads_a_prompt_group_package(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        prompt_group = {
            "id": "group-cinematic",
            "name": "电影镜头",
            "updated_at": 123,
            "items": [
                {"instance_id": "text-1", "kind": "text", "text": "缓慢推近"},
            ],
        }
        workflow_id, workflow_path, _ = store.save_workflow(
            "cinematic_api.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
            prompt_group=prompt_group,
        )

        package_path = web_app.WORKFLOW_ROOT / f"{workflow_id}.prompt_group.json"
        assert workflow_path.exists()
        assert package_path.exists()
        assert json.loads(package_path.read_text(encoding="utf-8")) == prompt_group
        record = store.workflow_record(workflow_id)
        assert record["prompt_group_id"] == prompt_group["id"]
        assert record["prompt_group_name"] == prompt_group["name"]
        assert record["prompt_group_path"] == str(package_path.resolve())
        registry_entry = json.loads(
            store._workflow_registry_entry_path(workflow_id).read_text(encoding="utf-8")
        )
        assert registry_entry["workflow_file"] == f"workflows/{workflow_id}.json"
        assert registry_entry["prompt_group_file"] == f"workflows/{workflow_id}.prompt_group.json"
        assert store.workflow_detail(workflow_id)["prompt_group"] == prompt_group

        store.update_workflow(workflow_id, {"prompt_group": None})
        assert not package_path.exists()
        assert store.workflow_detail(workflow_id)["prompt_group"] is None
        assert store.workflow_record(workflow_id)["prompt_group_id"] == ""

        store.update_workflow(workflow_id, {"prompt_group": prompt_group})
        assert store.workflow_detail(workflow_id)["prompt_group"] == prompt_group
        store.delete_workflow(workflow_id)
        assert not package_path.exists()
    finally:
        store._db.close()


def test_telegram_submission_migrates_legacy_workflow_to_stable_path(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        workflow_id, original_path, _ = store.save_workflow(
            "old-name.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
            remote_workflow_id="123456",
        )
        legacy_path = original_path.with_name(f"{workflow_id}_legacy-name.json")
        original_path.rename(legacy_path)
        registry = store._read_workflow_registry()
        registry[0]["name"] = "telegram-name.json"
        store._write_workflow_registry(registry)

        task = manager.submit_task(
            workflow_id,
            {},
            {},
            None,
            None,
            remote_workflow_id="123456",
            submission_source="telegram",
        )

        expected_path = tmp_path / "data" / "workflows" / f"{workflow_id}.json"
        assert task["workflow_name"] == "telegram-name.json"
        assert Path(task["workflow_path"]) == expected_path.resolve()
        assert expected_path.is_file()
        assert not legacy_path.exists()
    finally:
        manager.close()
        store._db.close()


def test_workflow_folders_persist_membership_and_restore_unclassified(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        workflow_id, _, _ = store.save_workflow(
            "folder-demo.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
        )
        folder = store.create_workflow_folder("视频项目")
        assert folder["workflow_count"] == 0
        assert store.workflow_record(workflow_id)["folder_id"] == ""

        moved = store.update_workflow(workflow_id, {"folder_id": folder["id"]})
        assert moved["folder_id"] == folder["id"]
        assert store.workflow_folders()[0]["workflow_count"] == 1

        restored = store.update_workflow(workflow_id, {"folder_id": ""})
        assert restored["folder_id"] == ""
        assert store.workflow_folders()[0]["workflow_count"] == 0

        store.update_workflow(workflow_id, {"folder_id": folder["id"]})
        store.delete_workflow_folder(folder["id"])
        assert store.workflow_record(workflow_id)["folder_id"] == ""
        assert store.workflow_folders() == []
    finally:
        store._db.close()


def test_workflow_folder_can_be_renamed_without_changing_membership(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        folder = store.create_workflow_folder("视频项目")

        renamed = store.rename_workflow_folder(folder["id"], "首帧项目")

        assert renamed["id"] == folder["id"]
        assert renamed["name"] == "首帧项目"
        assert store.workflow_folders()[0]["name"] == "首帧项目"
    finally:
        store._db.close()


def test_rename_workflow_changes_registry_name_without_moving_json(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        workflow_id, workflow_path, _ = store.save_workflow(
            "original.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
        )

        renamed = store.rename_workflow(workflow_id, "首帧工作流")

        renamed_path = tmp_path / "data" / "workflows" / f"{workflow_id}.json"
        assert renamed["name"] == "首帧工作流.json"
        assert renamed["workflow_path"] == str(renamed_path.resolve())
        assert workflow_path == renamed_path
        assert renamed_path.exists()
        assert store.workflow_record(workflow_id)["name"] == "首帧工作流.json"
    finally:
        store._db.close()


def test_save_workflow_persists_manual_input_config(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        config = {
            "mode": "manual",
            "items": [
                {"id": "2:steps", "label": "采样步数", "kind": "number", "required": True},
            ],
        }
        workflow_id, _, _ = store.save_workflow(
            "sampler_api.json",
            json.dumps({"2": {"class_type": "KSampler", "inputs": {"steps": 20, "cfg": 7.0}}}),
            input_config=config,
        )

        record = store.workflow_record(workflow_id)
        assert record["input_config"]["mode"] == "manual"
        assert record["input_config"]["items"][0]["id"] == "2:steps"
    finally:
        store._db.close()


def test_legacy_workflow_registry_is_readable_and_migrates_on_write(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        legacy_record = {
            "id": "wf_legacy123",
            "name": "legacy.json",
            "account_id": "",
            "site": "",
            "remote_workflow_id": "123",
            "source_dir": "",
            "source": "library",
            "created_at": 1,
            "updated_at": 2,
            "input_config": {"mode": "manual", "items": []},
        }
        registry_path = store._workflow_registry_path()
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps({"workflows": [legacy_record]}), encoding="utf-8")

        assert store._read_workflow_registry() == [legacy_record]
        store._write_workflow_registry([legacy_record])

        index = json.loads(registry_path.read_text(encoding="utf-8"))
        assert index == {
            "version": 2,
            "workflows": [{"id": "wf_legacy123", "file": "workflow-registry/wf_legacy123.json"}],
        }
        entry_path = store._workflow_registry_entry_path("wf_legacy123")
        assert json.loads(entry_path.read_text(encoding="utf-8")) == {
            **legacy_record,
            "workflow_file": "workflows/wf_legacy123.json",
        }
        assert store._read_workflow_registry() == [
            {**legacy_record, "workflow_file": "workflows/wf_legacy123.json"}
        ]
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
        updated = store.update_workflow(workflow_id, {
            "input_config": config,
            "input_defaults": [{"node_id": "2", "field": "steps", "default": 12}],
        })
        assert updated["input_config"]["mode"] == "manual"
        assert updated["input_config"]["items"][0]["id"] == "2:steps"
        registry = json.loads((store._workflow_registry_path()).read_text(encoding="utf-8"))
        assert registry["version"] == 2
        assert registry["workflows"][0]["id"] == workflow_id
        entry_path = store._workflow_registry_entry_path(workflow_id)
        assert "input_config" in json.loads(entry_path.read_text(encoding="utf-8"))
        assert json.loads(workflow_path.read_text(encoding="utf-8"))["2"]["inputs"]["steps"] == 12

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

        assert not workflow_path.exists()
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
        store.save_task_workflow_snapshot(
            {"id": "task_load", "output_dir": str(tmp_path / "out")},
            {"2": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0, "mode": "randomize"}}},
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
    store.update_task(
        task_id,
        outputs_json=json.dumps(
            [{"kind": "file", "path": str(task_folder / "output_1.png"), "name": "output_1.png"}],
            ensure_ascii=False,
        ),
    )
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


def test_startup_recovery_does_not_complete_from_unrecorded_partial_outputs(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    output_dir = tmp_path / "out"
    task_id = "task_partial_recover"
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
    store.update_task(task_id, status="running", remote_task_id="remote-partial")
    task_folder = output_dir / task_id
    task_folder.mkdir(parents=True)
    (task_folder / "workflow_api.json").write_text("{}", encoding="utf-8")
    (task_folder / "output_1.png").write_bytes(b"png")
    store._db.close()

    recovered_store = web_app.LocalStore()
    manager = web_app.TaskManager.__new__(web_app.TaskManager)
    manager.store = recovered_store
    try:
        manager._recover_tasks_on_startup()
        recovered = recovered_store.task(task_id)
        assert recovered["status"] == "recovering"
        assert "记录不完整" in recovered["progress"]
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


def test_submit_task_saves_and_loads_prompt_group_snapshot(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        group = {
            "id": "task-group-test",
            "name": "首帧提示词",
            "updated_at": 123456,
            "items": [{"instance_id": "item-1", "kind": "text", "text": "A cinematic opening shot."}],
        }
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="123456",
            workflow_data={"2": {"class_type": "SaveImage", "inputs": {}}},
            workflow_name="prompt_group_api.json",
            prompt_group=group,
        )

        snapshot = Path(task["output_dir"]) / task["id"] / "prompt_group.json"
        assert snapshot.is_file()
        assert json.loads(snapshot.read_text(encoding="utf-8"))["group"] == group
        loaded = store.load_task_workflow(task["id"])
        assert loaded["prompt_group"] == group
        assert web_app.LocalStore.existing_task_outputs({**task, "outputs": []}) == []
    finally:
        manager.close()
        store._db.close()


def test_submit_task_writes_path_only_replay_manifest(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    input_path = tmp_path / "source.png"
    input_path.write_bytes(b"source")
    group = {
        "id": "task-group-manifest",
        "name": "可复现提示词",
        "updated_at": 123456,
        "items": [{"instance_id": "item-1", "kind": "text", "text": "A reproducible shot."}],
    }
    try:
        task = manager.submit_task(
            "unused",
            {"1:image": str(input_path)},
            {"2:text": "A fixed prompt."},
            None,
            str(tmp_path / "external-output"),
            remote_workflow_id="123456",
            workflow_data={
                "1": {"class_type": "LoadImage", "inputs": {"image": "source.png"}},
                "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "original"}},
                "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
            },
            workflow_name="manifest_api.json",
            prompt_group=group,
            output_prefix="chinatsu-showcase",
        )

        task_folder = Path(task["output_dir"]) / task["id"]
        manifest_path = task_folder / web_app.TASK_MANIFEST_FILENAME
        prompt_group_path = task_folder / web_app.PROMPT_GROUP_SNAPSHOT_FILENAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert task["workflow_snapshot_path"] == str((task_folder / "workflow_api.json").resolve())
        assert task["prompt_group_snapshot_path"] == str(prompt_group_path.resolve())
        assert task["manifest_path"] == str(manifest_path.resolve())
        assert task["output_prefix"] == "chinatsu-showcase"
        assert manifest["execution"]["output_prefix"] == "chinatsu-showcase"
        assert manifest["inputs"] == {
            "files": {"1:image": str(input_path)},
            "prompts": {"2:text": "A fixed prompt."},
            "policy": "paths-only",
        }
        assert manifest["workflow"]["snapshot_path"] == task["workflow_snapshot_path"]
        assert manifest["prompt_group"]["snapshot_path"] == task["prompt_group_snapshot_path"]
        assert not (task_folder / "inputs").exists()
        assert {path.name for path in task_folder.iterdir()} == {
            "workflow_api.json",
            "prompt_group.json",
            "manifest.json",
        }
    finally:
        manager.close()
        store._db.close()


def test_download_outputs_uses_task_output_prefix_and_keeps_default_fallback(tmp_path):
    class FakeClient:
        def download(self, url, target):
            Path(target).write_bytes(str(url).encode("utf-8"))

    class DefaultFakeClient:
        def download(self, url, target):
            Path(target).write_bytes(b"output")

    prefixed_folder = tmp_path / "prefixed"
    prefixed_folder.mkdir()
    saved = web_app.TaskManager._download_outputs(
        FakeClient(),
        [
            {"url": "video-url", "fileType": "mp4"},
            {"url": "image-url", "fileType": "png"},
        ],
        prefixed_folder,
        "../chinatsu showcase",
    )

    assert [item["name"] for item in saved] == ["chinatsu showcase_1.mp4", "chinatsu showcase_2.png"]
    assert (prefixed_folder / "chinatsu showcase_1.mp4").read_bytes() == b"video-url"

    default_folder = tmp_path / "default"
    default_folder.mkdir()
    default_saved = web_app.TaskManager._download_outputs(
        DefaultFakeClient(),
        [{"url": "video-url", "fileType": "mp4"}],
        default_folder,
    )
    assert default_saved[0]["name"] == "output_1.mp4"


def test_submit_task_records_project_in_task_and_manifest(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    project_root = tmp_path / "VideoMake" / "projects" / "chinatsu-showcase"
    group = {
        "id": "task-group-project",
        "name": "项目提示词",
        "updated_at": 123456,
        "items": [{"instance_id": "item-1", "kind": "text", "text": "A project shot."}],
    }
    try:
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            str(project_root / "output"),
            remote_workflow_id="123456",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
            workflow_name="project_api.json",
            prompt_group=group,
        )

        expected_path = project_root.resolve()
        assert task["project_name"] == "chinatsu-showcase"
        assert task["project_path"] == str(expected_path)
        assert task["project_id"].startswith("project_")
        manifest = json.loads((Path(task["manifest_path"])).read_text(encoding="utf-8"))
        assert manifest["project"] == {
            "id": task["project_id"],
            "name": "chinatsu-showcase",
            "path": str(expected_path),
        }
    finally:
        manager.close()
        store._db.close()


def test_legacy_task_project_is_backfilled_from_existing_project_path(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    project_root = tmp_path / "VideoMake" / "projects" / "legacy-showcase"
    output_dir = project_root / "output"
    store = web_app.LocalStore()
    store.create_task(
        {
            "id": "task_legacy_project",
            "created_at": 1,
            "workflow_path": str(tmp_path / "workflow.json"),
            "workflow_name": "legacy.json",
            "files": {},
            "prompts": {},
            "key_id": None,
            "remote_workflow_id": "123456",
            "output_dir": str(output_dir),
        }
    )
    store._db.close()

    recovered_store = web_app.LocalStore()
    try:
        task = recovered_store.task("task_legacy_project")
        assert task["project_name"] == "legacy-showcase"
        assert task["project_path"] == str(project_root.resolve())
        assert task["project_id"].startswith("project_")
    finally:
        recovered_store._db.close()


def test_telegram_tasks_are_backfilled_and_new_direct_tasks_use_telegrame_project(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    store.create_task(
        {
            "id": "task_legacy_telegram_project",
            "created_at": 1,
            "workflow_path": str(tmp_path / "workflow.json"),
            "workflow_name": "legacy-telegram.json",
            "files": {},
            "prompts": {},
            "key_id": None,
            "remote_workflow_id": "123456",
            "output_dir": str(tmp_path / "old-project" / "output"),
            "project_id": "project_old",
            "project_name": "旧项目",
            "project_path": str(tmp_path / "old-project"),
        }
    )
    store.create_task(
        {
            "id": "task_legacy_telegram_input_path",
            "created_at": 2,
            "workflow_path": str(tmp_path / "workflow.json"),
            "workflow_name": "legacy-telegram-input.json",
            "files": {"13:image": str(tmp_path / "telegram-inputs" / "telegram-image.jpg")},
            "prompts": {},
            "key_id": None,
            "remote_workflow_id": "123456",
            "output_dir": str(tmp_path / "old-project" / "output"),
            "project_id": "project_old",
            "project_name": "旧项目",
            "project_path": str(tmp_path / "old-project"),
        }
    )
    store._db.execute(
        "UPDATE tasks SET stage_logs_json=? WHERE id=?",
        (json.dumps([{"stage": "telegram", "message": "已从 Telegram 接收图片并提交工作流"}], ensure_ascii=False), "task_legacy_telegram_project"),
    )
    store._db.commit()
    store._db.close()

    recovered_store = web_app.LocalStore()
    try:
        legacy = recovered_store.task("task_legacy_telegram_project")
        assert legacy["project_name"] == "Telegrame"
        assert legacy["project_id"] == recovered_store.telegram_project()["id"]
        assert recovered_store.project_folder(legacy["project_id"])["name"] == "Telegrame"
        legacy_input = recovered_store.task("task_legacy_telegram_input_path")
        assert legacy_input["submission_source"] == "telegram"
        assert legacy_input["project_name"] == "Telegrame"
        assert legacy_input["project_id"] == recovered_store.telegram_project()["id"]

        recovered_store.create_task(
            {
                "id": "task_direct_telegram_project",
                "created_at": 2,
                "workflow_path": str(tmp_path / "workflow.json"),
                "workflow_name": "direct-telegram.json",
                "files": {},
                "prompts": {},
                "key_id": None,
                "remote_workflow_id": "123456",
                "output_dir": str(tmp_path / "another-project" / "output"),
                "submission_source": "telegram",
                "project_id": "project_other",
                "project_name": "另一个项目",
            }
        )
        direct = recovered_store.task("task_direct_telegram_project")
        assert direct["project_name"] == "Telegrame"
        assert direct["project_id"] == recovered_store.telegram_project()["id"]
    finally:
        recovered_store._db.close()


def test_task_project_reclassification_updates_manifest_without_moving_media(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    group = {
        "id": "task-group-reclassify",
        "name": "归类提示词",
        "updated_at": 123456,
        "items": [{"instance_id": "item-1", "kind": "text", "text": "A classified shot."}],
    }
    try:
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            str(tmp_path / "output"),
            remote_workflow_id="123456",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
            workflow_name="reclassify_api.json",
            prompt_group=group,
        )
        task_folder = Path(task["output_dir"]) / task["id"]
        media_path = task_folder / "result.png"
        media_path.write_bytes(b"media")

        updated = store.set_task_project(
            task["id"],
            {"project_id": "project_manual", "project_name": "手动项目", "project_path": str(tmp_path / "manual-project")},
        )

        assert updated["project_id"] == "project_manual"
        assert updated["project_name"] == "手动项目"
        assert media_path.read_bytes() == b"media"
        manifest = json.loads(Path(updated["manifest_path"]).read_text(encoding="utf-8"))
        assert manifest["project"] == {
            "id": "project_manual",
            "name": "手动项目",
            "path": str((tmp_path / "manual-project").resolve()),
        }
    finally:
        manager.close()
        store._db.close()


def test_project_folders_can_be_created_renamed_and_deleted_without_moving_media(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    group = {
        "id": "task-group-project-folder",
        "name": "项目文件夹提示词",
        "updated_at": 123456,
        "items": [],
    }
    try:
        project = store.create_project_folder("角色样片")
        assert store.project_folders() == [{**project, "task_count": 0}]

        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            str(tmp_path / "output"),
            remote_workflow_id="123456",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
            workflow_name="project-folder_api.json",
            prompt_group=group,
        )
        task_folder = Path(task["output_dir"]) / task["id"]
        media_path = task_folder / "result.png"
        media_path.write_bytes(b"media")
        store.set_task_project(task["id"], {"project_id": project["id"]})

        renamed = store.rename_project_folder(project["id"], "角色展示")
        assert renamed["name"] == "角色展示"
        assert store.task(task["id"])["project_name"] == "角色展示"
        manifest = json.loads(Path(task["manifest_path"]).read_text(encoding="utf-8"))
        assert manifest["project"]["name"] == "角色展示"

        deleted = store.delete_project_folder(project["id"])
        assert deleted["affected_task_count"] == 1
        cleared = store.task(task["id"])
        assert cleared["project_id"] == ""
        assert cleared["project_inference_disabled"] == 1
        assert media_path.read_bytes() == b"media"
        manifest = json.loads(Path(task["manifest_path"]).read_text(encoding="utf-8"))
        assert manifest["project"] == {"id": "", "name": "", "path": ""}
    finally:
        manager.close()
        store._db.close()


def test_deleted_inferred_project_is_not_restored_on_next_startup(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    project_root = tmp_path / "VideoMake" / "projects" / "delete-me"
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            str(project_root / "output"),
            remote_workflow_id="123456",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
            workflow_name="delete-me_api.json",
        )
        store.delete_project_folder(task["project_id"])
    finally:
        manager.close()
        store._db.close()

    recovered_store = web_app.LocalStore()
    try:
        recovered = recovered_store.task(task["id"])
        assert recovered["project_id"] == ""
        assert recovered["project_name"] == ""
        assert recovered["project_inference_disabled"] == 1
    finally:
        recovered_store._db.close()


def test_load_task_workflow_backfills_replay_paths_and_prefers_registered_workflow(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    group = {
        "id": "task-group-legacy",
        "name": "旧任务提示词",
        "updated_at": 123456,
        "items": [{"instance_id": "item-1", "kind": "text", "text": "Legacy shot."}],
    }
    try:
        registered_id, _, _ = store.save_workflow(
            "registered.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
        )
        temporary_id, _, _ = store.save_workflow(
            "temporary.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
            register=False,
        )
        assert temporary_id != registered_id
        temporary_path = tmp_path / "temporary.json"
        temporary_path.write_text(
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
            encoding="utf-8",
        )
        task = {
            "id": "task_legacy_replay",
            "created_at": 1,
            "workflow_path": str(temporary_path),
            "workflow_name": "temporary.json",
            "registered_workflow_id": registered_id,
            "files": {},
            "prompts": {},
            "key_id": None,
            "remote_workflow_id": "123456",
            "output_dir": str(tmp_path / "external-output"),
        }
        store.create_task(task)
        store.save_task_workflow_snapshot(
            task,
            {"1": {"class_type": "SaveImage", "inputs": {}}},
        )
        store.save_task_prompt_group_snapshot(task, group)

        loaded = store.load_task_workflow(task["id"])
        saved_task = loaded["task"]
        task_folder = Path(task["output_dir"]) / task["id"]

        assert loaded["workflow_id"] == registered_id
        assert loaded["prompt_group"] == group
        assert saved_task["workflow_snapshot_path"] == str((task_folder / "workflow_api.json").resolve())
        assert saved_task["prompt_group_snapshot_path"] == str((task_folder / "prompt_group.json").resolve())
        assert saved_task["manifest_path"] == str((task_folder / "manifest.json").resolve())
        assert (task_folder / "manifest.json").is_file()
    finally:
        store._db.close()


def test_submit_task_uses_optional_file_defaults_when_not_overridden(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    required_file = tmp_path / "incoming.png"
    optional_file = tmp_path / "reference.png"
    required_file.write_bytes(b"incoming")
    optional_file.write_bytes(b"reference")
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "incoming.png"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": str(optional_file)}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    try:
        workflow_id, workflow_path, _ = store.save_workflow(
            "optional-default-api.json",
            json.dumps(workflow),
            remote_workflow_id="123456",
            input_config={
                "mode": "manual",
                "items": [
                    {"id": "1:image", "kind": "file", "required": True},
                    {"id": "2:image", "kind": "file", "required": False},
                ],
            },
        )

        task = manager.submit_task(
            workflow_id,
            {"1:image": str(required_file)},
            {},
            None,
            None,
            remote_workflow_id="123456",
            submission_source="telegram",
        )

        assert task["files"] == {
            "1:image": str(required_file),
            "2:image": str(optional_file.resolve()),
        }
        assert task["submission_source"] == "telegram"
        assert Path(workflow_path).is_file()
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
        loaded = store.load_task_workflow(task["id"])
        assert Path(task["workflow_path"]).name == "workflow_api.json"
        assert task["local_workflow_id"] == loaded["workflow_id"]
        assert not (web_app.WORKFLOW_ROOT / f"{task['local_workflow_id']}.json").exists()
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
        active = [task_id for task_id in task_ids if store.task(task_id)["status"] == "submitting"]
        store.update_task(active[-1], status="completed", completed_at=web_app.now_ms())
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


@pytest.mark.parametrize(
    ("initial_instance_type", "expected_instance_types"),
    [
        ("default", ["default", "plus"]),
        ("plus", ["plus", "plus"]),
    ],
)
def test_run_task_retries_805_once_with_plus_instance(
    tmp_path, monkeypatch, initial_instance_type, expected_instance_types,
):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        manager._stop.set()
        manager._wake.set()
        manager._dispatcher.join(timeout=1)
        key = _saved_key()
        store.save_keys([key])
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="987654",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
            instance_type=initial_instance_type,
        )
        submitted_instance_types = []
        poll_calls = 0

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_submit(*args, **kwargs):
            submitted_instance_types.append(kwargs["instance_type"])
            return f"remote-task-{len(submitted_instance_types)}"

        def fake_poll(*args, **kwargs):
            nonlocal poll_calls
            poll_calls += 1
            if poll_calls == 1:
                raise RhCliError(
                    "TASK_FAILED",
                    "任务执行失败：显存不足",
                    detail={"code": 805, "msg": "out of memory"},
                )
            return []

        monkeypatch.setattr(web_app, "RhHttpClient", lambda *args, **kwargs: FakeClient())
        monkeypatch.setattr(web_app, "_site_urls", lambda site: ("upload", "create", "outputs"))
        monkeypatch.setattr(web_app, "_submit", fake_submit)
        monkeypatch.setattr(web_app, "_poll_outputs", fake_poll)

        manager._run_task(task["id"], key, threading.Event())

        finished = store.task(task["id"])
        assert submitted_instance_types == expected_instance_types
        assert poll_calls == 2
        assert finished["status"] == "completed"
        assert finished["instance_type"] == "plus"
        assert any("805" in item["message"] for item in finished["stage_logs"])
    finally:
        manager.close()
        store._db.close()


def test_run_task_does_not_retry_805_more_than_once(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        manager._stop.set()
        manager._wake.set()
        manager._dispatcher.join(timeout=1)
        key = _saved_key()
        store.save_keys([key])
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="987654",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
            instance_type="plus",
        )
        submitted_instance_types = []

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
            lambda *args, **kwargs: submitted_instance_types.append(kwargs["instance_type"]) or "remote-task",
        )
        monkeypatch.setattr(
            web_app,
            "_poll_outputs",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RhCliError("TASK_FAILED", "任务执行失败：显存不足", detail={"code": 805})
            ),
        )

        manager._run_task(task["id"], key, threading.Event())

        failed = store.task(task["id"])
        assert submitted_instance_types == ["plus", "plus"]
        assert failed["status"] == "failed"
        assert failed["error_detail"]["code"] == "TASK_FAILED"
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
        registered_workflow_id, _, _ = store.save_workflow(
            "registered-output.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
        )
        output_dir = tmp_path / "out"
        store.create_task(
            {
                "id": task_id,
                "created_at": 1,
                "workflow_path": str(tmp_path / "workflow.json"),
                "workflow_name": "demo_api.json",
                "registered_workflow_id": registered_workflow_id,
                "account_id": "account-output",
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
        assert result["summary"]["rating_counts"]["unrated"] == 2
        assert [item["name"] for item in result["outputs"]] == ["preview.png", "文本输出 · 3"]
        assert result["outputs"][0]["file_index"] == 0
        assert {item["registered_workflow_id"] for item in result["outputs"]} == {registered_workflow_id}
        assert {item["account_id"] for item in result["outputs"]} == {"account-output"}
    finally:
        store._db.close()


def test_usage_ledger_survives_task_deletion_and_dashboard_reads_it(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        now = web_app.now_ms()
        day_start = datetime.fromtimestamp(now / 1000).replace(hour=0, minute=0, second=0, microsecond=0)
        created_at = int(day_start.timestamp() * 1000) + 1000
        task_id = "task_usage_ledger"
        store.create_task(
            {
                "id": task_id,
                "created_at": created_at,
                "workflow_path": str(tmp_path / "workflow.json"),
                "workflow_name": "usage.json",
                "files": {},
                "prompts": {},
                "output_dir": str(tmp_path / "outputs"),
            }
        )
        store.update_task(
            task_id,
            status="completed",
            started_at=created_at + 1000,
            completed_at=created_at + 9000,
            cost_type="coins",
            cost="12",
            duration="8",
            outputs_json=json.dumps([{"kind": "text", "text": "done"}], ensure_ascii=False),
        )
        manager = SimpleNamespace(public_tasks=lambda: [store.task(task_id)], public_keys=lambda: [])

        result = web_app.public_dashboard(store, manager, days=1, current_time=now)

        assert result["source"]["type"] == "usage_records"
        assert result["summary"]["coins_spent"] == "12"
        assert result["summary"]["submissions"] == 1
        assert result["summary"]["processing_seconds"] == "8"
        assert result["summary"]["outputs"] == 1
        assert result["recent"][0]["task_available"] is True

        store.delete_task(task_id)
        manager.public_tasks = lambda: []
        result_after_delete = web_app.public_dashboard(store, manager, days=1, current_time=now)

        assert result_after_delete["summary"]["coins_spent"] == "12"
        assert result_after_delete["summary"]["submissions"] == 1
        assert result_after_delete["recent"][0]["task_available"] is False
    finally:
        store._db.close()


def test_delete_task_removes_task_output_folder_and_database_record(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    task_id = "task_delete_outputs"
    output_dir = tmp_path / "out"
    task_folder = output_dir / task_id
    try:
        store.create_task(
            {
                "id": task_id,
                "created_at": 1,
                "workflow_path": str(tmp_path / "workflow.json"),
                "workflow_name": "demo.json",
                "files": {},
                "prompts": {},
                "key_id": None,
                "output_dir": str(output_dir),
            }
        )
        store.update_task(task_id, status="completed", completed_at=2)
        task_folder.mkdir(parents=True)
        (task_folder / "output.mp4").write_bytes(b"video")
        (task_folder / "workflow_api.json").write_text("{}", encoding="utf-8")

        manager.delete_task(task_id)

        assert not task_folder.exists()
        assert store.task(task_id) is None
    finally:
        manager.close()
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


def test_run_task_quarantines_key_after_auth_failure(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        manager._stop.set()
        manager._wake.set()
        manager._dispatcher.join(timeout=1)
        key = _saved_key()
        store.save_keys([key])
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="987654",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
            submission_source="telegram",
        )
        failure_notifications = []
        manager._queue_telegram_task_failure = lambda task_id, message: failure_notifications.append((task_id, message))

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
            lambda *args, **kwargs: (_ for _ in ()).throw(RhCliError("AUTH_FAILED", "invalid api key")),
        )

        manager._run_task(task["id"], key, threading.Event())

        assert store.get_key("key_test")["status"] == "error"
        assert manager._automatic_candidates(store.keys()) == []
        assert store.task(task["id"])["status"] == "failed"
        assert failure_notifications == [(task["id"], "invalid api key")]
    finally:
        manager.close()
        store._db.close()


def test_run_task_requeues_remote_queue_full_without_holding_key_slot(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        manager._stop.set()
        manager._wake.set()
        manager._dispatcher.join(timeout=1)
        key = _saved_key()
        store.save_keys([key])
        predecessor = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="987654",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
        )
        assert store.claim_task_slot(
            predecessor["id"], key, capacity=3, worker_capacity=100, recovery=False,
        )
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="987654",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
        )

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
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RhCliError("REMOTE_QUEUE_FULL", "远程队列已满")
            ),
        )

        manager._run_task(task["id"], key, threading.Event(), automatic_dispatch=True)

        requeued = store.task(task["id"])
        assert requeued["status"] == "queued"
        assert requeued["key_id"] is None
        assert "等待 1 个前序任务完成后再提交" in requeued["progress"]
        assert manager._active_by_key["key_test"] == 0
        state = store.remote_queue_states()["key_test"]
        assert state["attempts"] == 1
        assert state["retry_after"] == 0
        assert state["wait_for_predecessors"] is True
        assert not store.claim_task_slot(
            task["id"], key, capacity=3, worker_capacity=100, recovery=False,
        )
        store.update_task(predecessor["id"], status="completed", progress="已完成")
        assert store.claim_task_slot(
            task["id"], key, capacity=3, worker_capacity=100, recovery=False,
        )
        assert store.remote_queue_states()["key_test"]["probe_task_id"] == task["id"]
    finally:
        manager.close()
        store._db.close()


@pytest.mark.parametrize(
    ("api_type", "capacity"),
    [("NORMAL", 3), ("SHARED", 100)],
)
def test_remote_queue_gate_is_shared_across_local_store_processes(
    tmp_path, monkeypatch, api_type, capacity,
):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    mirror = None
    try:
        manager._stop.set()
        manager._wake.set()
        manager._dispatcher.join(timeout=1)
        key = {**_saved_key(), "api_type": api_type, "capacity": capacity}
        store.save_keys([key])
        # A second live frontend opens the database before either one starts
        # work. (LocalStore startup intentionally recovers stale work.)
        mirror = web_app.LocalStore()
        predecessor = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="987654",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
        )
        assert store.claim_task_slot(
            predecessor["id"], key, capacity=capacity, worker_capacity=100, recovery=False,
        )
        task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="987654",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
        )
        assert store.claim_task_slot(task["id"], key, capacity=capacity, worker_capacity=100, recovery=False)
        delay, attempts = store.defer_task_for_remote_queue(
            task["id"], key["id"], automatic_dispatch=False,
        )
        assert (delay, attempts) == (0, 1)

        state = mirror.remote_queue_states()[key["id"]]
        assert state["wait_for_predecessors"] is True
        assert state["retry_after"] == 0
        assert not mirror.claim_task_slot(task["id"], key, capacity=capacity, worker_capacity=100, recovery=False)

        store.update_task(predecessor["id"], status="completed", progress="已完成")
        assert mirror.claim_task_slot(task["id"], key, capacity=capacity, worker_capacity=100, recovery=False)
        probe = mirror.remote_queue_states()[key["id"]]
        assert probe["probe_task_id"] == task["id"]

        next_task = manager.submit_task(
            "unused",
            {},
            {},
            None,
            None,
            remote_workflow_id="987654",
            workflow_data={"1": {"class_type": "SaveImage", "inputs": {}}},
        )
        assert not store.claim_task_slot(next_task["id"], key, capacity=capacity, worker_capacity=100, recovery=False)

        # A process crash can leave the probe row behind. Once that task is no
        # longer submitting, the next queued task must clear the stale probe
        # instead of leaving the Key blocked forever.
        mirror.update_task(task["id"], status="interrupted", progress="进程已停止")
        records = {key["id"]: key}
        assert manager._select_key(next_task, [key], records)["id"] == key["id"]
        assert store.claim_task_slot(next_task["id"], key, capacity=capacity, worker_capacity=100, recovery=False)
    finally:
        if mirror is not None:
            mirror._db.close()
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
