from __future__ import annotations

import http.client
import io
import json
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlencode

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


@pytest.mark.parametrize("project_id, expected", [
    ("project_a", {"a"}),
    ("project_b", {"b"}),
    ("__unclassified__", {"unclassified"}),
    ("missing_project", set()),
    ("", {"a", "b", "unclassified"}),
])
def test_bulk_output_actions_are_scoped_to_project_over_http(tmp_path, monkeypatch, project_id, expected):
    _configure_web_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    server = web_server.AppServer(("127.0.0.1", 0))
    paths = {}
    for name, folder_id in [("a", "project_a"), ("b", "project_b"), ("unclassified", "")]:
        task_folder = tmp_path / "outputs" / name
        task_folder.mkdir(parents=True)
        paths[name] = [task_folder / "case.mp4", task_folder / "keep.mp4"]
        for path in paths[name]:
            path.write_bytes(name.encode())
        server.store.create_task({
            "id": name, "created_at": 1,
            "workflow_path": str(tmp_path / "workflow.json"),
            "workflow_name": "workflow.json", "files": {}, "prompts": {},
            "output_dir": str(tmp_path / "outputs"),
            "project_id": folder_id, "project_name": folder_id,
        })
        server.store.update_task(name, outputs_json=json.dumps([
            {"kind": "file", "path": str(paths[name][0]), "mime": "video/mp4", "rating": 1, "tags": ["案例"]},
            {"kind": "file", "path": str(paths[name][1]), "mime": "video/mp4", "rating": 2},
        ]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    query = "?" + urlencode({"project_id": project_id})
    try:
        connection.request("GET", "/api/outputs/export/case" + query)
        response = connection.getresponse()
        payload = response.read()
        if expected:
            assert response.status == 200
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                assert set(archive.namelist()) == {f"workflow.json/{name}/case.mp4" for name in expected}
        else:
            assert response.status == 404
            assert json.loads(payload)["code"] == "NO_CASE_OUTPUTS"

        connection.request("DELETE", "/api/outputs/rating/1" + query)
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["deleted"] == len(expected)
        assert payload["tasks_updated"] == len(expected)
        for name, (case_path, keep_path) in paths.items():
            assert case_path.exists() == (name not in expected)
            assert keep_path.exists()
            assert len(server.store.task(name)["outputs"]) == (1 if name in expected else 2)
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_bulk_output_actions_follow_current_filters_over_http(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    server = web_server.AppServer(("127.0.0.1", 0))
    records = {
        "match": {"project_id": "project_a", "workflow_name": "流程甲", "name": "目标片段.mp4", "tags": ["案例"], "mime": "video/mp4"},
        "wrong_name": {"project_id": "project_a", "workflow_name": "流程甲", "name": "其他片段.mp4", "tags": ["案例"], "mime": "video/mp4"},
        "wrong_type": {"project_id": "project_a", "workflow_name": "流程甲", "name": "目标图片.png", "tags": ["案例"], "mime": "image/png"},
        "wrong_workflow": {"project_id": "project_a", "workflow_name": "流程乙", "name": "目标片段-乙.mp4", "tags": ["案例"], "mime": "video/mp4"},
        "wrong_tag": {"project_id": "project_a", "workflow_name": "流程甲", "name": "目标 H.mp4", "tags": ["案例", "H"], "mime": "video/mp4"},
        "other_project": {"project_id": "project_b", "workflow_name": "流程甲", "name": "目标片段-B.mp4", "tags": ["案例"], "mime": "video/mp4"},
    }
    paths = {}
    for task_id, record in records.items():
        task_folder = tmp_path / "outputs" / task_id
        task_folder.mkdir(parents=True)
        output_path = task_folder / record["name"]
        output_path.write_bytes(task_id.encode())
        paths[task_id] = output_path
        server.store.create_task({
            "id": task_id,
            "created_at": 1,
            "workflow_path": str(tmp_path / "workflow.json"),
            "workflow_name": record["workflow_name"],
            "files": {},
            "prompts": {},
            "output_dir": str(tmp_path / "outputs"),
            "project_id": record["project_id"],
            "project_name": record["project_id"],
        })
        server.store.update_task(task_id, outputs_json=json.dumps([
            {
                "kind": "file",
                "path": str(output_path),
                "name": record["name"],
                "mime": record["mime"],
                "rating": 1,
                "tags": record["tags"],
            }
        ]))

    query = "?" + urlencode({
        "project_id": "project_a",
        "search": "目标",
        "type": "video",
        "rating": "1",
        "workflow": "流程甲",
        "tag_case": "include",
        "tag_h": "exclude",
    })
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        connection.request("GET", "/api/outputs/export/case" + query)
        response = connection.getresponse()
        payload = response.read()
        assert response.status == 200
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert archive.namelist() == [f"流程甲/match/目标片段.mp4"]

        connection.request("DELETE", "/api/outputs/rating/1" + query)
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 200
        assert result["deleted"] == 1
        assert result["tasks_updated"] == 1
        assert not paths["match"].exists()
        for task_id in records:
            if task_id != "match":
                assert paths[task_id].exists()
                assert len(server.store.task(task_id)["outputs"]) == 1
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


@pytest.mark.parametrize("selection", ["existing", "unclassified", "automatic", "deleted"])
def test_task_submission_project_selection_over_http(tmp_path, monkeypatch, selection):
    _configure_web_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    server = web_server.AppServer(("127.0.0.1", 0))
    server.manager.close()  # Exercise local submission without dispatching remote jobs.
    project = server.store.create_project_folder("所选项目")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        connection.request("GET", "/api/state?scope=submit")
        response = connection.getresponse()
        assert json.loads(response.read())["projects"][0]["id"] == project["id"]
        body = {
            "workflow": {"1": {"class_type": "SaveImage", "inputs": {}}},
            "workflow_name": "project-selector.json", "remote_workflow_id": "123456",
            "output_dir": str(tmp_path / "projects" / "自动项目" / "output"),
            "project": {"existing": {"id": project["id"]}, "unclassified": {}, "automatic": None, "deleted": {"id": "missing"}}[selection],
        }
        connection.request("POST", "/api/tasks", body=json.dumps(body), headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        if selection == "deleted":
            assert response.status == 400
            assert payload["error"] == "PROJECT_FOLDER_NOT_FOUND"
            assert server.store.tasks() == []
            return
        assert response.status == 202
        task = payload["task"]
        expected_name = {"existing": "所选项目", "unclassified": "", "automatic": "自动项目"}[selection]
        assert task["project_name"] == expected_name
        if selection == "existing":
            assert task["project_id"] == project["id"]
        assert task["output_dir"] == body["output_dir"]
        manifest = json.loads(Path(task["manifest_path"]).read_text())
        assert manifest["project"]["name"] == expected_name
        server.store._backfill_task_projects()
        assert server.store.task(task["id"])["project_name"] == expected_name
        if selection == "unclassified":
            assert server.store.task(task["id"])["project_inference_disabled"] == 1
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


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


def test_input_file_folder_can_be_opened_over_http(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    source = tmp_path / "inputs" / "reference.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    opened = []
    monkeypatch.setattr(web_server, "open_local_directory", lambda path: opened.append(path) or True)

    server = web_server.AppServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        body = json.dumps({"path": str(source)}).encode("utf-8")
        connection.request(
            "POST",
            "/api/open-file-folder",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload == {"opened": True, "message": "已打开文件所在文件夹"}
        assert opened == [source.parent.resolve()]
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_social_video_download_can_be_requested_for_supported_platforms(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    downloaded = tmp_path / "data" / "downloaded-inputs" / "bilibili-video.mp4"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"video")
    calls = []

    def fake_download(url, data_root, cookie_path):
        calls.append((url, data_root, cookie_path))
        return downloaded

    monkeypatch.setattr(web_server, "download_workflow_social_video", fake_download)

    server = web_server.AppServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    try:
        body = json.dumps({"url": "https://www.bilibili.com/video/BV1public"}).encode("utf-8")
        connection.request(
            "POST",
            "/api/download-social-video",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["platform"] == "bilibili"
        assert payload["platform_label"] == "Bilibili"
        assert payload["name"] == "bilibili-video.mp4"
        assert calls == [("https://www.bilibili.com/video/BV1public", web_server.DATA_ROOT, "")]
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
