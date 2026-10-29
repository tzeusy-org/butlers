"""Real-Postgres roundtrip: `butlers deployments record` writes a deploy row (bu-viat6h.5).

The Helm deploy wrapper (scripts/k8s/record-deploy.sh) runs this verb inside the
dashboard-api pod; here the verb runs in-process against a migrated Postgres and
the row is read back through plain SQL.
"""

from __future__ import annotations

import asyncio
import shutil

import asyncpg
import pytest
from click.testing import CliRunner

from butlers.cli import cli
from butlers.core.deployments import resolve_core_migration_head
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


async def test_record_verb_writes_one_image_served_deploy_row(
    monkeypatch, migrated_db_url: str
) -> None:
    async def _make_pool() -> asyncpg.Pool:
        return await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=2)

    monkeypatch.setattr("butlers.core.deploy._make_pool", _make_pool)
    monkeypatch.setenv("GIT_SHA", "pod-image-sha")

    # The verb owns its event loop (asyncio.run), so run it off the test loop.
    result = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        ["deployments", "record", "--source", "deploy", "--result", "success"],
    )
    failed = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        ["deployments", "record", "--source", "deploy", "--result", "failed", "--git-sha", "tag-x"],
    )

    assert result.exit_code == 0, result.output
    assert failed.exit_code == 0, failed.output
    conn = await asyncpg.connect(migrated_db_url)
    try:
        rows = await conn.fetch(
            """
            SELECT git_sha, result, source, serving_mode, serving_worktree, migration_head
            FROM public.deployments
            WHERE git_sha IN ('pod-image-sha', 'tag-x')
            ORDER BY git_sha
            """
        )
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=1)
    try:
        head = await resolve_core_migration_head(pool)
    finally:
        await pool.close()
    assert head is not None  # the migrated database really has a core head to record
    assert [tuple(r) for r in rows] == [
        ("pod-image-sha", "success", "deploy", "image", None, head),
        ("tag-x", "failed", "deploy", "image", None, head),
    ]
