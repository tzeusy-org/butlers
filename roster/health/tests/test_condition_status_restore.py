"""health_004 restores condition statuses the old decay sweep stripped (bu-cgl4na).

Before bu-pimdvx the nightly decay sweep popped ``metadata.status`` from every decaying
fact, so Health ``condition`` rows lost ``active`` / ``managed`` / ``resolved`` and read as
``active``.  ``health_004`` restores the status from the ``"<name>: <status>"`` content
suffix, tags each restored row, and its downgrade leaves every row unchanged.

Real PostgreSQL (Docker), CI only.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "004_restore_condition_statuses.py"
)


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    from butlers.db import register_jsonb_codec

    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE public.memory_links, public.facts CASCADE")
    yield p
    await p.close()


def _migration_sql(step: str) -> list[str]:
    spec = importlib.util.spec_from_file_location("health_004", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (mod.revision, mod.down_revision) == ("health_004", "health_003")
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = sqls.append
    with patch.object(mod, "op", mock_op):
        getattr(mod, step)()
    return sqls


async def _run(pool, step: str) -> None:
    for sql in _migration_sql(step):
        await pool.execute(sql)


async def _condition(
    pool,
    subject: str,
    content: str,
    metadata: dict | None = None,
    *,
    predicate: str = "condition",
    scope: str = "health",
    validity: str = "active",
) -> None:
    await pool.execute(
        "INSERT INTO facts (subject, predicate, content, scope, permanence, decay_rate, "
        "validity, confidence, importance, source_butler, metadata) "
        "VALUES ($1, $2, $3, $4, 'permanent', 0.0, $5, 1.0, 5.0, 'health', $6::text::jsonb)",
        subject,
        predicate,
        content,
        scope,
        validity,
        json.dumps(metadata or {}),
    )


async def _metadata(pool, subject: str) -> dict:
    value = await pool.fetchval("SELECT metadata FROM facts WHERE subject = $1", subject)
    return json.loads(value) if isinstance(value, str) else dict(value)


async def test_stripped_condition_regains_its_status_from_content(pool) -> None:
    await _condition(pool, "asthma", "Asthma: managed", {"name": "Asthma"})
    await _condition(pool, "flu", "Flu: resolved", {"name": "Flu"})
    await _condition(pool, "ibs", "IBS: active", {"name": "IBS"})
    await _condition(pool, "odd", "Back pain: flare: managed", {"name": "Back pain: flare"})
    await _condition(pool, "fading", "Gout: resolved", {"name": "Gout"}, validity="fading")

    await _run(pool, "upgrade")

    for subject, status in [
        ("asthma", "managed"),
        ("flu", "resolved"),
        ("ibs", "active"),
        ("odd", "managed"),
        ("fading", "resolved"),
    ]:
        meta = await _metadata(pool, subject)
        assert meta["status"] == status, subject
        assert meta["status_restored_by"] == "health_004", subject
    assert (await _metadata(pool, "asthma"))["name"] == "Asthma"


async def test_rows_with_a_status_or_unparseable_content_are_untouched(pool) -> None:
    await _condition(pool, "has-status", "Eczema: active", {"name": "Eczema", "status": "resolved"})
    await _condition(pool, "bad-suffix", "Migraine: severe", {"name": "Migraine"})
    await _condition(pool, "no-suffix", "Migraine", {"name": "Migraine"})
    await _condition(pool, "other-pred", "Sleep: managed", {}, predicate="symptom")
    await _condition(pool, "other-scope", "Acne: managed", {}, scope="relationship")
    await _condition(pool, "retracted", "Cold: managed", {}, validity="retracted")
    before = {
        r["subject"]: r["metadata"] for r in await pool.fetch("SELECT subject, metadata FROM facts")
    }

    await _run(pool, "upgrade")

    after = {
        r["subject"]: r["metadata"] for r in await pool.fetch("SELECT subject, metadata FROM facts")
    }
    assert after == before
    assert (await _metadata(pool, "has-status"))["status"] == "resolved"


async def test_upgrade_is_idempotent(pool) -> None:
    await _condition(pool, "asthma", "Asthma: managed", {"name": "Asthma"})

    await _run(pool, "upgrade")
    first = await _metadata(pool, "asthma")
    await _run(pool, "upgrade")

    assert await _metadata(pool, "asthma") == first


async def test_downgrade_leaves_restored_rows_unchanged(pool) -> None:
    await _condition(pool, "stripped", "Asthma: managed", {"name": "Asthma", "notes": "n"})
    # condition_update copies metadata forward, so a row the owner updated after the repair
    # carries the tag with a status the owner chose.
    await _condition(
        pool,
        "inherited",
        "Eczema: resolved",
        {"name": "Eczema", "status": "resolved", "status_restored_by": "health_004"},
    )
    await _run(pool, "upgrade")
    restored = await _metadata(pool, "stripped")
    assert restored["status"] == "managed"

    await _run(pool, "downgrade")

    assert await _metadata(pool, "stripped") == restored
    assert await _metadata(pool, "inherited") == {
        "name": "Eczema",
        "status": "resolved",
        "status_restored_by": "health_004",
    }
