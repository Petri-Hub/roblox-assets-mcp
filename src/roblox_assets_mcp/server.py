import asyncio
import math
import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from roblox_assets_mcp.models import Asset, AssetDetails, DownloadLocation, Price, SearchResults

load_dotenv(Path.cwd() / ".env", override=False)

CREATOR_STORE_URL = os.getenv(
    "CREATOR_STORE_URL", "https://apis.roblox.com/toolbox-service/v2/assets"
).rstrip("/")
DELIVERY_URL = os.getenv(
    "DELIVERY_URL", "https://apis.roblox.com/asset-delivery-api/v1/assetId"
).rstrip("/")
THUMBNAILS_URL = os.getenv("THUMBNAILS_URL", "https://thumbnails.roblox.com/v1/assets").rstrip("/")
try:
    REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "20.0"))
except ValueError as error:
    raise ValueError("REQUEST_TIMEOUT must be a positive number") from error
if not math.isfinite(REQUEST_TIMEOUT) or REQUEST_TIMEOUT <= 0:
    raise ValueError("REQUEST_TIMEOUT must be a positive number")

Category = Literal["Audio", "Place", "Model", "Decal", "Plugin", "MeshPart", "Video", "FontFamily"]
AssetId = Annotated[int, Field(gt=0, description="Roblox asset ID")]
PageSize = Annotated[int, Field(ge=1, le=100, description="Number of results per page")]


def _price(product: dict[str, Any]) -> Price | None:
    raw_price = product.get("purchasePrice")
    if not raw_price:
        return None
    quantity = raw_price.get("quantity", {})
    if "significand" not in quantity or "exponent" not in quantity:
        return None
    amount = Decimal(quantity["significand"]).scaleb(quantity["exponent"])
    return Price(amount=str(amount), currency=raw_price.get("currencyCode", ""))


def _asset(item: dict[str, Any]) -> Asset:
    raw_asset = item.get("asset", item)
    creator = item.get("creator") or {}
    product = item.get("creatorStoreProduct") or {}
    price = _price(product)
    return Asset(
        id=int(raw_asset["id"]),
        name=raw_asset["name"],
        asset_type_id=raw_asset.get("assetTypeId"),
        creator_name=creator.get("name"),
        description=raw_asset.get("description"),
        price=price,
        is_free=Decimal(price.amount) == 0 if price else None,
        has_scripts=raw_asset.get("hasScripts"),
    )


async def _validate_api_key(
    api_key: str, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=transport) as client:
        try:
            search = await _request_json(
                client,
                "POST",
                f"{CREATOR_STORE_URL}:search",
                api_key=api_key,
                json={
                    "query": "tree",
                    "searchCategoryType": "Model",
                    "maxPriceCents": 0,
                    "maxPageSize": 1,
                    "searchView": "Full",
                },
            )
        except ToolError as error:
            raise ToolError(f"Creator Store access check failed: {error}") from error

        try:
            asset_id = search["creatorStoreAssets"][0]["asset"]["id"]
        except (KeyError, IndexError, TypeError) as error:
            raise ToolError("Creator Store access check returned no usable asset.") from error

        try:
            delivery = await _request_json(
                client, "GET", f"{DELIVERY_URL}/{asset_id}", api_key=api_key
            )
        except ToolError as error:
            raise ToolError(f"Asset Delivery access check failed: {error}") from error
        if not delivery.get("location"):
            raise ToolError("Asset Delivery access check returned no download URL.")


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    api_key: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    headers = {"x-api-key": api_key} if api_key else None
    try:
        response = await client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
        return payload
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if status in (401, 403):
            raise ToolError(
                f"Roblox denied access (HTTP {status}). Check the API key and scopes."
            ) from error
        if status == 429:
            raise ToolError("Roblox rate limit reached (HTTP 429). Try again later.") from error
        raise ToolError(f"Roblox request failed (HTTP {status}).") from error
    except (httpx.RequestError, ValueError) as error:
        raise ToolError("Could not read a valid response from Roblox.") from error


def create_server(api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> FastMCP:
    """Create an asset-only Roblox MCP server with a required Open Cloud API key."""
    if not api_key.strip():
        raise ValueError("ROBLOX_API_KEY is required")

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
        body: dict[str, Any] = {
            "query": query,
            "searchCategoryType": category,
            "maxPageSize": page_size,
            "searchView": "Full",
        }
        if free_only:
            body["maxPriceCents"] = 0
        if page_token:
            body["pageToken"] = page_token
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=transport) as client:
            payload = await _request_json(
                client, "POST", f"{CREATOR_STORE_URL}:search", api_key=api_key, json=body
            )
        if not isinstance(payload.get("creatorStoreAssets"), list):
            raise ToolError("Roblox returned search results in an unexpected format.")
        try:
            assets = [_asset(item) for item in payload["creatorStoreAssets"]]
        except (KeyError, TypeError, ValueError) as error:
            raise ToolError("Roblox returned asset data in an unexpected format.") from error
        return SearchResults(assets=assets, next_page_token=payload.get("nextPageToken"))

    @mcp.tool
    async def get_asset_details(asset_id: AssetId) -> AssetDetails:
        """Get a Creator Store asset's metadata and its 2D thumbnail URL.

        Args:
            asset_id: ID from search_assets or a Roblox asset page.

        Returns:
            Asset metadata, thumbnail state, and a PNG URL when ready.
        """
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=transport) as client:
            details = await _request_json(
                client, "GET", f"{CREATOR_STORE_URL}/{asset_id}", api_key=api_key
            )
            thumbnails = await _request_json(
                client,
                "GET",
                THUMBNAILS_URL,
                params={
                    "assetIds": asset_id,
                    "size": "420x420",
                    "format": "Png",
                    "isCircular": "false",
                },
            )
        if not isinstance(thumbnails.get("data"), list):
            raise ToolError("Roblox returned thumbnails in an unexpected format.")
        try:
            asset = _asset(details)
            thumbnail = next(
                (entry for entry in thumbnails["data"] if entry.get("targetId") == asset_id),
                {},
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ToolError("Roblox returned asset details in an unexpected format.") from error
        state = thumbnail.get("state")
        return AssetDetails(
            asset=asset,
            thumbnail_url=thumbnail.get("imageUrl") if state == "Completed" else None,
            thumbnail_state=state,
        )

    @mcp.tool
    async def prepare_asset_download(asset_id: AssetId) -> DownloadLocation:
        """Get a temporary CDN URL for an asset; fetch it promptly in your own sandbox.

        Args:
            asset_id: ID of an asset selected from Creator Store.

        Returns:
            The asset ID and a temporary HTTPS CDN URL; no file is written.
        """
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=transport) as client:
            payload = await _request_json(
                client, "GET", f"{DELIVERY_URL}/{asset_id}", api_key=api_key
            )
        url = payload.get("location")
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or not parsed.hostname:
            raise ToolError("Roblox did not return a valid asset download URL.")
        return DownloadLocation(asset_id=asset_id, url=url)

    return mcp


def main() -> None:
    """Run the stdio server after validating its required API key."""
    api_key = os.environ.get("ROBLOX_API_KEY", "")
    if not api_key.strip():
        raise SystemExit("ROBLOX_API_KEY is required to start roblox-assets-mcp")
    try:
        asyncio.run(_validate_api_key(api_key))
    except ToolError as error:
        raise SystemExit(f"ROBLOX_API_KEY validation failed: {error}") from error
    create_server(api_key).run(transport="stdio")


if __name__ == "__main__":
    main()
