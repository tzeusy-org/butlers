"""Real-PostgreSQL cadence projection over the Relationship fact taxonomy."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "relationship"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    connection_pool = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=2, init=register_jsonb_codec
    )
    yield connection_pool
    await connection_pool.close()


async def test_cadence_counts_only_stable_interaction_events(pool: asyncpg.Pool) -> None:
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ('Cadence test person', 'person') RETURNING id"
    )
    observed_at = datetime.now(UTC) - timedelta(hours=1)
    for index, (predicate, permanence, validity) in enumerate(
        (
            ("interaction_call", "stable", "active"),
            ("interaction_note", "ephemeral", "active"),
            ("interaction_note", "stable", "active"),
            ("interactionXcall", "stable", "active"),
            ("interaction_email", "ephemeral", "active"),
            ("interaction_meeting", "stable", "retracted"),
        )
    ):
        await pool.execute(
            """
            INSERT INTO facts (
                subject, entity_id, predicate, content, scope, validity,
                permanence, valid_at
            ) VALUES ($1, $2, $3, $4, 'relationship', $5, $6, $7)
            """,
            f"entity:{entity_id}",
            entity_id,
            predicate,
            f"cadence-test-{index}",
            validity,
            permanence,
            observed_at - timedelta(minutes=index),
        )

    # Exercise the real route and SQL against migrated PostgreSQL. Owner auth
    # belongs to the mounted-app API suite; this case pins the row taxonomy.
    router_path = Path(__file__).resolve().parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location("relationship_cadence_db_router", router_path)
    assert spec is not None and spec.loader is not None
    router_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = router_module
    spec.loader.exec_module(router_module)
    app = FastAPI()
    app.include_router(router_module.router)
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app.dependency_overrides[router_module._get_db_manager] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = f"/api/relationship/entities/{entity_id}/cadence"
        response = await client.get(path, params={"window_days": 30, "limit": 10})
        assert response.status_code == 200
        assert response.json()["interaction_count"] == 1
        assert response.json()["completeness"] == "complete"
        assert response.json()["has_more"] is False

        await pool.execute(
            "DELETE FROM facts WHERE entity_id = $1 AND predicate = 'interaction_call'",
            entity_id,
        )
        without_event = await client.get(path, params={"window_days": 30, "limit": 10})
        assert without_event.status_code == 200
        assert without_event.json()["interaction_count"] == 0
        assert without_event.json()["completeness"] == "complete"
