"""Tests for Google OAuth startup gating (butlers.startup_guard).

Covers:
- check_google_credentials() returns ok=True when env vars are present
- check_google_credentials() returns ok=False when env vars are missing
- check_google_credentials() with BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON
- check_google_credentials() with connector env file (simulated via env)
- require_google_credentials_or_exit() exits when credentials missing
- require_google_credentials_or_exit() does NOT exit when credentials present
- Error messages are actionable (contain bootstrap instructions)
- Secret values are not echoed back in output
"""

from __future__ import annotations

import json
import unittest.mock as mock

import pytest

from butlers.startup_guard import (
    _CALENDAR_JSON_ENV,
    _CREDENTIAL_FIELD_ALIASES,
    GoogleCredentialCheckResult,
    check_google_credentials,
    check_google_credentials_with_db,
    require_google_credentials_or_exit,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _all_google_env_vars_cleared() -> dict[str, str]:
    """Return a dict with all Google credential env vars set to empty."""
    all_vars = [v for _, aliases in _CREDENTIAL_FIELD_ALIASES for v in aliases]
    all_vars.append(_CALENDAR_JSON_ENV)
    return {v: "" for v in all_vars}


FULL_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "gid-123",
    "GOOGLE_OAUTH_CLIENT_SECRET": "gsecret-xyz",
    "GOOGLE_REFRESH_TOKEN": "1//gtoken-abc",
}

GMAIL_ENV = {
    "GMAIL_CLIENT_ID": "gmail-id-456",
    "GMAIL_CLIENT_SECRET": "gmail-secret",
    "GMAIL_REFRESH_TOKEN": "1//gmail-token",
}


# ---------------------------------------------------------------------------
# check_google_credentials — credential present scenarios
# ---------------------------------------------------------------------------


class TestCheckGoogleCredentialsPresent:
    def test_returns_ok_with_google_env_vars(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", {**clear, **FULL_ENV}):
            result = check_google_credentials()
        assert result.ok is True
        assert result.missing_vars == []

    def test_returns_ok_with_gmail_env_vars(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", {**clear, **GMAIL_ENV}):
            result = check_google_credentials()
        assert result.ok is True

    def test_returns_ok_with_calendar_json_blob(self) -> None:
        blob = json.dumps(
            {
                "client_id": "cal-id",
                "client_secret": "cal-secret",
                "refresh_token": "cal-token",
            }
        )
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", {**clear, _CALENDAR_JSON_ENV: blob}):
            result = check_google_credentials()
        assert result.ok is True

    def test_returns_ok_with_mixed_google_and_gmail_vars(self) -> None:
        clear = _all_google_env_vars_cleared()
        mixed = {
            "GOOGLE_OAUTH_CLIENT_ID": "gid",
            "GMAIL_CLIENT_SECRET": "gsecret",
            "GOOGLE_REFRESH_TOKEN": "gtoken",
        }
        with mock.patch.dict("os.environ", {**clear, **mixed}):
            result = check_google_credentials()
        assert result.ok is True

    def test_result_has_empty_remediation_when_ok(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", {**clear, **FULL_ENV}):
            result = check_google_credentials()
        assert result.remediation == ""


# ---------------------------------------------------------------------------
# check_google_credentials — credential missing scenarios
# ---------------------------------------------------------------------------


class TestCheckGoogleCredentialsMissing:
    def test_returns_not_ok_when_all_missing(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", clear, clear=True):
            result = check_google_credentials()
        assert result.ok is False

    def test_missing_vars_listed_when_all_missing(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", clear, clear=True):
            result = check_google_credentials()
        assert len(result.missing_vars) == 3
        # Canonical var names are reported
        assert "GOOGLE_OAUTH_CLIENT_ID" in result.missing_vars
        assert "GOOGLE_OAUTH_CLIENT_SECRET" in result.missing_vars
        assert "GOOGLE_REFRESH_TOKEN" in result.missing_vars

    def test_returns_not_ok_when_partial_missing(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", {**clear, "GMAIL_CLIENT_ID": "id"}):
            result = check_google_credentials()
        assert result.ok is False
        assert "GOOGLE_OAUTH_CLIENT_SECRET" in result.missing_vars
        assert "GOOGLE_REFRESH_TOKEN" in result.missing_vars
        # client_id is present
        assert "GOOGLE_OAUTH_CLIENT_ID" not in result.missing_vars

    def test_message_is_actionable(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", clear, clear=True):
            result = check_google_credentials()
        msg = result.message.lower()
        assert "google" in msg
        assert "credentials" in msg or "oauth" in msg

    def test_remediation_contains_bootstrap_instructions(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", clear, clear=True):
            result = check_google_credentials()
        remediation = result.remediation
        assert "dashboard" in remediation.lower() or "localhost:40200" in remediation
        assert "GOOGLE_OAUTH_CLIENT_ID" in remediation
        assert "GOOGLE_OAUTH_CLIENT_SECRET" in remediation
        assert "GOOGLE_REFRESH_TOKEN" in remediation

    def test_remediation_does_not_contain_secret_values(self) -> None:
        """Remediation text must not echo back any env var values."""
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", {**clear, "GMAIL_CLIENT_ID": "SUPER-SECRET-ID"}):
            result = check_google_credentials()
        assert "SUPER-SECRET-ID" not in result.remediation
        assert "SUPER-SECRET-ID" not in result.message

    def test_calendar_json_blob_missing_fields_not_ok(self) -> None:
        """Calendar JSON blob with missing fields falls through to env check."""
        clear = _all_google_env_vars_cleared()
        # Blob is missing refresh_token
        blob = json.dumps({"client_id": "id", "client_secret": "secret"})
        with mock.patch.dict("os.environ", {**clear, _CALENDAR_JSON_ENV: blob}):
            result = check_google_credentials()
        assert result.ok is False

    def test_calendar_json_blob_malformed_falls_through(self) -> None:
        """Malformed Calendar JSON blob falls through to env check."""
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", {**clear, _CALENDAR_JSON_ENV: "not-valid-json{{"}):
            result = check_google_credentials()
        assert result.ok is False


# ---------------------------------------------------------------------------
# require_google_credentials_or_exit
# ---------------------------------------------------------------------------


class TestRequireGoogleCredentialsOrExit:
    def test_does_not_exit_when_credentials_present(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", {**clear, **FULL_ENV}):
            # Should not raise SystemExit
            require_google_credentials_or_exit(caller="test-component")

    def test_exits_when_credentials_missing(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", clear, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                require_google_credentials_or_exit(caller="test-component")
        assert exc_info.value.code == 1

    def test_custom_exit_code(self) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", clear, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                require_google_credentials_or_exit(caller="test-component", exit_code=2)
        assert exc_info.value.code == 2

    def test_prints_to_stderr_when_missing(self, capsys: pytest.CaptureFixture) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", clear, clear=True):
            with pytest.raises(SystemExit):
                require_google_credentials_or_exit(
                    caller="gmail-connector",
                    dashboard_url="http://localhost:40200",
                )
        captured = capsys.readouterr()
        # Output goes to stderr
        assert "gmail-connector" in captured.err
        assert "STARTUP BLOCKED" in captured.err
        assert "http://localhost:40200" in captured.err

    def test_caller_name_in_stderr_output(self, capsys: pytest.CaptureFixture) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", clear, clear=True):
            with pytest.raises(SystemExit):
                require_google_credentials_or_exit(caller="my-special-connector")
        captured = capsys.readouterr()
        assert "my-special-connector" in captured.err

    def test_stderr_does_not_contain_secret_values(self, capsys: pytest.CaptureFixture) -> None:
        """Error output must not echo back env var values (even partial/wrong creds)."""
        clear = _all_google_env_vars_cleared()
        partial = {"GMAIL_CLIENT_ID": "PARTIAL-SECRET-VALUE"}
        with mock.patch.dict("os.environ", {**clear, **partial}):
            with pytest.raises(SystemExit):
                require_google_credentials_or_exit(caller="test")
        captured = capsys.readouterr()
        assert "PARTIAL-SECRET-VALUE" not in captured.err

    def test_dashboard_url_in_stderr_output(self, capsys: pytest.CaptureFixture) -> None:
        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", clear, clear=True):
            with pytest.raises(SystemExit):
                require_google_credentials_or_exit(
                    caller="test",
                    dashboard_url="http://myhost:9999",
                )
        captured = capsys.readouterr()
        assert "http://myhost:9999" in captured.err

    def test_no_exit_logs_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        clear = _all_google_env_vars_cleared()
        import logging

        with caplog.at_level(logging.DEBUG, logger="butlers.startup_guard"):
            with mock.patch.dict("os.environ", {**clear, **FULL_ENV}):
                require_google_credentials_or_exit(caller="test-caller")
        assert "test-caller" in caplog.text
        assert "OK" in caplog.text


# ---------------------------------------------------------------------------
# GoogleCredentialCheckResult dataclass
# ---------------------------------------------------------------------------


class TestGoogleCredentialCheckResult:
    def test_ok_result_is_frozen(self) -> None:
        result = GoogleCredentialCheckResult(
            ok=True,
            missing_vars=[],
            message="ok",
            remediation="",
        )
        with pytest.raises((AttributeError, TypeError)):
            result.ok = False  # type: ignore[misc]

    def test_not_ok_result_has_missing_vars(self) -> None:
        result = GoogleCredentialCheckResult(
            ok=False,
            missing_vars=["GOOGLE_OAUTH_CLIENT_ID"],
            message="missing",
            remediation="do x",
        )
        assert result.ok is False
        assert "GOOGLE_OAUTH_CLIENT_ID" in result.missing_vars


# ---------------------------------------------------------------------------
# check_google_credentials_with_db (async DB + env check)
# ---------------------------------------------------------------------------


class TestCheckGoogleCredentialsWithDb:
    """Tests for the async DB-first credential check."""

    async def test_returns_ok_when_db_has_credentials(self) -> None:
        """check_google_credentials_with_db returns ok=True when DB has credentials."""
        from unittest.mock import AsyncMock, MagicMock

        # Build a fake conn that returns stored credentials
        payload = {
            "client_id": "db-client-id",
            "client_secret": "db-secret",
            "refresh_token": "db-refresh-token",
        }
        record = MagicMock()
        record.__getitem__ = lambda self, key: payload
        conn = AsyncMock()
        conn.fetchrow.return_value = record

        result = await check_google_credentials_with_db(conn, caller="test")
        assert result.ok is True
        assert result.missing_vars == []
        assert result.remediation == ""

    async def test_falls_back_to_env_when_db_empty(self) -> None:
        """Returns ok=True when DB empty but env vars are set."""
        from unittest.mock import AsyncMock

        conn = AsyncMock()
        conn.fetchrow.return_value = None  # Empty DB

        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", {**clear, **FULL_ENV}):
            result = await check_google_credentials_with_db(conn, caller="test")
        assert result.ok is True

    async def test_returns_not_ok_when_neither_db_nor_env(self) -> None:
        """Returns ok=False when neither DB nor env vars have credentials."""
        from unittest.mock import AsyncMock

        conn = AsyncMock()
        conn.fetchrow.return_value = None  # Empty DB

        clear = _all_google_env_vars_cleared()
        with mock.patch.dict("os.environ", clear, clear=True):
            result = await check_google_credentials_with_db(conn, caller="test")
        assert result.ok is False
        assert len(result.missing_vars) > 0

    async def test_db_takes_priority_over_env(self) -> None:
        """DB credentials are returned even when env vars differ."""
        from unittest.mock import AsyncMock, MagicMock

        payload = {
            "client_id": "db-id",
            "client_secret": "db-secret",
            "refresh_token": "db-token",
        }
        record = MagicMock()
        record.__getitem__ = lambda self, key: payload
        conn = AsyncMock()
        conn.fetchrow.return_value = record

        # Env has different credentials
        different_env = {
            "GOOGLE_OAUTH_CLIENT_ID": "env-id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "env-secret",
            "GOOGLE_REFRESH_TOKEN": "env-token",
        }
        with mock.patch.dict("os.environ", different_env):
            result = await check_google_credentials_with_db(conn, caller="test")
        assert result.ok is True
