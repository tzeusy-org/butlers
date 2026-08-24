.PHONY: lint format test test-unit test-integration test-core test-modules test-e2e test-e2e-validate test-e2e-benchmark test-e2e-frontend test-qg test-qg-serial test-qg-parallel check check-for-update-joins check-integration-coverage check-em-dashes check-spec-overwrites check-countable-tasks check-duplicate-names check-session-links lint-decision-beads lint-decision-beads-strict bump-version release-tag

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

# E2E validate mode — run scenarios with current model, hard fail on first mismatch
# This is the default E2E mode (no --benchmark flag).
test-e2e-validate:
	uv run pytest tests/e2e/ -v -s -m "e2e and not benchmark"

# E2E benchmark mode — iterate over models, accumulate without hard failures,
# generate scorecards at session end.
# Requires BENCHMARK_MODELS env var or --benchmark-models=<model1>,<model2> argument.
# Example: make test-e2e-benchmark BENCHMARK_MODELS=claude-sonnet-4-5,gpt-4o
test-e2e-benchmark:
	E2E_BENCHMARK_MODELS="$(BENCHMARK_MODELS)" uv run pytest tests/e2e/ -v -s --benchmark \
		$(if $(BENCHMARK_MODELS_FLAG),--benchmark-models=$(BENCHMARK_MODELS_FLAG),)

# Frontend Playwright e2e tests — requires dev server running on localhost:5173
# Start the server first: cd frontend && npm run dev
# Install browsers once: cd frontend && npm run test:e2e:install
test-e2e-frontend:
	cd frontend && npm run test:e2e

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

# Structural SQL safety: flag FOR UPDATE queries with unqualified outer joins.
# PostgreSQL raises "FOR UPDATE cannot be applied to the nullable side of an
# outer join" at runtime — mock-based tests silently bypass this.
# Safe form: FOR UPDATE OF <table>  (excludes the nullable join side)
check-for-update-joins:
	python3 scripts/check_for_update_joins.py src/ tests/ roster/

# Regression guard for bu-m8cmk: fails if the CI "Integration tests
# (testcontainers)" job's pytest path list would silently miss any
# pytest.mark.integration test that exists elsewhere in the repo.
check-integration-coverage:
	uv run python3 scripts/check_integration_coverage.py

# Non-negotiable #6: no em-dashes in doctrine or dashboard copy. Ratchets a
# per-file baseline (scripts/em-dash-baseline.json) so pre-existing debt is
# frozen while any net-new em-dash fails. Mirrors the CI `em-dash-guard` job.
check-em-dashes:
	python3 scripts/check-no-em-dashes.py

# Regression guard for bu-s9uv3: `openspec archive` writes the WHOLE requirement
# block into the baseline, so an unarchived MODIFIED block authored against an
# older ancestor silently deletes whatever the baseline gained since. OpenSpec
# 1.9.0 only compares scenario NAMES, so a block that keeps every name and guts
# the bodies validates clean. This compares bodies. A digest-keyed ratchet
# (scripts/spec-overwrite-baseline.json) freezes today's debt; it re-fires the
# moment an archive moves a baseline requirement under an unarchived block.
# Mirrors the CI `spec-overwrite-guard` job.
check-spec-overwrites:
	python3 scripts/check_spec_overwrites.py

# Regression guard for bu-h7igs: `openspec archive`'s incomplete-task gate counts
# markdown checkboxes only, so a `### N.` heading-style tasks.md reports
# `Task status: No tasks`, cannot be incomplete, and archives unprompted -- and
# that silence reads as evidence the tasks were done. Fails on any unarchived
# change whose tasks.md the gate cannot see. Mirrors the CI
# `countable-tasks-guard` job.
check-countable-tasks:
	python3 scripts/check_countable_tasks.py

# Regression guard for bu-ayrbg: two branches each added a module-level helper
# with the same name to one file, git auto-merged them cleanly, and the later
# definition silently shadowed the earlier one for every caller. `ruff` stays
# green -- F811 skips a redefinition whose earlier definition is used in
# between, which is exactly the shape a merge produces. Scans the lint gate's
# own scope and fails on a zero-file scan so it cannot go quiet. Mirrors the CI
# `duplicate-name-guard` job.
check-duplicate-names:
	python3 scripts/check_duplicate_toplevel_names.py

check: lint check-for-update-joins check-integration-coverage check-em-dashes check-spec-overwrites check-countable-tasks check-duplicate-names test

# Local dry run of the session-link-guard CI job (bu-mr5t5): scans commit
# messages not yet on origin/main for tool-session link/footer leakage
# before you push. No PR title/body or review comments to check locally, so
# this is a strict subset of what CI enforces — treat it as a pre-push sanity
# check, not a full substitute for the CI job.
check-session-links:
	python3 scripts/session_link_guard.py --commit-range "origin/main..HEAD"

# Decision-bead convention (bu-ckkpz.1): lint open beads carrying the
# `decision` label for structured options/default/deadline. Reads live via
# `bd`, so it needs the local Dolt server and is a manual/local check, not a
# `check` or CI member (GitHub Actions cannot reach the Dolt server).
lint-decision-beads:
	python3 scripts/lint_decision_beads.py

# Non-vacuous variant (bu-hmdqz.6): also flags open, non-epic beads whose
# titles match a legacy decision marker but haven't migrated to the
# `decision` label yet -- see AGENTS.md "Decision-bead convention".
lint-decision-beads-strict:
	python3 scripts/lint_decision_beads.py --check-unlabeled-markers

# Version management — single source of truth is pyproject.toml
# Usage: make bump-version VERSION=1.2.3
# Increments the patch segment if VERSION is not specified:
#   Current: 0.1.0 → make bump-version → 0.1.1
bump-version:
	@if [ -z "$(VERSION)" ]; then \
		python scripts/bump_version.py; \
	else \
		python scripts/bump_version.py "$(VERSION)"; \
	fi

# Create and push a release tag matching the current pyproject.toml version.
# Usage: make release-tag
# This triggers the release workflow in CI.
release-tag:
	@python scripts/release_tag.py
