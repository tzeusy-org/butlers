"""Infra core tools: status, trigger, tick, correct."""

from __future__ import annotations

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
            Optional complexity tier ("trivial", "medium", "high",
            "extra_high", "discretion", "self_healing"). Defaults to medium
            when omitted.
        """
        from butlers.core.model_routing import Complexity

        spawn_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "context": context,
            "trigger_source": "trigger",
        }
        if complexity is not None:
            spawn_kwargs["complexity"] = Complexity(complexity)
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
                registered_butlers=None,
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
                registered_butlers=None,
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
                registered_butlers=None,
                switchboard_client=client,
                original_butler=butler_name,
                target_butler=target_butler,
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
                registered_butlers=None,
            )
        else:
            from butlers.core.corrections import FAILURE_MESSAGES

            return {
                "status": "failed",
                "correction_id": "",
                "summary": FAILURE_MESSAGES["unknown_correction_type"].format(type=correction_type),
            }
