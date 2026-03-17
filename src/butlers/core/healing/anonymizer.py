"""Anonymizer pipeline for self-healing PR content.

Scrubs PII, credentials, environment-specific paths, and hostnames from
error context before inclusion in PR descriptions, commit messages, or
branch metadata.

Public API::

    anonymize(text: str, repo_root: Path) -> str
    validate_anonymized(text: str) -> tuple[bool, list[str]]
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Credential patterns
# ---------------------------------------------------------------------------

# Anthropic / Claude API keys: sk-ant-* prefix
_RE_ANTHROPIC_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")

# OpenAI-style API keys: sk- prefix (not already matched by Anthropic pattern)
_RE_OPENAI_KEY = re.compile(r"sk-[A-Za-z0-9]{20,}")

# AWS Access Key IDs: AKIA / ASIA + 16 uppercase alphanumeric chars
_RE_AWS_KEY = re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")

# Generic long API key patterns: apikey / api_key / key followed by = / : / space then a token
_RE_GENERIC_API_KEY = re.compile(
    r"(?i)(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token)"
    r"(?:\s*[=:]\s*|\s+)"
    r"([A-Za-z0-9_\-./]{16,})"
)

# Database connection URLs with embedded credentials
# postgresql://user:pass@host/db, mysql://user:pass@host:port/db, etc.
_RE_DB_URL = re.compile(
    r"(?i)(?:postgresql|postgres|mysql|mariadb|mssql|mongodb|redis|amqp|amqps)"
    r"(?:\+[a-z]+)?"
    r"://[^@\s]+@[^\s]+"
)

# JWT tokens: three base64url segments separated by dots, starting with eyJ
_RE_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

# Telegram bot token in URL paths: /bot<id>:<token>/
_RE_TELEGRAM_BOT_TOKEN = re.compile(r"/bot\d+:[A-Za-z0-9_-]+/")

# Bearer tokens in Authorization headers
_RE_BEARER_TOKEN = re.compile(r"Bearer\s+\S+", re.IGNORECASE)

# ---------------------------------------------------------------------------
# PII patterns
# ---------------------------------------------------------------------------

# Email addresses — case-insensitive; must not be preceded by a dot or word char
# (guards against variable names like self.user_email triggering a match)
# The local part cannot start with a dot.
_RE_EMAIL = re.compile(
    r"(?<![.\w])[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{2,}\.[A-Za-z]{2,}",
    re.IGNORECASE,
)

# Phone numbers: +1-555-123-4567, (555) 123-4567, 555.123.4567, etc.
# Two forms:
#   1. Optional country-code + area-code(parens or digits) + 7-digit local number (3+4)
#   2. Full 10-digit: NXX-NXX-XXXX
_RE_PHONE = re.compile(
    r"(?<!\d)"
    r"(?:"
    # Form 1: optional country code + parenthesised area code + 3-4 local
    r"(?:\+\d{1,3}[\s\-])?"
    r"\(\d{1,4}\)[\s\-]?"
    r"\d{3}[\s.\-]\d{4}"
    r"|"
    # Form 2: optional country code + all-digit 3+3+4 format (no parens)
    r"(?:\+\d{1,3}[\s\-])?"
    r"\d{3}[\s.\-]\d{3}[\s.\-]\d{4}"
    r")"
    r"(?!\d)"
)

# IPv4 addresses — NOT localhost (127.0.0.1) or private loopback.
# We use a simple 4-octet pattern; false-positive filtering is done in _scrub_ipv4().
_RE_IPV4 = re.compile(
    r"\b"
    r"(?!127\.\d+\.\d+\.\d+)"
    r"(?!0\.0\.0\.0)"
    r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})"
    r"\b"
)

# IPv6 addresses — full and compressed notation.
# Loopback (::1) is filtered out in _scrub_pii() after matching.
_RE_IPV6 = re.compile(
    r"(?:"
    r"[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){7}"  # full 8-group form
    r"|"
    r"[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6}::"  # N groups :: trailing
    r"[0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{1,4}){0,6}"
    r"|"
    r"::(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6})?"  # :: alone or :: M groups
    r")"
)

# ---------------------------------------------------------------------------
# False positive guards
# ---------------------------------------------------------------------------

# Patterns that look like credentials but are actually harmless:
# 40-hex-char git SHA-1 hashes (only lowercase hex — uppercase is not a git SHA)
_RE_GIT_SHA = re.compile(r"\b[0-9a-f]{40}\b")
# 64-hex-char git SHA-256 hashes
_RE_GIT_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")

# Version strings like "3.12.0" or "Python 3.12.0" — four dotted number groups.
# Used by both _scrub_ipv4 (via _is_preceded_by_version_keyword) and
# validate_anonymized to avoid false-positive IPv4 hits on version quads.
_RE_VERSION_QUAD = re.compile(r"\b\d+\.\d+\.\d+\.\d+\b")

# ---------------------------------------------------------------------------
# Path patterns
# ---------------------------------------------------------------------------

# Absolute filesystem paths: Unix-style /foo/bar or Windows-style C:\foo\bar.
# Only matches at start-of-line or after whitespace so that URL path components
# like the /sendMessage in "https://host/sendMessage" are not mistaken for
# filesystem paths.
_RE_ABS_PATH = re.compile(
    r"(?:(?<=\s)|^)"
    r"(?:/?(?:[A-Za-z]:)?/[^\s\"',;(){}\[\]<>]+)",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Environment scrubbing: hostnames
# ---------------------------------------------------------------------------

# Internal hostname patterns (at least two components, not localhost/loopback)
_RE_INTERNAL_HOSTNAME = re.compile(
    r"\b"
    r"(?!localhost\b)"
    r"(?![Ll]ocalhost\b)"
    r"(?!(?:25[0-5]|2[0-4]\d|\d{1,3})\.)"  # not a bare IP
    r"[a-zA-Z][a-zA-Z0-9\-]*"
    r"(?:\.[a-zA-Z][a-zA-Z0-9\-]*)+"
    r"\b"
)

# Allow-listed domain suffixes that are safe / public (not internal)
_SAFE_DOMAIN_SUFFIXES = (
    ".com",
    ".org",
    ".net",
    ".io",
    ".dev",
    ".ai",
    ".gov",
    ".edu",
    ".py",  # python file extensions like foo.py
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".txt",
    ".log",
    ".sh",
    ".sql",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".rs",
    ".go",
    ".rb",
)

# Well-known public domains that should never be redacted
_SAFE_DOMAINS = frozenset(
    {
        "example.com",
        "github.com",
        "gitlab.com",
        "pypi.org",
        "python.org",
        "googleapis.com",
        "anthropic.com",
        "openai.com",
        "huggingface.co",
        "amazonaws.com",
        "cloudflare.com",
        "fastapi.tiangolo.com",
        "docs.python.org",
        "readthedocs.io",
        "postgresql.org",
        "redis.io",
        "docker.com",
        "ubuntu.com",
    }
)


def _is_safe_hostname(hostname: str) -> bool:
    """Return True if the hostname is safe to include (not internal/environment-specific)."""
    lower = hostname.lower()
    # Allowed by explicit whitelist
    if lower in _SAFE_DOMAINS:
        return True
    for domain in _SAFE_DOMAINS:
        if lower.endswith("." + domain):
            return True
    # Allowed by suffix heuristic (file extensions, public TLDs)
    for suffix in _SAFE_DOMAIN_SUFFIXES:
        if lower.endswith(suffix):
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_valid_ipv4_octet(value: str) -> bool:
    try:
        return 0 <= int(value) <= 255
    except ValueError:
        return False


def _is_preceded_by_version_keyword(text: str, start: int) -> bool:
    """Return True if the character before *start* suggests a version string context."""
    # Look at up to 10 chars before the match for version/v prefix
    prefix = text[max(0, start - 10) : start].lower()
    return bool(re.search(r"(?:version\s*|python\s*|v)\d*\.?\s*$", prefix))


def _scrub_ipv4(text: str) -> str:
    """Replace non-loopback IPv4 addresses, skipping version-string quads."""

    def _replace_ipv4(m: re.Match[str]) -> str:
        # Skip if preceded by a version keyword (e.g. "version 1.2.3.4", "v1.2.3.4")
        if _is_preceded_by_version_keyword(text, m.start()):
            return m.group(0)

        octets = (m.group(1), m.group(2), m.group(3), m.group(4))
        if not all(_is_valid_ipv4_octet(o) for o in octets):
            return m.group(0)

        # Preserve localhost / loopback (127.x.x.x already excluded in regex)
        if int(octets[0]) == 127:
            return m.group(0)

        return "[REDACTED-IP]"

    return _RE_IPV4.sub(_replace_ipv4, text)


def _scrub_credentials(text: str) -> str:
    """Apply credential redaction in priority order."""
    # Highest priority: long-form tokens that might overlap with shorter patterns
    text = _RE_JWT.sub("[REDACTED-JWT]", text)
    text = _RE_ANTHROPIC_KEY.sub("[REDACTED-API-KEY]", text)
    text = _RE_AWS_KEY.sub("[REDACTED-API-KEY]", text)
    text = _RE_DB_URL.sub("[REDACTED-DB-URL]", text)
    text = _RE_TELEGRAM_BOT_TOKEN.sub("/bot[REDACTED]/", text)
    text = _RE_BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    # OpenAI-style (sk- …) after Anthropic to avoid double-scrubbing
    text = _RE_OPENAI_KEY.sub("[REDACTED-API-KEY]", text)

    # Generic labelled keys — replace whole match, but guard against git SHAs
    def _redact_generic(m: re.Match[str]) -> str:
        token = m.group(1)
        # Git SHA-1 (40 hex) and SHA-256 (64 hex) are not credentials even when labelled
        if _RE_GIT_SHA.fullmatch(token) or _RE_GIT_SHA256.fullmatch(token):
            return m.group(0)
        # Preserve label, replace token
        prefix = m.group(0)[: m.start(1) - m.start()]
        return prefix + "[REDACTED-API-KEY]"

    text = _RE_GENERIC_API_KEY.sub(_redact_generic, text)
    return text


def _scrub_ipv6(text: str) -> str:
    """Replace non-loopback IPv6 addresses."""

    def _replace_ipv6(m: re.Match[str]) -> str:
        addr = m.group(0)
        # Preserve loopback ::1 and all-zeros ::
        normalized = addr.strip()
        if normalized in ("::1", "::"):
            return addr
        return "[REDACTED-IP]"

    return _RE_IPV6.sub(_replace_ipv6, text)


def _scrub_pii(text: str) -> str:
    """Scrub email, phone, and IP addresses."""
    text = _RE_EMAIL.sub("[REDACTED-EMAIL]", text)
    text = _RE_PHONE.sub("[REDACTED-PHONE]", text)
    text = _scrub_ipv4(text)
    text = _scrub_ipv6(text)
    return text


def _normalize_paths(text: str, repo_root: Path) -> str:
    """Replace absolute paths with repo-relative paths or [REDACTED-PATH]."""
    repo_str = str(repo_root.resolve())

    def _replace_path(m: re.Match[str]) -> str:
        raw = m.group(0)
        try:
            resolved = str(Path(raw).resolve())
        except (ValueError, OSError):
            return "[REDACTED-PATH]"

        if resolved.startswith(repo_str):
            rel = resolved[len(repo_str) :].lstrip("/")
            return rel if rel else "."
        # Non-repo absolute paths are redacted
        if raw.startswith("/") or (len(raw) > 2 and raw[1] == ":"):
            return "[REDACTED-PATH]"
        return raw

    return _RE_ABS_PATH.sub(_replace_path, text)


def _scrub_hostnames(text: str) -> str:
    """Replace internal hostnames with [REDACTED-HOST], preserving safe/public ones."""

    def _replace_hostname(m: re.Match[str]) -> str:
        hostname = m.group(0)
        if _is_safe_hostname(hostname):
            return hostname
        return "[REDACTED-HOST]"

    return _RE_INTERNAL_HOSTNAME.sub(_replace_hostname, text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def anonymize(text: str, repo_root: Path) -> str:
    """Apply all scrubbing transforms to *text*.

    Transforms applied in order:
    1. Credential redaction (API keys, DB URLs, JWTs, Bearer / Telegram tokens)
    2. PII scrubbing (email, phone, IPv4, IPv6) — localhost/loopback preserved
    3. Path normalization (absolute → repo-relative; non-repo → [REDACTED-PATH])
    4. Hostname scrubbing (internal hostnames → [REDACTED-HOST])

    Parameters
    ----------
    text:
        Raw text to sanitize (e.g. error message, traceback, PR body).
    repo_root:
        Absolute path to the repository root.  Used to relativize repo paths.

    Returns
    -------
    str
        Sanitized text with all sensitive data replaced by typed placeholders.
    """
    text = _scrub_credentials(text)
    text = _scrub_pii(text)
    text = _normalize_paths(text, repo_root)
    text = _scrub_hostnames(text)
    return text


# ---------------------------------------------------------------------------
# Validation pass
# ---------------------------------------------------------------------------

# Patterns used for post-anonymization residual scan.
# These are intentionally more liberal than the scrubbing patterns —
# they catch anything that slipped through.
_VALIDATION_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "email pattern",
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", re.IGNORECASE),
    ),
    (
        "JWT pattern",
        re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    ),
    (
        "credential URL pattern",
        re.compile(
            r"(?i)(?:postgresql|postgres|mysql|mariadb|mssql|mongodb|redis|amqp)"
            r"(?:\+[a-z]+)?://[^@\s]+@"
        ),
    ),
    (
        "API key pattern",
        re.compile(r"(?:sk-ant-|sk-)[A-Za-z0-9_\-]{16,}|(?:AKIA|ASIA)[A-Z0-9]{16}"),
    ),
    (
        "IPv4 address pattern",
        re.compile(
            r"\b(?!127\.\d+\.\d+\.\d+)(?!0\.0\.0\.0)"
            r"(?:\d{1,3}\.){3}\d{1,3}\b"
        ),
    ),
]

# Characters surrounding match to include in context (without revealing the match)
_CONTEXT_CHARS = 5


def validate_anonymized(text: str) -> tuple[bool, list[str]]:
    """Scan anonymized text for residual sensitive patterns.

    Performs a second-pass validation after ``anonymize()`` to catch any
    patterns the scrubbing step missed.  Violations are described without
    including the actual sensitive value.

    Parameters
    ----------
    text:
        Text that has already been passed through ``anonymize()``.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if no violations found, otherwise
        ``(False, list_of_violation_descriptions)`` where each description
        includes: pattern type detected, character offset, and surrounding
        anonymized context (5 chars before/after the match replaced by
        ``[MATCH]``).
    """
    violations: list[str] = []

    for pattern_type, pattern in _VALIDATION_RULES:
        for m in pattern.finditer(text):
            start, end = m.start(), m.end()

            # IPv4 validation: skip version-string quads that anonymize() correctly
            # preserved (e.g. "version 1.2.3.4", "v2.1.0.5").  These match the IPv4
            # pattern but are not real IP addresses — flagging them would produce a
            # false-positive block that prevents PR creation for harmless content.
            if pattern_type == "IPv4 address pattern" and _is_preceded_by_version_keyword(
                text, start
            ):
                continue

            # Build surrounding context without revealing the match value
            ctx_before = text[max(0, start - _CONTEXT_CHARS) : start]
            ctx_after = text[end : end + _CONTEXT_CHARS]
            context = f"{ctx_before}[MATCH]{ctx_after}"

            description = (
                f"{pattern_type} detected at offset {start}-{end} "
                f"(len={end - start}); context: {context!r}"
            )
            violations.append(description)

    is_clean = len(violations) == 0
    return is_clean, violations
