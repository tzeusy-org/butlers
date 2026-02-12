# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Butlers is an AI agent framework where each "butler" is a long-running MCP server daemon with core infrastructure (state store, scheduler, CC spawner, session log) and opt-in modules (email, telegram, calendar, etc.). When triggered, a butler spawns an ephemeral Claude Code instance wired exclusively to itself via a locked-down MCP config.

**Tech stack:** Python 3.12+, FastMCP, Claude Code SDK, PostgreSQL (JSONB-heavy, one DB per butler), Docker, asyncio

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
uv run pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py -q --maxfail=1 --tb=short >"$PYTEST_LOG" 2>&1 || tail -n 120 "$PYTEST_LOG"
```

## Architecture

### Two-Layer Butler Design

Every butler has **core components** (always present) and **modules** (opt-in per butler):

- **Core:** State store (KV JSONB), task scheduler (cron-driven), CC spawner (ephemeral Claude Code via SDK), session log, tick handler, status
- **Modules:** Pluggable units adding domain-specific MCP tools (email, telegram, calendar, etc.). Implement the `Module` abstract base class with `register_tools()`, `migrations()`, `on_startup()`, `on_shutdown()`.

### Trigger Flow

1. Trigger arrives (external MCP call, scheduler due task, or heartbeat tick)
2. CC Spawner generates ephemeral MCP config → spawns Claude Code via SDK
3. Claude Code calls butler's MCP tools, runs skill scripts, returns
4. Butler logs the session

### Special Butlers

- **Switchboard Butler:** Routes external MCP requests to the correct butler
- **Heartbeat Butler:** Calls `tick()` on every registered butler every 10 min

### Database Isolation

Each butler owns a dedicated PostgreSQL database. Inter-butler communication only via MCP tools through the Switchboard. This is a hard architectural constraint.

### Butler Config Directory (git-based, `roster/`)

```
roster/butler-name/
├── MANIFESTO.md    # Public-facing identity, purpose, and value proposition
├── CLAUDE.md       # Butler personality/instructions (system prompt)
├── AGENTS.md       # Runtime agent notes
├── skills/         # Skills available to CC instances (SKILL.md + optional scripts)
└── butler.toml     # Identity, schedule, modules config
```

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

## Implementation Plan

Development follows milestones defined in `PROJECT_PLAN.md`. Use the `superpowers:executing-plans` skill to implement tasks from that plan. A separate `MEMORY_PROJECT_PLAN.md` covers the tiered memory subsystem (Eden → Mid-Term → Long-Term, LRU-based promotion/eviction).
