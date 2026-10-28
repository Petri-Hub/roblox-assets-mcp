.PHONY: format-apply format-check lint test build quality

quality: format-check lint test build

format-apply:
	uv run --locked ruff format .

format-check:
	uv run --locked ruff format --check .

lint:
	uv run --locked ruff check .

test:
	uv run --locked pytest

build:
	uv build
