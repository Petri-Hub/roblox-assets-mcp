import asyncio
from pathlib import Path

import httpx
from fastmcp import Client

from roblox_assets_mcp.app import create_server


def test_create_and_update_upload_files_from_server_filesystem(tmp_path: Path) -> None:
    model = tmp_path / "sword.fbx"
    model.write_bytes(b"example fbx")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"path": "operations/op_123", "done": False})

    async def exercise() -> None:
        async with Client(create_server("test-key", transport=httpx.MockTransport(respond))) as client:
            result = await client.call_tool(
                "create_asset",
                {
                    "file_path": str(model),
                    "asset_type": "Model",
                    "display_name": "Sword",
                    "description": "Test",
                    "creator_id": 42,
                },
            )
            assert result.structured_content["path"] == "operations/op_123"
            await client.call_tool(
                "update_asset",
                {
                    "asset_id": 123,
                    "metadata": {"displayName": "New sword"},
                    "update_mask": "displayName",
                    "file_path": str(model),
                    "asset_type": "Model",
                },
            )

    asyncio.run(exercise())
    create, update = requests
    assert create.method == "POST" and create.url.path == "/assets/v1/assets"
    assert create.headers["x-api-key"] == "test-key"
    assert b'"assetType": "Model"' in create.content
    assert b'"userId": "42"' in create.content
    assert b'"expectedPrice": 0' in create.content
    assert b"Content-Type: model/fbx" in create.content and b"example fbx" in create.content
    assert update.method == "PATCH" and update.url.params["updateMask"] == "displayName"
    assert b'"assetId": 123' in update.content


def test_lifecycle_routes_and_responses() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/versions"):
            return httpx.Response(200, json=[{"path": "assets/123/versions/1"}])
        return httpx.Response(200, json={"path": "assets/123", "done": True})

    async def exercise() -> None:
        async with Client(create_server("test-key", transport=httpx.MockTransport(respond))) as client:
            await client.call_tool("get_asset_operation", {"operation_id": "operations/op_1"})
            await client.call_tool("get_owned_asset", {"asset_id": 123, "read_mask": "state"})
            await client.call_tool(
                "update_asset", {"asset_id": 123, "metadata": {"description": "Updated"}, "update_mask": "description"}
            )
            versions = await client.call_tool(
                "list_asset_versions", {"asset_id": 123, "page_size": 10, "page_token": "next"}
            )
            assert versions.structured_content == {"versions": [{"path": "assets/123/versions/1"}]}
            await client.call_tool("get_asset_version", {"asset_id": 123, "version_number": 2})
            await client.call_tool("rollback_asset_version", {"asset_id": 123, "version_number": 2})
            await client.call_tool("archive_asset", {"asset_id": 123})
            await client.call_tool("restore_asset", {"asset_id": 123})

    asyncio.run(exercise())
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/assets/v1/operations/op_1"),
        ("GET", "/assets/v1/assets/123"),
        ("PATCH", "/assets/v1/assets/123"),
        ("GET", "/assets/v1/assets/123/versions"),
        ("GET", "/assets/v1/assets/123/versions/2"),
        ("POST", "/assets/v1/assets/123/versions:rollback"),
        ("POST", "/assets/v1/assets/123:archive"),
        ("POST", "/assets/v1/assets/123:restore"),
    ]
    assert requests[1].url.params["readMask"] == "state"
    assert b'"description": "Updated"' in requests[2].content
    assert b"filename=" not in requests[2].content
    assert requests[3].url.params["pageToken"] == "next"
    assert b"assets/123/versions/2" in requests[5].content
    assert requests[5].headers["content-type"] == "application/json"


def test_upload_validation_prevents_bad_requests(tmp_path: Path) -> None:
    model = tmp_path / "sword.fbx"
    model.write_bytes(b"data")
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async def exercise() -> None:
        async with Client(create_server("test-key", transport=httpx.MockTransport(respond))) as client:
            for path, kind in [("relative.fbx", "Model"), (str(model), "Decal")]:
                result = await client.call_tool(
                    "create_asset",
                    {"file_path": path, "asset_type": kind, "display_name": "x", "description": "", "creator_id": 1},
                    raise_on_error=False,
                )
                assert result.is_error
            result = await client.call_tool(
                "update_asset",
                {"asset_id": 1, "metadata": {}, "file_path": str(model), "asset_type": "Decal"},
                raise_on_error=False,
            )
            assert result.is_error
            result = await client.call_tool(
                "get_asset_operation", {"operation_id": "../../assets/1"}, raise_on_error=False
            )
            assert result.is_error

    asyncio.run(exercise())
    assert not sent


def test_api_error_explains_invalid_asset_type_and_redacts_key() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Model cannot be archived; credential test-key"})

    async def exercise() -> None:
        async with Client(create_server("test-key", transport=httpx.MockTransport(respond))) as client:
            result = await client.call_tool("archive_asset", {"asset_id": 123}, raise_on_error=False)
            assert result.is_error
            text = str(result.content)
            assert "Model cannot be archived" in text
            assert "test-key" not in text

    asyncio.run(exercise())
