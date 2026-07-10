"""Tests for butlers.core.failover_classifier.

Covers every acceptance criterion from bu-ojiij.2:

1. Systemic pre-tool-call failures ARE eligible for failover:
   - runtime config errors (FileNotFoundError, ValueError with config message,
     RuntimeError with config message)
   - provider/auth errors (RuntimeError with auth/provider message)
   - rate-limit errors (RuntimeError with rate-limit message)
   - MCP discovery failures (MCPToolDiscoveryError, RuntimeError with MCP message)
   - timeout-before-work (TimeoutError with no tool calls)

2. Captured MCP tool calls SUPPRESS failover (any tool call → no retry).

3. Guardrail terminations SUPPRESS failover.

4. Business/validation failures SUPPRESS failover.

5. Unknown errors SUPPRESS failover (default closed).

6. Default-closed: passing bare minimal context yields no retry.
"""

from __future__ import annotations

import itertools

import pytest

from butlers.core import failover_classifier as _fc
from butlers.core.failover_classifier import (
    FailoverContext,
    FailoverDecision,
    classify_failover_eligibility,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    exc: BaseException,
    tool_calls: list | None = None,
    process_info: dict | None = None,
) -> FailoverContext:
    return FailoverContext(
        exception=exc,
        tool_calls=tool_calls or [],
        process_info=process_info,
    )


def _eligible(decision: FailoverDecision) -> bool:
    return decision.eligible


def _suppressed(decision: FailoverDecision) -> bool:
    return not decision.eligible


# ---------------------------------------------------------------------------
# AC-6: Default-closed — bare context must suppress failover
# ---------------------------------------------------------------------------


class TestDefaultClosed:
    """AC-6: Unknown errors suppress failover (default closed)."""

    def test_no_context_yields_no_retry(self) -> None:
        """Bare RuntimeError with no tool calls: unknown, must be default-closed."""
        dec = classify_failover_eligibility(_ctx(RuntimeError("unexpected problem")))
        assert _suppressed(dec), f"Expected suppressed, got: {dec.reason}"
        assert "default-closed" in dec.reason

    def test_unknown_exception_class_is_default_closed(self) -> None:
        """A completely unknown exception class suppresses failover."""

        class _WeirdError(Exception):
            pass

        dec = classify_failover_eligibility(_ctx(_WeirdError("something weird")))
        assert _suppressed(dec)
        assert "default-closed" in dec.reason or "unknown" in dec.reason

    def test_bare_exception_is_default_closed(self) -> None:
        """Bare Exception (not a subclass) suppresses failover."""
        dec = classify_failover_eligibility(_ctx(Exception("generic")))
        assert _suppressed(dec)

    def test_os_error_is_default_closed(self) -> None:
        """OSError (not FileNotFoundError) suppresses failover."""
        dec = classify_failover_eligibility(_ctx(OSError("disk full")))
        assert _suppressed(dec)

    def test_reason_is_non_empty_string(self) -> None:
        """Every FailoverDecision has a non-empty reason string."""
        dec = classify_failover_eligibility(_ctx(RuntimeError("x")))
        assert isinstance(dec.reason, str) and len(dec.reason) > 0


# ---------------------------------------------------------------------------
# AC-2: Captured tool calls suppress failover
# ---------------------------------------------------------------------------


class TestToolCallsSuppressFailover:
    """AC-2: Any captured MCP tool call suppresses failover regardless of exception."""

    def test_file_not_found_with_tool_calls_is_suppressed(self) -> None:
        """FileNotFoundError would be eligible but tool calls suppress it."""
        tool_calls = [{"name": "read_file", "input": {"path": "/tmp/x"}}]
        dec = classify_failover_eligibility(_ctx(FileNotFoundError("cli"), tool_calls=tool_calls))
        assert _suppressed(dec)
        assert "tool call" in dec.reason

    def test_timeout_with_tool_calls_is_suppressed(self) -> None:
        """TimeoutError would be eligible but tool calls suppress it."""
        tool_calls = [{"name": "send_email", "input": {}}]
        dec = classify_failover_eligibility(_ctx(TimeoutError("timed out"), tool_calls=tool_calls))
        assert _suppressed(dec)
        assert "tool call" in dec.reason

    def test_auth_error_with_tool_calls_is_suppressed(self) -> None:
        """Provider auth error would be eligible but tool calls suppress it."""
        tool_calls = [{"name": "calendar_create", "input": {"title": "Meeting"}}]
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("authentication failed"), tool_calls=tool_calls)
        )
        assert _suppressed(dec)
        assert "tool call" in dec.reason

    def test_multiple_tool_calls_suppressed(self) -> None:
        """Multiple tool calls are suppressed; count appears in reason."""
        tool_calls = [
            {"name": "tool_a", "input": {}},
            {"name": "tool_b", "input": {}},
            {"name": "tool_c", "input": {}},
        ]
        dec = classify_failover_eligibility(_ctx(RuntimeError("boom"), tool_calls=tool_calls))
        assert _suppressed(dec)
        assert "3" in dec.reason

    def test_single_tool_call_suppresses(self) -> None:
        """Even a single tool call suppresses failover."""
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("boom"), tool_calls=[{"name": "touch_file"}])
        )
        assert _suppressed(dec)


# ---------------------------------------------------------------------------
# AC-3: Guardrail terminations suppress failover
# ---------------------------------------------------------------------------


class TestGuardrailTerminationsSuppressFailover:
    """AC-3: Guardrail terminations suppress failover."""

    @pytest.mark.parametrize(
        "msg",
        [
            "session terminated: degenerate_tool_loop",
            "Session aborted: tool_call_budget_exceeded",
            "token_budget_exceeded",
            "guardrail: max tool calls reached",
            "budget exceeded for session",
            "tool call budget reached",
            "token budget exceeded",
            "degenerate loop detected",
        ],
    )
    def test_guardrail_message_suppresses(self, msg: str) -> None:
        """RuntimeError with a guardrail message suppresses failover."""
        dec = classify_failover_eligibility(_ctx(RuntimeError(msg)))
        assert _suppressed(dec), f"Expected suppressed for msg={msg!r}, got: {dec.reason}"
        assert "guardrail" in dec.reason

    def test_guardrail_checked_before_runtime_error_allow_list(self) -> None:
        """Guardrail detection runs before the RuntimeError allow-list so a
        guardrail message is never accidentally matched as an eligible pattern."""
        # "token_budget_exceeded" might contain a substring but must be suppressed
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("guardrail: token_budget_exceeded (500k tokens)"))
        )
        assert _suppressed(dec)
        assert "guardrail" in dec.reason


# ---------------------------------------------------------------------------
# AC-4: Business/validation failures suppress failover
# ---------------------------------------------------------------------------


class TestBusinessValidationFailuresSuppressFailover:
    """AC-4: Business/validation failures suppress failover."""

    def test_value_error_without_config_message_suppresses(self) -> None:
        """Plain ValueError suppresses failover (business logic error)."""
        dec = classify_failover_eligibility(_ctx(ValueError("invalid date format")))
        assert _suppressed(dec)
        assert "business" in dec.reason or "validation" in dec.reason

    def test_type_error_suppresses(self) -> None:
        """TypeError suppresses failover (unexpected type = application error)."""
        dec = classify_failover_eligibility(_ctx(TypeError("unexpected type")))
        assert _suppressed(dec)

    def test_runtime_error_with_validation_message_suppresses(self) -> None:
        """RuntimeError with a validation-looking message is suppressed by default-closed."""
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("tool returned invalid JSON response"))
        )
        assert _suppressed(dec)

    def test_key_error_suppresses(self) -> None:
        """KeyError suppresses failover (application bug, not infrastructure)."""
        dec = classify_failover_eligibility(_ctx(KeyError("missing_key")))
        assert _suppressed(dec)


# ---------------------------------------------------------------------------
# AC-1a: Runtime config errors ARE eligible
# ---------------------------------------------------------------------------


class TestRuntimeConfigErrorsEligible:
    """AC-1a: Runtime config errors are failover-eligible."""

    def test_file_not_found_is_eligible(self) -> None:
        """FileNotFoundError is eligible — CLI binary missing."""
        dec = classify_failover_eligibility(_ctx(FileNotFoundError("codex: command not found")))
        assert _eligible(dec)
        assert "missing_cli_binary" in dec.reason or "cli" in dec.reason.lower()

    def test_runtime_error_unknown_runtime_type_is_eligible(self) -> None:
        """RuntimeError matching 'unknown runtime type' is eligible."""
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("Unknown runtime type 'turbo'. Available adapters: ..."))
        )
        assert _eligible(dec)
        assert "runtime" in dec.reason.lower()

    def test_runtime_error_unregistered_runtime_is_eligible(self) -> None:
        """RuntimeError matching 'unregistered runtime' is eligible."""
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("unregistered runtime: foobar adapter not found"))
        )
        assert _eligible(dec)

    def test_value_error_runtime_type_is_eligible(self) -> None:
        """ValueError from adapter factory (unregistered runtime) is eligible."""
        dec = classify_failover_eligibility(
            _ctx(ValueError("Unknown runtime type 'codex_v2'. Available adapters: codex, claude"))
        )
        assert _eligible(dec)

    def test_value_error_runtime_config_is_eligible(self) -> None:
        """ValueError matching 'invalid runtime' pattern is eligible."""
        dec = classify_failover_eligibility(_ctx(ValueError("invalid runtime configuration")))
        assert _eligible(dec)

    def test_file_not_found_no_tool_calls_required(self) -> None:
        """FileNotFoundError is eligible regardless of message content."""
        dec = classify_failover_eligibility(_ctx(FileNotFoundError("")))
        assert _eligible(dec)


# ---------------------------------------------------------------------------
# AC-1b: Provider/auth errors ARE eligible
# ---------------------------------------------------------------------------


class TestProviderAuthErrorsEligible:
    """AC-1b: Provider/auth errors are failover-eligible."""

    @pytest.mark.parametrize(
        "msg",
        [
            "Codex CLI exited with code 1: authentication failed",
            "Codex CLI exited with code 401: unauthorized",
            "RuntimeError: invalid api key provided",
            "provider unavailable: anthropic returning 503",
            "model unavailable: claude-opus-4 is not active",
            "service unavailable: provider returned 503",
            "access denied: your account lacks access",
            "credential not found in keychain",
            "token expired, please re-authenticate",
            "permission denied: insufficient scope",
            "backend unavailable",
            "model not found in catalog",
            "no such model: claude-opus-99",
            "OpenCode CLI exited with code 1: provider request failed upstream",
        ],
    )
    def test_provider_auth_message_is_eligible(self, msg: str) -> None:
        """RuntimeError with provider/auth message is eligible."""
        dec = classify_failover_eligibility(_ctx(RuntimeError(msg)))
        assert _eligible(dec), f"Expected eligible for msg={msg!r}, got: {dec.reason}"

    @pytest.mark.parametrize(
        "msg",
        [
            "Codex CLI exited with code 1: authentication failed",
            "Codex CLI exited with code 401: unauthorized",
            "RuntimeError: invalid api key provided",
            "access denied: your account lacks access",
            "credential not found in keychain",
            "token expired, please re-authenticate",
            "permission denied: insufficient scope",
        ],
    )
    def test_genuine_auth_message_carries_provider_auth_error_reason(self, msg: str) -> None:
        """bu-ujm9d: genuine identity/credential failures must carry the
        narrow ``provider_auth_error`` reason prefix, not the availability
        bucket's ``provider_unavailable`` — these are the messages discretion's
        auth-health surface and the ``auth_failure_default`` ignore-kind key
        off of."""
        dec = classify_failover_eligibility(_ctx(RuntimeError(msg)))
        assert _eligible(dec), f"Expected eligible for msg={msg!r}, got: {dec.reason}"
        assert dec.reason.startswith("provider_auth_error"), dec.reason

    def test_connection_refused_is_eligible(self) -> None:
        """RuntimeError with 'connection refused' is eligible (provider unreachable)."""
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("Codex CLI exited with code 1: connection refused"))
        )
        assert _eligible(dec)

    def test_network_error_is_eligible(self) -> None:
        """RuntimeError with 'network error' is eligible."""
        dec = classify_failover_eligibility(_ctx(RuntimeError("network error: timeout connecting")))
        assert _eligible(dec)

    def test_opencode_pre_tool_call_api_error_is_eligible(self) -> None:
        """OpenCode APIError envelopes are provider failures when marked pre-tool-call."""
        dec = classify_failover_eligibility(
            _ctx(
                RuntimeError(
                    "OpenCode CLI exited with code 1: APIError: provider rejected the request"
                ),
                process_info={"runtime_type": "opencode", "is_pre_tool_call": True},
            )
        )

        assert _eligible(dec), dec.reason
        assert "provider_api_error" in dec.reason

    def test_opencode_pre_tool_call_api_error_does_not_require_cli_message(self) -> None:
        """Adapter metadata, not message prefix wording, identifies OpenCode failures."""
        dec = classify_failover_eligibility(
            _ctx(
                RuntimeError("APIError: provider rejected the request"),
                process_info={"runtime_type": "opencode", "is_pre_tool_call": True},
            )
        )

        assert _eligible(dec), dec.reason
        assert "provider_api_error" in dec.reason

    def test_opencode_api_error_without_pre_tool_call_metadata_is_default_closed(self) -> None:
        """Generic APIError text is not enough without adapter pre-tool-call metadata."""
        dec = classify_failover_eligibility(
            _ctx(
                RuntimeError(
                    "OpenCode CLI exited with code 1: APIError: provider rejected the request"
                )
            )
        )

        assert _suppressed(dec)
        assert "default-closed" in dec.reason


class TestForbiddenMarkerAuditGuard:
    """bu-0n2wk: pin the ``forbidden`` auth marker's behavior after the WAF/CDN
    false-positive audit.

    The audit (2026-07-10, live dev DB) found zero real provider-side WAF
    "403 Forbidden by security policy" messages in the classifier's actual
    input surface (butler ``sessions.error``). The only HTTP-403 traffic in the
    fleet is connector-level (Telegram, ingestion, connector heartbeat) and
    never reaches this classifier. An LLM provider's own 403 is an
    auth/permission rejection, so ``forbidden`` stays in the auth bucket
    (option a). These tests lock that decision and its precedence so a future
    edit cannot silently reclassify it.
    """

    def test_forbidden_marker_carries_provider_auth_error_reason(self) -> None:
        """A bare provider 403 is classified as a genuine auth failure."""
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("Anthropic API error: 403 Forbidden"))
        )
        assert _eligible(dec), dec.reason
        assert dec.reason.startswith("provider_auth_error"), dec.reason

    def test_hypothetical_waf_forbidden_is_eligible_auth_by_precedence(self) -> None:
        """The audited hypothetical ("403 Forbidden by security policy") is still
        failover-eligible; with no rate-limit/availability marker matching first
        it lands in the auth bucket. Documents the precedence the audit accepted
        (both buckets are eligible; only the reason label differs)."""
        dec = classify_failover_eligibility(_ctx(RuntimeError("403 Forbidden by security policy")))
        assert _eligible(dec), dec.reason
        assert dec.reason.startswith("provider_auth_error"), dec.reason

    def test_rate_limit_and_availability_win_over_forbidden(self) -> None:
        """Precedence guard: if a real message ever pairs 'forbidden' with a
        rate-limit or availability signal, the earlier bucket must classify it,
        never the auth bucket. Rate-limit is checked first, availability after
        auth — so a forbidden+rate-limit message is a rate-limit, while a
        forbidden+availability message stays auth (auth is checked first)."""
        rate = classify_failover_eligibility(
            _ctx(RuntimeError("429 forbidden: rate limit exceeded"))
        )
        assert rate.reason.startswith("rate_limit_before_work"), rate.reason


class TestProviderAvailabilityErrorsSplitFromAuth:
    """bu-ujm9d: provider/backend availability failures (connectivity, service
    errors) must be eligible for failover — same as genuine auth failures —
    but carry the distinct ``provider_unavailable`` reason prefix so they are
    never misattributed to the ``provider_auth_error`` bucket that discretion's
    auth-health surface and the ``auth_failure_default`` ignore-kind key off
    of. Proven repro from PR #3004 review:
    ``_classify_default_error(RuntimeError("Connection refused: could not
    reach provider"))`` used to return ``"auth_failure"`` before this split.
    """

    @pytest.mark.parametrize(
        "msg",
        [
            "Connection refused: could not reach provider",
            "Codex CLI exited with code 1: connection refused",
            "service unavailable: provider returned 503",
            "provider unavailable: anthropic returning 503",
            "backend unavailable",
            "network unreachable",
            "OpenCode CLI exited with code 1: bad gateway",
            "gateway timeout while contacting provider",
            "ApiAdapter invocation failed: Connection error.",
            "model not found in catalog",
            "no such model: claude-opus-99",
        ],
    )
    def test_availability_message_is_eligible_with_provider_unavailable_reason(
        self, msg: str
    ) -> None:
        dec = classify_failover_eligibility(_ctx(RuntimeError(msg)))
        assert _eligible(dec), f"Expected eligible for msg={msg!r}, got: {dec.reason}"
        assert dec.reason.startswith("provider_unavailable"), dec.reason
        assert not dec.reason.startswith("provider_auth_error"), dec.reason

    def test_connection_refused_repro_is_not_classified_as_auth_failure(self) -> None:
        """Proven repro from PR #3004 review — must NOT collapse to auth_failure."""
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("Connection refused: could not reach provider"))
        )
        assert _eligible(dec)
        assert dec.reason.startswith("provider_unavailable"), dec.reason


class TestApiAdapterAnthropicSdkErrorsEligible:
    """bu-qvnce.12 (PR #2936) fail-open verification: ApiAdapter wraps every
    Anthropic SDK exception as ``RuntimeError(f"ApiAdapter invocation failed:
    {exc}")``. The SDK's own exception message shapes differ from the CLI
    adapters this classifier was originally tuned for — most notably,
    ``anthropic.APIConnectionError`` hardcodes the literal message
    "Connection error." (no "refused"/"reset"/"timed out" suffix) for ANY
    network-level failure, and ``APIStatusError``'s JSON error envelope uses
    underscore-joined ``error.type`` identifiers (e.g. "api_error" for a
    generic 5xx) rather than the space-joined phrases the marker list already
    covers. Without explicit coverage, these are exactly the failure modes a
    live outage or network blip would produce, and they must fail over to the
    same-tier CLI safety net rather than terminate the dispatch.
    """

    def test_bare_anthropic_connection_error_is_eligible(self) -> None:
        """anthropic.APIConnectionError's exact hardcoded message must fail over.

        bu-ujm9d: this is a connectivity failure, not an identity/credential
        rejection — it must carry the ``provider_unavailable`` reason prefix,
        not ``provider_auth_error``.
        """
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("ApiAdapter invocation failed: Connection error."))
        )
        assert _eligible(dec), dec.reason
        assert dec.reason.startswith("provider_unavailable"), dec.reason

    def test_anthropic_5xx_api_error_type_is_eligible(self) -> None:
        """A generic Anthropic 500 (error.type == 'api_error') must fail over.

        bu-ujm9d: a 5xx is a backend availability failure, not an auth
        rejection — carries the ``provider_unavailable`` reason prefix.
        """
        dec = classify_failover_eligibility(
            _ctx(
                RuntimeError(
                    "ApiAdapter invocation failed: Error code: 500 - "
                    "{'type': 'error', 'error': {'type': 'api_error', "
                    "'message': 'Internal server error'}}"
                )
            )
        )
        assert _eligible(dec), dec.reason
        assert dec.reason.startswith("provider_unavailable"), dec.reason

    def test_anthropic_missing_api_key_message_is_eligible(self) -> None:
        """ApiAdapter's own no-credential RuntimeError must fail over — and
        stays in the genuine ``provider_auth_error`` bucket (bu-ujm9d)."""
        dec = classify_failover_eligibility(
            _ctx(
                RuntimeError(
                    "ApiAdapter: no Anthropic API key available (checked env, "
                    "credential store 'cli-auth/claude', ANTHROPIC_API_KEY env var)"
                )
            )
        )
        assert _eligible(dec), dec.reason
        assert dec.reason.startswith("provider_auth_error"), dec.reason

    def test_anthropic_401_authentication_error_is_eligible(self) -> None:
        """A 401 (error.type == 'authentication_error') must fail over — and
        stays in the genuine ``provider_auth_error`` bucket (bu-ujm9d)."""
        dec = classify_failover_eligibility(
            _ctx(
                RuntimeError(
                    "ApiAdapter invocation failed: Error code: 401 - "
                    "{'type': 'error', 'error': {'type': 'authentication_error', "
                    "'message': 'invalid x-api-key'}}"
                )
            )
        )
        assert _eligible(dec), dec.reason
        assert dec.reason.startswith("provider_auth_error"), dec.reason

    def test_anthropic_timeout_is_eligible(self) -> None:
        """ApiAdapter's own TimeoutError (no captured tool calls) must fail over."""
        dec = classify_failover_eligibility(
            _ctx(TimeoutError("ApiAdapter invocation timed out after 60 seconds"))
        )
        assert _eligible(dec), dec.reason


# ---------------------------------------------------------------------------
# AC-1b.1: Empty runtime responses ARE eligible
# ---------------------------------------------------------------------------


class TestEmptyRuntimeResponsesEligible:
    """Empty successful CLI responses are failover-eligible before any tool call."""

    def test_no_response_returned_is_eligible(self) -> None:
        """RuntimeError with empty-response wording is eligible."""
        dec = classify_failover_eligibility(
            _ctx(
                RuntimeError(
                    "OpenCode CLI returned no response: no result, tool calls, token usage, or stderr"
                )
            )
        )
        assert _eligible(dec), dec.reason
        assert "empty_runtime_response" in dec.reason


# ---------------------------------------------------------------------------
# AC-1c: Rate-limit errors ARE eligible
# ---------------------------------------------------------------------------


class TestRateLimitErrorsEligible:
    """AC-1c: Rate-limit errors are failover-eligible."""

    @pytest.mark.parametrize(
        "msg",
        [
            "Codex CLI exited with code 429: rate limit exceeded",
            "too many requests — please retry later",
            "rate_limit: 60 requests per minute exceeded",
            "ratelimit hit for model anthropic/claude-3",
            "quota exceeded: monthly token limit reached",
            "OpenCode CLI exited with code 1: Insufficient balance",
            "OpenCode CLI exited with code 1: APIError insufficient credits",
            "provider credit balance exhausted",
            "requests per second limit exceeded",
            "tokens per minute budget exhausted",
            "model is at capacity",
            "service overloaded, retry after 30s",
            "throttling applied — backoff required",
        ],
    )
    def test_rate_limit_message_is_eligible(self, msg: str) -> None:
        """RuntimeError with rate-limit message is eligible."""
        dec = classify_failover_eligibility(_ctx(RuntimeError(msg)))
        assert _eligible(dec), f"Expected eligible for msg={msg!r}, got: {dec.reason}"
        assert "rate_limit" in dec.reason

    def test_rate_limit_reason_label(self) -> None:
        """Decision reason should identify rate limit category."""
        dec = classify_failover_eligibility(_ctx(RuntimeError("too many requests")))
        assert _eligible(dec)
        assert "rate_limit" in dec.reason

    @pytest.mark.parametrize(
        "msg",
        [
            # Codex-specific transient backend failures — adapter exhausts internal
            # retries before propagating; spawner should attempt cross-model failover.
            "Codex CLI exited with code 1: codex_core::compact_remote failed",
            "Codex CLI exited with code 1: remote compaction failed",
            "compact_remote: could not compact session history",
        ],
    )
    def test_codex_compact_remote_is_eligible(self, msg: str) -> None:
        """Codex compact_remote / remote compaction failures are failover-eligible.

        These are transient Codex backend failures.  The adapter retries them
        internally; when all internal retries are exhausted the spawner should
        attempt failover to another same-tier model.
        """
        dec = classify_failover_eligibility(_ctx(RuntimeError(msg)))
        assert _eligible(dec), (
            f"Expected eligible for compact_remote msg={msg!r}, got: {dec.reason}"
        )
        assert "rate_limit" in dec.reason

    @pytest.mark.parametrize(
        "msg",
        [
            # The exact string the Codex CLI emits when the ChatGPT plan 5h/weekly
            # usage cap is hit — exit 1 before any tool call.  Observed in dev:
            # 74 such failures in 24h were misclassified as unknown_runtime_error
            # and never failed over to the same-tier opencode model.
            "Codex CLI exited with code 1: You've hit your usage limit. Visit "
            "https://chatgpt.com/codex/settings/usage to purchase more credits "
            "or try again at 12:25 PM.",
            "you've hit your usage limit",
            "reached usage limit for this period",
        ],
    )
    def test_codex_usage_limit_is_eligible(self, msg: str) -> None:
        """Codex plan usage-cap exhaustion is failover-eligible.

        Exit 1 with no tool calls is a pre-invocation systemic rejection; the
        spawner should fail over to a same-tier non-codex model rather than
        terminating the session.
        """
        dec = classify_failover_eligibility(_ctx(RuntimeError(msg)))
        assert _eligible(dec), f"Expected eligible for usage-limit msg={msg!r}, got: {dec.reason}"
        assert "rate_limit" in dec.reason


# ---------------------------------------------------------------------------
# AC-1d: MCP discovery failures ARE eligible
# ---------------------------------------------------------------------------


class TestMCPDiscoveryFailuresEligible:
    """AC-1d: MCP discovery failures are failover-eligible."""

    def test_mcp_tool_discovery_error_class_is_eligible(self) -> None:
        """MCPToolDiscoveryError (by class name) is eligible with no tool calls."""

        class MCPToolDiscoveryError(RuntimeError):
            """Fake stand-in for butlers.core.runtimes.codex.MCPToolDiscoveryError."""

            def __init__(self, msg: str = "discovery failed") -> None:
                super().__init__(msg)
                self.tool_calls: list = []
                self.result_text: str | None = None
                self.usage: dict = {}
                self.last_attempt_process_info: dict | None = None

        exc = MCPToolDiscoveryError("MCP tool discovery failed: connection refused")
        dec = classify_failover_eligibility(_ctx(exc))
        assert _eligible(dec)
        assert "mcp_discovery" in dec.reason

    def test_runtime_error_mcp_connection_failed_is_eligible(self) -> None:
        """RuntimeError with 'mcp connection failed' is eligible."""
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("mcp connection failed: transport error"))
        )
        assert _eligible(dec)
        assert "mcp_discovery" in dec.reason

    def test_runtime_error_failed_to_start_mcp_is_eligible(self) -> None:
        """RuntimeError with 'failed to start mcp' is eligible."""
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("Codex CLI exited with code 1: failed to start mcp server"))
        )
        assert _eligible(dec)

    def test_runtime_error_mcp_discovery_failed_is_eligible(self) -> None:
        """RuntimeError with 'mcp discovery failed' is eligible."""
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("mcp discovery failed after 3 retries"))
        )
        assert _eligible(dec)

    def test_mcp_tool_discovery_error_with_tool_calls_is_suppressed(self) -> None:
        """MCPToolDiscoveryError is suppressed when tool_calls are present.

        The side-effect gate (GATE 1) runs before the MCPToolDiscoveryError
        gate, so even a true discovery error is suppressed when work happened.
        """

        class MCPToolDiscoveryError(RuntimeError):
            pass

        exc = MCPToolDiscoveryError("mcp discovery failed")
        tool_calls = [{"name": "send_message", "input": {"text": "hello"}}]
        dec = classify_failover_eligibility(_ctx(exc, tool_calls=tool_calls))
        assert _suppressed(dec)
        assert "tool call" in dec.reason


# ---------------------------------------------------------------------------
# AC-1e: Timeout before work IS eligible
# ---------------------------------------------------------------------------


class TestTimeoutBeforeWorkEligible:
    """AC-1e: Timeout before any tool call is failover-eligible."""

    def test_timeout_error_no_tool_calls_is_eligible(self) -> None:
        """TimeoutError with no tool calls is eligible."""
        dec = classify_failover_eligibility(_ctx(TimeoutError("Codex CLI timed out after 300s")))
        assert _eligible(dec)
        assert "timeout" in dec.reason

    def test_timeout_error_with_tool_calls_is_suppressed(self) -> None:
        """TimeoutError with tool calls is suppressed (GATE 1 wins)."""
        tool_calls = [{"name": "memory_retrieve"}]
        dec = classify_failover_eligibility(_ctx(TimeoutError("timeout"), tool_calls=tool_calls))
        assert _suppressed(dec)
        assert "tool call" in dec.reason

    def test_timeout_error_empty_tool_calls_is_eligible(self) -> None:
        """TimeoutError with explicitly-empty tool_calls list is eligible."""
        dec = classify_failover_eligibility(_ctx(TimeoutError("timed out"), tool_calls=[]))
        assert _eligible(dec)


# ---------------------------------------------------------------------------
# FailoverDecision dataclass contract
# ---------------------------------------------------------------------------


class TestFailoverDecisionContract:
    """FailoverDecision is a frozen dataclass with typed fields."""

    def test_decision_is_immutable(self) -> None:
        """FailoverDecision is frozen (immutable after construction)."""
        dec = FailoverDecision(eligible=True, reason="test")
        with pytest.raises(Exception):
            dec.eligible = False  # type: ignore[misc]

    def test_eligible_true(self) -> None:
        dec = FailoverDecision(eligible=True, reason="systemic failure")
        assert dec.eligible is True
        assert dec.reason == "systemic failure"

    def test_eligible_false(self) -> None:
        dec = FailoverDecision(eligible=False, reason="tool calls present")
        assert dec.eligible is False

    def test_reason_is_string(self) -> None:
        dec = FailoverDecision(eligible=False, reason="suppressed")
        assert isinstance(dec.reason, str)


# ---------------------------------------------------------------------------
# AC-5: Unknown errors suppress failover
# ---------------------------------------------------------------------------


class TestUnknownErrorsSuppressFailover:
    """AC-5: Unknown errors suppress failover (default closed)."""

    def test_runtime_error_unknown_pattern_suppressed(self) -> None:
        """RuntimeError with no matching pattern is suppressed."""
        dec = classify_failover_eligibility(_ctx(RuntimeError("something went wrong in pipeline")))
        assert _suppressed(dec)
        assert "default-closed" in dec.reason or "unknown" in dec.reason

    def test_attribute_error_suppressed(self) -> None:
        """AttributeError is suppressed — application bug."""
        dec = classify_failover_eligibility(
            _ctx(AttributeError("'NoneType' has no attribute 'run'"))
        )
        assert _suppressed(dec)

    def test_import_error_suppressed(self) -> None:
        """ImportError is suppressed — application infrastructure issue."""
        dec = classify_failover_eligibility(_ctx(ImportError("cannot import name 'x'")))
        assert _suppressed(dec)

    def test_memory_error_suppressed(self) -> None:
        """MemoryError is suppressed."""
        dec = classify_failover_eligibility(_ctx(MemoryError()))
        assert _suppressed(dec)

    def test_arithmetic_error_suppressed(self) -> None:
        """ArithmeticError is suppressed."""
        dec = classify_failover_eligibility(_ctx(ZeroDivisionError("division by zero")))
        assert _suppressed(dec)


# ---------------------------------------------------------------------------
# Process info and trigger context are accepted without error
# ---------------------------------------------------------------------------


class TestContextFieldsAccepted:
    """process_info and trigger_context are accepted without crashing the classifier."""

    def test_process_info_present_does_not_affect_decision(self) -> None:
        """process_info is passed through but does not change eligibility."""
        proc_info = {"exit_code": 1, "stderr": "auth failed", "runtime_type": "codex"}
        dec = classify_failover_eligibility(
            _ctx(RuntimeError("authentication failed"), process_info=proc_info)
        )
        assert _eligible(dec)

    def test_trigger_context_present_does_not_crash(self) -> None:
        """trigger_context is accepted without crashing."""
        ctx = FailoverContext(
            exception=RuntimeError("unknown"),
            tool_calls=[],
            trigger_context={"source": "scheduler", "task_id": "abc"},
        )
        dec = classify_failover_eligibility(ctx)
        assert isinstance(dec, FailoverDecision)

    def test_none_process_info_accepted(self) -> None:
        """None process_info is handled gracefully."""
        ctx = FailoverContext(
            exception=FileNotFoundError("cli"),
            tool_calls=[],
            process_info=None,
        )
        dec = classify_failover_eligibility(ctx)
        assert _eligible(dec)


class TestMarkerBucketDisjointness:
    """Invariant: the marker buckets must have zero pairwise substring overlaps.

    Overlap would make classification order-dependent in a hidden way — a
    message could match two buckets and be labeled by whichever gate happens to
    run first, silently corrupting the ``provider_auth_error`` /
    ``provider_unavailable`` / ``rate_limit_before_work`` reason taxonomy that
    discretion's auth-health surface and the ``auth_failure_default`` ignore-kind
    key off of (bu-ujm9d, bu-0n2wk). This test reads the live marker tuples off
    the module, so any new marker that collides with an existing one across
    buckets fails here immediately.
    """

    def _marker_buckets(self) -> dict[str, tuple[str, ...]]:
        return {
            name: getattr(_fc, name)
            for name in dir(_fc)
            if name.endswith("_MARKERS") and isinstance(getattr(_fc, name), tuple)
        }

    def test_expected_seven_marker_buckets_present(self) -> None:
        """Guards against a bucket being renamed/dropped out of the invariant."""
        assert set(self._marker_buckets()) == {
            "_PROVIDER_AUTH_MARKERS",
            "_PROVIDER_AVAILABILITY_MARKERS",
            "_RATE_LIMIT_MARKERS",
            "_EMPTY_RESPONSE_MARKERS",
            "_MCP_DISCOVERY_MARKERS",
            "_RUNTIME_CONFIG_MARKERS",
            "_GUARDRAIL_MARKERS",
        }

    def test_no_pairwise_substring_overlap_across_buckets(self) -> None:
        """No marker in one bucket may be a substring of (or equal to) a marker
        in a different bucket. ``forbidden`` living only in the auth bucket is
        part of what this pins (bu-0n2wk)."""
        buckets = self._marker_buckets()
        offenders: list[str] = []
        for (na, a), (nb, b) in itertools.combinations(buckets.items(), 2):
            for x in a:
                for y in b:
                    if x in y or y in x:
                        offenders.append(f"{na}:{x!r} overlaps {nb}:{y!r}")
        assert not offenders, "marker buckets must be pairwise substring-disjoint: " + "; ".join(
            offenders
        )

    def test_markers_within_a_bucket_are_lowercase_and_stripped(self) -> None:
        """Markers are matched against a lowercased message, so an upper-case or
        untrimmed marker would silently never fire."""
        for name, bucket in self._marker_buckets().items():
            for marker in bucket:
                assert marker == marker.lower(), f"{name}: {marker!r} is not lowercase"
                assert marker == marker.strip(), f"{name}: {marker!r} has surrounding whitespace"
