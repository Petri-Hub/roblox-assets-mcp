import math
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.cwd() / ".env", override=False)

CREATOR_STORE_URL = os.getenv("CREATOR_STORE_URL", "https://apis.roblox.com/toolbox-service/v2/assets").rstrip("/")
DELIVERY_URL = os.getenv("DELIVERY_URL", "https://apis.roblox.com/asset-delivery-api/v1/assetId").rstrip("/")
THUMBNAILS_URL = os.getenv("THUMBNAILS_URL", "https://thumbnails.roblox.com/v1/assets").rstrip("/")
try:
    REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "20.0"))
except ValueError as error:
    raise ValueError("REQUEST_TIMEOUT must be a positive number") from error
if not math.isfinite(REQUEST_TIMEOUT) or REQUEST_TIMEOUT <= 0:
    raise ValueError("REQUEST_TIMEOUT must be a positive number")
