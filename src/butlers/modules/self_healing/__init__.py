"""Declared error relay to the centralized RFC 0015 QA pipeline.

Reception is volatile QA buffering, not investigation or publication. Reporting
butlers never dispatch or recover investigations; shared QA utilities remain
owned by the central pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastmcp import Client
from pydantic import BaseModel, ConfigDict

from butlers.core.healing import get_active_attempt, get_recent_attempt, list_attempts
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
# Config schema
# ---------------------------------------------------------------------------


class SelfHealingConfig(BaseModel):
    """Relay configuration with backwards-compatible dispatch keys.

    ``enabled`` controls report admission. The other five keys retain their
    existing defaults and accepted shape, but are inert: QA owns investigation
    severity, concurrency, cooldown, breaker and timeout policy.
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
    """Register report_error and read-only get_healing_status; never dispatch."""

    def __init__(self) -> None:
        self._config = SelfHealingConfig()
        # Canonical emitter identity comes only from daemon registration.
        self._butler_name: str = "<unknown>"
        self._pool: Any = None

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
        """Initialize relay state without touching shared investigation history."""
        self._config = (
            config if isinstance(config, SelfHealingConfig) else SelfHealingConfig(**(config or {}))
        )
        self._pool = getattr(db, "pool", None) if db is not None else None

    async def on_shutdown(self) -> None:
        """No module-owned investigation tasks or shared cleanup to stop."""

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
            """Relay an unexpected error to QA for centralized triage.

            accepted=true confirms volatile QA reception, not an investigation,
            PR or deployed fix. An unavailable result is unconfirmed reception;
            do not blindly retry an ambiguous transport failure.

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
        """Fingerprint and relay within one deadline; never consult local gates."""
        fp = compute_fingerprint_from_report(
            error_type=error_type,
            error_message=error_message,
            call_site=call_site,
            traceback_str=traceback_str,
            severity_hint=severity_hint,
        )
        if not self._config.enabled:
            return self._refused(fp.fingerprint, "disabled")
        try:
            async with asyncio.timeout(2.0):
                return await self._try_qa_relay(
                    fingerprint=fp.fingerprint,
                    exception_type=error_type,
                    call_site=call_site or "",
                    severity=fp.severity,
                    event_summary=error_message[:200],
                    context=context,
                )
        except TimeoutError:
            return self._refused(fp.fingerprint, "relay_timeout")

    def _refused(self, fingerprint: str, reason: str) -> dict:
        messages = {
            "disabled": "Error relay is disabled for this butler",
            "qa_unavailable": "QA reception unavailable through Switchboard",
            "relay_failed": "QA reception was not confirmed",
            "relay_timeout": "QA reception was not confirmed before the relay deadline",
        }
        # Never log error input or provider exception text.
        logger.warning("Error relay refused: butler=%s reason=%s", self._butler_name, reason)
        return {
            "accepted": False,
            "fingerprint": fingerprint,
            "reason": reason,
            "message": messages[reason],
        }

    async def _try_qa_relay(
        self,
        fingerprint: str,
        exception_type: str,
        call_site: str,
        severity: int,
        event_summary: str,
        context: str | None,
    ) -> dict:
        """Use Switchboard MCP; target acceptance alone confirms reception."""
        client = self._switchboard_client
        if client is None or not await self._is_qa_available(client):
            return self._refused(fingerprint, "qa_unavailable")
        try:
            raw = await client.call_tool(
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
            )
        except TimeoutError:
            raise
        except Exception:
            return self._refused(fingerprint, "relay_failed")
        result = _mcp_payload(raw)
        if isinstance(result, dict) and "result" in result:
            result = _mcp_payload(result["result"])
        if not isinstance(result, dict) or result.get("accepted") is not True:
            return self._refused(fingerprint, "relay_failed")
        return {
            "accepted": True,
            "fingerprint": fingerprint,
            "message": "Finding received by QA via Switchboard",
        }

    async def _is_qa_available(self, client: Any) -> bool:
        """Normalize the registry result, preserving its sixty-second TTL."""
        now = time.monotonic()
        if self._qa_available_cache is not None:
            available, cached_at = self._qa_available_cache
            if now - cached_at < _QA_AVAILABILITY_CACHE_TTL:
                return available
        try:
            result = _mcp_payload(await client.call_tool("list_butlers", {}))
            agents = (
                result
                if isinstance(result, list)
                else (
                    result.get("butlers", result.get("agents", []))
                    if isinstance(result, dict)
                    else []
                )
            )
            available = isinstance(agents, list) and any(
                agent.get("name") == _QA_BUTLER_NAME
                if isinstance(agent, dict)
                else agent == _QA_BUTLER_NAME
                for agent in agents
            )
        except Exception:
            available = False
        self._qa_available_cache = (available, now)
        return available

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

    def wire_runtime(
        self,
        spawner: Any,
        repo_root: Path | str,
        switchboard_client: Any = None,
    ) -> None:
        """Keep the public runtime signature; only MCP transport is consumed.

        Spawner/repo_root are compatibility inputs, never dispatch authority.
        """
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


def _mcp_payload(result: Any) -> Any:
    """Decode FastMCP and raw MCP results without treating errors as data."""
    if isinstance(result, (dict, list)):
        return None if isinstance(result, dict) and result.get("error") else result
    if getattr(result, "is_error", False) is True or getattr(result, "isError", False) is True:
        return None
    for field in ("data", "structured_content", "structuredContent"):
        value = getattr(result, field, None)
        if isinstance(value, (dict, list)):
            return _mcp_payload(value)
    content = getattr(result, "content", [])
    if (
        isinstance(content, (list, tuple))
        and len(content) == 1
        and isinstance(getattr(content[0], "text", None), str)
    ):
        try:
            return _mcp_payload(json.loads(content[0].text))
        except (ValueError, TypeError):
            pass
    return None


class LocalSwitchboardClient:
    """Self-healing-only adapter to the already registered local MCP tools."""

    def __init__(self, mcp: Any) -> None:
        self._mcp = mcp

    async def call_tool(self, name: str, arguments: dict) -> Any:
        async with Client(self._mcp) as client:
            return await client.call_tool(name, arguments)
