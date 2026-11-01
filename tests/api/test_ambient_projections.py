"""Ambient projection grants and the glance payload at the real ASGI seam.

REQ-ambient-projections-001 (owner-minted grants), REQ-ambient-projections-002
(route-scoped grant boundary), REQ-ambient-projections-003 (deterministic glance),
REQ-ambient-projections-004 (consumer liveness).
The real ``create_app()`` owner-auth middleware runs; only its persistence
(owner-auth service, grant store) and the dashboard state sources are synthetic.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.briefing.cache import BriefingCache
from butlers.api.briefing.glance import compose_glance
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_butler_configs, get_db, get_mcp_manager, get_pricing
from butlers.api.projection_grants import ProjectionGrant, new_token, token_digest
from butlers.api.routers.dashboard_briefing import get_cache
from tests.api.auth_helpers import _DomainOwnerState

pytestmark = pytest.mark.unit

KEY = "synthetic-projection-owner-key"
ORIGIN = "https://owner.test.invalid"
BRIEFING = "butlers.api.routers.dashboard_briefing"
PII = ("Alice Tan", "S$4,200", "insulin")


class MemoryGrantStore:
    def __init__(self) -> None:
        self.grants: dict[uuid.UUID, ProjectionGrant] = {}
        self.fetches: list[tuple[uuid.UUID, str]] = []

    async def mint(
        self, *, label, surface_kind, audience_class, route_scope, expected_fetch_interval
    ):
        token = new_token()
        grant = ProjectionGrant(
            id=uuid.uuid4(),
            label=label,
            surface_kind=surface_kind,
            audience_class=audience_class,
            route_scope=route_scope,
            token_digest=token_digest(token),
            expected_fetch_interval=expected_fetch_interval,
            created_at=datetime.now(UTC),
        )
        self.grants[grant.id] = grant
        return grant, token

    async def list(self):
        return list(self.grants.values())

    async def get_active(self, projection_id):
        grant = self.grants.get(projection_id)
        return grant if grant is not None and grant.revoked_at is None else None

    async def revoke(self, projection_id):
        grant = await self.get_active(projection_id)
        if grant is None:
            return None
        self.grants[projection_id] = replace(grant, revoked_at=datetime.now(UTC))
        return self.grants[projection_id]

    async def record_fetch(self, projection_id, status):
        self.fetches.append((projection_id, status))
        grant = self.grants[projection_id]
        self.grants[projection_id] = replace(
            grant, last_fetch_at=datetime.now(UTC), last_fetch_status=status
        )

    async def liveness_rows(self, sql, *args):
        """Answer the briefing's liveness read from this store, as the table would."""
        if "ambient_projections" not in sql:
            return []
        return [vars(g) for g in self.grants.values() if g.revoked_at is None]


def _item(severity: str, kind: str, description: str) -> dict:
    return {"severity": severity, "type": kind, "description": description, "link": "/approvals"}


def _seed(*, items=(), failed=(), statuses=()):
    """Patch five dashboard state sources; projection liveness reads the grant store."""
    failed = set(failed)
    stack = ExitStack()
    sources = {
        "_fetch_audit_issues": ([], list(items), "audit" in failed),
        "_fetch_board_state": ([], list(statuses), "board" in failed),
        "_fetch_approvals_state": (0, "approvals" in failed),
        "_fetch_notifications_state": (0, "notifications" in failed),
        "_fetch_qa_state": (None, "qa" in failed),
    }
    for name, result in sources.items():
        stack.enter_context(patch(f"{BRIEFING}.{name}", new=AsyncMock(return_value=result)))
    llm = stack.enter_context(patch(f"{BRIEFING}.elaborate_llm", new=AsyncMock(return_value=None)))
    return stack, llm


def _harness(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_ORIGIN", ORIGIN)
    monkeypatch.setenv("DASHBOARD_AUTH_RP_ID", "owner.test.invalid")
    app = create_app(api_key=KEY)
    app.state.owner_auth_service = _DomainOwnerState(KEY)
    store = MemoryGrantStore()
    app.state.projection_grant_store = store
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"id": "owner-entity"})

    async def _fetch(sql, *args):
        return await store.liveness_rows(sql, *args)

    pool.fetch = AsyncMock(side_effect=_fetch)
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    db.credential_shared_pool.side_effect = KeyError("no shared pool")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_butler_configs] = lambda: []
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    app.dependency_overrides[get_pricing] = lambda: MagicMock()
    app.dependency_overrides[get_cache] = lambda: BriefingCache(ttl_seconds=0.001)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
    return client, store, app


@pytest.fixture
def harness(monkeypatch):
    return _harness(monkeypatch)[:2]


async def _raw_status(app, method, path, query=b"", headers=()):
    """Drive the ASGI app directly so the path reaches it unnormalised."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query,
        "headers": [(b"host", b"owner.test.invalid"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("owner.test.invalid", 443),
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


async def _mint(client, audience="owner_private"):
    resp = await client.post(
        "/api/projections",
        headers={"x-api-key": KEY},
        json={"label": "Hall display", "surface_kind": "eink", "audience_class": audience},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def test_grant_unlocks_only_its_glance_route(harness):
    client, store = harness
    minted = await _mint(client)
    path, token = minted["path"], minted["token"]
    other = await _mint(client)
    with _seed()[0]:
        assert (await client.get(path)).status_code == 401
        by_header = await client.get(path, headers={"x-projection-token": token})
        by_query = await client.get(path, params={"token": token})
        assert (by_header.status_code, by_query.status_code) == (200, 200)
        assert store.fetches == [(uuid.UUID(minted["id"]), "ok")] * 2
        # Refused everywhere else: other routes fall through to owner auth.
        for off_route in ("/api/approvals", "/api/projections", "/api/dashboard/briefing"):
            resp = await client.get(off_route, headers={"x-projection-token": token})
            assert resp.status_code == 401, off_route
        # Another projection's glance does not accept this grant and leaks nothing.
        resp = await client.get(other["path"], headers={"x-projection-token": token})
        assert resp.status_code == 404
        # A grant never reads; it is GET-only.
        resp = await client.post(path, headers={"x-projection-token": token})
        assert resp.status_code == 405


async def test_mint_list_revoke_then_grant_is_404(harness):
    client, store = harness
    minted = await _mint(client)
    assert (await client.post("/api/projections", json={})).status_code == 401
    listed = (await client.get("/api/projections", headers={"x-api-key": KEY})).json()["data"]
    assert [row["id"] for row in listed] == [minted["id"]]
    assert "token" not in listed[0] and "token_digest" not in listed[0]
    assert store.grants[uuid.UUID(minted["id"])].token_digest == token_digest(minted["token"])
    revoke = await client.delete(f"/api/projections/{minted['id']}", headers={"x-api-key": KEY})
    assert revoke.status_code == 200 and revoke.json()["data"]["revoked_at"]
    with _seed()[0]:
        resp = await client.get(minted["path"], headers={"x-projection-token": minted["token"]})
    assert resp.status_code == 404
    again = await client.delete(f"/api/projections/{minted['id']}", headers={"x-api-key": KEY})
    assert again.status_code == 404


async def test_failed_source_with_no_items_is_degraded_and_expires(harness):
    client, _ = harness
    minted = await _mint(client)
    stack, _llm = _seed(failed={"approvals"})
    with stack:
        resp = await client.get(minted["path"], headers={"x-projection-token": minted["token"]})
    glance = resp.json()["data"]
    assert glance["state_class"] == "degraded"
    assert glance["coverage"]["failed"] == ["approvals"]
    assert "approvals" not in glance["coverage"]["ok"]
    as_of = datetime.fromisoformat(glance["as_of"])
    assert datetime.fromisoformat(glance["valid_until"]) == as_of + timedelta(minutes=10)
    assert len(glance["line"]) <= 48


async def test_household_glance_carries_no_names_health_or_amounts(harness):
    client, _ = harness
    items = [_item("high", "approval", f"Pay {PII[0]} {PII[1]} for {PII[2]}")]
    household = await _mint(client, audience="household")
    private = await _mint(client, audience="owner_private")
    with _seed(items=items)[0]:
        house = await client.get(
            household["path"], headers={"x-projection-token": household["token"]}
        )
        owner = await client.get(private["path"], headers={"x-projection-token": private["token"]})
    assert house.json()["data"]["top_item"] == {
        "kind": "approval",
        "label": "An approval is waiting",
        "door_url": None,
    }
    assert not [pii for pii in PII if pii in house.text]
    # Positive control: the same seed does reach an owner-private surface.
    assert all(pii in owner.text for pii in PII)


@pytest.mark.parametrize(
    ("items", "failed", "expected"),
    [
        ([_item("high", "runtime", "x is offline")], (), "urgent"),
        ([_item("medium", "approval", "1 pending approval")], ("qa",), "mild"),
        ([], ("board",), "degraded"),
        ([], (), "quiet"),
    ],
)
async def test_glance_matches_briefing_class_without_a_model_call(harness, items, failed, expected):
    client, _ = harness
    minted = await _mint(client)
    stack, llm = _seed(items=items, failed=failed)
    with stack:
        glance = await client.get(minted["path"], headers={"x-projection-token": minted["token"]})
        assert llm.await_count == 0
        briefing = await client.get("/api/dashboard/briefing", headers={"x-api-key": KEY})
        # Positive control: the spy is wired, since the briefing does elaborate.
        assert llm.await_count == (expected != "degraded")
    assert glance.json()["data"]["state_class"] == expected
    assert briefing.json()["data"]["state_class"] == expected


async def test_lapsed_surface_opens_one_clause_until_its_next_fetch(harness):
    client, store = harness
    lapsed, fresh = await _mint(client), await _mint(client)
    lapsed_id = uuid.UUID(lapsed["id"])
    # Minted 11 minutes ago with a 5-minute interval: past its own valid_until.
    store.grants[lapsed_id] = replace(
        store.grants[lapsed_id], created_at=datetime.now(UTC) - timedelta(minutes=11)
    )
    preview = {"x-api-key": KEY}  # an owner preview never stamps a fetch
    with _seed()[0]:
        before = (await client.get(fresh["path"], headers=preview)).json()["data"]
        again = (await client.get(fresh["path"], headers=preview)).json()["data"]
        briefing = await client.get("/api/dashboard/briefing", headers=preview)
        # The lapsed surface's own fetch does not report itself, and it stamps.
        own = await client.get(lapsed["path"], headers={"x-projection-token": lapsed["token"]})
        after = (await client.get(fresh["path"], headers=preview)).json()["data"]
    assert (before["needs_you"], again["needs_you"]) == (1, 1)
    assert before["top_item"]["kind"] == "projection"
    assert before["top_item"]["label"].startswith("Hall display has not fetched")
    assert (before["state_class"], briefing.json()["data"]["state_class"]) == ("mild", "mild")
    assert own.json()["data"]["needs_you"] == 0
    assert (after["needs_you"], after["state_class"]) == (0, "quiet")


async def test_unreadable_projection_store_is_a_failed_source(harness):
    client, store = harness
    minted = await _mint(client)
    store.liveness_rows = AsyncMock(side_effect=ConnectionError("down"))
    with _seed()[0]:
        resp = await client.get(minted["path"], headers={"x-api-key": KEY})
    assert resp.json()["data"]["coverage"]["failed"] == ["projections"]
    assert resp.json()["data"]["state_class"] == "degraded"


def test_classifier_failure_composes_degraded_not_quiet():
    with patch("butlers.api.briefing.glance.classify", side_effect=RuntimeError("drift")):
        glance = compose_glance(
            {}, audience_class="household", as_of=datetime.now(UTC), refresh=timedelta(minutes=5)
        )
    assert glance["state_class"] == "degraded"


@pytest.mark.parametrize(
    ("method", "path", "query", "header", "expected"),
    [
        pytest.param("GET", "{path}", b"", b"wrong-token", 404, id="wrong-token-own-projection"),
        pytest.param("GET", "{path}/", b"token={token}", None, 401, id="trailing-slash"),
        pytest.param("GET", "{upper}", b"", b"{token}", 401, id="uppercase-uuid"),
        pytest.param(
            "GET",
            "/api/projections/../projections/{id}/glance",
            b"token={token}",
            None,
            401,
            id="dot-segment",
        ),
        pytest.param("HEAD", "{path}", b"", b"{token}", 405, id="head"),
        pytest.param("OPTIONS", "{path}", b"", b"{token}", 405, id="options"),
        pytest.param("GET", "{path}", b"token={token}&token={token}", None, 400, id="dup-query"),
        pytest.param("GET", "{path}", b"token={token}", b"{token}", 400, id="header-and-query"),
        pytest.param("GET", "{path}", b"token=", None, 400, id="empty-token"),
    ],
)
async def test_grant_boundary_at_the_raw_asgi_seam(
    monkeypatch, method, path, query, header, expected
):
    client, store, app = _harness(monkeypatch)
    minted = await _mint(client)
    fill = {
        "path": minted["path"],
        "upper": minted["path"]
        .upper()
        .replace("/API/PROJECTIONS/", "/api/projections/")
        .replace("/GLANCE", "/glance"),
        "id": minted["id"],
        "token": minted["token"],
    }
    headers = (
        ()
        if header is None
        else ((b"x-projection-token", header.decode().format(**fill).encode()),)
    )
    with _seed()[0]:
        status = await _raw_status(
            app, method, path.format(**fill), query.decode().format(**fill).encode(), headers
        )
    assert status == expected
    # Only an exact, well-formed GET under a matching grant ever reaches the glance.
    assert store.fetches == []


async def test_projection_tokens_never_reach_traces_on_any_path(monkeypatch):
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.metrics import NoOpMeterProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrument = FastAPIInstrumentor.instrument_app
    monkeypatch.setattr(
        FastAPIInstrumentor,
        "instrument_app",
        staticmethod(
            lambda app, **kw: instrument(
                app, tracer_provider=provider, meter_provider=NoOpMeterProvider(), **kw
            )
        ),
    )
    monkeypatch.setattr("butlers.core.telemetry.init_telemetry", lambda *args: None)
    monkeypatch.setattr("butlers.core.metrics.init_metrics", lambda *args: None)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", "http/dup")
    client, _store, app = _harness(monkeypatch)
    try:
        minted = await _mint(client)
        token, path = minted["token"], minted["path"]
        with _seed()[0]:
            typo = path.replace("/projections/", "/projection/")
            for url in (path, f"{path}/", f"{path}//", typo, "/api/approvals"):
                await client.get(url, params={"token": token, "view": "kept"})
        spans = exporter.get_finished_spans()
        retained = repr([(span.name, dict(span.attributes or {})) for span in spans])
        assert token not in retained
        # Glance-family spellings are not traced at all; any other path is traced
        # with the credential scrubbed from both URL attributes (positive control).
        urls = [(span.attributes or {}).get("http.url") for span in spans]
        assert not [url for url in urls if url and "/projections/" in url]
        scrubbed = "token=REDACTED&view=kept"
        assert sorted(url.split("/api/")[1] for url in urls if url and url.endswith(scrubbed)) == [
            f"approvals?{scrubbed}",
            f"projection/{minted['id']}/glance?{scrubbed}",
        ]
        assert sum((span.attributes or {}).get("url.query") == scrubbed for span in spans) == 2
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        provider.shutdown()
