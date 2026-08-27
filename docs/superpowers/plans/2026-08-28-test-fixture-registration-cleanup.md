# Test Fixture Registration Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the repository-root `conftest.py` the single global registration layer for the canonical shared spawner fixtures and remove the retired compatibility registrations.

**Architecture:** `src/butlers/testing/shared_fixtures.py` remains the sole definition module for `MockSpawner`, `SpawnerResult`, and `mock_spawner`; root `conftest.py` imports them so pytest registers the fixture for both `tests/` and `roster/`. The redundant `tests/conftest.py` file and smoke-tier re-export block are deleted while `tests/smoke/conftest.py` continues to own only `smoke_db_url`; OpenSpec, repository guidance, and scoped-runner metadata are updated in the same change.

**Tech Stack:** Python 3.12, pytest, Ruff, OpenSpec 1.9, Git

## Global Constraints

- Preserve behavior: fixture definitions, fixture scope, and the `MockSpawner` API do not change.
- Do not remove `MockSpawner`, `SpawnerResult`, `mock_spawner`, or their two smoke tests.
- Do not add a compatibility shim, re-export, or fallback for the deleted `tests/conftest.py` path.
- Keep `tests/smoke/conftest.py::smoke_db_url` unchanged apart from removing the shared-fixture re-export block.
- Keep the root `conftest.py` import and `__all__` export as the single global registration layer.
- Full-suite execution is reserved for final pull-request gating; this implementation uses targeted smoke tests plus complete collection.
- Net test delta must remain zero.

## Baseline Evidence

- `uv run pytest tests/smoke/test_scaffolding.py -q -k 'mock_spawner' --tb=short -n 0` — `2 passed, 2 deselected`.
- `uv run pytest tests/smoke/test_scaffolding.py --fixtures -q -n 0` — resolves `mock_spawner` from `src/butlers/testing/shared_fixtures.py`, `postgres_container` from root `conftest.py`, and `smoke_db_url` from `tests/smoke/conftest.py`.
- `uv run pytest tests/smoke --collect-only -q -n 0` — `24 tests collected`.

---

### Task 1: Retire duplicate shared-fixture registration paths

**Files:**
- Delete: `tests/conftest.py`
- Modify: `tests/smoke/conftest.py`
- Modify: `conftest.py`
- Modify: `src/butlers/testing/scoped_runner.py`
- Modify: `src/butlers/testing/source_test_map.py`

**Interfaces:**
- Consumes: `MockSpawner`, `SpawnerResult`, and `mock_spawner` from `butlers.testing.shared_fixtures`.
- Produces: one pytest-global registration through root `conftest.py`; smoke-local `smoke_db_url`; no `tests/conftest.py` compatibility import surface.

- [ ] **Step 1: Delete the tests-namespace compatibility layer**

Delete `tests/conftest.py` in full. It contains only documentation plus re-exports of the canonical definitions and therefore owns no fixture behavior.

- [ ] **Step 2: Remove only the smoke-tier re-export block**

Delete the `butlers.testing.shared_fixtures` import and `__all__` declaration from `tests/smoke/conftest.py`. Retain its module documentation, migration helper imports, Docker availability flag, and `smoke_db_url` fixture unchanged.

- [ ] **Step 3: Describe the single root registration path accurately**

Replace the opening docstring in root `conftest.py` with:

```python
"""Project-wide pytest configuration and shared fixture registration.

Canonical shared fixture definitions live in
``butlers.testing.shared_fixtures``.  Importing them here registers
``mock_spawner`` once for every configured test tree, including ``tests/`` and
``roster/*/tests/``.
"""
```

Retain the existing import from `butlers.testing.shared_fixtures` and the `__all__ = ["MockSpawner", "SpawnerResult", "mock_spawner"]` declaration.

- [ ] **Step 4: Remove dead deleted-path metadata**

Remove only the exact `"tests/conftest.py"` entry from `FULL_SUITE_FALLBACK_ALLOWLIST` in `src/butlers/testing/scoped_runner.py` and from `FULL_SUITE_TRIGGERS` in `src/butlers/testing/source_test_map.py`. Retain root `"conftest.py"` as the cross-cutting full-suite trigger.

- [ ] **Step 5: Run targeted implementation checks**

Run:

```bash
uv run ruff check conftest.py tests/smoke/conftest.py src/butlers/testing/scoped_runner.py src/butlers/testing/source_test_map.py
uv run ruff format --check conftest.py tests/smoke/conftest.py src/butlers/testing/scoped_runner.py src/butlers/testing/source_test_map.py
uv run pytest tests/smoke/test_scaffolding.py -q -k 'mock_spawner' --tb=short -n 0
uv run pytest tests/smoke/test_scaffolding.py --fixtures -q -n 0
```

Expected: Ruff is clean; both spawner smoke tests pass; `mock_spawner` resolves from `src/butlers/testing/shared_fixtures.py`, `postgres_container` from root `conftest.py`, and `smoke_db_url` from `tests/smoke/conftest.py`.

### Task 2: Align the test contract and repository guidance

**Files:**
- Modify: `openspec/specs/testing/spec.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the single-registration implementation from Task 1.
- Produces: a canonical testing contract and repository note that name definition ownership separately from pytest registration ownership.

- [ ] **Step 1: Rewrite the conftest hierarchy requirement**

Change `### Requirement: Conftest Fixture Hierarchy` so it requires canonical shared fixture definitions in `src/butlers/testing/shared_fixtures.py` and exactly one global registration through root `conftest.py`. In the root scenario, state that root imports and exports `SpawnerResult`, `MockSpawner`, and `mock_spawner`, that this makes the fixture available to both configured test trees, and retain the existing root-owned database fixture guarantees.

- [ ] **Step 2: Delete the retired compatibility scenario**

Delete `#### Scenario: Tests conftest (tests/conftest.py)` and its two lines. Retain the roster conftest scenario unchanged.

- [ ] **Step 3: Update the exact repository note**

Replace the `AGENTS.md` note claiming both conftest files re-export the shared fixtures with a note that definitions live in `src/butlers/testing/shared_fixtures.py`, root `conftest.py` is their sole global pytest registration layer, and tier-specific conftests should contain only fixtures scoped to that tier.

- [ ] **Step 4: Validate the normative contract**

Run:

```bash
openspec validate --strict
make check-spec-overwrites
```

Expected: strict OpenSpec validation succeeds and the body-level overwrite guard reports no new destructive losses.

### Task 3: Prove collection and migration completeness

**Files:**
- Verify: all files changed by Tasks 1 and 2

**Interfaces:**
- Consumes: the implementation and documentation changes.
- Produces: final evidence that all tests still collect through the root registration path and no retired path remains.

- [ ] **Step 1: Re-grep retired paths and wording**

Run:

```bash
rg -n 'tests/conftest\.py|both root .*tests/conftest|Tests conftest|layered across three conftest' conftest.py tests src/butlers/testing openspec/specs/testing/spec.md AGENTS.md
```

Expected: no match refers to the deleted compatibility layer; unrelated conftest examples and generic conftest-handling logic remain.

- [ ] **Step 2: Re-run the fixture contract smoke tests**

Run:

```bash
uv run pytest tests/smoke/test_scaffolding.py -q -k 'mock_spawner' --tb=short -n 0
uv run pytest tests/smoke/test_scaffolding.py --fixtures -q -n 0
```

Expected: `2 passed, 2 deselected`; fixture ownership matches the baseline except the redundant conftest files are no longer registration layers.

- [ ] **Step 3: Collect the configured suite without executing it**

Run with output routed to a log:

```bash
uv run pytest --collect-only -q -n 0 > .tmp/test-fixture-registration-collect.log 2>&1
tail -20 .tmp/test-fixture-registration-collect.log
```

Expected: exit status `0`, a positive `tests collected` summary, and no import or fixture-resolution error.

- [ ] **Step 4: Run final static and diff hygiene checks**

Run:

```bash
uv run ruff check conftest.py tests/smoke/conftest.py src/butlers/testing/scoped_runner.py src/butlers/testing/source_test_map.py
uv run ruff format --check conftest.py tests/smoke/conftest.py src/butlers/testing/scoped_runner.py src/butlers/testing/source_test_map.py
git diff --check
git diff --stat
```

Expected: all checks succeed; `tests/conftest.py` is the only deleted test file; no test function is added or removed, so the net test delta is `0`.

- [ ] **Step 5: Review and commit the scoped change**

Review the diff for spec/code alignment, root fixture ownership, preserved smoke-local behavior, and scoped-runner metadata. Commit only the planned files with:

```bash
git add AGENTS.md conftest.py docs/superpowers/plans/2026-08-28-test-fixture-registration-cleanup.md openspec/specs/testing/spec.md tests/conftest.py tests/smoke/conftest.py src/butlers/testing/scoped_runner.py src/butlers/testing/source_test_map.py
git commit -m "refactor(testing): centralize shared fixture registration"
```

Expected: one scoped implementation commit on `refactor/test-fixture-registration`; no push, pull request, GitHub mutation, or Beads mutation.
