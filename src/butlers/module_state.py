"""Module runtime state types for the Butler daemon.

These dataclasses and exception types track per-module lifecycle state
(startup outcome, enabled/disabled, health) during daemon operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class ModuleConfigError(Exception):
    """Raised when a module's configuration fails Pydantic validation."""


@dataclass
class ModuleStartupStatus:
    """Per-module startup outcome tracked by the daemon."""

    status: str  # "active", "failed", "cascade_failed"
    phase: str | None = None  # "credentials", "config", "migration", "startup", "tools"
    error: str | None = None


_MODULE_ENABLED_KEY_PREFIX = "module::"
_MODULE_ENABLED_KEY_SUFFIX = "::enabled"
_MODULE_DISABLED_BY_KEY_SUFFIX = "::disabled_by"


@dataclass
class ModuleRuntimeState:
    """Combined health and enabled state for a module at runtime."""

    health: Literal["active", "failed", "cascade_failed"]
    enabled: bool
    failure_phase: str | None = None
    failure_error: str | None = None
