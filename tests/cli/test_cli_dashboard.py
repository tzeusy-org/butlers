"""Tests for the 'butlers dashboard' CLI command."""

from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from butlers.cli import cli

pytestmark = pytest.mark.unit


@pytest.fixture
def runner():
    return CliRunner()


class TestDashboardCommand:
    """Tests for the dashboard CLI command."""

    def test_dashboard_help_registered(self, runner):
        """Dashboard command is listed and shows --host/--port options."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "dashboard" in result.output

        result2 = runner.invoke(cli, ["dashboard", "--help"])
        assert result2.exit_code == 0
        assert "--host" in result2.output
        assert "--port" in result2.output
        assert "41200" in result2.output

    @patch(
        "butlers.core.custody_api_parent.run_dashboard_parent",
        new_callable=AsyncMock,
        return_value=0,
    )
    def test_dashboard_host_port_variants(self, mock_parent, runner):
        """Default host/port, custom host, and custom port all reach the native fixed parent correctly."""
        # Default
        result = runner.invoke(cli, ["dashboard"])
        assert result.exit_code == 0
        assert "Starting Butlers dashboard on 0.0.0.0:41200" in result.output
        mock_parent.assert_awaited_once_with("0.0.0.0", 41200)

        # Custom host
        mock_parent.reset_mock()
        result2 = runner.invoke(cli, ["dashboard", "--host", "127.0.0.1"])
        assert "127.0.0.1:41200" in result2.output
        mock_parent.assert_awaited_once_with("127.0.0.1", 41200)

        # Custom port
        mock_parent.reset_mock()
        result3 = runner.invoke(cli, ["dashboard", "--port", "9999"])
        assert "0.0.0.0:9999" in result3.output
        mock_parent.assert_awaited_once_with("0.0.0.0", 9999)

        # The child must still install its one-shot private parent channel and
        # serve the same factory with proxy/access logging disabled.
        import socket
        import sys

        from butlers.core.custody_api_parent import main

        with socket.socket() as channel:
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "custody-parent",
                        "--serve",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "9999",
                        "--parent-fd",
                        str(channel.fileno()),
                    ],
                ),
                patch("butlers.core.custody_api_parent.install_api_parent_channel") as install,
                patch("butlers.core.custody_api_parent.socket.socket", return_value=channel),
                patch("uvicorn.run") as serve,
            ):
                main()
            install.assert_called_once_with(channel)
            serve.assert_called_once_with(
                "butlers.api.app:create_app",
                host="127.0.0.1",
                port=9999,
                factory=True,
                proxy_headers=False,
                access_log=False,
            )

    def test_dashboard_invalid_port(self, runner):
        result = runner.invoke(cli, ["dashboard", "--port", "abc"])
        assert result.exit_code != 0
