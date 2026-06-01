from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from .errors import RhCliError
from .http import BASE_URL, API_HOST, RhHttpClient


STANDARD_UPLOAD_URL = f"{BASE_URL}/media/upload/binary"
APP_UPLOAD_URL = f"{API_HOST}/task/openapi/upload"
INLINE_LIMIT_BYTES = 5 * 1024 * 1024


def image_to_data_uri(file_path: str | Path) -> str:
    path = Path(file_path)
    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        raise RhCliError("FILE_NOT_FOUND", f"文件读取失败：{path}") from exc
    return f"data:{mime_type};base64,{encoded}"


def upload_standard_media(client: RhHttpClient, file_path: str | Path) -> str:
    path = Path(file_path)
    if not path.exists():
        raise RhCliError("FILE_NOT_FOUND", f"文件不存在：{path}")
    response = client.upload_form(
        STANDARD_UPLOAD_URL,
        str(path),
        data={},
        headers={"Authorization": f"Bearer {client.api_key}"},
    )
    if response.get("code") == 0:
        try:
            return response["data"]["download_url"]
        except KeyError as exc:
            raise RhCliError("UPLOAD_FAILED", "上传成功但响应中没有 download_url。") from exc
    raise RhCliError("UPLOAD_FAILED", f"上传失败：{response.get('msg', response)}")


def resolve_media(client: RhHttpClient, media_path: str, *, force_upload: bool = False) -> str:
    if media_path.startswith(("http://", "https://")):
        return media_path
    path = Path(media_path).expanduser()
    if not path.exists():
        raise RhCliError("FILE_NOT_FOUND", f"文件不存在：{path}")
    if force_upload or path.stat().st_size > INLINE_LIMIT_BYTES:
        return upload_standard_media(client, path)
    return image_to_data_uri(path)


def upload_app_file(client: RhHttpClient, file_path: str | Path) -> str:
    path = Path(file_path).expanduser()
    if not path.exists():
        raise RhCliError("FILE_NOT_FOUND", f"文件不存在：{path}")
    response = client.upload_form(
        APP_UPLOAD_URL,
        str(path),
        data={"apiKey": client.api_key, "fileType": "input"},
    )
    if response.get("code") != 0 or response.get("msg") != "success":
        raise RhCliError("UPLOAD_FAILED", f"上传失败：{response.get('msg', response)}")
    file_name = response.get("data", {}).get("fileName")
    if not file_name:
        raise RhCliError("UPLOAD_FAILED", "上传成功但响应中没有 fileName。")
    return str(file_name)
