"""Host confirmation and output boundaries, without touching a host database."""

from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from butlers.api.owner_auth.host_cli import auth
from butlers.api.owner_auth.service import AuthError, OwnerAuthService
from butlers.cli import cli

pytestmark = pytest.mark.unit


def test_host_commands_are_registered_require_confirmation_and_do_not_expose_material(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_ORIGIN", "https://butlers.example.test")
    monkeypatch.setenv("DASHBOARD_AUTH_RP_ID", "butlers.example.test")
    monkeypatch.setenv("DASHBOARD_API_KEY", "private-configured-key-sentinel")
    runner = CliRunner()
    assert "auth" in runner.invoke(cli, ["--help"]).output
    for command in ("authorize-recovery", "reconcile-mode", "rebind-origin", "revoke-sessions"):
        args = [command] + (["--request", "A" * 43] if command == "authorize-recovery" else [])
        with patch(
            "butlers.api.owner_auth.host_cli.run_host_operation", new_callable=AsyncMock
        ) as operation:
            denied = runner.invoke(auth, args)
            assert denied.exit_code != 0
            operation.assert_not_awaited()
            operation.return_value = {"changed": True}
            accepted = runner.invoke(auth, args + ["--confirm-revoke"])
            assert accepted.exit_code == 0, accepted.output
            operation.assert_awaited_once()
            assert "Canonical origin: https://butlers.example.test" in accepted.output
            assert "private-configured-key-sentinel" not in accepted.output
            assert "A" * 43 not in accepted.output
    with patch(
        "butlers.api.owner_auth.host_cli.run_host_operation", new_callable=AsyncMock
    ) as operation:
        operation.side_effect = AuthError()
        result = runner.invoke(auth, ["authorize-registration", "--request", "A" * 43])
        assert result.exit_code != 0
        assert "Owner authentication unavailable" in result.output


async def test_malformed_configured_key_is_fixed_denial():
    from butlers.api.owner_auth.config import OwnerAuthConfig

    service = OwnerAuthService(None, OwnerAuthConfig(None, None, "synthetic key"))
    for malformed in ("", "wrong", "\ud800", None, 42):
        with pytest.raises(AuthError) as caught:
            await service.key_session(malformed)
        assert caught.value.status_code == 401
        assert caught.value.message == "Owner authentication required"
