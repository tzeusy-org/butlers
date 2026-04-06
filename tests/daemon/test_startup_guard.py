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


class TestGoogleStartupGuard:
    def test_sync_checks_and_exit(self, capsys: pytest.CaptureFixture) -> None:
        """check_google_credentials returns not-ok with db-managed message; result frozen;
        require_google_credentials_or_exit exits with correct code and stderr."""
        result = check_google_credentials()
        assert result.ok is False
        assert result.missing_vars == []
        assert "dashboard" in result.remediation.lower()
        assert "db-managed" in result.message.lower()

        # Result is frozen/immutable
        frozen = GoogleCredentialCheckResult(ok=True, missing_vars=[], message="ok", remediation="")
        with pytest.raises((AttributeError, TypeError)):
            frozen.ok = False  # type: ignore[misc]

        # require_google_credentials_or_exit exits with code 1 and correct stderr
        with pytest.raises(SystemExit) as exc_info:
            require_google_credentials_or_exit(
                caller="gmail-connector",
                dashboard_url="http://localhost:41200",
                exit_code=1,
            )
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "gmail-connector" in captured.err
        assert "STARTUP BLOCKED" in captured.err
        assert "http://localhost:41200" in captured.err

    async def test_check_with_db_ok_and_not_ok(self) -> None:
        """check_google_credentials_with_db returns ok on success, not-ok on error."""
        conn = object()

        with mock.patch(
            "butlers.google_credentials.resolve_google_credentials",
            new=mock.AsyncMock(return_value=object()),
        ):
            result = await check_google_credentials_with_db(conn, caller="test")
        assert result.ok is True

        from butlers.google_credentials import MissingGoogleCredentialsError

        with mock.patch(
            "butlers.google_credentials.resolve_google_credentials",
            new=mock.AsyncMock(side_effect=MissingGoogleCredentialsError("missing")),
        ):
            result2 = await check_google_credentials_with_db(conn, caller="test")
        assert result2.ok is False
        assert "missing" in result2.message
        assert "dashboard" in result2.remediation.lower()
