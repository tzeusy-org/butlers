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

from butlers.core.healing import anonymizer
from butlers.core.healing.anonymizer import anonymize, sanitize_labels, validate_anonymized

pytestmark = pytest.mark.unit

REPO_ROOT = Path("/home/tze/gt/butlers/mayor/rig")


def test_credential_redaction():
    """API keys, DB URLs, JWTs, Bearer and Telegram tokens are redacted."""

    def r(text: str) -> str:
        return anonymize(text, REPO_ROOT)

    assert "sk-ant-api03-abc123XYZ_extra-long-token" not in r(
        "key sk-ant-api03-abc123XYZ_extra-long-token here"
    )
    assert "[REDACTED-API-KEY]" in r("key sk-ant-api03-abc123XYZ_extra-long-token here")
    assert "AKIA1234567890ABCDEF" not in r("AWS key: AKIA1234567890ABCDEF")
    assert "[REDACTED-API-KEY]" in r("AWS key: AKIA1234567890ABCDEF")
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in r("sk-abcdefghijklmnopqrstuvwxyz123456")
    assert "password" not in r("postgresql://user:password@host:5432/dbname")
    assert "[REDACTED-DB-URL]" in r("postgresql://user:password@host:5432/dbname")
    assert "s3cr3t" not in r("mysql://admin:s3cr3t@db.internal:3306/app")
    assert "supersecretvalue123456" not in r("api_key=supersecretvalue123456")
    # JWT redaction
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    r_jwt = r(f"token: {jwt}")
    assert jwt not in r_jwt and "[REDACTED-JWT]" in r_jwt
    # Telegram bot token redacted
    r_tg = r("url: https://api.telegram.org/bot123456789:AAHabcXYZ-_tokenValue/sendMessage")
    assert "AAHabcXYZ-_tokenValue" not in r_tg and "/bot[REDACTED]/" in r_tg
    # Bearer token redacted
    r_bearer = r("Authorization: Bearer eyABCDEF1234567890")
    assert "eyABCDEF1234567890" not in r_bearer and "Bearer [REDACTED]" in r_bearer


def test_pii_scrubbing():
    """Emails, phone numbers, and IPs (v4/v6) are scrubbed; localhost preserved."""

    def r(text: str) -> str:
        return anonymize(text, REPO_ROOT)

    # Emails
    redacted_email = r("user@example.com")
    assert "user@example.com" not in redacted_email and "[REDACTED-EMAIL]" in redacted_email
    assert "USER@EXAMPLE.COM" not in r("USER@EXAMPLE.COM")
    assert "user.name+tag@sub.domain.org" not in r("user.name+tag@sub.domain.org")
    assert r("From: alice@example.com To: bob@corp.io").count("[REDACTED-EMAIL]") == 2
    # Phone numbers
    redacted_phone = r("Call +1-555-123-4567")
    assert "555-123-4567" not in redacted_phone and "[REDACTED-PHONE]" in redacted_phone
    assert "555) 123-4567" not in r("Contact: (555) 123-4567")
    assert "555.123.4567" not in r("fax: 555.123.4567")
    # IPv4/IPv6
    assert "192.168.1.100" not in r("Connection from 192.168.1.100") and "[REDACTED-IP]" in r(
        "Connection from 192.168.1.100"
    )
    assert "203.0.113.42" not in r("Remote host: 203.0.113.42")
    assert "2001:db8" not in r("Remote: 2001:db8::1")
    # Localhost preserved
    assert "127.0.0.1" in r("Listening on 127.0.0.1:8080")
    assert "localhost" in r("Connect to localhost:5432")
    assert "::1" in r("Listening on ::1 port 8080")


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


def test_validation_detects_residual_patterns():
    """validate_anonymized flags un-scrubbed PII; clean text passes; multiple violations reported."""
    cases = [
        ("Contact admin@corp.com for help", "email pattern"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abc123DEF456ghi789", "JWT pattern"),
        ("postgresql://user:secret@db.host/mydb", "credential URL pattern"),
        ("key: sk-ant-api03-realtoken12345678", "API key pattern"),
    ]
    for text, violation_type in cases:
        is_clean, violations = validate_anonymized(text)
        assert is_clean is False, f"expected dirty for: {text}"
        assert any(violation_type in v for v in violations)
    # Clean text passes validation
    assert validate_anonymized("No sensitive data here.") == (True, [])
    # Multiple violations all reported
    bad_text = "alice@example.com and bob@example.com and 203.0.113.1 remain"
    is_clean2, violations2 = validate_anonymized(bad_text)
    assert is_clean2 is False
    assert len(violations2) >= 3


# ---------------------------------------------------------------------------
# Label sanitization gate (externally-visible GitHub PR/issue field)
# ---------------------------------------------------------------------------


def test_sanitize_labels_passes_clean_labels_unchanged():
    """Clean labels are returned unchanged with no violations."""
    sanitized, violations = sanitize_labels(["self-healing", "automated", "bug"], REPO_ROOT)
    assert sanitized == ["self-healing", "automated", "bug"]
    assert violations == []


def test_sanitize_labels_empty_input_is_clean():
    """No labels => nothing to sanitize, no violations (gate trivially passes)."""
    assert sanitize_labels([], REPO_ROOT) == ([], [])


def test_sanitize_labels_redacts_embedded_sensitive_value():
    """A label carrying a synthetic email is scrubbed; the raw value never survives."""
    # Synthetic placeholder — NOT real PII.
    sanitized, violations = sanitize_labels(["owner-tester@synthetic.example"], REPO_ROOT)
    assert violations == []
    assert "tester@synthetic.example" not in sanitized[0]
    assert "REDACTED" in sanitized[0]


def test_sanitize_labels_blocks_residual_sensitive_content(monkeypatch: pytest.MonkeyPatch):
    """Fail-closed: if a label still trips validation after scrubbing, report a violation
    WITHOUT echoing the raw sensitive value in the violation text."""
    # Force the scrub step to be a no-op so the synthetic secret reaches the
    # validation backstop unchanged (models a pattern the scrubber missed).
    monkeypatch.setattr(anonymizer, "anonymize", lambda text, _repo: text)

    secret_label = "leak-tester@synthetic.example"
    sanitized, violations = sanitize_labels([secret_label], REPO_ROOT)

    assert violations, "expected the validation backstop to flag residual sensitive content"
    # Audit/log safety: the raw sensitive value must not appear in the violation text.
    assert all("tester@synthetic.example" not in v for v in violations)
