"""Integration coverage for manual measurement provenance in the sources read model.

This covers the complete health write-to-read path against the migrated fact
store: owner ``measurement_log`` writes, connector-shaped facts, in-place
measurement updates, and the dashboard's source aggregation endpoint.
"""

from __future__ import annotations

import shutil
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# Router discovery registers the dependency under this module name. FastAPI's
# dependency overrides key on the function object, so keep that exact object.
_APP_SEED = create_app(api_key="")
_health_get_db_manager = sys.modules["health_api_router"]._get_db_manager


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the real core and memory fact-store schema once for this module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an isolated JSONB-aware fact-store pool for each test."""
    pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await pool.execute("TRUNCATE TABLE public.memory_links, public.facts CASCADE")
    yield pool
    await pool.close()


@pytest.fixture(autouse=True)
def _fake_embedding_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real fact-store write deterministic without loading a model."""
    from butlers.tools.health import measurements

    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    engine.model_name = "test-model"
    monkeypatch.setattr(measurements, "_get_embedding_engine", lambda: engine)


class _SinglePoolDB:
    """Minimal health-scoped DatabaseManager surface for the API route."""

    def __init__(self, pool: object) -> None:
        self._pool = pool

    def pool(self, name: str) -> object:
        if name != "health":
            raise KeyError(f"No pool for butler: {name}")
        return self._pool


@asynccontextmanager
async def _sources_client(pool: object):
    app = create_app(api_key="")
    app.dependency_overrides[_health_get_db_manager] = lambda: _SinglePoolDB(pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _insert_measurement_fact(
    pool: asyncpg.Pool,
    *,
    predicate: str,
    valid_at: datetime,
    metadata: dict[str, object],
) -> uuid.UUID:
    """Insert one connector-shaped or historical measurement fact."""
    fact_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO facts (id, subject, predicate, content, valid_at, metadata, validity, scope)
        VALUES ($1, 'owner', $2, $3, $4, $5, 'active', 'health')
        """,
        fact_id,
        predicate,
        f"fixture:{predicate}",
        valid_at,
        metadata,
    )
    return fact_id


async def test_measurement_sources_aggregate_owner_and_connector_provenance(
    pool: asyncpg.Pool,
) -> None:
    """Only source-bearing facts appear, including new owner logs and legacy providers."""
    from butlers.tools.health import measurement_log, measurement_update

    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    owner_log = await measurement_log(pool, "weight", 70, measured_at=now)
    await measurement_update(pool, str(owner_log["id"]), notes="corrected")

    canonical_id = await _insert_measurement_fact(
        pool,
        predicate="measurement_heart_rate",
        valid_at=now - timedelta(minutes=1),
        metadata={"value": 65, "source": "google_health"},
    )
    await measurement_update(pool, str(canonical_id), notes="connector correction")

    legacy_id = await _insert_measurement_fact(
        pool,
        predicate="measurement_temperature",
        valid_at=now - timedelta(minutes=2),
        metadata={"value": 36.8, "provider": "home_assistant"},
    )
    await measurement_update(pool, str(legacy_id), notes="legacy connector correction")

    await _insert_measurement_fact(
        pool,
        predicate="measurement_blood_sugar",
        valid_at=now - timedelta(minutes=3),
        metadata={"value": 95},
    )

    owner_metadata = await pool.fetchval(
        "SELECT metadata FROM facts WHERE id = $1", owner_log["id"]
    )
    canonical_metadata = await pool.fetchval(
        "SELECT metadata FROM facts WHERE id = $1", canonical_id
    )
    legacy_metadata = await pool.fetchval("SELECT metadata FROM facts WHERE id = $1", legacy_id)
    assert owner_metadata["source"] == "owner_log"
    assert canonical_metadata["source"] == "google_health"
    assert legacy_metadata["provider"] == "home_assistant"

    all_measurement_count = await pool.fetchval(
        """
        SELECT count(*)
        FROM facts
        WHERE predicate LIKE 'measurement~_%' ESCAPE '~'
          AND scope = 'health'
          AND validity = 'active'
        """
    )
    assert all_measurement_count == 4

    async with _sources_client(pool) as client:
        response = await client.get("/api/health/measurements/sources")

    assert response.status_code == 200, response.text
    source_counts = {
        source["name"]: source["sample_count"] for source in response.json()["sources"]
    }
    assert source_counts == {
        "owner_log": 1,
        "google_health": 1,
        "home_assistant": 1,
    }
    assert sum(source_counts.values()) == 3
