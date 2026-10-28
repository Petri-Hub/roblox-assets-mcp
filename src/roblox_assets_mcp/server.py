import asyncio
import os
from typing import Annotated

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from roblox_assets_mcp.client import RobloxClient
from roblox_assets_mcp.models import AssetDetails, Category, DownloadLocation, SearchResults

AssetId = Annotated[int, Field(gt=0, description="Roblox asset ID")]
PageSize = Annotated[int, Field(ge=1, le=100, description="Number of results per page")]


def create_server(api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> FastMCP:
    """Create an asset-only Roblox MCP server with a required Open Cloud API key."""
    roblox = RobloxClient(api_key, transport)
    mcp = FastMCP(
        name="Roblox Assets",
        instructions=(
            "Search Roblox Creator Store assets, inspect details and 2D thumbnails, and obtain "
            "temporary asset download URLs. Search before choosing an asset. Use free_only=True "
            "for free assets. Download URLs expire; fetch them promptly in your own sandbox. "
            "This server does not write files or manage Roblox assets."
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

    return mcp


def main() -> None:
    """Run the stdio server after validating its required API key."""
    api_key = os.environ.get("ROBLOX_API_KEY", "")
    if not api_key.strip():
        raise SystemExit("ROBLOX_API_KEY is required to start roblox-assets-mcp")
    try:
        asyncio.run(RobloxClient(api_key).validate_access())
    except ToolError as error:
        raise SystemExit(f"ROBLOX_API_KEY validation failed: {error}") from error
    create_server(api_key).run(transport="stdio")


if __name__ == "__main__":
    main()
