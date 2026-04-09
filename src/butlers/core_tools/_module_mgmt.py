"""Module management core tools: module.states, module.set_enabled."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from butlers.core_tools._base import ToolContext


def register_module_mgmt_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register module_mgmt group tools: module.states and module.set_enabled."""
    daemon = ctx.daemon

    @_core_tool("module_mgmt", name="module.states")
    async def module_states() -> dict:
        """Return runtime state (health + enabled flag) for all modules.

        Returns a dict keyed by module name.  Each value is a dict with:
        - health: 'active' | 'failed' | 'cascade_failed'
        - enabled: bool
        - failure_phase: str or null
        - failure_error: str or null
        """
        states = daemon.get_module_states()
        return {
            name: {
                "health": state.health,
                "enabled": state.enabled,
                "failure_phase": state.failure_phase,
                "failure_error": state.failure_error,
            }
            for name, state in states.items()
        }

    @_core_tool("module_mgmt", name="module.set_enabled")
    async def module_set_enabled(name: str, enabled: bool) -> dict:
        """Toggle the runtime enabled flag for a module.

        Persists the change to the KV state store.

        Parameters
        ----------
        name:
            The module name to toggle.
        enabled:
            Whether to enable (True) or disable (False) the module.

        Returns
        -------
        dict
            - status: 'ok'
            - name: module name
            - enabled: new enabled state

        Raises
        ------
        ValueError
            If the module does not exist or is unavailable (health=failed).
        """
        try:
            await daemon.set_module_enabled(name, enabled)
            return {"status": "ok", "name": name, "enabled": enabled}
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
