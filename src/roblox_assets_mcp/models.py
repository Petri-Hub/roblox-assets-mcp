from typing import Literal

from pydantic import BaseModel

Category = Literal["Audio", "Place", "Model", "Decal", "Plugin", "MeshPart", "Video", "FontFamily"]


class Price(BaseModel):
    amount: str
    currency: str


class Asset(BaseModel):
    id: int
    name: str
    asset_type_id: int | None
    creator_name: str | None
    description: str | None
    price: Price | None
    is_free: bool | None
    has_scripts: bool | None


class SearchResults(BaseModel):
    assets: list[Asset]
    next_page_token: str | None


class AssetDetails(BaseModel):
    asset: Asset
    thumbnail_url: str | None
    thumbnail_state: str | None


class DownloadLocation(BaseModel):
    asset_id: int
    url: str
