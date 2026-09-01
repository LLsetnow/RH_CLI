from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from web import app as web_app
from web import server as web_server


def _configure_web_paths(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setattr(web_app, "DATA_ROOT", data_root)
    monkeypatch.setattr(web_app, "WORKFLOW_ROOT", data_root / "workflows")
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", data_root / "outputs")
    monkeypatch.setattr(web_app, "KEYS_PATH", data_root / "keys.json")
    monkeypatch.setattr(web_app, "DB_PATH", data_root / "tasks.sqlite3")
    monkeypatch.setattr(web_app, "default_local_output_dir", lambda: data_root / "outputs")
    monkeypatch.setattr(web_server, "DATA_ROOT", data_root)


def test_output_supports_http_ranges_for_video_seek(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    output_dir = tmp_path / "outputs" / "task_media"
    output_dir.mkdir(parents=True)
    media = output_dir / "preview.mp4"
    media.write_bytes(b"0123456789")

    server = web_server.AppServer(("127.0.0.1", 0))
    server.store.create_task(
        {
            "id": "task_media",
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
    server.store.update_task(
        "task_media",
        outputs_json=json.dumps([{"kind": "file", "path": str(media), "mime": "video/mp4"}]),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
        connection.request("GET", "/api/tasks/task_media/output/0", headers={"Range": "bytes=2-5"})
        response = connection.getresponse()
        assert response.status == 206
        assert response.getheader("Accept-Ranges") == "bytes"
        assert response.getheader("Content-Range") == "bytes 2-5/10"
        assert response.read() == b"2345"
        connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_action_api_exposes_both_paired_assets(tmp_path, monkeypatch):
    resources = Path("/Users/apple/Documents/VideoMake/ref/Resources.md")
    if not resources.is_file():
        pytest.skip("本机未安装 VideoMake 的 Resources.md")
    _configure_web_paths(tmp_path, monkeypatch)

    server = web_server.AppServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        connection.request("GET", "/api/prompt/actions")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["source_status"]["issue_count"] == 0
        action = payload["actions"][0]
        assert action["pair_status"] == "paired"
        assert action["color_image_url"].endswith("/image")
        assert action["depth_image_url"].endswith("/depth")

        connection.request("GET", action["depth_image_url"])
        depth_response = connection.getresponse()
        assert depth_response.status == 200
        assert depth_response.getheader("Content-Type", "").startswith("image/")
        assert depth_response.read()
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()
