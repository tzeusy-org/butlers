"""Tests for butlers.core.healing.fingerprint — condensed.

Covers:
- FingerprintResult fields and types
- Message sanitization: UUIDs, timestamps, numeric IDs, truncation
- Exception type extraction and chained exception handling
- Call site extraction from traceback objects and strings
- Severity scoring by error type and call site
- Severity hint overrides
- Fingerprint hash correctness
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


# ---------------------------------------------------------------------------
# FingerprintResult shape and dual-input parity
# ---------------------------------------------------------------------------


def test_fingerprint_result_fields_and_is_frozen():
    exc = ValueError("some error")
    result = compute_fingerprint(exc, _tb_for_exc(exc))
    assert isinstance(result, FingerprintResult)
    assert isinstance(result.fingerprint, str)
    assert len(result.fingerprint) == 64
    assert hasattr(result, "exception_type")
    assert hasattr(result, "call_site")
    assert hasattr(result, "severity")
    with pytest.raises((AttributeError, TypeError)):
        result.fingerprint = "modified"  # type: ignore[misc]


def test_same_fingerprint_from_both_paths():
    exc = RuntimeError("db connection refused")
    result1 = compute_fingerprint(exc, _tb_for_exc(exc))
    result2 = compute_fingerprint_from_report(
        error_type=result1.exception_type,
        error_message="db connection refused",
        call_site=result1.call_site,
        traceback_str=None,
    )
    assert result1.fingerprint == result2.fingerprint
    assert result1.severity == result2.severity


# ---------------------------------------------------------------------------
# Message sanitization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg,placeholder",
    [
        ("session 550e8400-e29b-41d4-a716-446655440000 not found", "<UUID>"),
        ("timeout at 2026-03-17T14:30:00Z", "<TS>"),
        ("event on 2026-03-17 was missed", "<TS>"),
        ("row 12345 missing", "<ID>"),
        ('relation "foo_123" does not exist', "<ID>"),
    ],
)
def test_sanitization_replaces_dynamic_values(msg, placeholder):
    assert placeholder in _sanitize_message(msg)


def test_sanitization_edge_cases():
    assert _sanitize_message("") == "<empty>"
    assert len(_sanitize_message("x" * 600)) == 500
    assert _sanitize_message("key missing_key not found") == "key missing_key not found"
    # Same relation name but different numeric suffix -> same sanitized output
    assert _sanitize_message('relation "foo_123" does not exist') == _sanitize_message(
        'relation "foo_456" does not exist'
    )


# ---------------------------------------------------------------------------
# Exception type and call site extraction
# ---------------------------------------------------------------------------


def test_exception_type_and_call_site_extraction():
    """Fully qualified type; outermost in chain; no-tb → unknown site; different sites differ."""
    # Fully qualified type
    exc = ValueError("invalid literal")
    result = compute_fingerprint(exc, _tb_for_exc(exc))
    assert result.exception_type == "builtins.ValueError"

    # Chained exception uses outermost
    try:
        try:
            raise ConnectionRefusedError("port 5432")
        except ConnectionRefusedError as cause:
            raise RuntimeError("failed") from cause
    except RuntimeError as exc2:
        chained = compute_fingerprint(exc2, sys.exc_info()[2])
    assert "RuntimeError" in chained.exception_type
    assert "ConnectionRefusedError" not in chained.exception_type

    # No traceback → unknown call site
    assert compute_fingerprint(ValueError("test"), None).call_site == "<unknown>:<unknown>"

    # Different call sites → different fingerprints
    r1 = compute_fingerprint_from_report("builtins.ValueError", "err", "file_a.py:fn_a", None)
    r2 = compute_fingerprint_from_report("builtins.ValueError", "err", "file_b.py:fn_b", None)
    assert r1.fingerprint != r2.fingerprint


# ---------------------------------------------------------------------------
# Severity scoring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_type,call_site,expected",
    [
        (
            "asyncpg.exceptions.UndefinedTableError",
            "src/butlers/modules/email.py:send",
            SEVERITY_CRITICAL,
        ),
        ("asyncpg.InterfaceError", "src/butlers/modules/email.py:send", SEVERITY_CRITICAL),
        (
            "butlers.credentials.CredentialStoreError",
            "src/butlers/core/spawner.py:_init",
            SEVERITY_CRITICAL,
        ),
        (
            "builtins.RuntimeError",
            "src/butlers/core/runtimes/claude_code.py:invoke",
            SEVERITY_HIGH,
        ),
        (
            "builtins.FileNotFoundError",
            "src/butlers/core/spawner.py:read_system_prompt",
            SEVERITY_HIGH,
        ),
        ("builtins.KeyError", "src/butlers/core/spawner.py:_build_env", SEVERITY_HIGH),
        ("builtins.TypeError", "src/butlers/modules/calendar.py:create_event", SEVERITY_MEDIUM),
        (
            "builtins.RuntimeError",
            "src/butlers/core/spawner.py:fetch_memory_context",
            SEVERITY_LOW,
        ),
        ("builtins.AttributeError", "<unknown>:<unknown>", SEVERITY_MEDIUM),
    ],
)
def test_severity_scoring_by_call_site(error_type, call_site, expected):
    result = compute_fingerprint_from_report(error_type, "error", call_site, None)
    assert result.severity == expected


@pytest.mark.parametrize(
    "error_type",
    [
        "asyncio.CancelledError",
        "asyncio.exceptions.CancelledError",
        "builtins.KeyboardInterrupt",
        "concurrent.futures.CancelledError",
    ],
)
def test_cancellation_errors_are_info_severity(error_type):
    result = compute_fingerprint_from_report(error_type, "", "<unknown>:<unknown>", None)
    assert result.severity == SEVERITY_INFO


@pytest.mark.parametrize("adapter_module", ["claude_code", "codex", "gemini", "opencode"])
def test_adapter_init_error_is_high(adapter_module):
    result = compute_fingerprint_from_report(
        "builtins.ValueError",
        "config error",
        f"src/butlers/core/runtimes/{adapter_module}.py:__init__",
        None,
    )
    assert result.severity == SEVERITY_HIGH


# ---------------------------------------------------------------------------
# Severity hint
# ---------------------------------------------------------------------------


def test_severity_hint_behavior():
    """Hint upgrades default; cannot override specific rule; invalid hint → auto."""
    # Upgrades default medium
    r1 = compute_fingerprint_from_report(
        "builtins.AttributeError", "attr missing", "<unknown>:<unknown>", None, severity_hint="high"
    )
    assert r1.severity == SEVERITY_HIGH

    # Cannot override specific rule (postgres error in module path → critical)
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


# ---------------------------------------------------------------------------
# Fingerprint hash correctness
# ---------------------------------------------------------------------------


def test_fingerprint_matches_manual_sha256():
    error_type = "asyncpg.exceptions.UndefinedTableError"
    call_site = "src/butlers/modules/email.py:send_email"
    raw_msg = "relation foo does not exist (row 123)"
    sanitized = "relation foo does not exist (row <ID>)"

    expected = hashlib.sha256(f"{error_type}||{call_site}||{sanitized}".encode()).hexdigest()
    result = compute_fingerprint_from_report(error_type, raw_msg, call_site, None)
    assert result.fingerprint == expected
