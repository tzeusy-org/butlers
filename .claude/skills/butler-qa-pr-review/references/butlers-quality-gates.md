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
```

Interpretation:

- `make lint`: repo-standard lint entrypoint
- `make test-qg`: repo-standard quality-gate pytest scope
- `make check`: lint plus the full suite

## How To Use This Reference

- If a GitHub check is clearly mapped to one of the commands above, reproduce
  that exact failure locally first.
- During active PR review follow-up, prefer targeted tests while iterating.
- Before final handoff, rerun the relevant local reproduction commands for the
  checks you touched, then verify the remote GitHub checks are green.
- If a required check is failing because of infrastructure or an unrelated base
  branch issue, report that explicitly instead of claiming success.
