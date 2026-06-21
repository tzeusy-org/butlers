"""Failover eligibility classifier for model catalog same-tier failover.

This module decides whether a failed model invocation attempt may be retried on
another same-tier candidate from the model catalog.

**Default-closed contract** — the classifier returns ``eligible=False`` unless an
explicit allow-list condition matches.  Unknown failures are suppressed to protect
against duplicate side effects on a retry.

Eligible (pre-tool-call systemic failures):
- Runtime binary missing or unregistered runtime type (``FileNotFoundError``,
  ``ValueError`` with runtime mismatch message)
- Provider/auth failures (``RuntimeError`` with recognized auth/provider message patterns)
- Rate-limit before work starts (``RuntimeError`` with recognized rate-limit message)
- MCP discovery failure before any tool was executed (``MCPToolDiscoveryError``
  when ``tool_calls`` is empty)
- Timeout before any tool call or side-effect-capable output (``TimeoutError``
  when ``tool_calls`` is empty)
- Runtime config errors (``RuntimeError`` with config/unregistered message patterns)

Suppressed (default-closed, any of the following):
- Any captured MCP tool call  — world may have been touched
- Guardrail terminations (``degenerate_tool_loop``, ``tool_call_budget_exceeded``,
  ``token_budget_exceeded``) — intentional runtime terminations
- Business / validation errors (``ValueError``, ``TypeError``, application errors)
- Unknown errors — cannot confirm no side effect occurred
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FailoverDecision:
    """Result of a failover eligibility classification.

    Attributes
    ----------
    eligible:
        True when the failed attempt may be retried on a different same-tier
        model.  False (default-closed) whenever uncertainty exists.
    reason:
        Human-readable explanation for the decision.  Suitable for operator
        logs and provenance records; must not contain secrets or PII.
    """

    eligible: bool
    reason: str


# ---------------------------------------------------------------------------
# Eligibility allow-list — message pattern matching
# ---------------------------------------------------------------------------

# Substrings matched (lowercased) against the exception message to detect
# provider/auth failures that are systemic and pre-invocation.  These indicate
# the provider rejected the request before any user work was attempted.
_PROVIDER_AUTH_MARKERS: tuple[str, ...] = (
    # Authentication / credential failures
    "authentication",
    "auth failed",
    "auth error",
    "unauthorized",
    "invalid api key",
    "api key",
    "credential",
    "token expired",
    "token invalid",
    "permission denied",
    "access denied",
    "forbidden",
    # Provider / model availability
    "model not found",
    "model unavailable",
    "model is unavailable",
    "provider unavailable",
    "provider error",
    "service unavailable",
    "backend unavailable",
    "no such model",
    "apierror",
    "api error",
    # OpenCode-specific structured errors (exit 0 with stderr)
    "providermodelnotfounderror",
    "model not found:",
    # Connection-level failures before work starts
    "connection refused",
    "connection reset",
    "connection timed out",
    "failed to connect",
    "network error",
    "network unreachable",
    "name or service not known",
    "temporary failure",
)

# Substrings matched (lowercased) against the exception message to detect
# rate-limit rejections before any work was performed.
_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "quota exceeded",
    "requests per minute",
    "requests per second",
    "tokens per minute",
    "model is at capacity",
    "overloaded",
    "retry after",
    "backoff",
    "throttl",
    # Codex-specific transient backend failures — the adapter retries these
    # internally; when all internal retries are exhausted, failover to a
    # different same-tier model is the correct response.
    "compact_remote",
    "remote compaction failed",
    # Codex / ChatGPT plan usage-cap exhaustion (5h or weekly limit).  The CLI
    # exits 1 before any tool call with "You've hit your usage limit." — a
    # pre-invocation systemic rejection, so failover to a same-tier non-codex
    # model (e.g. opencode) is the correct response.
    "usage limit",
    "hit your usage limit",
    # Provider account billing / credit exhaustion. These are account-state
    # rejections before any model work starts, equivalent to quota exhaustion
    # for failover purposes.
    "insufficient balance",
    "insufficient credits",
    "credit balance",
    "out of credits",
    "balance exhausted",
    "billing limit reached",
    "credit limit reached",
)

# Substrings matched (lowercased) against the exception message to detect a
# runtime/provider process that exited successfully but produced no usable
# output, tool calls, usage, or stderr. With no tool calls (Gate 1), this is a
# pre-work systemic failure and should be eligible for same-tier failover.
_EMPTY_RESPONSE_MARKERS: tuple[str, ...] = (
    "no response",
    "empty response",
    "no result, tool calls, token usage, or stderr",
)

# Substrings matched (lowercased) against the exception message to detect
# MCP discovery / transport failures that are pre-invocation.
_MCP_DISCOVERY_MARKERS: tuple[str, ...] = (
    "mcp tool discovery failed",
    "mcp discovery failed",
    "mcp connection failed",
    "failed to start mcp",
    "mcp transport",
    "rmcp",
    "streamable_http",
    "method not allowed",
    "unsupported media type",
)

# Substrings matched (lowercased) against the exception message to detect
# runtime config/registration problems — systemic before invocation.
_RUNTIME_CONFIG_MARKERS: tuple[str, ...] = (
    "unknown runtime type",
    "unregistered runtime",
    "runtime type",
    "invalid runtime",
    "malformed cli config",
    "missing cli",
    "cli not found",
    "cli binary",
)

# Substrings matched (lowercased) against the exception message to identify
# guardrail terminations.  These SUPPRESS failover — they are intentional.
_GUARDRAIL_MARKERS: tuple[str, ...] = (
    "degenerate_tool_loop",
    "tool_call_budget_exceeded",
    "token_budget_exceeded",
    "guardrail",
    "budget exceeded",
    "tool call budget",
    "token budget exceeded",
    "degenerate loop",
)


# ---------------------------------------------------------------------------
# Classification context dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class FailoverContext:
    """Inputs to the failover eligibility decision.

    Parameters
    ----------
    exception:
        The exception raised by the failing adapter invocation.  Required.
    tool_calls:
        List of MCP tool-call records captured before or during the failed
        invocation.  Any non-empty list suppresses failover.
    process_info:
        Dict of adapter/process metadata (exit_code, stderr, runtime_type,
        etc.) as returned by ``runtime.last_process_info``.  Optional.
    trigger_context:
        Optional free-form trigger metadata.  Reserved for future classifiers;
        not currently used for eligibility decisions.
    """

    exception: BaseException
    tool_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    process_info: dict[str, Any] | None = None
    trigger_context: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def classify_failover_eligibility(ctx: FailoverContext) -> FailoverDecision:
    """Classify whether a failed model attempt is eligible for failover.

    The decision is default-closed: ``eligible=False`` is returned whenever no
    explicit allow-list condition matches.  This protects against unknown errors
    causing duplicate side effects on a retry.

    Parameters
    ----------
    ctx:
        Full context for the failed attempt.  See :class:`FailoverContext`.

    Returns
    -------
    FailoverDecision
        ``eligible=True`` only when the failure is confirmed to be a systemic,
        pre-invocation error with no captured tool calls.
    """
    exc = ctx.exception
    tool_calls = ctx.tool_calls or []

    # ------------------------------------------------------------------
    # GATE 1: Captured tool calls — world may have been touched.
    # This check runs first to ensure the side-effect gate is never skipped
    # regardless of the exception class.
    # ------------------------------------------------------------------
    if tool_calls:
        logger.debug(
            "Failover suppressed: %d captured tool call(s) — world may have been touched",
            len(tool_calls),
        )
        return FailoverDecision(
            eligible=False,
            reason=f"captured_tool_calls: {len(tool_calls)} tool call(s) recorded; "
            "retry suppressed to prevent duplicate side effects",
        )

    exc_msg = str(exc).lower()
    exc_class = type(exc).__name__

    # ------------------------------------------------------------------
    # GATE 2: Guardrail terminations — intentional, not a systemic failure.
    # Must be checked before the RuntimeError allow-list below.
    # ------------------------------------------------------------------
    if _matches_any(exc_msg, _GUARDRAIL_MARKERS):
        logger.debug("Failover suppressed: guardrail termination detected (exc=%s)", exc_class)
        return FailoverDecision(
            eligible=False,
            reason=f"guardrail_termination: {exc_class} matched guardrail marker; "
            "intentional session termination is not failover-eligible",
        )

    # ------------------------------------------------------------------
    # GATE 3: MCPToolDiscoveryError — eligible only when tool_calls is empty
    # (already verified by GATE 1 above).
    # ------------------------------------------------------------------
    if _is_mcp_tool_discovery_error(exc):
        logger.debug("Failover eligible: MCPToolDiscoveryError with no captured tool calls")
        return FailoverDecision(
            eligible=True,
            reason="mcp_discovery_failure: MCP tool discovery failed before any tool "
            "was executed; systemic pre-invocation failure",
        )

    # ------------------------------------------------------------------
    # GATE 4: FileNotFoundError — CLI binary missing or unregistered adapter.
    # ------------------------------------------------------------------
    if isinstance(exc, FileNotFoundError):
        logger.debug("Failover eligible: FileNotFoundError (missing CLI binary)")
        return FailoverDecision(
            eligible=True,
            reason=f"missing_cli_binary: {exc_class} — runtime binary not found; "
            "systemic infrastructure failure before invocation",
        )

    # ------------------------------------------------------------------
    # GATE 5: TimeoutError — eligible only without tool calls (already clear
    # from GATE 1) because the timeout fired before work could start.
    # ------------------------------------------------------------------
    if isinstance(exc, TimeoutError):
        logger.debug("Failover eligible: TimeoutError with no captured tool calls")
        return FailoverDecision(
            eligible=True,
            reason="timeout_before_work: TimeoutError with no captured tool calls; "
            "timeout fired before any side effect was observable",
        )

    # ------------------------------------------------------------------
    # GATE 6: RuntimeError with recognized systemic patterns.
    # Runtime errors produced by adapter invocations carry structured
    # message detail that identifies the failure class.
    # ------------------------------------------------------------------
    if isinstance(exc, RuntimeError):
        # Runtime config / registration errors
        if _matches_any(exc_msg, _RUNTIME_CONFIG_MARKERS):
            logger.debug("Failover eligible: RuntimeError — runtime config/registration error")
            return FailoverDecision(
                eligible=True,
                reason="runtime_config_error: runtime configuration or registration "
                "failure before invocation",
            )

        # Rate-limit / quota / billing exhaustion before work. Check this before
        # generic provider/auth markers because structured OpenCode APIError
        # messages can include both "APIError" and a more specific quota marker.
        if _matches_any(exc_msg, _RATE_LIMIT_MARKERS):
            logger.debug("Failover eligible: RuntimeError — rate-limit before work")
            return FailoverDecision(
                eligible=True,
                reason="rate_limit_before_work: provider rate-limit rejection "
                "before any tool call was executed",
            )

        # Provider / auth failures
        if _matches_any(exc_msg, _PROVIDER_AUTH_MARKERS):
            logger.debug("Failover eligible: RuntimeError — provider/auth failure")
            return FailoverDecision(
                eligible=True,
                reason="provider_auth_error: provider or authentication failure "
                "before session work started",
            )

        # Empty runtime/provider response before work
        if _matches_any(exc_msg, _EMPTY_RESPONSE_MARKERS):
            logger.debug("Failover eligible: RuntimeError — empty runtime response")
            return FailoverDecision(
                eligible=True,
                reason="empty_runtime_response: runtime returned no usable output "
                "before any tool call was executed",
            )

        # MCP discovery patterns embedded in a RuntimeError message
        if _matches_any(exc_msg, _MCP_DISCOVERY_MARKERS):
            logger.debug("Failover eligible: RuntimeError — MCP discovery/transport failure")
            return FailoverDecision(
                eligible=True,
                reason="mcp_discovery_failure: MCP transport/discovery failure "
                "detected in RuntimeError message",
            )

        # Unmatched RuntimeError — default closed
        logger.debug(
            "Failover suppressed: RuntimeError with unrecognized message pattern (exc=%s)",
            exc_class,
        )
        return FailoverDecision(
            eligible=False,
            reason=f"unknown_runtime_error: {exc_class} did not match any "
            "failover-eligible pattern; default-closed",
        )

    # ------------------------------------------------------------------
    # GATE 7: ValueError — covers unregistered runtime type (raised by
    # base adapter factory) when the message matches a config marker.
    # All other ValueError instances suppress failover (business/validation).
    # ------------------------------------------------------------------
    if isinstance(exc, ValueError):
        if _matches_any(exc_msg, _RUNTIME_CONFIG_MARKERS):
            logger.debug("Failover eligible: ValueError — unregistered runtime type")
            return FailoverDecision(
                eligible=True,
                reason="runtime_config_error: ValueError matched runtime "
                "registration pattern; unregistered runtime type",
            )

        logger.debug(
            "Failover suppressed: ValueError — business/validation error (exc=%s)", exc_class
        )
        return FailoverDecision(
            eligible=False,
            reason=f"business_validation_error: {exc_class} — validation or "
            "business-logic failure; not a systemic infrastructure error",
        )

    # ------------------------------------------------------------------
    # DEFAULT: Unknown exception class — default-closed.
    # ------------------------------------------------------------------
    logger.debug("Failover suppressed: unknown exception class %s (default-closed)", exc_class)
    return FailoverDecision(
        eligible=False,
        reason=f"unknown_error: {exc_class} is not a recognized failover-eligible "
        "exception class; default-closed to prevent unknown side effects",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _matches_any(text: str, markers: tuple[str, ...]) -> bool:
    """Return True when any marker substring appears in text (already lowercased)."""
    return any(marker in text for marker in markers)


def _is_mcp_tool_discovery_error(exc: BaseException) -> bool:
    """Return True for MCPToolDiscoveryError instances.

    Uses class-name matching to avoid a hard import dependency on the Codex
    adapter from this module, keeping the classifier adapter-agnostic.  The
    ``MCPToolDiscoveryError`` class is a ``RuntimeError`` subclass defined in
    ``butlers.core.runtimes.codex``.
    """
    return type(exc).__name__ == "MCPToolDiscoveryError"
