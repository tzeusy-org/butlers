"""Deterministic error fingerprinting for butler self-healing.

Computes a stable SHA-256 fingerprint from an exception, collapsing dynamic
values (UUIDs, timestamps, numeric IDs) so that semantically identical errors
map to the same key regardless of session-specific details.

Two input modes are supported so that both the spawner fallback path (raw
Python exception + traceback) and the module MCP tool path (structured string
fields from the reporting butler agent) produce identical fingerprints:

- ``compute_fingerprint(exc, tb)`` — raw exception path
- ``compute_fingerprint_from_report(...)`` — structured string path
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sys
import traceback
import types
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum sanitized message length before truncation.
_MAX_MESSAGE_LEN = 500

#: Path prefixes that indicate application code (vs. stdlib or third-party).
_APP_CODE_PREFIXES = ("src/butlers/", "roster/", "tests/")

#: Filename that is always treated as application code (conftest.py).
_APP_CODE_FILES = ("conftest.py",)

# Severity constants (lower = more severe)
SEVERITY_CRITICAL = 0
SEVERITY_HIGH = 1
SEVERITY_MEDIUM = 2
SEVERITY_LOW = 3
SEVERITY_INFO = 4

_SEVERITY_HINT_MAP: dict[str, int] = {
    "critical": SEVERITY_CRITICAL,
    "high": SEVERITY_HIGH,
    "medium": SEVERITY_MEDIUM,
    "low": SEVERITY_LOW,
    "info": SEVERITY_INFO,
}

# ---------------------------------------------------------------------------
# Sanitization patterns — applied in order
# ---------------------------------------------------------------------------

# UUID: 8-4-4-4-12 hex groups (case-insensitive)
_RE_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# ISO 8601 timestamps (date + optional time component)
# Covers: 2026-03-17, 2026-03-17T14:30:00, 2026-03-17T14:30:00Z, 2026-03-17 14:30:00
_RE_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)

# Numeric and alphanumeric IDs not already consumed by UUID/timestamp patterns.
# Matches two forms:
#   1. Standalone digit sequences surrounded by non-word characters: "row 123 missing"
#   2. Word-tokens that contain at least one digit: "foo_123", "session_42", "0c869f47"
#      These are dynamic identifiers (table names, hashes, session slugs) that must be
#      collapsed so that "relation foo_123 does not exist" and
#      "relation foo_456 does not exist" produce the same fingerprint.
#      (spec scenario: relation "foo_123" -> relation <ID>)
_RE_NUMERIC_ID = re.compile(r"\b\w*\d\w*\b")

# ---------------------------------------------------------------------------
# Severity rule patterns
# ---------------------------------------------------------------------------

# Exception type substrings / patterns that map to specific severities.
# Checked in order; first match wins.
_CRITICAL_TYPE_PATTERNS = (
    "asyncpg.PostgresError",
    "asyncpg.InterfaceError",
    "asyncpg.exceptions.",
    "CredentialStore",
    "CredentialError",
    "SecretError",
)

_HIGH_CALL_SITE_PREFIXES = ("src/butlers/core/runtimes/",)

_HIGH_FUNCTION_NAMES = (
    "read_system_prompt",
    "_build_env",
    "_resolve_provider_config",
)

_MODULE_CALL_SITE_PREFIXES = ("src/butlers/modules/",)

_LOW_FUNCTION_NAMES = (
    "fetch_memory_context",
    "store_session_episode",
)

#: Exception types that represent intentional cancellation (scored as info).
_CANCELLATION_TYPES = (asyncio.CancelledError, KeyboardInterrupt)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FingerprintResult:
    """Structured result of a fingerprint computation.

    Fields
    ------
    fingerprint:
        64-character lowercase SHA-256 hex string.
    severity:
        Integer severity score. 0=critical, 1=high, 2=medium, 3=low, 4=info.
    exception_type:
        Fully qualified exception class name (e.g. ``builtins.ValueError``).
    call_site:
        ``<relative_file_path>:<function_name>`` from the innermost app frame,
        or ``<unknown>:<unknown>`` if no app frame was found.
    sanitized_message:
        Error message with dynamic values replaced by typed placeholders,
        truncated to 500 characters.
    """

    fingerprint: str
    severity: int
    exception_type: str
    call_site: str
    sanitized_message: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fully_qualified_name(exc: BaseException) -> str:
    """Return the fully qualified class name of *exc*."""
    cls = type(exc)
    module = cls.__module__ or ""
    qualname = cls.__qualname__
    if module:
        return f"{module}.{qualname}"
    return qualname


def _sanitize_message(message: str) -> str:
    """Replace dynamic values with typed placeholders and truncate.

    Replacements are applied in this order so longer/more-specific patterns
    consume their tokens before simpler ones:

    1. UUIDs  → ``<UUID>``
    2. Timestamps → ``<TS>``
    3. Standalone numeric IDs → ``<ID>``

    The result is then truncated to ``_MAX_MESSAGE_LEN`` characters.
    """
    if not message:
        return "<empty>"

    text = _RE_UUID.sub("<UUID>", message)
    text = _RE_TIMESTAMP.sub("<TS>", text)
    text = _RE_NUMERIC_ID.sub("<ID>", text)

    if len(text) > _MAX_MESSAGE_LEN:
        text = text[:_MAX_MESSAGE_LEN]

    return text


def _is_app_frame(filename: str) -> bool:
    """Return True if *filename* belongs to application code."""
    # Normalise path separators
    norm = filename.replace("\\", "/")

    # Explicit file match (e.g. conftest.py at any depth)
    base = Path(norm).name
    if base in _APP_CODE_FILES:
        return True

    # Check against known app path prefixes (relative or absolute with them embedded)
    for prefix in _APP_CODE_PREFIXES:
        if prefix in norm:
            return True

    return False


def _extract_call_site(tb: types.TracebackType | None) -> str:
    """Walk traceback frames from innermost to outermost, return the first app frame.

    Returns ``<relative_file_path>:<function_name>`` with path relative to the
    repo root (strips any absolute prefix up to the first app-code prefix).

    Falls back to ``<unknown>:<unknown>`` if no app frame is found.
    """
    if tb is None:
        return "<unknown>:<unknown>"

    # Collect all frames (innermost last in the standard traceback walk)
    frames = traceback.extract_tb(tb)

    # Walk from innermost outward — innermost frame is the actual crash site
    for frame in reversed(frames):
        filename = frame.filename or ""
        if not _is_app_frame(filename):
            continue

        # Make path relative to repo root by stripping prefix up to app code
        relative = _relativize_path(filename)
        func_name = frame.name or "<unknown>"
        return f"{relative}:{func_name}"

    return "<unknown>:<unknown>"


def _relativize_path(filename: str) -> str:
    """Strip absolute prefix from *filename*, keeping from the first app-prefix onwards."""
    norm = filename.replace("\\", "/")

    for prefix in _APP_CODE_PREFIXES:
        idx = norm.find(prefix)
        if idx != -1:
            return norm[idx:]

    # conftest.py: keep just the filename
    base = Path(norm).name
    if base in _APP_CODE_FILES:
        return base

    return norm


def _extract_call_site_from_str(traceback_str: str) -> str:
    """Parse a traceback string and return the innermost app frame call site.

    Scans for ``File "<path>", line N, in <func>`` entries and returns the
    last (innermost) app frame found.
    """
    if not traceback_str:
        return "<unknown>:<unknown>"

    # Pattern: File "path", line N, in func_name
    pattern = re.compile(r'File "([^"]+)",\s*line \d+,\s*in (\S+)')
    matches = pattern.findall(traceback_str)

    # Walk from innermost (last entry) backwards
    for filename, func_name in reversed(matches):
        if _is_app_frame(filename):
            relative = _relativize_path(filename)
            return f"{relative}:{func_name}"

    return "<unknown>:<unknown>"


def _score_severity(
    exception_type: str,
    call_site: str,
    exc: BaseException | None = None,
) -> int:
    """Compute severity score from exception type and call site.

    Score table (0 = most severe):
    - 0 critical : DB errors, credential errors
    - 1 high     : runtime adapter, system prompt / config resolution
    - 2 medium   : module tool errors, unknown errors (default)
    - 3 low      : memory context helpers
    - 4 info     : intentional cancellation (CancelledError, KeyboardInterrupt)
    """
    # Cancellation — check live exception type first (fastest path)
    if exc is not None and isinstance(exc, _CANCELLATION_TYPES):
        return SEVERITY_INFO
    # Also check by type string for the structured path.
    # Include both the public alias ("asyncio.CancelledError") and the internal module
    # path ("asyncio.exceptions.CancelledError") that _fully_qualified_name() returns on
    # Python 3.12+, plus the concurrent.futures public and private paths.
    if exception_type in (
        "asyncio.CancelledError",
        "asyncio.exceptions.CancelledError",
        "builtins.KeyboardInterrupt",
        "concurrent.futures.CancelledError",
        "concurrent.futures._base.CancelledError",
    ):
        return SEVERITY_INFO

    # Critical: DB / credential errors
    for pattern in _CRITICAL_TYPE_PATTERNS:
        if exception_type.startswith(pattern) or pattern in exception_type:
            return SEVERITY_CRITICAL

    # Low: memory context helpers (check before high/medium to avoid misclassification)
    call_site_func = call_site.split(":")[-1] if ":" in call_site else call_site
    if call_site_func in _LOW_FUNCTION_NAMES:
        return SEVERITY_LOW

    # High: runtime adapter call sites
    for prefix in _HIGH_CALL_SITE_PREFIXES:
        if call_site.startswith(prefix):
            return SEVERITY_HIGH

    # High: specific infrastructure function names
    if call_site_func in _HIGH_FUNCTION_NAMES:
        return SEVERITY_HIGH

    # Medium: module tool errors
    for prefix in _MODULE_CALL_SITE_PREFIXES:
        if call_site.startswith(prefix):
            return SEVERITY_MEDIUM

    # Default
    return SEVERITY_MEDIUM


def _apply_severity_hint(auto_severity: int, severity_hint: str | None) -> int:
    """Apply agent severity hint as tiebreaker when auto score is the default (medium).

    The hint only overrides when the automatic scoring returned ``SEVERITY_MEDIUM``
    (the default). Specific rules (critical, high, low, info) always win.
    """
    if severity_hint is None:
        return auto_severity

    # Only applies when auto returned the default medium score
    if auto_severity != SEVERITY_MEDIUM:
        return auto_severity

    hint_normalized = severity_hint.strip().lower()
    if hint_normalized in _SEVERITY_HINT_MAP:
        return _SEVERITY_HINT_MAP[hint_normalized]

    return auto_severity


def _compute_hash(exception_type: str, call_site: str, sanitized_message: str) -> str:
    """Compute SHA-256 fingerprint over the structured tuple."""
    raw = f"{exception_type}||{call_site}||{sanitized_message}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_fingerprint(
    exc: BaseException,
    tb: types.TracebackType | None,
) -> FingerprintResult:
    """Compute a deterministic fingerprint from a raw Python exception.

    This is the **spawner fallback path** — called when the butler agent has
    crashed and could not self-report via the MCP tool.

    Parameters
    ----------
    exc:
        The caught exception object.
    tb:
        The associated traceback, or ``None`` if not available.

    Returns
    -------
    FingerprintResult
        Named tuple with fingerprint, severity, exception_type, call_site,
        and sanitized_message.
    """
    exception_type = _fully_qualified_name(exc)
    call_site = _extract_call_site(tb)
    raw_message = str(exc)
    sanitized_message = _sanitize_message(raw_message)

    severity = _score_severity(exception_type, call_site, exc=exc)
    fingerprint = _compute_hash(exception_type, call_site, sanitized_message)

    return FingerprintResult(
        fingerprint=fingerprint,
        severity=severity,
        exception_type=exception_type,
        call_site=call_site,
        sanitized_message=sanitized_message,
    )


def compute_fingerprint_from_report(
    error_type: str,
    error_message: str,
    call_site: str | None,
    traceback_str: str | None,
    severity_hint: str | None = None,
) -> FingerprintResult:
    """Compute a deterministic fingerprint from structured string fields.

    This is the **module MCP tool path** — called when the butler agent
    self-reports an error via the ``report_error`` tool with structured context.

    Parameters
    ----------
    error_type:
        Fully qualified exception class name (e.g. ``builtins.KeyError``).
        Used directly without further extraction.
    error_message:
        Raw error message string. Dynamic values are sanitized before hashing.
    call_site:
        Pre-extracted call site ``<file>:<function>``, or ``None`` to extract
        from *traceback_str*.
    traceback_str:
        Full traceback as a string. Used to extract *call_site* when the caller
        did not provide one directly. May be ``None``.
    severity_hint:
        Optional severity hint from the reporting butler agent
        (``"critical"``, ``"high"``, ``"medium"``, ``"low"``, ``"info"``).
        Used as a tiebreaker only when automatic scoring returns the default
        medium severity.

    Returns
    -------
    FingerprintResult
        Named tuple with fingerprint, severity, exception_type, call_site,
        and sanitized_message.
    """
    exception_type = error_type or "<unknown>"

    # Resolve call site
    if call_site and call_site not in ("<unknown>:<unknown>", ""):
        resolved_call_site = call_site
    elif traceback_str:
        resolved_call_site = _extract_call_site_from_str(traceback_str)
    else:
        resolved_call_site = "<unknown>:<unknown>"

    sanitized_message = _sanitize_message(error_message or "")

    auto_severity = _score_severity(exception_type, resolved_call_site, exc=None)
    severity = _apply_severity_hint(auto_severity, severity_hint)

    fingerprint = _compute_hash(exception_type, resolved_call_site, sanitized_message)

    return FingerprintResult(
        fingerprint=fingerprint,
        severity=severity,
        exception_type=exception_type,
        call_site=resolved_call_site,
        sanitized_message=sanitized_message,
    )


# ---------------------------------------------------------------------------
# Convenience re-export for current-exception usage
# ---------------------------------------------------------------------------


def compute_fingerprint_from_current_exc() -> FingerprintResult | None:
    """Compute fingerprint from ``sys.exc_info()`` if an active exception exists.

    Returns ``None`` if no exception is currently being handled.
    """
    exc_type, exc_value, exc_tb = sys.exc_info()
    if exc_value is None:
        return None
    return compute_fingerprint(exc_value, exc_tb)
