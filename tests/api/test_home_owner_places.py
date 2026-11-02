"""Owner place registry editor: /api/home/places over public.owner_places (bu-jv1moa.7).

Unit cases drive the production app and owner middleware through raw ASGI to prove the
refusal order (owner control before any body octet, decode or pool) and content-blind
validation.  Integration cases run the mounted routes against a migrated PostgreSQL
database: the editor lifecycle, compare-and-swap, live-place invariants, rollback, the
allowlisted audit, the runtime-role read matrix and the shared deterministic read helper.
"""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import asyncpg
import httpx
import pytest
import pytest_asyncio

from butlers.api import dashboard_audit_middleware
from butlers.api.app import create_app
from butlers.api.deps import get_db_optional
from butlers.db import register_jsonb_codec
from butlers.owner_places import load_active_owner_places
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import _DomainOwnerState, create_authenticated_domain_app
from tests.migrations.test_qa_publication_operations_migration import _bootstrap_rerun

_DOCKER_AVAILABLE = shutil.which("docker") is not None
_ROUTE = "/api/home/places"
_ORIGIN = "https://butlers.example.test"
_OWNER_KEY = "synthetic-owner-key"
# Synthetic private values: any appearance outside public.owner_places is a leak.
_SSID_SENTINEL = "ssid-sentinel-7f3a"
_ZONE_SENTINEL = "zone.sentinel_7f3a"
_LAT_SENTINEL = 12.34567
_LON_SENTINEL = 76.54321


def _place(**overrides) -> dict:
    place = {
        "label": "Home",
        "kind": "home",
        "lat": _LAT_SENTINEL,
        "lon": _LON_SENTINEL,
        "radius_m": 150,
        "aliases": ["the flat"],
        "ssids": [_SSID_SENTINEL],
        "ha_zone": _ZONE_SENTINEL,
        "paused": False,
    }
    place.update(overrides)
    return place


class _Manager:
    """Dashboard DB-manager seam that counts pool requests; no pool means unavailable."""

    def __init__(self, pool: asyncpg.Pool | None = None) -> None:
        self.serving = pool
        self.calls = 0

    def pool(self, _name: str) -> asyncpg.Pool:
        self.calls += 1
        if self.serving is None:
            raise KeyError("synthetic unavailable")
        return self.serving


@pytest.fixture
def seams(monkeypatch):
    observed = SimpleNamespace(manager=_Manager(), generic=[])

    async def record_generic(_db_manager, **fields) -> None:
        observed.generic.append(fields)

    monkeypatch.setattr(dashboard_audit_middleware, "get_db_manager", lambda: observed.manager)
    monkeypatch.setattr(dashboard_audit_middleware, "emit_dashboard_audit", record_generic)
    return observed


def _mounted(manager: _Manager, app=None):
    app = app if app is not None else create_authenticated_domain_app()
    app.dependency_overrides[get_db_optional] = lambda: manager
    return app


async def _raw(app, method: str, path: str, body: bytes, headers: list[tuple[bytes, bytes]]):
    """Drive one request through the full ASGI stack, counting ``receive`` calls."""
    pending = [body]
    received = 0
    sent: list[dict] = []

    async def receive():
        nonlocal received
        received += 1
        if pending:
            return {"type": "http.request", "body": pending.pop(), "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    path_only, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path_only,
        "raw_path": path_only.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"butlers.example.test"),
            (b"content-type", b"application/json"),
            *headers,
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("butlers.example.test", 443),
    }
    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, (json.loads(raw) if raw else None), received


_FIXED_ID = "00000000-0000-4000-8000-00000000f1ac"
_WRITES = [
    ("POST", _ROUTE),
    ("PUT", f"{_ROUTE}/{_FIXED_ID}"),
    ("DELETE", f"{_ROUTE}/{_FIXED_ID}?expected_version=1"),
]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [("GET", _ROUTE), *_WRITES])
async def test_owner_control_refuses_before_body_pool_or_generic_audit(
    seams, method: str, path: str
) -> None:
    """Unavailable or wrong owner authority fails before ASGI receive, pool or any audit."""
    unavailable = _mounted(seams.manager, create_app(api_key=_OWNER_KEY))
    configured = _mounted(seams.manager, create_app(api_key=_OWNER_KEY))
    configured.state.owner_auth_service = _DomainOwnerState(_OWNER_KEY)
    payload = _place(expected_version=1) if method == "PUT" else _place()
    body = json.dumps(payload).encode()
    owner = [(b"x-api-key", _OWNER_KEY.encode())]
    for app, headers, expected in [
        (unavailable, owner, (503, "AUTH_UNAVAILABLE")),
        (configured, [], (401, "UNAUTHORIZED")),
        (configured, [(b"x-api-key", b"wrong-owner-key")], (401, "UNAUTHORIZED")),
    ]:
        status, response, received = await _raw(app, method, path, body, headers)
        assert (status, response["error"]["code"], received) == (*expected, 0)
    assert seams.manager.calls == 0
    assert seams.generic == []

    # Positive control: the owner credential reaches the pool seam, which is unavailable.
    status, response, _ = await _raw(configured, method, path, body, owner)
    assert (status, response["error"]["code"]) == (503, "PLACES_UNAVAILABLE")
    assert seams.manager.calls == 1
    assert seams.generic == []


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        _place(lat=90.00001),
        _place(lon=-180.5),
        _place(lat=12.345678),
        _place(lat=True),
        _place(lat="12.3"),
        _place(lat=float("nan")),
        _place(radius_m=49),
        _place(radius_m=2001),
        _place(radius_m=150.0),
        _place(kind="gym"),
        _place(label="   "),
        _place(ssids=[_SSID_SENTINEL, _SSID_SENTINEL]),
        _place(ssids=["s" * 33]),
        _place(aliases=["Flat", "flat"]),
        _place(ha_zone="person.sentinel_7f3a"),
        {**_place(), "owner": "someone-else"},
        {key: value for key, value in _place().items() if key != "lat"},
    ],
)
async def test_malformed_place_is_one_content_blind_refusal_before_the_pool(
    seams, payload: dict
) -> None:
    """Range, precision, type, shape and duplicate errors share one fixed 422; no echo."""
    app = _mounted(seams.manager)
    body = json.dumps(payload, allow_nan=True).encode()
    status, response, _ = await _raw(app, "POST", _ROUTE, body, [])
    assert status == 422
    assert response == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "Place request is invalid.",
            "butler": None,
            "details": None,
        }
    }
    assert seams.manager.calls == 0
    assert seams.generic == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_oversized_body_stops_at_sixteen_kib(seams) -> None:
    app = _mounted(seams.manager)
    status, response, _ = await _raw(app, "POST", _ROUTE, b" " * 16_385, [])
    assert (status, response["error"]["code"]) == (413, "REQUEST_BODY_TOO_LARGE")
    assert seams.manager.calls == 0


# ---------------------------------------------------------------------------
# Real migrated PostgreSQL
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def places_db(postgres_container) -> SimpleNamespace:
    db_name = migration_db_name()
    db_url = create_migrated_test_db(postgres_container, db_name, chains=["core"])
    return SimpleNamespace(name=db_name, url=db_url)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def places_pool(places_db):
    pool = await asyncpg.create_pool(
        places_db.url, min_size=1, max_size=4, init=register_jsonb_codec
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(loop_scope="session")
async def client(places_pool, seams):
    await places_pool.execute("TRUNCATE public.owner_places")
    seams.manager.serving = places_pool
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_mounted(seams.manager)), base_url=_ORIGIN
    ) as http:
        yield http


async def _place_audits(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT metadata FROM public.audit_log WHERE action LIKE 'home_owner_place_%' ORDER BY id"
    )
    return [row["metadata"] for row in rows]


async def _tables_containing(pool: asyncpg.Pool, needle: str) -> set[str]:
    tables = await pool.fetch(
        "SELECT format('%I.%I', n.nspname, c.relname) AS name FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' "
        "AND has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(c.oid, 'SELECT')"
    )
    hits = set()
    for table in tables:
        found = await pool.fetchval(
            f"SELECT EXISTS (SELECT 1 FROM {table['name']} AS row_value "
            "WHERE strpos(row_value::text, $1) > 0)",
            needle,
        )
        if found:
            hits.add(table["name"])
    return hits


async def _as_role(connection: asyncpg.Connection, role: str, sql: str) -> str:
    """Run one statement as a runtime role; report its status or a privilege refusal."""
    try:
        async with connection.transaction():
            await connection.execute(f"SET LOCAL ROLE {role}")
            return await connection.execute(sql)
    except asyncpg.InsufficientPrivilegeError:
        return "denied"


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_editor_lifecycle_cas_and_resolution(client, places_pool, seams, caplog) -> None:
    """Create, edit, stale CAS, identical no-op, pause and soft delete, as readers see them."""
    audits_before = len(await _place_audits(places_pool))
    caplog.set_level("DEBUG")

    created = await client.post(_ROUTE, json=_place())
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    home = created.json()["data"]
    assert (home["version"], home["lat"], home["ssids"]) == (1, _LAT_SENTINEL, [_SSID_SENTINEL])
    work = (
        await client.post(
            _ROUTE, json=_place(label="Office", kind="work", ssids=[], ha_zone=None, aliases=[])
        )
    ).json()["data"]
    assert [place.label for place in await load_active_owner_places(places_pool)] == [
        "Home",
        "Office",
    ]

    listed = await client.get(_ROUTE)
    assert listed.headers["cache-control"] == "no-store"
    assert [place["label"] for place in listed.json()["data"]] == ["Home", "Office"]

    moved = _place(radius_m=300, expected_version=1)
    edited = await client.put(f"{_ROUTE}/{home['id']}", json=moved)
    assert (edited.status_code, edited.json()["data"]["version"]) == (200, 2)
    # The same edit again from the now-stale version is a no-op success.
    again = await client.put(f"{_ROUTE}/{home['id']}", json=moved)
    assert (again.status_code, again.json()["data"]["version"]) == (200, 2)
    # A changed edit from a stale version conflicts and writes nothing.
    stale = await client.put(
        f"{_ROUTE}/{home['id']}", json=_place(radius_m=400, expected_version=1)
    )
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "VERSION_CONFLICT")
    assert (
        await places_pool.fetchval(
            "SELECT radius_m FROM public.owner_places WHERE id = $1", edited.json()["data"]["id"]
        )
        == 300
    )

    paused = await client.put(
        f"{_ROUTE}/{home['id']}", json=_place(radius_m=300, paused=True, expected_version=2)
    )
    assert paused.json()["data"]["paused"] is True
    assert [place.label for place in await load_active_owner_places(places_pool)] == ["Office"]
    assert len((await client.get(_ROUTE)).json()["data"]) == 2  # paused stays editable

    wrong = await client.delete(f"{_ROUTE}/{work['id']}", params={"expected_version": 9})
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (409, "VERSION_CONFLICT")
    deleted = await client.delete(f"{_ROUTE}/{work['id']}", params={"expected_version": 1})
    assert deleted.status_code == 204
    gone = await client.put(f"{_ROUTE}/{work['id']}", json=_place(expected_version=2))
    assert gone.status_code == 404
    assert [place["label"] for place in (await client.get(_ROUTE)).json()["data"]] == ["Home"]
    assert await places_pool.fetchval("SELECT count(*) FROM public.owner_places") == 2

    # A deleted label may be reused, but under a new identity.
    reused = (await client.post(_ROUTE, json=_place(label="Office", kind="work", ssids=[]))).json()
    assert reused["data"]["id"] != work["id"]

    outcomes = [audit["outcome"] for audit in (await _place_audits(places_pool))[audits_before:]]
    assert outcomes == [
        "created",
        "created",
        "updated",
        "unchanged",
        "version_conflict",
        "updated",
        "version_conflict",
        "deleted",
        "not_found",
        "created",
    ]
    for audit in await _place_audits(places_pool):
        assert set(audit) == {"operation", "outcome", "prior_version", "new_version"}
    assert seams.generic == []
    for needle in (_SSID_SENTINEL, _ZONE_SENTINEL, str(_LAT_SENTINEL), "the flat"):
        assert await _tables_containing(places_pool, needle) == {"public.owner_places"}
        assert needle not in caplog.text


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_live_place_invariants_and_rollback(client, places_pool) -> None:
    """One home, unique labels and SSIDs among live places; a failed write changes nothing."""
    first = (await client.post(_ROUTE, json=_place())).json()["data"]
    for duplicate in (
        _place(label="Second home", ssids=[]),
        _place(label="home", kind="other", ssids=[]),
        _place(label="Cafe", kind="other"),
    ):
        refused = await client.post(_ROUTE, json=duplicate)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "PLACE_CONFLICT")
        assert _SSID_SENTINEL not in refused.text
    assert await places_pool.fetchval("SELECT count(*) FROM public.owner_places") == 1

    await places_pool.execute(
        "CREATE FUNCTION public.reject_place_audit_fixture() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic audit failure'; END $$"
    )
    await places_pool.execute(
        "CREATE TRIGGER reject_place_audit_fixture BEFORE INSERT ON public.audit_log "
        "FOR EACH ROW WHEN (NEW.action LIKE 'home_owner_place_%') "
        "EXECUTE FUNCTION public.reject_place_audit_fixture()"
    )
    try:
        failed = await client.put(
            f"{_ROUTE}/{first['id']}", json=_place(radius_m=900, expected_version=1)
        )
    finally:
        await places_pool.execute("DROP TRIGGER reject_place_audit_fixture ON public.audit_log")
        await places_pool.execute("DROP FUNCTION public.reject_place_audit_fixture()")
    assert (failed.status_code, failed.json()["error"]["code"]) == (503, "PLACES_UNAVAILABLE")
    assert "synthetic audit failure" not in failed.text
    row = await places_pool.fetchrow(
        "SELECT radius_m, version FROM public.owner_places WHERE id = $1", first["id"]
    )
    assert (row["radius_m"], row["version"]) == (150, 1)


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_only_the_two_deterministic_readers_see_places_after_bootstrap(
    client, places_pool, places_db, postgres_container
) -> None:
    """Travel and Chronicler read; other runtimes see nothing; no runtime writes."""
    migration_user = await places_pool.fetchval("SELECT session_user")
    _bootstrap_rerun(postgres_container, places_db.name, migration_user)
    await client.post(_ROUTE, json=_place())
    posture = await places_pool.fetchrow(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'public.owner_places'::regclass"
    )
    assert tuple(posture) == (True, False)

    async with places_pool.acquire() as connection:
        for role in ("butler_travel_rw", "butler_chronicler_rw"):
            async with connection.transaction():
                await connection.execute(f"SET LOCAL ROLE {role}")
                assert [p.label for p in await load_active_owner_places(connection)] == ["Home"]
        for role in ("butler_general_rw", "butler_home_rw", "butler_switchboard_rw"):
            assert await _as_role(connection, role, "SELECT * FROM public.owner_places") in {
                "SELECT 0",
                "denied",
            }
        for role in ("butler_travel_rw", "butler_chronicler_rw", "butler_general_rw"):
            insert = (
                "INSERT INTO public.owner_places (label, kind, lat, lon, radius_m) "
                "VALUES ('Intruder', 'other', 1, 1, 100)"
            )
            with pytest.raises(asyncpg.PostgresError):
                async with connection.transaction():
                    await connection.execute(f"SET LOCAL ROLE {role}")
                    await connection.execute(insert)
            assert await _as_role(
                connection, role, "UPDATE public.owner_places SET paused = true"
            ) in {"UPDATE 0", "denied"}
            assert await _as_role(connection, role, "DELETE FROM public.owner_places") in {
                "DELETE 0",
                "denied",
            }
    assert (
        await places_pool.fetchval("SELECT count(*) FROM public.owner_places WHERE NOT paused") == 1
    )
