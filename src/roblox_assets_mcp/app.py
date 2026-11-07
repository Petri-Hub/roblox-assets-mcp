import asyncio
import os
from typing import Annotated, Any, Literal

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from roblox_assets_mcp.client import RobloxClient
from roblox_assets_mcp.management import AssetManager
from roblox_assets_mcp.models import AssetDetails, Category, DownloadLocation, SearchResults, UploadType

AssetId = Annotated[int, Field(gt=0, description="Roblox asset ID")]
PageSize = Annotated[int, Field(ge=1, le=100, description="Number of results per page")]
LiteralCreator = Literal["user", "group"]


def create_server(
    api_key: str, *, owner_id: str, owner_type: LiteralCreator, transport: httpx.AsyncBaseTransport | None = None
) -> FastMCP:
    """Create an asset-only Roblox MCP server with a required Open Cloud API key."""
    roblox = RobloxClient(api_key, transport)
    manager = AssetManager(api_key, transport)
    mcp = FastMCP(
        name="Roblox Assets",
        instructions=(
            "Search Roblox Creator Store assets, inspect details and 2D thumbnails, and obtain "
            "temporary asset download URLs. Search before choosing an asset. Use free_only=True "
            "for free assets. Download URLs expire; fetch them promptly in your own sandbox. "
            "Create and manage owned assets using absolute paths in this server's filesystem. "
            "Uploading does not create a public Creator Store listing. "
            "Archive and rollback change existing assets; confirm the intended target and action."
        ),
        mask_error_details=True,
        strict_input_validation=True,
    )

    @mcp.tool
    async def search_assets(
        query: Annotated[str, Field(min_length=1, description="Words to search in Creator Store")],
        category: Category = "Model",
        free_only: bool = True,
        page_size: PageSize = 20,
        page_token: str | None = None,
    ) -> SearchResults:
        """Search Creator Store assets, with category, price, and pagination filters.

        Args:
            query: Search text, such as "medieval house" or "footsteps".
            category: Roblox Creator Store category. Defaults to Model.
            free_only: Limit results to free assets. Defaults to true.
            page_size: Maximum assets to request in this page, from 1 to 100.
            page_token: Pass next_page_token from a previous result to continue.

        Returns:
            Assets and the token for the next page, if one exists.
        """
        return await roblox.search_assets(query, category, free_only, page_size, page_token)

    @mcp.tool
    async def get_asset_details(asset_id: AssetId) -> AssetDetails:
        """Get a Creator Store asset's metadata and its 2D thumbnail URL.

        Font families use Roblox's 1200x80 font preview; other assets use 420x420.

        Args:
            asset_id: ID from search_assets or a Roblox asset page.

        Returns:
            Asset metadata, thumbnail state, and a PNG URL when ready.
        """
        return await roblox.get_asset_details(asset_id)

    @mcp.tool
    async def prepare_asset_download(asset_id: AssetId) -> DownloadLocation:
        """Get a temporary CDN URL for an asset; fetch it promptly in your own sandbox.

        Args:
            asset_id: ID of an asset selected from Creator Store.

        Returns:
            The asset ID and a temporary HTTPS CDN URL; no file is written.
        """
        return await roblox.prepare_asset_download(asset_id)

    @mcp.tool
    async def create_asset(
        file_path: str,
        asset_type: UploadType,
        display_name: Annotated[str, Field(min_length=1)],
        description: Annotated[str, Field(max_length=1000)],
        expected_price: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Upload a local file as an owned asset. Poll get_asset_operation for the result.

        file_path is an absolute path on the MCP server, not the client's machine.
        expected_price is the maximum upload fee in Robux; it defaults to zero.
        This creates an asset, not a public Creator Store listing.
        """
        metadata = {
            "assetType": asset_type,
            "displayName": display_name,
            "description": description,
            "creationContext": {
                "creator": {"userId" if owner_type == "user" else "groupId": owner_id},
                "expectedPrice": expected_price,
            },
        }
        return await manager.upload(metadata, file_path, asset_type)

    @mcp.tool
    async def get_asset_operation(operation_id: str) -> dict[str, Any]:
        """Check an upload or content update operation by ID, or its operations/{id} path."""
        import re

        operation_id = operation_id.removeprefix("operations/")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", operation_id):
            raise ToolError("Invalid operation ID.")
        return await manager.request("GET", f"/operations/{operation_id}")

    @mcp.tool
    async def get_owned_asset(asset_id: AssetId, read_mask: str | None = None) -> dict[str, Any]:
        """Get the Open Cloud metadata of an asset by ID, optionally selecting a read mask."""
        return await manager.request(
            "GET", f"/assets/{asset_id}", params={"readMask": read_mask} if read_mask else None
        )

    @mcp.tool
    async def update_asset(
        asset_id: AssetId,
        metadata: dict[str, Any],
        update_mask: str | None = None,
        file_path: str | None = None,
        asset_type: UploadType | None = None,
    ) -> dict[str, Any]:
        """Update asset metadata or Model content. Metadata keys use Roblox's camelCase field names.

        Supply update_mask for metadata changes. Only Model content is supported by Roblox;
        pass its absolute file_path and asset_type='Model'. Metadata-only updates return
        updated fields; content updates can return an operation to poll.
        """
        if not metadata and not file_path:
            raise ToolError("Provide metadata or a Model file to update.")
        if metadata and not update_mask:
            raise ToolError("An update_mask is required for metadata changes.")
        request = {**metadata, "assetId": asset_id}
        if file_path:
            if asset_type != "Model":
                raise ToolError("Roblox only supports content updates for Model assets.")
            return await manager.upload(request, file_path, asset_type, asset_id=asset_id, update_mask=update_mask)
        if asset_type is not None:
            raise ToolError("asset_type requires a file_path.")
        import json

        return await manager.request(
            "PATCH",
            f"/assets/{asset_id}",
            params={"updateMask": update_mask},
            files={"request": (None, json.dumps(request), "application/json")},
        )

    @mcp.tool
    async def list_asset_versions(
        asset_id: AssetId,
        page_size: Annotated[int, Field(ge=1, le=50)] = 8,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List an asset's versions; pass the returned page token to retrieve more."""
        params: dict[str, Any] = {"maxPageSize": page_size}
        if page_token:
            params["pageToken"] = page_token
        return await manager.request("GET", f"/assets/{asset_id}/versions", params=params)

    @mcp.tool
    async def get_asset_version(asset_id: AssetId, version_number: AssetId) -> dict[str, Any]:
        """Get metadata for an asset version."""
        return await manager.request("GET", f"/assets/{asset_id}/versions/{version_number}")

    @mcp.tool
    async def rollback_asset_version(asset_id: AssetId, version_number: AssetId) -> dict[str, Any]:
        """Roll an asset back to an earlier version; this changes the existing asset."""
        return await manager.request(
            "POST",
            f"/assets/{asset_id}/versions:rollback",
            json={"assetVersion": f"assets/{asset_id}/versions/{version_number}"},
        )

    @mcp.tool
    async def archive_asset(asset_id: AssetId) -> dict[str, Any]:
        """Archive an asset; it may stop working in experiences that use it."""
        return await manager.request("POST", f"/assets/{asset_id}:archive")

    @mcp.tool
    async def restore_asset(asset_id: AssetId) -> dict[str, Any]:
        """Restore an archived asset."""
        return await manager.request("POST", f"/assets/{asset_id}:restore")

    return mcp


def main() -> None:
    """Run the stdio server after validating its required API key."""
    api_key = os.environ.get("ROBLOX_API_KEY", "")
    if not api_key.strip():
        raise SystemExit("ROBLOX_API_KEY is required to start roblox-assets-mcp")
    owner_id = os.environ.get("ROBLOX_OWNER_ID", "").strip()
    owner_type = os.environ.get("ROBLOX_OWNER_TYPE", "").strip().lower()
    if not owner_id.isascii() or not owner_id.isdecimal() or int(owner_id) <= 0:
        raise SystemExit("ROBLOX_OWNER_ID must be a positive numeric ID")
    if owner_type not in {"user", "group"}:
        raise SystemExit("ROBLOX_OWNER_TYPE must be user or group")
    check_search = os.environ.get("ROBLOX_VALIDATE_SEARCH_ACCESS", "true").lower()
    if check_search not in {"true", "false"}:
        raise SystemExit("ROBLOX_VALIDATE_SEARCH_ACCESS must be true or false")
    if check_search == "true":
        try:
            asyncio.run(RobloxClient(api_key).validate_access())
        except ToolError as error:
            raise SystemExit(f"ROBLOX_API_KEY validation failed: {error}") from error
    create_server(api_key, owner_id=owner_id, owner_type=owner_type).run(transport="stdio")


if __name__ == "__main__":
    main()
