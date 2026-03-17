"""Self-healing module — registers report_error and get_healing_status MCP tools.

This is the **primary** entry point for the butler self-healing system.
When a butler agent encounters an unexpected exception during a session, it
calls ``report_error`` with structured error context and its own diagnostic
reasoning.  The module fingerprints the error, runs gate checks, and
dispatches a healing agent — all as a thin MCP wrapper over the shared
``core.healing`` package.

A secondary fallback in the spawner's except block catches hard crashes where
the agent never got a chance to self-report.  Both paths converge on the same
dispatch engine in ``src/butlers/core/healing/``.

Spec reference
--------------
openspec/changes/butler-self-healing/specs/self-healing-module/spec.md
openspec/changes/butler-self-healing/design.md §3 (Module)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from butlers.core.healing import (
    HealingConfig,
    dispatch_healing,
    get_active_attempt,
    get_recent_attempt,
    list_attempts,
    reap_stale_worktrees,
    recover_stale_attempts,
)
from butlers.core.healing.fingerprint import compute_fingerprint_from_report
from butlers.modules.base import Module, ToolMeta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class SelfHealingConfig(BaseModel):
    """Configuration for the self-healing module.

    All fields have safe defaults so that ``[modules.self_healing]`` with no
    sub-keys is a valid (enabled) configuration.

    Parameters
    ----------
    enabled:
        Master on/off switch.  When ``False``, tools are registered but
        dispatch is always skipped.  Default ``True``.
    severity_threshold:
        Maximum severity score that triggers healing.  Lower is more severe.
        Default 2 (medium).
    max_concurrent:
        Maximum number of simultaneous ``investigating`` rows.  Default 2.
    cooldown_minutes:
        Minutes between investigations of the same fingerprint after any
        terminal status.  Default 60.
    circuit_breaker_threshold:
        Number of consecutive failure statuses before dispatch is halted.
        Default 5.  ``unfixable`` does not count as a failure.
    timeout_minutes:
        Maximum wall-clock minutes for a healing agent session before the
        watchdog cancels it.  Default 30.
    """

    enabled: bool = True
    severity_threshold: int = 2
    max_concurrent: int = 2
    cooldown_minutes: int = 60
    circuit_breaker_threshold: int = 5
    timeout_minutes: int = 30
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Module implementation
# ---------------------------------------------------------------------------


class SelfHealingModule(Module):
    """Self-healing module — primary entry point for butler self-healing.

    Registers two MCP tools:
    - ``report_error``: Butler agent calls this when it encounters an
      unexpected exception.  Returns ``{accepted, fingerprint, reason}``
      immediately; dispatch is async.
    - ``get_healing_status``: Query tool for checking healing attempt status
      by fingerprint or listing recent attempts for this butler.
    """

    def __init__(self) -> None:
        self._config = SelfHealingConfig()
        # Set during register_tools; held for dispatch calls in MCP tool handlers
        self._butler_name: str = "<unknown>"
        self._pool: Any = None
        self._spawner: Any = None
        self._repo_root: Path = Path(".")
        # Background watchdog tasks that on_shutdown must cancel
        self._watchdog_tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Module ABC
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "self_healing"

    @property
    def config_schema(self) -> type[BaseModel]:
        return SelfHealingConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # Schema owned by core migration (shared.healing_attempts)

    # ------------------------------------------------------------------
    # Sensitivity metadata
    # ------------------------------------------------------------------

    def tool_metadata(self) -> dict[str, ToolMeta]:
        """Mark error_message, traceback, and context as sensitive.

        These fields may contain PII from error context or agent reasoning
        about user-related errors.
        """
        return {
            "report_error": ToolMeta(
                arg_sensitivities={
                    "error_message": True,
                    "traceback": True,
                    "context": True,
                }
            ),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Run recovery and cleanup before accepting dispatch calls.

        Transitions stale ``investigating`` rows to ``timeout``/``failed``
        (from a prior crash) and reaps orphaned healing worktrees.
        """
        self._config = (
            config
            if isinstance(config, SelfHealingConfig)
            else SelfHealingConfig(**(config or {}))
        )

        pool = getattr(db, "pool", None) if db is not None else None
        self._pool = pool

        if pool is None:
            logger.debug("SelfHealingModule.on_startup: no DB pool — skipping recovery")
            return

        try:
            recovered = await recover_stale_attempts(pool, self._config.timeout_minutes)
            if recovered:
                logger.info("SelfHealingModule startup: recovered %d stale attempt(s)", recovered)
        except Exception:
            logger.warning(
                "SelfHealingModule startup: recover_stale_attempts failed", exc_info=True
            )

        try:
            await reap_stale_worktrees(self._repo_root, pool)
        except Exception:
            logger.warning(
                "SelfHealingModule startup: reap_stale_worktrees failed", exc_info=True
            )

    async def on_shutdown(self) -> None:
        """Cancel in-progress watchdog tasks (best-effort).

        Active healing agent sessions are NOT terminated — they may complete
        independently after the daemon shuts down.
        """
        for task in list(self._watchdog_tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._watchdog_tasks.clear()
        logger.debug("SelfHealingModule shut down; watchdog tasks cancelled")

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register report_error and get_healing_status tools on the MCP server."""
        self._config = (
            config
            if isinstance(config, SelfHealingConfig)
            else SelfHealingConfig(**(config or {}))
        )
        self._pool = getattr(db, "pool", None) if db is not None else None

        # Capture self reference for tool handler closures
        module = self

        @mcp.tool()
        async def report_error(
            error_type: str,
            error_message: str,
            traceback: str | None = None,
            call_site: str | None = None,
            context: str | None = None,
            tool_name: str | None = None,
            severity_hint: str | None = None,
        ) -> dict:
            """Report an unexpected error for automated self-healing investigation.

            Call this when you encounter an unexpected exception or code bug
            during your session.  The system will fingerprint the error,
            deduplicate it, and dispatch a healing agent to investigate and
            propose a fix via PR.

            Parameters
            ----------
            error_type:
                Fully qualified exception class name (e.g.
                ``asyncpg.exceptions.UndefinedTableError``).
            error_message:
                The exception message.
            traceback:
                The formatted traceback string (optional but recommended).
            call_site:
                ``<file>:<function>`` where the error occurred (your best
                guess).  Derived from traceback if not provided.
            context:
                Your diagnostic reasoning — what you were trying to do, what
                you expected, what you think went wrong.  Do NOT include user
                data, PII, or credentials.
            tool_name:
                Which MCP tool was being called when the error occurred.
            severity_hint:
                Your assessment of impact: ``critical``, ``high``, ``medium``,
                or ``low``.
            """
            return await module._handle_report_error(
                error_type=error_type,
                error_message=error_message,
                traceback_str=traceback,
                call_site=call_site,
                context=context,
                tool_name=tool_name,
                severity_hint=severity_hint,
            )

        @mcp.tool()
        async def get_healing_status(fingerprint: str | None = None) -> dict:
            """Query the status of self-healing attempts.

            Parameters
            ----------
            fingerprint:
                64-character SHA-256 hex fingerprint.  When provided, returns
                the most recent attempt for that fingerprint.  When omitted,
                returns the 5 most recent attempts for this butler.
            """
            return await module._handle_get_healing_status(fingerprint=fingerprint)

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _handle_report_error(
        self,
        error_type: str,
        error_message: str,
        traceback_str: str | None,
        call_site: str | None,
        context: str | None,
        tool_name: str | None,
        severity_hint: str | None,
    ) -> dict:
        """Core handler for the report_error MCP tool.

        Delegates fingerprinting and dispatch to the shared core.healing package.
        Returns immediately with accept/reject status; healing agent spawns async.
        """
        # Compute fingerprint from structured report
        fp = compute_fingerprint_from_report(
            error_type=error_type,
            error_message=error_message,
            call_site=call_site,
            traceback_str=traceback_str,
            severity_hint=severity_hint,
        )

        # Short-circuit: check for active attempt without going through dispatch gates.
        # This provides a fast deduplicated response before touching the gate machinery.
        if self._pool is not None:
            try:
                active = await get_active_attempt(self._pool, fp.fingerprint)
                if active is not None:
                    return {
                        "accepted": False,
                        "fingerprint": fp.fingerprint,
                        "reason": "already_investigating",
                        "attempt_id": str(active["id"]),
                        "message": "This error is already under investigation",
                    }
            except Exception:
                logger.debug("report_error: fast-path active check failed", exc_info=True)

        healing_cfg = HealingConfig.from_module_config(self._config.model_dump())

        # We don't have the current session_id in the MCP tool handler context.
        # Use a zero UUID as a sentinel — the dispatch engine uses it only for
        # fingerprint persistence and session_ids seeding. The healing agent will
        # have its own real session_id.
        import uuid as _uuid

        sentinel_session_id = _uuid.UUID(int=0)

        if self._pool is None or self._spawner is None:
            # No DB pool or spawner available — return gracefully
            return {
                "accepted": False,
                "fingerprint": fp.fingerprint,
                "reason": "not_configured",
                "message": "Self-healing module not fully initialised (no DB pool or spawner)",
            }

        result = await dispatch_healing(
            pool=self._pool,
            butler_name=self._butler_name,
            session_id=sentinel_session_id,
            fingerprint_input=fp,
            config=healing_cfg,
            repo_root=self._repo_root,
            spawner=self._spawner,
            agent_context=context,
            trigger_source="external",  # Not a healing session
            gh_token=None,
        )

        if result.accepted:
            return {
                "accepted": True,
                "fingerprint": result.fingerprint,
                "attempt_id": str(result.attempt_id) if result.attempt_id else None,
                "message": "Healing agent dispatched",
            }

        # Rejected — map reason to a human-readable message
        _reason_messages: dict[str, str] = {
            "no_recursion": "Healing sessions do not trigger recursive healing",
            "disabled": "Self-healing is disabled for this butler",
            "severity_below_threshold": "Error severity is below the configured threshold",
            "already_investigating": "This error is already under investigation",
            "cooldown": "Cooldown period active — a recent investigation already occurred",
            "concurrency_cap": "Maximum concurrent investigations reached",
            "circuit_breaker": "Circuit breaker tripped — too many consecutive failures",
            "no_model": "No self-healing tier model is available",
            "worktree_creation_failed": "Failed to create the healing worktree",
            "internal_error": "An internal error occurred in the dispatch engine",
        }
        reason = result.reason or "unknown"
        message = _reason_messages.get(reason, f"Dispatch rejected: {reason}")

        response: dict = {
            "accepted": False,
            "fingerprint": result.fingerprint,
            "reason": reason,
            "message": message,
        }
        if result.attempt_id is not None:
            response["attempt_id"] = str(result.attempt_id)
        return response

    async def _handle_get_healing_status(
        self,
        fingerprint: str | None,
    ) -> dict:
        """Core handler for the get_healing_status MCP tool."""
        if self._pool is None:
            return {
                "attempts": [],
                "message": "Self-healing module not configured (no DB pool)",
            }

        if fingerprint:
            # Return the most recent attempt for this specific fingerprint
            attempt = await get_recent_attempt(
                self._pool, fingerprint, window_minutes=60 * 24 * 365  # 1 year
            )
            if attempt is None:
                # Also check for active attempts
                attempt = await get_active_attempt(self._pool, fingerprint)
            if attempt is None:
                return {
                    "attempts": [],
                    "message": f"No healing attempts found for fingerprint {fingerprint[:12]}",
                }
            return {
                "attempts": [_serialize_attempt(attempt)],
                "message": "Found healing attempt",
            }

        # No fingerprint — return 5 most recent for this butler
        all_attempts = await list_attempts(self._pool, limit=5)
        butler_attempts = [a for a in all_attempts if a.get("butler_name") == self._butler_name]

        if not butler_attempts:
            return {
                "attempts": [],
                "message": "No healing attempts found",
            }

        return {
            "attempts": [_serialize_attempt(a) for a in butler_attempts],
            "message": f"Found {len(butler_attempts)} healing attempt(s)",
        }

    # ------------------------------------------------------------------
    # Runtime wiring (called by daemon after register_tools)
    # ------------------------------------------------------------------

    def wire_runtime(
        self,
        butler_name: str,
        spawner: Any,
        repo_root: Path | str,
    ) -> None:
        """Wire the module to the spawner and butler identity.

        Called by the butler daemon after ``register_tools()`` to give the
        module access to the spawner (for healing agent dispatch) and the
        repo root (for worktree creation).
        """
        self._butler_name = butler_name
        self._spawner = spawner
        self._repo_root = Path(repo_root)


# ---------------------------------------------------------------------------
# Serialisation helper
# ---------------------------------------------------------------------------


def _serialize_attempt(row: dict) -> dict:
    """Convert a HealingAttemptRow to a JSON-serialisable dict."""
    import uuid as _uuid

    result: dict = {}
    for key, value in row.items():
        if isinstance(value, _uuid.UUID):
            result[key] = str(value)
        elif hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        elif isinstance(value, list):
            # session_ids array — may contain UUID objects
            result[key] = [str(v) if isinstance(v, _uuid.UUID) else v for v in value]
        else:
            result[key] = value
    return result
