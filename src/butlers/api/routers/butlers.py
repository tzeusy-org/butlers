"""Butler discovery and status endpoints.

Scans the roster directory for butler configs, then probes each butler's
MCP server in parallel to determine live status.  Unreachable butlers
(timeout, connection refused, etc.) are reported with ``status: "down"``
rather than causing the entire request to fail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.models import (
    ApiResponse,
    ButlerConfigResponse,
    ButlerDetail,
    ButlerSummary,
    MCPToolCallRequest,
    MCPToolCallResponse,
    MCPToolInfo,
    ModuleInfo,
    ModuleStatus,
    ProcessFacts,
    ScheduleEntry,
    SkillInfo,
    TickResponse,
    TriggerRequest,
    TriggerResponse,
)
from butlers.api.read_models.butlers_v1 import query_sessions_24h
from butlers.api.routers.audit import log_audit_entry
from butlers.config import ConfigError, load_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers"])

# Timeout (in seconds) for each individual butler status probe.
_STATUS_TIMEOUT_S = 5.0
_MCP_LIST_TOOLS_TIMEOUT_S = 15.0
_MCP_CALL_TIMEOUT_S = 30.0

# Default roster location relative to the repository root.
_DEFAULT_ROSTER_DIR = Path(__file__).resolve().parents[4] / "roster"


def _get_roster_dir() -> Path:
    """Return the roster directory path. Override in tests."""
    return _DEFAULT_ROSTER_DIR


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


_STALE_CONNECTION_ERRORS = (anyio.ClosedResourceError, anyio.BrokenResourceError)


async def _probe_butler(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
    sessions_24h: int = 0,
) -> ButlerSummary:
    """Probe a single butler's MCP server and return a summary.

    Attempts to connect via the MCP client and call ``ping()``.  If the
    butler responds, ``status`` is ``"ok"``.  Any failure (connection
    refused, timeout, unexpected error) results in ``status: "down"``.

    On stale-connection errors (``ClosedResourceError``, ``BrokenResourceError``)
    the cached client is evicted and a single retry is attempted with a fresh
    connection before reporting the butler as down.
    """
    for attempt in range(2):
        try:
            client = await asyncio.wait_for(
                mgr.get_client(info.name),
                timeout=_STATUS_TIMEOUT_S,
            )
            await asyncio.wait_for(client.ping(), timeout=_STATUS_TIMEOUT_S)
            status = "ok"
            break
        except ButlerUnreachableError:
            logger.debug("Butler %s is unreachable", info.name)
            status = "down"
            break
        except _STALE_CONNECTION_ERRORS:
            await mgr.invalidate_client(info.name)
            if attempt == 0:
                logger.debug("Butler %s: stale connection, retrying with fresh client", info.name)
                continue
            logger.debug("Butler %s is unreachable after reconnect attempt", info.name)
            status = "down"
        except TimeoutError:
            logger.debug("Butler %s timed out", info.name)
            status = "down"
            break
        except Exception:
            logger.warning("Unexpected error probing butler %s", info.name, exc_info=True)
            status = "down"
            break

    return ButlerSummary(
        name=info.name,
        status=status,
        port=info.port,
        type=info.type,
        description=info.description,
        sessions_24h=sessions_24h,
    )


async def _fetch_sessions_24h(
    db: DatabaseManager,
    butler_names: list[str] | None = None,
) -> dict[str, int]:
    """Return a mapping of butler_name -> session count for the last 24 hours.

    Delegates to :func:`~butlers.api.read_models.butlers_v1.query_sessions_24h`
    from the versioned read-model boundary.  This call is best-effort: any DB
    or query failure returns an empty mapping so the list endpoint stays
    available when the DB is unhealthy.

    Args:
        db: The database manager.
        butler_names: Subset of butler names to query.  Defaults to all
            registered butlers if omitted.
    """
    return await query_sessions_24h(db, butler_names, timeout_s=_STATUS_TIMEOUT_S)


@router.get("", response_model=ApiResponse[list[ButlerSummary]])
async def list_butlers(
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ButlerSummary]]:
    """Return all discovered butlers with live status and 24h session counts."""
    config_names = [info.name for info in configs]
    sessions_by_butler = await _fetch_sessions_24h(db, butler_names=config_names)
    tasks = [
        _probe_butler(mgr, info, sessions_24h=sessions_by_butler.get(info.name, 0))
        for info in configs
    ]
    summaries = await asyncio.gather(*tasks)
    return ApiResponse[list[ButlerSummary]](data=list(summaries))


async def _fetch_last_session_started_at(
    db: DatabaseManager,
    butler_name: str,
) -> datetime | None:
    """Return the MAX ``started_at`` timestamp from the butler's ``sessions`` table.

    Uses the butler-scoped pool so no ``butler_name`` column filter is needed.
    Returns ``None`` when the sessions table does not exist, the butler has no
    sessions, or the DB is unavailable.
    """
    query = (
        "SELECT CASE WHEN to_regclass('sessions') IS NOT NULL"
        " THEN (SELECT MAX(started_at) FROM sessions)"
        " ELSE NULL END"
    )
    try:
        pool = db.pool(butler_name)
        value = await asyncio.wait_for(
            pool.fetchval(query),
            timeout=_STATUS_TIMEOUT_S,
        )
    except Exception:
        logger.debug(
            "Failed to fetch last_session_started_at for butler %s", butler_name, exc_info=True
        )
        return None
    return value


# ---------------------------------------------------------------------------
# Process facts helpers
# ---------------------------------------------------------------------------


async def _fetch_registered_duration(
    db: DatabaseManager,
    butler_name: str,
) -> float | None:
    """Return seconds elapsed since the butler first registered in the switchboard.

    Queries ``switchboard.butler_registry.registered_at`` and returns the age
    in seconds relative to now.  Returns ``None`` when the switchboard pool is
    unavailable or the butler has no registry row.
    """
    try:
        sw_pool = db.pool("switchboard")
    except KeyError:
        return None

    try:
        row = await asyncio.wait_for(
            sw_pool.fetchrow(
                "SELECT registered_at FROM butler_registry WHERE name = $1",
                butler_name,
            ),
            timeout=_STATUS_TIMEOUT_S,
        )
    except Exception:
        logger.debug("Failed to fetch registered_at for butler %s", butler_name, exc_info=True)
        return None

    if row is None or row["registered_at"] is None:
        return None

    registered_at = row["registered_at"]
    # Normalize to UTC-aware datetime
    if hasattr(registered_at, "tzinfo") and registered_at.tzinfo is None:
        registered_at = registered_at.replace(tzinfo=UTC)

    elapsed = (datetime.now(UTC) - registered_at).total_seconds()
    return max(elapsed, 0.0)


def _build_process_facts(
    connection_info: ButlerConnectionInfo,
    roster_dir: Path,
    registered_duration_seconds: float | None,
) -> ProcessFacts:
    """Assemble process facts from stable topology sources.

    - ``container_name``: derived from the ``BUTLERS_HOST`` env var (the MCP
      host the dashboard uses to reach butler daemons). Absent when unset or
      resolves to ``localhost``.
    - ``port``: from ``ButlerConnectionInfo.port``.
    - ``registered_duration_seconds``: seconds since switchboard registration.
    - ``config_path``: roster-relative path, e.g. ``roster/general/butler.toml``.
    """
    host = os.environ.get("BUTLERS_HOST", "localhost")
    container_name: str | None = host if host and host != "localhost" else None

    toml_path = roster_dir / connection_info.name / "butler.toml"
    config_path = str(toml_path.relative_to(roster_dir.parent))

    return ProcessFacts(
        container_name=container_name,
        port=connection_info.port,
        registered_duration_seconds=registered_duration_seconds,
        config_path=config_path,
    )


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


@router.get("/{name}", response_model=ApiResponse[ButlerDetail])
async def get_butler_detail(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    roster_dir: Path = Depends(_get_roster_dir),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ButlerDetail]:
    """Return detailed information for a single butler.

    Looks up the butler by name in the roster directory, parses its config,
    discovers skills, and attempts to get live status via MCP.
    """
    connection_info: ButlerConnectionInfo | None = None
    for cfg in configs:
        if cfg.name == name:
            connection_info = cfg
            break

    if connection_info is None:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    butler_dir = roster_dir / name
    try:
        config = load_config(butler_dir)
    except ConfigError:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    modules = [
        ModuleInfo(name=mod_name, enabled=True, config=mod_cfg or None)
        for mod_name, mod_cfg in config.modules.items()
    ]

    schedules = [ScheduleEntry(name=s.name, cron=s.cron, prompt=s.prompt) for s in config.schedules]

    skills = _discover_skills(butler_dir)
    status = await _get_live_status(name, mcp_manager)
    sessions_map = await _fetch_sessions_24h(db, butler_names=[name])
    last_session_started_at = await _fetch_last_session_started_at(db, name)
    registered_duration = await _fetch_registered_duration(db, name)
    process_facts = _build_process_facts(connection_info, roster_dir, registered_duration)

    detail = ButlerDetail(
        name=config.name,
        port=config.port,
        type=config.type.value,
        status=status,
        description=config.description,
        db_name=config.db_name,
        db_schema=config.db_schema,
        modules=modules,
        schedules=schedules,
        skills=skills,
        sessions_24h=sessions_map.get(name, 0),
        last_session_started_at=last_session_started_at,
        process_facts=process_facts,
    )

    return ApiResponse[ButlerDetail](data=detail)


# ---------------------------------------------------------------------------
# Config endpoint
# ---------------------------------------------------------------------------


def _read_optional_text(path: Path) -> str | None:
    """Read a text file and return its contents, or None if the file does not exist."""
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def _extract_mcp_result_text(result: object) -> str | None:
    """Extract text content from an MCP tool result."""
    content = getattr(result, "content", None)
    if not isinstance(content, list):
        return None

    text_parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            text_parts.append(text)

    if not text_parts:
        return None
    return "\n".join(text_parts)


def _parse_mcp_result_payload(raw_text: str | None) -> object:
    """Parse MCP text payload as JSON when possible, else return plain text."""
    if raw_text is None:
        return None
    try:
        return json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return raw_text


def _normalize_tool_info(tool: object) -> MCPToolInfo | None:
    """Normalize FastMCP tool metadata from dict- or object-shaped records."""
    if isinstance(tool, dict):
        name = tool.get("name")
        description = tool.get("description")
        input_schema = tool.get("inputSchema", tool.get("input_schema"))
    else:
        name = getattr(tool, "name", None)
        description = getattr(tool, "description", None)
        input_schema = getattr(tool, "input_schema", getattr(tool, "inputSchema", None))

    if not isinstance(name, str) or not name:
        return None
    if description is not None and not isinstance(description, str):
        description = str(description)
    if input_schema is not None and not isinstance(input_schema, dict):
        input_schema = None

    return MCPToolInfo(name=name, description=description, input_schema=input_schema)


@router.get("/{name}/config", response_model=ApiResponse[ButlerConfigResponse])
async def get_butler_config(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    roster_dir: Path = Depends(_get_roster_dir),
) -> ApiResponse[ButlerConfigResponse]:
    """Return the butler's configuration files as a structured response.

    Reads ``butler.toml`` and parses it as a dict.  Also reads the markdown
    config files (``CLAUDE.md``, ``AGENTS.md``, ``MANIFESTO.md``) as raw text.
    Missing markdown files are returned as ``null``.
    """
    # Validate butler exists in discovered configs
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    butler_dir = roster_dir / name
    toml_path = butler_dir / "butler.toml"

    if not toml_path.is_file():
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    try:
        butler_toml = tomllib.loads(toml_path.read_bytes().decode())
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.error("Failed to read butler.toml for %s: %s", name, exc)
        raise HTTPException(status_code=500, detail=f"Failed to read config for butler: {name}")

    config_response = ButlerConfigResponse(
        butler_toml=butler_toml,
        claude_md=_read_optional_text(butler_dir / "CLAUDE.md"),
        agents_md=_read_optional_text(butler_dir / "AGENTS.md"),
        manifesto_md=_read_optional_text(butler_dir / "MANIFESTO.md"),
    )

    return ApiResponse[ButlerConfigResponse](data=config_response)


def _discover_skills(butler_dir: Path) -> list[str]:
    """List skill names from the butler's .agents/skills/ directory."""
    skills_dir = butler_dir / ".agents" / "skills"
    if not skills_dir.is_dir():
        return []

    skills: list[str] = []
    for entry in sorted(skills_dir.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").exists():
            skills.append(entry.name)

    return skills


async def _get_live_status(name: str, mcp_manager: MCPClientManager) -> str:
    """Attempt to determine a butler's live status via MCP ping.

    Retries once on stale-connection errors (evicts the cached client first).
    """
    for attempt in range(2):
        try:
            client = await mcp_manager.get_client(name)
            await client.ping()
            return "online"
        except _STALE_CONNECTION_ERRORS:
            await mcp_manager.invalidate_client(name)
            if attempt == 0:
                continue
            return "offline"
        except ButlerUnreachableError:
            return "offline"
        except Exception:
            logger.warning("Unexpected error pinging butler %s", name, exc_info=True)
            return "offline"
    return "offline"


# ---------------------------------------------------------------------------
# Skills endpoint
# ---------------------------------------------------------------------------


def _read_skills(butler_dir: Path) -> list[SkillInfo]:
    """Read skill names and SKILL.md content from the butler's .agents/skills/ directory."""
    skills_dir = butler_dir / ".agents" / "skills"
    if not skills_dir.is_dir():
        return []

    skills: list[SkillInfo] = []
    for entry in sorted(skills_dir.iterdir()):
        skill_md = entry / "SKILL.md"
        if entry.is_dir() and skill_md.exists():
            content = skill_md.read_text(encoding="utf-8")
            skills.append(SkillInfo(name=entry.name, content=content))

    return skills


@router.get("/{name}/skills", response_model=ApiResponse[list[SkillInfo]])
async def list_butler_skills(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    roster_dir: Path = Depends(_get_roster_dir),
) -> ApiResponse[list[SkillInfo]]:
    """Return skills for a single butler with name and SKILL.md content."""
    connection_info: ButlerConnectionInfo | None = None
    for cfg in configs:
        if cfg.name == name:
            connection_info = cfg
            break

    if connection_info is None:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    butler_dir = roster_dir / name
    skills = _read_skills(butler_dir)

    return ApiResponse[list[SkillInfo]](data=skills)


# ---------------------------------------------------------------------------
# MCP debug endpoints
# ---------------------------------------------------------------------------


@router.get("/{name}/mcp/tools", response_model=ApiResponse[list[MCPToolInfo]])
async def list_butler_mcp_tools(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[list[MCPToolInfo]]:
    """Return MCP tools exposed by a single butler."""
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_MCP_LIST_TOOLS_TIMEOUT_S,
        )
        raw_tools = await asyncio.wait_for(
            client.list_tools(),
            timeout=_MCP_LIST_TOOLS_TIMEOUT_S,
        )
    except ButlerUnreachableError:
        raise HTTPException(status_code=503, detail=f"Butler '{name}' is unreachable")
    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail=f"MCP tool listing for butler '{name}' timed out",
        )
    except Exception as exc:
        logger.warning("Unexpected MCP list_tools failure for %s", name, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to list MCP tools for butler '{name}': {exc}",
        )

    if not isinstance(raw_tools, list):
        logger.warning(
            "Unexpected list_tools payload type for butler %s: %s",
            name,
            type(raw_tools).__name__,
        )
        raw_tools = []

    tools: list[MCPToolInfo] = []
    for raw in raw_tools:
        normalized = _normalize_tool_info(raw)
        if normalized is not None:
            tools.append(normalized)

    return ApiResponse[list[MCPToolInfo]](data=tools)


@router.post("/{name}/mcp/call", response_model=ApiResponse[MCPToolCallResponse])
async def call_butler_mcp_tool(
    name: str,
    request: MCPToolCallRequest,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[MCPToolCallResponse]:
    """Invoke an MCP tool on a butler for debugging."""
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    tool_name = request.tool_name.strip()
    if not tool_name:
        raise HTTPException(status_code=400, detail="tool_name must not be empty")

    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_MCP_CALL_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool(tool_name, request.arguments),
            timeout=_MCP_CALL_TIMEOUT_S,
        )
    except ButlerUnreachableError:
        raise HTTPException(status_code=503, detail=f"Butler '{name}' is unreachable")
    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail=f"MCP tool call '{tool_name}' to butler '{name}' timed out",
        )
    except Exception as exc:
        logger.warning(
            "Unexpected MCP tool call failure for %s.%s",
            name,
            tool_name,
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail=f"MCP tool call '{tool_name}' failed for butler '{name}': {exc}",
        )

    raw_text = _extract_mcp_result_text(result)
    parsed_result = _parse_mcp_result_payload(raw_text)
    is_error = bool(getattr(result, "is_error", False))
    response = MCPToolCallResponse(
        tool_name=tool_name,
        arguments=request.arguments,
        result=parsed_result,
        raw_text=raw_text,
        is_error=is_error,
    )
    return ApiResponse[MCPToolCallResponse](data=response)


# ---------------------------------------------------------------------------
# Module health endpoint
# ---------------------------------------------------------------------------


async def _get_module_health_via_mcp(
    name: str,
    mcp_manager: MCPClientManager,
    module_names: list[str],
) -> list[ModuleStatus]:
    """Call the butler's MCP ``status()`` tool and extract per-module health.

    Expects the current status payload shape:
    ``{"modules": {"mod": {"status": ...}}}``.

    New optional OAuth/credential fields are populated when the butler's
    status() tool emits them; older butlers that don't yet emit these fields
    will return None for all three (forward-compatible graceful degradation):

    - ``oauth_status``: ``"granted"`` | ``"reauth_needed"`` | ``"not_configured"``
    - ``oauth_expires_at``: ISO-8601 datetime string (parsed to datetime)
    - ``credential_health``: ``"ok"`` | ``"warning"`` | ``"error"``
    """
    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_STATUS_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool("status", {}),
            timeout=_STATUS_TIMEOUT_S,
        )

        status_data: dict = {}
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                status_data = json.loads(text)

        raw_modules = status_data.get("modules", {})
        if not isinstance(raw_modules, dict):
            logger.warning(
                "Unexpected module status payload for butler %s: expected object, got %s",
                name,
                type(raw_modules).__name__,
            )
            raw_modules = {}
        butler_health = status_data.get("health", "unknown")

        modules: list[ModuleStatus] = []
        for mod_name in module_names:
            mod_info = raw_modules.get(mod_name)
            if mod_info is None:
                modules.append(
                    ModuleStatus(
                        name=mod_name,
                        enabled=True,
                        status="error",
                        error="Module configured but not loaded by butler",
                    )
                )
                continue

            daemon_status = (
                mod_info.get("status", "unknown") if isinstance(mod_info, dict) else "unknown"
            )

            # Extract OAuth/credential fields when present; default to None for
            # butlers that haven't implemented these fields yet.  Unknown values
            # are silently coerced to None so future butler versions with new
            # enum variants don't break the dashboard for existing deployments.
            _VALID_OAUTH_STATUS = {"granted", "reauth_needed", "not_configured"}
            _VALID_CREDENTIAL_HEALTH = {"ok", "warning", "error"}

            oauth_status_raw = mod_info.get("oauth_status") if isinstance(mod_info, dict) else None
            if oauth_status_raw not in _VALID_OAUTH_STATUS:
                if oauth_status_raw is not None:
                    logger.warning(
                        "Unknown oauth_status %r for butler %s module %s; ignoring",
                        oauth_status_raw,
                        name,
                        mod_name,
                    )
                oauth_status = None
            else:
                oauth_status = oauth_status_raw

            oauth_expires_at_raw = (
                mod_info.get("oauth_expires_at") if isinstance(mod_info, dict) else None
            )

            credential_health_raw = (
                mod_info.get("credential_health") if isinstance(mod_info, dict) else None
            )
            if credential_health_raw not in _VALID_CREDENTIAL_HEALTH:
                if credential_health_raw is not None:
                    logger.warning(
                        "Unknown credential_health %r for butler %s module %s; ignoring",
                        credential_health_raw,
                        name,
                        mod_name,
                    )
                credential_health = None
            else:
                credential_health = credential_health_raw

            oauth_expires_at = None
            if oauth_expires_at_raw:
                try:
                    oauth_expires_at = datetime.fromisoformat(oauth_expires_at_raw)
                except (ValueError, TypeError):
                    logger.warning(
                        "Invalid oauth_expires_at value for butler %s module %s: %r",
                        name,
                        mod_name,
                        oauth_expires_at_raw,
                    )

            if daemon_status == "active":
                if butler_health == "ok":
                    mod_status = "connected"
                elif butler_health == "degraded":
                    mod_status = "degraded"
                else:
                    mod_status = "unknown"
                modules.append(
                    ModuleStatus(
                        name=mod_name,
                        enabled=True,
                        status=mod_status,
                        oauth_status=oauth_status,
                        oauth_expires_at=oauth_expires_at,
                        credential_health=credential_health,
                    )
                )
            else:
                # failed or cascade_failed
                error_msg = mod_info.get("error") if isinstance(mod_info, dict) else None
                phase = mod_info.get("phase") if isinstance(mod_info, dict) else None
                modules.append(
                    ModuleStatus(
                        name=mod_name,
                        enabled=True,
                        status="error",
                        phase=phase,
                        error=error_msg or f"Module {daemon_status}",
                        oauth_status=oauth_status,
                        oauth_expires_at=oauth_expires_at,
                        credential_health=credential_health,
                    )
                )

        return modules

    except (ButlerUnreachableError, TimeoutError):
        return [
            ModuleStatus(name=mod_name, enabled=True, status="unknown") for mod_name in module_names
        ]
    except Exception:
        logger.warning(
            "Unexpected error fetching module health for butler %s",
            name,
            exc_info=True,
        )
        return [
            ModuleStatus(name=mod_name, enabled=True, status="unknown") for mod_name in module_names
        ]


@router.get("/{name}/modules", response_model=ApiResponse[list[ModuleStatus]])
async def get_butler_modules(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    roster_dir: Path = Depends(_get_roster_dir),
) -> ApiResponse[list[ModuleStatus]]:
    """Return module list with health status for a single butler."""
    connection_info: ButlerConnectionInfo | None = None
    for cfg in configs:
        if cfg.name == name:
            connection_info = cfg
            break

    if connection_info is None:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    butler_dir = roster_dir / name
    try:
        config = load_config(butler_dir)
    except ConfigError:
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    module_names = list(config.modules.keys())

    if not module_names:
        return ApiResponse[list[ModuleStatus]](data=[])

    module_statuses = await _get_module_health_via_mcp(name, mcp_manager, module_names)

    return ApiResponse[list[ModuleStatus]](data=module_statuses)


# ---------------------------------------------------------------------------
# Trigger endpoint
# ---------------------------------------------------------------------------

# Timeout (in seconds) for the trigger call to the butler's MCP server.
_TRIGGER_TIMEOUT_S = 120.0


@router.post("/{name}/trigger", response_model=ApiResponse[TriggerResponse])
async def trigger_butler(
    name: str,
    request: TriggerRequest,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TriggerResponse]:
    """Trigger a runtime session on the named butler with the provided prompt.

    Sends the prompt to the butler's MCP ``trigger`` tool and returns
    the session result.  Returns 503 if the butler is unreachable or
    the request times out.
    """
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    from butlers.api.routers.model_settings import _validate_complexity_tier

    _validate_complexity_tier(request.complexity)

    summary = {"prompt": request.prompt[:200], "complexity": request.complexity}
    trigger_args: dict = {"prompt": request.prompt, "complexity": request.complexity}
    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_TRIGGER_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool("trigger", trigger_args),
            timeout=_TRIGGER_TIMEOUT_S,
        )
    except ButlerUnreachableError:
        await log_audit_entry(
            db, name, "trigger", summary, result="error", error="Butler unreachable"
        )
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' is unreachable",
        )
    except TimeoutError:
        await log_audit_entry(
            db, name, "trigger", summary, result="error", error="Request timed out"
        )
        raise HTTPException(
            status_code=503,
            detail=f"Trigger request to butler '{name}' timed out",
        )

    # Parse the MCP tool result
    session_id: str | None = None
    success = True
    output: str | None = None

    if result.content:
        text = result.content[0].text if hasattr(result.content[0], "text") else ""
        if text:
            try:
                data = json.loads(text)
                session_id = data.get("session_id")
                success = data.get("success", True)
                output = data.get("output")
            except (json.JSONDecodeError, AttributeError):
                output = text

    if hasattr(result, "is_error") and result.is_error:
        success = False

    trigger_response = TriggerResponse(
        session_id=session_id,
        success=success,
        output=output,
    )

    if success:
        await log_audit_entry(db, name, "trigger", summary)
    else:
        await log_audit_entry(
            db, name, "trigger", summary, result="error", error=output or "Trigger failed"
        )

    return ApiResponse[TriggerResponse](data=trigger_response)


# ---------------------------------------------------------------------------
# Tick endpoint
# ---------------------------------------------------------------------------


@router.post("/{name}/tick", response_model=ApiResponse[TickResponse])
async def force_butler_tick(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TickResponse] | JSONResponse:
    """Force a scheduler tick on the specified butler.

    Connects to the butler's MCP server and calls the tick tool,
    which triggers the scheduler to run immediately.  Returns 503 if the
    butler is unreachable or the request times out.
    """
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    summary: dict = {}
    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_STATUS_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool("tick", {}),
            timeout=_STATUS_TIMEOUT_S,
        )

        message: str | None = None
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                message = text

        tick_resp = TickResponse(success=True, message=message)
        await log_audit_entry(db, name, "tick", summary)
        return ApiResponse[TickResponse](data=tick_resp)

    except ButlerUnreachableError:
        logger.warning("Butler %s is unreachable for tick", name)
        await log_audit_entry(db, name, "tick", summary, result="error", error="Butler unreachable")
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "butler_unreachable",
                    "message": f"Butler '{name}' is unreachable",
                    "butler": name,
                }
            },
        )
    except TimeoutError:
        logger.warning("Tick request to butler %s timed out", name)
        await log_audit_entry(db, name, "tick", summary, result="error", error="Request timed out")
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "butler_timeout",
                    "message": f"Tick request to butler '{name}' timed out",
                    "butler": name,
                }
            },
        )
