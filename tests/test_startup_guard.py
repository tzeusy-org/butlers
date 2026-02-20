"""Tests for Google OAuth startup gating (butlers.startup_guard)."""

from __future__ import annotations

import unittest.mock as mock

import pytest

from butlers.startup_guard import (
    GoogleCredentialCheckResult,
    check_google_credentials,
    check_google_credentials_with_db,
    require_google_credentials_or_exit,
)

pytestmark = pytest.mark.unit


class TestCheckGoogleCredentials:
    def test_sync_check_returns_db_only_remediation(self) -> None:
        result = check_google_credentials()
        assert result.ok is False
        assert result.missing_vars == []
        assert "dashboard" in result.remediation.lower()
        assert "db-managed" in result.message.lower()


class TestRequireGoogleCredentialsOrExit:
    def test_exits_with_default_code(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            require_google_credentials_or_exit(caller="test-component")
        assert exc_info.value.code == 1

    def test_custom_exit_code(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            require_google_credentials_or_exit(caller="test-component", exit_code=2)
        assert exc_info.value.code == 2

    def test_stderr_contains_caller_and_dashboard(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit):
            require_google_credentials_or_exit(
                caller="gmail-connector",
                dashboard_url="http://localhost:40200",
            )
        captured = capsys.readouterr()
        assert "gmail-connector" in captured.err
        assert "STARTUP BLOCKED" in captured.err
        assert "http://localhost:40200" in captured.err


class TestGoogleCredentialCheckResult:
    def test_result_is_frozen(self) -> None:
        result = GoogleCredentialCheckResult(
            ok=True,
            missing_vars=[],
            message="ok",
            remediation="",
        )
        with pytest.raises((AttributeError, TypeError)):
            result.ok = False  # type: ignore[misc]


class TestCheckGoogleCredentialsWithDb:
    async def test_returns_ok_when_resolver_succeeds(self) -> None:
        conn = object()
        with mock.patch(
            "butlers.google_credentials.resolve_google_credentials",
            new=mock.AsyncMock(return_value=object()),
        ):
            result = await check_google_credentials_with_db(conn, caller="test")
        assert result.ok is True
        assert result.missing_vars == []
        assert result.remediation == ""

    async def test_returns_not_ok_when_resolver_fails(self) -> None:
        from butlers.google_credentials import MissingGoogleCredentialsError

        conn = object()
        with mock.patch(
            "butlers.google_credentials.resolve_google_credentials",
            new=mock.AsyncMock(side_effect=MissingGoogleCredentialsError("missing")),
        ):
            result = await check_google_credentials_with_db(conn, caller="test")
        assert result.ok is False
        assert result.missing_vars == []
        assert "missing" in result.message
        assert "dashboard" in result.remediation.lower()
