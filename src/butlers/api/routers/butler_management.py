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
import hashlib
import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, field_validator

from butlers.api.audit_emit import IgnoresCallerAssertedActor, authenticated_principal
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.session import PromptProvenance
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.api.routers.audit import append as audit_append
from butlers.core.skills import read_system_prompt_with_sources
from butlers.core.spawner_context import compose_effective_system_prompt_receipt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butler-management"])

_MCP_CALL_TIMEOUT_S = 30.0
_ROSTER_ROOT = Path(__file__).resolve().parents[4] / "roster"
_PROMPT_PROVENANCE_ADAPTER = TypeAdapter(list[PromptProvenance])


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
    #: Server-derived acting principal, never a caller-supplied value.
    updated_by: str | None = None


class ButlerEffectivePrompt(BaseModel):
    """Owner-only composed prompt preview and roster-drift receipt."""

    butler_name: str
    status: Literal["captured", "preview", "legacy_unavailable", "corrupt"]
    effective_prompt: str | None = None
    prompt_digest: str | None = None
    prompt_provenance: list[PromptProvenance]
    total_bytes: int | None = None
    roster_digest: str | None = None
    drift_status: Literal["matches_git", "drifted", "unknown"]
    drifted_since: str | None = None
    changed_sources: list[str]


class PromptUpdateRequest(IgnoresCallerAssertedActor):
    """Request body for PUT /api/butlers/{name}/prompt.

    Deliberately carries no ``actor``: the row's ``updated_by`` and the audit
    actor come from :func:`~butlers.api.audit_emit.authenticated_principal`, so
    an ``actor`` sent by a caller is ignored rather than persisted.
    """

    model_config = ConfigDict(extra="forbid")

    prompt: str


class ButlerTool(BaseModel):
    """A tool grant entry for a butler."""

    name: str
    description: str | None = None
    allowed: bool
    scope: str | None = None


class ToolUpdateRequest(IgnoresCallerAssertedActor):
    """Request body for PUT /api/butlers/{name}/tools/{tool}.

    Deliberately carries no ``actor`` — see :class:`PromptUpdateRequest`.
    """

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    scope: str | None = None


class MemoryAccess(BaseModel):
    """Memory tier access metadata for a butler."""

    read: list[str]
    write: list[str]
    namespace: str | None = None
    embedding_model: str | None = None
    drops_7d: int = 0


class KillRequest(IgnoresCallerAssertedActor):
    """Request body for POST /api/butlers/{name}/kill.

    Deliberately carries no ``actor`` — see :class:`PromptUpdateRequest`.
    """

    model_config = ConfigDict(extra="forbid")

    grace_seconds: int = 30

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


def _roster_evidence(
    provenance: list[PromptProvenance],
) -> tuple[str | None, dict[str, tuple[int, str | None]]]:
    """Return a deterministic digest and comparable map for roster sources."""
    rows = {
        entry.source: (entry.bytes, entry.sha)
        for entry in provenance
        if entry.source.startswith("roster:")
    }
    if not rows:
        return None, {}
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), rows


@router.get(
    "/{name}/prompt/effective",
    response_model=ApiResponse[ButlerEffectivePrompt],
)
async def get_butler_effective_prompt(
    name: str,
    _owner: str = Depends(require_dashboard_owner_control),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ButlerEffectivePrompt]:
    """Return the latest prompt that ran and compare its roster inputs to disk.

    The route is separate from prompt authoring: it performs no write and does
    not reinterpret a database prompt row as an overlay. Owner control runs
    before pool or roster access because effective prompts may contain private
    memory/context layers.
    """
    _assert_butler_exists(name, configs)
    shared_pool = await _get_shared_pool(db)
    override = await shared_pool.fetchval(
        """
        SELECT prompt FROM public.system_prompt_history
         WHERE butler_name = $1
         ORDER BY version DESC LIMIT 1
        """,
        name,
    )
    resolved = read_system_prompt_with_sources(
        _ROSTER_ROOT / name,
        name,
        db_override=override if isinstance(override, str) else None,
    )
    preview_receipt = compose_effective_system_prompt_receipt(
        resolved.prompt,
        None,
        base_sources=[
            (source.source, source.status, source.content) for source in resolved.sources
        ],
    )
    current_provenance = _PROMPT_PROVENANCE_ADAPTER.validate_python(
        [entry.as_dict() for entry in preview_receipt.provenance]
    )
    roster_digest, current_roster = _roster_evidence(current_provenance)

    try:
        session_pool = db.pool(name)
        latest = await session_pool.fetchrow(
            """
            SELECT effective_system_prompt, prompt_digest, prompt_provenance, started_at
              FROM sessions
             WHERE effective_system_prompt IS NOT NULL
             ORDER BY started_at DESC, id DESC
             LIMIT 1
            """
        )
    except Exception:
        logger.warning("Failed to read latest prompt receipt for butler=%s", name, exc_info=True)
        latest = None

    if latest is None:
        return ApiResponse[ButlerEffectivePrompt](
            data=ButlerEffectivePrompt(
                butler_name=name,
                status="preview",
                effective_prompt=preview_receipt.prompt,
                prompt_digest=preview_receipt.digest,
                prompt_provenance=current_provenance,
                total_bytes=preview_receipt.total_bytes,
                roster_digest=roster_digest,
                drift_status="unknown",
                changed_sources=[],
            )
        )

    try:
        effective_prompt = latest["effective_system_prompt"]
        prompt_digest = latest["prompt_digest"]
        if not isinstance(effective_prompt, str) or not isinstance(prompt_digest, str):
            raise ValueError("incomplete receipt")
        prompt_bytes = effective_prompt.encode("utf-8")
        if hashlib.sha256(prompt_bytes).hexdigest() != prompt_digest:
            raise ValueError("digest mismatch")
        raw_provenance = latest["prompt_provenance"]
        if isinstance(raw_provenance, str):
            raw_provenance = json.loads(raw_provenance)
        latest_provenance = _PROMPT_PROVENANCE_ADAPTER.validate_python(raw_provenance)
    except (TypeError, UnicodeEncodeError, ValidationError, ValueError):
        return ApiResponse[ButlerEffectivePrompt](
            data=ButlerEffectivePrompt(
                butler_name=name,
                status="corrupt",
                prompt_provenance=[],
                roster_digest=roster_digest,
                drift_status="unknown",
                changed_sources=[],
            )
        )

    _, executed_roster = _roster_evidence(latest_provenance)
    changed_sources = sorted(
        source
        for source in current_roster.keys() | executed_roster.keys()
        if current_roster.get(source) != executed_roster.get(source)
    )
    drift_status: Literal["matches_git", "drifted", "unknown"] = (
        "unknown" if not executed_roster else "drifted" if changed_sources else "matches_git"
    )
    started_at = latest["started_at"]
    return ApiResponse[ButlerEffectivePrompt](
        data=ButlerEffectivePrompt(
            butler_name=name,
            status="captured",
            effective_prompt=effective_prompt,
            prompt_digest=prompt_digest,
            prompt_provenance=latest_provenance,
            total_bytes=len(prompt_bytes),
            roster_digest=roster_digest,
            drift_status=drift_status,
            drifted_since=(
                started_at.isoformat()
                if drift_status == "drifted" and started_at is not None
                else None
            ),
            changed_sources=changed_sources,
        )
    )


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

    ``updated_by`` and the audit actor are derived from the authenticated
    principal; an ``actor`` in the request body is ignored.
    """
    _assert_butler_exists(name, configs)
    pool = await _get_shared_pool(db)
    updated_by = authenticated_principal()

    # Insert new version + audit entry inside one transaction so a missing
    # audit table rolls back the prompt insert too, instead of persisting an
    # un-audited prompt change. AuditTableNotAvailableError is intentionally
    # NOT caught here — it propagates to the app-level handler, which returns
    # 503 {"error": "audit_unavailable"} (dashboard-audit-log spec).
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
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
            updated_by,
        )
        new_version: int = row["version"]

        await audit_append(
            conn,
            updated_by,
            "butler.prompt_set",
            target=name,
            note=f"v{new_version}",
        )

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

    ``updated_by`` and the audit actor are derived from the authenticated
    principal; an ``actor`` in the request body is ignored.
    """
    _assert_butler_exists(name, configs)
    pool = await _get_shared_pool(db)
    updated_by = authenticated_principal()

    # Upsert + audit entry inside one transaction so a missing audit table
    # rolls back the tool-grant change too, instead of persisting un-audited.
    # AuditTableNotAvailableError is intentionally NOT caught here — it
    # propagates to the app-level handler, which returns
    # 503 {"error": "audit_unavailable"} (dashboard-audit-log spec).
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
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
            updated_by,
        )

        await audit_append(
            conn,
            updated_by,
            "butler.tool_set",
            target=f"{name}.{tool}",
            note=f"allowed={body.allowed}",
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
    unreachable.  Appends an audit entry for ``butler.kill``, whose actor is
    derived from the authenticated principal; an ``actor`` in the request body
    is ignored.
    """
    _assert_butler_exists(name, configs)

    pool = await _get_shared_pool(db)

    # Audit before dispatching the shutdown call: if the audit table is
    # unavailable, fail fast and never fire the (irreversible) kill. Do NOT
    # catch AuditTableNotAvailableError — it propagates to the app-level
    # handler, which returns 503 {"error": "audit_unavailable"}
    # (dashboard-audit-log spec).
    await audit_append(
        pool,
        authenticated_principal(),
        "butler.kill",
        target=name,
        note=f"grace={body.grace_seconds}s",
    )

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
