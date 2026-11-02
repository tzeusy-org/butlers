"""workout_last against real Postgres facts (CI-only: needs Docker)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


class _StubEmbeddingEngine:
    model_name = "stub"

    def embed(self, text: str) -> list[float]:
        return [0.0] * 384


@pytest.fixture
async def memory_pool(db):
    """A private clone migrated with the core and memory chains."""
    return await db("core", "memory")


async def _store(
    pool: asyncpg.Pool,
    start: datetime,
    activity: str = "running",
    *,
    detection: str = "auto",
    minutes: int = 30,
    scope: str = "health",
) -> str:
    from butlers.modules.memory.storage import store_fact

    metadata: dict[str, Any] = {
        "activity_type": activity,
        "duration_ms": minutes * 60000,
        "detection": detection,
        "source": "google_health",
    }
    stored = await store_fact(
        pool,
        subject="owner",
        predicate="workout_session",
        content=f"{activity} {start.isoformat()}",
        embedding_engine=_StubEmbeddingEngine(),
        scope=scope,
        valid_at=start,
        metadata=metadata,
        idempotency_key=f"{activity}-{start.isoformat()}-{detection}-{scope}",
    )
    return str(stored["id"])


async def test_latest_matching_workout_from_facts(memory_pool) -> None:
    from butlers.tools.health import workout_last

    await _store(memory_pool, datetime(2026, 10, 1, 6, tzinfo=UTC))
    newest = await _store(memory_pool, datetime(2026, 10, 12, 6, tzinfo=UTC), minutes=50)
    await _store(memory_pool, datetime(2026, 10, 14, 6, tzinfo=UTC), "swimming")

    result = await workout_last(memory_pool, "jog", timezone="Pacific/Auckland")
    assert result["found"] is True
    assert result["id"] == newest
    assert result["duration_minutes"] == 50.0
    assert result["started_at"] == "2026-10-12T19:00:00+13:00"


async def test_retracted_and_other_scope_facts_are_ignored(memory_pool) -> None:
    from butlers.tools.health import workout_last

    kept = await _store(memory_pool, datetime(2026, 10, 1, 6, tzinfo=UTC))
    retracted = await _store(memory_pool, datetime(2026, 10, 5, 6, tzinfo=UTC))
    await _store(memory_pool, datetime(2026, 10, 6, 6, tzinfo=UTC), scope="global")
    await memory_pool.execute(
        "UPDATE public.facts SET validity = 'retracted' WHERE id = $1::uuid", retracted
    )

    result = await workout_last(memory_pool, "running")
    assert result["id"] == kept
