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
        assert "--project-name" in result2.output
        assert "--env-file" in result2.output
        assert "--health-url" in result2.output
        assert "--profile" in result2.output
        assert "--allow-dirty-root" in result2.output

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

    def test_defaults_unchanged_when_new_options_omitted(self, runner, tmp_path):
        """bu-hmdqz.1: project-name/env-file/health-url/profile are opt-in --
        omitting them must reproduce the exact prior prod-targeting behavior."""
        captured = {}

        async def fake_run_deploy(config, **kwargs):
            captured["config"] = config
            return DeployResult(git_sha="abc1234", migration_head=None, result="success")

        with patch("butlers.core.deploy.run_deploy", new=fake_run_deploy):
            result = runner.invoke(cli, ["deploy", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        config = captured["config"]
        assert config.project_name == "butlers"
        assert config.env_file == ".env.prod"
        assert config.health_url == "http://localhost:41200/health"
        assert config.profiles == ()

    def test_dev_stack_options_are_threaded_into_deploy_config(self, runner, tmp_path):
        """bu-hmdqz.1: redeploying the live 'butlers-dev' stack needs the
        project name, env file, health URL, and 'dev' profile (frontend-dev
        is profile-gated) all threaded through explicitly."""
        captured = {}

        async def fake_run_deploy(config, **kwargs):
            captured["config"] = config
            return DeployResult(git_sha="abc1234", migration_head="core_163", result="success")

        with patch("butlers.core.deploy.run_deploy", new=fake_run_deploy):
            result = runner.invoke(
                cli,
                [
                    "deploy",
                    "--dir",
                    str(tmp_path),
                    "--project-name",
                    "butlers-dev",
                    "--env-file",
                    ".env.dev",
                    "--health-url",
                    "http://localhost:42200/health",
                    "--profile",
                    "dev",
                ],
            )

        assert result.exit_code == 0
        config = captured["config"]
        assert config.project_name == "butlers-dev"
        assert config.env_file == ".env.dev"
        assert config.health_url == "http://localhost:42200/health"
        assert config.profiles == ("dev",)

    def test_allow_dirty_root_defaults_false(self, runner, tmp_path):
        """The preflight override must be opt-in — omitting the flag keeps the
        canonical fail-closed guard."""
        captured = {}

        async def fake_run_deploy(config, **kwargs):
            captured["config"] = config
            return DeployResult(git_sha="abc1234", migration_head=None, result="success")

        with patch("butlers.core.deploy.run_deploy", new=fake_run_deploy):
            result = runner.invoke(cli, ["deploy", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        assert captured["config"].allow_dirty_root is False

    def test_allow_dirty_root_flag_threads_into_config(self, runner, tmp_path):
        captured = {}

        async def fake_run_deploy(config, **kwargs):
            captured["config"] = config
            return DeployResult(git_sha="abc1234", migration_head=None, result="success")

        with patch("butlers.core.deploy.run_deploy", new=fake_run_deploy):
            result = runner.invoke(cli, ["deploy", "--dir", str(tmp_path), "--allow-dirty-root"])

        assert result.exit_code == 0
        assert captured["config"].allow_dirty_root is True

    def test_override_reasons_are_printed_as_warnings(self, runner, tmp_path):
        """When the preflight guard is overridden, each downgraded reason is
        surfaced loudly on the deploy output."""
        with patch("butlers.core.deploy.run_deploy", new=AsyncMock()) as mock_run:
            mock_run.return_value = DeployResult(
                git_sha="abc1234",
                migration_head="core_163",
                result="success",
                overrides=("deploy root /wt is a linked git worktree",),
            )
            result = runner.invoke(cli, ["deploy", "--dir", str(tmp_path), "--allow-dirty-root"])

        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "linked git worktree" in result.output

    def test_rejects_hotreload_profile_with_clean_error(self, runner, tmp_path):
        """The CLI must surface DeployConfig's hotreload guard as a clean
        error/exit code, not a traceback."""
        result = runner.invoke(cli, ["deploy", "--dir", str(tmp_path), "--profile", "hotreload"])

        assert result.exit_code == 1
        assert "hotreload" in result.output
