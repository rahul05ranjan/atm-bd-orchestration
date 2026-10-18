lint:
    cd apps/atm-bd-console && uv run --frozen ruff check src tests
    cd apps/atm-bd-console && uv run --frozen pyright src
