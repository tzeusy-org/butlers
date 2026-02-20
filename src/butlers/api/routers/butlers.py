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
import tomllib
from pathlib import Path

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
    ModuleInfo,
    ModuleStatus,
    ScheduleEntry,
    SkillInfo,
    TickResponse,
    TriggerRequest,
    TriggerResponse,
)
from butlers.api.routers.audit import log_audit_entry
from butlers.config import ConfigError, load_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers"])

# Timeout (in seconds) for each individual butler status probe.
_STATUS_TIMEOUT_S = 5.0

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


async def _probe_butler(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
) -> ButlerSummary:
    """Probe a single butler's MCP server and return a summary.

    Attempts to connect via the MCP client and call ``ping()``.  If the
    butler responds, ``status`` is ``"ok"``.  Any failure (connection
    refused, timeout, unexpected error) results in ``status: "down"``.
    """
    try:
        client = await asyncio.wait_for(
            mgr.get_client(info.name),
            timeout=_STATUS_TIMEOUT_S,
        )
        await asyncio.wait_for(client.ping(), timeout=_STATUS_TIMEOUT_S)
        status = "ok"
    except ButlerUnreachableError:
        logger.debug("Butler %s is unreachable", info.name)
        status = "down"
    except TimeoutError:
        logger.debug("Butler %s timed out", info.name)
        status = "down"
    except Exception:
        logger.warning("Unexpected error probing butler %s", info.name, exc_info=True)
        status = "down"

    return ButlerSummary(
        name=info.name,
        status=status,
        port=info.port,
        description=info.description,
    )


@router.get("", response_model=ApiResponse[list[ButlerSummary]])
async def list_butlers(
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
) -> ApiResponse[list[ButlerSummary]]:
    """Return all discovered butlers with live status."""
    tasks = [_probe_butler(mgr, info) for info in configs]
    summaries = await asyncio.gather(*tasks)
    return ApiResponse[list[ButlerSummary]](data=list(summaries))


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


@router.get("/{name}", response_model=ApiResponse[ButlerDetail])
async def get_butler_detail(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    roster_dir: Path = Depends(_get_roster_dir),
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

    detail = ButlerDetail(
        name=config.name,
        port=config.port,
        status=status,
        description=config.description,
        db_name=config.db_name,
        db_schema=config.db_schema,
        modules=modules,
        schedules=schedules,
        skills=skills,
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
    """List skill names from the butler's skills/ directory."""
    skills_dir = butler_dir / "skills"
    if not skills_dir.is_dir():
        return []

    skills: list[str] = []
    for entry in sorted(skills_dir.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").exists():
            skills.append(entry.name)

    return skills


async def _get_live_status(name: str, mcp_manager: MCPClientManager) -> str:
    """Attempt to determine a butler's live status via MCP ping."""
    try:
        client = await mcp_manager.get_client(name)
        await client.ping()
        return "online"
    except ButlerUnreachableError:
        return "offline"
    except Exception:
        logger.warning("Unexpected error pinging butler %s", name, exc_info=True)
        return "offline"


# ---------------------------------------------------------------------------
# Skills endpoint
# ---------------------------------------------------------------------------


def _read_skills(butler_dir: Path) -> list[SkillInfo]:
    """Read skill names and SKILL.md content from the butler's skills/ directory."""
    skills_dir = butler_dir / "skills"
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

            if daemon_status == "active":
                if butler_health == "ok":
                    mod_status = "connected"
                elif butler_health == "degraded":
                    mod_status = "degraded"
                else:
                    mod_status = "unknown"
                modules.append(ModuleStatus(name=mod_name, enabled=True, status=mod_status))
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

    summary = {"prompt": request.prompt[:200]}
    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_TRIGGER_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool("trigger", {"prompt": request.prompt}),
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
