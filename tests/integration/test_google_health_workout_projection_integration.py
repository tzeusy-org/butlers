"""Real migrated workout ingest/projection replay contract (bu-q7vx1q.39).

The connector normalizer/envelope, account and owner lookup, memory writer,
Health facts, adapter, episode upsert, owner link and checkpoint are real. Only
embedding computation uses the existing offline helper; no provider is called.

REQ-chronicler-source-compatibility-006: one workout fact projects one episode.
This isolated successful replay does not attest crash atomicity of the current
episode/link writes, shared sleep/workout checkpoint ordering, concurrent first
fact admission, or provider edit/delete reconciliation. Account fixtures follow
the adopted companion-entity registry shape, with independent owner attribution.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio

from butlers.chronicler.adapters.google_health import SOURCE_NAME, GoogleHealthWorkoutAdapter
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.storage import get_checkpoint, get_source_state
from butlers.connectors.google_health import (
    GOOGLE_HEALTH_SCOPES,
    _normalize_workout_record,
    build_workout_session_envelope,
)
from butlers.db import register_jsonb_codec, schema_search_path
from butlers.google_account_registry import list_health_scoped_accounts
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.health import wellness_ingest
from tests.modules.memory._test_helpers import make_embedding_engine_mock

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_EMAIL = "owner@example.invalid"
_ENDPOINT = f"google_health:user:{_EMAIL}"
_START = datetime(2026, 4, 24, 7, tzinfo=UTC)
_END = datetime(2026, 4, 24, 7, 45, tzinfo=UTC)
_RAW_CANARY = "synthetic-private-provider-field"


@dataclass(frozen=True)
class _ProjectionPools:
    health: asyncpg.Pool
    chronicler: asyncpg.Pool
    owner_id: UUID
    account_entity_id: UUID


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def migrated_db_url(postgres_container) -> str:
    # chronicler_003 restricts Health grants; mem_011 must apply afterwards.
    return await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core", "chronicler", "memory", "health"],
        {"core": "health", "chronicler": "chronicler", "memory": "health", "health": "health"},
    )


async def _runtime_pool(db_url: str, schema: str, role: str) -> asyncpg.Pool:
    async def setup(conn: asyncpg.Connection) -> None:
        # Pool release resets SET ROLE, so restore it on every acquisition.
        await conn.execute(f'SET ROLE "{role}"')

    search_path = schema_search_path(schema)
    assert search_path is not None
    return await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        setup=setup,
        server_settings={"search_path": search_path},
    )


@pytest_asyncio.fixture(loop_scope="session")
async def projection_pools(migrated_db_url: str, monkeypatch) -> AsyncIterator[_ProjectionPools]:
    owner_id, account_entity_id = uuid4(), uuid4()
    # Fixture control is the ordinary migration login in this disposable DB.
    # No SQL schema or privilege is invented to make the runtime path pass.
    control = await asyncpg.connect(migrated_db_url)
    try:
        await register_jsonb_codec(control)
        # This transaction is fixture seeding only, never the executing chain.
        async with control.transaction():
            await control.execute(
                """
                TRUNCATE health.facts, chronicler.episodes, chronicler.point_events,
                         chronicler.projection_checkpoints, chronicler.source_adapter_state CASCADE
                """
            )
            await control.execute(
                """
                INSERT INTO public.entities (id, canonical_name, entity_type, roles)
                VALUES ($1, 'Workout fixture owner', 'person', ARRAY['owner']::text[]),
                       ($2, 'google-account:owner@example.invalid', 'other',
                        ARRAY['google_account']::text[])
                """,
                owner_id,
                account_entity_id,
            )
            await control.execute(
                """
                INSERT INTO public.google_accounts (entity_id, email, granted_scopes, status)
                VALUES ($1, $2, $3::text[], 'active')
                """,
                account_entity_id,
                _EMAIL,
                sorted(GOOGLE_HEALTH_SCOPES),
            )
    finally:
        await control.close()

    monkeypatch.setattr(wellness_ingest, "_recognised_owner_identities", None)
    health = chronicler = None
    try:
        health = await _runtime_pool(migrated_db_url, "health", "butler_health_rw")
        chronicler = await _runtime_pool(migrated_db_url, "chronicler", "butler_chronicler_rw")
        yield _ProjectionPools(health, chronicler, owner_id, account_entity_id)
    finally:
        if chronicler is not None:
            await chronicler.close()
        if health is not None:
            await health.close()
        # Pools are closed before the ordinary fixture login removes its seeds.
        control = await asyncpg.connect(migrated_db_url)
        try:
            await control.execute(
                """
                TRUNCATE health.facts, chronicler.episodes, chronicler.point_events,
                         chronicler.projection_checkpoints, chronicler.source_adapter_state CASCADE
                """
            )
            await control.execute("DELETE FROM public.google_accounts WHERE email = $1", _EMAIL)
            await control.execute(
                "DELETE FROM public.entities WHERE id = ANY($1::uuid[])",
                [owner_id, account_entity_id],
            )
        finally:
            await control.close()


def _envelope(
    session_id: str,
    *,
    detection: str,
    heart_rate: bool,
    email: str = _EMAIL,
    zero_duration: bool = False,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {"caloriesKcal": 345, "distanceMillimeters": 7_500_000}
    if heart_rate:
        metrics["averageHeartRateBeatsPerMinute"] = "152"
    raw = _normalize_workout_record(
        {
            "name": f"users/u/dataTypes/exercise/dataPoints/{session_id}",
            "exercise": {
                "interval": {
                    "startTime": "2026-04-24T07:00:00Z",
                    "endTime": "2026-04-24T07:00:00Z" if zero_duration else "2026-04-24T07:45:00Z",
                },
                "exerciseType": "RUNNING",
                "metricsSummary": metrics,
                "dataSource": {"recordingMethod": "MANUAL" if detection == "manual" else "AUTO"},
                "private_raw_canary": _RAW_CANARY,
            },
        }
    )
    return build_workout_session_envelope(
        endpoint_identity=f"google_health:user:{email}",
        google_user_id=email,
        session_id=raw["session_id"],
        session_record=raw,
        observed_at="2026-04-24T07:46:00Z",
    )


async def _facts(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    # Each assertion observes committed rows on a separate pool acquisition.
    return await pool.fetch(
        """
        SELECT id, entity_id, subject, scope, tenant_id, valid_at, created_at,
               idempotency_key, permanence, retention_class, sensitivity,
               content_authority, metadata
        FROM health.facts WHERE predicate = 'workout_session' ORDER BY created_at, id
        """
    )


async def _episodes(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch(
        """
        SELECT id, source_ref, start_at, end_at, precision, layer, confidence, privacy, payload
        FROM chronicler.episodes
        WHERE source_name = $1 AND episode_type = 'workout_episode' ORDER BY source_ref
        """,
        SOURCE_NAME,
    )


async def _owner_links(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT episode_id, entity_id, role FROM chronicler.episode_entities ORDER BY episode_id"
    )


@pytest.mark.parametrize(
    ("detection", "heart_rate", "confidence", "privacy"),
    [
        pytest.param("manual", False, "medium", "normal", id="manual-normal"),
        pytest.param("manual", True, "high", "sensitive", id="manual-sensitive"),
        pytest.param("auto", False, "low", "normal", id="auto-normal"),
        pytest.param("auto", True, "medium", "sensitive", id="auto-sensitive"),
    ],
)
async def test_connector_workout_ingest_projects_once_then_checkpointed_replay_projects_none(
    projection_pools: _ProjectionPools,
    detection: str,
    heart_rate: bool,
    confidence: str,
    privacy: str,
) -> None:
    health, chronicler = projection_pools.health, projection_pools.chronicler
    assert await health.fetchval("SELECT current_user") == "butler_health_rw"
    assert await chronicler.fetchval("SELECT current_user") == "butler_chronicler_rw"
    assert await chronicler.fetchval("SELECT count(*) FROM health.facts") == 0
    accounts = await list_health_scoped_accounts(health)
    assert [(a.email, a.entity_id, a.refresh_token_present) for a in accounts] == [
        (_EMAIL, projection_pools.account_entity_id, False)
    ]
    assert projection_pools.account_entity_id != projection_pools.owner_id
    embedding = make_embedding_engine_mock()

    foreign = await wellness_ingest.translate_wellness_envelope(
        health,
        embedding,
        _envelope(
            "foreign", detection=detection, heart_rate=heart_rate, email="foreign@example.invalid"
        ),
    )
    assert foreign["status"] == "rejected_non_owner_sender"
    malformed = await wellness_ingest.translate_wellness_envelope(
        health,
        embedding,
        _envelope("zero", detection=detection, heart_rate=heart_rate, zero_duration=True),
    )
    assert malformed["status"] == "skipped_malformed_payload"
    assert await _facts(health) == []
    assert await _episodes(chronicler) == []

    envelope = _envelope("run-9", detection=detection, heart_rate=heart_rate)
    assert envelope["payload"]["raw"]["private_raw_canary"] == _RAW_CANARY
    key = f"google_health:{_EMAIL}:workout:run-9"
    assert envelope["event"]["external_event_id"] == f"google_health:{_EMAIL}:workout_session:run-9"
    assert envelope["control"]["idempotency_key"] == key
    ingested = await wellness_ingest.translate_wellness_envelope(health, embedding, envelope)
    assert ingested["status"] == "ok"
    facts = await _facts(health)
    assert len(facts) == 1
    fact = facts[0]
    assert UUID(str(ingested["fact_id"])) == fact["id"]
    assert (fact["entity_id"], fact["subject"], fact["scope"], fact["tenant_id"]) == (
        projection_pools.owner_id,
        "owner",
        "health",
        "shared",
    )
    assert (
        fact["permanence"],
        fact["retention_class"],
        fact["sensitivity"],
        fact["content_authority"],
    ) == ("standard", "operational", "normal", "system")
    assert fact["valid_at"] == _START
    assert fact["idempotency_key"] == key
    metadata = {
        "activity_type": "running",
        "duration_ms": 2_700_000,
        "end_time": "2026-04-24T07:45:00Z",
        "session_id": "run-9",
        "detection": detection,
        "calories": 345,
        "distance_m": 7500.0,
        "source": "google_health",
        "source_endpoint_identity": _ENDPOINT,
    }
    if heart_rate:
        metadata["average_heart_rate"] = 152
    assert isinstance(fact["metadata"], dict)
    assert fact["metadata"] == metadata

    await seed_source_registry(chronicler, sources=INITIAL_SOURCES)
    first = await GoogleHealthWorkoutAdapter().run(pool=chronicler, chronicler_pool=chronicler)
    assert first.success and not first.skipped, first
    assert first.rows_projected == 1
    episodes = await _episodes(chronicler)
    assert len(episodes) == 1
    episode = episodes[0]
    assert episode["source_ref"] == f"health.facts:workout_session:{key}"
    assert (episode["start_at"], episode["end_at"], episode["precision"], episode["layer"]) == (
        _START,
        _END,
        "minute",
        "activity",
    )
    assert (episode["confidence"], episode["privacy"]) == (confidence, privacy)
    payload = {
        "fact_id": str(fact["id"]),
        "idempotency_key": key,
        "activity_type": "running",
        "duration_ms": 2_700_000,
        "detection": detection,
        "calories": 345,
        "distance_m": 7500.0,
        "session_id": "run-9",
    }
    if heart_rate:
        payload["average_heart_rate"] = 152
    assert isinstance(episode["payload"], dict)
    assert episode["payload"] == payload
    links = await _owner_links(chronicler)
    assert [dict(row) for row in links] == [
        {"episode_id": episode["id"], "entity_id": projection_pools.owner_id, "role": "owner"}
    ]
    checkpoint = await get_checkpoint(chronicler, SOURCE_NAME)
    assert checkpoint is not None and checkpoint.watermark == fact["created_at"]
    assert checkpoint.last_success_at is not None and checkpoint.last_error is None

    replayed = await wellness_ingest.translate_wellness_envelope(health, embedding, envelope)
    assert replayed["status"] == "ok" and replayed["fact_id"] == ingested["fact_id"]
    # facts_written describes accepted processing, not new inserts on a replay.
    assert await _facts(health) == facts
    repeated = await GoogleHealthWorkoutAdapter().run(pool=chronicler, chronicler_pool=chronicler)
    assert repeated.success and not repeated.skipped, repeated
    assert repeated.rows_projected == 0
    assert await _episodes(chronicler) == episodes
    assert await _owner_links(chronicler) == links
    repeated_checkpoint = await get_checkpoint(chronicler, SOURCE_NAME)
    assert repeated_checkpoint is not None
    assert repeated_checkpoint.watermark == checkpoint.watermark
    assert repeated_checkpoint.run_count == checkpoint.run_count + 1
    assert (
        repeated_checkpoint.last_success_at is not None and repeated_checkpoint.last_error is None
    )
    state = await get_source_state(chronicler, SOURCE_NAME)
    assert state is not None and state.active and state.inactive_reason is None

    # A forced scan processes the existing fact but the SQL upsert cannot duplicate it.
    scanned = await GoogleHealthWorkoutAdapter().project(
        chronicler, chronicler_pool=chronicler, since=None
    )
    assert scanned.success and not scanned.skipped and scanned.rows_projected == 1
    assert await _episodes(chronicler) == episodes
    assert await _owner_links(chronicler) == links

    # Same type/time, distinct genuine provider record: neither constant keys nor
    # an always-empty/always-skipped pipeline can satisfy this control.
    distinct = await wellness_ingest.translate_wellness_envelope(
        health, embedding, _envelope("run-10", detection=detection, heart_rate=heart_rate)
    )
    assert distinct["status"] == "ok" and distinct["fact_id"] != ingested["fact_id"]
    assert len(await _facts(health)) == 2
    added = await GoogleHealthWorkoutAdapter().run(pool=chronicler, chronicler_pool=chronicler)
    assert added.success and not added.skipped and added.rows_projected == 1
    final_episodes = await _episodes(chronicler)
    assert len(final_episodes) == 2
    assert {row["source_ref"] for row in final_episodes} == {
        f"health.facts:workout_session:google_health:{_EMAIL}:workout:run-9",
        f"health.facts:workout_session:google_health:{_EMAIL}:workout:run-10",
    }
    assert episode["id"] in {row["id"] for row in final_episodes}
    assert len(await _owner_links(chronicler)) == 2
