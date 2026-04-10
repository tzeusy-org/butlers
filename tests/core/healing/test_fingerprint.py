"""Tests for butlers.core.healing.fingerprint — condensed.

Covers:
- FingerprintResult fields, frozen, and dual-input parity
- Sanitization: UUIDs, timestamps, numeric IDs, empty, truncation
- Exception type: fully qualified, chained (outermost), no-tb unknown site
- Severity scoring by error type and call site (critical/high/medium/low/info)
- Severity hint override behavior
- Fingerprint hash matches manual SHA256
"""

from __future__ import annotations

import hashlib
import sys
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

pytestmark = pytest.mark.unit


def _tb_for_exc(exc: BaseException) -> types.TracebackType:
    try:
        raise exc
    except type(exc):
        return sys.exc_info()[2]  # type: ignore[return-value]


def test_fingerprint_result_fields_sanitization_and_parity():
    """FingerprintResult frozen, 64-char hash, dual-input parity; sanitization removes UUIDs/timestamps/IDs."""
    exc = ValueError("some error")
    result = compute_fingerprint(exc, _tb_for_exc(exc))
    assert isinstance(result, FingerprintResult) and len(result.fingerprint) == 64
    assert hasattr(result, "exception_type") and hasattr(result, "call_site")
    with pytest.raises((AttributeError, TypeError)):
        result.fingerprint = "modified"  # type: ignore[misc]
    result2 = compute_fingerprint_from_report(
        error_type=result.exception_type,
        error_message="some error",
        call_site=result.call_site,
        traceback_str=None,
    )
    assert result.fingerprint == result2.fingerprint and result.severity == result2.severity

    # Sanitization
    assert "<UUID>" in _sanitize_message("session 550e8400-e29b-41d4-a716-446655440000 not found")
    assert "<TS>" in _sanitize_message("timeout at 2026-03-17T14:30:00Z")
    assert "<TS>" in _sanitize_message("event on 2026-03-17 was missed")
    assert "<ID>" in _sanitize_message("row 12345 missing")
    assert "<ID>" in _sanitize_message('relation "foo_123" does not exist')
    assert _sanitize_message("") == "<empty>"
    assert len(_sanitize_message("x" * 600)) == 500
    assert _sanitize_message('relation "foo_123" does not exist') == _sanitize_message(
        'relation "foo_456" does not exist'
    )


def test_exception_type_and_call_site():
    """Fully qualified type; outermost in chain; no-tb → unknown site; different sites differ."""
    exc = ValueError("invalid literal")
    r = compute_fingerprint(exc, _tb_for_exc(exc))
    assert r.exception_type == "builtins.ValueError"

    try:
        try:
            raise ConnectionRefusedError("port 5432")
        except ConnectionRefusedError as cause:
            raise RuntimeError("failed") from cause
    except RuntimeError as exc2:
        chained = compute_fingerprint(exc2, sys.exc_info()[2])
    assert "RuntimeError" in chained.exception_type
    assert "ConnectionRefusedError" not in chained.exception_type

    assert compute_fingerprint(ValueError("test"), None).call_site == "<unknown>:<unknown>"

    r1 = compute_fingerprint_from_report("builtins.ValueError", "err", "file_a.py:fn_a", None)
    r2 = compute_fingerprint_from_report("builtins.ValueError", "err", "file_b.py:fn_b", None)
    assert r1.fingerprint != r2.fingerprint


def test_severity_scoring():
    """Error type and call site map to expected severity tier."""

    def s(error_type: str, call_site: str) -> int:
        return compute_fingerprint_from_report(error_type, "error", call_site, None).severity

    assert (
        s("asyncpg.exceptions.UndefinedTableError", "src/butlers/modules/email.py:send")
        == SEVERITY_CRITICAL
    )
    assert s("asyncpg.InterfaceError", "src/butlers/modules/email.py:send") == SEVERITY_CRITICAL
    assert (
        s("butlers.credentials.CredentialStoreError", "src/butlers/core/spawner.py:_init")
        == SEVERITY_CRITICAL
    )
    assert (
        s("builtins.RuntimeError", "src/butlers/core/runtimes/claude_code.py:invoke")
        == SEVERITY_HIGH
    )
    assert (
        s("builtins.FileNotFoundError", "src/butlers/core/spawner.py:read_system_prompt")
        == SEVERITY_HIGH
    )
    assert s("builtins.KeyError", "src/butlers/core/spawner.py:_build_env") == SEVERITY_HIGH
    assert (
        s("builtins.TypeError", "src/butlers/modules/calendar.py:create_event") == SEVERITY_MEDIUM
    )
    assert (
        s("builtins.RuntimeError", "src/butlers/core/spawner.py:fetch_memory_context")
        == SEVERITY_LOW
    )
    assert s("asyncio.CancelledError", "<unknown>:<unknown>") == SEVERITY_INFO
    assert s("builtins.KeyboardInterrupt", "<unknown>:<unknown>") == SEVERITY_INFO


def test_severity_hint_and_hash():
    """Hint upgrades default; can't override specific rule; invalid falls back; hash matches SHA256."""
    # Hint upgrades default medium to high
    r1 = compute_fingerprint_from_report(
        "builtins.AttributeError", "attr missing", "<unknown>:<unknown>", None, severity_hint="high"
    )
    assert r1.severity == SEVERITY_HIGH

    # Specific rule wins over hint (postgres error in module → critical despite low hint)
    r2 = compute_fingerprint_from_report(
        "asyncpg.exceptions.PostgresError",
        "lost",
        "src/butlers/modules/email.py:send",
        None,
        severity_hint="low",
    )
    assert r2.severity == SEVERITY_CRITICAL

    # Invalid hint falls back to auto
    r3 = compute_fingerprint_from_report(
        "builtins.AttributeError",
        "attr missing",
        "<unknown>:<unknown>",
        None,
        severity_hint="urgent",
    )
    assert r3.severity == SEVERITY_MEDIUM

    # Fingerprint matches manual SHA256
    error_type = "asyncpg.exceptions.UndefinedTableError"
    call_site = "src/butlers/modules/email.py:send_email"
    raw_msg = "relation foo does not exist (row 123)"
    sanitized = "relation foo does not exist (row <ID>)"
    expected = hashlib.sha256(f"{error_type}||{call_site}||{sanitized}".encode()).hexdigest()
    result = compute_fingerprint_from_report(error_type, raw_msg, call_site, None)
    assert result.fingerprint == expected
