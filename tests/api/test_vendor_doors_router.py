from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest

from butlers.api import vendor_door_store as store_mod
from butlers.api.vendor_door_store import VendorDoorStore, VendorDoorStoreUnavailable
from butlers.core import vendor_doors as flag
from tests.api.auth_helpers import create_authenticated_domain_app

DOOR = uuid.uuid4()
CREATE = {"kind": "cancel", "template": "https://shop.example.com/cancel/{order}"}


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.result = {"outcome": "ok", "id": str(DOOR), "status": "proposed", "version": 1}
        self.rows: list[dict] = []
        self.down = False

    def _out(self, name, *args, **kwargs):
        if self.down:
            raise VendorDoorStoreUnavailable
        self.calls.append((name, args, kwargs))
        return self.result

    async def create(self, **kw):
        return self._out("create", **kw)

    async def confirm(self, *a):
        return self._out("confirm", *a)

    async def revoke(self, *a):
        return self._out("revoke", *a)

    async def reject(self, *a):
        return self._out("reject", *a)

    async def list(self, **kw):
        if self.down:
            raise VendorDoorStoreUnavailable
        self.calls.append(("list", (), kw))
        return self.rows


def _row(n: int) -> dict:
    t = datetime(2026, 11, 1, 12, n, tzinfo=UTC)
    return {
        "id": uuid.uuid4(),
        "version": 1,
        "kind": "cancel",
        "host": "shop.example.com",
        "template": "https://shop.example.com/c",
        "slot_types": {},
        "status": "proposed",
        "origin": "dashboard",
        "tier": "exempt",
        "sender_verdict": None,
        "sender_domain": None,
        "vendor_entity_id": None,
        "supersedes_id": None,
        "expires_at": t,
        "confirmed_at": None,
        "revoked_at": None,
        "rejected_at": None,
        "created_at": t,
    }


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(flag, "VENDOR_DOORS_LIVE", True)
    return FakeStore()


@pytest.fixture
def door_app(fake):
    app = create_authenticated_domain_app()
    app.state.vendor_door_store = fake
    return app


@pytest.fixture
async def client(door_app):
    transport = httpx.ASGITransport(app=door_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_dark_by_default_every_route_404(door_app, fake, monkeypatch):
    monkeypatch.setattr(flag, "VENDOR_DOORS_LIVE", False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=door_app), base_url="http://t"
    ) as c:
        assert (await c.get("/api/vendor-doors")).status_code == 404
        assert (await c.post("/api/vendor-doors", json=CREATE)).status_code == 404
        for verb in ("confirm", "revoke", "reject"):
            r = await c.post(f"/api/vendor-doors/{DOOR}/{verb}", json={"expected_version": 1})
            assert r.status_code == 404
    assert fake.calls == []


async def test_non_owner_is_refused_before_any_store_call(fake, monkeypatch):
    monkeypatch.setattr(flag, "VENDOR_DOORS_LIVE", True)
    app = create_authenticated_domain_app()
    app.state.vendor_door_store = fake
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://t",
        headers={"x-api-key": "wrong"},
    ) as c:
        assert (await c.post("/api/vendor-doors", json=CREATE)).status_code in (401, 403)
    assert fake.calls == []


@pytest.mark.parametrize(
    "extra",
    [
        {"ingestion_event_id": str(uuid.uuid4())},
        {"origin": "email"},
        {"tier": "verified"},
        {"sender_verdict": "authenticated"},
        {"lineage": {}},
        {"status": "confirmed"},
    ],
)
async def test_create_cannot_carry_event_origin_tier_or_verdict(client, fake, extra):
    r = await client.post("/api/vendor-doors", json={**CREATE, **extra})
    assert r.status_code == 422
    assert fake.calls == []


async def test_create_forwards_only_owner_typed_fields(client, fake):
    r = await client.post("/api/vendor-doors", json=CREATE)
    assert r.status_code == 200
    assert r.json()["data"]["outcome"] == "ok"
    name, _, kw = fake.calls[0]
    assert name == "create"
    assert set(kw) == {"kind", "template", "slot_types", "vendor_entity_id", "supersedes"}


@pytest.mark.parametrize("verb", ["confirm", "revoke", "reject"])
async def test_transitions_pass_the_expected_version(client, fake, verb):
    r = await client.post(f"/api/vendor-doors/{DOOR}/{verb}", json={"expected_version": 3})
    assert r.status_code == 200
    name, args, _ = fake.calls[0]
    assert name == verb and args[:2] == (DOOR, 3)


async def test_confirm_records_a_session_hash_not_the_token(client, fake):
    await client.post(f"/api/vendor-doors/{DOOR}/confirm", json={"expected_version": 1})
    session = fake.calls[0][1][2]
    assert len(session) == 64 and int(session, 16) >= 0


@pytest.mark.parametrize("body", [{}, {"expected_version": 0}, {"expected_version": 1, "x": 1}])
async def test_transition_body_is_strict(client, fake, body):
    r = await client.post(f"/api/vendor-doors/{DOOR}/confirm", json=body)
    assert r.status_code == 422
    assert fake.calls == []


async def test_refusals_map_to_409_and_not_found_to_404(client, fake):
    fake.result = {"outcome": "refused", "reason": "stale_version", "id": str(DOOR)}
    r = await client.post(f"/api/vendor-doors/{DOOR}/confirm", json={"expected_version": 1})
    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"reason": "stale_version"}
    fake.result = {"outcome": "refused", "reason": "not_found"}
    r = await client.post(f"/api/vendor-doors/{DOOR}/revoke", json={"expected_version": 1})
    assert r.status_code == 404


async def test_unchanged_is_a_success(client, fake):
    fake.result = {"outcome": "unchanged", "id": str(DOOR), "status": "confirmed", "version": 2}
    r = await client.post(f"/api/vendor-doors/{DOOR}/confirm", json={"expected_version": 1})
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "unchanged"


async def test_unavailable_store_is_503_never_an_empty_list(client, fake):
    fake.down = True
    assert (await client.get("/api/vendor-doors")).status_code == 503
    assert (await client.post("/api/vendor-doors", json=CREATE)).status_code == 503


async def test_missing_store_is_503(door_app, monkeypatch):
    door_app.state.vendor_door_store = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=door_app), base_url="http://t"
    ) as c:
        assert (await c.get("/api/vendor-doors")).status_code == 503


async def test_list_pages_with_an_opaque_cursor(client, fake):
    fake.rows = [_row(3), _row(2), _row(1)]
    r = await client.get("/api/vendor-doors?limit=2")
    body = r.json()
    assert len(body["items"]) == 2 and body["next_cursor"]
    fake.rows = [_row(1)]
    r2 = await client.get(f"/api/vendor-doors?limit=2&cursor={body['next_cursor']}")
    assert r2.json()["next_cursor"] is None
    assert fake.calls[-1][2]["after"] is not None
    assert (await client.get("/api/vendor-doors?cursor=junk")).status_code == 400
    assert (await client.get("/api/vendor-doors?status=bogus")).status_code == 422


# --- the dedicated login -------------------------------------------------------------------


class _Conn:
    def __init__(self, safe):
        self.safe = safe
        self.args = None

    async def fetchval(self, sql, *args):
        self.args = args
        return self.safe


async def test_login_check_accepts_only_a_clean_capability_member():
    await store_mod._initialize_connection(_Conn(True))
    for bad in (False, None):
        with pytest.raises(RuntimeError):
            await store_mod._initialize_connection(_Conn(bad))
    conn = _Conn(True)
    await store_mod._initialize_connection(conn)
    assert conn.args == ("dashboard_vendor_doors",)


async def test_login_check_sql_rejects_reachable_foreign_roles():
    sql = store_mod._SAFE_LOGIN_SQL
    for needle in ("rolsuper", "rolcreaterole", "rolbypassrls", "datdba", "pg_has_role"):
        assert needle in sql
    assert "dashboard_auth_api" not in sql


async def test_pool_is_absent_without_its_own_credentials(monkeypatch):
    monkeypatch.delenv(store_mod.USER_ENV, raising=False)
    monkeypatch.delenv(store_mod.PASSWORD_ENV, raising=False)
    monkeypatch.setenv("DASHBOARD_AUTH_DB_USER", "x")
    monkeypatch.setenv("DASHBOARD_AUTH_DB_PASSWORD", "y")
    assert await store_mod.create_vendor_door_pool() is None


async def test_store_without_a_pool_is_unavailable():
    with pytest.raises(VendorDoorStoreUnavailable):
        await VendorDoorStore(None).confirm(DOOR, 1, "h")
