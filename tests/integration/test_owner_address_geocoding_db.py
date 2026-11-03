"""Real-Postgres owner anchor-address geocoding (bu-r3fmja.3).

Openspec change ``chronicler-situational-inference``.

Runs the core, memory and relationship Alembic chains, so ``relationship.entity_facts``,
``public.entities`` and ``relationship.address_geocodes`` are the production shapes and
the job's anchor-selection SQL runs unmodified. HTTP is an ``httpx.MockTransport``; no
test reaches a live geocoder. People, places and addresses are synthetic.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from butlers.tools.relationship.owner_address_geocoding import (
    LOCK_NAME,
    address_sha256,
    run_owner_address_geocoding,
    select_anchor_address_facts,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

_HOME = "1 Example Avenue, Singapore 123456"
_OFFICE = "8 Sample Boulevard, Singapore 654321"
_RENTAL = "Flat 2, 10 Test Street, London, United Kingdom"
_LIVES = "5 Demo Road, Singapore 111222"


@pytest.fixture
async def rel_pool(db):
    return await db(
        "core",
        "memory",
        "relationship",
        schemas={"relationship": "relationship"},
        schema="relationship",
    )


async def _entity(rel_pool, name: str, entity_type: str = "person", *, owner: bool = False):
    return await rel_pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ($1, $2, $3::text[]) RETURNING id
        """,
        name,
        entity_type,
        ["owner"] if owner else [],
    )


async def _fact(rel_pool, subject, predicate: str, obj, *, kind: str = "literal") -> uuid.UUID:
    return await rel_pool.fetchval(
        """
        INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src)
        VALUES ($1, $2, $3, $4, 'test') RETURNING id
        """,
        subject,
        predicate,
        str(obj),
        kind,
    )


async def _retract(rel_pool, fact_id) -> None:
    await rel_pool.execute(
        "UPDATE relationship.entity_facts SET validity = 'retracted' WHERE id = $1", fact_id
    )


class _Provider:
    """Serves OneMap and Nominatim from fixed address→coordinate maps."""

    def __init__(
        self,
        onemap: dict[str, tuple[str, str]] | None = None,
        nominatim: dict[str, tuple[str, str]] | None = None,
        *,
        nominatim_status: int = 200,
    ) -> None:
        self.onemap = onemap or {}
        self.nominatim = nominatim or {}
        self.nominatim_status = nominatim_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "www.onemap.gov.sg":
            hit = self.onemap.get(request.url.params["searchVal"])
            results = [{"LATITUDE": hit[0], "LONGITUDE": hit[1]}] if hit else []
            return httpx.Response(200, json={"found": len(results), "results": results})
        if request.url.host == "nominatim.openstreetmap.org":
            if self.nominatim_status != 200:
                return httpx.Response(self.nominatim_status)
            hit = self.nominatim.get(request.url.params["q"])
            return httpx.Response(200, json=[{"lat": hit[0], "lon": hit[1]}] if hit else [])
        raise AssertionError(f"unexpected host {request.url.host}")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    @property
    def queried(self) -> list[str]:
        return [r.url.params.get("searchVal") or r.url.params.get("q") for r in self.requests]


class _NoCredentials:
    async def resolve(self, key: str, *, env_fallback: bool = False) -> str | None:
        return None


async def _no_sleep(_seconds: float) -> None:
    return None


async def _run(rel_pool, provider: _Provider, *, now: datetime = _NOW, limit: int = 20):
    async with provider.client() as client:
        return await run_owner_address_geocoding(
            rel_pool,
            credential_store=_NoCredentials(),
            http_client=client,
            now=now,
            sleep=_no_sleep,
            limit=limit,
        )


async def _graph(rel_pool) -> dict[str, uuid.UUID]:
    """Owner with all three anchor kinds, plus every near-miss that must stay unsent."""
    owner = await _entity(rel_pool, "Owner", owner=True)
    employer = await _entity(rel_pool, "Employer Org", "organization")
    rental = await _entity(rel_pool, "Rental Flat", "place")
    home = await _entity(rel_pool, "Family Home", "place")
    friend = await _entity(rel_pool, "Friend", "person")
    old_employer = await _entity(rel_pool, "Old Employer", "organization")
    not_a_place = await _entity(rel_pool, "Landlord Co", "organization")
    friend_employer = await _entity(rel_pool, "Friend Employer", "organization")

    ids = {
        "owner_home": await _fact(rel_pool, owner, "has-address", _HOME),
        "employer": await _fact(rel_pool, employer, "has-address", _OFFICE),
        "rental": await _fact(rel_pool, rental, "has-address", _RENTAL),
        "lives": await _fact(rel_pool, home, "has-address", _LIVES),
        # Not anchors:
        "friend": await _fact(rel_pool, friend, "has-address", "9 Friend Lane, Singapore 222333"),
        "old_employer": await _fact(rel_pool, old_employer, "has-address", "Old Office, SG"),
        "not_a_place": await _fact(rel_pool, not_a_place, "has-address", "Landlord HQ, SG"),
        "friend_employer": await _fact(rel_pool, friend_employer, "has-address", "FE Tower, SG"),
        "retracted_home": await _fact(rel_pool, owner, "has-address", "Old Home, Singapore"),
    }
    await _fact(rel_pool, owner, "has-email", "owner@example.test")
    ids["works_at"] = await _fact(rel_pool, owner, "works-at", employer, kind="entity")
    await _fact(rel_pool, owner, "rental-location", rental, kind="entity")
    await _fact(rel_pool, owner, "lives-at", home, kind="entity")
    await _fact(rel_pool, owner, "rental-location", not_a_place, kind="entity")
    await _fact(rel_pool, owner, "friend-of", friend, kind="entity")
    await _fact(rel_pool, friend, "works-at", friend_employer, kind="entity")
    await _retract(rel_pool, await _fact(rel_pool, owner, "works-at", old_employer, kind="entity"))
    await _retract(rel_pool, ids["retracted_home"])
    return ids


async def test_selects_only_the_owner_anchor_addresses(rel_pool) -> None:
    ids = await _graph(rel_pool)

    async with rel_pool.acquire() as conn:
        facts = await select_anchor_address_facts(conn)

    assert {f.fact_id for f in facts} == {
        ids["owner_home"],
        ids["employer"],
        ids["rental"],
        ids["lives"],
    }
    by_id = {f.fact_id: f.address for f in facts}
    assert by_id[ids["employer"]] == _OFFICE


async def test_no_owner_means_no_anchors(rel_pool) -> None:
    place = await _entity(rel_pool, "Somewhere", "place")
    await _fact(rel_pool, place, "has-address", _HOME)

    async with rel_pool.acquire() as conn:
        assert await select_anchor_address_facts(conn) == []


async def test_run_stores_keyed_geocodes_and_logs_nothing_private(rel_pool, caplog) -> None:
    caplog.set_level("DEBUG")
    ids = await _graph(rel_pool)
    provider = _Provider(
        onemap={_HOME: ("1.3000012345", "103.8000067891"), _OFFICE: ("1.28", "103.85")},
        nominatim={_RENTAL: ("51.5000049", "-0.1200051")},
    )

    summary = await _run(rel_pool, provider)

    assert summary == {
        "status": "ok",
        "anchor_facts": 4,
        "due": 4,
        "attempted": 4,
        "resolved": 3,
        "not_found": 1,
        "failed": 0,
        "deferred": 0,
        "pruned": 0,
    }
    # Only anchor addresses were ever sent anywhere; _LIVES fell through OneMap to Nominatim.
    assert sorted(provider.queried) == sorted([_HOME, _OFFICE, _RENTAL, _LIVES, _LIVES])

    rows = {
        r["fact_id"]: r for r in await rel_pool.fetch("SELECT * FROM relationship.address_geocodes")
    }
    assert set(rows) == {ids["owner_home"], ids["employer"], ids["rental"], ids["lives"]}

    home = rows[ids["owner_home"]]
    assert home["address_sha256"] == address_sha256(_HOME)
    assert (home["status"], home["provider"], home["confidence"]) == ("resolved", "onemap", 0.9)
    assert (home["lat"], home["lon"]) == (Decimal("1.30000"), Decimal("103.80001"))
    assert (home["attempts"], home["geocoded_at"], home["next_attempt_at"]) == (1, _NOW, None)

    rental = rows[ids["rental"]]
    assert (rental["provider"], rental["confidence"]) == ("nominatim", 0.7)
    assert (rental["lat"], rental["lon"]) == (Decimal("51.50000"), Decimal("-0.12001"))

    lives = rows[ids["lives"]]
    assert (lives["status"], lives["provider"], lives["lat"], lives["lon"]) == (
        "not_found",
        "nominatim",
        None,
        None,
    )
    assert (lives["attempts"], lives["next_attempt_at"]) == (1, _NOW + timedelta(hours=1))

    for private in ("Example Avenue", "Sample Boulevard", "Test Street", "Demo Road"):
        assert private not in caplog.text
        assert private not in repr(summary)
    for coordinate in ("1.30000", "103.8", "51.5", "-0.12"):
        assert coordinate not in caplog.text
    assert str(ids["owner_home"]) in caplog.text


async def test_a_settled_run_sends_nothing(rel_pool) -> None:
    await _graph(rel_pool)
    first = _Provider(
        onemap={_HOME: ("1.3", "103.8"), _OFFICE: ("1.28", "103.85"), _LIVES: ("1.4", "103.9")},
        nominatim={_RENTAL: ("51.5", "-0.12")},
    )
    await _run(rel_pool, first)

    second = _Provider()
    summary = await _run(rel_pool, second, now=_NOW + timedelta(hours=1))

    assert second.requests == []
    assert (summary["due"], summary["attempted"]) == (0, 0)


async def test_changed_address_text_is_geocoded_again(rel_pool) -> None:
    ids = await _graph(rel_pool)
    await _run(rel_pool, _Provider(onemap={_HOME: ("1.3", "103.8")}))

    moved = "2 Example Avenue, Singapore 123457"
    await rel_pool.execute(
        "UPDATE relationship.entity_facts SET object = $2 WHERE id = $1", ids["owner_home"], moved
    )
    provider = _Provider(onemap={moved: ("1.31", "103.81")})
    await _run(rel_pool, provider, now=_NOW + timedelta(minutes=5))

    assert moved in provider.queried
    row = await rel_pool.fetchrow(
        "SELECT * FROM relationship.address_geocodes WHERE fact_id = $1", ids["owner_home"]
    )
    assert row["address_sha256"] == address_sha256(moved)
    assert (row["status"], row["lat"], row["attempts"]) == ("resolved", Decimal("1.31000"), 1)


async def test_not_found_and_failed_back_off(rel_pool) -> None:
    owner = await _entity(rel_pool, "Owner", owner=True)
    fact = await _fact(rel_pool, owner, "has-address", _RENTAL)

    async def row():
        return await rel_pool.fetchrow(
            "SELECT * FROM relationship.address_geocodes WHERE fact_id = $1", fact
        )

    await _run(rel_pool, _Provider(nominatim_status=503))
    first = await row()
    assert (first["status"], first["attempts"]) == ("failed", 1)
    assert first["next_attempt_at"] == _NOW + timedelta(hours=1)

    early = _Provider()
    await _run(rel_pool, early, now=_NOW + timedelta(minutes=59))
    assert early.requests == []

    t = _NOW + timedelta(hours=1)
    expected = [timedelta(hours=6), timedelta(hours=24), timedelta(hours=48)]
    for attempts, delay in enumerate(expected, start=2):
        await _run(rel_pool, _Provider(), now=t)
        current = await row()
        assert (current["status"], current["attempts"]) == ("not_found", attempts)
        assert current["next_attempt_at"] == t + delay
        t = t + delay

    await _run(rel_pool, _Provider(nominatim={_RENTAL: ("51.5", "-0.12")}), now=t)
    resolved = await row()
    assert (resolved["status"], resolved["attempts"], resolved["next_attempt_at"]) == (
        "resolved",
        5,
        None,
    )


async def test_work_per_run_is_capped(rel_pool) -> None:
    owner = await _entity(rel_pool, "Owner", owner=True)
    for n in range(5):
        await _fact(rel_pool, owner, "has-address", f"{n} Cap Street, London, United Kingdom")

    provider = _Provider()
    summary = await _run(rel_pool, provider, limit=2)

    assert len(provider.requests) == 2
    assert (summary["anchor_facts"], summary["due"], summary["attempted"], summary["deferred"]) == (
        5,
        5,
        2,
        3,
    )
    second = _Provider()
    await _run(rel_pool, second, limit=2)
    assert len(second.requests) == 2
    assert not set(provider.queried) & set(second.queried)


async def test_an_address_that_stops_being_an_anchor_is_pruned(rel_pool) -> None:
    ids = await _graph(rel_pool)
    await _run(rel_pool, _Provider(onemap={_OFFICE: ("1.28", "103.85")}))
    assert await rel_pool.fetchval(
        "SELECT count(*) FROM relationship.address_geocodes WHERE fact_id = $1", ids["employer"]
    )

    await _retract(rel_pool, ids["works_at"])
    summary = await _run(rel_pool, _Provider(), now=_NOW + timedelta(minutes=1))

    assert summary["pruned"] == 1
    assert not await rel_pool.fetchval(
        "SELECT count(*) FROM relationship.address_geocodes WHERE fact_id = $1", ids["employer"]
    )


async def test_a_concurrent_run_is_skipped(rel_pool) -> None:
    owner = await _entity(rel_pool, "Owner", owner=True)
    await _fact(rel_pool, owner, "has-address", _RENTAL)

    async with rel_pool.acquire() as holder:
        await holder.execute("SELECT pg_advisory_lock(hashtext($1))", LOCK_NAME)
        try:
            provider = _Provider()
            summary = await _run(rel_pool, provider)
        finally:
            await holder.execute("SELECT pg_advisory_unlock(hashtext($1))", LOCK_NAME)

    assert summary == {"status": "skipped", "reason": "already_running"}
    assert provider.requests == []
