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
---

# Adding Butlers to the Roster

Guide for creating new butlers that integrate seamlessly with the Butlers framework. Each butler is a self-contained MCP server daemon with its own database schema, tools, and personality, running within a shared PostgreSQL database.

## Prerequisites

Before creating a new butler, confirm:
- The butler has a clear, distinct domain that doesn't overlap with existing butlers: general (catch-all), health (medical/nutrition), relationship (personal CRM), messenger (delivery plane), switchboard (router)
- The butler's purpose can't be served by extending an existing butler
- The CLAUDE.md project instructions have been read and understood
- Review `docs/roles/base_butler.md` for the full base butler contract (lifecycle, tool surface, module system, persistence, routing, observability). Note: section 10 still references per-butler databases; the actual codebase uses shared DB with per-butler schemas — follow the butler.toml patterns in this skill, not the base spec's isolation section.

## Workflow Overview

Creating a butler involves these files (in recommended order):

1. **butler.toml** — Identity, database, schedule, runtime, and module configuration (required)
2. **MANIFESTO.md** — Public-facing identity document (required)
3. **CLAUDE.md** — System prompt for spawned runtime instances, including Interactive Response Mode and Memory Classification (required)
4. **AGENTS.md** — Runtime agent notes (required, initialize with header)
5. **tools/** — MCP tool implementations as a package or single file (required)
6. **migrations/** — Alembic database schema (if butler needs persistence)
7. **api/** — Dashboard API routes and Pydantic models (optional)
8. **skills/** — Skill definitions for runtime instances (optional, add later)
9. **tests/** — Integration tests (required)

## Step 1: Create the Directory

Create the butler directory under the roster root. The directory name IS the butler's identity — use lowercase, no hyphens or underscores.

```
roster/<butler-name>/
├── butler.toml
├── MANIFESTO.md
├── CLAUDE.md
├── AGENTS.md
├── tools/                      # Package for complex butlers
│   ├── __init__.py             # Re-exports all public symbols
│   ├── _helpers.py             # Private helpers (underscore prefix)
│   ├── domain_a.py             # e.g., measurements.py
│   └── domain_b.py             # e.g., medications.py
├── migrations/
│   ├── __init__.py             # Empty file (required)
│   └── 001_<butler-name>_tables.py
├── api/                        # Optional dashboard endpoints
│   ├── router.py               # FastAPI router (exports 'router')
│   └── models.py               # Pydantic response models
├── skills/
│   ├── <custom-skill>/
│   │   └── SKILL.md
│   ├── butler-memory -> ../../shared/skills/butler-memory
│   └── butler-notifications -> ../../shared/skills/butler-notifications
└── tests/
    └── test_tools.py
```

**Alternative: single-file tools** — For simple butlers with few tools, use `tools.py` instead of the `tools/` package. The framework auto-discovers either form.

**Naming rules:**
- Single word preferred (e.g., `finance`, `fitness`, `journal`)
- If multi-word is unavoidable, no separators (e.g., `mealplan` not `meal-plan`)
- Must be a valid Python identifier (used as Alembic branch label and module name)

## Step 2: butler.toml

The identity and configuration file. Consult `references/butler-toml.md` for the full schema and examples.

**Recommended config for a domain butler:**

```toml
[butler]
name = "<butler-name>"
port = <port-number>
description = "<one-line description>"

[butler.runtime]
model = "gpt-5.3-codex-spark"
max_concurrent_sessions = 3

[runtime]
type = "codex"

[butler.db]
name = "butlers"              # Shared database (all butlers use the same DB)
schema = "<butler-name>"      # Per-butler schema for isolation

[[butler.schedule]]           # Optional: cron-triggered tasks
name = "<task-name>"
cron = "<cron-expression>"
prompt = """<prompt text>"""

[[butler.schedule]]           # Optional: job-mode tasks (no prompt)
name = "<job-name>"
cron = "<cron-expression>"
dispatch_mode = "job"
job_name = "<python_function_name>"

[modules.calendar]            # Optional: if butler manages appointments
provider = "google"
calendar_id = "<shared butler calendar ID>"

[modules.calendar.conflicts]
policy = "suggest"

[modules.contacts]            # Optional: if butler works with contacts
provider = "google"
include_other_contacts = false

[modules.contacts.sync]
enabled = true
run_on_startup = true
interval_minutes = 15
full_sync_interval_days = 6

[modules.memory]              # Optional: if butler uses memory system
```

**Key decisions:**
- **Port**: Pick the next available port. Active butlers: switchboard=40100, general=40101, relationship=40102, health=40103, messenger=40104. Use 40105+ for new domain butlers (40199 is reserved for infrastructure).
- **Database**: Always `name = "butlers"` (shared DB) with `schema = "<butler-name>"` for per-butler isolation. This is a hard architectural constraint. The DB sets `search_path` to `<schema>, shared, public` so SQL queries resolve tables in butler schema first, then shared, then public.
- **Schedule**: Two dispatch modes:
  - `prompt` (default): Sends the `prompt` text to a spawned runtime instance.
  - `job`: Calls a named Python function directly via `job_name`. No `prompt` field needed.
- **Runtime**: `[butler.runtime]` sets model (default: `claude-haiku-4-5-20251001`) and concurrency (default: 1 serial). `[runtime]` sets the runtime adapter type (e.g., `"codex"`). All current butlers use `model = "gpt-5.3-codex-spark"` and `type = "codex"`.
- **Modules**: Most user-facing domain butlers enable `calendar`, `contacts`, and `memory`. Infrastructure butlers (switchboard, messenger) have different module profiles. Modules provide their own MCP tools, migrations, and lifecycle hooks.
- **Advanced sections** (optional): `[butler.env]` declares required/optional env vars. `[butler.shutdown]` sets graceful shutdown timeout. `[butler.switchboard]` configures routing integration (URL, advertise, liveness TTL, route contract versions). See `docs/roles/base_butler.md` for full spec.

## Step 3: MANIFESTO.md

The manifesto defines the butler's identity, purpose, and value proposition. It's a public-facing document that guides all feature and UX decisions. Consult `references/manifesto-guide.md` for the pattern.

**Structure:**
1. **Title**: `# The <Name> Butler` (or a metaphorical name)
2. **What We Believe**: The core philosophy — why this domain matters
3. **Our Promise / What It Does**: 2-4 value propositions with bold headers
4. **What You Can Do / What You Get**: Concrete capabilities as bullet points
5. **Why It Matters**: Emotional resonance — how this improves the user's life
6. **Closing**: A signature tagline

**Writing style:**
- Second person ("you"), warm but not saccharine
- Focus on user outcomes, not technical capabilities
- Each value proposition gets a bold one-word header + explanation
- Acknowledge real-world friction the butler solves
- 300-500 words. No mention of PostgreSQL, JSONB, MCP, or any technical terms.

## Step 4: CLAUDE.md

The system prompt for ephemeral LLM CLI instances spawned by this butler. Keep it concise — runtime instances get this as context for every interaction.

**Structure:**

```markdown
# <Name> Butler

You are the <Name> butler — <one-sentence role description>.

## Your Tools
- **tool_group/action1/action2**: Brief description
- **other_tool**: Brief description
- **calendar_list_events/get_event/create_event/update_event**: Calendar management

## Guidelines
- Key behavioral rule 1
- Key behavioral rule 2
- Domain-specific conventions

## Calendar Usage
- Use calendar tools for domain-relevant scheduling
- Write Butler-managed events to the shared butler calendar, not the user's primary calendar
- Default conflict behavior is `suggest`

## Interactive Response Mode
(See below for full pattern)

### Memory Classification
(See below for full pattern)
```

**Rules:**
- Under 50 lines for the core section. Interactive Response Mode and Memory Classification add more.
- List every tool from tools.py with a brief description.
- Include behavioral guidelines (how to handle ambiguity, proactive behaviors, data conventions).
- Use imperative tone, not conversational.

### Interactive Response Mode (for user-facing butlers)

All butlers that receive routed user messages (via Telegram, email, etc.) need an Interactive Response Mode section. This covers:

1. **Detection**: Check for REQUEST CONTEXT JSON block with `source_channel`
2. **Response Mode Selection**: Five modes:
   - **React**: Emoji-only acknowledgment (simple actions)
   - **Affirm**: Brief confirmation message
   - **Follow-up**: Proactive question or suggestion
   - **Answer**: Substantive response to a question
   - **React + Reply**: Combined emoji + message
3. **Complete Examples**: 5-7 full interaction flows showing the mode in action with tool calls

### Memory Classification (for butlers with memory module)

Define a domain-specific taxonomy for `memory_store_fact()`:

- **Subject**: What entity types are valid (e.g., user, condition names, medication names)
- **Predicates**: Domain-specific predicates (e.g., `medication`, `dosage`, `symptom_pattern`, `goal`, `preference`)
- **Permanence levels**: `stable` (long-term/chronic), `standard` (default), `volatile` (temporary/acute)
- **Tags**: Domain-specific tags for cross-cutting organization
- **Example facts**: 3-5 complete `memory_store_fact()` calls with realistic data

See health butler's CLAUDE.md for a comprehensive reference implementation.

## Step 5: AGENTS.md

Initialize the agent notes file. This is used by runtime instances to persist notes across sessions.

```markdown
# Notes to self

```

Keep it minimal at creation. Runtime instances will populate it over time.

## Step 6: tools/ (Package or Single File)

The MCP tool implementations. All tools follow a consistent pattern. Consult `references/tools-patterns.md` for the full pattern reference.

### Package structure (recommended for 5+ tools)

```
tools/
├── __init__.py          # Re-exports all public symbols
├── _helpers.py          # Shared helpers (underscore prefix)
├── domain_a.py          # Domain-grouped tools
└── domain_b.py          # Another group
```

**`__init__.py` pattern:**

```python
"""<Butler-name> butler tools — <brief description>.

Re-exports all public symbols so that ``from butlers.tools.<name> import X``
continues to work as before.
"""

from butlers.tools.<butler>._helpers import _row_to_dict
from butlers.tools.<butler>.domain_a import (
    VALID_TYPES,
    thing_create,
    thing_list,
)
from butlers.tools.<butler>.domain_b import (
    other_create,
    other_list,
)

__all__ = [
    "VALID_TYPES",
    "_row_to_dict",
    "thing_create",
    "thing_list",
    "other_create",
    "other_list",
]
```

### Single file (for simple butlers)

Use a standalone `tools.py` file with the same conventions.

### Tool function conventions

```python
"""<Butler-name> butler tools — <brief description>."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)
```

- **First parameter**: Always `pool: asyncpg.Pool`
- **Return types**: `uuid.UUID` for creates, `dict[str, Any] | None` for gets, `list[dict]` for lists, `None` for updates/deletes
- **Error handling**: Raise `ValueError` for "not found" on mutations. Return `None` on gets. Let `asyncpg` exceptions propagate for constraints.
- **JSONB handling**: Use `json.dumps()` for writes, `json.loads()` on read. Cast with `::jsonb` in SQL.
- **Helper functions**: Prefix with underscore (`_row_to_dict`, `_log_activity`)
- **No framework imports**: Tools are pure async functions. No FastMCP, no decorators — the framework wraps them.
- **Type hints**: Use `from __future__ import annotations` and modern union syntax (`str | None`)

## Step 7: migrations/

Alembic migrations for the butler's database schema. Only needed if the butler persists data.

**File structure:**

```
migrations/
├── __init__.py                    # Empty file (required)
└── 001_<butler-name>_tables.py    # First migration
```

**Migration template:**

```python
"""create_<butler_name>_tables

Revision ID: <butler>_001
Revises:
Create Date: <date>

"""

from __future__ import annotations

from alembic import op

revision = "<butler>_001"
down_revision = None
branch_labels = ("<butler-name>",)
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS <table_name> (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            -- domain columns here
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Add indexes
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_<table>_<col>
            ON <table_name> (<col>)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS <table_name>")
```

**Critical rules:**
- `branch_labels` MUST be a tuple with the butler name: `("<butler-name>",)`. This enables per-butler migration chains.
- Revision naming: Use `<butler>_001` format (e.g., `health_001`, `rel_001`).
- First migration: `down_revision = None`. Subsequent: `down_revision = "<butler>_001"`.
- Multiple revisions at the same level are allowed when independent (e.g., `rel_002a`, `rel_002c`).
- Use `op.execute()` with raw SQL, not SQLAlchemy ORM operations.
- Always include `IF NOT EXISTS` / `IF EXISTS` guards.
- Add GIN indexes on JSONB columns for containment queries (`@>`).
- Use UUID primary keys with `gen_random_uuid()`.
- Use `TIMESTAMPTZ` (not `TIMESTAMP`) for all datetime columns.
- Note: module migrations (calendar, contacts, memory) are handled by the module system in `src/butlers/modules/<module>/migrations/` — don't duplicate them.

## Step 8: api/ (Dashboard Routes)

Optional dashboard API endpoints for the butler's data. Auto-discovered by `src/butlers/api/router_discovery.py`.

### router.py

```python
"""<Butler-name> butler endpoints."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import PaginatedResponse, PaginationMeta

# Import models (use importlib pattern from health butler for co-located models)
import importlib.util
import sys
from pathlib import Path

_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("<butler>_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["<butler>_api_models"] = _models
    _spec.loader.exec_module(_models)
    MyModel = _models.MyModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/<butler-name>", tags=["<butler-name>"])

BUTLER_DB = "<butler-name>"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the butler's connection pool."""
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="<Butler-name> butler database is not available",
        )


@router.get("/things", response_model=PaginatedResponse[MyModel])
async def list_things(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[MyModel]:
    """List things with pagination."""
    pool = _pool(db)
    total = await pool.fetchval("SELECT count(*) FROM things") or 0
    rows = await pool.fetch(
        "SELECT * FROM things ORDER BY created_at DESC OFFSET $1 LIMIT $2",
        offset, limit,
    )
    data = [MyModel(id=str(r["id"]), ...) for r in rows]
    return PaginatedResponse[MyModel](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )
```

**Key patterns:**
- Must export module-level `router` variable (APIRouter instance)
- No `__init__.py` needed in api/ directory
- `_get_db_manager()` is a dependency stub, overridden at app startup by `wire_db_dependencies()`
- Use `PaginatedResponse[T]` and `PaginationMeta` from `butlers.api.models`
- Prefix routes with `/api/<butler-name>`
- Use parameterized queries (`$1`, `$2`) — never string interpolation

### models.py

```python
"""Pydantic models for <butler-name> butler API."""

from __future__ import annotations

from pydantic import BaseModel


class MyModel(BaseModel):
    """Description."""

    id: str
    name: str
    data: dict | None = None  # JSONB fields as dict
    created_at: str
    updated_at: str
```

## Step 9: skills/

Skills provide detailed workflow guidance to runtime instances. Each skill is a subdirectory with a `SKILL.md` file.

### Shared skills

Most butlers should symlink to shared skills:

```bash
cd roster/<butler-name>/skills/
ln -s ../../shared/skills/butler-memory butler-memory
ln -s ../../shared/skills/butler-notifications butler-notifications
```

### Custom skills

Create butler-specific skills for domain workflows (e.g., health check-in flow, relationship outreach cycle). Each skill should include:
- Pre-requisites (what data to gather first)
- Step-by-step flow
- Example conversational interactions
- Error handling patterns

## Step 10: tests/

Integration tests using pytest, asyncio, and testcontainers. Consult `references/test-patterns.md` for the full pattern.

**File: `roster/<butler-name>/tests/test_tools.py`**

```python
"""Tests for butlers.tools.<butler_name> — <brief description>."""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    from testcontainers.postgres import PostgresContainer
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture
async def pool(postgres_container):
    from butlers.db import Database
    db = Database(
        db_name=_unique_db_name(),
        schema="<butler-name>",  # Matches butler.toml [butler.db].schema
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()
    p = await db.connect()
    # Create schema and tables (mirror Alembic migrations)
    await p.execute("CREATE SCHEMA IF NOT EXISTS <butler_name>")
    await p.execute("SET search_path TO <butler_name>, public")
    await p.execute("""...""")
    yield p
    await db.close()
```

**Test conventions:**
- Import tools inside test functions: `from butlers.tools.<butler_name> import <func>`
- One test per behavior, organized under section comments (`# --- tool_name ---`)
- Test happy path, not-found, constraint violations, and edge cases
- Use parametrize for testing multiple valid inputs
- Fixtures create isolated databases — tests don't share state between test functions

## Step 11: Register with Switchboard

The Switchboard butler's routing rules are **hardcoded in its CLAUDE.md** (`roster/switchboard/CLAUDE.md`). This is the LLM's system prompt — it classifies messages by reading these rules. You must update it manually:

1. **Available Butlers section** (line ~10): Add a bullet with the new butler name and description:
   ```
   - **<butler-name>**: <one-line description of what it handles>
   ```

2. **Classification Rules section** (line ~15): Add a classification rule:
   ```
   - If the message is about <domain keywords> → <butler-name>
   ```

3. **Update the message-triage skill** if it exists under `roster/switchboard/skills/`

Note: The framework auto-discovers butlers via `discover_butlers()` scanning butler.toml files for registry and connectivity purposes. But routing classification is LLM-driven from the Switchboard's CLAUDE.md — without the manual update, messages in your butler's domain will fall through to `general`.

## Auto-Discovery

The framework automatically discovers new butlers — no registration code needed:

- **Tools**: `register_all_butler_tools()` in `src/butlers/tools/_loader.py` scans `roster/*/tools.py` or `roster/*/tools/__init__.py`
- **Migrations**: `_discover_butler_chains()` in `src/butlers/migrations.py` scans `roster/*/migrations/`
- **API Routers**: `discover_butler_routers()` in `src/butlers/api/router_discovery.py` scans `roster/*/api/router.py`
- **Registry**: `discover_butlers()` scans butler.toml files to populate the butler registry

Simply placing the correct files in the right directory structure is sufficient for integration. No hardcoded butler names anywhere.

## Common Mistakes

1. **Wrong database config**: Using `name = "butler_<name>"` (old pattern). Correct: `name = "butlers"` + `schema = "<name>"`.
2. **Overlapping domain**: Creating a butler whose tools duplicate what another butler already does. Check existing butlers first.
3. **Missing branch_labels**: Forgetting `branch_labels = ("<name>",)` in the first migration causes Alembic chain resolution failures.
4. **Port conflicts**: Using a port already assigned to another butler.
5. **Non-Python-identifier name**: Butler names with hyphens or starting with digits break module imports.
6. **Missing `__init__.py`**: The migrations directory needs an empty `__init__.py`. The tools/ package directory also needs one with re-exports.
7. **Framework imports in tools**: Tools must be pure async functions taking `pool: asyncpg.Pool`. No FastMCP decorators.
8. **Forgetting Switchboard CLAUDE.md update**: New butlers won't receive routed messages unless the Switchboard's system prompt knows about them.
9. **TIMESTAMP instead of TIMESTAMPTZ**: Always use timezone-aware timestamps.
10. **Missing Interactive Response Mode**: User-facing butlers that receive Telegram/email messages need the full IRM section in CLAUDE.md.
11. **Missing memory taxonomy**: Butlers with `[modules.memory]` need domain-specific Memory Classification in CLAUDE.md.
12. **Forgetting shared skill symlinks**: Most butlers should symlink `butler-memory` and `butler-notifications` from `roster/shared/skills/`.
13. **Missing AGENTS.md**: Every butler needs this file, even if initially just a header.
14. **API router missing `router` export**: Dashboard routes must export a module-level `router` variable (APIRouter instance).
