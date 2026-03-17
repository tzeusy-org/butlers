"""Tests for butlers.core.healing.fingerprint.

Covers:
- FingerprintResult fields and types
- Dual-input parity: compute_fingerprint vs compute_fingerprint_from_report
- Message sanitization: UUIDs, timestamps, numeric IDs, empty messages, truncation
- Call site extraction from traceback objects and traceback strings
- Severity scoring: DB errors, credential errors, runtimes, modules, memory, cancellation
- Severity hint: hint overrides default-only, not specific rules
- Chained exceptions use outer exception type
- Same root cause → same fingerprint; different call sites → different fingerprints
- Exception without traceback falls back to <unknown>:<unknown>
"""

from __future__ import annotations

import asyncio
import hashlib
import types

import pytest

from butlers.core.healing.fingerprint import (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_INFO,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    FingerprintResult,
    _sanitize_message,
    compute_fingerprint,
    compute_fingerprint_from_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tb_for_exc(exc: BaseException) -> types.TracebackType:
    """Raise and immediately catch *exc* to obtain a fresh traceback."""
    try:
        raise exc
    except type(exc):
        import sys

        return sys.exc_info()[2]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# FingerprintResult type checks
# ---------------------------------------------------------------------------


class TestFingerprintResultType:
    def test_fields_present(self) -> None:
        exc = ValueError("some error")
        tb = _tb_for_exc(exc)
        result = compute_fingerprint(exc, tb)

        assert isinstance(result, FingerprintResult)
        assert isinstance(result.fingerprint, str)
        assert isinstance(result.severity, int)
        assert isinstance(result.exception_type, str)
        assert isinstance(result.call_site, str)
        assert isinstance(result.sanitized_message, str)

    def test_fingerprint_is_64_hex_chars(self) -> None:
        exc = ValueError("some error")
        tb = _tb_for_exc(exc)
        result = compute_fingerprint(exc, tb)

        assert len(result.fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in result.fingerprint)

    def test_fingerprint_result_is_frozen(self) -> None:
        exc = ValueError("some error")
        tb = _tb_for_exc(exc)
        result = compute_fingerprint(exc, tb)

        with pytest.raises(Exception):  # frozen dataclass raises FrozenInstanceError
            result.fingerprint = "new"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Dual-input parity
# ---------------------------------------------------------------------------


class TestDualInputParity:
    def test_same_fingerprint_both_paths(self) -> None:
        """compute_fingerprint and compute_fingerprint_from_report produce identical
        fingerprints."""

        class FakeError(Exception):
            pass

        exc = FakeError("missing_key")
        tb = _tb_for_exc(exc)

        raw_result = compute_fingerprint(exc, tb)

        # Reproduce what the module path would pass
        report_result = compute_fingerprint_from_report(
            error_type=raw_result.exception_type,
            error_message="missing_key",
            call_site=raw_result.call_site,
            traceback_str=None,
            severity_hint=None,
        )

        assert raw_result.fingerprint == report_result.fingerprint

    def test_same_message_sanitized_both_paths(self) -> None:
        msg = "session 550e8400-e29b-41d4-a716-446655440000 not found"
        exc = RuntimeError(msg)
        tb = _tb_for_exc(exc)

        raw_result = compute_fingerprint(exc, tb)
        report_result = compute_fingerprint_from_report(
            error_type=raw_result.exception_type,
            error_message=msg,
            call_site=raw_result.call_site,
            traceback_str=None,
        )

        assert raw_result.fingerprint == report_result.fingerprint
        assert raw_result.sanitized_message == report_result.sanitized_message
        assert "<UUID>" in raw_result.sanitized_message


# ---------------------------------------------------------------------------
# Message sanitization
# ---------------------------------------------------------------------------


class TestMessageSanitization:
    def test_uuid_replaced(self) -> None:
        msg = "session 550e8400-e29b-41d4-a716-446655440000 not found"
        assert _sanitize_message(msg) == "session <UUID> not found"

    def test_timestamp_replaced(self) -> None:
        msg = "timeout at 2026-03-17T14:30:00Z"
        result = _sanitize_message(msg)
        assert "<TS>" in result
        assert "2026" not in result

    def test_date_only_timestamp_replaced(self) -> None:
        msg = "event on 2026-03-17 was missed"
        result = _sanitize_message(msg)
        assert "<TS>" in result

    def test_numeric_id_replaced(self) -> None:
        msg = "row 12345 missing"
        assert _sanitize_message(msg) == "row <ID> missing"

    def test_multiple_dynamic_values(self) -> None:
        msg = "user 550e8400-e29b-41d4-a716-446655440000 failed at 2026-03-17 with code 500"
        result = _sanitize_message(msg)
        assert "<UUID>" in result
        assert "<TS>" in result
        assert "<ID>" in result
        # Original dynamic values should not appear
        assert "550e8400" not in result
        assert "2026-03-17" not in result
        assert "500" not in result

    def test_empty_message_produces_empty_placeholder(self) -> None:
        assert _sanitize_message("") == "<empty>"

    def test_none_like_empty_message(self) -> None:
        # None is passed as empty string to _sanitize_message
        assert _sanitize_message("") == "<empty>"

    def test_truncation_at_500_chars(self) -> None:
        long_msg = "x" * 600
        result = _sanitize_message(long_msg)
        assert len(result) == 500

    def test_short_message_not_truncated(self) -> None:
        msg = "short error"
        result = _sanitize_message(msg)
        assert result == "short error"

    def test_alphanumeric_id_replaced(self) -> None:
        """Spec scenario: alphanumeric identifiers with numeric suffixes must be collapsed.

        Covers the Fingerprint Computation scenario where two sessions failing with
        'relation "foo_123" does not exist' and 'relation "foo_456" does not exist'
        must produce the same fingerprint.
        """
        # Both variants (with different numeric suffixes) must produce the same result
        result1 = _sanitize_message('relation "foo_123" does not exist')
        result2 = _sanitize_message('relation "foo_456" does not exist')
        assert result1 == result2
        assert "<ID>" in result1
        assert "123" not in result1
        assert "456" not in result2

    def test_pure_alpha_not_replaced(self) -> None:
        """Non-numeric tokens (no digits) are not replaced."""
        msg = "key missing_key not found"
        assert _sanitize_message(msg) == "key missing_key not found"


# ---------------------------------------------------------------------------
# Exception type extraction
# ---------------------------------------------------------------------------


class TestExceptionTypeExtraction:
    def test_builtin_exception(self) -> None:
        exc = ValueError("invalid literal")
        tb = _tb_for_exc(exc)
        result = compute_fingerprint(exc, tb)
        assert result.exception_type == "builtins.ValueError"

    def test_standard_library_exception(self) -> None:
        exc = asyncio.CancelledError()
        tb = _tb_for_exc(exc)
        result = compute_fingerprint(exc, tb)
        assert "CancelledError" in result.exception_type

    def test_custom_exception_module(self) -> None:
        """Exception defined in this test module has correct module prefix."""
        exc = ValueError("test")
        tb = _tb_for_exc(exc)
        result = compute_fingerprint(exc, tb)
        assert result.exception_type.endswith("ValueError")

    def test_chained_exception_uses_outermost(self) -> None:
        """Chained exception: outermost type is used, not the cause."""
        try:
            try:
                raise ConnectionRefusedError("port 5432")
            except ConnectionRefusedError as cause:
                raise RuntimeError("failed") from cause
        except RuntimeError as exc:
            import sys

            tb = sys.exc_info()[2]
            result = compute_fingerprint(exc, tb)

        # Outermost exception type — RuntimeError
        assert "RuntimeError" in result.exception_type
        assert "ConnectionRefusedError" not in result.exception_type


# ---------------------------------------------------------------------------
# Call site extraction
# ---------------------------------------------------------------------------


class TestCallSiteExtraction:
    def test_no_traceback_returns_unknown(self) -> None:
        exc = ValueError("test")
        result = compute_fingerprint(exc, None)
        assert result.call_site == "<unknown>:<unknown>"

    def test_call_site_from_traceback_excludes_line_number(self) -> None:
        """Call site must be file:function, never file:line:function."""
        exc = ValueError("test")
        tb = _tb_for_exc(exc)
        result = compute_fingerprint(exc, tb)
        # Should not contain a digit-only segment (line number)
        parts = result.call_site.split(":")
        # Either <unknown>:<unknown> or path:funcname
        assert len(parts) >= 2
        assert not parts[-1].isdigit()

    def test_call_site_from_report_uses_provided_call_site(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.KeyError",
            error_message="missing",
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
        )
        assert result.call_site == "src/butlers/modules/email.py:send_email"

    def test_call_site_from_report_parses_traceback_str_when_no_call_site(self) -> None:
        tb_str = (
            "Traceback (most recent call last):\n"
            '  File "/usr/lib/python3.12/asyncio/tasks.py", line 314, in __step\n'
            "    coro.send(None)\n"
            '  File "/home/user/repo/src/butlers/modules/email.py", line 42, in send_email\n'
            '    raise smtplib.SMTPAuthenticationError(535, b"auth failed")\n'
        )
        result = compute_fingerprint_from_report(
            error_type="smtplib.SMTPAuthenticationError",
            error_message="auth failed",
            call_site=None,
            traceback_str=tb_str,
        )
        assert "src/butlers/modules/email.py" in result.call_site
        assert "send_email" in result.call_site

    def test_call_site_falls_back_when_only_stdlib_frames(self) -> None:
        tb_str = (
            "Traceback (most recent call last):\n"
            '  File "/usr/lib/python3.12/asyncio/tasks.py", line 314, in __step\n'
            "    coro.send(None)\n"
        )
        result = compute_fingerprint_from_report(
            error_type="builtins.ValueError",
            error_message="bad value",
            call_site=None,
            traceback_str=tb_str,
        )
        assert result.call_site == "<unknown>:<unknown>"

    def test_different_call_sites_produce_different_fingerprints(self) -> None:
        result_a = compute_fingerprint_from_report(
            error_type="builtins.KeyError",
            error_message="x",
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
        )
        result_b = compute_fingerprint_from_report(
            error_type="builtins.KeyError",
            error_message="x",
            call_site="src/butlers/modules/calendar.py:create_event",
            traceback_str=None,
        )
        assert result_a.fingerprint != result_b.fingerprint

    def test_same_root_cause_same_call_site_same_fingerprint(self) -> None:
        """Two sessions with the same error at the same call site produce same fingerprint."""
        result_a = compute_fingerprint_from_report(
            error_type="builtins.KeyError",
            error_message="missing_key",
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
        )
        result_b = compute_fingerprint_from_report(
            error_type="builtins.KeyError",
            error_message="missing_key",
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
        )
        assert result_a.fingerprint == result_b.fingerprint


# ---------------------------------------------------------------------------
# Severity scoring
# ---------------------------------------------------------------------------


class TestSeverityScoring:
    def test_asyncpg_postgres_error_is_critical(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="asyncpg.exceptions.UndefinedTableError",
            error_message='relation "foo" does not exist',
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_CRITICAL

    def test_asyncpg_interface_error_is_critical(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="asyncpg.InterfaceError",
            error_message="connection pool exhausted",
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_CRITICAL

    def test_credential_store_error_is_critical(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="butlers.credentials.CredentialStoreError",
            error_message="secret not found",
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_CRITICAL

    def test_runtime_call_site_is_high(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.RuntimeError",
            error_message="adapter failed",
            call_site="src/butlers/core/runtimes/claude_code.py:invoke",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_HIGH

    def test_read_system_prompt_is_high(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.FileNotFoundError",
            error_message="system prompt not found",
            call_site="src/butlers/core/spawner.py:read_system_prompt",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_HIGH

    def test_build_env_is_high(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.KeyError",
            error_message="missing env var",
            call_site="src/butlers/core/spawner.py:_build_env",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_HIGH

    def test_resolve_provider_config_is_high(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.ValueError",
            error_message="no model configured",
            call_site="src/butlers/core/spawner.py:_resolve_provider_config",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_HIGH

    def test_module_call_site_is_medium(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.TypeError",
            error_message="wrong arg type",
            call_site="src/butlers/modules/calendar.py:create_event",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_MEDIUM

    def test_memory_fetch_is_low(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.RuntimeError",
            error_message="memory fetch failed",
            call_site="src/butlers/core/spawner.py:fetch_memory_context",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_LOW

    def test_memory_store_is_low(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.RuntimeError",
            error_message="episode store failed",
            call_site="src/butlers/core/spawner.py:store_session_episode",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_LOW

    def test_cancelled_error_is_info(self) -> None:
        exc = asyncio.CancelledError()
        tb = _tb_for_exc(exc)
        result = compute_fingerprint(exc, tb)
        assert result.severity == SEVERITY_INFO

    def test_keyboard_interrupt_is_info(self) -> None:
        exc = KeyboardInterrupt()
        tb = _tb_for_exc(exc)
        result = compute_fingerprint(exc, tb)
        assert result.severity == SEVERITY_INFO

    def test_unknown_error_defaults_to_medium(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.AttributeError",
            error_message="object has no attribute 'foo'",
            call_site="<unknown>:<unknown>",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_MEDIUM

    @pytest.mark.parametrize(
        "error_type",
        [
            # Public alias
            "asyncio.CancelledError",
            # Internal module path returned by _fully_qualified_name() on Python 3.12+
            "asyncio.exceptions.CancelledError",
            "builtins.KeyboardInterrupt",
            # concurrent.futures paths
            "concurrent.futures.CancelledError",
            "concurrent.futures._base.CancelledError",
        ],
    )
    def test_cancelled_error_by_type_string_is_info(self, error_type: str) -> None:
        result = compute_fingerprint_from_report(
            error_type=error_type,
            error_message="",
            call_site="<unknown>:<unknown>",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_INFO


# ---------------------------------------------------------------------------
# Severity hint
# ---------------------------------------------------------------------------


class TestSeverityHint:
    def test_hint_upgrades_default_medium(self) -> None:
        """Agent hint overrides default severity (medium → high)."""
        result = compute_fingerprint_from_report(
            error_type="builtins.AttributeError",
            error_message="attr missing",
            call_site="<unknown>:<unknown>",
            traceback_str=None,
            severity_hint="high",
        )
        assert result.severity == SEVERITY_HIGH

    def test_hint_does_not_override_specific_rule(self) -> None:
        """Agent hint cannot downgrade a specific rule (critical DB error)."""
        result = compute_fingerprint_from_report(
            error_type="asyncpg.exceptions.PostgresError",
            error_message="connection lost",
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
            severity_hint="low",
        )
        assert result.severity == SEVERITY_CRITICAL

    def test_hint_does_not_override_high_rule(self) -> None:
        """Hint cannot upgrade a high-severity rule to critical."""
        result = compute_fingerprint_from_report(
            error_type="builtins.RuntimeError",
            error_message="adapter failed",
            call_site="src/butlers/core/runtimes/claude_code.py:invoke",
            traceback_str=None,
            severity_hint="critical",
        )
        # High was set by specific rule; hint should not change it
        assert result.severity == SEVERITY_HIGH

    def test_no_hint_uses_auto_scoring(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.AttributeError",
            error_message="attr missing",
            call_site="<unknown>:<unknown>",
            traceback_str=None,
            severity_hint=None,
        )
        assert result.severity == SEVERITY_MEDIUM

    def test_hint_downgrade_from_default(self) -> None:
        """Agent hint can downgrade default medium to low."""
        result = compute_fingerprint_from_report(
            error_type="builtins.AttributeError",
            error_message="attr missing",
            call_site="<unknown>:<unknown>",
            traceback_str=None,
            severity_hint="low",
        )
        assert result.severity == SEVERITY_LOW

    def test_invalid_hint_falls_back_to_auto(self) -> None:
        """Unknown hint string is ignored; auto score used."""
        result = compute_fingerprint_from_report(
            error_type="builtins.AttributeError",
            error_message="attr missing",
            call_site="<unknown>:<unknown>",
            traceback_str=None,
            severity_hint="urgent",  # not a valid key
        )
        assert result.severity == SEVERITY_MEDIUM


# ---------------------------------------------------------------------------
# Fingerprint hash correctness
# ---------------------------------------------------------------------------


class TestFingerprintHashCorrectness:
    def test_fingerprint_matches_manual_sha256(self) -> None:
        """Fingerprint must equal SHA-256(type||call_site||sanitized_msg)."""
        error_type = "asyncpg.exceptions.UndefinedTableError"
        call_site = "src/butlers/modules/email.py:send_email"
        # Use a message with a standalone numeric ID (space-separated)
        raw_msg = "relation foo does not exist (row 123)"
        sanitized = "relation foo does not exist (row <ID>)"

        expected_hash = hashlib.sha256(
            f"{error_type}||{call_site}||{sanitized}".encode()
        ).hexdigest()

        result = compute_fingerprint_from_report(
            error_type=error_type,
            error_message=raw_msg,
            call_site=call_site,
            traceback_str=None,
        )
        assert result.fingerprint == expected_hash

    def test_fingerprint_from_spec_scenario(self) -> None:
        """Spec example: asyncpg UndefinedTableError at email.py:send_email.

        Verifies that two sessions failing with different table names but the same
        root cause ('relation "foo_123"' vs 'relation "foo_456"') produce the
        same fingerprint — the core deduplication requirement of the spec.
        """
        error_type = "asyncpg.exceptions.UndefinedTableError"
        call_site = "src/butlers/modules/email.py:send_email"

        result_123 = compute_fingerprint_from_report(
            error_type=error_type,
            error_message='relation "foo_123" does not exist',
            call_site=call_site,
            traceback_str=None,
        )
        result_456 = compute_fingerprint_from_report(
            error_type=error_type,
            error_message='relation "foo_456" does not exist',
            call_site=call_site,
            traceback_str=None,
        )
        # Verify structural correctness
        assert len(result_123.fingerprint) == 64
        assert result_123.severity == SEVERITY_CRITICAL
        # Same root cause (different dynamic table names) → same fingerprint
        assert result_123.fingerprint == result_456.fingerprint
        # Dynamic table name was collapsed to <ID>
        assert "123" not in result_123.sanitized_message
        assert "456" not in result_456.sanitized_message
        assert "<ID>" in result_123.sanitized_message


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_message_stable_fingerprint(self) -> None:
        """Empty messages produce a stable fingerprint using <empty> placeholder."""
        result_a = compute_fingerprint_from_report(
            error_type="builtins.ValueError",
            error_message="",
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
        )
        result_b = compute_fingerprint_from_report(
            error_type="builtins.ValueError",
            error_message="",
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
        )
        assert result_a.fingerprint == result_b.fingerprint
        assert result_a.sanitized_message == "<empty>"

    def test_none_traceback_gives_unknown_call_site(self) -> None:
        exc = RuntimeError("no traceback")
        result = compute_fingerprint(exc, None)
        assert result.call_site == "<unknown>:<unknown>"

    def test_exception_with_no_message(self) -> None:
        exc = ValueError()  # no message
        tb = _tb_for_exc(exc)
        result = compute_fingerprint(exc, tb)
        assert result.sanitized_message == "<empty>"
        assert len(result.fingerprint) == 64

    def test_cancellation_in_module_still_info(self) -> None:
        """CancelledError is always info regardless of call site."""
        result = compute_fingerprint_from_report(
            error_type="asyncio.CancelledError",
            error_message="",
            call_site="src/butlers/modules/email.py:send_email",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_INFO

    def test_keyboard_interrupt_by_type_string_is_info(self) -> None:
        result = compute_fingerprint_from_report(
            error_type="builtins.KeyboardInterrupt",
            error_message="",
            call_site="<unknown>:<unknown>",
            traceback_str=None,
        )
        assert result.severity == SEVERITY_INFO
