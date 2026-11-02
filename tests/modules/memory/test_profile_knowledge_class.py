"""Profile Facts exclude event/transaction predicates (bu-bagfg).

An owner-authored receipt is owner-class, so content authority alone does not
keep it out of Profile Facts. The knowledge class is derived deterministically
from the predicate; event-class facts stay recallable but never fill the
Profile Facts pack.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.memory import knowledge_class
from butlers.modules.memory.tools import context as _context
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None

pytestmark = pytest.mark.unit


class _RecordingPool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *params):
        self.calls.append((sql, params))
        return []

    async def fetchval(self, sql: str, *params):
        self.calls.append((sql, params))
        return 0


class TestKnowledgeClassDerivation:
    @pytest.mark.parametrize(
        "predicate",
        ["transaction_debit", "transaction_credit", "took_dose", "meal_lunch", "gift"],
    )
    def test_event_predicates_are_event_class(self, predicate: str) -> None:
        assert knowledge_class.knowledge_class(predicate) == "event"

    @pytest.mark.parametrize(
        "predicate", ["birthday", "preferences:general_timezone", "lives_in", "made-up-thing"]
    )
    def test_identity_preference_and_unknown_predicates_stay_durable(self, predicate: str) -> None:
        assert knowledge_class.knowledge_class(predicate) == "durable"

    def test_derivation_normalizes_like_the_write_path(self) -> None:
        assert knowledge_class.knowledge_class("Transaction-Debit") == "event"


class TestProfileFactsQueryExcludesEvents:
    @pytest.mark.parametrize("with_ceiling", [True, False])
    async def test_every_profile_query_binds_the_event_exclusion(self, with_ceiling: bool) -> None:
        pool = _RecordingPool()
        await _context._fetch_profile_facts(
            pool,
            "owner",
            allowed_sensitivities=("normal",) if with_ceiling else None,
        )
        assert pool.calls
        for sql, params in pool.calls:
            assert "f.predicate <> ALL(" in sql
            assert sorted(knowledge_class.EVENT_PREDICATES) in [
                sorted(p) for p in params if isinstance(p, list)
            ]


@pytest.fixture
def _migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "memory"]
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
async def test_owner_receipt_absent_from_profile_but_durable_fact_stays(
    _migrated_db_url: str,
) -> None:
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
        for predicate, content in (
            ("transaction_debit", "paid 12.50 at cafe"),
            ("birthday", "born in June"),
        ):
            await pool.execute(
                """
                INSERT INTO facts (subject, predicate, content, scope, entity_id,
                                   search_vector, last_confirmed_at, content_authority)
                VALUES ('owner', $1, $2, 'global', $3, to_tsvector('english', $2),
                        now(), 'owner')
                """,
                predicate,
                content,
                owner_id,
            )
        facts, _ = await _context._fetch_profile_facts(pool, "owner")
        assert [f["predicate"] for f in facts] == ["birthday"]
        still_stored = await pool.fetchval(
            "SELECT count(*) FROM facts WHERE predicate = 'transaction_debit'"
        )
        assert still_stored == 1
    finally:
        await pool.close()
