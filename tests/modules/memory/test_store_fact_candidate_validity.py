# ruff: noqa: F811
"""``store_fact(validity='candidate')`` holds a fact without asserting it (bu-swkpoo.11).

Real PostgreSQL (Docker), CI only, except the argument guard.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import pytest

from butlers.modules.memory import storage
from tests.modules.memory.test_content_authority import (  # noqa: F401
    _engine,
    pool_factory,
)
from tests.modules.memory.test_knowledge_gaps import _entity, session_hook  # noqa: F401
from tests.modules.memory.test_memory_migration_integration import (  # noqa: F401
    memory_migrated_db,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")
needs_docker = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_PREDICATE = "measurement_weight"


async def test_unknown_validity_is_refused() -> None:
    with pytest.raises(ValueError, match="validity"):
        await storage.store_fact(
            None,  # type: ignore[arg-type]
            "owner",
            _PREDICATE,
            "w",
            None,  # type: ignore[arg-type]
            validity="expired",
        )


async def _store(pool, entity, valid_at, key, **extra):
    return await storage.store_fact(
        pool,
        "owner",
        _PREDICATE,
        "w",
        _engine(),
        entity_id=entity,
        scope="health",
        valid_at=valid_at,
        idempotency_key=key,
        **extra,
    )


@needs_docker[0]
@needs_docker[1]
async def test_held_reading_leaves_the_active_fact_and_dedupes_on_redelivery(
    pool_factory, session_hook
) -> None:
    pool, _owner, _ = await pool_factory()
    session_hook(pool)
    entity = await _entity(pool)
    active = await _store(pool, entity, datetime(2026, 11, 1, tzinfo=UTC), uuid.uuid4().hex)

    held_key = uuid.uuid4().hex
    held_at = datetime(2026, 11, 2, tzinfo=UTC)
    held = await _store(pool, entity, held_at, held_key, validity="candidate")
    again = await _store(pool, entity, held_at, held_key, validity="candidate")

    assert again["id"] == held["id"]
    rows = {
        r["id"]: r["validity"]
        for r in await pool.fetch(
            "SELECT id, validity FROM facts WHERE entity_id = $1 AND predicate = $2",
            entity,
            _PREDICATE,
        )
    }
    assert rows == {active["id"]: "active", held["id"]: "candidate"}
