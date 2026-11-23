"""The production owner-watch providers over migrated PostgreSQL (bu-4c7nvp.1.5.1).

Real ``public.owner_watch_sources()`` (core_368), the real owner entity, the real watch and
condition stores, and the Switchboard role. Synthetic data only.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from butlers.watches import WatchState, providers, tick
from butlers.watches.approval import apply_watch_card, canonical
from butlers.watches.providers import GrantUnavailable, OwnerWatchRuntime

pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.asyncio(loop_scope="session")]

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
KINDS = [
    "expect.window",
    "home.sensing",
    "owner.activity",
    "owner.clock",
    "owner.mode",
    "owner.place",
    "owner.suppression",
    "system.dep",
]
from butlers.watches.approval import PROVIDERS as PROVIDERS_ABSENT  # noqa: E402

PREDICATE = {"tag": "mode", "mode": "travel", "change": "entered"}


@pytest.fixture(scope="module")
def url(postgres_container):
    return create_migrated_test_db(
        postgres_container, migration_db_name(), ["core"], schemas={"core": "switchboard"}
    )


async def _as_switchboard(connection):
    await connection.execute("SET ROLE butler_switchboard_rw")
    await connection.execute("SET search_path = switchboard, public")


@pytest.fixture
async def world(url, postgres_container):
    """An owner entity, a clean watch table, and Switchboard, lock and owner pools."""
    name = urlparse(url).path.lstrip("/")
    admin_url = migration_bootstrap_db_url(postgres_container, name).replace("+psycopg2", "")
    admin = await asyncpg.connect(admin_url)
    try:
        async with admin.transaction():
            await admin.execute("ALTER TABLE public.owner_watches DISABLE TRIGGER ALL")
            await admin.execute("TRUNCATE public.owner_watches")
            await admin.execute("ALTER TABLE public.owner_watches ENABLE TRIGGER ALL")
            await admin.execute("DELETE FROM public.entities WHERE 'owner' = ANY(roles)")
            owner = await admin.fetchval(
                "INSERT INTO public.entities (canonical_name, roles) "
                "VALUES ('Synthetic Owner', ARRAY['owner']) RETURNING id::text"
            )
            await admin.execute("DELETE FROM switchboard.state WHERE key LIKE 'owner_watch:card:%'")
        async with (
            asyncpg.create_pool(
                url, setup=_as_switchboard, init=register_jsonb_codec, min_size=1, max_size=6
            ) as runtime,
            asyncpg.create_pool(url, setup=_as_switchboard, min_size=1, max_size=3) as locks,
            asyncpg.create_pool(url, min_size=1, max_size=2) as outsider,
        ):
            yield {
                "owner": owner,
                "pool": runtime,
                "locks": locks,
                "outsider": outsider,
                "admin": admin,
            }
    finally:
        await admin.close()


def _runtime(world, clock=lambda: NOW, **kw):
    return OwnerWatchRuntime(world["pool"], world["locks"], clock=clock, **kw)


async def test_read_sources_returns_the_registered_kinds_and_the_owner(world):
    sources = await providers.read_sources(world["pool"], NOW)
    assert sources is not None
    assert list(sources.kinds) == KINDS and sources.owner == world["owner"]
    assert sources.unwritten == frozenset(f"perception:{k}" for k in KINDS)  # nothing written yet


async def test_the_grant_is_derived_from_the_live_read_never_from_the_model(world):
    runtime = _runtime(world)
    assert await runtime.prepare()
    grant = runtime.grant_for("w1")
    assert grant.goal_id == "w1"
    assert grant.sources == frozenset({"clock", *(f"perception:{k}" for k in KINDS)})
    assert grant.subjects == frozenset({world["owner"], None})
    assert not grant.decisions and not grant.effects and not grant.bindings  # goal-own: never
    narrowed = replace(runtime._sources, kinds=tuple(KINDS[:-1]))
    runtime._sources = narrowed
    assert runtime.grant_for("w1").digest != grant.digest  # a moved registry moves the digest


async def test_a_stale_or_unreadable_source_is_unknown_not_a_judgement(world):
    clock_now = [NOW]
    runtime = _runtime(world, clock=lambda: clock_now[0])
    with pytest.raises(GrantUnavailable):
        runtime.grant_for("w1")  # never prepared
    assert runtime.snapshot_for(NOW) is None
    assert await runtime.prepare()
    assert runtime.snapshot_for(NOW) is not None
    clock_now[0] = NOW + providers.SOURCES_TTL + timedelta(seconds=1)
    with pytest.raises(GrantUnavailable):
        runtime.grant_for("w1")  # past the 60 s cache
    assert runtime.snapshot_for(clock_now[0]) is None


async def test_other_roles_cannot_read_so_the_provider_goes_unknown(world):
    broken = OwnerWatchRuntime(world["outsider"], world["locks"], clock=lambda: NOW)
    assert await broken.prepare() is False  # the function refuses a non-Switchboard identity
    with pytest.raises(GrantUnavailable):
        broken.grant_for("w1")
    assert broken.snapshot_for(NOW) is None


async def test_no_single_owner_entity_is_unknown(world):
    await world["admin"].execute("DELETE FROM public.entities WHERE 'owner' = ANY(roles)")
    runtime = _runtime(world)
    assert await runtime.prepare() is False


async def _parked(world, runtime, **spec):
    assert await runtime.prepare()
    card = runtime.service.propose_create({"predicate": PREDICATE, **spec}, now=NOW)
    body = canonical(card.body)
    await world["admin"].execute(
        "INSERT INTO switchboard.state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        f"owner_watch:card:{card.digest}",
        json.dumps({"card_json": body, "digest": card.digest}),
    )
    return card, body


async def test_the_apply_creates_a_watch_through_the_real_providers(world):
    runtime = _runtime(world)
    card, body = await _parked(world, runtime)
    provider = runtime.providers()
    result = await apply_watch_card(
        world["pool"],
        card_json=body,
        digest=card.digest,
        service_for=provider.service_for,
        snapshot_for=provider.snapshot_for,
        prepare=provider.prepare,
        now=NOW,
    )
    assert result["status"] == "applied", result
    stored = await runtime.service.store.get(result["watch_id"])
    assert stored is not None and stored.state is WatchState.ACTIVE
    assert stored.sources == (
        "perception:owner.mode",
    )  # the card binds the predicate's own read set
    replay = await apply_watch_card(
        world["pool"],
        card_json=body,
        digest=card.digest,
        service_for=provider.service_for,
        snapshot_for=provider.snapshot_for,
        prepare=provider.prepare,
        now=NOW,
    )
    assert replay["watch_id"] == result["watch_id"]  # an approved card applies once


async def test_an_unreadable_grant_source_refuses_the_apply_and_changes_nothing(world):
    runtime = _runtime(world)
    card, body = await _parked(world, runtime)
    broken = OwnerWatchRuntime(world["outsider"], world["locks"], clock=lambda: NOW)
    provider = broken.providers()
    result = await apply_watch_card(
        world["pool"],
        card_json=body,
        digest=card.digest,
        service_for=provider.service_for,
        snapshot_for=provider.snapshot_for,
        prepare=provider.prepare,
        now=NOW,
    )
    assert result == {"error": "owner_watches_unavailable"}
    assert await runtime.service.store.list_all() == []


async def test_a_live_tick_with_the_real_providers_runs_and_a_dead_source_is_blind(world):
    runtime = _runtime(world)
    card, body = await _parked(world, runtime)
    provider = runtime.providers()
    applied = await apply_watch_card(
        world["pool"],
        card_json=body,
        digest=card.digest,
        service_for=provider.service_for,
        snapshot_for=provider.snapshot_for,
        prepare=provider.prepare,
        now=NOW,
    )
    assert applied["status"] == "applied"
    result = await tick.run_tick(world["pool"], now=NOW, live=True, providers=provider)
    assert result["status"] == "ok" and result["delivered"] == 0  # nothing fired
    broken = OwnerWatchRuntime(world["outsider"], world["locks"], clock=lambda: NOW)
    blind = await tick.run_tick(world["pool"], now=NOW, live=True, providers=broken.providers())
    assert blind == {"status": tick.BLIND}
    watch = await runtime.service.store.get(applied["watch_id"])
    assert watch is not None and watch.state is WatchState.ACTIVE  # a dead source pauses nothing


async def test_resolve_providers_is_absent_unless_live_and_registered(world):
    from butlers.watches.approval import PROVIDERS

    assert await providers.resolve_providers(world["pool"]) is PROVIDERS  # flag off by default
    assert await providers.resolve_providers(world["pool"], live=True) is PROVIDERS  # unregistered

    class FakeDb:
        pool = world["pool"]

    async def make_lock_pool():
        return world["locks"]

    providers.register_database(FakeDb(), lock_pool_factory=make_lock_pool)
    try:
        resolved = await providers.resolve_providers(world["pool"], live=True)
        assert resolved is not PROVIDERS and resolved.service_for() is not None
        assert tick.OWNER_WATCHES_LIVE is False
    finally:
        providers._database = None
        providers._lock_pool_factory = None
        providers._runtime = None


async def test_an_expired_cache_leaves_a_running_watch_alone(world):
    clock_now = [NOW]
    runtime = _runtime(world, clock=lambda: clock_now[0])
    card, body = await _parked(world, runtime)
    provider = runtime.providers()
    applied = await apply_watch_card(
        world["pool"],
        card_json=body,
        digest=card.digest,
        service_for=provider.service_for,
        snapshot_for=provider.snapshot_for,
        prepare=provider.prepare,
        now=NOW,
    )
    watch = await runtime.service.store.get(applied["watch_id"])
    assert watch is not None
    clock_now[0] = NOW + providers.SOURCES_TTL + timedelta(seconds=1)  # expired mid-tick
    with pytest.raises(GrantUnavailable):
        runtime.service.grant_is_current(watch)  # unknown, never "changed"
    view = await runtime.service.view(watch.id)
    assert view.reason is None
    result = await tick.run_tick(world["pool"], now=clock_now[0], live=True, providers=provider)
    assert result["status"] == "ok"  # the tick re-prepared: a fresh read, nothing paused
    stored = await runtime.service.store.get(watch.id)
    assert stored is not None and stored.state is WatchState.ACTIVE


async def test_the_tick_health_row_round_trips_to_the_collector_with_closed_words_only(world):
    from butlers.capability_health.collectors import probe_owner_watches
    from butlers.watches.health import KEY, StateReporter

    pool = world["pool"]
    await StateReporter(pool, now=lambda: NOW).report("x", "up", "tick_ok")
    seen = await probe_owner_watches(pool, live=True, clock=lambda: NOW)
    assert (seen.state, seen.cause) == ("available", None)
    raw = await pool.fetchval("SELECT value::text FROM state WHERE key = $1", KEY)
    assert set(json.loads(raw)) == {"state", "reason", "at"}  # no watch id, source or owner data
    await StateReporter(pool, now=lambda: NOW).report("x", "degraded", "no_snapshot_provider")
    seen = await probe_owner_watches(pool, live=True, clock=lambda: NOW)
    assert (seen.state, seen.cause) == ("degraded", "provider_unavailable")
    assert await pool.fetchval("SELECT count(*) FROM state WHERE key = $1", KEY) == 1
    stale = await probe_owner_watches(pool, live=True, clock=lambda: NOW + timedelta(minutes=11))
    assert stale.state is None  # an old report is no claim


async def test_a_real_blind_tick_reports_through_the_reporter(world):
    from butlers.watches.health import KEY, StateReporter

    pool = world["pool"]
    result = await tick.run_tick(
        pool, now=NOW, live=True, providers=PROVIDERS_ABSENT, reporter=StateReporter(pool)
    )
    assert result == {"status": tick.BLIND}
    value = json.loads(await pool.fetchval("SELECT value::text FROM state WHERE key = $1", KEY))
    assert (value["state"], value["reason"]) == ("degraded", "no_snapshot_provider")
