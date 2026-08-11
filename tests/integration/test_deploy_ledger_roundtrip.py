"""Real-Postgres regression: `butlers deploy` ledger writes (bu-9r3hd.3).

Exercises butlers.core.deploy.run_deploy's ledger-writing behavior against a
fully migrated Postgres instance (testcontainers) — the docker/subprocess/
httpx boundaries are mocked (container recreation cannot run in CI; see
tests/core/test_deploy.py for that coverage), but the actual
public.deployments write goes through the real production writer
(butlers.core.deployments.record_deployment), same as
tests/integration/test_deployments_roundtrip.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import asyncpg
import pytest

from butlers.core.deploy import DeployConfig, DeployError, RestoreDrillEndpoint, run_deploy
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    yield p
    await p.close()


def _patch_pipeline(monkeypatch, *, git_sha: str, fail_at: str | None = None) -> None:
    def make(name):
        def _fn(config, *a):
            if fail_at == name:
                raise DeployError(name, f"{name} boom")

        return _fn

    async def _wait_ok(config):
        if fail_at == "health-check":
            raise DeployError("health-check", "health-check boom")

    monkeypatch.setattr("butlers.core.deploy.build_image", make("build"))
    monkeypatch.setattr("butlers.core.deploy.run_migrations", make("migrate"))
    # Best-effort `bd export` refresh (bu-hmdqz.6) -- never touch the real
    # `bd`/Dolt server from a test process; stub as a no-op success.
    monkeypatch.setattr("butlers.core.deploy.materialize_beads_export", lambda config: True)
    monkeypatch.setattr(
        "butlers.core.deploy.prepare_restore_drill_executor",
        make("restore-drill-boundary"),
    )
    monkeypatch.setattr("butlers.core.deploy.recreate_services", make("recreate"))
    monkeypatch.setattr(
        "butlers.core.deploy._resolve_restore_drill_endpoint",
        lambda config: RestoreDrillEndpoint("postgres.example.test", "198.51.100.42", 5432),
    )
    monkeypatch.setattr("butlers.core.deploy.wait_for_health", _wait_ok)
    monkeypatch.setattr("butlers.core.deploy.resolve_git_sha", lambda repo_root: git_sha)
    # Preflight (linked-worktree / non-ancestor-HEAD guard) needs a real git
    # repo; this roundtrip uses a fake `/repo` path, so stub it as a clean pass.
    monkeypatch.setattr("butlers.core.deploy.preflight_check", lambda config: ())


async def test_successful_deploy_writes_success_row(monkeypatch, pool: asyncpg.Pool) -> None:
    _patch_pipeline(monkeypatch, git_sha="sha-success")

    result = await run_deploy(DeployConfig(repo_root=Path("/repo")), pool=pool)

    assert result.result == "success"
    row = await pool.fetchrow(
        """
        SELECT git_sha, result, source, serving_mode, serving_worktree
        FROM public.deployments
        WHERE git_sha = $1
        """,
        "sha-success",
    )
    assert row["result"] == "success"
    assert row["source"] == "deploy"
    assert row["serving_mode"] == "image"
    assert row["serving_worktree"] is None


@pytest.mark.parametrize("fail_at", ["build", "migrate", "recreate", "health-check"])
async def test_failed_deploy_writes_failed_row_not_silent(
    monkeypatch, pool: asyncpg.Pool, fail_at
) -> None:
    """A failed deploy must show up in the ledger — never silently vanish."""
    git_sha = f"sha-fail-{fail_at}"
    _patch_pipeline(monkeypatch, git_sha=git_sha, fail_at=fail_at)

    with pytest.raises(DeployError):
        await run_deploy(DeployConfig(repo_root=Path("/repo")), pool=pool)

    row = await pool.fetchrow(
        """
        SELECT git_sha, result, source, serving_mode, serving_worktree
        FROM public.deployments
        WHERE git_sha = $1
        """,
        git_sha,
    )
    assert row is not None
    assert row["result"] == "failed"
    assert row["source"] == "deploy"
    assert row["serving_mode"] == "image"
    assert row["serving_worktree"] is None


async def test_migration_head_is_read_from_the_real_public_schema(
    monkeypatch, pool: asyncpg.Pool
) -> None:
    """migration_head in the recorded row must reflect the real alembic_version,
    not a mock — the one thing this test adds over the mocked-pool unit tests."""
    _patch_pipeline(monkeypatch, git_sha="sha-head-check")

    result = await run_deploy(DeployConfig(repo_root=Path("/repo")), pool=pool)

    expected_head = await pool.fetchval("SELECT version_num FROM public.alembic_version LIMIT 1")
    assert result.migration_head == expected_head
    row = await pool.fetchrow(
        "SELECT migration_head FROM public.deployments WHERE git_sha = $1", "sha-head-check"
    )
    assert row["migration_head"] == expected_head
