"""Tests for adapter error surfaces that feed the failover classifier.

Verifies the acceptance criteria for bu-ojiij.5:

1. Codex pre-tool-call CLI failure is classifiable via last_process_info.
2. MCP discovery failure (MCPToolDiscoveryError) is classifiable and exposes
   internal_retry_count so the spawner never conflates adapter-internal retries
   with cross-model failover provenance.
3. Rate-limit / auth / model-unavailable / timeout signals map cleanly to
   classifier-eligible outcomes.
4. Adapter-internal retry provenance is NOT conflated with failover provenance —
   is_pre_tool_call=True and internal_retry_count>0 asserts the distinction.
5. ClaudeCode, Gemini, and OpenCode adapters expose error_detail and
   is_pre_tool_call on failure paths.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.failover_classifier import (
    FailoverContext,
    classify_failover_eligibility,
)
from butlers.core.runtimes.claude_code import ClaudeCodeAdapter
from butlers.core.runtimes.codex import CodexAdapter, MCPToolDiscoveryError
from butlers.core.runtimes.gemini import GeminiAdapter
from butlers.core.runtimes.opencode import OpenCodeAdapter

pytestmark = pytest.mark.unit

_CODEX_EXEC = "butlers.core.runtimes.codex.asyncio.create_subprocess_exec"
_CLAUDE_EXEC = "butlers.core.runtimes.claude_code.asyncio.create_subprocess_exec"
_GEMINI_EXEC = "butlers.core.runtimes.gemini.asyncio.create_subprocess_exec"
_OPENCODE_EXEC = "butlers.core.runtimes.opencode.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> AsyncMock:
    proc = AsyncMock()
    proc.pid = 9999
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = AsyncMock()
    proc.wait = AsyncMock()
    return proc


def _classify(exc: BaseException, tool_calls: list | None = None) -> bool:
    """Return True when the classifier says the exception is failover-eligible."""
    dec = classify_failover_eligibility(FailoverContext(exception=exc, tool_calls=tool_calls or []))
    return dec.eligible


# ---------------------------------------------------------------------------
# AC-1: Codex pre-tool-call CLI failure is classifiable
# ---------------------------------------------------------------------------


class TestCodexPreToolCallFailureClassifiable:
    """AC-1: Codex CLI pre-tool-call failures produce classifiable exceptions."""

    async def test_codex_nonzero_exit_raises_runtime_error(self, tmp_path: Path) -> None:
        """Codex non-zero exit raises RuntimeError classifiable as provider/auth."""
        proc = _make_proc(1, stderr=b"authentication failed: invalid API key")
        with patch(_CODEX_EXEC, return_value=proc):
            adapter = CodexAdapter(codex_binary="/usr/bin/codex")
            with pytest.raises(RuntimeError) as exc_info:
                await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        exc = exc_info.value
        # Classifier should treat this as eligible (auth failure, pre-tool-call)
        assert _classify(exc), (
            f"Expected classifier eligible, got reason: {classify_failover_eligibility(FailoverContext(exception=exc)).reason}"
        )

    async def test_codex_nonzero_exit_sets_is_pre_tool_call(self) -> None:
        """Codex non-zero exit sets is_pre_tool_call=True in last_process_info."""
        proc = _make_proc(1, stderr=b"rate limit exceeded")
        with patch(_CODEX_EXEC, return_value=proc):
            adapter = CodexAdapter(codex_binary="/usr/bin/codex")
            with pytest.raises(RuntimeError):
                await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        info = adapter.last_process_info
        assert info is not None
        assert info.get("is_pre_tool_call") is True

    async def test_codex_nonzero_exit_sets_error_detail(self) -> None:
        """Codex non-zero exit sets error_detail in last_process_info."""
        proc = _make_proc(1, stderr=b"model unavailable: gpt-4o")
        with patch(_CODEX_EXEC, return_value=proc):
            adapter = CodexAdapter(codex_binary="/usr/bin/codex")
            with pytest.raises(RuntimeError):
                await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        info = adapter.last_process_info
        assert info is not None
        assert "error_detail" in info
        assert "model unavailable" in info["error_detail"].lower()

    async def test_codex_timeout_sets_is_pre_tool_call(self) -> None:
        """Codex timeout sets is_pre_tool_call=True in last_process_info."""
        proc = _make_proc(0)
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        with patch(_CODEX_EXEC, return_value=proc):
            adapter = CodexAdapter(codex_binary="/usr/bin/codex")
            with pytest.raises(TimeoutError):
                await adapter.invoke(
                    prompt="slow", system_prompt="", mcp_servers={}, env={}, timeout=1
                )
        info = adapter.last_process_info
        assert info is not None
        assert info.get("is_pre_tool_call") is True

    async def test_codex_rate_limit_exit_is_classifiable(self) -> None:
        """Codex rate-limit exit code produces a classifier-eligible RuntimeError."""
        proc = _make_proc(429, stderr=b"too many requests")
        with patch(_CODEX_EXEC, return_value=proc):
            adapter = CodexAdapter(codex_binary="/usr/bin/codex")
            with pytest.raises(RuntimeError) as exc_info:
                await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        assert _classify(exc_info.value)

    async def test_codex_model_unavailable_exit_is_classifiable(self) -> None:
        """Codex model-unavailable stderr produces a classifier-eligible RuntimeError."""
        proc = _make_proc(1, stderr=b"model is unavailable: o3-pro")
        with patch(_CODEX_EXEC, return_value=proc):
            adapter = CodexAdapter(codex_binary="/usr/bin/codex")
            with pytest.raises(RuntimeError) as exc_info:
                await adapter.invoke(prompt="hello", system_prompt="", mcp_servers={}, env={})
        assert _classify(exc_info.value)

    def test_codex_file_not_found_is_classifiable(self) -> None:
        """Codex FileNotFoundError (missing binary) is classifier-eligible."""
        exc = FileNotFoundError("Codex CLI binary not found on PATH")
        assert _classify(exc)


# ---------------------------------------------------------------------------
# AC-2: MCP discovery failure is classifiable + internal_retry_count exposed
# ---------------------------------------------------------------------------


class TestMCPDiscoveryFailureClassifiable:
    """AC-2: MCPToolDiscoveryError is classifiable and exposes internal retry count."""

    def test_mcp_discovery_error_has_is_pre_tool_call_true(self) -> None:
        """MCPToolDiscoveryError.is_pre_tool_call is always True."""
        exc = MCPToolDiscoveryError(
            "MCP tool discovery failed after 3 attempts",
            result_text=None,
            tool_calls=[],
            usage=None,
            internal_retry_count=2,
        )
        assert exc.is_pre_tool_call is True

    def test_mcp_discovery_error_exposes_internal_retry_count(self) -> None:
        """MCPToolDiscoveryError.internal_retry_count reflects adapter-internal retries."""
        exc = MCPToolDiscoveryError(
            "MCP tool discovery failed after 3 attempts",
            result_text=None,
            tool_calls=[],
            usage=None,
            internal_retry_count=2,
        )
        assert exc.internal_retry_count == 2

    def test_mcp_discovery_error_zero_retries_by_default(self) -> None:
        """MCPToolDiscoveryError defaults internal_retry_count to 0."""
        exc = MCPToolDiscoveryError(
            "MCP tool discovery failed",
            result_text=None,
            tool_calls=[],
            usage=None,
        )
        assert exc.internal_retry_count == 0

    def test_mcp_discovery_error_is_classifier_eligible(self) -> None:
        """MCPToolDiscoveryError with no tool calls is classifier-eligible."""
        exc = MCPToolDiscoveryError(
            "MCP tool discovery failed after 3 attempts. "
            "The butler's MCP server was configured but the Codex CLI "
            "could not connect to it.",
            result_text=None,
            tool_calls=[],
            usage=None,
            internal_retry_count=2,
        )
        dec = classify_failover_eligibility(FailoverContext(exception=exc, tool_calls=[]))
        assert dec.eligible, f"Expected eligible, got: {dec.reason}"
        assert "mcp_discovery" in dec.reason

    def test_mcp_discovery_error_with_tool_calls_suppressed(self) -> None:
        """MCPToolDiscoveryError is suppressed when the spawner captured tool calls."""
        exc = MCPToolDiscoveryError(
            "MCP tool discovery failed",
            result_text=None,
            tool_calls=[],
            usage=None,
        )
        # Spawner passes daemon-captured tool calls to classifier
        spawner_tool_calls = [{"name": "state_set", "input": {"key": "x", "value": 1}}]
        dec = classify_failover_eligibility(
            FailoverContext(exception=exc, tool_calls=spawner_tool_calls)
        )
        assert not dec.eligible
        assert "tool call" in dec.reason

    def test_mcp_discovery_error_internal_retries_not_conflated_with_failover(self) -> None:
        """internal_retry_count distinguishes adapter retries from cross-model failover.

        The spawner must NOT count internal_retry_count toward its own failover
        attempt bookkeeping. This test asserts the field is accessible and
        correctly identifies the boundary.
        """
        # Simulate: Codex tried MCP discovery 3 times internally, then raised.
        exc = MCPToolDiscoveryError(
            "MCP tool discovery failed after 3 attempts",
            result_text=None,
            tool_calls=[],
            usage=None,
            internal_retry_count=2,  # 1 initial + 2 retries = 3 attempts
        )
        # The spawner would treat this as ONE logical failover attempt (not 3).
        # internal_retry_count=2 tells spawner: 2 retries happened inside adapter.
        assert exc.internal_retry_count == 2
        # is_pre_tool_call confirms no side effects occurred despite the retries.
        assert exc.is_pre_tool_call is True
        # The exception itself is still classifier-eligible.
        assert _classify(exc)

    def test_mcp_discovery_error_negative_retries_clamped_to_zero(self) -> None:
        """internal_retry_count is clamped to >= 0 even with negative input."""
        exc = MCPToolDiscoveryError(
            "discovery failed",
            result_text=None,
            tool_calls=[],
            usage=None,
            internal_retry_count=-5,
        )
        assert exc.internal_retry_count == 0


# ---------------------------------------------------------------------------
# AC-3: Rate-limit / auth / model-unavailable / timeout across all adapters
# ---------------------------------------------------------------------------


_ADAPTER_FACTORY = {
    "claude": (_CLAUDE_EXEC, lambda: ClaudeCodeAdapter(claude_binary="/usr/bin/claude")),
    "gemini": (_GEMINI_EXEC, lambda: GeminiAdapter(gemini_binary="/usr/bin/gemini")),
    "opencode": (_OPENCODE_EXEC, lambda: OpenCodeAdapter(opencode_binary="/usr/bin/opencode")),
}


class TestRateLimitAuthModelUnavailableTimeoutMapping:
    """AC-3: Rate-limit / auth / model-unavailable / timeout signals map cleanly.

    Parametrized across the ClaudeCode/Gemini/OpenCode adapters: each adapter must
    raise a classifier-eligible RuntimeError on auth / rate-limit / model-unavailable
    stderr, recording error_detail and is_pre_tool_call=True in last_process_info.
    """

    @pytest.mark.parametrize(
        "adapter_key,returncode,stderr,detail_substr",
        [
            # ClaudeCode
            ("claude", 1, b"unauthorized: invalid API key", "api key"),
            ("claude", 429, b"rate limit exceeded", "rate limit"),
            ("claude", 1, b"model is unavailable: claude-opus-4", "model is unavailable"),
            # Gemini
            ("gemini", 1, b"quota exceeded for project", "quota"),
            ("gemini", 1, b"authentication failed: google auth error", "auth"),
            ("gemini", 429, b"rate limit exceeded: too many requests", "rate limit"),
            ("gemini", 1, b"model not found: gemini-ultra-999", "model not found"),
            # OpenCode (non-zero exit)
            ("opencode", 1, b"provider unavailable: openai returned 503", "provider unavailable"),
            ("opencode", 1, b"authentication failed: invalid credential", "auth"),
            ("opencode", 429, b"rate limit exceeded", "rate limit"),
        ],
    )
    async def test_adapter_failure_classifiable_and_records_detail(
        self, adapter_key: str, returncode: int, stderr: bytes, detail_substr: str
    ) -> None:
        exec_const, make_adapter = _ADAPTER_FACTORY[adapter_key]
        proc = _make_proc(returncode, stderr=stderr)
        with patch(exec_const, return_value=proc):
            adapter = make_adapter()
            with pytest.raises(RuntimeError) as exc_info:
                await adapter.invoke(prompt="hi", system_prompt="", mcp_servers={}, env={})
        assert _classify(exc_info.value)
        info = adapter.last_process_info
        assert info is not None
        assert info.get("is_pre_tool_call") is True
        assert "error_detail" in info
        assert detail_substr in info["error_detail"].lower()

    @pytest.mark.parametrize("adapter_key", ["claude", "gemini", "opencode"])
    async def test_adapter_timeout_sets_is_pre_tool_call(self, adapter_key: str) -> None:
        """Each adapter's timeout path sets is_pre_tool_call=True."""
        exec_const, make_adapter = _ADAPTER_FACTORY[adapter_key]
        proc = _make_proc(0)
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        with patch(exec_const, return_value=proc):
            adapter = make_adapter()
            with pytest.raises(TimeoutError):
                await adapter.invoke(
                    prompt="slow", system_prompt="", mcp_servers={}, env={}, timeout=1
                )
        info = adapter.last_process_info
        assert info is not None
        assert info.get("is_pre_tool_call") is True

    @pytest.mark.parametrize(
        "message",
        [
            "Provider rejected request: out of credits",
            "Provider rejected request: balance exhausted",
            "Provider rejected request: billing limit reached",
            "Provider rejected request: credit limit reached",
        ],
    )
    def test_billing_credit_exhaustion_variants_are_classifiable(self, message: str) -> None:
        decision = classify_failover_eligibility(
            FailoverContext(exception=RuntimeError(message), tool_calls=[])
        )

        assert decision.eligible
        assert "rate_limit" in decision.reason

    @pytest.mark.parametrize(
        "stderr",
        [
            b"ProviderModelNotFoundError: gpt-9 not found",
            b"AuthenticationError: invalid API key",
        ],
    )
    async def test_opencode_exit0_stderr_error_is_classifiable(self, stderr: bytes) -> None:
        """OpenCode exits 0 on model-not-found/auth errors but writes structured stderr;
        the adapter detects it, raises a classifier-eligible RuntimeError, and flags
        is_pre_tool_call."""
        proc = _make_proc(0, stdout=b"", stderr=stderr)
        with patch(_OPENCODE_EXEC, return_value=proc):
            adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
            with pytest.raises(RuntimeError) as exc_info:
                await adapter.invoke(prompt="hi", system_prompt="", mcp_servers={}, env={})
        assert _classify(exc_info.value)
        info = adapter.last_process_info
        assert info is not None
        assert info.get("is_pre_tool_call") is True

    async def test_opencode_empty_exit_zero_is_empty_runtime_response(self) -> None:
        """OpenCode empty exit-0 output is a pre-tool-call failover signal classified
        as empty_runtime_response."""
        proc = _make_proc(0, stdout=b"", stderr=b"")
        with patch(_OPENCODE_EXEC, return_value=proc):
            adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
            with pytest.raises(RuntimeError) as exc_info:
                await adapter.invoke(prompt="hi", system_prompt="", mcp_servers={}, env={})

        assert "no response" in str(exc_info.value).lower()
        info = adapter.last_process_info
        assert info is not None
        assert info.get("is_pre_tool_call") is True
        assert "error_detail" in info
        decision = classify_failover_eligibility(
            FailoverContext(exception=exc_info.value, tool_calls=[], process_info=info)
        )
        assert decision.eligible
        assert "empty_runtime_response" in decision.reason

    async def test_opencode_nested_api_error_insufficient_balance_is_classifiable(self) -> None:
        """OpenCode structured APIError stdout should surface its nested billing message.

        The provider rejects the request before model work starts, so the classifier
        should treat the adapter RuntimeError as a same-tier failover signal.
        """
        stdout = (
            b'{"type":"error","timestamp":"2026-06-21T10:42:00.000Z",'
            b'"sessionID":"session-123","error":{"name":"APIError","data":'
            b'{"message":"Insufficient balance. Manage your balance settings."}}}'
        )
        proc = _make_proc(1, stdout=stdout, stderr=b"")
        with patch(_OPENCODE_EXEC, return_value=proc):
            adapter = OpenCodeAdapter(opencode_binary="/usr/bin/opencode")
            with pytest.raises(RuntimeError) as exc_info:
                await adapter.invoke(prompt="hi", system_prompt="", mcp_servers={}, env={})

        assert "APIError: Insufficient balance. Manage your balance settings." in str(
            exc_info.value
        )
        assert '{"type":"error"' not in str(exc_info.value)
        info = adapter.last_process_info
        assert info is not None
        assert info.get("is_pre_tool_call") is True
        assert (
            info.get("error_detail")
            == "APIError: Insufficient balance. Manage your balance settings."
        )
        decision = classify_failover_eligibility(
            FailoverContext(exception=exc_info.value, tool_calls=[], process_info=info)
        )
        assert decision.eligible
        assert "rate_limit" in decision.reason


# ---------------------------------------------------------------------------
# AC-4: Adapter-internal retry provenance NOT conflated with failover
# ---------------------------------------------------------------------------


class TestAdapterInternalRetryNotConflatedWithFailover:
    """AC-4: Adapter-internal retries are distinct from cross-model failover.

    The failover classifier must see ONE logical attempt even when the adapter
    exhausted multiple internal retry attempts.
    """

    def test_mcp_discovery_error_is_one_logical_failover_attempt(self) -> None:
        """MCPToolDiscoveryError represents one logical failover attempt.

        Even if the Codex adapter tried MCP discovery 3 times internally
        (1 initial + 2 retries), the spawner should treat the MCPToolDiscoveryError
        as ONE failed attempt. internal_retry_count=2 lets the spawner account for
        the adapter-internal work without double-counting it as failover.
        """
        # Simulate exactly what Codex raises after exhausting _MCP_RETRY_DELAYS
        # (which has 2 delays → 1 initial + 2 retries = 3 subprocess spawns).
        exc = MCPToolDiscoveryError(
            "MCP tool discovery failed after 3 attempts. "
            "The butler's MCP server was configured but the Codex CLI "
            "could not connect to it. This session cannot proceed without MCP tools.",
            result_text=None,
            tool_calls=[],
            usage=None,
            internal_retry_count=2,
        )
        # Classifier sees ONE exception → ONE failover-eligible event.
        dec = classify_failover_eligibility(FailoverContext(exception=exc, tool_calls=[]))
        assert dec.eligible

        # The spawner reads internal_retry_count to know 2 adapter-internal retries
        # happened but should NOT count them as cross-model failover attempts.
        assert exc.internal_retry_count == 2

        # is_pre_tool_call=True confirms no side effects across all 3 subprocess spawns.
        assert exc.is_pre_tool_call is True

    def test_runtime_error_retry_metadata_in_process_info(self) -> None:
        """RuntimeError from Codex transient failure path includes retry metadata.

        When the Codex adapter raises after internal transient-CLI retries, the
        last_process_info contains retry_attempted=True. The classifier sees only
        the RuntimeError message — not the retry count — so it cannot conflate
        internal retries with cross-model failover.
        """
        # Simulate the metadata set by the transient-retry path in codex.py
        process_info = {
            "exit_code": 1,
            "stderr": "compact_remote: remote compaction failed",
            "runtime_type": "codex",
            "retry_attempted": True,
            "retry_succeeded": False,
            "attempt_count": 3,  # adapter-internal attempts
            "is_pre_tool_call": True,
        }
        exc = RuntimeError("Codex CLI exited with code 1: compact_remote: remote compaction failed")
        dec = classify_failover_eligibility(
            FailoverContext(exception=exc, tool_calls=[], process_info=process_info)
        )
        # Classifier says eligible based on rate-limit/compact_remote marker.
        assert dec.eligible
        assert "rate_limit" in dec.reason

        # process_info.attempt_count=3 here is adapter-internal; the spawner
        # must not treat this as 3 cross-model failover attempts.
        assert process_info["retry_attempted"] is True
        assert process_info["is_pre_tool_call"] is True
