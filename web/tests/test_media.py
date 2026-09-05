from __future__ import annotations

import http.client
import io
import json
import threading
import zipfile
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


def test_output_folder_can_be_opened_for_a_task_over_http(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    output_root = tmp_path / "outputs"
    task_folder = output_root / "task_open_folder"
    task_folder.mkdir(parents=True)
    media = task_folder / "preview.mp4"
    media.write_bytes(b"video")
    opened = []
    monkeypatch.setattr(web_server, "open_local_directory", lambda path: opened.append(path) or True)

    server = web_server.AppServer(("127.0.0.1", 0))
    server.store.create_task(
        {
            "id": "task_open_folder",
            "created_at": 1,
            "workflow_path": str(tmp_path / "workflow.json"),
            "workflow_name": "workflow.json",
            "files": {},
            "prompts": {},
            "random_noise": {},
            "remote_workflow_id": "123456",
            "output_dir": str(output_root),
        }
    )
    server.store.update_task(
        "task_open_folder",
        outputs_json=json.dumps([{"kind": "file", "path": str(media), "mime": "video/mp4"}]),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        connection.request(
            "POST",
            "/api/tasks/task_open_folder/open-folder",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload == {"opened": True, "message": "已打开媒体所在文件夹"}
        assert opened == [task_folder.resolve()]
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_output_case_tags_can_be_updated_over_http_and_are_counted(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    output_dir = tmp_path / "outputs" / "task_tags_http"
    output_dir.mkdir(parents=True)
    media = output_dir / "case.mp4"
    media.write_bytes(b"video")

    server = web_server.AppServer(("127.0.0.1", 0))
    server.store.create_task(
        {
            "id": "task_tags_http",
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
        "task_tags_http",
        outputs_json=json.dumps([{"kind": "file", "path": str(media), "mime": "video/mp4"}]),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        body = json.dumps({"tags": ["案例"]}).encode("utf-8")
        connection.request(
            "PATCH",
            "/api/tasks/task_tags_http/outputs/0",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["output"]["tags"] == ["案例"]

        connection.request("GET", "/api/outputs")
        outputs_response = connection.getresponse()
        outputs_payload = json.loads(outputs_response.read())
        assert outputs_response.status == 200
        assert outputs_payload["summary"]["tag_counts"]["案例"] == 1
        assert outputs_payload["outputs"][0]["tags"] == ["案例"]

        connection.request("GET", "/api/outputs/export/case")
        archive_response = connection.getresponse()
        archive_payload = archive_response.read()
        assert archive_response.status == 200
        assert archive_response.getheader("Content-Type") == "application/zip"
        assert "attachment" in (archive_response.getheader("Content-Disposition") or "")
        with zipfile.ZipFile(io.BytesIO(archive_payload)) as archive:
            assert archive.namelist() == ["workflow.json/task_tags_http/case.mp4"]
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_local_video_preview_uses_a_streamed_range_endpoint(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"0123456789")

    server = web_server.AppServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        body = json.dumps({"path": str(source)}).encode("utf-8")
        connection.request(
            "POST",
            "/api/preview-file",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["preview_kind"] == "video"
        assert payload["preview_url"].startswith("/api/local-preview/")

        connection.request("GET", payload["preview_url"], headers={"Range": "bytes=2-5"})
        preview_response = connection.getresponse()
        assert preview_response.status == 206
        assert preview_response.getheader("Content-Type") == "video/mp4"
        assert preview_response.getheader("Content-Range") == "bytes 2-5/10"
        assert preview_response.read() == b"2345"
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_douyin_download_endpoint_returns_local_video_preview(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    source = tmp_path / "downloaded.mp4"
    source.write_bytes(b"video-bytes")
    monkeypatch.setattr(web_server, "download_douyin_video", lambda url, cookie_path, data_root: source)

    server = web_server.AppServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        body = json.dumps({"url": "https://v.douyin.com/example/"}).encode("utf-8")
        connection.request("POST", "/api/download-douyin", body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["path"] == str(source.resolve())
        assert payload["preview_kind"] == "video"
        assert payload["preview_url"].startswith("/api/local-preview/")
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_action_api_exposes_both_paired_assets(tmp_path, monkeypatch):
    resources = Path("/Users/apple/Documents/VideoMake/ref/pose/pose.json")
    if not resources.is_file():
        pytest.skip("本机未安装 VideoMake 的 pose.json")
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
        assert payload["source_status"]["paired_count"] == payload["source_status"]["action_count"]
        action = payload["actions"][0]
        assert action["category"] == "站立"
        assert action["tags"] == []
        assert action["pair_status"] == "paired"
        assert action["color_image_url"].endswith("/image")
        assert action["depth_image_url"].endswith("/depth")

        connection.request("GET", action["depth_image_url"].replace("/depth", "/depth-path"))
        path_response = connection.getresponse()
        path_payload = json.loads(path_response.read())
        assert path_response.status == 200
        assert Path(path_payload["path"]).is_file()

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
