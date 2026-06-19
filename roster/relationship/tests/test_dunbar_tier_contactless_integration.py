"""Testcontainers integration tests for PATCH /entities/{entity_id}/dunbar-tier.

Exercises the **contactless** path added in PR #2384 (bu-oz2bd):
- Entity with NO linked contact → writes directly to the ``facts`` table with
  subject=``entity:{entity_id}``, predicate=``dunbar_tier_override``,
  scope=``relationship``, validity=``active``, permanence=``permanent``.

Also covers the contact-linked path (delegates to _dunbar.dunbar_tier_set)
and cross-path retract semantics.

All tests run against a real Postgres instance spun up by testcontainers.
No mocks are used for the database layer.
"""

from __future__ import annotations

import shutil
import uuid
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_DUNBAR_TIER_PATH = "/api/relationship/entities/{entity_id}/dunbar-tier"


@pytest.fixture
async def tier_pool(provisioned_postgres_pool):
    """Fresh DB with public.entities, contacts and facts tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name VARCHAR NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                entity_type VARCHAR NOT NULL DEFAULT 'other',
                aliases TEXT[] NOT NULL DEFAULT '{}',
                metadata JSONB DEFAULT '{}'::jsonb,
                roles TEXT[] NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                first_name TEXT,
                last_name TEXT,
                entity_id UUID,
                stay_in_touch_days INT,
                listed BOOLEAN NOT NULL DEFAULT true,
                archived_at TIMESTAMPTZ,
                metadata JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                embedding TEXT,
                validity TEXT NOT NULL DEFAULT 'active',
                scope TEXT NOT NULL DEFAULT 'global',
                entity_id UUID,
                valid_at TIMESTAMPTZ,
                metadata JSONB DEFAULT '{}'::jsonb,
                permanence TEXT NOT NULL DEFAULT 'standard',
                importance FLOAT NOT NULL DEFAULT 5.0,
                confidence FLOAT NOT NULL DEFAULT 1.0,
                decay_rate FLOAT NOT NULL DEFAULT 0.008,
                source_butler TEXT,
                source_episode_id UUID,
                supersedes_id UUID,
                reference_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_referenced_at TIMESTAMPTZ,
                last_confirmed_at TIMESTAMPTZ,
                tags JSONB DEFAULT '[]'::jsonb,
                tenant_id TEXT NOT NULL DEFAULT 'owner',
                request_id TEXT,
                idempotency_key TEXT,
                observed_at TIMESTAMPTZ DEFAULT now(),
                invalid_at TIMESTAMPTZ,
                retention_class TEXT NOT NULL DEFAULT 'operational',
                sensitivity TEXT NOT NULL DEFAULT 'normal',
                search_vector tsvector,
                embedding_model_version TEXT DEFAULT 'unknown'
            )
        """)
        await p.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_subj_pred ON facts (subject, predicate)"
        )
        # contact_entity_map (rel_029) — contact_id → entity_id bridge.
        # patch_entity_dunbar_tier looks here instead of contacts.entity_id (bu-j77a5).
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contact_entity_map (
                contact_id  UUID NOT NULL,
                entity_id   UUID NOT NULL,
                CONSTRAINT contact_entity_map_pkey PRIMARY KEY (contact_id)
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_contact_entity_map_entity_id
                ON contact_entity_map (entity_id)
        """)
        yield p


# ---------------------------------------------------------------------------
# App wiring helper
# ---------------------------------------------------------------------------


def _wire_app_with_real_pool(real_pool) -> FastAPI:
    """Wire a fresh create_app() instance to use the given real asyncpg pool.

    Mirrors the ``_wire_app(mock_pool)`` pattern in test_entities_api.py,
    but uses the real pool so SQL is executed against the testcontainers DB.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = real_pool
    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break
    return app


async def _patch(app: FastAPI, entity_id: uuid.UUID, *, tier) -> httpx.Response:
    path = _DUNBAR_TIER_PATH.format(entity_id=entity_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.patch(path, json={"tier": tier})


# ---------------------------------------------------------------------------
# Helper: create a bare entity (no linked contact)
# ---------------------------------------------------------------------------


async def _create_entity(pool, name: str) -> uuid.UUID:
    row = await pool.fetchrow("INSERT INTO public.entities (name) VALUES ($1) RETURNING id", name)
    return row["id"]


async def _create_entity_with_contact(pool, name: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (entity_id, contact_id)."""
    entity_id = await _create_entity(pool, name)
    contact_row = await pool.fetchrow(
        "INSERT INTO contacts (first_name, entity_id) VALUES ($1, $2) RETURNING id",
        name,
        entity_id,
    )
    contact_id = contact_row["id"]
    # Populate contact_entity_map (rel_029) — patch_entity_dunbar_tier looks here
    # instead of contacts.entity_id to find a linked contact (bu-j77a5).
    await pool.execute(
        "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
        contact_id,
        entity_id,
    )
    return entity_id, contact_id


# ===========================================================================
# CONTACTLESS PATH TESTS
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contactless_set_tier_returns_200(tier_pool):
    """PATCH with a valid tier for a contactless entity → HTTP 200, action='set'."""
    entity_id = await _create_entity(tier_pool, "BarePerson")
    app = _wire_app_with_real_pool(tier_pool)

    resp = await _patch(app, entity_id, tier=15)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entity_id"] == str(entity_id)
    assert body["contact_id"] is None
    assert body["tier"] == 15
    assert body["action"] == "set"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contactless_set_tier_persists_fact_row(tier_pool):
    """The INSERT actually lands in the facts table with the correct columns.

    This is the core regression guard: verify the real SQL runs without error
    and the resulting row has the correct subject, predicate, content, scope,
    entity_id, validity, and permanence.
    """
    entity_id = await _create_entity(tier_pool, "FactPerson")
    app = _wire_app_with_real_pool(tier_pool)

    resp = await _patch(app, entity_id, tier=50)
    assert resp.status_code == 200, resp.text

    row = await tier_pool.fetchrow(
        """
        SELECT subject, predicate, content, scope, entity_id, validity, permanence
        FROM facts
        WHERE predicate = 'dunbar_tier_override'
          AND scope = 'relationship'
          AND entity_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        entity_id,
    )

    assert row is not None, "Expected a fact row to be inserted"
    assert row["subject"] == f"entity:{entity_id}"
    assert row["predicate"] == "dunbar_tier_override"
    assert row["content"] == "50"
    assert row["scope"] == "relationship"
    assert row["entity_id"] == entity_id
    assert row["validity"] == "active"
    assert row["permanence"] == "permanent"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contactless_repin_retracts_old_and_inserts_new(tier_pool):
    """Re-PATCHing to a different tier retracts the old fact and inserts a fresh one."""
    entity_id = await _create_entity(tier_pool, "RepinPerson")
    app = _wire_app_with_real_pool(tier_pool)

    # Initial pin
    resp1 = await _patch(app, entity_id, tier=15)
    assert resp1.status_code == 200, resp1.text

    # Re-pin to a different tier
    resp2 = await _patch(app, entity_id, tier=50)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["tier"] == 50

    rows = await tier_pool.fetch(
        """
        SELECT content, validity
        FROM facts
        WHERE predicate = 'dunbar_tier_override'
          AND scope = 'relationship'
          AND entity_id = $1
        ORDER BY created_at
        """,
        entity_id,
    )
    assert len(rows) == 2, f"Expected 2 fact rows, got {len(rows)}"
    # First row (tier=15) should be retracted
    assert rows[0]["content"] == "15"
    assert rows[0]["validity"] == "retracted"
    # Second row (tier=50) should be active
    assert rows[1]["content"] == "50"
    assert rows[1]["validity"] == "active"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contactless_clear_tier_retracts_existing_override(tier_pool):
    """PATCH with tier=null clears the override (retracts the active fact)."""
    entity_id = await _create_entity(tier_pool, "ClearPerson")
    app = _wire_app_with_real_pool(tier_pool)

    # Pin first
    resp1 = await _patch(app, entity_id, tier=150)
    assert resp1.status_code == 200, resp1.text

    # Clear
    resp2 = await _patch(app, entity_id, tier=None)
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["action"] == "cleared"
    assert body2["tier"] is None
    assert body2["contact_id"] is None

    # Fact should now be retracted
    row = await tier_pool.fetchrow(
        """
        SELECT validity FROM facts
        WHERE predicate = 'dunbar_tier_override'
          AND scope = 'relationship'
          AND entity_id = $1
        """,
        entity_id,
    )
    assert row is not None
    assert row["validity"] == "retracted"

    # And no new active fact inserted
    active = await tier_pool.fetchval(
        """
        SELECT COUNT(*) FROM facts
        WHERE predicate = 'dunbar_tier_override'
          AND scope = 'relationship'
          AND entity_id = $1
          AND validity = 'active'
        """,
        entity_id,
    )
    assert int(active) == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contactless_invalid_tier_returns_422(tier_pool):
    """Invalid tier value → HTTP 422."""
    entity_id = await _create_entity(tier_pool, "InvalidTierPerson")
    app = _wire_app_with_real_pool(tier_pool)

    resp = await _patch(app, entity_id, tier=99)

    assert resp.status_code == 422, resp.text


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_missing_entity_returns_404(tier_pool):
    """Entity that does not exist → HTTP 404."""
    app = _wire_app_with_real_pool(tier_pool)
    missing_id = uuid.uuid4()

    resp = await _patch(app, missing_id, tier=15)

    assert resp.status_code == 404, resp.text


# ===========================================================================
# CONTACT-LINKED PATH TESTS
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contact_linked_set_tier_returns_200(tier_pool):
    """PATCH for an entity WITH a linked contact → HTTP 200, contact_id populated."""
    entity_id, contact_id = await _create_entity_with_contact(tier_pool, "LinkedPerson")
    app = _wire_app_with_real_pool(tier_pool)

    resp = await _patch(app, entity_id, tier=5)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entity_id"] == str(entity_id)
    assert body["contact_id"] == str(contact_id)
    assert body["tier"] == 5
    assert body["action"] == "set"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_contact_linked_set_tier_persists_fact_row(tier_pool):
    """Contact-linked path also persists a dunbar_tier_override fact row."""
    entity_id, _contact_id = await _create_entity_with_contact(tier_pool, "LinkedFactPerson")
    app = _wire_app_with_real_pool(tier_pool)

    resp = await _patch(app, entity_id, tier=15)
    assert resp.status_code == 200, resp.text

    row = await tier_pool.fetchrow(
        """
        SELECT predicate, content, scope, validity
        FROM facts
        WHERE predicate = 'dunbar_tier_override'
          AND scope = 'relationship'
          AND entity_id = $1
          AND validity = 'active'
        LIMIT 1
        """,
        entity_id,
    )
    assert row is not None, "Expected a fact row to be inserted via contact-linked path"
    assert row["content"] == "15"
    assert row["validity"] == "active"
