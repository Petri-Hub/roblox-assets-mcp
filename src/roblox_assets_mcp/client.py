from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx
from fastmcp.exceptions import ToolError

from roblox_assets_mcp.config import CREATOR_STORE_URL, DELIVERY_URL, REQUEST_TIMEOUT, THUMBNAILS_URL
from roblox_assets_mcp.models import Asset, AssetDetails, Category, DownloadLocation, Price, SearchResults


def _parse_price(product: dict[str, Any]) -> Price | None:
    raw_price = product.get("purchasePrice")
    if not raw_price:
        return None
    quantity = raw_price.get("quantity", {})
    if "significand" not in quantity or "exponent" not in quantity:
        return None
    amount = Decimal(quantity["significand"]).scaleb(quantity["exponent"])
    return Price(amount=str(amount), currency=raw_price.get("currencyCode", ""))


def _parse_asset(item: dict[str, Any]) -> Asset:
    raw_asset = item.get("asset", item)
    creator = item.get("creator") or {}
    product = item.get("creatorStoreProduct") or {}
    price = _parse_price(product)
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


def _search_body(
    query: str, category: Category, free_only: bool, page_size: int, page_token: str | None
) -> dict[str, Any]:
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
    return body


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
            raise ToolError(f"Roblox denied access (HTTP {status}). Check the API key and scopes.") from error
        if status == 429:
            raise ToolError("Roblox rate limit reached (HTTP 429). Try again later.") from error
        raise ToolError(f"Roblox request failed (HTTP {status}).") from error
    except (httpx.RequestError, ValueError) as error:
        raise ToolError("Could not read a valid response from Roblox.") from error


class RobloxClient:
    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key.strip():
            raise ValueError("ROBLOX_API_KEY is required")
        self._api_key = api_key
        self._transport = transport

    async def validate_access(self) -> None:
        """Check Creator Store and Asset Delivery access before MCP starts."""
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=self._transport) as client:
            try:
                search = await _request_json(
                    client,
                    "POST",
                    f"{CREATOR_STORE_URL}:search",
                    api_key=self._api_key,
                    json=_search_body("tree", "Model", True, 1, None),
                )
            except ToolError as error:
                raise ToolError(f"Creator Store access check failed: {error}") from error

            try:
                asset_id = search["creatorStoreAssets"][0]["asset"]["id"]
            except (KeyError, IndexError, TypeError) as error:
                raise ToolError("Creator Store access check returned no usable asset.") from error

            try:
                delivery = await _request_json(client, "GET", f"{DELIVERY_URL}/{asset_id}", api_key=self._api_key)
            except ToolError as error:
                raise ToolError(f"Asset Delivery access check failed: {error}") from error
            if not delivery.get("location"):
                raise ToolError("Asset Delivery access check returned no download URL.")

    async def search_assets(
        self,
        query: str,
        category: Category,
        free_only: bool,
        page_size: int,
        page_token: str | None,
    ) -> SearchResults:
        """Search Creator Store and return parsed assets and pagination."""
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=self._transport) as client:
            payload = await _request_json(
                client,
                "POST",
                f"{CREATOR_STORE_URL}:search",
                api_key=self._api_key,
                json=_search_body(query, category, free_only, page_size, page_token),
            )
        if not isinstance(payload.get("creatorStoreAssets"), list):
            raise ToolError("Roblox returned search results in an unexpected format.")
        try:
            assets = [_parse_asset(item) for item in payload["creatorStoreAssets"]]
        except (KeyError, TypeError, ValueError) as error:
            raise ToolError("Roblox returned asset data in an unexpected format.") from error
        return SearchResults(assets=assets, next_page_token=payload.get("nextPageToken"))

    async def get_asset_details(self, asset_id: int) -> AssetDetails:
        """Read asset metadata and its current 2D thumbnail state."""
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=self._transport) as client:
            details = await _request_json(client, "GET", f"{CREATOR_STORE_URL}/{asset_id}", api_key=self._api_key)
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
            asset = _parse_asset(details)
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

    async def prepare_asset_download(self, asset_id: int) -> DownloadLocation:
        """Return Roblox's temporary HTTPS asset download URL."""
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=self._transport) as client:
            payload = await _request_json(client, "GET", f"{DELIVERY_URL}/{asset_id}", api_key=self._api_key)
        url = payload.get("location")
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or not parsed.hostname:
            raise ToolError("Roblox did not return a valid asset download URL.")
        return DownloadLocation(asset_id=asset_id, url=url)
