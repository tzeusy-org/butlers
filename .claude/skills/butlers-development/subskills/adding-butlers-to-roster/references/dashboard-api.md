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

from fastapi import APIRouter, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.deps import Db
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
    *,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Db,
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
- Import `Db` from `butlers.api.deps` for the shared DatabaseManager dependency;
  do not define a local dependency stub or factory. The lifespan in
  `src/butlers/api/app.py` initializes it with `init_db_manager()` and closes it
  with `shutdown_db_manager()`. `Depends(get_db)` uses the same public callable.
- Use `OptionalDb`/`get_db_optional()` from `butlers.api.deps` only for existing
  best-effort reads that allow an uninitialized manager. This adapter does not
  suppress pool, query or authorization errors.
- Tests override the public `get_db` callable through `app.dependency_overrides`.
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
