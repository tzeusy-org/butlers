"""Tests for the 'butlers deploy' CLI command (bu-9r3hd.3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from butlers.cli import cli
from butlers.core.deploy import DeployError, DeployResult

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner():
    return CliRunner()


def _stub_target(_roster_dir, ready_url, timeout_s):
    return {"ready_url": ready_url, "timeout_s": timeout_s}


@pytest.fixture(autouse=True)
def _stub_roster_readiness(request, monkeypatch):
    """Orchestration tests use an empty --dir; the real roster derivation is
    exercised by the readiness-timeout test below."""
    if request.node.name in _REAL_ROSTER_TESTS:
        return
    monkeypatch.setattr("butlers.cli._sustained_readiness_target", _stub_target)


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
        assert "--ready-url" in result2.output
        assert "--readiness-timeout" in result2.output
        assert "--base-image" in result2.output
        assert "--python-artifacts" in result2.output
        assert "--go-artifacts" in result2.output

    @pytest.mark.parametrize("environment_options", [False, True])
    def test_closed_build_inputs_are_forwarded(self, runner, tmp_path, environment_options):
        base = "registry.example.test/butlers/base@sha256:" + "a" * 64
        args = ["deploy", "--dir", str(tmp_path)]
        environment = {}
        if environment_options:
            environment = {
                "BUTLERS_BASE_IMAGE": base,
                "BUTLERS_PYTHON_ARTIFACTS": str(tmp_path),
                "BUTLERS_GO_ARTIFACTS": str(tmp_path),
            }
        else:
            args += [
                "--base-image",
                base,
                "--python-artifacts",
                str(tmp_path),
                "--go-artifacts",
                str(tmp_path),
            ]
        with patch("butlers.core.deploy.run_deploy", new=AsyncMock()) as mock_run:
            mock_run.return_value = DeployResult(
                git_sha="fixture", migration_head=None, result="success"
            )
            result = runner.invoke(cli, args, env=environment)
        assert result.exit_code == 0, result.output
        config = mock_run.await_args.args[0]
        assert config.base_image == base
        assert config.python_artifacts == tmp_path
        assert config.go_artifacts == tmp_path

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
        assert config.readiness == {"ready_url": None, "timeout_s": None}

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
        # /ready follows the selected stack's API, not prod's default port.
        assert config.readiness["ready_url"] == "http://localhost:42200/ready"

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


_REAL_ROSTER_TESTS = {
    "test_semantic_readiness_is_derived_from_the_roster_and_short_overrides_refused",
    "test_await_ready_check_only_validates_the_timeout_floor_without_waiting",
}


def test_semantic_readiness_is_derived_from_the_roster_and_short_overrides_refused(runner):
    """REQ-dashboard-api-064: the deadline floor comes from the real roster's QA
    cadence and longest TTL; a shorter override, or no roster, refuses before
    any build/deploy step runs."""
    with patch("butlers.core.deploy.run_deploy", new=AsyncMock()) as mock_run:
        short = runner.invoke(
            cli, ["deploy", "--dir", str(_REPO_ROOT), "--readiness-timeout", "600"]
        )
        missing = runner.invoke(cli, ["deploy", "--dir", str(_REPO_ROOT / "docs")])

    assert short.exit_code == 1
    assert "below the derived minimum" in short.output
    assert missing.exit_code == 1
    assert "roster" in missing.output
    mock_run.assert_not_awaited()

    captured = {}

    async def fake_run_deploy(config, **kwargs):
        captured["config"] = config
        return DeployResult(
            git_sha="abc1234", migration_head=None, result="success",
            readiness_receipt='{"result":"success"}',
        )  # fmt: skip

    with patch("butlers.core.deploy.run_deploy", new=fake_run_deploy):
        result = runner.invoke(cli, ["deploy", "--dir", str(_REPO_ROOT)])

    assert result.exit_code == 0, result.output
    readiness = captured["config"].readiness
    assert readiness.ready_url == "http://localhost:41200/ready"
    assert readiness.policy.effective_timeout_s >= 35 * 60
    assert "Semantic readiness receipt" in result.output


def test_await_ready_check_only_validates_the_timeout_floor_without_waiting(runner):
    """record-deploy.sh calls this before helm upgrade so a bad override never
    changes the cluster; it reads no database or /ready."""
    roster = str(_REPO_ROOT / "roster")
    with patch("butlers.cli._await_ready", new=AsyncMock()) as mock_wait:
        short = runner.invoke(
            cli, ["deployments", "await-ready", "--check-only", "--roster-dir", roster,
                  "--timeout", "600"],
        )  # fmt: skip
        ok = runner.invoke(
            cli, ["deployments", "await-ready", "--check-only", "--roster-dir", roster,
                  "--timeout", "7200"],
        )  # fmt: skip

    assert short.exit_code == 2
    assert "below the derived minimum" in short.output
    assert ok.exit_code == 0, ok.output
    assert "timeout_s=7200" in ok.output
    mock_wait.assert_not_awaited()
