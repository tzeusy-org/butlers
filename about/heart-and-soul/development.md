# Development Principles

How work gets done on this project. These are workflow constraints, not
aspirations.

## Test-Driven Development

Write the failing test first. Then write the code that makes it pass.

**Why this matters:**

- Tests written after implementation tend to test what the code does, not what
  it should do. Writing the test first forces you to think about the interface
  before the implementation.
- A failing test is a precise specification. It eliminates ambiguity about what
  "done" means.
- It prevents the "I'll add tests later" pattern, which in practice means tests
  never get added.

**Constraints:**

- New features must have tests before the implementation is merged.
- Bug fixes must include a test that reproduces the bug before the fix is
  applied.
- Test scope should be gradual: start with targeted unit tests during
  development, expand to integration tests during stabilization, run the full
  suite only for final pre-merge validation.

**Test infrastructure:**

- pytest with pytest-asyncio (asyncio_mode = "auto"). Default `addopts` use
  importlib import mode and exclude the `nightly`, `bench`, and `perf` markers.
- Tests are separated by marker, not by a single file. The relevant markers are
  `unit`, `smoke`, `integration`, `db`, `e2e`, `nightly`, `bench`, and
  `contract` (architectural invariants from this doctrine and key RFCs).
  Database and migration tests live under `tests/migrations/`, `tests/config/`,
  and `tests/core/` and carry the `integration`/`db`/`nightly` markers, so they
  are selected or skipped by marker rather than by ignoring named files.
- E2E test suite with benchmark scoring for routing accuracy and session
  reliability. E2E tests require `ANTHROPIC_API_KEY`, the `claude` binary, and
  Docker; they are not part of the default CI pipeline and run via
  `make test-e2e` (validate mode) or `make test-e2e-benchmark` (model
  scorecards). A separate frontend Playwright e2e suite does run in CI (see the
  `frontend-e2e` job below) and locally via `make test-e2e-frontend`.

## OpenSpec-Driven Development

Significant features start with a specification before code is written. The
spec captures the problem, the proposed design, alternatives considered, and
the migration path.

**Why specs before code:**

- They force clarity about what problem is being solved and why this particular
  approach was chosen.
- They create a reviewable artifact that is cheaper to change than code.
- They document the "why" that commit messages and code comments rarely capture.

**When a spec is required:**

- New modules.
- Changes to core infrastructure (state store, scheduler, spawner, session log).
- New butler additions to the roster.
- Cross-butler protocol changes.
- Database schema changes that affect multiple butlers.

**When a spec is NOT required:**

- Bug fixes.
- Refactoring that does not change behavior.
- Documentation.
- Test improvements.
- Single-butler tool additions that stay within the module boundary.

**When an RFC is required:**

Architectural constraints documented in `vision.md` are non-negotiable by
default. Any proposed exception --- such as cross-schema data access that
bypasses MCP-only inter-butler communication --- requires a dedicated RFC with
explicit guardrails, reuse criteria, and cost justification. The RFC must
define both when the exception pattern MAY be reused and when it MUST NOT
(see RFC 0010 as the template).

## Manifesto-Driven Design

Every butler has a `MANIFESTO.md` that defines its identity, purpose, and
boundaries. The manifesto is not aspirational marketing copy. It is a binding
contract that governs what features, tools, and interactions belong to that
butler.

**How to use manifestos:**

- Before adding a tool to a butler, check whether the manifesto supports it.
  If the tool does not align with what the butler promises, it belongs elsewhere.
- Before expanding a butler's scope, update the manifesto first and get
  alignment. The code change follows the manifesto change, not the other way
  around.
- When two butlers could plausibly own a capability, the manifestos resolve the
  dispute. Whichever butler's manifesto more naturally encompasses the capability
  gets it.

**Constraint:** A feature that contradicts its butler's manifesto must not ship.

## Issue Tracking with Beads

All work tracking uses `bd` (beads). Not markdown TODOs. Not external issue
trackers. Not task lists in comments.

**Why beads:**

- Dependency-aware: blockers and relationships between issues are first-class.
- Dolt-backed: issues live in a Dolt database, not in git or SQLite.
- Agent-optimized: JSON output, ready work detection, discovered-from links.

**Backend (how the data actually lives):** `bd` v1.0.x is backed by the shared
Dolt server on `127.0.0.1:3307` (database `butlers`), discovered via
`.beads/metadata.json` (`dolt_mode: server`). Writes auto-commit to Dolt
history; there is no `bd sync` step (that subcommand does not exist in this bd
version). To refresh the git-tracked JSONL mirror, run
`bd export -o .beads/issues.export.jsonl`. Never create `.beads/issues.jsonl`:
on bd 1.0.4 server mode its presence triggers a full-file re-import on every
write that can wedge bd town-wide. The mirror lives at
`.beads/issues.export.jsonl` (see `export.path` in `.beads/config.yaml`).

**Workflow:**

1. Check `bd ready` for unblocked work.
2. Claim a task with `bd update <id> --claim`.
3. Do the work.
4. If you discover new work, create a linked issue with `--deps discovered-from:<parent-id>`.
5. Close with `bd close <id> --reason "Done"`.

**Constraint:** Work that is not tracked in beads does not exist for planning
purposes.

## Code Quality

**Linting:** Ruff with target py312, line-length 100, rules E/F/I/UP.

**Formatting:** Ruff format. No debates about style.

**Quality gate before merge:**

```bash
make lint                                                  # ruff check src/ tests/
uv run ruff format --check src/ tests/ roster/ conftest.py -q
make check-for-update-joins                                # SQL safety: FOR UPDATE + outer joins
```

All must pass. No exceptions, no "I'll fix the lint later." The
`check-for-update-joins` gate is structural: PostgreSQL rejects `FOR UPDATE` on
the nullable side of an outer join at runtime, and mock-based tests silently
bypass it, so it is enforced statically.

**CI pipeline** (GitHub Actions, runs automatically on push/PR). There are three
jobs:

1. `check` (Python). Verifies the uv lock file (`uv lock --check`), installs
   with `uv sync --frozen --dev`, then runs lint, the format check, the SQL
   safety check, and the test stages below with coverage:

```bash
# Unit tests: excludes E2E, selects non-integration/non-e2e markers
uv run pytest tests/ -q --maxfail=1 --tb=short --ignore=tests/e2e \
  -m "not integration and not e2e" --cov=src/butlers --cov-report=json:coverage.json

# Smoke tests: fast operational gate plus release evidence
uv run pytest tests/ --ignore=tests/e2e -m smoke -q --tb=short

# Integration tests: requires Docker (testcontainers), runs in parallel
uv run pytest roster/ tests/integration/ tests/config/ tests/core/ tests/migrations/ \
  -q --maxfail=5 --tb=short -m "integration and not nightly" -n auto --dist loadfile \
  --cov=src/butlers --cov-append
```

2. `frontend` (Node 24, `frontend/`). Runs `npm ci`, `npm run lint`
   (`eslint .`), `npm run build` (`tsc -b && vite build`), and `npm run test`
   (`vitest run`).

3. `frontend-e2e` (Node 24, `frontend/`). Installs Playwright browsers, builds,
   and runs `npm run test:e2e`.

Longer-running schema-matrix and migration tests run separately in the nightly
workflow (`-m "nightly or integration"`), not on every push.

The local quality-gate shortcut (`make test-qg`) runs the unit-test scope in
parallel (`-n auto --dist loadfile`); `make test-qg-serial` is the serial
fallback for order-dependent debugging.

## Git Workflow

- Butler configurations (roster/) are git-tracked. Changes to butler identity,
  personality, schedules, or module selection go through git.
- Commits should explain why, not what. The diff shows what changed.
- Work is not complete until it is pushed to the remote. Local-only commits are
  unreliable artifacts.

**Repo-root discipline (non-negotiable):** never move the main repo root
(`~/gt/butlers`) off `main`. Agents and humans both rely on the root checkout
staying on `main` at all times.

- Small, low-regression-risk changes may be committed directly to `main` from
  the root checkout (after a best-effort format/lint pass on the touched files),
  without ever moving HEAD off `main`.
- Anything larger or riskier (features, migrations, broad refactors,
  architectural work) uses a dedicated git worktree branched off `origin/main`,
  with commits, pushes, and PRs all coming from the worktree. Open PRs against
  `--base main`. When in doubt, use a worktree.

See the root `CLAUDE.md` for the exact worktree commands and the full discipline.

## Development Environment

- **Package manager:** uv (not pip, not poetry, not conda).
- **Python:** 3.12+
- **Database:** PostgreSQL with JSONB support.
- **Containers:** Docker Compose for the full stack; testcontainers for
  integration tests.
- **Frontend:** Node 24 with Vite for the dashboard; ESLint for linting, Vitest
  for unit tests, Playwright for e2e.

## Anti-Patterns

- Writing code before writing the test.
- Skipping specs for features that touch core infrastructure.
- Adding tools to a butler without checking its manifesto.
- Tracking work in markdown files, inline TODOs, or external systems instead
  of beads.
- Running the full test suite on every change instead of targeted tests during
  development.
- Committing code that fails lint or format checks.
- Leaving work pushed only locally with "I'll push later."
- Using pip to install dependencies instead of uv.
- Treating architectural rule exceptions as precedent for unconstrained reuse
  without evaluating the specific guardrails and reuse criteria.
