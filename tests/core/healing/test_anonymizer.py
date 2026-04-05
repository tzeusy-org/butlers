"""Tests for the healing anonymizer pipeline — condensed.

Covers:
- Credential redaction: API keys, DB URLs, JWTs, Bearer / Telegram tokens
- PII scrubbing: emails, phone numbers, IPv4/IPv6
- Localhost/loopback preservation
- Path normalization
- Hostname scrubbing
- Validation pass: residual pattern detection
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.core.healing.anonymizer import anonymize, validate_anonymized

pytestmark = pytest.mark.unit

REPO_ROOT = Path("/home/tze/gt/butlers/mayor/rig")


# ---------------------------------------------------------------------------
# Credential redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,not_in,tag",
    [
        (
            "key sk-ant-api03-abc123XYZ_extra-long-token here",
            "sk-ant-api03-abc123XYZ_extra-long-token",
            "[REDACTED-API-KEY]",
        ),
        ("AWS key: AKIA1234567890ABCDEF", "AKIA1234567890ABCDEF", "[REDACTED-API-KEY]"),
        (
            "sk-abcdefghijklmnopqrstuvwxyz123456",
            "sk-abcdefghijklmnopqrstuvwxyz123456",
            "[REDACTED-API-KEY]",
        ),
        ("postgresql://user:password@host:5432/dbname", "password", "[REDACTED-DB-URL]"),
        ("mysql://admin:s3cr3t@db.internal:3306/app", "s3cr3t", "[REDACTED-DB-URL]"),
        ("api_key=supersecretvalue123456", "supersecretvalue123456", "[REDACTED-API-KEY]"),
    ],
)
def test_credential_redaction(text, not_in, tag):
    result = anonymize(text, REPO_ROOT)
    assert not_in not in result
    assert tag in result


def test_token_redaction():
    """JWT, Telegram bot token, Bearer token (case-insensitive) all redacted."""
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    r = anonymize(f"token: {jwt}", REPO_ROOT)
    assert jwt not in r and "[REDACTED-JWT]" in r

    tg_url = "url: https://api.telegram.org/bot123456789:AAHabcXYZ-_tokenValue/sendMessage"
    r2 = anonymize(tg_url, REPO_ROOT)
    assert "AAHabcXYZ-_tokenValue" not in r2 and "/bot[REDACTED]/" in r2

    r3 = anonymize("Authorization: Bearer eyABCDEF1234567890", REPO_ROOT)
    assert "eyABCDEF1234567890" not in r3 and "Bearer [REDACTED]" in r3

    r4 = anonymize("authorization: bearer MySecretToken12345", REPO_ROOT)
    assert "MySecretToken12345" not in r4


# ---------------------------------------------------------------------------
# PII scrubbing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,not_in,tag",
    [
        ("user@example.com", "user@example.com", "[REDACTED-EMAIL]"),
        ("USER@EXAMPLE.COM", "USER@EXAMPLE.COM", "[REDACTED-EMAIL]"),
        ("user.name+tag@sub.domain.org", "user.name+tag@sub.domain.org", "[REDACTED-EMAIL]"),
        ("Call +1-555-123-4567", "555-123-4567", "[REDACTED-PHONE]"),
        ("Contact: (555) 123-4567", "555) 123-4567", "[REDACTED-PHONE]"),
        ("fax: 555.123.4567", "555.123.4567", "[REDACTED-PHONE]"),
        ("Connection from 192.168.1.100", "192.168.1.100", "[REDACTED-IP]"),
        ("Remote host: 203.0.113.42", "203.0.113.42", "[REDACTED-IP]"),
        ("Remote: 2001:db8::1", "2001:db8", "[REDACTED-IP]"),
    ],
)
def test_pii_scrubbing(text, not_in, tag):
    result = anonymize(text, REPO_ROOT)
    assert not_in not in result
    assert tag in result
    # Multiple emails all scrubbed
    assert anonymize("From: alice@example.com To: bob@corp.io", REPO_ROOT).count("[REDACTED-EMAIL]") == 2


@pytest.mark.parametrize(
    "text,preserved",
    [
        ("Listening on 127.0.0.1:8080", "127.0.0.1"),
        ("Connect to localhost:5432", "localhost"),
        ("Listening on ::1 port 8080", "::1"),
    ],
)
def test_localhost_preserved(text, preserved):
    result = anonymize(text, REPO_ROOT)
    assert preserved in result


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------


def test_path_normalization_and_hostname_scrubbing():
    """Repo paths normalized to relative; external paths redacted; internal hosts redacted."""
    # Repo path normalized to relative
    abs_path = str(REPO_ROOT / "src/butlers/core/spawner.py")
    result = anonymize(f"Error at {abs_path}", REPO_ROOT)
    assert str(REPO_ROOT) not in result
    assert "src/butlers/core/spawner.py" in result

    # External path redacted
    result2 = anonymize("Config file: /etc/passwd", REPO_ROOT)
    assert "/etc/passwd" not in result2
    assert "[REDACTED-PATH]" in result2

    # Internal hostname redacted; public domain preserved
    assert "[REDACTED-HOST]" in anonymize("Cannot reach db.internal.example.local", REPO_ROOT)
    r3 = anonymize("Connecting to github.com", REPO_ROOT)
    assert "github.com" in r3 and "[REDACTED-HOST]" not in r3


# ---------------------------------------------------------------------------
# Validation pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,violation_type",
    [
        ("Contact admin@corp.com for help", "email pattern"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abc123DEF456ghi789", "JWT pattern"),
        ("postgresql://user:secret@db.host/mydb", "credential URL pattern"),
        ("key: sk-ant-api03-realtoken12345678", "API key pattern"),
    ],
)
def test_validation_detects_residual_patterns(text, violation_type):
    is_clean, violations = validate_anonymized(text)
    assert is_clean is False
    assert any(violation_type in v for v in violations)
    # Clean text passes validation
    assert validate_anonymized("No sensitive data here.") == (True, [])
    # Multiple violations all reported
    bad_text = "alice@example.com and bob@example.com and 203.0.113.1 remain"
    is_clean2, violations2 = validate_anonymized(bad_text)
    assert is_clean2 is False
    assert len(violations2) >= 3
