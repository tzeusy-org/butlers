"""Butler management endpoints — Phase 7 fold-in.

§9.2 of the settings-redesign OpenSpec.

Provides:
  GET  /api/butlers/{name}/prompt            — current system prompt
  PUT  /api/butlers/{name}/prompt            — update prompt (snapshots prior version)
  GET  /api/butlers/{name}/prompt/history    — version history DESC
  GET  /api/butlers/{name}/tools             — list tool grants
  PUT  /api/butlers/{name}/tools/{tool}      — update tool grant/scope
  GET  /api/butlers/{name}/memory-access     — memory tier access metadata
  POST /api/butlers/{name}/kill              — initiate graceful shutdown

All mutations append to ``public.audit_log`` via ``audit.append()``.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.routers.audit import AuditTableNotAvailableError
from butlers.api.routers.audit import append as audit_append

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butler-management"])

_MCP_CALL_TIMEOUT_S = 30.0


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PromptVersion(BaseModel):
    """A versioned snapshot of a butler's system prompt."""

    butler_name: str
    prompt: str
    version: int
    updated_at: str
    updated_by: str | None = None


class PromptUpdateRequest(BaseModel):
    """Request body for PUT /api/butlers/{name}/prompt."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    actor: str = "owner"


class ButlerTool(BaseModel):
    """A tool grant entry for a butler."""

    name: str
    description: str | None = None
    allowed: bool
    scope: str | None = None


class ToolUpdateRequest(BaseModel):
    """Request body for PUT /api/butlers/{name}/tools/{tool}."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    scope: str | None = None
    actor: str = "owner"


class MemoryAccess(BaseModel):
    """Memory tier access metadata for a butler."""

    read: list[str]
    write: list[str]
    namespace: str | None = None
    embedding_model: str | None = None
    drops_7d: int = 0


class KillRequest(BaseModel):
    """Request body for POST /api/butlers/{name}/kill."""

    model_config = ConfigDict(extra="forbid")

    grace_seconds: int = 30
    actor: str = "owner"

    @field_validator("grace_seconds")
    @classmethod
    def validate_grace(cls, v: int) -> int:
        if v < 0:
            raise ValueError("grace_seconds must be non-negative")
        if v > 300:
            raise ValueError("grace_seconds must not exceed 300")
        return v


class KillResponse(BaseModel):
    """Response for POST /api/butlers/{name}/kill."""

    butler_name: str
    grace_seconds: int
    status: str = "shutdown_initiated"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _assert_butler_exists(name: str, configs: list[ButlerConnectionInfo]) -> None:
    """Raise HTTP 404 if the butler is not in the discovered config list."""
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")


async def _get_shared_pool(db: DatabaseManager):
    """Return the credential_shared_pool, raising HTTP 503 on failure."""
    try:
        return db.credential_shared_pool()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc


# ---------------------------------------------------------------------------
# §9.2-A: System prompt endpoints
# ---------------------------------------------------------------------------


@router.get("/{name}/prompt", response_model=ApiResponse[PromptVersion])
async def get_butler_prompt(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[PromptVersion]:
    """Return the current versioned system prompt for a butler.

    If no prompt row exists, returns version 0 with an empty prompt string.
    """
    _assert_butler_exists(name, configs)
    pool = await _get_shared_pool(db)

    row = await pool.fetchrow(
        """
        SELECT butler_name, prompt, version, updated_at, updated_by
        FROM public.system_prompt_history
        WHERE butler_name = $1
        ORDER BY version DESC
        LIMIT 1
        """,
        name,
    )

    if row is None:
        # No prompt recorded yet — return empty version 0.
        pv = PromptVersion(
            butler_name=name,
            prompt="",
            version=0,
            updated_at="",
            updated_by=None,
        )
    else:
        pv = PromptVersion(
            butler_name=name,
            prompt=row["prompt"],
            version=row["version"],
            updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
            updated_by=row["updated_by"],
        )

    return ApiResponse[PromptVersion](data=pv)


@router.put("/{name}/prompt", response_model=ApiResponse[PromptVersion])
async def update_butler_prompt(
    name: str,
    body: PromptUpdateRequest = Body(...),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[PromptVersion]:
    """Update a butler's system prompt.

    The new prompt is appended as the next version in
    ``public.system_prompt_history``.  Appends an audit entry for
    ``butler.prompt_set``.

    This edit is **load-bearing**: the spawner reads the HEAD of
    ``public.system_prompt_history`` for the butler at spawn time and uses it as
    the live system prompt (falling back to the on-disk ``CLAUDE.md`` seed when
    no history row exists). The next session the butler spawns therefore
    receives this prompt.
    """
    _assert_butler_exists(name, configs)
    pool = await _get_shared_pool(db)

    # Insert new version atomically — no separate SELECT to avoid races.
    row = await pool.fetchrow(
        """
        INSERT INTO public.system_prompt_history (butler_name, prompt, version, updated_by)
        VALUES (
            $1,
            $2,
            (SELECT COALESCE(MAX(version), 0) + 1
             FROM public.system_prompt_history
             WHERE butler_name = $1),
            $3
        )
        RETURNING butler_name, prompt, version, updated_at, updated_by
        """,
        name,
        body.prompt,
        body.actor,
    )
    new_version: int = row["version"]

    try:
        await audit_append(
            pool,
            body.actor,
            "butler.prompt_set",
            target=name,
            note=f"v{new_version}",
        )
    except AuditTableNotAvailableError:
        logger.warning("audit_log not available; skipping audit for butler.prompt_set on %s", name)

    pv = PromptVersion(
        butler_name=row["butler_name"],
        prompt=row["prompt"],
        version=row["version"],
        updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
        updated_by=row["updated_by"],
    )

    return ApiResponse[PromptVersion](data=pv)


@router.get("/{name}/prompt/history", response_model=PaginatedResponse[PromptVersion])
async def get_butler_prompt_history(
    name: str,
    limit: int = Query(20, ge=1, le=100, description="Max versions to return"),
    offset: int = Query(0, ge=0),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[PromptVersion]:
    """Return version history for a butler's system prompt, newest first."""
    _assert_butler_exists(name, configs)
    pool = await _get_shared_pool(db)

    total: int = (
        await pool.fetchval(
            "SELECT COUNT(*) FROM public.system_prompt_history WHERE butler_name = $1",
            name,
        )
        or 0
    )

    rows = await pool.fetch(
        """
        SELECT butler_name, prompt, version, updated_at, updated_by
        FROM public.system_prompt_history
        WHERE butler_name = $1
        ORDER BY version DESC
        OFFSET $2 LIMIT $3
        """,
        name,
        offset,
        limit,
    )

    versions = [
        PromptVersion(
            butler_name=r["butler_name"],
            prompt=r["prompt"],
            version=r["version"],
            updated_at=r["updated_at"].isoformat() if r["updated_at"] else "",
            updated_by=r["updated_by"],
        )
        for r in rows
    ]

    return PaginatedResponse[PromptVersion](
        data=versions,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# §9.2-B: Tools endpoints
# ---------------------------------------------------------------------------


@router.get("/{name}/tools", response_model=ApiResponse[list[ButlerTool]])
async def get_butler_tools(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ButlerTool]]:
    """Return the list of tool grants for a butler.

    Returns rows from ``public.butler_tools`` ordered alphabetically by
    tool name.  Returns an empty list when no grants have been configured.
    """
    _assert_butler_exists(name, configs)
    pool = await _get_shared_pool(db)

    rows = await pool.fetch(
        """
        SELECT tool_name, description, allowed, scope
        FROM public.butler_tools
        WHERE butler_name = $1
        ORDER BY tool_name ASC
        """,
        name,
    )

    tools = [
        ButlerTool(
            name=r["tool_name"],
            description=r["description"],
            allowed=r["allowed"],
            scope=r["scope"],
        )
        for r in rows
    ]

    return ApiResponse[list[ButlerTool]](data=tools)


@router.put("/{name}/tools/{tool}", response_model=ApiResponse[ButlerTool])
async def update_butler_tool(
    name: str,
    tool: str,
    body: ToolUpdateRequest = Body(...),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ButlerTool]:
    """Upsert a tool grant for a butler.

    Creates the row if it does not exist (INSERT … ON CONFLICT).  Appends an
    audit entry for ``butler.tool_set``.
    """
    _assert_butler_exists(name, configs)
    pool = await _get_shared_pool(db)

    row = await pool.fetchrow(
        """
        INSERT INTO public.butler_tools (butler_name, tool_name, allowed, scope, updated_by)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (butler_name, tool_name) DO UPDATE
            SET allowed    = EXCLUDED.allowed,
                scope      = EXCLUDED.scope,
                updated_at = now(),
                updated_by = EXCLUDED.updated_by
        RETURNING tool_name, description, allowed, scope
        """,
        name,
        tool,
        body.allowed,
        body.scope,
        body.actor,
    )

    try:
        await audit_append(
            pool,
            body.actor,
            "butler.tool_set",
            target=f"{name}.{tool}",
            note=f"allowed={body.allowed}",
        )
    except AuditTableNotAvailableError:
        logger.warning(
            "audit_log not available; skipping audit for butler.tool_set on %s.%s", name, tool
        )

    bt = ButlerTool(
        name=row["tool_name"],
        description=row["description"],
        allowed=row["allowed"],
        scope=row["scope"],
    )

    return ApiResponse[ButlerTool](data=bt)


# ---------------------------------------------------------------------------
# §9.2-C: Memory access endpoint
# ---------------------------------------------------------------------------


@router.get("/{name}/memory-access", response_model=ApiResponse[MemoryAccess])
async def get_butler_memory_access(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[MemoryAccess]:
    """Return memory tier access metadata for a butler.

    Calls the butler's ``memory_access`` MCP tool when online.  Falls back to
    an empty (no access) response when the butler is offline or the tool is
    not available.
    """
    _assert_butler_exists(name, configs)

    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_MCP_CALL_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool("memory_access", {}),
            timeout=_MCP_CALL_TIMEOUT_S,
        )

        payload: dict = {}
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        payload = parsed
                except (json.JSONDecodeError, AttributeError):
                    pass

        raw_drops = payload.get("drops_7d")
        drops_7d = int(raw_drops) if raw_drops not in (None, "") else 0

        ma = MemoryAccess(
            read=payload.get("read", []),
            write=payload.get("write", []),
            namespace=payload.get("namespace"),
            embedding_model=payload.get("embedding_model"),
            drops_7d=drops_7d,
        )

    except (ButlerUnreachableError, TimeoutError):
        logger.debug("Butler %s is offline; returning empty memory-access", name)
        ma = MemoryAccess(read=[], write=[])
    except Exception:
        logger.warning("Failed to get memory-access for butler %s", name, exc_info=True)
        ma = MemoryAccess(read=[], write=[])

    return ApiResponse[MemoryAccess](data=ma)


# ---------------------------------------------------------------------------
# §9.2-D: Kill switch endpoint
# ---------------------------------------------------------------------------


@router.post("/{name}/kill", response_model=ApiResponse[KillResponse])
async def kill_butler(
    name: str,
    body: KillRequest = Body(...),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[KillResponse]:
    """Initiate a graceful shutdown of a butler.

    Sends the ``shutdown`` tool call to the butler's MCP server with the
    configured ``grace_seconds``.  The butler is expected to honour the
    grace window before terminating.  Returns 503 if the butler is
    unreachable.  Appends an audit entry for ``butler.kill``.
    """
    _assert_butler_exists(name, configs)

    pool = await _get_shared_pool(db)

    try:
        await audit_append(
            pool,
            body.actor,
            "butler.kill",
            target=name,
            note=f"grace={body.grace_seconds}s",
        )
    except AuditTableNotAvailableError:
        logger.warning("audit_log not available; skipping audit for butler.kill on %s", name)

    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_MCP_CALL_TIMEOUT_S,
        )
        await asyncio.wait_for(
            client.call_tool("shutdown", {"grace_seconds": body.grace_seconds}),
            timeout=_MCP_CALL_TIMEOUT_S,
        )
    except ButlerUnreachableError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' is unreachable — cannot initiate shutdown",
        )
    except TimeoutError:
        # A timeout here may mean the butler has already started shutting down.
        logger.info("Kill call to butler %s timed out — may have started shutting down", name)

    resp = KillResponse(butler_name=name, grace_seconds=body.grace_seconds)
    return ApiResponse[KillResponse](data=resp)
