"""Profile Facts exclude event-class (temporal-predicate) facts (bu-bagfg).

An owner-authored receipt is owner-class, so content authority alone does not
keep it out of Profile Facts. The class derives from ``predicate_registry.
is_temporal`` (seeded, and auto-set from ``valid_at`` at storage time), so there
is no second list to drift. Event-class facts stay recallable.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.memory import search as _search
from butlers.modules.memory.tools import context as _context
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.modules.memory.test_local_read_ceiling import (
    _FILTER_SCOPE,
    _filter_engine,
    _insert_ranked_fact,
)

docker_available = shutil.which("docker") is not None

pytestmark = pytest.mark.unit


class _RecordingPool:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def fetch(self, sql: str, *params):
        self.sql.append(sql)
        return []

    async def fetchval(self, sql: str, *params):
        self.sql.append(sql)
        return 0


@pytest.mark.parametrize("with_ceiling", [True, False])
async def test_every_profile_query_is_gated_on_the_registry_temporal_flag(
    with_ceiling: bool,
) -> None:
    pool = _RecordingPool()
    await _context._fetch_profile_facts(
        pool, "owner", allowed_sensitivities=("normal",) if with_ceiling else None
    )
    assert len(pool.sql) == (2 if with_ceiling else 1)
    for sql in pool.sql:
        assert "predicate_registry" in sql and "pr.is_temporal" in sql


@pytest.fixture
def _migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "memory"]
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
async def test_event_facts_leave_profile_but_stay_recallable(_migrated_db_url: str) -> None:
    pool = await asyncpg.create_pool(
        _migrated_db_url, min_size=1, max_size=2, init=register_jsonb_codec
    )
    try:
        await pool.execute(
            "TRUNCATE TABLE facts, rules, episodes, public.entities RESTART IDENTITY CASCADE"
        )
        owner_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, roles) VALUES ('Owner', ARRAY['owner'])"
            " RETURNING id"
        )
        # Guard: a predicate on no hand list becomes event-class purely by being
        # registered temporal, exactly as storage.py does for new valid_at predicates.
        await pool.execute(
            "INSERT INTO predicate_registry (name, is_temporal) VALUES ('zz_new_event', true)"
        )
        durable = {
            "birthday": "work born in june",
            "zz_unregistered": "work unregistered predicate stays durable",
        }
        events = {
            "transaction_debit": "work paid at cafe",
            "sleep_session": "work slept seven hours",
            "zz_new_event": "work newly registered event",
        }
        ids = {}
        for predicate, content in {**durable, **events}.items():
            ids[predicate] = await _insert_ranked_fact(
                pool, content=content, near=True, predicate=predicate, entity_id=owner_id
            )

        profile, _ = await _context._fetch_profile_facts(pool, "owner")
        assert {f["predicate"] for f in profile} == set(durable)

        recalled = await _search.recall(
            pool,
            "work",
            _filter_engine(),
            scope=_FILTER_SCOPE,
            limit=20,
            read_policy=_search.resolve_catalog_read_policy("normal"),
        )
        recalled_ids = {row["id"] for row in recalled}
        assert {ids[p] for p in events} <= recalled_ids
    finally:
        await pool.close()
