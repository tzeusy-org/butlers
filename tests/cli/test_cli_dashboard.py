"""Tests for the 'butlers dashboard' CLI command."""

from unittest.mock import patch

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

    @patch("uvicorn.run")
    def test_dashboard_host_port_variants(self, mock_uvicorn_run, runner):
        """Default host/port, custom host, and custom port all invoke uvicorn correctly."""
        # Default
        result = runner.invoke(cli, ["dashboard"])
        assert result.exit_code == 0
        assert "Starting Butlers dashboard on 0.0.0.0:41200" in result.output
        mock_uvicorn_run.assert_called_with(
            "butlers.api.app:create_app", host="0.0.0.0", port=41200, factory=True
        )

        # Custom host
        mock_uvicorn_run.reset_mock()
        result2 = runner.invoke(cli, ["dashboard", "--host", "127.0.0.1"])
        assert "127.0.0.1:41200" in result2.output
        mock_uvicorn_run.assert_called_with(
            "butlers.api.app:create_app", host="127.0.0.1", port=41200, factory=True
        )

        # Custom port
        mock_uvicorn_run.reset_mock()
        result3 = runner.invoke(cli, ["dashboard", "--port", "9999"])
        assert "0.0.0.0:9999" in result3.output
        mock_uvicorn_run.assert_called_with(
            "butlers.api.app:create_app", host="0.0.0.0", port=9999, factory=True
        )

    def test_dashboard_invalid_port(self, runner):
        result = runner.invoke(cli, ["dashboard", "--port", "abc"])
        assert result.exit_code != 0
