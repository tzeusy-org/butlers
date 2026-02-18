"""Module state management endpoints — list and toggle butler modules.

Provides REST endpoints for querying live module states and toggling
the enabled/disabled flag on individual modules via the butler's MCP server.

Routes
------
GET  /api/butlers/{name}/module-states
    Return all modules with runtime state (health, enabled flag, failure info).
    This complements the existing ``/api/butlers/{name}/modules`` health endpoint
    by exposing the richer ``ModuleRuntimeState`` data (from daemon.get_module_states()).

PUT  /api/butlers/{name}/module-states/{module_name}/enabled
    Toggle the enabled flag for a single module.
    - 404 if the module is not known to the daemon.
    - 409 if the module is unavailable (health=failed or cascade_failed).
"""

from __future__ import annotations

import asyncio
import json
import logging
import tomllib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.models import ApiResponse
from butlers.api.models.modules import (
    ModuleRuntimeStateResponse,
    ModuleSetEnabledRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers", "modules"])

_STATUS_TIMEOUT_S = 5.0

# Default roster location relative to the repository root.
_DEFAULT_ROSTER_DIR = Path(__file__).resolve().parents[4] / "roster"


def _get_roster_dir() -> Path:
    """Return the roster directory path. Override in tests."""
    return _DEFAULT_ROSTER_DIR


def _has_module_config(butler_dir: Path, module_name: str) -> bool:
    """Return True if butler.toml contains a [modules.{module_name}] section."""
    toml_path = butler_dir / "butler.toml"
    if not toml_path.is_file():
        return False
    try:
        data = tomllib.loads(toml_path.read_bytes().decode())
        modules = data.get("modules", {})
        return module_name in modules
    except (tomllib.TOMLDecodeError, OSError):
        return False


async def _get_module_states_via_mcp(
    butler_name: str,
    mcp_manager: MCPClientManager,
    butler_dir: Path,
) -> list[ModuleRuntimeStateResponse]:
    """Call the butler's MCP ``module.states`` tool and return parsed results.

    Falls back to an empty list if the butler is unreachable.
    """
    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(butler_name),
            timeout=_STATUS_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool("module.states", {}),
            timeout=_STATUS_TIMEOUT_S,
        )

        raw: dict = {}
        if result.content:
            text = result.content[0].text if hasattr(result.content[0], "text") else ""
            if text:
                raw = json.loads(text)

        # Read butler.toml once before iterating to avoid repeated file I/O per module.
        toml_path = butler_dir / "butler.toml"
        configured_modules: set[str] = set()
        if toml_path.is_file():
            try:
                configured_modules = set(
                    tomllib.loads(toml_path.read_bytes().decode()).get("modules", {}).keys()
                )
            except (tomllib.TOMLDecodeError, OSError):
                pass

        states: list[ModuleRuntimeStateResponse] = []
        for name, info in raw.items():
            states.append(
                ModuleRuntimeStateResponse(
                    name=name,
                    health=info.get("health", "active"),
                    enabled=info.get("enabled", True),
                    failure_phase=info.get("failure_phase"),
                    failure_error=info.get("failure_error"),
                    has_config=name in configured_modules,
                )
            )
        return states

    except (ButlerUnreachableError, TimeoutError):
        logger.debug("Butler %s is unreachable for module.states call", butler_name)
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{butler_name}' is unreachable",
        )
    except Exception:
        logger.warning(
            "Unexpected error fetching module states for butler %s",
            butler_name,
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Failed to retrieve module states for butler '{butler_name}'",
        )


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/module-states
# ---------------------------------------------------------------------------


@router.get(
    "/{name}/module-states",
    response_model=ApiResponse[list[ModuleRuntimeStateResponse]],
)
async def get_module_states(
    name: str,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    roster_dir: Path = Depends(_get_roster_dir),
) -> ApiResponse[list[ModuleRuntimeStateResponse]]:
    """Return runtime state for all modules in a butler.

    Calls the butler's ``module.states`` MCP tool to retrieve live runtime
    state including health, enabled flag, and failure details.

    Raises 404 if the butler is not known.
    Raises 503 if the butler daemon is unreachable.
    """
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    butler_dir = roster_dir / name
    states = await _get_module_states_via_mcp(name, mcp_manager, butler_dir)
    return ApiResponse[list[ModuleRuntimeStateResponse]](data=states)


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/module-states/{module_name}/enabled
# ---------------------------------------------------------------------------


@router.put(
    "/{name}/module-states/{module_name}/enabled",
    response_model=ApiResponse[ModuleRuntimeStateResponse],
)
async def set_module_enabled(
    name: str,
    module_name: str,
    request: ModuleSetEnabledRequest,
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    roster_dir: Path = Depends(_get_roster_dir),
) -> ApiResponse[ModuleRuntimeStateResponse]:
    """Toggle the enabled/disabled state of a module on a live butler.

    Calls the butler's ``module.set_enabled`` MCP tool, which persists the
    change to the KV state store.

    Returns
    -------
    ApiResponse[ModuleRuntimeStateResponse]
        The updated module state.

    Raises
    ------
    404
        If the butler or module is not found.
    409
        If the module is unavailable (health=failed or cascade_failed) and
        cannot be toggled.
    503
        If the butler daemon is unreachable.
    """
    if not any(cfg.name == name for cfg in configs):
        raise HTTPException(status_code=404, detail=f"Butler not found: {name}")

    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(name),
            timeout=_STATUS_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool(
                "module.set_enabled",
                {"name": module_name, "enabled": request.enabled},
            ),
            timeout=_STATUS_TIMEOUT_S,
        )
    except (ButlerUnreachableError, TimeoutError):
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' is unreachable",
        )

    # Parse the tool response
    response_data: dict = {}
    if result.content:
        text = result.content[0].text if hasattr(result.content[0], "text") else ""
        if text:
            response_data = json.loads(text)

    status = response_data.get("status", "error")
    if status == "error":
        error_msg = response_data.get("error", "Unknown error")
        # Determine the appropriate HTTP status code from the error message
        if "not exist" in error_msg or "Unknown module" in error_msg:
            raise HTTPException(status_code=404, detail=f"Module not found: {module_name}")
        if "unavailable" in error_msg or "cannot be toggled" in error_msg:
            raise HTTPException(
                status_code=409,
                detail=f"Module '{module_name}' is unavailable and cannot be toggled",
            )
        raise HTTPException(status_code=500, detail=error_msg)

    # Fetch updated state to return full state object
    butler_dir = roster_dir / name
    has_config = _has_module_config(butler_dir, module_name)

    updated = ModuleRuntimeStateResponse(
        name=module_name,
        health="active",  # Must be active if set_enabled succeeded
        enabled=request.enabled,
        failure_phase=None,
        failure_error=None,
        has_config=has_config,
    )
    return ApiResponse[ModuleRuntimeStateResponse](data=updated)
