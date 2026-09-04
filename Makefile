.PHONY: lint format test test-unit test-integration test-core test-modules test-e2e test-e2e-validate test-e2e-benchmark test-e2e-frontend test-plan test-ci-unit test-ci-integration test-qg test-qg-serial test-qg-parallel check check-guards check-for-update-joins check-ci-test-shards check-test-budget check-em-dashes check-spec-overwrites check-openspec-strict check-countable-tasks check-duplicate-names check-session-links lint-decision-beads lint-decision-beads-strict bump-version release-tag

# Keep quality-gate selection stable across execution modes (coverage expectations unchanged).
QG_PYTEST_ARGS = tests/ -q --maxfail=1 --tb=short --ignore=tests/test_db.py --ignore=tests/test_migrations.py --ignore=tests/e2e
CI_UNIT_PYTEST_ARGS = tests/ roster/ -q --maxfail=1 --tb=short --ignore=tests/e2e -m "not integration and not e2e and not nightly and not bench and not perf" --cov=src/butlers --cov-report=json:coverage.json --cov-report=term-missing
CI_INTEGRATION_PYTEST_ARGS = tests/ roster/ -q --maxfail=5 --tb=short -m "integration and not nightly and not bench and not perf" -n auto --dist loadfile --cov=src/butlers --cov-append --cov-report=json:coverage.json --cov-report=term-missing
BASE ?= origin/main

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

# Print a fail-closed, dirty-worktree-aware pytest plan. This command does not
# execute pytest and is not verification evidence; it identifies the narrowest
# candidate scope or tells the caller to escalate for shared/migration/unknown
# changes. Use BASE=<ref> when the comparison base differs from origin/main.
test-plan:
	uv run --no-sync python -m butlers.testing.scoped_runner --base "$(BASE)"

# Every pytest-based E2E target below passes `-n 0` (bu-ejgwv). It is not redundant:
# pytest prepends `addopts` to every invocation, and this repo's addopts carries
# `-n 3 --dist loadfile` -- so a target that merely *omits* `-n` still booted three xdist
# workers. E2E cannot survive that. `butler_ecosystem` is session-scoped and every worker
# gets its own session, so three workers boot three full ecosystems; tests/e2e/conftest.py
# offsets every roster port by a fixed `E2E_PORT_OFFSET = 11000` with no worker component,
# so all three contend for the identical ports.
#
# The alternative fix -- making E2E_PORT_OFFSET worker-aware -- was rejected because these
# targets also pass `-s`, and xdist silently drops `-s` (capture=no): under xdist the
# streamed phase-by-phase bootstrap output these targets exist to show is swallowed. Serial
# is the only mode in which the recipe means what it says. Worker-aware ports would also
# triple the real cost of a run (three PostgreSQL testcontainers, three sets of butler
# daemons, three times the live-model spend) and split the session-end cost/benchmark
# scorecards across three processes.
#
# `-n 0`, not `-p no:xdist`: the latter turns the inherited `-n 3` into an
# unrecognized-argument error. xdist derives `--dist no` and an empty tx list from `-n 0`,
# so the leftover `--dist loadfile` in addopts is inert.
# tests/contracts/test_qg_serial_target.py pins the effective value by running each
# target's real argv, so addopts cannot silently re-parallelise them again.
#
# test-e2e-frontend is unaffected: it shells out to Playwright via npm and never reads
# pytest's addopts.

# E2E tests — requires ANTHROPIC_API_KEY, claude binary, and Docker
test-e2e:
	uv run pytest tests/e2e/ -v -s -n 0

# E2E validate mode — run scenarios with current model, hard fail on first mismatch
# This is the default E2E mode (no --benchmark flag).
test-e2e-validate:
	uv run pytest tests/e2e/ -v -s -n 0 -m "e2e and not benchmark"

# E2E benchmark mode — iterate over models, accumulate without hard failures,
# generate scorecards at session end.
# Requires BENCHMARK_MODELS env var or --benchmark-models=<model1>,<model2> argument.
# Example: make test-e2e-benchmark BENCHMARK_MODELS=claude-sonnet-4-5,gpt-4o
test-e2e-benchmark:
	E2E_BENCHMARK_MODELS="$(BENCHMARK_MODELS)" uv run pytest tests/e2e/ -v -s -n 0 --benchmark \
		$(if $(BENCHMARK_MODELS_FLAG),--benchmark-models=$(BENCHMARK_MODELS_FLAG),)

# Frontend Playwright e2e tests — requires dev server running on localhost:5173
# Start the server first: cd frontend && npm run dev
# Install browsers once: cd frontend && npm run test:e2e:install
test-e2e-frontend:
	cd frontend && npm run test:e2e

# Every quality-gate run goes through scripts/pytest_gate.py (bu-5hp74, bu-ecizp): a killed or
# truncated run has no summary line and no FAILED line, so a raw `uv run pytest` piped into any
# grep-shaped check reads as green. `run` records a `## pytest-gate exit=N` receipt written by the
# child, `verdict` refuses to call a log green without one, and its exit status (0 PASS / 1 FAILED /
# 2 UNKNOWN) is what make sees -- so an UNKNOWN fails the target instead of passing it.
#
# `uv run python`, never `python3`: --python defaults to sys.executable, and a bare python3 on PATH
# is an interpreter without the `butlers` package -> ModuleNotFoundError -> pytest exit 4 -> UNKNOWN
# (fixed in adb0261bc for the CLAUDE.md snippet; the same trap applies here).
#
# --tee mirrors the log to the terminal as it grows, so these stay watchable. $$$$ is the shell's
# PID: make eats one $ per pair, and the timestamp+PID pair keeps concurrent runs off each other.
QG_GATE = uv run python scripts/pytest_gate.py
QG_LOG = .tmp/test-logs/pytest-$@-$$(date +%Y%m%d-%H%M%S)-$$$$.log

# Mirrors CI's pytest selectors and coverage semantics on one local filesystem.
# Hosted CI runs the unit and integration lanes independently, then combines
# their separate coverage artifacts in its fail-closed `check` fan-in; these
# sequential convenience targets use --cov-append instead. The smoke
# release-evidence step, static checks, and throwaway-runner Ryuk override stay
# CI-only; local testcontainers keep Ryuk enabled for cleanup. These targets use
# pytest_gate so a killed foreground process never looks green.
test-ci-unit:
	LOG="$(QG_LOG)"; \
	if [ -n "$$(git status --porcelain)" ]; then echo "REFUSED: test-ci receipts require a clean worktree" >&2; exit 2; fi; \
	printf '## test-ci HEAD=%s clean=true target=$@\n' "$$(git rev-parse HEAD)" | tee "$$LOG"; \
	$(QG_GATE) run --tee --log "$$LOG" -- $(CI_UNIT_PYTEST_ARGS); \
	$(QG_GATE) verdict "$$LOG"

test-ci-integration:
	LOG="$(QG_LOG)"; \
	if [ -n "$$(git status --porcelain)" ]; then echo "REFUSED: test-ci receipts require a clean worktree" >&2; exit 2; fi; \
	printf '## test-ci HEAD=%s clean=true target=$@\n' "$$(git rev-parse HEAD)" | tee "$$LOG"; \
	$(QG_GATE) run --tee --log "$$LOG" -- $(CI_INTEGRATION_PYTEST_ARGS); \
	$(QG_GATE) verdict "$$LOG"

# Quality-gate default: parallel xdist (see docs/PYTEST_QG_ALTERNATIVES_QKX5.md benchmark).
# --dist loadfile keeps tests from the same file on the same worker so module-scoped fixtures
# are not torn down mid-module (important for shared FastAPI app and module-scoped DB pools).
test-qg:
	LOG="$(QG_LOG)"; \
	$(QG_GATE) run --tee --log "$$LOG" -- $(QG_PYTEST_ARGS) -n auto --dist loadfile; \
	$(QG_GATE) verdict "$$LOG"

# Same quality-gate scope as test-qg, serial fallback for order-dependent debugging.
#
# `-n 0` is not redundant (bu-bcujm). pytest prepends `addopts` to every invocation, and this
# repo's addopts carries `-n 3 --dist loadfile` -- so a target that merely *omits* `-n` still
# ran on three xdist workers, reshuffling the very execution order this target exists to hold
# still. `-n 0` is the only thing that overrides it; `-p no:xdist` would instead turn the
# inherited `-n 3` into an unrecognized-argument error. xdist derives `--dist no` and an empty
# tx list from `-n 0`, so the leftover `--dist loadfile` in addopts is inert.
# tests/contracts/test_qg_serial_target.py pins the effective value by running this target's
# real argv, so addopts cannot silently re-parallelise it again.
test-qg-serial:
	LOG="$(QG_LOG)"; \
	$(QG_GATE) run --tee --log "$$LOG" -- $(QG_PYTEST_ARGS) -n 0; \
	$(QG_GATE) verdict "$$LOG"

# Explicit parallel alias (backward compatibility)
test-qg-parallel:
	$(MAKE) test-qg

# Structural SQL safety: flag FOR UPDATE queries with unqualified outer joins.
# PostgreSQL raises "FOR UPDATE cannot be applied to the nullable side of an
# outer join" at runtime — mock-based tests silently bypass this.
# Safe form: FOR UPDATE OF <table>  (excludes the nullable join side)
check-for-update-joins:
	python3 scripts/check_for_update_joins.py src/ tests/ roster/

# Fail-closed replacement for the former integration-only coverage guard.
# Collects the real unit and integration marker populations and rejects stale,
# missing, overlapping, or zero-selected checked-in file manifests.
check-ci-test-shards:
	uv run --no-sync python scripts/check_ci_test_shards.py

# Per-lane collected-test budget ratchet (bu-r5mnn). Fails when a CI lane
# collects more tests than scripts/test-budget-baseline.json allows: condense
# tests in the same PR, or raise the budget with
# `uv run --no-sync python scripts/check_test_budget.py --update-baseline` and
# state the net test delta and why in the PR body. Mirrors the CI
# check-preflight step.
check-test-budget:
	uv run --no-sync python scripts/check_test_budget.py

# Non-negotiable #6: no em-dashes in doctrine or dashboard copy. Ratchets a
# per-file baseline (scripts/em-dash-baseline.json) so pre-existing debt is
# frozen while any net-new em-dash fails. Mirrors the em-dash step of the CI `guards` job.
check-em-dashes:
	python3 scripts/check-no-em-dashes.py

# Regression guard for bu-s9uv3: `openspec archive` writes the WHOLE requirement
# block into the baseline, so an unarchived MODIFIED block authored against an
# older ancestor silently deletes whatever the baseline gained since. OpenSpec
# 1.9.0 only compares scenario NAMES, so a block that keeps every name and guts
# the bodies validates clean. This compares bodies. A digest-keyed ratchet
# (scripts/spec-overwrite-baseline.json) freezes today's debt; it re-fires the
# moment an archive moves a baseline requirement under an unarchived block.
# Mirrors the spec-overwrite step of the CI `guards` job.
check-spec-overwrites:
	python3 scripts/check_spec_overwrites.py

# Regression gate for bu-n58sv: validate every canonical spec and unarchived
# change with OpenSpec's strict RFC-2119 and scenario-shape checks. Keep the
# command explicit so a bare `openspec validate --strict` cannot silently
# validate nothing.
check-openspec-strict:
	openspec validate --all --strict

# Regression guard for bu-h7igs: `openspec archive`'s incomplete-task gate counts
# markdown checkboxes only, so a `### N.` heading-style tasks.md reports
# `Task status: No tasks`, cannot be incomplete, and archives unprompted -- and
# that silence reads as evidence the tasks were done. Fails on any unarchived
# change whose tasks.md the gate cannot see. Mirrors the countable-tasks
# step of the CI `guards` job.
check-countable-tasks:
	python3 scripts/check_countable_tasks.py

# Regression guard for bu-ayrbg: two branches each added a module-level helper
# with the same name to one file, git auto-merged them cleanly, and the later
# definition silently shadowed the earlier one for every caller. `ruff` stays
# green -- F811 skips a redefinition whose earlier definition is used in
# between, which is exactly the shape a merge produces. Scans the lint gate's
# own scope and fails on a zero-file scan so it cannot go quiet. Mirrors the duplicate-name
# step of the CI `guards` job.
check-duplicate-names:
	python3 scripts/check_duplicate_toplevel_names.py

# Every guard the CI `guards` job runs, locally, before you push (bu-r5mnn).
# check-openspec-strict is skipped with a message when `openspec` is not on
# PATH; everything else is a plain python3 script. The frontend-copy inventory
# check regenerates the committed file and fails on any diff, exactly as CI
# does, so run it on a clean worktree or expect the diff to be yours.
check-guards: check-em-dashes check-spec-overwrites check-countable-tasks check-duplicate-names check-session-links
	python3 scripts/check_archived_requirements_landed.py
	python3 scripts/check_cited_requirements_resolve.py
	python3 scripts/extract-frontend-copy.py
	git diff --exit-code -- about/lay-and-land/frontend-copy-inventory.md
	@if command -v openspec >/dev/null 2>&1; then \
		$(MAKE) check-openspec-strict; \
	else \
		echo "check-guards: openspec not on PATH, skipping check-openspec-strict (CI runs it)"; \
	fi

check: lint check-for-update-joins check-ci-test-shards check-test-budget check-guards test

# Local dry run of the session-link step of the CI `guards` job (bu-mr5t5): scans commit
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
