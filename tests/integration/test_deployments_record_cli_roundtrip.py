"""Real-Postgres roundtrip: `butlers deployments record` writes a deploy row (bu-viat6h.5).

The Helm deploy wrapper (scripts/k8s/record-deploy.sh) runs this verb inside the
dashboard-api pod; here the verb runs in-process against a migrated Postgres and
the row is read back through plain SQL.
"""

from __future__ import annotations

import asyncio
import shutil
from urllib.parse import urlparse

import pytest
from click.testing import CliRunner

from butlers.cli import cli
from butlers.core.deployments import resolve_core_migration_head

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def test_record_verb_writes_one_image_served_deploy_row(monkeypatch, db, fresh_db) -> None:
    migrated_db_url = await asyncio.to_thread(fresh_db, "core")
    # The verb opens its own pool from the environment, as it does in the pod.
    monkeypatch.setenv("DATABASE_URL", migrated_db_url)
    monkeypatch.setenv("POSTGRES_DB", urlparse(migrated_db_url).path.removeprefix("/"))
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
    pool = await db.open(migrated_db_url)
    rows = await pool.fetch(
        """
        SELECT git_sha, result, source, serving_mode, serving_worktree, migration_head
        FROM public.deployments
        WHERE git_sha IN ('pod-image-sha', 'tag-x')
        ORDER BY git_sha
        """
    )
    head = await resolve_core_migration_head(pool)
    assert head is not None  # the migrated database really has a core head to record
    assert [tuple(r) for r in rows] == [
        ("pod-image-sha", "success", "deploy", "image", None, head),
        ("tag-x", "failed", "deploy", "image", None, head),
    ]
