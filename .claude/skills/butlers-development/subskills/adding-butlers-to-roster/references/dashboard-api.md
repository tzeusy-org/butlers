# Dashboard API Routes

Optional dashboard API endpoints for the butler's data. Auto-discovered by
`src/butlers/api/router_discovery.py`. See also
`docs/api_and_protocols/response-conventions.md`.

## `api/router.py`

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

### Key patterns

- Must export module-level `router` variable (APIRouter instance).
- No `__init__.py` needed in api/ directory.
- `_get_db_manager()` is a dependency stub, overridden at app startup by `wire_db_dependencies()`.
- Use `PaginatedResponse[T]` and `PaginationMeta` from `butlers.api.models`.
- Prefix routes with `/api/<butler-name>`.
- Parameterized queries (`$1`, `$2`) — never string interpolation.

## `api/models.py`

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
