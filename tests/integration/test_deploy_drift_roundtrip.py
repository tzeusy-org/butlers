"""Real-Postgres regression: the migration-drift sentinel (bu-9r3hd.1).

Exercises butlers.jobs.deploy_drift.compute_drift_report against a real,
fully-migrated Postgres instance (testcontainers) reading the actual
alembic_version table -- not just the mocked-pool unit tests in
tests/jobs/test_deploy_drift.py (mirroring the split used for
tests/integration/test_deployments_roundtrip.py vs tests/core/test_deployments.py).

- A schema migrated to the real codebase head is reported as not drifted.
- A schema whose alembic_version is manually rolled back to the head's parent
  revision (simulating the bu-zhfd0 incident: a merged revision never
  actually deployed) is reported as drifted, with the correct expected/actual
  revision pair.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.jobs.deploy_drift import compute_drift_report
from butlers.migrations import _chain_script_directory, get_chain_head
from butlers.testing.db_manager import db_manager

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def switchboard_pool(db) -> asyncpg.Pool:
    """The core chain migrated into the switchboard schema, as deployed."""
    return await db("core", schemas={"core": "switchboard"})


def _only_core_for_switchboard(monkeypatch) -> None:
    """Scope the comparison to a single schema/chain: this test's real DB."""
    monkeypatch.setattr(
        "butlers.jobs.deploy_drift._expected_chains_by_schema",
        lambda: {"switchboard": ["core"]},
    )


async def test_freshly_migrated_schema_is_not_drifted(
    switchboard_pool: asyncpg.Pool, monkeypatch
) -> None:
    _only_core_for_switchboard(monkeypatch)
    db = db_manager({"switchboard": switchboard_pool})

    report = await compute_drift_report(db)

    assert report.is_available
    assert not report.is_drifted


async def test_rolled_back_alembic_version_is_detected_as_drift(
    switchboard_pool: asyncpg.Pool, monkeypatch
) -> None:
    _only_core_for_switchboard(monkeypatch)
    db = db_manager({"switchboard": switchboard_pool})

    head = get_chain_head("core")
    parent = _chain_script_directory("core").get_revision(head).down_revision
    assert parent is not None, "core chain must have more than one revision for this test"

    # Simulate the bu-zhfd0 shape: the DB never actually applied the head
    # revision, even though the codebase (and, in the real incident, the
    # merged PR) already has it.
    await switchboard_pool.execute(
        'UPDATE "switchboard".alembic_version SET version_num = $1 WHERE version_num = $2',
        parent,
        head,
    )

    report = await compute_drift_report(db)

    assert report.is_available
    assert report.is_drifted
    assert len(report.drifted) == 1
    drift = report.drifted[0]
    assert drift.schema == "switchboard"
    assert drift.chain == "core"
    assert drift.expected_head == head
    assert drift.actual_revision == parent
