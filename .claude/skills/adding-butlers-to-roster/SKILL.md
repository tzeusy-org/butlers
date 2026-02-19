---
name: adding-butlers-to-roster
description: >
  This skill should be used when creating a new butler in the Butlers project roster.
  It covers the complete workflow: directory scaffolding, butler.toml configuration,
  MANIFESTO.md identity document, CLAUDE.md system prompt, tools.py implementation,
  Alembic migrations, skills, and integration tests. Follow this skill to ensure
  new butlers conform to established patterns and integrate correctly with the
  framework's auto-discovery mechanisms.
---

# Adding Butlers to the Roster

Guide for creating new butlers that integrate seamlessly with the Butlers framework. Each butler is a self-contained MCP server daemon with its own database, tools, and personality.

## Prerequisites

Before creating a new butler, confirm:
- The butler has a clear, distinct domain that doesn't overlap with existing butlers (general, health, heartbeat, relationship, switchboard)
- The butler's purpose can't be served by extending an existing butler
- The CLAUDE.md project instructions have been read and understood

## Workflow Overview

Creating a butler involves these files (in recommended order):

1. **butler.toml** — Identity and configuration (required)
2. **MANIFESTO.md** — Public-facing identity document (required)
3. **CLAUDE.md** — System prompt for spawned runtime instances (required)
4. **tools.py** — MCP tool implementations (required)
5. **migrations/** — Alembic database schema (if butler needs persistence)
6. **skills/** — Skill definitions for runtime instances (optional, add later)
7. **tests/** — Integration tests (required)

## Step 1: Create the Directory

Create the butler directory under the roster root. The directory name IS the butler's identity — use lowercase, no hyphens or underscores.

```
roster/<butler-name>/
├── butler.toml
├── MANIFESTO.md
├── CLAUDE.md
├── tools.py
├── migrations/
│   ├── __init__.py
│   └── 001_<butler-name>_tables.py
├── skills/
│   └── <skill-name>/
│       └── SKILL.md
└── tests/
    └── test_tools.py
```

**Naming rules:**
- Single word preferred (e.g., `finance`, `fitness`, `journal`)
- If multi-word is unavoidable, no separators (e.g., `mealplan` not `meal-plan`)
- Must be a valid Python identifier (used as Alembic branch label and module name)

## Step 2: butler.toml

The identity and configuration file. Consult `references/butler-toml.md` for the full schema and examples.

**Minimal required config:**

```toml
[butler]
name = "<butler-name>"
port = <port-number>
description = "<one-line description>"

[butler.db]
name = "butler_<butler-name>"
```

**Key decisions:**
- **Port**: Pick the next available port. Existing: switchboard=40100, general=40101, relationship=40102, health=40103, heartbeat=40199. Use 40104+ for new butlers.
- **Database**: Always `butler_<name>` — one database per butler (hard architectural constraint).
- **Schedule**: Only add `[[butler.schedule]]` entries if the butler has periodic tasks. Each entry needs `name`, `cron`, and `prompt`.
- **Modules**: Only add `[modules.<name>]` if using opt-in modules (telegram, email, etc.). Most butlers don't need modules.

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

## Step 4: CLAUDE.md

The system prompt for ephemeral LLM CLI instances spawned by this butler. Keep it concise — runtime instances get this as context for every interaction.

**Structure:**

```markdown
# <Name> Butler

You are the <Name> butler — <one-sentence role description>.

## Your Tools
- **tool_name**: Brief description of what it does
- **tool_group/list/create**: Group related tools with slashes

## Guidelines
- Key behavioral rule 1
- Key behavioral rule 2
- Domain-specific convention
```

**Rules:**
- Under 50 lines. runtime instances also get the skill files for detailed knowledge.
- List every tool from tools.py with a brief description.
- Include behavioral guidelines (how to handle ambiguity, proactive behaviors, data conventions).
- Use imperative tone, not conversational.

## Step 5: tools.py

The MCP tool implementations. All tools follow a consistent pattern. Consult `references/tools-patterns.md` for the full pattern reference.

**Key conventions:**

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
- **Return types**: `uuid.UUID` for create operations, `dict[str, Any]` or `list[dict]` for reads, `None` for deletes/updates
- **Error handling**: Raise `ValueError` for "not found" cases. Let `asyncpg` exceptions propagate for constraint violations.
- **JSONB handling**: Use `json.dumps()` for writes, parse strings from reads with `json.loads()`. Cast with `::jsonb` in SQL.
- **Helper functions**: Prefix with underscore (`_deep_merge`, `_row_to_dict`, `_log_activity`)
- **No framework imports**: Tools are pure functions that take a connection pool. No FastMCP, no decorators.
- **Type hints**: Use `from __future__ import annotations` and modern union syntax (`str | None`)

## Step 6: migrations/

Alembic migrations for the butler's database schema. Only needed if the butler persists data (skip for infrastructure butlers like heartbeat).

**File structure:**

```
migrations/
├── __init__.py          # Empty file (required)
└── 001_<butler-name>_tables.py
```

**Migration template:**

```python
"""create_<butler_name>_tables

Revision ID: 001
Revises:
Create Date: <date>

"""

from __future__ import annotations

from alembic import op

revision = "001"
down_revision = None
branch_labels = ("<butler-name>",)
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS <table_name> (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            -- domain columns here
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS <table_name>")
```

**Critical rules:**
- `branch_labels` MUST be a tuple with the butler name: `("<butler-name>",)`. This enables per-butler migration chains.
- First migration: `revision = "001"`, `down_revision = None`
- Subsequent migrations: `revision = "002"`, `down_revision = "001"`. Multiple 002-level migrations are allowed when they're independent (parallel schema evolution).
- Use `op.execute()` with raw SQL, not SQLAlchemy ORM operations.
- Always include `IF NOT EXISTS` / `IF EXISTS` guards.
- Add GIN indexes on JSONB columns, compound indexes on common query patterns.
- Use UUID primary keys with `gen_random_uuid()`.
- Use `TIMESTAMPTZ` (not `TIMESTAMP`) for all datetime columns.

## Step 7: tests/

Integration tests using pytest, asyncio, and testcontainers. Consult `references/test-patterns.md` for the full pattern.

**File: `tests/test_tools.py`**

```python
"""Tests for butlers.tools.<butler-name> — <brief description>."""

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
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture
async def pool(postgres_container):
    """Provision a fresh database with <butler-name> tables."""
    from butlers.db import Database

    db = Database(
        db_name=_unique_db_name(),
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()
    p = await db.connect()

    # Create tables (mirrors Alembic migrations)
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

## Step 8: Register with Switchboard

After creating the butler, update the Switchboard butler's CLAUDE.md to include the new butler in its routing rules:

1. Add the butler to the "Available Butlers" list
2. Add classification rules for the new domain
3. Update the message-triage skill if it exists

## Auto-Discovery

The framework automatically discovers new butlers — no registration code needed:

- **Tools**: `register_all_butler_tools()` in `src/butlers/tools/_loader.py` scans `butlers/*/tools.py`
- **Migrations**: `_discover_butler_chains()` in `src/butlers/migrations.py` scans `butlers/*/migrations/`
- **Switchboard**: `discover_butlers()` scans butler.toml files to populate the butler registry

Simply placing the correct files in the right directory structure is sufficient for integration.

## Common Mistakes

1. **Overlapping domain**: Creating a butler whose tools duplicate what another butler already does. Check existing butlers first.
2. **Missing branch_labels**: Forgetting `branch_labels = ("<name>",)` in the first migration causes Alembic chain resolution failures.
3. **Port conflicts**: Using a port already assigned to another butler.
4. **Non-Python-identifier name**: Butler names with hyphens or starting with digits break module imports.
5. **Missing `__init__.py`**: The migrations directory needs an empty `__init__.py`.
6. **Framework imports in tools.py**: Tools must be pure async functions taking `pool: asyncpg.Pool`. No FastMCP decorators — the framework wraps them.
7. **Forgetting Switchboard update**: New butlers won't receive routed messages unless the Switchboard knows about them.
8. **TIMESTAMP instead of TIMESTAMPTZ**: Always use timezone-aware timestamps.
