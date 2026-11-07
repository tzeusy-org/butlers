"""Owner presence entity settings: /api/home/settings/presence/owner-entities (bu-jv1moa.7).

Executes REQ-home-presence-configuration-001..003 at their real seams.  Unit cases drive
the production app and owner middleware through raw ASGI to prove owner control finishes
before any body octet, pool or audit, and that every refusal is content-blind.  Integration
cases run the mounted routes against migrated core + Home PostgreSQL: the GET envelope,
version-CAS writes, advisory-lock concurrency, rollback, the allowlisted audit, privacy
sentinels, the generic-state limitation, and the real context producer reading the list.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
import pytest_asyncio

from butlers.api.app import create_app
from butlers.core.state import state_set
from butlers.db import register_jsonb_codec
from butlers.jobs.context_producers import run_home_presence_context_producer
from butlers.testing.db_manager import db_manager
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import _DomainOwnerState

# ``seams`` is a shared fixture: owner-control and audit seams with a counting pool manager.
from tests.api.test_home_owner_places import (
    _ORIGIN,
    _OWNER_KEY,
    _mounted,
    _raw,
    _tables_containing,
    seams,  # noqa: F401
)

_DOCKER_AVAILABLE = shutil.which("docker") is not None
_ROUTE = "/api/home/settings/presence/owner-entities"
_KEY = "home:presence:owner_entities"
# Synthetic private identifiers: any appearance outside the state/snapshot rows is a leak.
_OWNER = "person.sentinel_owner_9c2e"
_ROOM = "sensor.sentinel_room_9c2e"
_STALE = "device_tracker.sentinel_phone_9c2e"
_ACTION = "home_presence_owner_entities_put"


def _put(entities: list[str], version: int) -> dict:
    return {"owner_entities": entities, "expected_version": version}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_owner_control_refuses_before_body_pool_or_audit(seams, method: str) -> None:  # noqa: F811
    """Unavailable owner auth is 503 and a missing or wrong credential 401, both blind."""
    unavailable = _mounted(seams.manager, create_app(api_key=_OWNER_KEY))
    configured = _mounted(seams.manager, create_app(api_key=_OWNER_KEY))
    configured.state.owner_auth_service = _DomainOwnerState(_OWNER_KEY)
    body = json.dumps(_put([_OWNER], 0)).encode() if method == "PUT" else b""
    owner = [(b"x-api-key", _OWNER_KEY.encode())]
    for app, headers, expected in [
        (unavailable, owner, (503, "AUTH_UNAVAILABLE")),
        (configured, [], (401, "UNAUTHORIZED")),
        (configured, [(b"x-api-key", b"wrong-owner-key")], (401, "UNAUTHORIZED")),
    ]:
        status, response, received = await _raw(app, method, _ROUTE, body, headers)
        assert (status, response["error"]["code"], received) == (*expected, 0)
    assert seams.manager.pool.call_count == 0
    assert seams.generic == []

    # Positive control: the owner credential reaches the pool seam, which is unavailable,
    # and that is a fixed 503 rather than an empty "unconfigured" success.
    status, response, _ = await _raw(configured, method, _ROUTE, body, owner)
    assert (status, response["error"]["code"]) == (503, "PRESENCE_CONFIG_UNAVAILABLE")
    assert "configured" not in json.dumps(response)
    assert seams.generic == []


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        (_ROUTE, json.dumps(_put([_OWNER, _OWNER], 0))),
        (_ROUTE, json.dumps({**_put([_OWNER], 0), "actor": "someone-else"})),
        (_ROUTE, json.dumps({"owner_entities": _OWNER, "expected_version": 0})),
        (_ROUTE, json.dumps(_put([_OWNER], -1))),
        (_ROUTE, json.dumps(_put([_OWNER], True))),
        (_ROUTE, json.dumps(_put([_OWNER], "1"))),
        (_ROUTE, json.dumps(_put([f"sensor.s{i}" for i in range(51)], 0))),
        (_ROUTE, json.dumps(_put(["Person.Owner"], 0))),
        (_ROUTE, json.dumps(_put([f" {_OWNER}"], 0))),
        (_ROUTE, json.dumps(_put(["person"], 0))),
        (_ROUTE, json.dumps(_put(["person." + "a" * 249], 0))),
        (_ROUTE, json.dumps(_put(["persön.owner"], 0))),
        (_ROUTE, json.dumps({"owner_entities": [_OWNER]})),
        (_ROUTE, "{not json"),
        (f"{_ROUTE}?owner=1", json.dumps(_put([_OWNER], 0))),
    ],
    ids=[
        "duplicate",
        "unknown-field",
        "non-array",
        "negative-version",
        "bool-version",
        "string-version",
        "too-many",
        "uppercase",
        "whitespace",
        "no-domain",
        "too-long",
        "non-ascii",
        "missing-version",
        "malformed-json",
        "query-string",
    ],
)
async def test_malformed_put_is_one_content_blind_refusal(seams, path: str, body: str) -> None:  # noqa: F811
    """Shape, syntax, count, duplicate and query errors share one fixed 422 with no echo."""
    app = _mounted(seams.manager)
    status, response, _ = await _raw(app, "PUT", path, body.encode(), [])
    assert status == 422
    assert response == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "Presence settings request is invalid.",
            "butler": None,
            "details": None,
        }
    }
    assert seams.generic == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_oversized_body_stops_at_thirty_two_kib(seams) -> None:  # noqa: F811
    """The body stops at 32 KiB; the refusal still reaches the pool once, for its audit."""
    app = _mounted(seams.manager)
    status, response, _ = await _raw(app, "PUT", _ROUTE, b" " * 32_769, [])
    assert (status, response["error"]["code"]) == (413, "REQUEST_BODY_TOO_LARGE")
    assert seams.manager.pool.call_count == 1


# ---------------------------------------------------------------------------
# Real migrated PostgreSQL (core + Home)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def presence_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core", "home"])


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def presence_pool(presence_db_url):
    pool = await asyncpg.create_pool(
        presence_db_url, min_size=2, max_size=6, init=register_jsonb_codec
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(loop_scope="session")
async def client(presence_pool, seams):  # noqa: F811
    await presence_pool.execute("DELETE FROM state WHERE key = $1", _KEY)
    await presence_pool.execute("TRUNCATE ha_entity_snapshot")
    now = datetime.now(UTC)
    await presence_pool.executemany(
        "INSERT INTO ha_entity_snapshot (entity_id, state, last_updated) VALUES ($1, $2, $3)",
        [
            (_OWNER, "home", now),
            (_ROOM, "kitchen", now),
            # A long-stale row is still a locally observed, valid reference.
            (_STALE, "home", now - timedelta(days=90)),
        ],
    )
    seams.manager = db_manager({"home": presence_pool})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_mounted(seams.manager)), base_url=_ORIGIN
    ) as http:
        yield http


async def _audits(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT actor, metadata FROM public.audit_log WHERE action = $1 ORDER BY id", _ACTION
    )
    return [{"actor": row["actor"], **row["metadata"]} for row in rows]


async def _row(pool: asyncpg.Pool) -> tuple:
    row = await pool.fetchrow("SELECT value, version FROM state WHERE key = $1", _KEY)
    return (None, 0) if row is None else (row["value"], row["version"])


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_get_put_cas_lifecycle_audit_and_privacy(
    client,
    presence_pool,
    seams,  # noqa: F811
    caplog,
) -> None:
    """No row, first insert, CAS edit, exact no-op, stale conflict, references and clear."""
    audits_before = len(await _audits(presence_pool))
    context_before = await presence_pool.fetchval("SELECT count(*) FROM public.user_context")
    caplog.set_level("DEBUG")

    empty = await client.get(_ROUTE)
    assert empty.headers["cache-control"] == "no-store"
    assert empty.json()["data"] == {"configured": False, "owner_entities": [], "version": 0}

    # Submitting the absent-or-empty value is a no-op success at any version.
    noop = await client.put(_ROUTE, json=_put([], 7))
    assert (noop.status_code, noop.json()["data"]["version"]) == (200, 0)
    assert await _row(presence_pool) == (None, 0)

    first = await client.put(_ROUTE, json=_put([_ROOM, _OWNER], 0))
    assert first.status_code == 200
    assert first.json()["data"] == {
        "configured": True,
        "owner_entities": sorted([_OWNER, _ROOM]),
        "version": 1,
    }
    assert await _row(presence_pool) == (sorted([_OWNER, _ROOM]), 1)

    # Exact resubmission from a stale version is a no-op success with zero writes.
    again = await client.put(_ROUTE, json=_put([_OWNER, _ROOM], 0))
    assert (again.status_code, again.json()["data"]["version"]) == (200, 1)
    # A stale divergent submission is a conflict that names no identifier.
    stale = await client.put(_ROUTE, json=_put([_OWNER], 0))
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "VERSION_CONFLICT")
    assert _OWNER not in stale.text and _ROOM not in stale.text
    # Unobserved references are one aggregate category exposing only the count.
    unknown = await client.put(
        _ROUTE, json=_put([_OWNER, "person.never_seen_a", "person.never_seen_b"], 1)
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "INVALID_REFERENCE"
    assert unknown.json()["error"]["details"] == {"invalid_reference_count": 2}
    assert "never_seen" not in unknown.text
    assert await _row(presence_pool) == (sorted([_OWNER, _ROOM]), 1)

    # A stale-but-observed tracker is a valid reference.
    changed = await client.put(_ROUTE, json=_put([_STALE], 1))
    assert changed.json()["data"] == {"configured": True, "owner_entities": [_STALE], "version": 2}

    # The generic state path writes outside this capability's lock and CAS (REQ-home-presence-configuration-002).
    await state_set(presence_pool, _KEY, [_OWNER])
    generic = await client.put(_ROUTE, json=_put([_ROOM], 2))
    assert (generic.status_code, generic.json()["error"]["code"]) == (409, "VERSION_CONFLICT")

    cleared = await client.put(_ROUTE, json=_put([], 3))
    assert cleared.json()["data"] == {"configured": False, "owner_entities": [], "version": 4}
    assert await _row(presence_pool) == ([], 4)
    listed = await client.get(_ROUTE)
    assert listed.json()["data"] == {"configured": False, "owner_entities": [], "version": 4}

    # A PUT is only a configuration receipt: no user_context write.
    assert (
        await presence_pool.fetchval("SELECT count(*) FROM public.user_context") == context_before
    )

    audits = (await _audits(presence_pool))[audits_before:]
    assert [audit["outcome"] for audit in audits] == [
        "unchanged",
        "updated",
        "unchanged",
        "version_conflict",
        "invalid_reference",
        "updated",
        "version_conflict",
        "updated",
    ]
    for audit in audits:
        assert set(audit) == {
            "actor",
            "operation",
            "configured",
            "entity_count",
            "prior_version",
            "new_version",
            "outcome",
        }
    assert audits[1] | {"actor": None} == {
        "actor": None,
        "operation": "put",
        "configured": True,
        "entity_count": 2,
        "prior_version": 0,
        "new_version": 1,
        "outcome": "updated",
    }
    assert seams.generic == []
    for needle in (_OWNER, _ROOM, _STALE):
        assert await _tables_containing(presence_pool, needle) <= {
            "public.state",
            "public.ha_entity_snapshot",
        }
        assert needle not in caplog.text


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_refused_bodies_each_record_one_invalid_request_audit(client, presence_pool) -> None:
    """Malformed and oversized PUTs are terminal outcomes too: one blind audit event each."""
    audits_before = len(await _audits(presence_pool))
    malformed = await client.put(_ROUTE, content=b"{not json")
    oversized = await client.put(_ROUTE, content=b" " * 32_769)
    assert (malformed.status_code, oversized.status_code) == (422, 413)
    audits = (await _audits(presence_pool))[audits_before:]
    assert [(audit["outcome"], audit["entity_count"]) for audit in audits] == [
        ("invalid_request", None),
        ("invalid_request", None),
    ]
    assert await _row(presence_pool) == (None, 0)


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_malformed_stored_value_fails_closed(client, presence_pool) -> None:
    """A stored non-list blocks GET and PUT with a blind 503 and is never overwritten."""
    await state_set(presence_pool, _KEY, {"secret_shape": _OWNER})
    for response in (
        await client.get(_ROUTE),
        await client.put(_ROUTE, json=_put([_OWNER], 1)),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (
            503,
            "PRESENCE_CONFIG_UNAVAILABLE",
        )
        for leak in (_OWNER, "secret_shape", _KEY, "dict"):
            assert leak not in response.text
    assert await _row(presence_pool) == ({"secret_shape": _OWNER}, 1)


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_advisory_lock_serializes_concurrent_writers(client, presence_pool) -> None:
    """Divergent racers yield one winner and one 409; identical racers converge once."""
    divergent = await asyncio.gather(
        client.put(_ROUTE, json=_put([_OWNER], 0)),
        client.put(_ROUTE, json=_put([_ROOM], 0)),
    )
    assert sorted(response.status_code for response in divergent) == [200, 409]
    assert (await _row(presence_pool))[1] == 1

    identical = await asyncio.gather(
        client.put(_ROUTE, json=_put([_OWNER, _ROOM], 1)),
        client.put(_ROUTE, json=_put([_OWNER, _ROOM], 1)),
    )
    assert [response.status_code for response in identical] == [200, 200]
    assert {response.json()["data"]["version"] for response in identical} == {2}
    assert await _row(presence_pool) == (sorted([_OWNER, _ROOM]), 2)


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_pre_commit_failure_changes_nothing(client, presence_pool) -> None:
    """A failure after validation but before commit leaves value and version intact."""
    await client.put(_ROUTE, json=_put([_OWNER], 0))
    await presence_pool.execute(
        "CREATE FUNCTION public.reject_presence_audit_fixture() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic audit failure'; END $$"
    )
    await presence_pool.execute(
        "CREATE TRIGGER reject_presence_audit_fixture BEFORE INSERT ON public.audit_log "
        f"FOR EACH ROW WHEN (NEW.action = '{_ACTION}') "
        "EXECUTE FUNCTION public.reject_presence_audit_fixture()"
    )
    try:
        failed = await client.put(_ROUTE, json=_put([_ROOM], 1))
    finally:
        await presence_pool.execute(
            "DROP TRIGGER reject_presence_audit_fixture ON public.audit_log"
        )
        await presence_pool.execute("DROP FUNCTION public.reject_presence_audit_fixture()")
    assert (failed.status_code, failed.json()["error"]["code"]) == (
        503,
        "PRESENCE_CONFIG_UNAVAILABLE",
    )
    assert "synthetic audit failure" not in failed.text
    assert await _row(presence_pool) == ([_OWNER], 1)


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_next_producer_run_consumes_the_stored_list(client, presence_pool) -> None:
    """The list a PUT stores is exactly what the scheduled producer reads into at_home."""
    await presence_pool.execute(
        "INSERT INTO ha_source_health (source, status, last_success_at, updated_at) "
        "VALUES ('home_assistant', 'healthy', now(), now()) "
        "ON CONFLICT (source) DO UPDATE SET status = 'healthy', "
        "last_success_at = now(), updated_at = now()"
    )
    await presence_pool.execute(
        "UPDATE public.user_context SET superseded_at = now() "
        "WHERE signal_type <> 'dnd' AND superseded_at IS NULL"
    )
    unconfigured = await run_home_presence_context_producer(presence_pool)
    assert unconfigured["presence"] == "unconfigured"

    assert (await client.put(_ROUTE, json=_put([_OWNER], 0))).status_code == 200
    configured = await run_home_presence_context_producer(presence_pool)
    assert configured["presence"] == "home"
    assert await presence_pool.fetchval(
        "SELECT count(*) FROM public.user_context WHERE signal_type = 'at_home' "
        "AND set_by_butler = 'home' AND superseded_at IS NULL AND expires_at > now()"
    )
