"""Failover eligibility classifier for model catalog same-tier failover.

This module decides whether a failed model invocation attempt may be retried on
another same-tier candidate from the model catalog.

**Default-closed contract** — the classifier returns ``eligible=False`` unless an
explicit allow-list condition matches.  Unknown failures are suppressed to protect
against duplicate side effects on a retry.

Eligible (pre-tool-call systemic failures):
- Runtime binary missing or unregistered runtime type (``FileNotFoundError``,
  ``ValueError`` with runtime mismatch message)
- Provider/auth failures (``RuntimeError`` with recognized auth/credential message
  patterns) — reason prefix ``provider_auth_error``
- Provider/backend availability failures (``RuntimeError`` with recognized
  connectivity/service message patterns, e.g. connection refused, service
  unavailable, bad gateway) — reason prefix ``provider_unavailable``.  Split from
  the auth bucket (bu-ujm9d) so a network blip is not misattributed as an
  identity/credential rejection; eligibility is identical to the auth bucket,
  only the reason label differs.
- OpenCode CLI ``APIError`` payloads flagged by the adapter as pre-tool-call
- Rate-limit before work starts (``RuntimeError`` with recognized rate-limit message)
- MCP discovery failure before any tool was executed (``MCPToolDiscoveryError``
  when ``tool_calls`` is empty)
- Timeout before any tool call or side-effect-capable output (``TimeoutError``
  when ``tool_calls`` is empty)
- Runtime config errors (``RuntimeError`` with config/unregistered message patterns)
- Bounded opt-in stderr gate (bu-hmdqz.2): when the exception message itself
  matches nothing above but the adapter explicitly reported
  ``process_info["is_pre_tool_call"] is True`` and its ``error_detail``/
  ``stderr`` text matches an auth/availability/rate-limit marker, the failure
  is still eligible — reused vocabulary, corroborating adapter signal
  required, default-closed on any missing piece. See ``_classify_stderr_gate``.

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
# genuine provider/auth failures that are systemic and pre-invocation — the
# provider rejected the request because of who is asking (bad/missing/expired
# credential, insufficient scope), not because the provider itself is down.
# This bucket feeds discretion's auth-health surface (bu-ur7go) and the
# ``auth_failure_default`` ignore-kind (bu-n0336), so it must stay narrow: a
# network blip here would falsely paint a healthy-but-unreachable provider as
# an auth problem (bu-ujm9d).
#
# ``forbidden`` WAF/CDN audit (bu-0n2wk, 2026-07-10 vs the live dev DB): the
# concern was that a provider-side WAF/CDN block (e.g. "403 Forbidden by
# security policy") would misclassify as a genuine auth failure because no
# rate-limit or availability marker catches it first. Audited every LLM-spawn
# exception the classifier actually sees — the ``error`` column across all
# butler ``sessions`` tables (~2,300 failure rows): zero rows contain
# ``forbidden``, ``security policy``, ``access denied``, or ``denied by``. The
# HTTP-403 traffic that DOES exist in the fleet lives only in connector tables
# (Telegram Bot API "403 Forbidden: bots can't send messages to bots", the
# ``api_forbidden`` connector-heartbeat status, Pydantic ``extra_forbidden``
# validation) — none of which is a model-CLI spawn exception, so none of it
# ever reaches this classifier. Where an LLM provider itself does return 403 it
# is semantically an auth/permission rejection (Anthropic ``PermissionDenied``,
# OpenAI region/permission 403), so the auth bucket is its correct home.
# Decision: keep ``forbidden`` here (option a) — no availability sub-marker is
# added for traffic that does not exist, which would only risk the disjointness
# invariant below. Guarded by ``test_forbidden_marker_*`` and the marker-bucket
# disjointness test in tests/core/test_failover_classifier.py.
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
    # A raw "403 Forbidden" from an LLM *provider* is an auth/permission
    # rejection, not availability. Audited zero real-world provider-side WAF
    # "403 Forbidden by security policy" hits (bu-0n2wk); see bucket comment.
    "forbidden",
    # OAuth refresh-token revocation (bu-hmdqz.2, live-confirmed session
    # b03d3af4): a revoked Codex ChatGPT OAuth token surfaces as "RuntimeError:
    # Codex CLI exited with code 1: Your access token could not be refreshed
    # because your refresh token was revoked. Please log out and sign in
    # again." — none of the markers above matched, so the failure default-
    # closed and silently killed the workhorse/reasoning/specialty tiers for
    # hours behind a green Models tab. These three markers are OAuth-specific
    # phrasing (not generic English words), so the false-positive risk against
    # business/validation error text is low.
    "refresh token",
    "token could not be refreshed",
    "log out and sign in",
)

# Substrings matched (lowercased) against the exception message to detect
# provider/backend *availability* failures that are systemic and
# pre-invocation — the provider (or the network path to it) is unreachable or
# erroring, independent of whether the caller's credentials are valid.  Split
# out from ``_PROVIDER_AUTH_MARKERS`` (bu-ujm9d): a 502 from provider A says
# nothing about whether provider A's or provider B's credentials are good, so
# conflating "can't reach the provider" with "the provider rejected our
# identity" mislabeled every connectivity blip as an auth failure and
# polluted discretion's auth-health metric and the ``auth_failure_default``
# ignore-kind taxonomy with false positives. Failover *eligibility* is
# unchanged by this split — both buckets remain eligible — only the reason
# label differs (``provider_auth_error`` vs ``provider_unavailable``).
_PROVIDER_AVAILABILITY_MARKERS: tuple[str, ...] = (
    # Provider / model availability
    "model not found",
    "model unavailable",
    "model is unavailable",
    "provider unavailable",
    "provider error",
    "provider request failed",
    "service unavailable",
    "backend unavailable",
    "no such model",
    "api error",
    # Anthropic Messages API (ApiAdapter, bu-qvnce.12): the SDK's own
    # APIStatusError embeds a JSON error envelope whose `error.type` field
    # uses underscore-joined identifiers (e.g. "api_error" for a generic 5xx,
    # "overloaded_error" for 529) rather than the space-joined phrases above.
    # These are pre-invocation, systemic provider failures — eligible.
    "api_error",
    "internal server error",
    "bad gateway",
    "gateway timeout",
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
    # Anthropic SDK's own APIConnectionError hardcodes this exact message
    # ("Connection error.") for ANY network-level failure (DNS, dropped
    # connection, TLS handshake failure, refused connection, etc.) — none
    # of the more specific connection markers above match that literal text.
    "connection error",
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
# output or tool calls. Token usage or benign stderr can describe an attempt,
# but neither is a usable session result. With no tool calls (Gate 1), this is
# a pre-work systemic failure and should be eligible for same-tier failover.
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
                reason="rate_limit_before_work: provider rate-limit, quota, or billing rejection "
                "before any tool call was executed",
            )

        # OpenCode may surface provider-side failures as a structured
        # ``APIError`` envelope that has no stable vendor-specific wording.
        # Trust it only when the adapter explicitly marked the failure as
        # pre-tool-call and Gate 1 saw no captured MCP calls.
        if _is_opencode_pre_tool_call_api_error(ctx, exc_msg):
            logger.debug("Failover eligible: RuntimeError — OpenCode pre-tool-call APIError")
            return FailoverDecision(
                eligible=True,
                reason="provider_api_error: OpenCode APIError before session work started",
            )

        # Provider / auth failures — genuine identity/credential rejections.
        if _matches_any(exc_msg, _PROVIDER_AUTH_MARKERS):
            logger.debug("Failover eligible: RuntimeError — provider/auth failure")
            return FailoverDecision(
                eligible=True,
                reason="provider_auth_error: provider or authentication failure "
                "before session work started",
            )

        # Provider / backend availability failures — connectivity or service
        # errors independent of credential validity. Same eligibility as the
        # auth bucket above (bu-ujm9d): distinct reason label only, so callers
        # keying off "provider_auth_error" (discretion auth-health, the
        # auth_failure_default ignore-kind) do not misattribute a network
        # blip as an auth problem.
        if _matches_any(exc_msg, _PROVIDER_AVAILABILITY_MARKERS):
            logger.debug("Failover eligible: RuntimeError — provider/backend availability failure")
            return FailoverDecision(
                eligible=True,
                reason="provider_unavailable: provider or backend availability failure "
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

        # GATE 6b: bounded opt-in stderr-matching gate (bu-hmdqz.2). The
        # exception message itself matched nothing above, but the adapter's
        # own stderr/error_detail may carry an unambiguous systemic-failure
        # marker the CLI's stdout summary line dropped (live-confirmed:
        # process_info stderr contained "401 Unauthorized" for a revoked-
        # token failure whose RuntimeError message did not). See
        # ``_classify_stderr_gate`` for the opt-in contract.
        stderr_decision = _classify_stderr_gate(ctx.process_info)
        if stderr_decision is not None:
            logger.debug(
                "Failover eligible: RuntimeError — stderr gate matched (exc=%s)", exc_class
            )
            return stderr_decision

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
    # DEFAULT: Unknown exception class — try the stderr gate before closing.
    # ------------------------------------------------------------------
    stderr_decision = _classify_stderr_gate(ctx.process_info)
    if stderr_decision is not None:
        logger.debug(
            "Failover eligible: unknown exception class %s — stderr gate matched", exc_class
        )
        return stderr_decision

    logger.debug("Failover suppressed: unknown exception class %s (default-closed)", exc_class)
    return FailoverDecision(
        eligible=False,
        reason=f"unknown_error: {exc_class} is not a recognized failover-eligible "
        "exception class; default-closed to prevent unknown side effects",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_provider_auth_marker(text: str | None) -> bool:
    """Return whether *text* contains a provider auth/credential-failure marker.

    Public wrapper around the same ``_PROVIDER_AUTH_MARKERS`` vocabulary the
    classifier itself uses (case-insensitive substring match), so callers
    outside the failover hot path — e.g. the QA dashboard's watcher-death
    verdict clause (bu-hmdqz.9), which reads ``model_dispatch_attempts``
    failure text after the fact — can recognize the same OAuth-revocation /
    credential phrasing without duplicating or drifting from the marker list.
    """
    if not text:
        return False
    return _matches_any(text.lower(), _PROVIDER_AUTH_MARKERS)


def _matches_any(text: str, markers: tuple[str, ...]) -> bool:
    """Return True when any marker substring appears in text (already lowercased)."""
    return any(marker in text for marker in markers)


# ---------------------------------------------------------------------------
# Bounded opt-in stderr-matching gate (bu-hmdqz.2)
# ---------------------------------------------------------------------------
#
# Reuses the exact same marker buckets consulted against the exception
# message — no new vocabulary, no new eligibility class — so a stderr match
# carries the identical reason-prefix taxonomy (``provider_auth_error`` /
# ``provider_unavailable`` / ``rate_limit_before_work``) that discretion's
# auth-health surface and the ``auth_failure_default`` ignore-kind already key
# off of. Only the *source* of the matched text differs (adapter stderr /
# error_detail instead of ``str(exc)``).
#
# Default-closed contract preserved: this gate requires BOTH of:
#   1. ``process_info.get("is_pre_tool_call") is True`` — an explicit,
#      adapter-supplied signal (not inferred from the absence of markers)
#      that the failure happened before any tool call could have run. Gate 1
#      (captured tool calls) already ran by the time any caller reaches this
#      helper, so this is corroborating, not substituting, evidence.
#   2. A marker match against ``error_detail`` (preferred) or ``stderr``.
# Either condition failing falls through to the caller's own default-closed
# return — this helper never turns "no signal" into "eligible".
_STDERR_GATE_BUCKETS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("provider_auth_error", _PROVIDER_AUTH_MARKERS, "provider or authentication failure"),
    (
        "provider_unavailable",
        _PROVIDER_AVAILABILITY_MARKERS,
        "provider or backend availability failure",
    ),
    (
        "rate_limit_before_work",
        _RATE_LIMIT_MARKERS,
        "provider rate-limit, quota, or billing rejection",
    ),
)


def _stderr_gate_text(process_info: dict[str, Any]) -> str:
    """Return the lowercased text to match, preferring ``error_detail`` over ``stderr``.

    ``error_detail`` is the adapter's own structured extraction (see
    ``RuntimeAdapter.last_process_info`` docstring: "preferred over raw
    stderr for classifier matching"); raw ``stderr`` is the fallback for
    adapters that do not populate ``error_detail``.
    """
    for key in ("error_detail", "stderr"):
        value = process_info.get(key)
        if isinstance(value, str) and value.strip():
            return value.lower()
    return ""


def _classify_stderr_gate(process_info: dict[str, Any] | None) -> FailoverDecision | None:
    """Bounded opt-in stderr-matching gate for pre-tool-call systemic failures.

    Returns ``None`` (defer to the caller's own default-closed return) unless
    the adapter explicitly marked the failure ``is_pre_tool_call=True`` AND
    the stderr/error_detail text matches one of the systemic-failure marker
    buckets. See the module-level comment above for the full contract.
    """
    if not isinstance(process_info, dict) or process_info.get("is_pre_tool_call") is not True:
        return None
    text = _stderr_gate_text(process_info)
    if not text:
        return None
    for reason_prefix, markers, description in _STDERR_GATE_BUCKETS:
        if _matches_any(text, markers):
            return FailoverDecision(
                eligible=True,
                reason=f"{reason_prefix}: {description} detected in adapter stderr "
                "(opt-in pre-tool-call stderr gate; process_info.is_pre_tool_call=True)",
            )
    return None


def _is_opencode_pre_tool_call_api_error(ctx: FailoverContext, exc_msg: str) -> bool:
    """Return True for OpenCode APIError envelopes raised before any tool work."""
    process_info = ctx.process_info or {}
    return (
        process_info.get("runtime_type") == "opencode"
        and process_info.get("is_pre_tool_call") is True
        and "apierror" in exc_msg
    )


def _is_mcp_tool_discovery_error(exc: BaseException) -> bool:
    """Return True for MCPToolDiscoveryError instances.

    Uses class-name matching to avoid a hard import dependency on the Codex
    adapter from this module, keeping the classifier adapter-agnostic.  The
    ``MCPToolDiscoveryError`` class is a ``RuntimeError`` subclass defined in
    ``butlers.core.runtimes.codex``.
    """
    return type(exc).__name__ == "MCPToolDiscoveryError"
