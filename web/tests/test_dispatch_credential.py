from __future__ import annotations

import json

from web import app as web_app


def _configure_web_paths(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setattr(web_app, "DATA_ROOT", data_root)
    monkeypatch.setattr(web_app, "WORKFLOW_ROOT", data_root / "workflows")
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", data_root / "outputs")
    monkeypatch.setattr(web_app, "KEYS_PATH", data_root / "keys.json")
    monkeypatch.setattr(web_app, "DB_PATH", data_root / "tasks.sqlite3")
    monkeypatch.setattr(web_app, "default_local_output_dir", lambda: data_root / "outputs")


def test_task_keeps_dispatch_credential_snapshot(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        manager._stop.set()
        manager._wake.set()
        manager._dispatcher.join(timeout=1)
        monkeypatch.setattr(manager._executor, "submit", lambda *args, **kwargs: None)
        store.save_keys(
            [
                {
                    "id": "key_dispatch",
                    "name": "发布账号",
                    "site": "ai",
                    "api_key": "abcdefgh12345678",
                    "status": "ready",
                    "status_message": "检测成功",
                    "api_type": "NORMAL",
                    "capacity": 3,
                    "active_tasks": 0,
                    "balance": "1",
                    "coins": "2",
                    "symbol": "$",
                    "checked_at": 1,
                    "balance_checked_at": 1,
                }
            ]
        )
        workflow_path = tmp_path / "workflow.json"
        workflow_path.write_text(json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}), encoding="utf-8")
        store.create_task(
            {
                "id": "task_dispatch_credential",
                "created_at": 1,
                "workflow_path": str(workflow_path),
                "workflow_name": "workflow.json",
                "files": {},
                "prompts": {},
                "random_noise": {},
                "remote_workflow_id": "123456",
                "output_dir": str(tmp_path / "outputs"),
            }
        )

        manager._dispatch_once()

        task = store.task("task_dispatch_credential")
        assert task["key_id"] == "key_dispatch"
        assert task["dispatch_key_name"] == "发布账号"
        assert task["dispatch_key_site"] == "ai"
        assert task["dispatch_key_api_type"] == "NORMAL"
        public_task = manager.public_tasks()[0]
        assert public_task["key_name"] == "发布账号"
        assert public_task["key_site"] == "ai"
        assert public_task["dispatch_credential_recorded"] is True
    finally:
        manager.close()
        store._db.close()
