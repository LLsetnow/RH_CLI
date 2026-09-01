from __future__ import annotations

import base64
import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from rh_cli.errors import RhCliError

from .app import DATA_ROOT, WEB_ROOT, LocalStore, TaskManager, pick_local_directory_on_macos, pick_local_file_on_macos, public_key, public_state


STATIC_ROOT = WEB_ROOT / "static"
LOCAL_PREVIEW_LIMIT = 8 * 1024 * 1024


def local_file_preview(path_value: str) -> dict[str, object]:
    """Read a local image into memory for preview; never copy it into web/data."""
    path = Path(str(path_value or "")).expanduser().resolve()
    if not path.is_file():
        raise RhCliError("FILE_NOT_FOUND", f"本地文件不存在：{path}")
    stat = path.stat()
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    preview_url = ""
    if mime.startswith("image/") and stat.st_size <= LOCAL_PREVIEW_LIMIT:
        try:
            preview_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        except OSError:
            preview_url = ""
    return {
        "path": str(path),
        "name": path.name,
        "size": stat.st_size,
        "mime": mime,
        "preview_url": preview_url,
    }


class LocalHandler(BaseHTTPRequestHandler):
    server_version = "RHWorkflowDesk/0.1"

    @property
    def state(self) -> tuple[LocalStore, TaskManager]:
        app_server = self.server  # type: ignore[assignment]
        return app_server.store, app_server.manager

    def log_message(self, format: str, *args: object) -> None:
        # Keep API keys and file contents out of the terminal log.
        print(f"[rh-web] {format % args}")

    def _headers(self, content_type: str = "application/json; charset=utf-8", length: int | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:%s" % self.server.server_port)
        if length is not None:
            self.send_header("Content-Length", str(length))

    def _json(self, status: int, data: object) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._headers(length=len(raw))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 600 * 1024 * 1024:
            raise RhCliError("REQUEST_TOO_LARGE", "请求体过大。")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RhCliError("BAD_JSON", "请求体不是有效 JSON。") from exc
        if not isinstance(value, dict):
            raise RhCliError("BAD_JSON", "请求体必须是 JSON 对象。")
        return value

    def _safe_error(self, exc: Exception) -> dict:
        if isinstance(exc, RhCliError):
            return exc.to_dict()
        return {"code": "SERVER_ERROR", "message": str(exc)}

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._headers(length=0)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self._headers(length=0)
            self.end_headers()
            return
        if path == "/api/state":
            store, manager = self.state
            self._json(200, public_state(store, manager))
            return
        if path.startswith("/api/tasks/") and "/output/" in path:
            self._serve_output(path)
            return
        if path.startswith("/api/tasks/") and path.endswith("/load"):
            task_id = path.split("/")[3]
            store, _ = self.state
            try:
                self._json(200, store.load_task_workflow(task_id))
            except Exception as exc:
                self._json(400 if isinstance(exc, RhCliError) else 500, self._safe_error(exc))
            return
        if path.startswith("/api/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            store, _ = self.state
            task = store.task(task_id)
            if not task:
                self._json(404, {"code": "TASK_NOT_FOUND", "message": "找不到任务"})
            else:
                self._json(200, task)
            return
        if path == "/api/health":
            self._json(200, {"ok": True, "data_dir": str(DATA_ROOT)})
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/preview-file":
                body = self._body()
                self._json(200, local_file_preview(str(body.get("path") or "")))
                return
            if path == "/api/pick-file":
                selected = pick_local_file_on_macos()
                self._json(200, local_file_preview(str(selected)))
                return
            if path == "/api/pick-directory":
                selected = pick_local_directory_on_macos()
                self._json(200, {"path": str(selected) if selected else ""})
                return
            if path == "/api/workflows/analyze":
                body = self._body()
                content = body.get("content")
                if not isinstance(content, str):
                    raise RhCliError("INVALID_WORKFLOW", "缺少工作流 JSON 内容。")
                store, _ = self.state
                workflow_id, workflow_path, analysis = store.save_workflow(str(body.get("filename") or "workflow.json"), content)
                self._json(
                    200,
                    {
                        "workflow_id": workflow_id,
                        "filename": workflow_path.name,
                        "analysis": analysis,
                        "remote_workflow_id": analysis.get("remote_workflow_id", ""),
                    },
                )
                return
            if path == "/api/keys":
                body = self._body()
                _, manager = self.state
                record = manager.add_key(str(body.get("name") or ""), str(body.get("site") or "ai"), str(body.get("api_key") or ""))
                self._json(200, {"key": record})
                return
            if path.startswith("/api/keys/") and path.endswith("/check"):
                key_id = path.split("/")[3]
                _, manager = self.state
                self._json(200, {"key": manager.check_key(key_id)})
                return
            if path.startswith("/api/keys/") and path.endswith("/balance"):
                key_id = path.split("/")[3]
                _, manager = self.state
                self._json(200, {"key": manager.refresh_balance(key_id)})
                return
            if path == "/api/tasks":
                body = self._body()
                _, manager = self.state
                task = manager.submit_task(
                    str(body.get("workflow_id") or ""),
                    body.get("files") if isinstance(body.get("files"), dict) else {},
                    body.get("prompts") if isinstance(body.get("prompts"), dict) else {},
                    str(body.get("key_id") or "") or None,
                    str(body.get("output_dir") or "") or None,
                    remote_workflow_id=str(body.get("remote_workflow_id") or "") or None,
                    random_noise=body.get("random_noise") if isinstance(body.get("random_noise"), dict) else {},
                    workflow_data=body.get("workflow") if isinstance(body.get("workflow"), dict) else None,
                    workflow_name=str(body.get("workflow_name") or "") or None,
                )
                self._json(202, {"task": task})
                return
            if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                task_id = path.split("/")[3]
                _, manager = self.state
                self._json(200, {"task": manager.cancel_task(task_id)})
                return
            self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
        except Exception as exc:
            self._json(400 if isinstance(exc, RhCliError) else 500, self._safe_error(exc))

    def do_PATCH(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/settings":
                body = self._body()
                store, _ = self.state
                output_dir = store.set_output_dir(str(body.get("output_dir") or ""))
                self._json(200, {"output_dir": output_dir})
                return
            self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
        except Exception as exc:
            self._json(400 if isinstance(exc, RhCliError) else 500, self._safe_error(exc))

    def do_DELETE(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path.startswith("/api/keys/"):
                key_id = path.rsplit("/", 1)[-1]
                _, manager = self.state
                manager.remove_key(key_id)
                self._json(200, {"ok": True})
                return
            if path.startswith("/api/tasks/"):
                task_id = path.rsplit("/", 1)[-1]
                _, manager = self.state
                manager.delete_task(task_id)
                self._json(200, {"ok": True})
                return
            self._json(404, {"code": "NOT_FOUND", "message": "接口不存在"})
        except Exception as exc:
            self._json(400 if isinstance(exc, RhCliError) else 500, self._safe_error(exc))

    def _serve_output(self, path: str) -> None:
        parts = path.split("/")
        try:
            task_id = parts[3]
            index = int(parts[5])
            store, _ = self.state
            task = store.task(task_id)
            if not task:
                raise FileNotFoundError
            outputs = [item for item in task.get("outputs", []) if item.get("kind") == "file"]
            output = outputs[index]
            file_path = Path(output["path"]).resolve()
            allowed = Path(task["output_dir"]).resolve()
            if allowed not in file_path.parents or not file_path.is_file():
                raise FileNotFoundError
            data = file_path.read_bytes()
        except (ValueError, IndexError, KeyError, OSError):
            self._json(404, {"code": "OUTPUT_NOT_FOUND", "message": "产物不存在"})
            return
        self.send_response(200)
        self._headers(mimetypes.guess_type(str(file_path))[0] or "application/octet-stream", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        if relative in {"prompt", "prompt/"}:
            relative = "prompt.html"
        if relative.startswith("static/"):
            relative = relative[len("static/"):]
        file_path = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in file_path.parents and file_path != STATIC_ROOT.resolve():
            self._json(404, {"code": "NOT_FOUND", "message": "页面不存在"})
            return
        if not file_path.is_file():
            self._json(404, {"code": "NOT_FOUND", "message": "页面不存在"})
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self._headers(mimetypes.guess_type(str(file_path))[0] or "application/octet-stream", len(data))
        self.end_headers()
        self.wfile.write(data)


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        self.store = LocalStore()
        self.manager = TaskManager(self.store)
        super().__init__(address, LocalHandler)

    def server_close(self) -> None:
        self.manager.close()
        self.store._db.close()
        super().server_close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="启动纯本地 RH 工作流 Web 应用")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8766, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = AppServer((args.host, args.port))
    url = f"http://{args.host}:{args.port}/"
    print(f"RH Workflow Desk 已启动：{url}")
    print(f"本地数据目录：{DATA_ROOT}")
    if not args.no_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRH Workflow Desk 已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
