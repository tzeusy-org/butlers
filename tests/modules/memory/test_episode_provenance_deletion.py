"""Real-PostgreSQL regression coverage for episode-deletion provenance.

The normal memory cleanup path deletes episodes.  This suite verifies that the
database itself preserves only content-free evidence for the durable records
that cite an expired episode; a mocked pool cannot exercise the foreign-key and
trigger interaction that previously erased provenance.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers.memory import _get_db_manager, list_facts, list_rules
from butlers.db import register_jsonb_codec
from butlers.migrations import run_migrations
from butlers.modules.memory.storage import get_links
from butlers.testing.migration import create_migration_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def memory_migrated_db(postgres_container) -> str:
    """Return a fresh database with the complete memory chain applied."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="memory"))
    return db_url


async def _delete_sourced_episode(db_url: str) -> dict[str, object]:
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, init=register_jsonb_codec)
    try:
        episode_id = uuid.uuid4()
        live_episode_id = uuid.uuid4()
        unresolved_episode_id = uuid.uuid4()
        fact_id = uuid.uuid4()
        live_fact_id = uuid.uuid4()
        unresolved_fact_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        live_rule_id = uuid.uuid4()
        unresolved_rule_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO episodes (id, butler, content) VALUES ($1, 'general', $2)",
            episode_id,
            "raw owner-message content that must not survive deletion",
        )
        await pool.execute(
            "INSERT INTO episodes (id, butler, content) VALUES ($1, 'general', 'live source')",
            live_episode_id,
        )
        await pool.execute(
            "INSERT INTO facts (id, subject, predicate, content, source_episode_id) "
            "VALUES ($1, 'owner', 'preference', 'durable derived fact', $2)",
            fact_id,
            episode_id,
        )
        await pool.execute(
            "INSERT INTO facts (id, subject, predicate, content, source_episode_id) "
            "VALUES ($1, 'owner', 'live_preference', 'live-source fact', $2)",
            live_fact_id,
            live_episode_id,
        )
        await pool.execute(
            "INSERT INTO facts (id, subject, predicate, content, source_episode_id) "
            "VALUES ($1, 'owner', 'unresolved_preference', 'unresolved-source fact', $2)",
            unresolved_fact_id,
            unresolved_episode_id,
        )
        await pool.execute(
            "INSERT INTO rules (id, content, source_episode_id) VALUES ($1, $2, $3)",
            rule_id,
            "durable derived rule",
            episode_id,
        )
        await pool.execute(
            "INSERT INTO rules (id, content, source_episode_id) VALUES ($1, $2, $3)",
            live_rule_id,
            "live-source rule",
            live_episode_id,
        )
        await pool.execute(
            "INSERT INTO rules (id, content, source_episode_id) VALUES ($1, $2, $3)",
            unresolved_rule_id,
            "unresolved-source rule",
            unresolved_episode_id,
        )
        await pool.execute(
            "INSERT INTO memory_links (source_type, source_id, target_type, target_id, relation) "
            "VALUES ('fact', $1, 'episode', $2, 'derived_from')",
            fact_id,
            episode_id,
        )
        await pool.execute(
            "INSERT INTO memory_links (source_type, source_id, target_type, target_id, relation) "
            "VALUES ('episode', $1, 'rule', $2, 'supports')",
            live_episode_id,
            live_rule_id,
        )
        await pool.execute(
            "INSERT INTO memory_links (source_type, source_id, target_type, target_id, relation) "
            "VALUES ('episode', $1, 'fact', $2, 'supports')",
            unresolved_episode_id,
            unresolved_fact_id,
        )

        await pool.execute("DELETE FROM episodes WHERE id = $1", episode_id)

        class _MemoryDb:
            butler_names = ["general"]

            def pool(self, name: str):
                assert name == "general"
                return pool

        api_db = _MemoryDb()
        api_facts = await list_facts(
            q=None,
            scope=None,
            validity=None,
            permanence=None,
            subject=None,
            source_episode_id=None,
            importance_min=None,
            offset=0,
            limit=50,
            db=api_db,  # type: ignore[arg-type]
        )
        api_rules = await list_rules(
            q=None,
            scope=None,
            maturity=None,
            forgotten=None,
            offset=0,
            limit=50,
            db=api_db,  # type: ignore[arg-type]
        )
        live_source_links = await get_links(pool, "rule", live_rule_id)
        unresolved_source_links = await get_links(pool, "fact", unresolved_fact_id)

        fact_source = await pool.fetchval(
            "SELECT source_episode_id FROM facts WHERE id = $1", fact_id
        )
        rule_source = await pool.fetchval(
            "SELECT source_episode_id FROM rules WHERE id = $1", rule_id
        )
        tombstone = await pool.fetchrow(
            "SELECT episode_id, deleted_at FROM episode_tombstones WHERE episode_id = $1",
            episode_id,
        )
        links = await get_links(pool, "fact", fact_id)
        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: api_db
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            expired_link_response = await client.get(f"/api/memory/links/fact/{fact_id}")
            live_link_response = await client.get(f"/api/memory/links/rule/{live_rule_id}")
            unresolved_link_response = await client.get(
                f"/api/memory/links/fact/{unresolved_fact_id}"
            )
        tombstone_columns = {
            row["column_name"]
            for row in await pool.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'episode_tombstones'"
            )
        }
        return {
            "episode_id": episode_id,
            "fact_source": fact_source,
            "rule_source": rule_source,
            "tombstone": dict(tombstone) if tombstone else None,
            "tombstone_columns": tombstone_columns,
            "links": links,
            "fact_api_statuses": {
                fact.content: fact.source_episode_status for fact in api_facts.data
            },
            "rule_api_statuses": {
                rule.content: rule.source_episode_status for rule in api_rules.data
            },
            "live_source_links": live_source_links,
            "unresolved_source_links": unresolved_source_links,
            "expired_link_response": expired_link_response,
            "live_link_response": live_link_response,
            "unresolved_link_response": unresolved_link_response,
        }
    finally:
        await pool.close()


def test_deleting_episode_preserves_content_free_durable_provenance(
    memory_migrated_db: str,
) -> None:
    """Deletion keeps source ids and makes generic links truthfully expired."""
    result = asyncio.run(_delete_sourced_episode(memory_migrated_db))

    assert result["fact_source"] == result["episode_id"]
    assert result["rule_source"] == result["episode_id"]
    assert result["tombstone"] is not None
    assert result["tombstone"]["episode_id"] == result["episode_id"]
    assert result["tombstone"]["deleted_at"] is not None
    assert result["tombstone_columns"] == {"episode_id", "deleted_at"}
    assert result["fact_api_statuses"] == {
        "durable derived fact": "expired",
        "live-source fact": "available",
        "unresolved-source fact": "unresolved",
    }
    assert result["rule_api_statuses"] == {
        "durable derived rule": "expired",
        "live-source rule": "available",
        "unresolved-source rule": "unresolved",
    }

    links = result["links"]
    assert len(links) == 1
    assert links[0]["source_type"] == "fact"
    assert links[0]["target_type"] == "episode"
    assert links[0]["target_id"] == result["episode_id"]
    assert links[0]["relation"] == "derived_from"
    assert links[0]["source_episode_status"] is None
    assert links[0]["target_episode_status"] == "expired"

    live_source_links = result["live_source_links"]
    assert len(live_source_links) == 1
    assert live_source_links[0]["source_episode_status"] == "available"
    assert live_source_links[0]["target_episode_status"] is None

    unresolved_source_links = result["unresolved_source_links"]
    assert len(unresolved_source_links) == 1
    assert unresolved_source_links[0]["source_episode_status"] == "unresolved"
    assert unresolved_source_links[0]["target_episode_status"] is None

    # The HTTP reader is the production contract: endpoint status remains visible
    # after raw episode content has been deleted, with no tombstone details exposed.
    for response, status_field, expected_status in (
        (result["expired_link_response"], "target_episode_status", "expired"),
        (result["live_link_response"], "source_episode_status", "available"),
        (result["unresolved_link_response"], "source_episode_status", "unresolved"),
    ):
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["data"]) == 1
        assert payload["data"][0][status_field] == expected_status
        assert set(payload["data"][0]) == {
            "source_type",
            "source_id",
            "target_type",
            "target_id",
            "relation",
            "created_at",
            "source_episode_status",
            "target_episode_status",
        }


def test_episode_delete_fails_without_its_tombstone_write(memory_migrated_db: str) -> None:
    """The raw episode remains if the atomic provenance write cannot complete."""

    async def _assert_atomic_failure() -> None:
        pool = await asyncpg.create_pool(
            memory_migrated_db,
            min_size=1,
            max_size=1,
            init=register_jsonb_codec,
        )
        episode_id = uuid.uuid4()
        try:
            await pool.execute(
                "INSERT INTO episodes (id, butler, content) VALUES ($1, 'general', $2)",
                episode_id,
                "raw content must remain when provenance persistence fails",
            )
            await pool.execute(
                "ALTER TABLE episode_tombstones RENAME TO episode_tombstones_blocked"
            )
            with pytest.raises(asyncpg.UndefinedTableError):
                await pool.execute("DELETE FROM episodes WHERE id = $1", episode_id)
            assert await pool.fetchval(
                "SELECT content FROM episodes WHERE id = $1", episode_id
            ) == ("raw content must remain when provenance persistence fails")
        finally:
            await pool.execute(
                "ALTER TABLE IF EXISTS episode_tombstones_blocked RENAME TO episode_tombstones"
            )
            await pool.close()

    asyncio.run(_assert_atomic_failure())
