"""The public MCP request host (RFC 0039 D1): auth, allowlist, budgets, DLP, audit.

PR 2's FastMCP entrypoint calls :meth:`CapabilityHost.handle` once per HTTP request on
``MCP_PATH`` and serves :meth:`Metrics.render` on ``METRICS_PORT``.
"""

from butlers.capability_host.config import (
    MCP_PATH,
    MCP_PORT,
    METRICS_PORT,
    BudgetLimits,
    ConfigError,
    FileLayers,
    HostSettings,
)
from butlers.capability_host.host import CapabilityHost, HostResponse, Tool, default_tools
from butlers.capability_host.observe import Metrics
from butlers.capability_host.projection import create_pool

__all__ = [
    "MCP_PATH",
    "MCP_PORT",
    "METRICS_PORT",
    "BudgetLimits",
    "CapabilityHost",
    "ConfigError",
    "FileLayers",
    "HostResponse",
    "HostSettings",
    "Metrics",
    "Tool",
    "create_pool",
    "default_tools",
]
