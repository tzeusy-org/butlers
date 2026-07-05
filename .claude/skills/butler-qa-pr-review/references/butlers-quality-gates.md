# Butlers Quality Gates

Use this reference when the PR's GitHub checks are failing or when you need to
reproduce the expected gates locally before calling the PR done.

## CI Gates In This Repo

From [.github/workflows/ci.yml](../../../.github/workflows/ci.yml), the primary
checks are:

1. `Lint`
2. `Format check`
3. `Unit tests`
4. `Integration tests (testcontainers)`
5. `session-link-guard` (bu-mr5t5): fails the PR when a tool-session link or
   footer (see [scripts/session_link_guard.py](../../../../scripts/session_link_guard.py))
   leaks into the PR body or a commit message on the current head; review
   comments are covered best-effort. Runs on `pull_request` events only.
   Not currently a GitHub-required check on this repo's branch protection —
   same advisory-but-visible standing as the other checks below — so a PR
   can still merge over it red; treat a red run the same way you'd treat any
   other failing gate here and fix it before calling review done.

Treat these as the default required gates for this repository unless the PR
shows a different required-check set in GitHub.

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
