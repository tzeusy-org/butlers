"""health_008 retires transient (non-stabilized) scale weight facts (bu-swkpoo.4).

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
    Path(__file__).resolve().parents[1] / "migrations" / "008_retire_transient_weight_facts.py"
)


@pytest.fixture
async def memory_pool(db):
    return await db("core", "memory")


def _migration_sql(step: str) -> list[str]:
    spec = importlib.util.spec_from_file_location("health_008", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (mod.revision, mod.down_revision) == ("health_008", "health_007")
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
    source_entity_id: str | None,
    *,
    validity: str = "active",
) -> uuid.UUID:
    metadata = {"value": 80.0, "unit": "kg"}
    if source_entity_id is not None:
        metadata["source_entity_id"] = source_entity_id
    return await pool.fetchval(
        "INSERT INTO facts (subject, predicate, content, scope, permanence, decay_rate, "
        "validity, confidence, importance, source_butler, metadata) "
        "VALUES ('owner', $1, 'w', 'health', 'permanent', 0.0, $2, 1.0, 5.0, 'health', "
        "$3::text::jsonb) RETURNING id",
        predicate,
        validity,
        json.dumps(metadata),
    )


async def _validity(pool, fact_id: uuid.UUID) -> str:
    return await pool.fetchval("SELECT validity FROM facts WHERE id = $1", fact_id)


async def test_only_transient_weight_facts_are_retracted(memory_pool) -> None:
    transient = [
        await _fact(memory_pool, "measurement_weight", entity)
        for entity in (
            "sensor.bathroom_scale_weight_non_stabilized",
            "sensor.scale_weight_UNSTABILIZED",
            "sensor.scale_weight_non_stable",
            "sensor.scale_weight_unstable",
        )
    ]
    keep = [
        await _fact(memory_pool, "measurement_weight", "sensor.bathroom_scale_weight"),
        await _fact(memory_pool, "measurement_weight", "sensor.nonXstabilized_lookalike"),
        await _fact(memory_pool, "measurement_weight", None),
        await _fact(memory_pool, "measurement_heart_rate", "sensor.hr_non_stabilized"),
    ]
    already = await _fact(
        memory_pool, "measurement_weight", "sensor.s_non_stabilized", validity="superseded"
    )

    await _run(memory_pool, "upgrade")

    assert [await _validity(memory_pool, f) for f in transient] == ["retracted"] * 4
    assert [await _validity(memory_pool, f) for f in keep] == ["active"] * 4
    assert await _validity(memory_pool, already) == "superseded"

    await _run(memory_pool, "upgrade")  # idempotent
    assert [await _validity(memory_pool, f) for f in transient] == ["retracted"] * 4
