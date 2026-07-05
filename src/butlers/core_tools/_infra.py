"""Infra core tools: status, trigger, tick, correct."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from butlers.core.corrections import (
    CORRECT_TOOL_DESCRIPTION,
    CorrectionType,
    handle_action_reversal,
    handle_data_correction,
    handle_memory_deletion,
    handle_misroute,
)
from butlers.core.scheduler import tick as _tick
from butlers.core.telemetry import tool_span
from butlers.core.tool_call_capture import get_current_runtime_session_id
from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)


def _registered_butler_names(ctx: ToolContext) -> list[str]:
    """Return the names of all butlers known to the roster.

    Used to validate ``target_butler``/``correct_butler`` params on the
    ``correct`` tool against real, configured butlers. ``list_butlers()``
    does synchronous disk I/O and TOML parsing over the whole roster, so the
    result is cached on the ``ToolContext`` for the daemon's lifetime — the
    roster is static while the daemon is running.
    """
    cached = ctx.extra.get("_registered_butler_names")
    if cached is not None:
        return cached

    from butlers.config import list_butlers

    names = [b.name for b in list_butlers()]
    ctx.extra["_registered_butler_names"] = names
    return names


async def _get_target_pool(ctx: ToolContext, target_butler: str | None) -> Any | None:
    """Return a DB pool scoped to *target_butler*'s schema for cross-schema corrections.

    Corrections must read and write the TARGET butler's own tables (state,
    sessions, memories, actions) — not the current butler's schema. Without
    this, a cross-schema correction silently ran the current butler's pool
    against another schema's data.

    Returns ``None`` when there is no cross-schema target (``target_butler``
    is unset or equal to the current butler), so callers fall back to using
    the current butler's own pool. Pools are created lazily and cached on the
    ``ToolContext`` for the daemon's lifetime, mirroring the pattern used by
    ``MemoryModule._get_or_create_chronicler_pool``.

    Pool creation is serialized with a per-daemon lock: without it, two
    concurrent corrections targeting the same butler could both miss the
    cache, each open its own ``Database``/pool, and have one silently
    overwrite the other in ``cache`` — leaking the loser's pool (and its
    live connections) for the rest of the daemon's lifetime.
    """
    if target_butler is None or target_butler == ctx.butler_name:
        return None
    source_db = getattr(ctx.daemon, "db", None)
    if source_db is None:
        return None

    cache: dict[str, Any] = ctx.extra.setdefault("_correction_target_pools", {})
    cached = cache.get(target_butler)
    if cached is not None:
        return cached

    lock: asyncio.Lock = ctx.extra.setdefault("_correction_target_pool_lock", asyncio.Lock())
    async with lock:
        cached = cache.get(target_butler)
        if cached is not None:
            return cached

        from butlers.db import Database

        target_db = Database(
            db_name=source_db.db_name,
            schema=target_butler,
            host=source_db.host,
            port=source_db.port,
            user=source_db.user,
            password=source_db.password,
            ssl=source_db.ssl,
            min_pool_size=1,
            max_pool_size=source_db.max_pool_size,
        )
        await target_db.connect()
        cache[target_butler] = target_db.pool
        return target_db.pool


def register_infra_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register infra group tools: status, trigger, tick, correct."""
    daemon = ctx.daemon
    pool = ctx.pool
    spawner = ctx.spawner
    butler_name = ctx.butler_name

    @_core_tool("infra")
    @tool_span("status", butler_name=butler_name)
    async def status() -> dict:
        """Return butler identity, health, loaded modules, and uptime."""
        uptime_seconds = time.monotonic() - daemon._started_at if daemon._started_at else 0
        health = await daemon._check_health()
        modules_dict: dict[str, dict[str, Any]] = {}
        for mod in daemon._modules:
            ms = daemon._module_statuses.get(mod.name)
            if ms is None or ms.status == "active":
                entry: dict[str, Any] = {"status": "active"}
                try:
                    extra = await mod.extra_status_fields()
                    if extra:
                        entry.update(extra)
                        # Re-assert lifecycle status so modules cannot clobber it.
                        entry["status"] = "active"
                except Exception:
                    logger.debug(
                        "extra_status_fields() failed for module %r", mod.name, exc_info=True
                    )
                modules_dict[mod.name] = entry
            else:
                entry = {"status": ms.status}
                if ms.phase:
                    entry["phase"] = ms.phase
                if ms.error:
                    entry["error"] = ms.error
                modules_dict[mod.name] = entry
        return {
            "name": daemon.config.name,
            "description": daemon.config.description,
            "port": daemon.config.port,
            "modules": modules_dict,
            "health": health,
            "uptime_seconds": round(uptime_seconds, 1),
        }

    @_core_tool("infra")
    async def trigger(
        prompt: str,
        context: str | None = None,
        complexity: str | None = None,
    ) -> dict:
        """Trigger the spawner with a prompt.

        Parameters
        ----------
        prompt:
            The prompt to send to the runtime instance.
        context:
            Optional text to prepend to the prompt.
        complexity:
            Optional complexity tier — one of "reasoning", "workhorse",
            "cheap", "specialty", "local", "legacy". Defaults to "workhorse"
            when omitted. Retired pre-core_092 values (e.g. "medium", "high")
            are still accepted and remapped to their canonical equivalent
            with a logged deprecation warning; any other unrecognized value
            raises a clear error.
        """
        from butlers.core.model_routing import coerce_complexity_tier

        spawn_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "context": context,
            "trigger_source": "trigger",
        }
        if complexity is not None:
            spawn_kwargs["complexity"] = coerce_complexity_tier(complexity)
        result = await spawner.trigger(**spawn_kwargs)
        session_id = getattr(result, "session_id", None)
        return {
            "output": result.output,
            "success": result.success,
            "error": result.error,
            "duration_ms": result.duration_ms,
            "session_id": str(session_id) if session_id else None,
        }

    @_core_tool("infra")
    async def tick() -> dict:
        """Evaluate due scheduled tasks and dispatch them now.

        Primarily driven by the internal scheduler loop. Retained as an MCP tool
        for debugging and manual triggering.
        """
        count = await _tick(
            pool,
            daemon._dispatch_scheduled_task,
            stagger_key=daemon.config.name,
            butler_name=daemon.config.name,
        )
        return {"dispatched": count}

    @_core_tool("infra")
    async def correct(
        correction_type: str,
        target_session_id: str,
        description: str,
        target_butler: str | None = None,
        correct_butler: str | None = None,
        state_key: str | None = None,
        corrected_value: Any | None = None,
        memory_type: str | None = None,
        memory_id: str | None = None,
        action_description: str | None = None,
    ) -> dict[str, Any]:
        __doc__ = CORRECT_TOOL_DESCRIPTION  # noqa: F841

        import uuid as _uuid

        correcting_session_id_str = get_current_runtime_session_id()
        if not correcting_session_id_str:
            return {
                "status": "error",
                "error": (
                    "No active runtime session ID. "
                    "correct tool must be called from a spawned session."
                ),
            }
        try:
            correcting_sid = _uuid.UUID(correcting_session_id_str)
            target_sid = _uuid.UUID(target_session_id)
        except (ValueError, AttributeError) as exc:
            return {"status": "error", "error": f"Invalid UUID: {exc}"}

        # Resolve the real butler registry and, for cross-schema corrections,
        # the target butler's own DB pool — so the correction runs against
        # the target butler's schema instead of the current butler's pool.
        registered_butlers = _registered_butler_names(ctx)
        target_pool: Any | None = None
        if target_butler is not None and target_butler in registered_butlers:
            target_pool = await _get_target_pool(ctx, target_butler)

        if correction_type == CorrectionType.DATA_CORRECTION:
            if state_key is None:
                from butlers.core.corrections import FAILURE_MESSAGES

                return {
                    "status": "failed",
                    "correction_id": "",
                    "summary": FAILURE_MESSAGES["missing_required_parameter"].format(
                        param="state_key", type=correction_type
                    ),
                }
            return await handle_data_correction(
                pool,
                target_session_id=target_sid,
                correcting_session_id=correcting_sid,
                description=description,
                state_key=state_key,
                corrected_value=corrected_value,
                target_butler=target_butler,
                registered_butlers=registered_butlers,
                target_pool=target_pool,
            )
        elif correction_type == CorrectionType.MEMORY_DELETION:
            if memory_type is None or memory_id is None:
                from butlers.core.corrections import FAILURE_MESSAGES

                missing = "memory_type" if memory_type is None else "memory_id"
                return {
                    "status": "failed",
                    "correction_id": "",
                    "summary": FAILURE_MESSAGES["missing_required_parameter"].format(
                        param=missing, type=correction_type
                    ),
                }
            try:
                mem_id = _uuid.UUID(memory_id)
            except ValueError as exc:
                return {"status": "error", "error": f"Invalid memory_id UUID: {exc}"}
            return await handle_memory_deletion(
                pool,
                target_session_id=target_sid,
                correcting_session_id=correcting_sid,
                description=description,
                memory_type=memory_type,
                memory_id=mem_id,
                target_butler=target_butler,
                registered_butlers=registered_butlers,
                target_pool=target_pool,
            )
        elif correction_type == CorrectionType.MISROUTE:
            if correct_butler is None:
                from butlers.core.corrections import FAILURE_MESSAGES

                return {
                    "status": "failed",
                    "correction_id": "",
                    "summary": FAILURE_MESSAGES["missing_required_parameter"].format(
                        param="correct_butler", type=correction_type
                    ),
                }
            client = daemon.switchboard_client
            if client is None:
                return {
                    "status": "error",
                    "error": ("Switchboard is not connected. Cannot perform misroute correction."),
                }
            return await handle_misroute(
                pool,
                target_session_id=target_sid,
                correcting_session_id=correcting_sid,
                description=description,
                correct_butler=correct_butler,
                registered_butlers=registered_butlers,
                switchboard_client=client,
                original_butler=butler_name,
                target_butler=target_butler,
                target_pool=target_pool,
            )
        elif correction_type == CorrectionType.ACTION_REVERSAL:
            if action_description is None:
                from butlers.core.corrections import FAILURE_MESSAGES

                return {
                    "status": "failed",
                    "correction_id": "",
                    "summary": FAILURE_MESSAGES["missing_required_parameter"].format(
                        param="action_description", type=correction_type
                    ),
                }
            return await handle_action_reversal(
                pool,
                target_session_id=target_sid,
                correcting_session_id=correcting_sid,
                description=description,
                action_description=action_description,
                target_butler=target_butler,
                registered_butlers=registered_butlers,
                target_pool=target_pool,
            )
        else:
            from butlers.core.corrections import FAILURE_MESSAGES

            return {
                "status": "failed",
                "correction_id": "",
                "summary": FAILURE_MESSAGES["unknown_correction_type"].format(type=correction_type),
            }
