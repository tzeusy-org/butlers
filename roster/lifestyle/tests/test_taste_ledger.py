"""Integration tests for the taste ledger resolver (bu-2jtfw.10).

Real Postgres via testcontainers: migrates core + memory + lifestyle chains,
seeds real ``connectors.spotify_listening_sessions`` / ``spotify_track_plays``
rows, and asserts the resolver's projection counts and idempotence.

Issue: bu-2jtfw.10
"""

from __future__ import annotations

import asyncio
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from butlers.testing.migration import create_migrated_test_pool

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture
async def lifestyle_pool(postgres_container):
    pool = await create_migrated_test_pool(
        postgres_container,
        chains=["core", "memory", "lifestyle"],
        schemas={"memory": "lifestyle", "lifestyle": "lifestyle"},
        pool_schema="lifestyle",
    )
    try:
        yield pool
    finally:
        await pool.close()


async def _insert_session(
    pool,
    *,
    idempotency_key: str,
    endpoint_identity: str = "spotify_user_client:spotify:user1",
    spotify_user_id: str = "user1",
    started_at: datetime,
    track_names: list[str],
) -> None:
    await pool.execute(
        """
        INSERT INTO connectors.spotify_listening_sessions (
            idempotency_key, endpoint_identity, spotify_user_id,
            started_at, ended_at, duration_seconds, track_count, track_names
        ) VALUES ($1, $2, $3, $4, $4, 0, $5, $6)
        """,
        idempotency_key,
        endpoint_identity,
        spotify_user_id,
        started_at,
        len(track_names),
        track_names,
    )


async def _insert_closed_play(
    pool,
    *,
    endpoint_identity: str = "spotify_user_client:spotify:user1",
    spotify_user_id: str = "user1",
    track_uri: str,
    track_name: str,
    first_seen_ms: int,
    duration_ms: int | None,
    completion_ratio: float | None,
    skipped: bool | None,
    observation_precision: str = "progress_tracked",
) -> None:
    await pool.execute(
        """
        INSERT INTO connectors.spotify_track_plays (
            endpoint_identity, spotify_user_id, track_uri, track_name,
            first_seen_ms, last_seen_ms, duration_ms, max_progress_ms,
            completion_ratio, skipped, observation_precision, closed_at
        ) VALUES ($1, $2, $3, $4, $5, $5, $6, $6, $7, $8, $9, now())
        """,
        endpoint_identity,
        spotify_user_id,
        track_uri,
        track_name,
        first_seen_ms,
        duration_ms,
        completion_ratio,
        skipped,
        observation_precision,
    )


class TestBackfillFromListeningSessions:
    async def test_three_sessions_yield_exact_counts_and_second_pass_is_noop(
        self, lifestyle_pool
    ) -> None:
        from butlers.tools.lifestyle.taste_ledger import backfill_from_listening_sessions

        now = datetime(2026, 9, 1, tzinfo=UTC)
        await _insert_session(
            lifestyle_pool,
            idempotency_key="spotify:ep1:session:1",
            started_at=now,
            track_names=["Song A", "Song B"],
        )
        await _insert_session(
            lifestyle_pool,
            idempotency_key="spotify:ep1:session:2",
            started_at=now,
            track_names=["Song C"],
        )
        await _insert_session(
            lifestyle_pool,
            idempotency_key="spotify:ep1:session:3",
            started_at=now,
            track_names=[],
        )

        result = await backfill_from_listening_sessions(lifestyle_pool)
        assert result.works_created == 3
        assert result.signals_created == 3

        works_count = await lifestyle_pool.fetchval("SELECT count(*) FROM works")
        signals_count = await lifestyle_pool.fetchval("SELECT count(*) FROM taste_signals")
        assert works_count == 3
        assert signals_count == 3

        second_pass = await backfill_from_listening_sessions(lifestyle_pool)
        assert second_pass.works_created == 0
        assert second_pass.signals_created == 0
        assert await lifestyle_pool.fetchval("SELECT count(*) FROM works") == 3
        assert await lifestyle_pool.fetchval("SELECT count(*) FROM taste_signals") == 3

    async def test_repeated_track_name_across_sessions_is_not_deduped(self, lifestyle_pool) -> None:
        """Unresolved evidence (no stable id) never collapses same-name mentions."""
        from butlers.tools.lifestyle.taste_ledger import backfill_from_listening_sessions

        now = datetime(2026, 9, 1, tzinfo=UTC)
        await _insert_session(
            lifestyle_pool,
            idempotency_key="spotify:ep1:session:10",
            started_at=now,
            track_names=["Same Song"],
        )
        await _insert_session(
            lifestyle_pool,
            idempotency_key="spotify:ep1:session:11",
            started_at=now,
            track_names=["Same Song"],
        )

        result = await backfill_from_listening_sessions(lifestyle_pool)
        assert result.works_created == 2
        assert result.signals_created == 2

    async def test_concurrent_unresolved_session_projection_leaves_no_orphan_work(
        self, lifestyle_pool
    ) -> None:
        from butlers.tools.lifestyle.taste_ledger import backfill_from_listening_sessions

        await _insert_session(
            lifestyle_pool,
            idempotency_key="spotify:ep1:session:concurrent",
            started_at=datetime(2026, 9, 1, tzinfo=UTC),
            track_names=["Unresolved Song"],
        )

        results = await asyncio.gather(
            backfill_from_listening_sessions(lifestyle_pool),
            backfill_from_listening_sessions(lifestyle_pool),
        )

        assert sum(result.works_created for result in results) == 1
        assert sum(result.signals_created for result in results) == 1
        assert await lifestyle_pool.fetchval("SELECT count(*) FROM works") == 1
        assert await lifestyle_pool.fetchval("SELECT count(*) FROM taste_signals") == 1

    async def test_scheduled_projector_materializes_connector_evidence(
        self, lifestyle_pool
    ) -> None:
        from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

        await _insert_session(
            lifestyle_pool,
            idempotency_key="spotify:ep1:session:scheduled",
            started_at=datetime(2026, 9, 1, tzinfo=UTC),
            track_names=["Scheduled Song"],
        )

        handler = get_deterministic_schedule_job_registry()["lifestyle"]["taste_ledger_project"]
        result = await handler(lifestyle_pool, None)

        assert result["sessions"] == {"works_created": 1, "signals_created": 1}
        assert await lifestyle_pool.fetchval("SELECT count(*) FROM works") == 1
        assert await lifestyle_pool.fetchval("SELECT count(*) FROM taste_signals") == 1


class TestBackfillFromTrackPlays:
    async def test_concurrent_passes_yield_one_signal_per_source_ref_kind(
        self, lifestyle_pool
    ) -> None:
        from butlers.tools.lifestyle.taste_ledger import backfill_from_track_plays

        await _insert_closed_play(
            lifestyle_pool,
            track_uri="spotify:track:aaa",
            track_name="Finished Track",
            first_seen_ms=1000,
            duration_ms=200_000,
            completion_ratio=0.95,
            skipped=False,
        )

        results = await asyncio.gather(
            backfill_from_track_plays(lifestyle_pool),
            backfill_from_track_plays(lifestyle_pool),
        )
        total_signals_created = sum(r.signals_created for r in results)
        assert total_signals_created == 1

        signal_count = await lifestyle_pool.fetchval(
            "SELECT count(*) FROM taste_signals WHERE source_table = 'spotify_track_plays'"
        )
        assert signal_count == 1
        work_count = await lifestyle_pool.fetchval(
            "SELECT count(*) FROM works WHERE external_ids ->> 'primary' = 'spotify:track:aaa'"
        )
        assert work_count == 1

    async def test_resolved_track_uri_dedupes_across_multiple_plays(self, lifestyle_pool) -> None:
        from butlers.tools.lifestyle.taste_ledger import backfill_from_track_plays

        await _insert_closed_play(
            lifestyle_pool,
            track_uri="spotify:track:bbb",
            track_name="Replayed Track",
            first_seen_ms=1000,
            duration_ms=200_000,
            completion_ratio=1.0,
            skipped=False,
        )
        await _insert_closed_play(
            lifestyle_pool,
            track_uri="spotify:track:bbb",
            track_name="Replayed Track",
            first_seen_ms=500_000,
            duration_ms=200_000,
            completion_ratio=0.02,
            skipped=True,
        )

        result = await backfill_from_track_plays(lifestyle_pool)
        assert result.works_created == 1
        assert result.signals_created == 2

        work_count = await lifestyle_pool.fetchval(
            "SELECT count(*) FROM works WHERE external_ids ->> 'primary' = 'spotify:track:bbb'"
        )
        assert work_count == 1

    async def test_play_only_precision_yields_play_only_signal_kind(self, lifestyle_pool) -> None:
        from butlers.tools.lifestyle.taste_ledger import backfill_from_track_plays

        await _insert_closed_play(
            lifestyle_pool,
            track_uri="spotify:track:ccc",
            track_name="Gap-filled Track",
            first_seen_ms=2000,
            duration_ms=None,
            completion_ratio=None,
            skipped=None,
            observation_precision="play_only",
        )

        await backfill_from_track_plays(lifestyle_pool)
        signal_kind = await lifestyle_pool.fetchval(
            "SELECT signal_kind FROM taste_signals WHERE source_ref LIKE '%ccc%'"
        )
        assert signal_kind == "play_only"


class TestPredicateRegistrySeeding:
    async def test_every_memory_taxonomy_predicate_is_registered(self, lifestyle_pool) -> None:
        taxonomy = (
            Path(__file__).resolve().parents[1]
            / ".agents"
            / "skills"
            / "memory-taxonomy"
            / "SKILL.md"
        ).read_text()
        predicate_section = taxonomy.split("**Permanence levels**", maxsplit=1)[0]
        expected = set(
            re.findall(
                r"^- `(?:[^`]+ \| )?([a-z][a-z0-9_]*)`:",
                predicate_section,
                re.MULTILINE,
            )
        )
        assert expected
        rows = await lifestyle_pool.fetch(
            "SELECT name FROM predicate_registry WHERE name = ANY($1::text[])",
            list(expected),
        )
        found = {r["name"] for r in rows}
        assert found == expected

    async def test_lifestyle_scope_rejects_unregistered_predicate(self, lifestyle_pool) -> None:
        from butlers.modules.memory.storage import store_fact

        embedding_engine = MagicMock()
        embedding_engine.embed.return_value = [0.0] * 384
        embedding_engine.model_name = "test-model"

        with pytest.raises(ValueError, match="Lifestyle predicate.*is not registered"):
            await store_fact(
                lifestyle_pool,
                subject="user",
                predicate="likes_geners",
                content="modal jazz",
                embedding_engine=embedding_engine,
                scope="lifestyle",
            )

        assert (
            await lifestyle_pool.fetchval(
                "SELECT count(*) FROM facts WHERE predicate = 'likes_geners'"
            )
            == 0
        )
