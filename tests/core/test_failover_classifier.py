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

import pytest

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
