---
name: adding-butlers-to-roster
description: >
  This skill should be used when creating a new butler in the Butlers project roster.
  It covers the complete workflow: directory scaffolding, butler.toml configuration,
  MANIFESTO.md identity document, CLAUDE.md system prompt (including Interactive Response
  Mode and Memory Classification), tools implementation (single file or package),
  Alembic migrations, dashboard API routes, shared and custom skills, and integration
  tests. Follow this skill to ensure new butlers conform to established patterns and
  integrate correctly with the framework's auto-discovery mechanisms.
metadata:
  owner: tze
  authors: [Tzeusy, Claude]
  status: active
  last_reviewed: "2026-09-05"
---

# Adding Butlers to the Roster

Guide for creating new butlers that integrate seamlessly with the Butlers framework.
Each butler is a self-contained MCP server daemon with its own database schema, tools,
and personality, running within a shared PostgreSQL database (one DB `butlers`, one
schema per butler).

**Triggers:** "add a new butler", "scaffold a butler", "create the finance/journal/travel
butler", "register a butler with switchboard", "what files does a butler need".

## Prerequisites

Before creating a butler, confirm:
- It has a clear, distinct domain not overlapping existing butlers: general (catch-all),
  health (medical/nutrition), relationship (personal CRM), finance, education, travel,
  home (smart home), lifestyle, messenger (delivery plane), switchboard (router).
- Its purpose can't be served by extending an existing butler.
- The project `CLAUDE.md` has been read.
- For the full base butler contract (lifecycle, tool surface, module system,
  persistence, routing, observability), read `docs/architecture/butler-daemon.md` and
  `docs/concepts/butler-lifecycle.md`. The DB is shared with per-butler schemas — follow
  the butler.toml patterns here.

## Reference map

Load the reference for the step you are on — a typical butler touches two or three:

| Reference | When to load |
| --- | --- |
| [references/butler-toml.md](references/butler-toml.md) | Step 2 — full butler.toml schema, port allocation, module profiles, cron, examples |
| [references/manifesto-guide.md](references/manifesto-guide.md) | Step 3 — MANIFESTO.md structure, tone, opening/closing examples, anti-patterns |
| [references/claude-md-guide.md](references/claude-md-guide.md) | Step 4 — runtime CLAUDE.md structure, Interactive Response Mode, Memory Classification |
| [references/tools-patterns.md](references/tools-patterns.md) | Step 6 — tool function signatures, SQL/JSONB patterns, error handling, `_helpers.py` |
| [references/module-package.md](references/module-package.md) | Step 6b — `modules/` package that wires tools as MCP tools (required if tools exist) |
| [references/migrations-guide.md](references/migrations-guide.md) | Step 7 — Alembic migration template, branch_labels, revision naming rules |
| [references/dashboard-api.md](references/dashboard-api.md) | Step 8 — dashboard `api/router.py` + `models.py` patterns (optional) |
| [references/test-patterns.md](references/test-patterns.md) | Step 10 — integration test template, fixtures, what to test, assertions |
| [references/integration.md](references/integration.md) | Steps 9 & 11 — runtime skills, Switchboard registration, auto-discovery, Common Mistakes checklist |

## Directory scaffold

The directory name IS the butler's identity — lowercase, no hyphens/underscores.

```
roster/<butler-name>/
├── butler.toml                 # Step 2 — identity, db, schedule, runtime, modules (required)
├── MANIFESTO.md                # Step 3 — public identity document (required)
├── CLAUDE.md                   # Step 4 — runtime system prompt (required)
├── AGENTS.md                   # Step 5 — runtime agent notes (required, header only at first)
├── modules/                    # Step 6b — wires tools/ to MCP server (required if tools/ exists)
│   ├── __init__.py             # Module class (config, lifecycle, _get_pool)
│   └── tools.py                # @mcp.tool() closure registrations
├── tools/                      # Step 6 — package for complex butlers (or single tools.py)
│   ├── __init__.py             # Re-exports all public symbols
│   ├── _helpers.py             # Private helpers (underscore prefix)
│   └── domain_a.py             # e.g., measurements.py, medications.py
├── migrations/                 # Step 7 — if butler needs persistence
│   ├── __init__.py             # Empty file (required)
│   └── 001_<butler-name>_tables.py
├── api/                        # Step 8 — optional dashboard endpoints
│   ├── router.py               # FastAPI router (exports 'router')
│   └── models.py               # Pydantic response models
├── .agents/skills/             # Step 9 — runtime skills (shared symlinks + custom)
├── .claude -> .agents
└── tests/                      # Step 10 — integration tests (required)
    └── test_tools.py
```

**Naming rules:** single word preferred (`finance`, `journal`); if multi-word, no
separators (`mealplan` not `meal-plan`); must be a valid Python identifier (used as
Alembic branch label and module name).

## Steps

Build in this order.

1. **Create the directory** under `roster/` (see scaffold above).
2. **butler.toml** — identity, db (`name = "butlers"`, `schema = "<name>"`), runtime
   (`model = "gpt-5.4-mini"`, `type = "codex"`), schedule, modules. Pick the next port
   (active: switchboard 41100, general 41101, relationship 41102, health 41103,
   messenger 41104; use 41105+, 41199 reserved). Full schema, port table, and module
   profiles in [references/butler-toml.md](references/butler-toml.md).
3. **MANIFESTO.md** — the butler's identity and value proposition; guides every feature
   and UX decision. 300-500 words, second person, no technical terms. Pattern in
   [references/manifesto-guide.md](references/manifesto-guide.md).
4. **CLAUDE.md** — runtime system prompt (tools list, guidelines, calendar usage). Add
   Interactive Response Mode for user-facing butlers and Memory Classification for
   butlers with `[modules.memory]`. See [references/claude-md-guide.md](references/claude-md-guide.md);
   `roster/health/CLAUDE.md` is a comprehensive reference implementation.
5. **AGENTS.md** — initialize minimal; runtime instances populate it over time:
   ```markdown
   # Notes to self

   ```
6. **tools/** — MCP tool implementations as pure async functions. First param always
   `pool: asyncpg.Pool`, no FastMCP imports. Single `tools.py` for <5 tools, else a
   `tools/` package. Signatures, SQL/JSONB, and error patterns in
   [references/tools-patterns.md](references/tools-patterns.md).
6b. **modules/** — **required if the butler has domain tools.** Without it the runtime
   never sees the tools ("tool not found"). Wraps tools with `@mcp.tool()` closures and
   is auto-discovered. Templates and conventions in [references/module-package.md](references/module-package.md).
7. **migrations/** — Alembic schema, only if the butler persists data.
   `branch_labels = ("<name>",)`, `TIMESTAMPTZ`, UUID PKs, `IF NOT EXISTS` guards.
   Template and rules in [references/migrations-guide.md](references/migrations-guide.md).
8. **api/** — optional dashboard routes; auto-discovered, must export module-level
   `router`. Patterns in [references/dashboard-api.md](references/dashboard-api.md).
9. **.agents/skills/** — symlink shared skills (`butler-memory`, `butler-notifications`)
   and add custom domain skills. Commands in [references/integration.md](references/integration.md).
10. **tests/** — integration tests (pytest + asyncio + testcontainers). Import tools
    inside test functions; fixtures create isolated DBs. Template in
    [references/test-patterns.md](references/test-patterns.md).
11. **Register with Switchboard** — routing is LLM-driven from `roster/switchboard/CLAUDE.md`;
    add an Available Butlers bullet and a Classification Rule manually, or messages fall
    through to `general`. Steps in [references/integration.md](references/integration.md).

## Guardrails

- **Database is a hard constraint:** always `name = "butlers"` + `schema = "<name>"`.
  Never the old `name = "butler_<name>"`. `search_path` resolves `<schema>, shared, public`.
- **Domain tools need a `modules/` package** (Step 6b) or they are invisible to the runtime.
- **Switchboard CLAUDE.md must be updated** (Step 11) or the butler receives no routed messages.
- **Migrations:** `branch_labels = ("<name>",)`, `TIMESTAMPTZ` not `TIMESTAMP`, empty
  `migrations/__init__.py`.
- Full Common Mistakes checklist (15 items) and auto-discovery details in
  [references/integration.md](references/integration.md).
