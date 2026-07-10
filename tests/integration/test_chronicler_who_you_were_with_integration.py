"""Real-Postgres integration tests for GET /api/chronicler/who-you-were-with
(bu-jc6htw.2, Chronicler IEA p9b).

Mocked-pool unit tests (tests/chronicler/test_who_you_were_with_api.py) cover
parameter validation, channel derivation, and the degraded-envelope flags
against a stubbed pool. These tests prove the real read path: the new
``episode_entities`` LEFT JOIN (``role != 'owner'``), the ``social``-lane
filter, and the window-overlap union actually work against a migrated
Postgres container seeded with real ``episodes``/``episode_entities`` rows —
not just hand-shaped mock rows.

Per RFC 0014 §D17 the endpoint's own SQL must stay inside the chronicler
schema (enforced statically by
tests/contracts/test_chronicler_no_cross_schema.py); entity-name resolution
runs through a separate fan_out call to the relationship butler's own pool,
which is mocked here (same convention
tests/integration/test_ingestion_events_histogram_db.py uses for its
cross-butler fan_out call) — the real-Postgres value of this suite is the
chronicler-own-schema query, not the cross-butler name lookup.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
from butlers.chronicler.storage import upsert_episode
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_ENDPOINT = "/api/chronicler/who-you-were-with"
_T0 = datetime(2026, 7, 5, 0, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 7, 6, 0, 0, 0, tzinfo=UTC)


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
    await p.execute("TRUNCATE TABLE source_adapter_state, projection_checkpoints CASCADE")
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


async def _seed_social_episode(
    pool: asyncpg.Pool,
    *,
    source_ref: str,
    start_at: datetime,
    end_at: datetime,
    channel: str = "telegram_bot",
    entity_id=None,
    privacy: Privacy = Privacy.NORMAL,
) -> None:
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
            payload={"channel": channel, "message_count": 2, "participant_status": "resolved"},
            privacy=privacy,
            layer=Layer.ACTIVITY,
        ),
    )
    assert episode.id is not None
    owner_id = uuid4()
    await pool.execute(
        "INSERT INTO episode_entities (episode_id, entity_id, role) VALUES ($1, $2, 'owner')",
        episode.id,
        owner_id,
    )
    if entity_id is not None:
        await pool.execute(
            "INSERT INTO episode_entities (episode_id, entity_id, role) VALUES ($1, $2, 'participant')",
            episode.id,
            entity_id,
        )


def _build_app(pool: asyncpg.Pool):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    mock_db.fan_out_with_status = AsyncMock(return_value=({}, []))

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler":
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
    return app


async def _get(app, params: dict) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(_ENDPOINT, params=params)


async def test_resolved_participant_seen_through_real_episode_entities_join(pool):
    entity_id = uuid4()
    await _seed_social_episode(
        pool,
        source_ref="wywr-1",
        start_at=_T0.replace(hour=10),
        end_at=_T0.replace(hour=10, minute=30),
        channel="telegram_bot",
        entity_id=entity_id,
    )
    app = _build_app(pool)
    resp = await _get(app, {"start_at": _T0.isoformat(), "end_at": _T1.isoformat()})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["companions"]) == 1
    companion = data["companions"][0]
    assert companion["entity_id"] == str(entity_id)
    assert companion["unattributed"] is False
    assert companion["channel"] == "Telegram"
    assert companion["co_present_seconds"] == pytest.approx(1800.0)


async def test_owner_role_excluded_unattributed_participant_kept(pool):
    await _seed_social_episode(
        pool,
        source_ref="wywr-2",
        start_at=_T0.replace(hour=11),
        end_at=_T0.replace(hour=11, minute=15),
        channel="email",
        entity_id=None,
    )
    app = _build_app(pool)
    resp = await _get(app, {"start_at": _T0.isoformat(), "end_at": _T1.isoformat()})
    data = resp.json()["data"]
    assert len(data["companions"]) == 1
    companion = data["companions"][0]
    assert companion["unattributed"] is True
    assert companion["entity_id"] is None
    assert companion["channel"] == "email"


async def test_restricted_episode_excluded(pool):
    entity_id = uuid4()
    await _seed_social_episode(
        pool,
        source_ref="wywr-3",
        start_at=_T0.replace(hour=12),
        end_at=_T0.replace(hour=12, minute=15),
        entity_id=entity_id,
        privacy=Privacy.RESTRICTED,
    )
    app = _build_app(pool)
    resp = await _get(app, {"start_at": _T0.isoformat(), "end_at": _T1.isoformat()})
    assert resp.json()["data"]["companions"] == []


async def test_episode_outside_window_excluded(pool):
    entity_id = uuid4()
    await _seed_social_episode(
        pool,
        source_ref="wywr-4",
        start_at=_T0 - timedelta(days=5),
        end_at=_T0 - timedelta(days=5) + timedelta(minutes=30),
        entity_id=entity_id,
    )
    app = _build_app(pool)
    resp = await _get(app, {"start_at": _T0.isoformat(), "end_at": _T1.isoformat()})
    assert resp.json()["data"]["companions"] == []


async def test_two_episodes_same_entity_union_duration(pool):
    entity_id = uuid4()
    await _seed_social_episode(
        pool,
        source_ref="wywr-5a",
        start_at=_T0.replace(hour=9),
        end_at=_T0.replace(hour=9, minute=30),
        entity_id=entity_id,
    )
    await _seed_social_episode(
        pool,
        source_ref="wywr-5b",
        start_at=_T0.replace(hour=10),
        end_at=_T0.replace(hour=10, minute=30),
        entity_id=entity_id,
    )
    app = _build_app(pool)
    resp = await _get(app, {"start_at": _T0.isoformat(), "end_at": _T1.isoformat()})
    companions = resp.json()["data"]["companions"]
    assert len(companions) == 1
    assert companions[0]["co_present_seconds"] == pytest.approx(3600.0)
    assert companions[0]["episode_count"] == 2
