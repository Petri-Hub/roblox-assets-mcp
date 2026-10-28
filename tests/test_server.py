import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from roblox_assets_mcp.server import _validate_api_key, create_server

ASSET_ID = 123456
ASSET = {
    "asset": {
        "id": ASSET_ID,
        "name": "Stone House",
        "description": "A small house",
        "assetTypeId": 10,
        "hasScripts": False,
    },
    "creator": {"name": "Builder"},
    "creatorStoreProduct": {
        "purchasePrice": {
            "currencyCode": "USD",
            "quantity": {"significand": 0, "exponent": 0},
        }
    },
}


def test_missing_key_fails_before_stdio_starts(tmp_path: Path) -> None:
    environment = {key: value for key, value in os.environ.items() if key != "ROBLOX_API_KEY"}
    result = subprocess.run(
        [sys.executable, "-m", "roblox_assets_mcp.server"],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert "ROBLOX_API_KEY is required" in result.stderr
    assert result.stdout == ""


def test_dotenv_overrides_endpoint_defaults(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "CREATOR_STORE_URL=https://creator.example/assets/\n"
        "DELIVERY_URL=https://delivery.example/assetId/\n"
        "THUMBNAILS_URL=https://thumbs.example/assets/\n"
        "REQUEST_TIMEOUT=3.5\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"CREATOR_STORE_URL", "DELIVERY_URL", "THUMBNAILS_URL", "REQUEST_TIMEOUT"}
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from roblox_assets_mcp import server; "
            "print(server.CREATOR_STORE_URL, server.DELIVERY_URL, "
            "server.THUMBNAILS_URL, server.REQUEST_TIMEOUT, sep='\\n')",
        ],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )

    assert result.stdout.splitlines() == [
        "https://creator.example/assets",
        "https://delivery.example/assetId",
        "https://thumbs.example/assets",
        "3.5",
    ]


def test_asset_tools_use_expected_endpoints_and_return_typed_data() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("assets:search"):
            return httpx.Response(
                200, json={"creatorStoreAssets": [ASSET], "nextPageToken": "next"}
            )
        if request.url.host == "thumbnails.roblox.com":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "targetId": ASSET_ID,
                            "state": "Completed",
                            "imageUrl": "https://tr.rbxcdn.com/preview.png",
                        }
                    ]
                },
            )
        if "asset-delivery-api" in request.url.path:
            return httpx.Response(200, json={"location": "https://fts.rbxcdn.com/asset"})
        return httpx.Response(200, json=ASSET)

    async def exercise() -> None:
        server = create_server("test-key", transport=httpx.MockTransport(respond))
        async with Client(server) as client:
            search = await client.call_tool(
                "search_assets", {"query": "stone house", "category": "Model", "free_only": True}
            )
            details = await client.call_tool("get_asset_details", {"asset_id": ASSET_ID})
            download = await client.call_tool("prepare_asset_download", {"asset_id": ASSET_ID})

        assert search.structured_content["assets"][0]["name"] == "Stone House"
        assert search.structured_content["assets"][0]["is_free"] is True
        assert search.structured_content["next_page_token"] == "next"
        assert details.structured_content["thumbnail_url"] == "https://tr.rbxcdn.com/preview.png"
        assert download.structured_content["url"] == "https://fts.rbxcdn.com/asset"

    asyncio.run(exercise())

    assert len(requests) == 4
    assert requests[0].headers["x-api-key"] == "test-key"
    assert b'"maxPriceCents":0' in requests[0].content
    assert requests[2].url.host == "thumbnails.roblox.com"
    assert "x-api-key" not in requests[2].headers


def test_roblox_authorization_failure_is_explicit() -> None:
    def deny(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "denied"})

    async def exercise() -> None:
        server = create_server("test-key", transport=httpx.MockTransport(deny))
        async with Client(server) as client:
            result = await client.call_tool(
                "search_assets", {"query": "house"}, raise_on_error=False
            )
        assert result.is_error
        assert "Check the API key and scopes" in str(result.content)

    asyncio.run(exercise())


def test_startup_check_requires_search_and_delivery_access() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("assets:search"):
            return httpx.Response(200, json={"creatorStoreAssets": [ASSET]})
        return httpx.Response(403, json={"message": "scope missing"})

    with pytest.raises(ToolError, match="Asset Delivery access check failed"):
        asyncio.run(_validate_api_key("test-key", httpx.MockTransport(respond)))

    def allow(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("assets:search"):
            return httpx.Response(200, json={"creatorStoreAssets": [ASSET]})
        return httpx.Response(200, json={"location": "https://fts.rbxcdn.com/asset"})

    asyncio.run(_validate_api_key("test-key", httpx.MockTransport(allow)))


def test_search_maps_pagination_and_paid_filter() -> None:
    bodies: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"creatorStoreAssets": []})

    async def exercise() -> None:
        server = create_server("test-key", transport=httpx.MockTransport(respond))
        async with Client(server) as client:
            await client.call_tool(
                "search_assets",
                {
                    "query": "music",
                    "category": "Audio",
                    "free_only": False,
                    "page_size": 5,
                    "page_token": "next",
                },
            )

    asyncio.run(exercise())

    assert bodies == [
        {
            "query": "music",
            "searchCategoryType": "Audio",
            "maxPageSize": 5,
            "searchView": "Full",
            "pageToken": "next",
        }
    ]
