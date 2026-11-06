"""Roblox Open Cloud asset lifecycle operations."""

import json
import stat
from pathlib import Path
from typing import Any

import httpx
from fastmcp.exceptions import ToolError

from roblox_assets_mcp.config import ASSETS_URL, REQUEST_TIMEOUT
from roblox_assets_mcp.models import UploadType

MAX_FILE_SIZE = 20 * 1024 * 1024
FORMATS: dict[str, dict[str, str]] = {
    "Animation": {".rbxm": "model/x-rbxm", ".rbxmx": "model/x-rbxm"},
    "Audio": {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac"},
    "Decal": {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".tga": "image/tga",
    },
    "Image": {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".tga": "image/tga",
    },
    "Mesh": {".mesh": "model/x-file-mesh-data"},
    "Model": {
        ".fbx": "model/fbx",
        ".gltf": "model/gltf+json",
        ".glb": "model/gltf-binary",
        ".rbxm": "model/x-rbxm",
        ".rbxmx": "model/x-rbxm",
    },
    "Video": {".mp4": "video/mp4", ".mov": "video/mov"},
}


class AssetManager:
    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.api_key = api_key
        self.transport = transport

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=self.transport) as client:
                response = await client.request(
                    method, ASSETS_URL + path, headers={"x-api-key": self.api_key}, **kwargs
                )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, (dict, list)):
                    raise ValueError("Expected a JSON object or array")
                return {"versions": data} if isinstance(data, list) else data
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in (401, 403):
                raise ToolError(
                    f"Roblox denied access (HTTP {status}). Check the API key, scopes and asset ownership."
                ) from error
            if status == 429:
                raise ToolError("Roblox rate limit reached (HTTP 429). Try again later.") from error
            if status == 400:
                try:
                    message = error.response.json().get("message", "")
                    if isinstance(message, str) and message:
                        message = " ".join(message.replace(self.api_key, "[redacted]").split())[:300]
                        raise ToolError(f"Roblox asset request failed (HTTP 400): {message}") from error
                except (ValueError, AttributeError):
                    pass
            raise ToolError(f"Roblox asset request failed (HTTP {status}).") from error
        except (httpx.RequestError, ValueError) as error:
            raise ToolError("Could not read a valid response from Roblox.") from error

    async def upload(
        self,
        metadata: dict[str, Any],
        file_path: str,
        asset_type: UploadType,
        *,
        asset_id: int | None = None,
        update_mask: str | None = None,
    ) -> dict[str, Any]:
        path = Path(file_path)
        if not path.is_absolute():
            raise ToolError("file_path must be an absolute path in the MCP server's filesystem.")
        mime = FORMATS[asset_type].get(path.suffix.lower())
        if not mime:
            raise ToolError(f"File extension is not supported for {asset_type}.")
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_SIZE or info.st_size == 0:
                raise ToolError("Asset file must be a non-empty regular file no larger than 20 MiB.")
            with path.open("rb") as file:
                endpoint = f"/assets/{asset_id}" if asset_id is not None else "/assets"
                params = {"updateMask": update_mask} if update_mask else None
                files = {
                    "request": (None, json.dumps(metadata), "application/json"),
                    "fileContent": (path.name, file, mime),
                }
                return await self.request(
                    "PATCH" if asset_id is not None else "POST", endpoint, params=params, files=files
                )
        except OSError as error:
            raise ToolError("Cannot read the asset file in the MCP server's filesystem.") from error
