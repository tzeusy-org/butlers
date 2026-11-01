"""Real-Postgres evidence for the entity life-graph reads
(openspec/changes/entity-chronicles-and-commitment-doors, tasks 1.3 and 1.5).

test_entities_api.py pins the route contracts (source matrix, gates, degraded
envelopes) against a mocked pool. This module proves the parts only real data
can show:

* ``source=chronicler`` resolves organizer and participant links through the
  real ``episode_entities`` table and the real ``chronicler_list_episodes``
  MCP tool, called over a real FastMCP client, and the Relationship route
  consumes that tool result. No pre-shaped episode list is injected.
* ``GET /entities/{id}/commitments`` selects, orders and pages rows of a
  migrated ``public.owner_conditions`` ledger through the core commitment seam.

Chronicler and Relationship use separate databases here, as they use separate
schemas in production: the only path between them is the MCP call.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from fastmcp import Client, FastMCP

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db, get_mcp_manager
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Episode, Layer, Precision
from butlers.chronicler.storage import upsert_episode
from butlers.core.commitments import create_commitment, resolve_commitment
from butlers.core.owner_conditions import Observation, reconcile_snapshot
from butlers.db import register_jsonb_codec
from butlers.roster_imports import roster_module
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def core_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture(scope="module")
def chronicler_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["chronicler"])


@pytest.fixture
async def core_pool(core_db_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(core_db_url, min_size=1, max_size=4)
    if not await pool.fetchval("SELECT 1 FROM public.entities WHERE 'owner' = ANY(roles)"):
        await pool.execute(
            "INSERT INTO public.entities (canonical_name, entity_type, roles) "
            "VALUES ('Owner', 'person', '{owner}')"
        )
    yield pool
    await pool.close()


@pytest.fixture
async def chronicler_pool(chronicler_db_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(
        chronicler_db_url, min_size=1, max_size=4, init=register_jsonb_codec
    )
    await pool.execute("TRUNCATE TABLE episode_entities, episodes CASCADE")
    await pool.execute("TRUNCATE TABLE source_adapter_state, projection_checkpoints CASCADE")
    await seed_source_registry(pool, sources=INITIAL_SOURCES)
    yield pool
    await pool.close()


async def _new_entity(pool: asyncpg.Pool, name: str) -> UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ($1, 'person', '{}') RETURNING id",
        f"{name}-{uuid4().hex[:8]}",
    )


def _relationship_app(core_pool: asyncpg.Pool, mcp_manager: object | None = None) -> FastAPI:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = core_pool
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    if mcp_manager is not None:
        app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    return app


async def _get(app: FastAPI, path: str, **params: str | int) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, params=params or None)


# ---------------------------------------------------------------------------
# Task 1.3 — organizer and participant links through the real Chronicler tool
# ---------------------------------------------------------------------------


async def _seed_episode(
    pool: asyncpg.Pool, *, ref: str, start_at: datetime, title: str, links: dict[UUID, str]
) -> UUID:
    episode = await upsert_episode(
        pool,
        Episode(
            source_name="google_calendar.completed",
            source_ref=ref,
            episode_type="meeting",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            precision=Precision.EXACT,
            title=title,
            payload={"private": "never rendered"},
            layer=Layer.ACTIVITY,
        ),
    )
    assert episode.id is not None
    for entity_id, role in links.items():
        await pool.execute(
            "INSERT INTO episode_entities (episode_id, entity_id, role) VALUES ($1, $2, $3)",
            episode.id,
            entity_id,
            role,
        )
    return episode.id


async def test_organizer_and_participant_episodes_reach_source_chronicler(
    core_pool: asyncpg.Pool, chronicler_pool: asyncpg.Pool
) -> None:
    entity_id = await _new_entity(core_pool, "Sam")
    calendar_owner, stranger = uuid4(), uuid4()
    organized = await _seed_episode(
        chronicler_pool,
        ref="sam-organized",
        start_at=_T0,
        title="Sam's planning session",
        links={calendar_owner: "owner", entity_id: "organizer"},
    )
    attended = await _seed_episode(
        chronicler_pool,
        ref="sam-attended",
        start_at=_T0 + timedelta(days=1),
        title="Team lunch",
        links={calendar_owner: "owner", entity_id: "participant"},
    )
    await _seed_episode(
        chronicler_pool,
        ref="unrelated",
        start_at=_T0 + timedelta(days=2),
        title="Someone else's meeting",
        links={calendar_owner: "owner", stranger: "participant"},
    )

    chronicler_roster = roster_module("chronicler", "modules")
    mcp = FastMCP("chronicler-life-graph-test")
    await chronicler_roster.ChroniclerModule().register_tools(
        mcp, {}, SimpleNamespace(pool=chronicler_pool), "chronicler"
    )
    async with Client(mcp) as chronicler_client:
        calls: list[tuple[str, dict]] = []
        real_call_tool = chronicler_client.call_tool

        async def recording_call_tool(name: str, arguments: dict):
            calls.append((name, arguments))
            return await real_call_tool(name, arguments)

        chronicler_client.call_tool = recording_call_tool  # type: ignore[method-assign]
        mcp_manager = MagicMock()
        mcp_manager.get_client = AsyncMock(return_value=chronicler_client)
        app = _relationship_app(core_pool, mcp_manager)
        resp = await _get(
            app,
            f"/api/relationship/entities/{entity_id}/activity",
            source="chronicler",
            limit=5,
            offset=0,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert calls == [
        ("chronicler_list_episodes", {"participant_entity_id": str(entity_id), "limit": 500})
    ]
    assert body["degraded"] is False
    assert body["total"] == 2
    assert [(item["episode_id"], item["summary"]) for item in body["items"]] == [
        (str(attended), "Team lunch"),
        (str(organized), "Sam's planning session"),
    ]
    assert all(
        item["src"] == "chronicler" and item["store"] is None and item["id"] == item["episode_id"]
        for item in body["items"]
    )
    assert "never rendered" not in resp.text


# ---------------------------------------------------------------------------
# Task 1.5 — commitments endpoint against a migrated owner-condition ledger
# ---------------------------------------------------------------------------


async def _commit(
    pool: asyncpg.Pool,
    *,
    source: str,
    entity_id: UUID,
    direction: str,
    action: str,
    kind: str = "promise",
    deadline: str | None = None,
) -> str:
    transition = await create_commitment(
        pool,
        source=source,
        summary=action.capitalize(),
        kind=kind,
        direction=direction,
        counterparty_entity_id=str(entity_id),
        confidence=0.9,
        evidence_opened={"source": "conversation", "excerpt": "synthetic"},
        action_description=action,
        deadline=deadline,
    )
    assert transition is not None
    return transition.fingerprint


async def test_commitments_endpoint_selects_orders_and_pages_the_real_ledger(
    core_pool: asyncpg.Pool,
) -> None:
    entity_id = await _new_entity(core_pool, "Sam")
    other_entity = await _new_entity(core_pool, "Alex")
    tag = uuid4().hex[:8]
    relationship_src, finance_src = f"relationship:c-{tag}", f"finance:c-{tag}"

    owed = await _commit(
        core_pool,
        source=relationship_src,
        entity_id=entity_id,
        direction="owner_to_other",
        action="send sam the book",
        deadline="2026-09-08T00:00:00+00:00",
    )
    owed_to_owner = await _commit(
        core_pool,
        source=finance_src,
        entity_id=entity_id,
        direction="other_to_owner",
        action="sam repays the deposit",
        kind="obligation",
    )
    aging = await _commit(
        core_pool,
        source=finance_src,
        entity_id=entity_id,
        direction="owner_to_other",
        action="review sam's invoice",
        kind="follow_up",
    )
    # Excluded: self-directed, another entity, resolved, and non-commitment rows.
    await _commit(
        core_pool,
        source=relationship_src,
        entity_id=entity_id,
        direction="self",
        action="journal about sam",
    )
    await _commit(
        core_pool,
        source=relationship_src,
        entity_id=other_entity,
        direction="owner_to_other",
        action="call alex",
    )
    resolved = await _commit(
        core_pool,
        source=relationship_src,
        entity_id=entity_id,
        direction="owner_to_other",
        action="return sam's umbrella",
    )
    await resolve_commitment(
        core_pool,
        source=relationship_src,
        fingerprint=resolved,
        resolution_reason="satisfied",
        evidence_closed={"source": "owner_confirmed"},
    )
    await reconcile_snapshot(
        core_pool,
        source=relationship_src,
        observations=[
            Observation(
                fingerprint=f"reminder-{tag}",
                summary="Sam's birthday",
                metadata={
                    "class": "reminder",
                    "counterparty_entity_id": str(entity_id),
                    "direction": "owner_to_other",
                },
            )
        ],
        snapshot_complete=False,
        initial_grace_seconds=3600,
    )
    # Deterministic order: `aging` newest; the other two share an instant so the
    # fingerprint tie-break decides.
    tie = datetime(2026, 8, 1, tzinfo=UTC)
    await core_pool.execute(
        "UPDATE public.owner_conditions SET first_detected_at = $2 WHERE fingerprint = ANY($1)",
        [owed, owed_to_owner],
        tie,
    )
    await core_pool.execute(
        "UPDATE public.owner_conditions SET state = 'aging', escalation_level = 'L2', "
        "first_detected_at = $2 WHERE fingerprint = $1",
        aging,
        tie + timedelta(days=1),
    )
    expected_order = [aging, *sorted([owed, owed_to_owner])]

    app = _relationship_app(core_pool)
    path = f"/api/relationship/entities/{entity_id}/commitments"
    full = (await _get(app, path)).json()
    assert full["degraded"] is False
    assert full["total"] == 3
    assert [item["fingerprint"] for item in full["items"]] == expected_order
    by_fingerprint = {item["fingerprint"]: item for item in full["items"]}
    assert by_fingerprint[owed] == {
        "kind": "promise",
        "direction": "owner_to_other",
        "summary": "Send sam the book",
        "deadline": "2026-09-08T00:00:00+00:00",
        "escalation_level": "L0",
        "fingerprint": owed,
    }
    assert by_fingerprint[owed_to_owner]["direction"] == "other_to_owner"
    assert by_fingerprint[aging]["escalation_level"] == "L2"

    page = (await _get(app, path, limit=2, offset=1)).json()
    assert (page["total"], page["limit"], page["offset"]) == (3, 2, 1)
    assert [item["fingerprint"] for item in page["items"]] == expected_order[1:]
    past_end = (await _get(app, path, limit=2, offset=5)).json()
    assert (past_end["items"], past_end["total"]) == ([], 3)

    empty_entity = await _new_entity(core_pool, "Quiet")
    empty = (await _get(app, f"/api/relationship/entities/{empty_entity}/commitments")).json()
    assert (empty["items"], empty["total"], empty["degraded"]) == ([], 0, False)
