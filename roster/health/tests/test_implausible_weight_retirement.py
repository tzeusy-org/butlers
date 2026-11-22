"""health_009 retracts implausible Home Assistant weight facts (bu-swkpoo.11).

Real PostgreSQL (Docker), CI only.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "009_retract_implausible_weight_facts.py"
)


@pytest.fixture
async def memory_pool(db):
    return await db("core", "memory")


def _migration_sql(step: str) -> list[str]:
    spec = importlib.util.spec_from_file_location("health_009", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (mod.revision, mod.down_revision) == ("health_009", "health_007")
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = sqls.append
    with patch.object(mod, "op", mock_op):
        getattr(mod, step)()
    return sqls


async def _run(pool, step: str) -> None:
    for sql in _migration_sql(step):
        await pool.execute(sql)


async def _fact(
    pool,
    predicate: str,
    value: float,
    days_ago: int,
    *,
    source: str = "home_assistant",
    validity: str = "active",
) -> uuid.UUID:
    # Real readings carry valid_at (wellness_ingest passes it), which exempts them from the
    # one-live-fact-per-key unique indexes; without it the fixture rows would collide.
    metadata = {"source": source, "value": value, "unit": "kg"}
    return await pool.fetchval(
        "INSERT INTO facts (subject, predicate, content, scope, permanence, decay_rate, "
        "validity, confidence, importance, source_butler, metadata, valid_at) "
        "VALUES ('owner', $1, 'w', 'health', 'permanent', 0.0, $2, 1.0, 5.0, 'health', "
        "$3::text::jsonb, now() - make_interval(days => $4)) RETURNING id",
        predicate,
        validity,
        json.dumps(metadata),
        days_ago,
    )


async def _validity(pool, fact_id: uuid.UUID) -> str:
    return await pool.fetchval("SELECT validity FROM facts WHERE id = $1", fact_id)


async def test_only_ha_weight_outliers_against_their_window_are_retracted(memory_pool) -> None:
    for offset, value in enumerate((70.0, 71.0, 69.5, 70.5)):
        await _fact(memory_pool, "measurement_weight", value, 20 + offset, source="google_health")
    outliers = [
        await _fact(memory_pool, "measurement_weight", 50.0, 21),
        await _fact(memory_pool, "measurement_weight", 49.0, 24),
    ]
    near = await _fact(memory_pool, "measurement_weight", 72.0, 22)
    other_source = await _fact(memory_pool, "measurement_weight", 50.0, 23, source="google_health")
    other_predicate = await _fact(memory_pool, "measurement_body_fat", 50.0, 22)
    lonely = await _fact(memory_pool, "measurement_weight", 40.0, 400)

    await _run(memory_pool, "upgrade")

    assert [await _validity(memory_pool, f) for f in outliers] == ["retracted"] * 2
    for kept in (near, other_source, other_predicate, lonely):
        assert await _validity(memory_pool, kept) == "active"

    await _run(memory_pool, "upgrade")  # idempotent
    assert [await _validity(memory_pool, f) for f in outliers] == ["retracted"] * 2
    assert await _validity(memory_pool, near) == "active"
