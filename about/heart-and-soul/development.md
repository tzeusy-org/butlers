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

- pytest with pytest-asyncio (asyncio_mode = "auto").
- Database tests are separated (`tests/test_db.py`, `tests/test_migrations.py`)
  and can be skipped for fast iteration.
- E2E test suite with benchmark scoring for routing accuracy and session
  reliability.

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
- Git-friendly: Dolt-powered version control with native sync.
- Agent-optimized: JSON output, ready work detection, discovered-from links.

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
uv run ruff check src/ tests/ --output-format concise
uv run ruff format --check src/ tests/ -q
uv run pytest tests/ -q --maxfail=1 --tb=short
```

All three must pass. No exceptions, no "I'll fix the lint later."

## Git Workflow

- Butler configurations (roster/) are git-tracked. Changes to butler identity,
  personality, schedules, or module selection go through git.
- Commits should explain why, not what. The diff shows what changed.
- Work is not complete until it is pushed to the remote. Local-only commits are
  unreliable artifacts.

## Development Environment

- **Package manager:** uv (not pip, not poetry, not conda).
- **Python:** 3.12+
- **Database:** PostgreSQL with JSONB support.
- **Containers:** Docker Compose for the full stack.
- **Frontend:** Vite for the dashboard development server.

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
