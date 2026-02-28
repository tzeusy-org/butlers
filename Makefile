.PHONY: lint format test test-unit test-integration test-core test-modules test-e2e test-qg test-qg-serial test-qg-parallel check

# Keep quality-gate selection stable across execution modes (coverage expectations unchanged).
QG_PYTEST_ARGS = tests/ -q --maxfail=1 --tb=short --ignore=tests/test_db.py --ignore=tests/test_migrations.py --ignore=tests/e2e

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

# Full test suite — runs all tests (both unit and integration)
test:
	uv run pytest -v

# Unit tests only — fast, no Docker required
test-unit:
	uv run pytest -m unit -v

# Integration tests only — requires Docker (testcontainers)
test-integration:
	uv run pytest -m integration -v

# Core component tests — tests/core/ directory
test-core:
	uv run pytest tests/core/ -v

# Module tests — tests/modules/ directory
test-modules:
	uv run pytest tests/modules/ -v

# E2E tests — requires ANTHROPIC_API_KEY, claude binary, and Docker
test-e2e:
	uv run pytest tests/e2e/ -v -s

# Quality-gate default: parallel xdist (see docs/PYTEST_QG_ALTERNATIVES_QKX5.md benchmark).
# --dist loadfile keeps tests from the same file on the same worker so module-scoped fixtures
# are not torn down mid-module (important for shared FastAPI app and module-scoped DB pools).
test-qg:
	uv run pytest $(QG_PYTEST_ARGS) -n auto --dist loadfile

# Same quality-gate scope as test-qg, serial fallback for order-dependent debugging.
test-qg-serial:
	uv run pytest $(QG_PYTEST_ARGS)

# Explicit parallel alias (backward compatibility)
test-qg-parallel:
	$(MAKE) test-qg

check: lint test
