"""Unit tests for butlers.core.deploy (bu-9r3hd.3, "butlers deploy").

Every docker/compose/subprocess/httpx boundary is mocked here — the real
ledger write (via butlers.core.deployments.record_deployment) is exercised
against a migrated Postgres in
tests/integration/test_deploy_ledger_roundtrip.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from butlers.core.deploy import (
    DeployConfig,
    DeployError,
    _clean_compose_env,
    _compose_base_args,
    build_image,
    recreate_services,
    resolve_git_sha,
    run_deploy,
    run_migrations,
    wait_for_health,
)

pytestmark = pytest.mark.unit


def _config(**overrides) -> DeployConfig:
    defaults = dict(repo_root=Path("/repo"))
    defaults.update(overrides)
    return DeployConfig(**defaults)


class TestResolveGitSha:
    def test_runs_git_rev_parse_head_in_repo_root(self, monkeypatch):
        captured = {}

        def fake_run(cmd, cwd, capture_output, text, check):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            return subprocess.CompletedProcess(cmd, 0, stdout="abc1234\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        sha = resolve_git_sha(Path("/repo"))
        assert sha == "abc1234"
        assert captured["cmd"] == ["git", "rev-parse", "HEAD"]
        assert captured["cwd"] == Path("/repo")


class TestComposeArgsAndEnv:
    def test_compose_base_args_includes_files_project_and_env_file(self):
        config = _config(compose_files=("docker-compose.yml",), project_name="butlers")
        args = _compose_base_args(config)
        assert args == [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-p",
            "butlers",
            "--env-file",
            ".env.prod",
        ]

    def test_no_profile_flag_appears_by_default(self):
        """Structural guard: a default (prod) deploy must never request a compose profile."""
        args = _compose_base_args(_config())
        assert "--profile" not in args

    def test_explicit_profiles_are_threaded_into_compose_args(self):
        """bu-hmdqz.1: an explicitly-requested profile (e.g. 'dev' for the
        butlers-dev project's profile-gated frontend-dev service) must reach
        the actual compose invocation."""
        args = _compose_base_args(_config(profiles=("dev",)))
        assert "--profile" in args
        assert args[args.index("--profile") + 1] == "dev"

    def test_hotreload_profile_is_rejected_at_config_construction(self):
        """The hotreload profile bind-mounts source instead of the baked
        image -- never allowed, regardless of caller intent."""
        with pytest.raises(ValueError, match="hotreload"):
            _config(profiles=("hotreload",))

    def test_hotreload_profile_rejected_even_alongside_other_profiles(self):
        with pytest.raises(ValueError, match="hotreload"):
            _config(profiles=("dev", "hotreload"))

    def test_clean_compose_env_strips_compose_profiles(self, monkeypatch):
        monkeypatch.setenv("COMPOSE_PROFILES", "hotreload")
        monkeypatch.setenv("SOME_OTHER_VAR", "kept")
        env = _clean_compose_env()
        assert "COMPOSE_PROFILES" not in env
        assert env["SOME_OTHER_VAR"] == "kept"

    def test_clean_compose_env_is_noop_when_unset(self, monkeypatch):
        monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
        env = _clean_compose_env()
        assert "COMPOSE_PROFILES" not in env


class TestBuildImage:
    def test_success_builds_with_git_sha_arg(self, monkeypatch):
        captured = {}

        def fake_run(cmd, cwd, env, capture_output, text):
            captured["cmd"] = cmd
            captured["env"] = env
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        build_image(_config(), "abc1234")
        cmd = captured["cmd"]
        assert cmd[:2] == ["docker", "build"]
        assert "--build-arg" in cmd
        assert "GIT_SHA=abc1234" in cmd
        assert "butlers-app:latest" in cmd

    def test_failure_raises_deploy_error_with_build_phase(self, monkeypatch):
        def fake_run(cmd, cwd, env, capture_output, text):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no space left on device")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(DeployError) as exc_info:
            build_image(_config(), "abc1234")
        assert exc_info.value.phase == "build"
        assert "no space left on device" in str(exc_info.value)


class TestRunMigrations:
    def test_uses_run_rm_not_up(self, monkeypatch):
        """Never `up -d`-only for migrations — a stale exited container must
        not silently skip a rerun (bu-zhfd0)."""
        captured = {}

        def fake_run(cmd, cwd, env, capture_output, text):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        run_migrations(_config())
        cmd = captured["cmd"]
        assert cmd[-3:] == ["run", "--rm", "migrations"]

    def test_failure_raises_deploy_error_with_migrate_phase(self, monkeypatch):
        def fake_run(cmd, cwd, env, capture_output, text):
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="alembic: revision mismatch"
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(DeployError) as exc_info:
            run_migrations(_config())
        assert exc_info.value.phase == "migrate"


class TestRecreateServices:
    def test_up_dash_d_remove_orphans_no_profile(self, monkeypatch):
        captured = {}

        def fake_run(cmd, cwd, env, capture_output, text):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        recreate_services(_config())
        cmd = captured["cmd"]
        assert "up" in cmd
        assert "-d" in cmd
        assert "--remove-orphans" in cmd
        assert "--profile" not in cmd
        assert "--scale" not in cmd

    def test_failure_raises_deploy_error_with_recreate_phase(self, monkeypatch):
        def fake_run(cmd, cwd, env, capture_output, text):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="port already in use")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(DeployError) as exc_info:
            recreate_services(_config())
        assert exc_info.value.phase == "recreate"


class TestWaitForHealth:
    async def test_returns_when_status_ok(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        await wait_for_health(_config(health_timeout_s=1.0, health_poll_interval_s=0.01))

    async def test_retries_on_starting_then_succeeds(self, monkeypatch):
        calls = {"n": 0}

        async def fake_get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(
                    503, json={"status": "starting"}, request=httpx.Request("GET", url)
                )
            return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        await wait_for_health(_config(health_timeout_s=2.0, health_poll_interval_s=0.01))
        assert calls["n"] == 3

    async def test_times_out_and_raises_health_check_deploy_error(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return httpx.Response(
                503, json={"status": "starting"}, request=httpx.Request("GET", url)
            )

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        with pytest.raises(DeployError) as exc_info:
            await wait_for_health(_config(health_timeout_s=0.05, health_poll_interval_s=0.01))
        assert exc_info.value.phase == "health-check"

    async def test_connection_error_is_retried_not_raised_immediately(self, monkeypatch):
        calls = {"n": 0}

        async def fake_get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        await wait_for_health(_config(health_timeout_s=2.0, health_poll_interval_s=0.01))
        assert calls["n"] == 2


class TestRunDeploy:
    """Orchestration only — record_deployment is mocked here; the real write
    is covered by the integration test."""

    def _patch_phases(self, monkeypatch, *, fail_at: str | None = None):
        calls: list[str] = []

        def make(name):
            def _fn(config, *a):
                calls.append(name)
                if fail_at == name:
                    raise DeployError(name, f"{name} boom")

            return _fn

        async def _wait_ok(config):
            calls.append("health-check")
            if fail_at == "health-check":
                raise DeployError("health-check", "health-check boom")

        monkeypatch.setattr("butlers.core.deploy.build_image", make("build"))
        monkeypatch.setattr("butlers.core.deploy.run_migrations", make("migrate"))
        monkeypatch.setattr("butlers.core.deploy.recreate_services", make("recreate"))
        monkeypatch.setattr("butlers.core.deploy.wait_for_health", _wait_ok)
        monkeypatch.setattr("butlers.core.deploy.resolve_git_sha", lambda repo_root: "deadbeef")
        return calls

    async def test_success_records_success_row(self, monkeypatch):
        calls = self._patch_phases(monkeypatch)
        pool = AsyncMock()
        record_mock = AsyncMock(return_value="row-1")
        monkeypatch.setattr("butlers.core.deploy.record_deployment", record_mock)
        monkeypatch.setattr(
            "butlers.core.deploy.read_migration_head", AsyncMock(return_value="core_163")
        )

        result = await run_deploy(_config(), pool=pool)

        assert calls == ["build", "migrate", "recreate", "health-check"]
        assert result.result == "success"
        assert result.git_sha == "deadbeef"
        assert result.migration_head == "core_163"
        record_mock.assert_awaited_once_with(
            pool, git_sha="deadbeef", migration_head="core_163", result="success"
        )
        # Injected pool must not be closed by run_deploy.
        pool.close.assert_not_awaited()

    @pytest.mark.parametrize("fail_at", ["build", "migrate", "recreate", "health-check"])
    async def test_failure_at_each_phase_records_failed_row_and_reraises(
        self, monkeypatch, fail_at
    ):
        calls = self._patch_phases(monkeypatch, fail_at=fail_at)
        pool = AsyncMock()
        record_mock = AsyncMock(return_value="row-1")
        monkeypatch.setattr("butlers.core.deploy.record_deployment", record_mock)
        monkeypatch.setattr("butlers.core.deploy.read_migration_head", AsyncMock(return_value=None))

        with pytest.raises(DeployError) as exc_info:
            await run_deploy(_config(), pool=pool)

        assert exc_info.value.phase == fail_at
        # Every phase up to and including the failing one ran; nothing after it did.
        assert calls[-1] == fail_at
        record_mock.assert_awaited_once_with(
            pool, git_sha="deadbeef", migration_head=None, result="failed"
        )

    async def test_migration_head_read_failure_does_not_block_the_ledger_write(self, monkeypatch):
        """A failing alembic_version read must record an honest null, not crash."""
        self._patch_phases(monkeypatch)
        pool = AsyncMock()
        record_mock = AsyncMock(return_value="row-1")
        monkeypatch.setattr("butlers.core.deploy.record_deployment", record_mock)
        monkeypatch.setattr(
            "butlers.core.deploy.read_migration_head",
            AsyncMock(side_effect=Exception("relation does not exist")),
        )

        result = await run_deploy(_config(), pool=pool)

        assert result.migration_head is None
        record_mock.assert_awaited_once_with(
            pool, git_sha="deadbeef", migration_head=None, result="success"
        )

    async def test_no_injected_pool_creates_and_closes_its_own(self, monkeypatch):
        self._patch_phases(monkeypatch)
        owned_pool = AsyncMock()
        monkeypatch.setattr("butlers.core.deploy._make_pool", AsyncMock(return_value=owned_pool))
        monkeypatch.setattr(
            "butlers.core.deploy.record_deployment", AsyncMock(return_value="row-1")
        )
        monkeypatch.setattr(
            "butlers.core.deploy.read_migration_head", AsyncMock(return_value="core_163")
        )

        await run_deploy(_config())

        owned_pool.close.assert_awaited_once()

    async def test_idempotent_rerun_after_a_failure_can_succeed(self, monkeypatch):
        """Re-running the whole pipeline after a failed attempt must work —
        no leftover state from the previous run blocks it."""
        pool = AsyncMock()
        record_mock = AsyncMock(return_value="row-1")
        monkeypatch.setattr("butlers.core.deploy.record_deployment", record_mock)
        monkeypatch.setattr(
            "butlers.core.deploy.read_migration_head", AsyncMock(return_value="core_163")
        )

        self._patch_phases(monkeypatch, fail_at="recreate")
        with pytest.raises(DeployError):
            await run_deploy(_config(), pool=pool)

        self._patch_phases(monkeypatch)  # second run: nothing fails
        result = await run_deploy(_config(), pool=pool)

        assert result.result == "success"
        assert record_mock.await_count == 2
