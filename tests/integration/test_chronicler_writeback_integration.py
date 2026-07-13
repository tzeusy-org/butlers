"""Real-Postgres integration tests for the chronicler write-back DB path
(bu-93y4rt, tasks.md §8).

Mocked collaborators are fine for the orchestrator contract
(``roster/chronicler/tests/test_writeback.py``), but the DB-touching code —
``list_daily_rollups`` / ``list_daily_rollup_flags`` reads and the
``fetch_companion_copresence`` raw SQL over ``v_episodes_corrected`` +
``episode_entities`` — must be proven against the real migrated chronicler
schema (mocked-pool green ≠ integration green). These tests seed real rollup /
flag / social-episode rows and run ``run_day_close_writeback`` end to end with
fake (recording) ``store_fact`` / proposer collaborators, so no memory-module
tables are required.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from uuid import uuid4

import asyncpg
import pytest

from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
from butlers.chronicler.storage import (
    upsert_daily_rollup,
    upsert_daily_rollup_flag,
    upsert_episode,
)
from butlers.chronicler.writeback import (
    PREDICATE_RECURRING_COMPANION,
    SOURCE_BUTLER,
    fetch_companion_copresence,
    run_day_close_writeback,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_DAY = date(2026, 7, 9)


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=5, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE episode_entities, episodes CASCADE")
    await p.execute("TRUNCATE TABLE daily_rollups, daily_rollup_flags CASCADE")
    await p.execute("TRUNCATE TABLE source_adapter_state, projection_checkpoints CASCADE")
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


class _StoreSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return "fact-id"


class _ProposeSpy:
    def __init__(self) -> None:
        self.proposals: list = []

    async def __call__(self, proposal):
        self.proposals.append(proposal)


async def _seed_social_episode(pool, *, source_ref, start_at, end_at, entity_id) -> None:
    episode = await upsert_episode(
        pool,
        Episode(
            source_name="comms.message_bursts",
            source_ref=source_ref,
            episode_type="social_episode",
            start_at=start_at,
            end_at=end_at,
            precision=Precision.EXACT,
            title="Messages",
            payload={"channel": "telegram_bot"},
            privacy=Privacy.NORMAL,
            layer=Layer.ACTIVITY,
        ),
    )
    await pool.execute(
        "INSERT INTO episode_entities (episode_id, entity_id, role) VALUES ($1, $2, 'owner')",
        episode.id,
        uuid4(),
    )
    await pool.execute(
        "INSERT INTO episode_entities (episode_id, entity_id, role) VALUES ($1, $2, 'participant')",
        episode.id,
        entity_id,
    )


async def test_fetch_companion_copresence_counts_distinct_days(pool):
    companion = uuid4()
    # Same companion on three distinct UTC days within the window.
    for day in (1, 3, 5):
        await _seed_social_episode(
            pool,
            source_ref=f"c-{day}",
            start_at=datetime(2026, 7, day, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, day, 11, 0, tzinfo=UTC),
            entity_id=companion,
        )
    rows = await fetch_companion_copresence(
        pool, start_date=date(2026, 6, 12), end_date=_DAY, timezone="UTC"
    )
    assert len(rows) == 1
    assert rows[0].entity_id == str(companion)
    assert rows[0].distinct_days == 3
    assert rows[0].episode_count == 3


async def test_run_day_close_writeback_end_to_end(pool):
    # Baseline: 10 days of an 8h work usual → an anomaly bar the closed day can clear.
    for d in range(29, 31):  # 2026-06-29, 06-30
        await upsert_daily_rollup(
            pool,
            local_date=date(2026, 6, d),
            lane="work",
            seconds=8 * 3600,
            episode_count=1,
            timezone="UTC",
        )
    for d in range(1, 9):  # 2026-07-01 .. 07-08
        await upsert_daily_rollup(
            pool,
            local_date=date(2026, 7, d),
            lane="work",
            seconds=8 * 3600,
            episode_count=1,
            timezone="UTC",
        )
    # Closed day 07-09: sleep present, work absent (→ 0s work vs 8h usual = skew).
    await upsert_daily_rollup(
        pool, local_date=_DAY, lane="sleep", seconds=7 * 3600, episode_count=1, timezone="UTC"
    )
    # A pending-backfill flag → one self-reminder.
    await upsert_daily_rollup_flag(
        pool, local_date=_DAY, flag_type="feeder_dark", severity="warning", detail={"src": ["x"]}
    )
    # A recurring companion across three distinct days → one MCP proposal.
    companion = uuid4()
    for day in (1, 3, 5):
        await _seed_social_episode(
            pool,
            source_ref=f"wb-{day}",
            start_at=datetime(2026, 7, day, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, day, 11, 0, tzinfo=UTC),
            entity_id=companion,
        )

    store = _StoreSpy()
    propose = _ProposeSpy()
    result = await run_day_close_writeback(
        pool,
        day_date=_DAY,
        timezone="UTC",
        store_fact_fn=store,
        propose_enrichment_fn=propose,
    )

    # Own-schema insight + self-reminder were written; every write is chronicler-owned.
    assert result.insights_written >= 1
    assert result.self_reminders_written == 1
    assert store.calls
    assert all(c["metadata"]["source"] == SOURCE_BUTLER for c in store.calls)
    # The recurring companion left ONLY as an MCP proposal, never a stored fact.
    assert result.proposals_sent == 1
    assert propose.proposals[0].entity_id == str(companion)
    assert PREDICATE_RECURRING_COMPANION not in [c["predicate"] for c in store.calls]
