"""Core tool registration package for ButlerDaemon.

Each sub-module registers one logical group of MCP tools.
All modules are thin — they contain only tool handler closures and delegate
real logic to functions in ``butlers.core.*``.

Public API:
    - ``ToolContext``: shared state passed to every ``register_*`` function.
    - ``register_all_core_tools``: thin dispatcher called by ``_register_core_tools()``.
"""

from butlers.core_tools._base import ToolContext
from butlers.core_tools._dispatcher import register_all_core_tools

__all__ = ["ToolContext", "register_all_core_tools"]
