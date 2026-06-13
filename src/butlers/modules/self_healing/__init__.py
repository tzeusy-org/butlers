"""Self-healing module — registers report_error and get_healing_status MCP tools.

This is the **primary** entry point for the butler self-healing system.
When a butler agent encounters an unexpected exception during a session, it
calls ``report_error`` with structured error context and its own diagnostic
reasoning.  The module fingerprints the error and either:

  1. **QA relay path (primary):** When the QA staffer is registered with the
     Switchboard, relays the finding via Switchboard's ``route()`` MCP tool
     calling the QA staffer's ``report_finding`` tool directly.  The QA
     staffer handles all dispatch and investigation.  This preserves the
     non-negotiable MCP-only inter-butler communication rule (rule #3).

  2. **Direct dispatch path (fallback):** When the QA staffer is unavailable
     (not registered with Switchboard, Switchboard unreachable, or route()
     call fails), falls back to direct dispatch via ``core.healing.dispatch``.
     Behavior is identical to the pre-QA-staffer self-healing flow.

A secondary fallback in the spawner's except block catches hard crashes where
the agent never got a chance to self-report.  Both paths converge on the same
dispatch engine in ``src/butlers/core/healing/``.

Spec reference
--------------
openspec/changes/qa-staffer/specs/self-healing-module/spec.md
openspec/changes/butler-self-healing/specs/self-healing-module/spec.md
openspec/changes/butler-self-healing/design.md §3 (Module)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
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
    redispatch_attempt_by_id,
)
from butlers.core.healing.fingerprint import compute_fingerprint_from_report
from butlers.modules.base import Module, ToolMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: TTL in seconds for caching the result of list_butlers() (QA availability).
_QA_AVAILABILITY_CACHE_TTL = 60.0

#: Name of the QA staffer as registered with the Switchboard.
_QA_BUTLER_NAME = "qa"

#: Tool name on the QA staffer that accepts relayed findings.
_QA_REPORT_FINDING_TOOL = "report_finding"


# ---------------------------------------------------------------------------
# Prometheus metrics (task 13.9)
# ---------------------------------------------------------------------------
#
# Tracks how often the direct dispatch fallback fires (QA staffer unreachable).
# After 30 days with zero activations, the fallback path can be removed.
# See openspec/changes/qa-staffer/tasks.md §13.9.


def _get_qa_fallback_counter():
    """Return the qa_fallback_activations_total Prometheus Counter."""
    try:
        from prometheus_client import Counter

        return Counter(
            "qa_fallback_activations_total",
            "Total direct-dispatch fallback activations when QA staffer is unreachable",
            labelnames=["butler"],
        )
    except (ImportError, ValueError):
        logger.debug(
            "Failed to initialize Prometheus counter 'qa_fallback_activations_total';"
            " metric will not be exported",
            exc_info=True,
        )
        return None


_qa_fallback_activations_total = _get_qa_fallback_counter()


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

    When the QA staffer is available (registered with Switchboard), findings
    are relayed via Switchboard's ``route()`` tool to the QA staffer's
    ``report_finding`` tool.  When unavailable, the module falls back to
    direct dispatch via ``core.healing.dispatch``.
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

        # Switchboard client for QA relay (injected via wire_runtime)
        self._switchboard_client: Any = None

        # QA availability cache: (is_available: bool, cached_at: float)
        self._qa_available_cache: tuple[bool, float] | None = None

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
        return None  # Schema owned by core migration (public.healing_attempts)

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

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        """Run recovery and cleanup before accepting dispatch calls.

        Transitions stale ``investigating`` rows to ``timeout`` (deadline-aware)
        or ``failed`` (agent never spawned) using the updated recovery logic from
        core_066.  Reaps orphaned healing worktrees.

        Note: ``dispatch_pending`` re-dispatch was removed in core_066 — rows
        are now created as ``investigating`` directly, and the per-phase watchdog
        handles rows that never receive an agent within their deadline.
        """
        self._config = (
            config if isinstance(config, SelfHealingConfig) else SelfHealingConfig(**(config or {}))
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
            logger.warning("SelfHealingModule startup: reap_stale_worktrees failed", exc_info=True)

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

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register report_error and get_healing_status tools on the MCP server."""
        self._config = (
            config if isinstance(config, SelfHealingConfig) else SelfHealingConfig(**(config or {}))
        )
        if butler_name:
            self._butler_name = butler_name
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

        @mcp.tool()
        async def retry_healing(attempt_id: str) -> dict:
            """Re-dispatch a healing investigation by attempt id.

            This is the daemon-side entry point for the dashboard "retry"
            action.  The dashboard API runs in a separate process from the
            butler daemon and has no spawner, so it cannot dispatch a healing
            agent directly.  Instead it inserts a fresh ``investigating`` row
            and calls this tool, which spawns the healing agent in the daemon
            process where the spawner lives.

            The 10-gate admission-control sequence is intentionally bypassed —
            the operator explicitly requested the retry and the row already
            exists.  Returns ``{accepted, fingerprint, reason, attempt_id}``
            immediately; the healing agent runs asynchronously.

            Parameters
            ----------
            attempt_id:
                UUID string of the ``investigating`` healing attempt row to
                re-dispatch (created by the dashboard retry endpoint).
            """
            return await module._handle_retry_healing(attempt_id=attempt_id)

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

        Attempts to relay findings to the QA staffer via Switchboard (primary
        path).  Falls back to direct dispatch via core.healing when the QA
        staffer is unavailable.  Returns immediately; dispatch is async.
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

        # Try the QA relay path (primary)
        qa_result = await self._try_qa_relay(
            fingerprint=fp.fingerprint,
            exception_type=error_type,
            call_site=call_site or "",
            severity=fp.severity,
            event_summary=error_message[:200],
            context=context,
        )

        if qa_result is not None:
            return qa_result

        # QA relay unavailable — fall back to direct dispatch.
        # Increment the fallback counter for observability (task 13.9):
        # after 30 days with zero activations, the fallback path can be removed.
        if _qa_fallback_activations_total is not None:
            try:
                _qa_fallback_activations_total.labels(butler=self._butler_name).inc()
            except Exception:
                logger.debug(
                    "report_error: failed to increment QA fallback activation metric",
                    exc_info=True,
                )  # Metric errors must not disrupt the fallback path

        return await self._direct_dispatch(fp, error_type, error_message, context)

    async def _try_qa_relay(
        self,
        fingerprint: str,
        exception_type: str,
        call_site: str,
        severity: int,
        event_summary: str,
        context: str | None,
    ) -> dict | None:
        """Attempt to relay a finding to the QA staffer via Switchboard.

        Returns a response dict on success, or ``None`` if the QA staffer is
        unavailable or the relay fails (caller should fall back to direct dispatch).

        The relay is a direct tool-to-tool call: Switchboard's ``route()`` MCP
        tool forwards to the QA staffer's ``report_finding`` tool.  This
        preserves the non-negotiable MCP-only inter-butler communication rule.

        Parameters
        ----------
        fingerprint:
            Pre-computed fingerprint hex string.
        exception_type:
            Fully qualified exception class name.
        call_site:
            ``<file>:<function>`` call site.
        severity:
            Integer severity score (0=critical … 3=low).
        event_summary:
            First 200 chars of the error message (not anonymized here — the
            QA staffer's ``report_finding`` tool handles sensitivity).
        context:
            Optional diagnostic context (sensitive — not stored by QA staffer).

        Returns
        -------
        dict | None
            Response dict with ``accepted=True`` if relay succeeded.
            ``None`` if unavailable or relay failed.
        """
        client = self._switchboard_client
        if client is None:
            logger.debug(
                "report_error: Switchboard client not connected — falling back to direct dispatch"
            )
            return None

        # Check QA availability (cached with TTL to avoid per-error roundtrip)
        qa_available = await self._is_qa_available(client)
        if not qa_available:
            logger.debug("report_error: QA staffer not available — falling back to direct dispatch")
            return None

        # Relay finding via Switchboard route() → QA staffer's report_finding
        try:
            result = await asyncio.wait_for(
                client.call_tool(
                    "route",
                    {
                        "target_butler": _QA_BUTLER_NAME,
                        "tool_name": _QA_REPORT_FINDING_TOOL,
                        "allow_stale": True,
                        "args": {
                            "fingerprint": fingerprint,
                            "exception_type": exception_type,
                            "call_site": call_site,
                            "severity": severity,
                            "event_summary": event_summary,
                            "source_butler": self._butler_name,
                            **({"context": context} if context is not None else {}),
                        },
                    },
                ),
                timeout=10.0,
            )

            # Check if the route() call itself returned an error
            if isinstance(result, dict) and result.get("error"):
                logger.warning(
                    "report_error: Switchboard route() returned error: %s — "
                    "falling back to direct dispatch",
                    result.get("error"),
                )
                # Note: we do NOT invalidate the QA availability cache on a single failure
                return None

            logger.debug(
                "report_error: relayed finding to QA staffer via Switchboard (fingerprint=%s)",
                fingerprint[:12],
            )
            return {
                "accepted": True,
                "fingerprint": fingerprint,
                "message": "Finding relayed to QA staffer via Switchboard",
            }

        except Exception as relay_exc:
            logger.warning(
                "report_error: Switchboard route() call failed: %s — "
                "falling back to direct dispatch",
                relay_exc,
            )
            return None

    async def _is_qa_available(self, client: Any) -> bool:
        """Check if the QA staffer is registered with the Switchboard.

        Result is cached with TTL to avoid a list_butlers() call on every
        report_error invocation.

        Parameters
        ----------
        client:
            The Switchboard MCP client.

        Returns
        -------
        bool
            ``True`` if the QA staffer is registered and reachable.
        """
        now = time.monotonic()

        # Return cached result if still fresh
        if self._qa_available_cache is not None:
            is_available, cached_at = self._qa_available_cache
            if now - cached_at < _QA_AVAILABILITY_CACHE_TTL:
                return is_available

        # Query Switchboard
        try:
            butlers_result = await asyncio.wait_for(
                client.call_tool("list_butlers", {}),
                timeout=5.0,
            )

            # list_butlers returns a list of agent records or a dict with a list
            agents: list = []
            if isinstance(butlers_result, list):
                agents = butlers_result
            elif isinstance(butlers_result, dict):
                agents = butlers_result.get("butlers", butlers_result.get("agents", []))

            is_available = any(
                (
                    a.get("name") == _QA_BUTLER_NAME
                    if isinstance(a, dict)
                    else str(a) == _QA_BUTLER_NAME
                )
                for a in agents
            )

        except Exception as exc:
            logger.debug("report_error: list_butlers() failed: %s — QA staffer unavailable", exc)
            is_available = False

        self._qa_available_cache = (is_available, now)
        return is_available

    async def _direct_dispatch(
        self,
        fp: Any,
        error_type: str,
        error_message: str,
        context: str | None,
    ) -> dict:
        """Fall back to direct dispatch via core.healing when QA is unavailable.

        This preserves the pre-QA-staffer self-healing behavior: 10-gate
        sequence, worktree creation, healing agent spawn.

        Parameters
        ----------
        fp:
            FingerprintResult from compute_fingerprint_from_report().
        error_type:
            Fully qualified exception class name.
        error_message:
            The exception message.
        context:
            Optional diagnostic context.

        Returns
        -------
        dict
            Standard report_error response.
        """
        healing_cfg = HealingConfig.from_module_config(self._config.model_dump())

        # We don't have the current session_id in the MCP tool handler context.
        # Use a zero UUID as a sentinel — the dispatch engine uses it only for
        # fingerprint persistence and session_ids seeding. The healing agent will
        # have its own real session_id.
        sentinel_session_id = uuid.UUID(int=0)

        if self._pool is None or self._spawner is None:
            # No DB pool or spawner available — return gracefully
            return {
                "accepted": False,
                "fingerprint": fp.fingerprint,
                "reason": "not_configured",
                "message": "Self-healing module not fully initialised (no DB pool or spawner)",
            }

        # Prune completed tasks before dispatching to prevent unbounded accumulation.
        self._watchdog_tasks = [t for t in self._watchdog_tasks if not t.done()]

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
            task_registry=self._watchdog_tasks,
            metrics=getattr(self._spawner, "_metrics", None),
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
            "severity_above_threshold": "Error severity is above the configured threshold",
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
                self._pool,
                fingerprint,
                window_minutes=60 * 24 * 365,  # 1 year
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

        # No fingerprint — return 5 most recent for this butler (SQL-filtered)
        butler_attempts = await list_attempts(self._pool, limit=5, butler_name=self._butler_name)

        if not butler_attempts:
            return {
                "attempts": [],
                "message": "No healing attempts found",
            }

        return {
            "attempts": [_serialize_attempt(a) for a in butler_attempts],
            "message": f"Found {len(butler_attempts)} healing attempt(s)",
        }

    async def _handle_retry_healing(self, attempt_id: str) -> dict:
        """Core handler for the ``retry_healing`` MCP tool.

        Re-dispatches an existing ``investigating`` healing attempt by id,
        spawning the healing agent in the daemon process (where the spawner
        lives).  Returns a standard ``{accepted, fingerprint, reason}`` dict.
        """
        try:
            parsed_id = uuid.UUID(str(attempt_id))
        except (ValueError, AttributeError, TypeError):
            return {
                "accepted": False,
                "reason": "invalid_attempt_id",
                "message": f"Not a valid UUID: {attempt_id!r}",
            }

        if self._pool is None or self._spawner is None:
            return {
                "accepted": False,
                "attempt_id": str(parsed_id),
                "reason": "not_configured",
                "message": "Self-healing module not fully initialised (no DB pool or spawner)",
            }

        healing_cfg = HealingConfig.from_module_config(self._config.model_dump())

        # Prune completed watchdog tasks before dispatching.
        self._watchdog_tasks = [t for t in self._watchdog_tasks if not t.done()]

        result = await redispatch_attempt_by_id(
            pool=self._pool,
            attempt_id=parsed_id,
            config=healing_cfg,
            repo_root=self._repo_root,
            spawner=self._spawner,
            task_registry=self._watchdog_tasks,
            gh_token=None,
            metrics=getattr(self._spawner, "_metrics", None),
        )

        response: dict = {
            "accepted": result.accepted,
            "fingerprint": result.fingerprint,
            "reason": result.reason,
            "attempt_id": str(result.attempt_id) if result.attempt_id else str(parsed_id),
        }
        if result.accepted:
            response["message"] = "Healing agent re-dispatched"
        else:
            response["message"] = f"Re-dispatch rejected: {result.reason}"
        return response

    # ------------------------------------------------------------------
    # Runtime wiring (called by daemon after register_tools)
    # ------------------------------------------------------------------

    def wire_runtime(
        self,
        spawner: Any,
        repo_root: Path | str,
        switchboard_client: Any = None,
    ) -> None:
        """Wire the module to the spawner and runtime dependencies.

        Called by the butler daemon after ``register_tools()`` to give the
        module access to the spawner (for healing agent dispatch) and the
        repo root (for worktree creation).  Butler identity is provided
        exclusively through ``register_tools()`` and is not repeated here.

        Parameters
        ----------
        spawner:
            Spawner instance for dispatching healing agents (fallback path).
        repo_root:
            Absolute path to the repository root.
        switchboard_client:
            Optional Switchboard MCP client for QA relay.  When provided and
            the QA staffer is registered, findings are relayed via Switchboard.
            When ``None``, the module uses direct dispatch only.
        """
        self._spawner = spawner
        self._repo_root = Path(repo_root)
        self._switchboard_client = switchboard_client


# ---------------------------------------------------------------------------
# Serialisation helper
# ---------------------------------------------------------------------------


def _serialize_attempt(row: dict) -> dict:
    """Convert a HealingAttemptRow to a JSON-serialisable dict."""
    result: dict = {}
    for key, value in row.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
        elif hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        elif isinstance(value, list):
            # session_ids array — may contain UUID objects
            result[key] = [str(v) if isinstance(v, uuid.UUID) else v for v in value]
        else:
            result[key] = value
    return result
