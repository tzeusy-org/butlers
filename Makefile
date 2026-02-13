.PHONY: lint format test test-unit test-integration test-core test-modules test-qg test-qg-serial test-qg-parallel check

QG_PYTEST_ARGS = tests/ -q --maxfail=1 --tb=short --ignore=tests/test_db.py --ignore=tests/test_migrations.py

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

# Quality-gate scope default (parallel xdist)
test-qg:
	uv run pytest $(QG_PYTEST_ARGS) -n auto

# Quality-gate serial fallback for order-dependent debugging
test-qg-serial:
	uv run pytest $(QG_PYTEST_ARGS)

# Explicit parallel alias (backward compatibility)
test-qg-parallel:
	$(MAKE) test-qg

check: lint test
