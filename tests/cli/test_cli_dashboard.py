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

    def test_dashboard_command_registered(self, runner):
        """The dashboard command should be listed in CLI help."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "dashboard" in result.output

    def test_dashboard_help(self, runner):
        """The dashboard command should show its own help text."""
        result = runner.invoke(cli, ["dashboard", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
        assert "0.0.0.0" in result.output
        assert "40200" in result.output

    @patch("uvicorn.run")
    def test_dashboard_default_host_port(self, mock_uvicorn_run, runner):
        """Dashboard should use default host 0.0.0.0 and port 40200."""
        result = runner.invoke(cli, ["dashboard"])
        assert result.exit_code == 0
        assert "Starting Butlers dashboard on 0.0.0.0:40200" in result.output
        mock_uvicorn_run.assert_called_once_with(
            "butlers.api.app:create_app",
            host="0.0.0.0",
            port=40200,
            factory=True,
        )

    @patch("uvicorn.run")
    def test_dashboard_custom_host(self, mock_uvicorn_run, runner):
        """Dashboard should accept a custom --host."""
        result = runner.invoke(cli, ["dashboard", "--host", "127.0.0.1"])
        assert result.exit_code == 0
        assert "Starting Butlers dashboard on 127.0.0.1:40200" in result.output
        mock_uvicorn_run.assert_called_once_with(
            "butlers.api.app:create_app",
            host="127.0.0.1",
            port=40200,
            factory=True,
        )

    @patch("uvicorn.run")
    def test_dashboard_custom_port(self, mock_uvicorn_run, runner):
        """Dashboard should accept a custom --port."""
        result = runner.invoke(cli, ["dashboard", "--port", "9999"])
        assert result.exit_code == 0
        assert "Starting Butlers dashboard on 0.0.0.0:9999" in result.output
        mock_uvicorn_run.assert_called_once_with(
            "butlers.api.app:create_app",
            host="0.0.0.0",
            port=9999,
            factory=True,
        )

    @patch("uvicorn.run")
    def test_dashboard_custom_host_and_port(self, mock_uvicorn_run, runner):
        """Dashboard should accept both --host and --port together."""
        result = runner.invoke(cli, ["dashboard", "--host", "localhost", "--port", "3000"])
        assert result.exit_code == 0
        assert "Starting Butlers dashboard on localhost:3000" in result.output
        mock_uvicorn_run.assert_called_once_with(
            "butlers.api.app:create_app",
            host="localhost",
            port=3000,
            factory=True,
        )

    def test_dashboard_invalid_port(self, runner):
        """Dashboard should reject non-integer port values."""
        result = runner.invoke(cli, ["dashboard", "--port", "abc"])
        assert result.exit_code != 0
