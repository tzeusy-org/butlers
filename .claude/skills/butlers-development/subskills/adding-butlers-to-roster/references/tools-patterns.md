# tools.py / tools/ Patterns Reference

## Two Forms: Single File or Package

The framework auto-discovers either form:

### Single file: `roster/<butler>/tools.py`

Use for simple butlers with fewer than 5 tool functions.

### Package: `roster/<butler>/tools/__init__.py`

Use for complex butlers (recommended for 5+ tools). Organize by domain:

```
tools/
├── __init__.py          # Re-exports all public symbols
├── _helpers.py          # Shared helpers (underscore prefix, not public tools)
├── domain_a.py          # Domain-grouped tools (e.g., measurements.py)
└── domain_b.py          # Another group (e.g., medications.py)
```

**`__init__.py` pattern (from health butler):**

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
    thing_get,
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
    "thing_get",
    "other_create",
    "other_list",
]
```

## Module File Structure

Each domain module follows this template:

```python
"""<Butler-name> butler tools — <brief description>."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Optional: validation constants
VALID_STATUSES = ["active", "resolved", "managed"]
VALID_TYPES = ["weight", "blood_pressure", "glucose"]


# --- Public tool functions ---

async def thing_create(pool: asyncpg.Pool, name: str, ...) -> uuid.UUID:
    """Create a new thing."""
    ...

async def thing_get(pool: asyncpg.Pool, thing_id: uuid.UUID) -> dict[str, Any] | None:
    """Get a thing by ID."""
    ...

async def thing_list(pool: asyncpg.Pool, ...) -> list[dict[str, Any]]:
    """List things with optional filters."""
    ...

async def thing_update(pool: asyncpg.Pool, thing_id: uuid.UUID, ...) -> None:
    """Update a thing."""
    ...

async def thing_delete(pool: asyncpg.Pool, thing_id: uuid.UUID) -> None:
    """Delete a thing."""
    ...

async def thing_search(pool: asyncpg.Pool, query: dict | None = None) -> list[dict[str, Any]]:
    """Search things by criteria."""
    ...


# --- Private helpers ---

def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a dict, parsing JSONB strings."""
    d = dict(row)
    for key in ("data", "details", "config", "tags", "metadata"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    return d
```

## `_helpers.py` Pattern

Shared helpers go in a separate file with underscore prefix:

```python
"""Shared helpers for <butler-name> butler tools."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a dict, parsing JSONB strings."""
    d = dict(row)
    for key in ("data", "details", "config", "tags", "metadata", "nutrition",
                "value", "schedule"):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    return d
```

## Function Signature Conventions

### Create functions
```python
async def entity_create(
    pool: asyncpg.Pool,
    name: str,
    description: str | None = None,
    data: dict[str, Any] | None = None,
) -> uuid.UUID:
```
- Return the new UUID
- Required fields as positional params, optional as keyword params
- JSONB fields default to `None` (handle in SQL as `'{}'::jsonb` or `'[]'::jsonb`)

### Get functions
```python
async def entity_get(pool: asyncpg.Pool, entity_id: uuid.UUID) -> dict[str, Any] | None:
```
- Return `None` for not-found (don't raise)
- Parse JSONB strings in the result

### List functions
```python
async def entity_list(
    pool: asyncpg.Pool,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
```
- Optional filter params with defaults
- Return empty list when no matches
- Include ORDER BY (usually `created_at DESC` or `name ASC`)

### Update functions
```python
async def entity_update(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    **kwargs,
) -> None:
```
- Raise `ValueError` for not-found
- For JSONB fields, decide between replace vs. deep merge
- Always update `updated_at = now()`

### Delete functions
```python
async def entity_delete(pool: asyncpg.Pool, entity_id: uuid.UUID) -> None:
```
- Raise `ValueError` for not-found
- Check `result == "DELETE 0"` to detect missing rows

### Search functions
```python
async def entity_search(
    pool: asyncpg.Pool,
    collection_name: str | None = None,
    query: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
```
- Build WHERE clause dynamically with parameterized queries
- Use `$1`, `$2` etc. (asyncpg uses numbered params, not `%s`)
- Use `@>` for JSONB containment queries

### History/log functions
```python
async def entity_history(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID | None = None,
    days: int = 30,
    limit: int = 100,
) -> list[dict[str, Any]]:
```
- Support time-range filtering with `days` parameter
- Optional entity-scoped filtering
- ORDER BY time column DESC

## SQL Patterns

### Parameterized queries (asyncpg style)
```python
# Single value
row = await pool.fetchrow("SELECT * FROM things WHERE id = $1", thing_id)

# Multiple values
row = await pool.fetchrow(
    "INSERT INTO things (name, data) VALUES ($1, $2::jsonb) RETURNING id",
    name,
    json.dumps(data),
)

# Dynamic WHERE clause
conditions: list[str] = []
params: list[Any] = []
idx = 1

if status:
    conditions.append(f"status = ${idx}")
    params.append(status)
    idx += 1

where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
rows = await pool.fetch(f"SELECT * FROM things {where}", *params)
```

### JSONB operations
```python
# Insert JSONB
await pool.execute(
    "INSERT INTO things (data) VALUES ($1::jsonb)",
    json.dumps(data),
)

# Containment query (uses GIN index)
await pool.fetch(
    "SELECT * FROM things WHERE data @> $1::jsonb",
    json.dumps({"status": "active"}),
)

# Access nested JSONB field
await pool.fetch(
    "SELECT data->>'name' as name FROM things WHERE data->>'type' = $1",
    type_value,
)
```

### Common asyncpg methods
```python
pool.fetchval(query, *args)    # Single value (e.g., UUID from RETURNING id)
pool.fetchrow(query, *args)    # Single row as Record
pool.fetch(query, *args)       # Multiple rows as list[Record]
pool.execute(query, *args)     # No return value (returns status string like "DELETE 1")
```

## Error Handling

```python
# Not-found pattern for mutations
async def entity_delete(pool: asyncpg.Pool, entity_id: uuid.UUID) -> None:
    result = await pool.execute("DELETE FROM things WHERE id = $1", entity_id)
    if result == "DELETE 0":
        raise ValueError(f"Thing {entity_id} not found")

# Not-found pattern for reads (return None, don't raise)
async def entity_get(pool: asyncpg.Pool, entity_id: uuid.UUID) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM things WHERE id = $1", entity_id)
    if row is None:
        return None
    return _row_to_dict(row)

# Let constraint violations propagate
# (asyncpg.UniqueViolationError, asyncpg.ForeignKeyViolationError, etc.)
```

## Activity Logging Pattern

Some butlers log activity for audit trails or feeds:

```python
async def _log_activity(
    pool: asyncpg.Pool,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    await pool.execute(
        """INSERT INTO activity_log (entity_type, entity_id, action, details)
           VALUES ($1, $2, $3, $4::jsonb)""",
        entity_type,
        entity_id,
        action,
        json.dumps(details or {}),
    )
```

## What NOT to Do

- Don't import FastMCP or add decorators — the framework wraps tools
- Don't manage connection pools — the pool is passed in
- Don't catch and silence exceptions — let them propagate
- Don't use raw string interpolation for SQL — always use parameterized queries
- Don't use `datetime.now()` — use `now()` in SQL for consistency
- Don't add OpenTelemetry tracing unless the butler is infrastructure (like heartbeat/switchboard)
- Don't put helper functions in `__init__.py` — use `_helpers.py` for shared code
