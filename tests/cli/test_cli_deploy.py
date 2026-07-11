"""Tests for the 'butlers deploy' CLI command (bu-9r3hd.3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from butlers.cli import cli
from butlers.core.deploy import DeployError, DeployResult

pytestmark = pytest.mark.unit


@pytest.fixture
def runner():
    return CliRunner()


class TestDeployCommand:
    def test_deploy_help_registered(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "deploy" in result.output

        result2 = runner.invoke(cli, ["deploy", "--help"])
        assert result2.exit_code == 0
        assert "--timeout" in result2.output
        assert "--dir" in result2.output

    def test_success_prints_summary_and_exits_zero(self, runner, tmp_path):
        with patch("butlers.core.deploy.run_deploy", new=AsyncMock()) as mock_run:
            mock_run.return_value = DeployResult(
                git_sha="abc1234", migration_head="core_163", result="success"
            )
            result = runner.invoke(cli, ["deploy", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "abc1234" in result.output
        assert "core_163" in result.output
        mock_run.assert_awaited_once()

    def test_failure_prints_phase_and_exits_nonzero(self, runner, tmp_path):
        with patch("butlers.core.deploy.run_deploy", new=AsyncMock()) as mock_run:
            mock_run.side_effect = DeployError("migrate", "alembic: revision mismatch")
            result = runner.invoke(cli, ["deploy", "--dir", str(tmp_path)])

        assert result.exit_code == 1
        assert "migrate" in result.output
        assert "revision mismatch" in result.output

    def test_custom_timeout_is_threaded_into_deploy_config(self, runner, tmp_path):
        captured = {}

        async def fake_run_deploy(config, **kwargs):
            captured["config"] = config
            return DeployResult(git_sha="abc1234", migration_head=None, result="success")

        with patch("butlers.core.deploy.run_deploy", new=fake_run_deploy):
            result = runner.invoke(cli, ["deploy", "--dir", str(tmp_path), "--timeout", "42"])

        assert result.exit_code == 0
        assert captured["config"].health_timeout_s == 42.0
