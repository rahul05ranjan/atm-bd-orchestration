lint:
    cd apps/atm-bd-console && uv run --frozen ruff check src tests
    cd apps/atm-bd-console && uv run --frozen pyright src

test:
    cd apps/atm-bd-console && uv run --frozen pytest -q
    uv run --frozen --project apps/atm-bd-console pytest -q .github/scripts/tests
    cd plugins/atm-bd-orchestration && python3 tests/gen_manifest.py --check
    cd plugins/atm-bd-orchestration && python3 -m pytest -q -rs
