# Butlers Quality Gates

Use this reference when the PR's GitHub checks are failing or when you need to
reproduce the expected gates locally before calling the PR done.

## CI Gates In This Repo

The active `main-merge-queue` ruleset (id 22281319) requires three contexts:
`check`, `guards`, and `frontend`. From
[.github/workflows/ci.yml](../../../.github/workflows/ci.yml):

1. `guards` runs the repository-wide structural and privacy gates, including
   `session-link-guard` (bu-mr5t5).
2. `check` is the fail-closed fan-in over `check-preflight`, five unit shards,
   and five integration shards.
3. `frontend` runs its lint, copy/coercion guards, knip, build, and unit tests
   when the diff touches its path-filtered surface.

The pull-request run is path-filtered, so skipped jobs are expected and a fixed
job count is not a completeness test. `frontend-e2e` remains advisory. After
review and the PR-head gates complete, the sole merge route is
`gh pr merge <n> --squash --auto`; the queue reruns the required contexts on the
exact `merge_group` tree before landing. Do not rebase a clean PR merely to
refresh it.

## Local Reproduction Commands

### Fast local gate reproduction

Use the documented quality-gate sequence from
[docs/testing/testing-strategy.md](../../../docs/testing/testing-strategy.md):

```bash
uv run ruff check src/ tests/ roster/ conftest.py --output-format concise
uv run ruff format --check src/ tests/ roster/ conftest.py -q
uv run pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py \
  -q --maxfail=1 --tb=short
```

### Repo make targets

Useful shortcuts from [Makefile](../../../Makefile):

```bash
make lint
make test-qg
make check
make check-session-links
```

Interpretation:

- `make lint`: repo-standard lint entrypoint
- `make test-qg`: repo-standard quality-gate pytest scope
- `make check`: lint plus the full suite
- `make check-session-links`: local dry run of the `session-link-guard` CI
  job's commit-message check (no PR body/review comments available locally —
  a strict subset of what CI enforces, see the job comment in ci.yml)

## How To Use This Reference

- If a GitHub check is clearly mapped to one of the commands above, reproduce
  that exact failure locally first.
- During active PR review follow-up, prefer targeted tests while iterating.
- Before final handoff, rerun the relevant local reproduction commands for the
  checks you touched, then verify the remote GitHub checks are green.
- If a required check is failing because of infrastructure or an unrelated base
  branch issue, report that explicitly instead of claiming success.
