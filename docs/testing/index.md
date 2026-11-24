# Testing

> **Scope:** How testing works in Butlers — strategy, tools, execution.
> **Belongs here:** Test pyramid, pytest markers, fixtures, E2E suite, benchmarks.
> **Does NOT belong here:** Implementation details of individual tests.

- [Testing Strategy](testing-strategy.md) — test pyramid, unit vs integration vs E2E
- [Markers and Fixtures](markers-and-fixtures.md) — pytest markers, shared fixtures, parallel execution
- [Flaky-Test Quarantine](quarantine.md) — how flaky tests leave the blocking lanes, where they still run, how they come back
- [Orphaned Test Containers](orphaned-testcontainers.md) — how pytest leaks Docker containers, how to tell a leak from a kept container, safe sweeping
- [Standard Lint Tools](standard-lint-tools.md) — Ruff bug-class rules, the pinned gitleaks secret scan and actionlint, what `make lint` runs
- [E2E Suite](e2e/README.md) — how to run the end-to-end suite and what each test file proves
- [CI and Test Scope Policy](ci.md) — agent test ladder, gate verdicts, CI topology, CI guard predicates
- [Test Traps](test-traps.md) — hard-won rules for writing, auditing and running tests

## Quick start

```bash
uv sync --dev
make test-plan          # which tests your dirty worktree needs (does not run pytest)
uv run pytest tests/test_foo.py -q --tb=short           # one file; widen gradually
uv run pytest tests/test_foo.py::test_bar -n 0          # one test (avoid inherited xdist)
make test-unit          # -m unit
make test-integration   # -m integration (Docker)
make test-e2e-validate  # E2E (Docker, ANTHROPIC_API_KEY, claude CLI); always -n 0
```

Unit and integration are **marker selections**, not directories: both span `tests/` and `roster/`
(the CI selections are `CI_UNIT_PYTEST_ARGS` and `CI_INTEGRATION_PYTEST_ARGS` in the `Makefile`).
`pyproject.toml` defaults pytest to `-n 3 --dist loadfile`; E2E must override it with `-n 0`. The
exact agent test ladder and CI-shaped final commands are in [CI and test scope policy](ci.md).

Python typing: [`make typecheck` and its shrinking baseline](typechecking.md).
