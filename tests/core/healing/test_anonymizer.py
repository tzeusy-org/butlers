"""Tests for the healing anonymizer pipeline.

Covers:
- Credential redaction: API keys, DB URLs, JWTs, Bearer / Telegram tokens
- PII scrubbing: emails (case-insensitive), phone numbers, IPv4/IPv6
- Localhost/loopback IP preservation
- Path normalization: repo-relative and non-repo redaction
- Environment/hostname scrubbing
- Validation pass: residual pattern detection with type + offset detail
- False positive guards: code identifiers, version strings, git SHAs
- Case sensitivity for email addresses
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.core.healing.anonymizer import anonymize, validate_anonymized

pytestmark = pytest.mark.unit

# Use a deterministic fake repo root for all path tests
REPO_ROOT = Path("/home/tze/gt/butlers/mayor/rig")


# ---------------------------------------------------------------------------
# Credential redaction
# ---------------------------------------------------------------------------


class TestCredentialRedaction:
    def test_anthropic_api_key_redacted(self):
        text = "Using key sk-ant-api03-abc123XYZ_extra-long-token here"
        result = anonymize(text, REPO_ROOT)
        assert "sk-ant-api03-abc123XYZ_extra-long-token" not in result
        assert "[REDACTED-API-KEY]" in result

    def test_aws_akia_key_redacted(self):
        text = "AWS key: AKIA1234567890ABCDEF"
        result = anonymize(text, REPO_ROOT)
        assert "AKIA1234567890ABCDEF" not in result
        assert "[REDACTED-API-KEY]" in result

    def test_aws_asia_key_redacted(self):
        text = "Temporary key: ASIA1234567890ABCDEF"
        result = anonymize(text, REPO_ROOT)
        assert "ASIA1234567890ABCDEF" not in result
        assert "[REDACTED-API-KEY]" in result

    def test_openai_api_key_redacted(self):
        text = "key = sk-abcdefghijklmnopqrstuvwxyz123456"
        result = anonymize(text, REPO_ROOT)
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result
        assert "[REDACTED-API-KEY]" in result

    def test_postgres_db_url_redacted(self):
        text = "postgresql://user:password@host:5432/dbname"
        result = anonymize(text, REPO_ROOT)
        assert "password" not in result
        assert "[REDACTED-DB-URL]" in result

    def test_mysql_db_url_redacted(self):
        text = "mysql://admin:s3cr3t@db.internal:3306/app"
        result = anonymize(text, REPO_ROOT)
        assert "s3cr3t" not in result
        assert "[REDACTED-DB-URL]" in result

    def test_jwt_token_redacted(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        result = anonymize(f"token: {jwt}", REPO_ROOT)
        assert jwt not in result
        assert "[REDACTED-JWT]" in result

    def test_telegram_bot_token_redacted(self):
        text = "url: https://api.telegram.org/bot123456789:AAHabcXYZ-_tokenValue/sendMessage"
        result = anonymize(text, REPO_ROOT)
        assert "AAHabcXYZ-_tokenValue" not in result
        assert "/bot[REDACTED]/" in result

    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer eyABCDEF1234567890"
        result = anonymize(text, REPO_ROOT)
        assert "eyABCDEF1234567890" not in result
        assert "Bearer [REDACTED]" in result

    def test_bearer_token_case_insensitive(self):
        text = "authorization: bearer MySecretToken12345"
        result = anonymize(text, REPO_ROOT)
        assert "MySecretToken12345" not in result
        assert "Bearer [REDACTED]" in result

    def test_generic_api_key_label_redacted(self):
        text = "api_key=supersecretvalue123456"
        result = anonymize(text, REPO_ROOT)
        assert "supersecretvalue123456" not in result
        assert "[REDACTED-API-KEY]" in result


# ---------------------------------------------------------------------------
# PII scrubbing — email
# ---------------------------------------------------------------------------


class TestEmailScrubbing:
    def test_email_scrubbed(self):
        text = "Error for user@example.com"
        result = anonymize(text, REPO_ROOT)
        assert "user@example.com" not in result
        assert "[REDACTED-EMAIL]" in result

    def test_email_case_insensitive_uppercase(self):
        text = "USER@EXAMPLE.COM"
        result = anonymize(text, REPO_ROOT)
        assert "USER@EXAMPLE.COM" not in result
        assert "[REDACTED-EMAIL]" in result

    def test_email_case_insensitive_mixed(self):
        text = "User@Example.COM contacted support"
        result = anonymize(text, REPO_ROOT)
        assert "User@Example.COM" not in result
        assert "[REDACTED-EMAIL]" in result

    def test_email_with_plus_and_dots(self):
        text = "user.name+tag@sub.domain.org"
        result = anonymize(text, REPO_ROOT)
        assert "user.name+tag@sub.domain.org" not in result
        assert "[REDACTED-EMAIL]" in result

    def test_multiple_emails_all_scrubbed(self):
        text = "From: alice@example.com To: bob@corp.io"
        result = anonymize(text, REPO_ROOT)
        assert "alice@example.com" not in result
        assert "bob@corp.io" not in result
        assert result.count("[REDACTED-EMAIL]") == 2


# ---------------------------------------------------------------------------
# PII scrubbing — phone numbers
# ---------------------------------------------------------------------------


class TestPhoneScrubbing:
    def test_us_phone_with_country_code(self):
        text = "Call +1-555-123-4567 for support"
        result = anonymize(text, REPO_ROOT)
        assert "555-123-4567" not in result
        assert "[REDACTED-PHONE]" in result

    def test_us_phone_parentheses_format(self):
        text = "Contact: (555) 123-4567"
        result = anonymize(text, REPO_ROOT)
        assert "555) 123-4567" not in result
        assert "[REDACTED-PHONE]" in result

    def test_phone_dot_format(self):
        text = "fax: 555.123.4567"
        result = anonymize(text, REPO_ROOT)
        assert "555.123.4567" not in result
        assert "[REDACTED-PHONE]" in result


# ---------------------------------------------------------------------------
# PII scrubbing — IP addresses
# ---------------------------------------------------------------------------


class TestIPAddressScrubbing:
    def test_private_ip_scrubbed(self):
        text = "Connection from 192.168.1.100"
        result = anonymize(text, REPO_ROOT)
        assert "192.168.1.100" not in result
        assert "[REDACTED-IP]" in result

    def test_public_ip_scrubbed(self):
        text = "Remote host: 203.0.113.42"
        result = anonymize(text, REPO_ROOT)
        assert "203.0.113.42" not in result
        assert "[REDACTED-IP]" in result

    def test_localhost_preserved(self):
        """127.0.0.1 must NOT be scrubbed."""
        text = "Listening on 127.0.0.1:8080"
        result = anonymize(text, REPO_ROOT)
        assert "127.0.0.1" in result
        assert "[REDACTED-IP]" not in result

    def test_localhost_word_preserved(self):
        """The string 'localhost' must NOT be scrubbed."""
        text = "Connect to localhost:5432"
        result = anonymize(text, REPO_ROOT)
        assert "localhost" in result

    def test_ipv6_scrubbed(self):
        text = "Remote: 2001:db8::1"
        result = anonymize(text, REPO_ROOT)
        assert "2001:db8" not in result
        assert "[REDACTED-IP]" in result

    def test_ipv6_loopback_preserved(self):
        """::1 must NOT be scrubbed."""
        text = "Listening on ::1 port 8080"
        result = anonymize(text, REPO_ROOT)
        assert "::1" in result


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------


class TestPathNormalization:
    def test_repo_path_normalized_to_relative(self):
        abs_path = str(REPO_ROOT / "src/butlers/core/spawner.py")
        text = f"Error at {abs_path}"
        result = anonymize(text, REPO_ROOT)
        assert str(REPO_ROOT) not in result
        assert "src/butlers/core/spawner.py" in result

    def test_non_repo_path_redacted(self):
        text = "Config file: /etc/passwd"
        result = anonymize(text, REPO_ROOT)
        assert "/etc/passwd" not in result
        assert "[REDACTED-PATH]" in result

    def test_home_dir_path_outside_repo_redacted(self):
        text = "Found: /home/someuser/secret/file.txt"
        result = anonymize(text, REPO_ROOT)
        assert "/home/someuser/secret/file.txt" not in result
        assert "[REDACTED-PATH]" in result

    def test_combined_path_and_email(self):
        abs_path = str(REPO_ROOT / "src/butlers/core/spawner.py")
        text = f"Error at {abs_path} for user@test.com"
        result = anonymize(text, REPO_ROOT)
        assert "src/butlers/core/spawner.py" in result
        assert "[REDACTED-EMAIL]" in result
        assert "user@test.com" not in result


# ---------------------------------------------------------------------------
# Environment / hostname scrubbing
# ---------------------------------------------------------------------------


class TestHostnameScrubbing:
    def test_internal_hostname_scrubbed(self):
        text = "Cannot reach db.internal.example.local"
        result = anonymize(text, REPO_ROOT)
        assert "db.internal.example.local" not in result
        assert "[REDACTED-HOST]" in result

    def test_multi_component_internal_hostname_scrubbed(self):
        text = "Host: postgres.prod.internal"
        result = anonymize(text, REPO_ROOT)
        assert "postgres.prod.internal" not in result

    def test_public_domain_not_scrubbed(self):
        text = "Connecting to github.com"
        result = anonymize(text, REPO_ROOT)
        assert "github.com" in result
        assert "[REDACTED-HOST]" not in result


# ---------------------------------------------------------------------------
# Validation pass
# ---------------------------------------------------------------------------


class TestValidateAnonymized:
    def test_clean_text_passes(self):
        is_clean, violations = validate_anonymized("No sensitive data here.")
        assert is_clean is True
        assert violations == []

    def test_residual_email_fails(self):
        text = "Contact admin@corp.com for help"
        is_clean, violations = validate_anonymized(text)
        assert is_clean is False
        assert any("email pattern" in v for v in violations)

    def test_residual_jwt_fails(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abc123DEF456ghi789"
        is_clean, violations = validate_anonymized(jwt)
        assert is_clean is False
        assert any("JWT pattern" in v for v in violations)

    def test_residual_db_url_fails(self):
        text = "postgresql://user:secret@db.host/mydb"
        is_clean, violations = validate_anonymized(text)
        assert is_clean is False
        assert any("credential URL pattern" in v for v in violations)

    def test_residual_api_key_fails(self):
        text = "key: sk-ant-api03-realtoken12345678"
        is_clean, violations = validate_anonymized(text)
        assert is_clean is False
        assert any("API key pattern" in v for v in violations)

    def test_multiple_violations_all_reported(self):
        text = "alice@example.com and bob@example.com and 203.0.113.1 remain"
        is_clean, violations = validate_anonymized(text)
        assert is_clean is False
        # Expect at least 3 violations (2 emails, 1 IP)
        assert len(violations) >= 3

    def test_violation_includes_offset(self):
        text = "Contact admin@corp.com"
        is_clean, violations = validate_anonymized(text)
        assert is_clean is False
        # Each violation description must include "offset"
        for v in violations:
            assert "offset" in v

    def test_violation_context_uses_match_placeholder(self):
        text = "Contact admin@corp.com now"
        is_clean, violations = validate_anonymized(text)
        assert is_clean is False
        # Context uses [MATCH] not the actual value
        for v in violations:
            if "email" in v:
                assert "[MATCH]" in v
                assert "admin@corp.com" not in v

    def test_validation_after_anonymize_is_clean(self):
        """Running anonymize then validate should produce a clean result."""
        raw = "user@test.com sk-ant-api03-longkeyhere postgresql://u:p@host/db"
        cleaned = anonymize(raw, REPO_ROOT)
        is_clean, violations = validate_anonymized(cleaned)
        assert is_clean is True, f"Residual violations: {violations}"

    def test_version_string_preserved_by_anonymize_passes_validate(self):
        """Version strings like 'version 1.2.3.4' must pass the full pipeline.

        anonymize() correctly skips 4-part version quads when preceded by a
        version keyword.  validate_anonymized() must not then flag them as
        residual IPv4 addresses — that would be a false-positive block on
        legitimate content.
        """
        raw = "Running version 1.2.3.4 of the package"
        cleaned = anonymize(raw, REPO_ROOT)
        assert "1.2.3.4" in cleaned, "anonymize() should preserve version strings"
        is_clean, violations = validate_anonymized(cleaned)
        assert is_clean is True, f"Pipeline false positive on version string: {violations}"

    def test_python_version_quad_pipeline_clean(self):
        """Python-prefixed version quads like 'Python 3.12.0.0' pass the pipeline."""
        raw = "Python 3.12.0.0 interpreter"
        cleaned = anonymize(raw, REPO_ROOT)
        assert "3.12.0.0" in cleaned
        is_clean, violations = validate_anonymized(cleaned)
        assert is_clean is True, f"Pipeline false positive on Python version: {violations}"

    def test_placeholders_not_flagged(self):
        """[REDACTED-*] placeholders inserted by anonymize should not trigger validation."""
        text = "[REDACTED-EMAIL] and [REDACTED-API-KEY] and [REDACTED-IP]"
        is_clean, violations = validate_anonymized(text)
        assert is_clean is True, f"Unexpected violations: {violations}"


# ---------------------------------------------------------------------------
# False positive guards
# ---------------------------------------------------------------------------


class TestFalsePositiveGuards:
    def test_code_identifier_user_email_not_scrubbed(self):
        """Variable names like 'user_email' must not trigger email scrubbing."""
        text = "self.user_email = config['smtp_host']"
        result = anonymize(text, REPO_ROOT)
        assert "user_email" in result
        assert "[REDACTED-EMAIL]" not in result

    def test_code_identifier_smtp_host_not_scrubbed_as_hostname(self):
        """config['smtp_host'] is a code identifier, not a hostname."""
        text = "config['smtp_host']"
        result = anonymize(text, REPO_ROOT)
        # The key name should survive
        assert "smtp_host" in result

    def test_version_string_not_treated_as_ip_four_parts(self):
        """Version strings like 'version 1.2.3.4' must NOT be treated as IPs."""
        text = "Running version 1.2.3.4 of the package"
        result = anonymize(text, REPO_ROOT)
        assert "1.2.3.4" in result
        assert "[REDACTED-IP]" not in result

    def test_python_version_not_treated_as_ip(self):
        """Python 3.12.0 must not be redacted (only 3 parts, not 4)."""
        text = "Python 3.12.0"
        result = anonymize(text, REPO_ROOT)
        assert "3.12.0" in result

    def test_git_sha_not_treated_as_api_key(self):
        """40-char hex git SHA-1 must NOT be treated as a credential."""
        sha = "7c42c7a8e98d3c4b5e6f7a8b9c0d1e2f3a4b5c6d"
        text = f"Commit: {sha}"
        result = anonymize(text, REPO_ROOT)
        assert sha in result
        assert "[REDACTED-API-KEY]" not in result

    def test_git_sha_labeled_not_treated_as_api_key(self):
        """40-char hex git SHA-1 labelled with api_key: must NOT be redacted.

        The generic API key guard must check if the token is a git SHA before
        redacting.  Without this guard, 'api_key: <sha>' would be erroneously
        redacted to 'api_key: [REDACTED-API-KEY]'.
        """
        sha = "7c42c7a8e98d3c4b5e6f7a8b9c0d1e2f3a4b5c6d"
        text = f"api_key: {sha}"
        result = anonymize(text, REPO_ROOT)
        assert sha in result
        assert "[REDACTED-API-KEY]" not in result

    def test_git_sha256_not_treated_as_api_key(self):
        """64-char hex git SHA-256 must NOT be treated as a credential."""
        sha256 = "a" * 64
        text = f"Object: {sha256}"
        result = anonymize(text, REPO_ROOT)
        assert sha256 in result

    def test_git_sha256_labeled_not_treated_as_api_key(self):
        """64-char hex git SHA-256 labelled with secret_key: must NOT be redacted."""
        sha256 = "b" * 64
        text = f"secret_key: {sha256}"
        result = anonymize(text, REPO_ROOT)
        assert sha256 in result
        assert "[REDACTED-API-KEY]" not in result

    def test_short_hex_string_not_treated_as_key(self):
        """Short hex identifiers (like 12-char fingerprints) should not be redacted."""
        text = "Fingerprint: abc123def456"
        result = anonymize(text, REPO_ROOT)
        assert "abc123def456" in result


# ---------------------------------------------------------------------------
# Function signature contract
# ---------------------------------------------------------------------------


class TestFunctionSignature:
    def test_anonymize_returns_str(self):
        result = anonymize("some text", REPO_ROOT)
        assert isinstance(result, str)

    def test_validate_anonymized_returns_tuple(self):
        result = validate_anonymized("clean text")
        assert isinstance(result, tuple)
        assert len(result) == 2
        is_clean, violations = result
        assert isinstance(is_clean, bool)
        assert isinstance(violations, list)

    def test_anonymize_example_from_spec(self):
        """Spec example: path + email normalized correctly."""
        path = str(REPO_ROOT / "src/butlers/core/spawner.py")
        text = f"Error at {path} for user@test.com"
        result = anonymize(text, REPO_ROOT)
        assert "src/butlers/core/spawner.py" in result
        assert "[REDACTED-EMAIL]" in result
        assert "user@test.com" not in result
        assert str(REPO_ROOT) not in result

    def test_validate_example_from_spec(self):
        """Spec example: validate detects residual email."""
        is_clean, violations = validate_anonymized("Contact admin@corp.com for help")
        assert is_clean is False
        assert any("email pattern" in v for v in violations)
