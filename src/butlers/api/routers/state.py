"""State store endpoints — read and write butler key-value state.

Read endpoints query the butler's database directly via ``DatabaseManager``.
Write endpoints proxy through the butler's MCP server to ensure state
mutations go through the butler's own tools (``state_set``, ``state_delete``).

Provides a single router mounted at ``/api/butlers/{name}/state``.

Value contract: the underlying ``state`` JSONB column may hold any valid JSON
value (object, array, scalar, or null).  asyncpg decodes JSONB directly to
native Python types, so no secondary JSON parsing is needed.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerUnreachableError,
    MCPClientManager,
    get_mcp_manager,
)
from butlers.api.models import ApiResponse
from butlers.api.models.state import StateEntry, StateSetRequest
from butlers.api.routers.audit import log_audit_entry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers", "state"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{name}/state",
    response_model=ApiResponse[list[StateEntry]],
)
async def list_state(
    name: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[StateEntry]]:
    """Return all state entries for a butler.

    Queries the butler's ``state`` table directly and returns the full
    key-value list ordered by key.
    """
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    rows = await pool.fetch("SELECT key, value, updated_at FROM state ORDER BY key")

    entries = [
        StateEntry(
            key=row["key"],
            value=row["value"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]

    return ApiResponse[list[StateEntry]](data=entries)


@router.get(
    "/{name}/state/{key:path}",
    response_model=ApiResponse[StateEntry],
)
async def get_state(
    name: str,
    key: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[StateEntry]:
    """Return a single state entry by key.

    Returns 404 if the key does not exist in the butler's state store.
    """
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    row = await pool.fetchrow(
        "SELECT key, value, updated_at FROM state WHERE key = $1",
        key,
    )

    if row is None:
        raise HTTPException(status_code=404, detail=f"State key '{key}' not found")

    return ApiResponse[StateEntry](
        data=StateEntry(key=row["key"], value=row["value"], updated_at=row["updated_at"])
    )


# ---------------------------------------------------------------------------
# Write endpoints (MCP-proxied)
# ---------------------------------------------------------------------------


@router.put(
    "/{name}/state/{key:path}",
    response_model=ApiResponse[dict],
)
async def set_state(
    name: str,
    key: str,
    request: StateSetRequest,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Set a state value via the butler's MCP ``state_set`` tool.

    Proxies the write through MCP so the butler's own state management
    logic is invoked. Returns 503 if the butler is unreachable.
    """
    summary = {"key": key}
    try:
        client = await mgr.get_client(name)
        await client.call_tool("state_set", {"key": key, "value": request.value})
    except ButlerUnreachableError:
        await log_audit_entry(
            db, name, "state.set", summary, result="error", error="Butler unreachable"
        )
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' is unreachable",
        )

    await log_audit_entry(db, name, "state.set", summary)
    return ApiResponse[dict](data={"key": key, "status": "ok"})


@router.delete(
    "/{name}/state/{key:path}",
    response_model=ApiResponse[dict],
)
async def delete_state(
    name: str,
    key: str,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Delete a state entry via the butler's MCP ``state_delete`` tool.

    Proxies the delete through MCP so the butler's own state management
    logic is invoked. Returns 503 if the butler is unreachable.
    """
    summary = {"key": key}
    try:
        client = await mgr.get_client(name)
        await client.call_tool("state_delete", {"key": key})
    except ButlerUnreachableError:
        await log_audit_entry(
            db, name, "state.delete", summary, result="error", error="Butler unreachable"
        )
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' is unreachable",
        )

    await log_audit_entry(db, name, "state.delete", summary)
    return ApiResponse[dict](data={"key": key, "status": "deleted"})
