# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo Root Discipline (NON-NEGOTIABLE)

**NEVER switch the main repo root (`~/gt/butlers`) away from `main`.** Do not
`git checkout -b`, `git switch`, `git checkout <branch>`, or otherwise move HEAD
off `main` in this directory — agents and humans rely on the root checkout
staying on `main` at all times.

To do any branch work (features, fixes, experiments), **always use a dedicated
git worktree** outside the repo (e.g. under `/home/tze/.butlers-worktrees/`):

```bash
git worktree add /home/tze/.butlers-worktrees/<branch-name> -b <branch-name> origin/main
```

Commit, push, and open PRs from the worktree; the root checkout stays on `main`.
When done, `git worktree remove <path>`. If you find the root on any branch other
than `main`, switch it back to `main` before starting work.

**Small, low-regression-risk changes are fine to commit directly to `main`** (the
root checkout stays on `main` — you commit and push from it without ever moving
HEAD off `main`), after a best-effort format/lint pass on the touched files. The
worktree + PR flow above is for anything larger or riskier (features, migrations,
broad refactors, architectural work). When in doubt, use a worktree.

## Project Overview

Butlers is a personal AI agent system where each "butler" is a long-running MCP server daemon with core infrastructure (state store, scheduler, LLM CLI spawner, session log) and opt-in modules (email, telegram, calendar, etc.). When triggered, a butler spawns an ephemeral LLM CLI instance wired exclusively to itself via a locked-down MCP config.

**Tech stack:** Python 3.12+, FastMCP, Claude Agent SDK, PostgreSQL (JSONB-heavy; one DB with per-butler schemas, cross-butler tables in `public`), Docker, asyncio

## Commands

```bash
uv sync --dev          # Install dependencies
make lint              # Lint with ruff
make format            # Format with ruff
make test              # Run all tests (prefer quiet pytest flags for agent runs)
make check             # Lint + test
uv run pytest tests/test_foo.py -q --tb=short          # Run a single test file (quiet)
uv run pytest tests/test_foo.py::test_bar   # Run a single test
uv run ruff check src/ tests/ --output-format concise  # Lint only (quiet)
uv run ruff format src/ tests/              # Format only
```

Test execution policy for bugfixes and features:

- During active development or investigation, prefer targeted `pytest` runs to keep feedback fast and reduce context usage.
- Run the full test suite only when branch changes are finalized and you are doing final pre-merge validation.
- Increase test scope gradually as needed; do not default to full-suite runs early.

For low-context quality gates during agent runs:

```bash
uv run ruff check src/ tests/ roster/ conftest.py --output-format concise
uv run ruff format --check src/ tests/ roster/ conftest.py -q
mkdir -p .tmp/test-logs
PYTEST_LOG=".tmp/test-logs/pytest-$(basename "$PWD")-$(date +%Y%m%d-%H%M%S)-$$.log"
uv run pytest tests/ --ignore=tests/e2e -q --maxfail=1 --tb=short >"$PYTEST_LOG" 2>&1 || tail -n 120 "$PYTEST_LOG"
```

## Architecture

### Two-Layer Butler Design

Every butler has **core components** (always present) and **modules** (opt-in per butler):

- **Core:** State store (KV JSONB), task scheduler (cron-driven), LLM CLI spawner (ephemeral LLM CLI via SDK), session log, tick handler, status
- **Modules:** Pluggable units adding domain-specific MCP tools (email, telegram, calendar, etc.). Implement the `Module` abstract base class with `register_tools(mcp, config, db, butler_name)`, `migrations()`, `on_startup()`, `on_shutdown()`.

### Trigger Flow

1. Trigger arrives (external MCP call or internal scheduler due task)
2. LLM CLI Spawner generates ephemeral MCP config → spawns LLM CLI via SDK
3. Claude Code calls butler's MCP tools, runs skill scripts, returns
4. Butler logs the session

### Special Butlers

- **Switchboard Butler:** Routes external MCP requests to the correct butler

### Database Isolation

Target-state isolation is schema-based in a single PostgreSQL database: each butler role can access only its own schema plus `public`. Inter-butler communication remains MCP-only through the Switchboard.

The `public` schema contains cross-butler identity tables:
- **`public.contacts`** — canonical contact registry; one row per known person/actor. Includes a `roles` array (e.g. `['owner']`) and optional `entity_id` FK to the memory butler's entity graph.
- **`public.contact_info`** — per-channel identifiers linked to contacts (e.g. Telegram chat ID, email address). UNIQUE on `(type, value)`. `secured=true` marks credential entries.

These tables power identity resolution for all ingress routing (Switchboard reverse-lookup) and outbound targeting (`notify()` with `contact_id`). The owner contact is bootstrapped automatically on daemon startup.

### Runtime Config Architecture

Butler operational config (concurrency, core_groups) follows a seed-and-manage pattern:

- **`[butler.runtime_seed]`** in `butler.toml` provides initial defaults (seed). This section replaces the old `[butler.runtime]` and `[butler.seed_configs]`.
- **`{schema}.runtime_config`** DB table is the runtime source of truth, seeded from toml on first boot. It holds only **`core_groups`, `max_concurrent`, `max_queued`** — all cold fields requiring a daemon restart to take effect.
- **`RuntimeConfigAccessor`** (`src/butlers/core/runtime_config.py`) provides TTL-cached access (30s) to the DB table.
- **Dashboard API** at `GET/PATCH /api/butlers/{name}/runtime-config` reads/writes the DB table.

**Model / runtime / session_timeout config lives elsewhere.** As of migration `core_073`, `model`, `runtime_type`, `args`, and `session_timeout_s` were moved OFF `runtime_config` ONTO **`public.model_catalog`** (`session_timeout_s INT NOT NULL DEFAULT 1800`). These are resolved per **complexity tier** by `src/butlers/core/model_routing.py` (`resolve_model()` returns the catalog entry id and `session_timeout_s` for the chosen tier) and edited via the **Models tab** / `GET/PATCH /api/model-settings` (`src/butlers/api/routers/model_settings.py`), not the runtime-config surface.

### Butler Config Directory (git-based, `roster/`)

```
roster/butler-name/
├── MANIFESTO.md    # Public-facing identity, purpose, and value proposition
├── CLAUDE.md       # Butler personality/instructions (system prompt)
├── AGENTS.md       # Runtime agent notes
├── api/            # Dashboard API routes (optional)
│   ├── router.py   # FastAPI router (exports module-level 'router' variable)
│   └── models.py   # Pydantic models for request/response schemas
├── .agents/skills/ # Skills available to runtime instances (Codex discovery)
├── .claude -> .agents  # Claude Code compatibility symlink
└── butler.toml     # Identity, schedule, modules config
```

### Creating a New Butler

When adding a new butler to the roster, follow this checklist:

1. **Directory structure:** Create `roster/{butler-name}/` with required config files
2. **MANIFESTO.md:** Define the butler's identity, purpose, and value proposition
3. **CLAUDE.md:** Write the butler personality and system prompt instructions
4. **AGENTS.md:** Initialize with "# Notes to self" header for runtime agent notes
5. **butler.toml:** Configure identity (name, description), schedule (cron expressions), and enabled modules
6. **Database schema:** Create Alembic migration in `src/butlers/migrations/versions/` for butler-specific tables
7. **MCP tools:** If needed, implement butler-specific MCP tools as a custom module in `src/butlers/modules/`
8. **Dashboard routes:** If the butler needs web dashboard endpoints, create `roster/{butler-name}/api/router.py` and `models.py`
   - router.py must export a module-level `router` variable (APIRouter instance)
   - Use `from butlers.api.db import DatabaseManager` and `Depends(_get_db_manager)` for DB access
   - No `__init__.py` needed in the api/ directory
   - Auto-discovery handles registration via `src/butlers/api/router_discovery.py`
9. **Skills:** Add butler-specific skills to `roster/{butler-name}/.agents/skills/` (each skill needs a SKILL.md)
10. **Tests:** Write unit tests for MCP tools, API routes, and database operations
11. **Switchboard registration:** Update the Switchboard butler to route requests to the new butler

## Code Layout

```
src/butlers/         # Main package
  modules/
    base.py          # Module abstract base class
tests/               # pytest tests
```

## Key Conventions

- **Package manager:** uv (not pip)
- **Linting:** Ruff — target py312, line-length 100, rules: E, F, I, UP
- **Testing:** pytest with pytest-asyncio (asyncio_mode = "auto")
- **Build backend:** Hatchling (`src/butlers/` layout)
- **TDD approach:** Write failing test first, then implement
- **Module dependencies:** Resolved via topological sort
- **Modules only add tools** — they never touch core infrastructure
- **Manifesto-driven design:** Each butler has a `MANIFESTO.md` that defines its identity, purpose, and value proposition for users. New features, tools, and UX decisions for a butler should be deeply aligned with its manifesto. When in doubt about scope or framing, consult the manifesto.
- **Butler-specific API routes:** Dashboard API routes live in `roster/{butler}/api/router.py` and are auto-discovered by `src/butlers/api/router_discovery.py`. Each router.py must export a module-level `router` variable (APIRouter instance). No `__init__.py` needed. DB dependencies are auto-wired via `wire_db_dependencies()`. Co-locate Pydantic models in `models.py` alongside router.py.

## Issue Tracking (Beads)

This project uses `bd` (beads, v1.0.x) for issue tracking. The backend is the
**shared Dolt server** on `127.0.0.1:3307` (database `butlers`), discovered via
`.beads/metadata.json` (`dolt_mode: server`).

**Data flow:** `bd create/update/close` write directly to Dolt and auto-commit to
its history — there is **no `bd sync` step** (that subcommand does not exist in
this bd version). Dolt is the source of truth; the `.beads/` JSONL is a local,
gitignored mirror; never commit it. To refresh that local mirror, run
`bd export -o .beads/issues.export.jsonl`. **Never create `.beads/issues.jsonl`** —
on bd 1.0.4 server mode its presence triggers a full-file re-import on every write
that can wedge bd town-wide; the mirror lives at `.beads/issues.export.jsonl`
(see `export.path` in `.beads/config.yaml`).

See `AGENTS.md` for full beads workflow details.

## Implementation Plan

Development follows milestones defined in `PROJECT_PLAN.md`. Use the `superpowers:executing-plans` skill to implement tasks from that plan. A separate `MEMORY_PROJECT_PLAN.md` covers the tiered memory subsystem (Eden → Mid-Term → Long-Term, LRU-based promotion/eviction).

## API Conventions

### Cursor Pagination (BREAKING — Phase 2b, PR #1755)

`GET /api/ingestion/events` uses **keyset (cursor) pagination** — the `page` param is gone (use `limit` and `cursor` instead).

Response envelope:
```json
{"events": [...], "next_cursor": "<opaque>", "has_more": true}
```

- Pass `cursor=<next_cursor>` to fetch the next page.
- `has_more: false` means you are at the last page.
- No `total` field is returned.
- Keyset order: `received_at DESC, id DESC`.

### Degraded-Mode Response Envelope (Phase 4a, PRs #1762, #1798)

Endpoints that query Prometheus for aggregate metrics (`GET /api/ingestion/pipeline?window=24h`, `GET /api/ingestion/connectors/summaries`, `GET /api/ingestion/connectors/cross-summary`) always return HTTP 200. When Prometheus is unreachable, aggregate fields contain zeros and the envelope includes:

```json
{"...", "aggregates_available": false}
```

Never treat a missing or `false` `aggregates_available` field as an error — show a "metrics unavailable" indicator in the UI instead.
