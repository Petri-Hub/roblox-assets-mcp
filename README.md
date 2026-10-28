<h1 align="center">🧱 roblox-assets-mcp</h1>

<br>

<h3 align="center">Find Roblox Creator Store assets and hand their download URLs to an AI assistant.</h3>

<p align="center">
  <a href="https://pypi.org/project/roblox-assets-mcp/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/roblox-assets-mcp?logo=pypi&logoColor=white" /></a> <a href="https://github.com/Petri-Hub/roblox-assets-mcp/actions/workflows/quality.yml"><img alt="Quality" src="https://github.com/Petri-Hub/roblox-assets-mcp/actions/workflows/quality.yml/badge.svg?branch=main" /></a> <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white" /> <a href="LICENSE.md"><img alt="License" src="https://img.shields.io/github/license/Petri-Hub/roblox-assets-mcp" /></a>
</p>

<br>

## About

> **TL;DR:** a local stdio MCP server that searches Roblox Creator Store assets, reads their details and 2D thumbnails, and gets temporary CDN URLs for downloading them. It does not write files or change assets.

## Why it exists

An assistant building a Roblox experience needs to discover reusable models, audio, decals, plugins, and other Creator Store assets. This server exposes the asset part of Roblox Open Cloud as three focused tools. Pepper can choose an asset here, then fetch its temporary URL inside her own sandbox.

## How it works

The server uses [FastMCP](https://gofastmcp.com/) over stdio and [HTTPX](https://www.python-httpx.org/) for HTTPS requests. It sends `ROBLOX_API_KEY` only to Roblox's Open Cloud endpoints. Search and details use the Creator Store API; download URLs come from Asset Delivery; 2D previews come from the public Thumbnails API.

`app.py` defines the MCP tools, `client.py` handles Roblox requests and responses, `config.py` reads environment settings, and `models.py` defines the returned data.

| Tool | What it returns |
|---|---|
| `search_assets` | Creator Store assets matching a query, category, optional free-only filter, and page token |
| `get_asset_details` | An asset's metadata, 2D thumbnail state, and PNG URL when ready |
| `prepare_asset_download` | A temporary HTTPS CDN URL for an asset, without downloading it |

`search_assets` accepts `Audio`, `Place`, `Model`, `Decal`, `Plugin`, `MeshPart`, `Video`, and `FontFamily`. It defaults to free `Model` assets. Use `free_only=false` to include paid results. For later pages, pass the returned `next_page_token` back as `page_token`. A thumbnail may still be pending, in which case `thumbnail_url` is null. CDN URLs are temporary; fetch them promptly, and handle compressed responses in the downloading environment.

## Installation

Create a Roblox Open Cloud API key with these [scopes](https://create.roblox.com/docs/cloud/reference/scopes):

- `creator-store-product:read` for Creator Store search and asset details.
- `legacy-asset:manage` for Asset Delivery download locations.

Allow the machine running the MCP server in the key's IP restrictions. The public 2D thumbnail endpoint needs no additional scope. Keep the key in your MCP client's environment configuration, and revoke temporary keys after use.

Most MCP clients can run the published package with `uvx`:

```json
{
  "mcpServers": {
    "roblox-assets": {
      "command": "uvx",
      "args": ["roblox-assets-mcp"],
      "env": {
        "ROBLOX_API_KEY": "YOUR_ROBLOX_OPEN_CLOUD_API_KEY"
      }
    }
  }
}
```

For local development, use `uv sync` and launch `uv run roblox-assets-mcp` with `ROBLOX_API_KEY` set. Before starting MCP, the server makes one Creator Store search and one Asset Delivery request to check that the key works for both operations. It exits if the key is missing, blank, invalid, or lacks either scope. Roblox authorization and rate-limit failures during later tool calls are returned as explicit errors.

The server also reads `.env` from its working directory. The following values are optional; each uses the shown default when unset. Process environment variables take precedence over `.env` values.

| Variable | Default |
|---|---|
| `CREATOR_STORE_URL` | `https://apis.roblox.com/toolbox-service/v2/assets` |
| `DELIVERY_URL` | `https://apis.roblox.com/asset-delivery-api/v1/assetId` |
| `THUMBNAILS_URL` | `https://thumbnails.roblox.com/v1/assets` |
| `REQUEST_TIMEOUT` | `20.0` seconds |

`ROBLOX_API_KEY` can also come from `.env`. The file is ignored by Git.

## Development and release

After `uv sync`, run `make quality` to check formatting, lint, test, and build the package. Use `make format-apply` to format the code. The quality workflow calls the same Makefile targets for pull requests and pushes to `main`.

The release workflow follows [FAM MCP](https://github.com/Petri-Hub/fam-mcp): publish a GitHub Release tagged with the package version. It checks that the tag matches `pyproject.toml`, builds the distributions, and publishes them to PyPI with GitHub OIDC through the `PyPI - Production` GitHub environment.

## License

MIT. See [LICENSE.md](LICENSE.md).
