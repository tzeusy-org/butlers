"""Owner-only authorization guardrail tests — Amendment 12 (clauses 12a, 12b, 12c).

Source: openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md
        Requirement: Owner-only authorization for entity endpoints (§ Amendment 12)
Task:   tasks.md §12.8

Three-part spec:
  12a — Mutation endpoints (POST/DELETE) under /api/relationship/entities/*
         must return HTTP 403 + { "code": "owner_required" } when the caller is not
         resolved to an owner-role entity.
  12b — PII-bearing GET endpoints under /api/relationship/entities/* must
         apply the same owner-only gate.
  12c — After successor cutover, keyless deployments keep public health
         reachable while private data remains behind the central owner boundary.

All 12a and 12b endpoint tests are real passing assertions; the endpoints and their
owner-only authorization gates have all shipped (beads 9.4, 9.7, 9.8, 9.9, 9.10,
9.11).  Tests in these classes lock in the 403/non-403 contract so regressions are
caught immediately.

The old missing-key fatal-startup expectation was superseded by adopted owner-auth
commit3686954. Mounted unavailable-state denial is checked for production and dev;
real auth persistence/lifecycle evidence lives in the dedicated owner-auth tests.

Architecture notes
------------------
The owner-only check is an endpoint-level authorization layer distinct from the
central owner boundary (which authenticates before domain authorization).  The endpoint-level
owner check calls ``_get_owner_roles(pool)`` which fetches the first entity whose
``roles`` column contains ``'owner'`` and returns the roles list.  Access is denied
when the list is ``None`` (DB error) or does not include ``'owner'``.

The pattern used here is ``_get_owner_roles`` + roles inspection via the
``_assert_owner_role`` helper, which returns a 403 JSONResponse for non-owner
callers.  All owner-gated endpoints in this router use that single helper; the
older row-existence-only helper has been removed (bu-prdr0).

The tests use httpx.AsyncClient with a mocked DB pool (same approach as
test_entity_tabs.py and test_chronicler_boundary.py) so no real Postgres or
Docker is required.

Non-owner simulation
--------------------
To simulate a non-owner caller we configure a mock DB pool whose ``fetchrow``
returns a row with ``roles=[]``.  The ``_get_owner_roles`` helper returns that
list, and ``"owner" not in []`` triggers the 403 response.

To simulate an owner caller we set ``roles=["owner"]``.  The helper returns
``["owner"]``, the check passes, and the endpoint proceeds normally (returning
whatever the mocked pool produces for subsequent queries).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app as create_guarded_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_mcp_manager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENT_ID = uuid4()

# Base path for all relationship entity endpoints (as mounted by the router)
_BASE = "/api/relationship/entities"


def _make_app_with_caller(*, caller_is_owner: bool) -> FastAPI:
    """Return a FastAPI app whose mock DB simulates a non-owner or owner caller.

    The mock pool is wired so that:
    - ``fetchval`` returns 1 (entity exists) unconditionally.
    - ``fetchrow`` returns a row with roles=['owner'] or roles=[] depending on
      ``caller_is_owner``.

    When the endpoint implementation lands, it will call the pool to resolve
    the API-key-authenticated caller to an entity and check its roles. Until
    then the pool responses are irrelevant (the endpoints are 404), but the
    fixture is set up correctly so tests pass once the endpoints exist.
    """
    caller_roles = ["owner"] if caller_is_owner else []

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetchrow = AsyncMock(
        return_value={"id": _ENT_ID, "roles": caller_roles, "canonical_name": "Test"}
    )
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.execute = AsyncMock(return_value="DELETE 0")

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app(api_key="test-key")

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    # The activity endpoint uses Depends(get_mcp_manager). Provide a no-op mock
    # so the dependency resolves without RuntimeError — the owner check runs first
    # in the handler body, so MCP is never actually called on the 403 path.
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()

    return app


def _owner_app() -> FastAPI:
    return _make_app_with_caller(caller_is_owner=True)


def _non_owner_app() -> FastAPI:
    return _make_app_with_caller(caller_is_owner=False)


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    json_body: dict | None = None,
) -> httpx.Response:
    """Send an authenticated request to *path* on *app*."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        kwargs: dict = {"headers": {"X-API-Key": "test-key"}}
        if json_body is not None:
            kwargs["json"] = json_body
        return await getattr(client, method)(path, **kwargs)


def _assert_owner_required(resp: httpx.Response) -> None:
    """Assert HTTP 403 with code='owner_required' in the response body."""
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}. Body: {resp.text}"
    body = resp.json()
    # The spec mandates the code discriminator is present (envelope or unwrapped).
    # Accept either {"code": "owner_required"} or {"error": {"code": "owner_required"}}.
    code = body.get("code") or (body.get("error") or {}).get("code")
    assert code == "owner_required", (
        f"Expected code='owner_required', got code={code!r}. Body: {body}"
    )


# ---------------------------------------------------------------------------
# Clause 12a — Mutation endpoints (POST / DELETE)
#
# All endpoints shipped (beads 9.7, 9.8, 9.9, 9.10, 9.11):
#   POST /entities               → bead 9.7 (entity create)
#   POST /entities/{id}/merge    → bead 9.8 (entity merge)
#   POST /entities/{id}/archive  → bead 9.9 (entity archive)
#   DELETE /entities/{id}        → bead 9.7 (entity delete / forget)
#   POST /entities/queue/dismiss → bead 9.11 (queue dismiss)
#   POST /entities/{id}/contacts → bead 9.7 (entity contacts write)
#   DELETE /entities/{id}/contacts/{pred}/{valueHash} → bead 9.7
# ---------------------------------------------------------------------------


class TestClause12aMutationNonOwner:
    """Non-owner callers MUST receive HTTP 403 + owner_required on all mutation endpoints."""

    async def test_post_entities_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(app, "post", f"{_BASE}", json_body={"canonical_name": "Test"})
        _assert_owner_required(resp)

    async def test_post_entities_merge_non_owner_403(self):
        app = _non_owner_app()
        other = uuid4()
        resp = await _request(
            app,
            "post",
            f"{_BASE}/{_ENT_ID}/merge",
            json_body={"entityA": str(_ENT_ID), "entityB": str(other), "keepAs": "A"},
        )
        _assert_owner_required(resp)

    async def test_post_entities_archive_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(app, "post", f"{_BASE}/{_ENT_ID}/archive", json_body={})
        _assert_owner_required(resp)

    async def test_delete_entity_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(app, "delete", f"{_BASE}/{_ENT_ID}")
        _assert_owner_required(resp)

    async def test_post_queue_dismiss_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(
            app, "post", f"{_BASE}/queue/dismiss", json_body={"entity_id": str(_ENT_ID)}
        )
        _assert_owner_required(resp)

    async def test_post_entity_contacts_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(
            app,
            "post",
            f"{_BASE}/{_ENT_ID}/contacts",
            json_body={"predicate": "has-email", "value": "test@example.com"},
        )
        _assert_owner_required(resp)

    async def test_delete_entity_contact_fact_non_owner_403(self):
        app = _non_owner_app()
        fake_hash = "abc123"
        resp = await _request(app, "delete", f"{_BASE}/{_ENT_ID}/contacts/has-email/{fake_hash}")
        _assert_owner_required(resp)


class TestClause12aMutationOwner:
    """Owner callers MUST NOT be rejected by the owner_required gate.

    These tests verify the gate does not block legitimate owner requests.
    The assertion is relaxed: we accept any status code other than 403 with
    owner_required (e.g. 404 from a missing entity in the mock, or 200/201
    on a successful mutation).
    """

    async def test_post_entities_owner_not_rejected(self):
        app = _owner_app()
        resp = await _request(app, "post", f"{_BASE}", json_body={"canonical_name": "Test"})
        # Owner should NOT receive 403 owner_required — any other code is acceptable.
        if resp.status_code == 403:
            body = resp.json()
            code = body.get("code") or (body.get("error") or {}).get("code")
            assert code != "owner_required", "Owner caller was incorrectly rejected."

    async def test_delete_entity_owner_not_rejected(self):
        app = _owner_app()
        resp = await _request(app, "delete", f"{_BASE}/{_ENT_ID}")
        if resp.status_code == 403:
            body = resp.json()
            code = body.get("code") or (body.get("error") or {}).get("code")
            assert code != "owner_required", "Owner caller was incorrectly rejected."


# ---------------------------------------------------------------------------
# Clause 12b — PII-bearing GET endpoints
#
# All endpoints shipped (beads 9.7, 9.11, 9.12, 9.13):
#   GET /entities/queue            → bead 9.11 (curation queue)
#   GET /entities/search           → bead 9.12 (finder / cmd-K)
#   GET /entities/{id}/contacts    → bead 9.7 (entity contacts read)
#   GET /entities/{id}/neighbours  → bead 9.7 (entity neighbours)
#   GET /entities/{id}/activity    → bead 9.13 (activity aggregator)
#   GET /entities/{id}/facts       → facts drill / identity-staleness list
#                                    (migrated onto _assert_owner_role, bu-prdr0)
# ---------------------------------------------------------------------------


class TestClause12bPiiReadsNonOwner:
    """Non-owner callers MUST receive HTTP 403 + owner_required on PII-bearing GET endpoints."""

    async def test_get_queue_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(app, "get", f"{_BASE}/queue")
        _assert_owner_required(resp)

    async def test_get_search_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(app, "get", f"{_BASE}/search?q=alice")
        _assert_owner_required(resp)

    async def test_get_entity_contacts_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(app, "get", f"{_BASE}/{_ENT_ID}/contacts")
        _assert_owner_required(resp)

    async def test_get_entity_neighbours_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(app, "get", f"{_BASE}/{_ENT_ID}/neighbours")
        _assert_owner_required(resp)

    async def test_get_entity_activity_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(app, "get", f"{_BASE}/{_ENT_ID}/activity")
        _assert_owner_required(resp)

    async def test_get_entity_facts_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(app, "get", f"{_BASE}/{_ENT_ID}/facts")
        _assert_owner_required(resp)


class TestClause12bPiiReadsOwner:
    """Owner callers MUST NOT be rejected by the owner_required gate on PII GET endpoints."""

    async def test_get_queue_owner_not_rejected(self):
        app = _owner_app()
        resp = await _request(app, "get", f"{_BASE}/queue")
        if resp.status_code == 403:
            body = resp.json()
            code = body.get("code") or (body.get("error") or {}).get("code")
            assert code != "owner_required", "Owner caller was incorrectly rejected."

    async def test_get_entity_contacts_owner_not_rejected(self):
        app = _owner_app()
        resp = await _request(app, "get", f"{_BASE}/{_ENT_ID}/contacts")
        if resp.status_code == 403:
            body = resp.json()
            code = body.get("code") or (body.get("error") or {}).get("code")
            assert code != "owner_required", "Owner caller was incorrectly rejected."

    async def test_get_entity_facts_owner_not_rejected(self):
        app = _owner_app()
        resp = await _request(app, "get", f"{_BASE}/{_ENT_ID}/facts")
        if resp.status_code == 403:
            body = resp.json()
            code = body.get("code") or (body.get("error") or {}).get("code")
            assert code != "owner_required", "Owner caller was incorrectly rejected."


# ---------------------------------------------------------------------------
# Clause 12c — Startup gate: DASHBOARD_API_KEY must be set in non-dev envs
#
# The spec requires that daemon startup fails fatally when BUTLERS_ENV != 'dev'
# and DASHBOARD_API_KEY is unset. This is tested by verifying that create_app()
# or the lifespan startup raises a RuntimeError (or SystemExit) in that case.
#
# Awaiting: implementation in src/butlers/api/app.py (lifespan handler or
# create_app factory) — the guardrail is not yet present. When it lands the
# xfail will flip to a real pass.
# ---------------------------------------------------------------------------


class TestClause12cStartupGuardrail:
    """The adopted successor protects keyless deployments without a fatal-key gate."""

    @pytest.mark.parametrize("environment", ["production", "dev"])
    async def test_keyless_unavailable_state_protects_data_but_not_health(
        self, monkeypatch, environment
    ):
        # This is a mounted admission test, not an actual production lifespan run.
        # Spec: REQ-dashboard-owner-auth-004/005 and dashboard-relationship Clause12c.
        monkeypatch.setenv("BUTLERS_ENV", environment)
        app = create_guarded_app(api_key="")
        app.state.ready = True
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            health = await client.get("/api/health")
            protected = await client.get("/api/relationship/entities")
        assert health.status_code == 200
        assert protected.status_code == 503
        assert protected.json()["error"]["code"] == "AUTH_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Scope-exclusion assertions (endpoints NOT in the owner-only gate)
#
# The spec explicitly exempts:
#   - GET /entities                      (list; no raw contact-fact objects)
#   - GET /entities/{id}/notes, interactions, gifts, loans, timeline
#
# These endpoints already exist in the router and must remain accessible
# without the owner check. We verify they do NOT return 403 owner_required.
# ---------------------------------------------------------------------------


class TestOutOfScopeEndpointsNotBlocked:
    """Endpoints outside the 12a/12b gate must not receive owner_required rejections."""

    async def test_get_entity_detail_not_owner_gated(self):
        """GET /entities/{id} is not in the owner-only gate per the spec."""
        app = _non_owner_app()
        resp = await _request(app, "get", f"{_BASE}/{_ENT_ID}")
        # The endpoint exists; it may return 404 (entity not found in mock) or 200.
        # It MUST NOT return 403 owner_required.
        assert resp.status_code != 403 or (
            resp.json().get("code") != "owner_required"
            and (resp.json().get("error") or {}).get("code") != "owner_required"
        ), "GET /entities/{id} must not be gated by owner_required."

    @pytest.mark.parametrize(
        "tab",
        ["notes", "interactions", "gifts", "loans", "timeline"],
    )
    async def test_entity_tab_endpoints_not_owner_gated(self, tab: str):
        """Entity tab endpoints (notes/interactions/gifts/loans/timeline) must not be
        owner-gated per the spec exclusion clause."""
        app = _non_owner_app()
        resp = await _request(app, "get", f"{_BASE}/{_ENT_ID}/{tab}")
        # These endpoints exist; any code other than 403 owner_required is acceptable.
        # (404 is possible if entity not in mock; 200 with [] is also fine.)
        assert resp.status_code != 403 or (
            resp.json().get("code") != "owner_required"
            and (resp.json().get("error") or {}).get("code") != "owner_required"
        ), f"GET /entities/{{id}}/{tab} must not be gated by owner_required."


# Additive survivor candidate. All original owners above stay until the complete
# REQ-testing-053 executing account admits each owner separately.
# This domain fixture retains production middleware; it is not real-role/auth
# persistence evidence. Authority-backed identity routes retain their own tests.
_ENTITY_ROUTE_POLICY = {
    ("GET", "/api/relationship/entities/search"): ("search_entities", "owner"),
    ("GET", "/api/relationship/entities"): ("list_entities", "authenticated-domain"),
    ("POST", "/api/relationship/entities"): ("promote_entity", "owner"),
    ("GET", "/api/relationship/entities/queue"): ("get_entities_queue", "owner"),
    ("POST", "/api/relationship/entities/queue/dismiss"): ("dismiss_queue_entity", "owner"),
    ("GET", "/api/relationship/entities/concentration"): ("get_entities_concentration", "owner"),
    ("GET", "/api/relationship/entities/{entity_id}"): ("get_entity", "authenticated-domain"),
    ("POST", "/api/relationship/entities/{entity_id}/info"): (
        "create_entity_info",
        "authenticated-domain",
    ),
    ("PATCH", "/api/relationship/entities/{entity_id}/info/{info_id}"): (
        "patch_entity_info",
        "authenticated-domain",
    ),
    ("DELETE", "/api/relationship/entities/{entity_id}/info/{info_id}"): (
        "delete_entity_info",
        "authenticated-domain",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/secrets/{info_id}"): (
        "reveal_entity_secret",
        "owner",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/notes"): (
        "list_entity_notes",
        "authenticated-domain",
    ),
    ("POST", "/api/relationship/entities/{entity_id}/notes"): ("create_entity_note", "owner"),
    ("GET", "/api/relationship/entities/{entity_id}/interactions"): (
        "list_entity_interactions",
        "authenticated-domain",
    ),
    ("POST", "/api/relationship/entities/{entity_id}/interactions"): (
        "create_entity_interaction",
        "owner",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/gifts"): (
        "list_entity_gifts",
        "authenticated-domain",
    ),
    ("POST", "/api/relationship/entities/{entity_id}/gifts"): ("create_entity_gift", "owner"),
    ("GET", "/api/relationship/entities/{entity_id}/loans"): (
        "list_entity_loans",
        "authenticated-domain",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/timeline"): (
        "list_entity_timeline",
        "authenticated-domain",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/cadence"): (
        "get_entity_cadence",
        "authenticated-domain",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/linked-contacts"): (
        "list_entity_linked_contacts",
        "authenticated-domain",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/message-threads"): (
        "list_entity_message_threads",
        "authenticated-domain",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/dates"): (
        "list_entity_important_dates",
        "authenticated-domain",
    ),
    ("PATCH", "/api/relationship/entities/{entity_id}/dunbar-tier"): (
        "patch_entity_dunbar_tier",
        "authenticated-domain",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/neighbours"): (
        "list_entity_neighbours",
        "owner",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/contacts"): ("list_entity_contacts", "owner"),
    ("POST", "/api/relationship/entities/{entity_id}/contacts"): ("add_entity_contact", "owner"),
    ("DELETE", "/api/relationship/entities/{entity_id}/contacts/{predicate}/{value_hash}"): (
        "delete_entity_contact",
        "owner",
    ),
    ("POST", "/api/relationship/entities/{entity_id}/contacts/{predicate}/{value_hash}/verify"): (
        "verify_entity_contact",
        "owner",
    ),
    ("PUT", "/api/relationship/entities/{entity_id}/contacts/{predicate}/{value_hash}"): (
        "update_entity_contact",
        "owner",
    ),
    ("PUT", "/api/relationship/entities/{entity_id}/preferred-channel"): (
        "set_entity_preferred_channel",
        "owner",
    ),
    ("DELETE", "/api/relationship/entities/{entity_id}/preferred-channel"): (
        "clear_entity_preferred_channel",
        "owner",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/facts"): ("list_entity_facts", "owner"),
    ("POST", "/api/relationship/entities/{entity_id}/archive"): ("archive_entity", "owner"),
    ("DELETE", "/api/relationship/entities/{entity_id}"): ("forget_entity", "owner"),
    ("POST", "/api/relationship/entities/{entity_id}/merge"): ("merge_entities", "owner"),
    ("POST", "/api/relationship/entities/compare"): ("compare_entities", "owner"),
    ("POST", "/api/relationship/entities/dismiss-pair"): ("dismiss_pair", "owner"),
    ("GET", "/api/relationship/entities/{entity_id}/activity"): ("get_entity_activity", "owner"),
    ("POST", "/api/relationship/entities/{entity_id}/view-mark"): ("mark_entity_view", "owner"),
    ("GET", "/api/relationship/entities/{entity_id}/delta-facts"): (
        "get_entity_delta_facts",
        "owner",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/core-dates"): (
        "get_entity_core_dates",
        "owner",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/identity-candidates"): (
        "list_identity_candidates",
        "fact-authority",
    ),
    ("POST", "/api/relationship/entities/{entity_id}/identity-facts/{fact_id}/{decision}"): (
        "owner_identity_fact_decision",
        "fact-authority",
    ),
    ("GET", "/api/relationship/entities/{entity_id}/identity-facts/{fact_id}/decision"): (
        "read_identity_fact_decision",
        "fact-authority",
    ),
}


@pytest.fixture(scope="module")
def shared_relationship_domain_app():
    """One production-built domain app for the additive route-table species."""
    return create_app(api_key="test-key")


def _relationship_module(app):
    modules = [module for name, module in app.state.butler_routers if name == "relationship"]
    assert len(modules) == 1
    return modules[0]


def _mounted_entity_routes(app):
    """Follow actual FastAPI lazy includes, retaining complete endpoint identity."""
    found = {}

    def visit(router, prefix=""):
        for route in router.routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                visit(nested, prefix + route.include_context.prefix)
                continue
            path = prefix + getattr(route, "path", "")
            if path == _BASE or path.startswith(_BASE + "/"):
                for method in getattr(route, "methods", ()):
                    key = (method, path)
                    assert key not in found, f"duplicate mounted route: {key}"
                    found[key] = route.endpoint

    visit(app.router)
    return found


def _entity_table_body(endpoint):
    """Known valid domain payloads; validation must not stand in for denial."""
    a = "00000000-0000-4000-8000-000000000001"
    b = "00000000-0000-4000-8000-000000000002"
    return {
        "promote_entity": {"canonical_name": "Synthetic table entity"},
        "dismiss_queue_entity": {"entity_id": a},
        "create_entity_note": {"content": "Synthetic table note"},
        "create_entity_interaction": {"type": "call"},
        "create_entity_gift": {"description": "Synthetic table gift"},
        "add_entity_contact": {"predicate": "has-email", "value": "table@example.test"},
        "update_entity_contact": {"new_value": "updated@example.test"},
        "set_entity_preferred_channel": {"channel": "email"},
        "merge_entities": {"entityA": a, "entityB": b, "keepAs": "A"},
        "compare_entities": {"entity_a": a, "entity_b": b},
        "dismiss_pair": {"entity_a": a, "entity_b": b},
    }.get(endpoint)


def _entity_table_path(path):
    return (
        path.replace("{entity_id}", str(_ENT_ID))
        .replace("{info_id}", "00000000-0000-4000-8000-000000000003")
        .replace("{predicate}", "has-email")
        .replace("{value_hash}", "abc123")
    )


async def _run_entity_owner_case(app, method, path, endpoint, state):
    """Fresh dependency/collaborator state, finally restored on every outcome."""
    import asyncio
    from contextlib import contextmanager

    @contextmanager
    def original_app_state():
        old_state = dict(app.state._state)
        old_routers = list(app.state.butler_routers)
        old_overrides = dict(app.dependency_overrides)
        service = app.state.owner_auth_service
        old_service = dict(vars(service))
        tasks = set(asyncio.all_tasks())
        try:
            yield
        finally:
            # These cases do not start lifespan or background work. A newly live
            # Task is uncertainty, not silently cancelled/borrowed cleanup.
            live = {task for task in asyncio.all_tasks() if task not in tasks and not task.done()}
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old_overrides)
            app.state._state.clear()
            app.state._state.update(old_state)
            app.state.butler_routers[:] = old_routers
            vars(service).clear()
            vars(service).update(old_service)
            assert not live, "route table leaked live background Tasks"
            assert app.state.owner_auth_service is service
            assert app.state.butler_routers == old_routers
            assert app.dependency_overrides == old_overrides

    with original_app_state():
        pool = AsyncMock()
        pool.fetchval.return_value = 1
        pool.fetch.return_value = []
        pool.execute.return_value = "UPDATE 0"
        if state == "no-row":
            pool.fetchrow.return_value = None
        elif state == "error":
            pool.fetchrow.side_effect = RuntimeError("synthetic owner query failure")
        else:
            pool.fetchrow.return_value = {"id": _ENT_ID, "roles": [], "canonical_name": "Test"}
        db = MagicMock(spec=DatabaseManager)
        db.pool.return_value = pool
        module = _relationship_module(app)
        app.dependency_overrides[module._get_db_manager] = lambda: db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        request_path = _entity_table_path(path)
        if endpoint == "search_entities":
            request_path += "?q=synthetic"
        response = await _request(app, method.lower(), request_path, _entity_table_body(endpoint))
        _assert_owner_required(response)
        # Denial is before data/mutation collaborators, not an empty-row green.
        assert pool.fetchrow.await_count == 1
        owner_sql = pool.fetchrow.await_args.args[0]
        assert "'owner' = ANY(COALESCE(roles, '{}'))" in owner_sql
        assert "public.entities" in owner_sql
        pool.fetch.assert_not_awaited()
        pool.fetchval.assert_not_awaited()
        pool.execute.assert_not_awaited()
        pool.acquire.assert_not_called()


def test_entity_route_policy_covers_mounted_and_source_routes(shared_relationship_domain_app):
    """New, removed, remapped, duplicate or unclassified entity routes refuse."""
    import ast
    from pathlib import Path

    app = shared_relationship_domain_app
    module = _relationship_module(app)
    mounted = _mounted_entity_routes(app)
    assert set(mounted) == set(_ENTITY_ROUTE_POLICY)
    source = ast.parse(Path(module.__file__).read_text())
    declared = {}
    for function in source.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in function.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "router"
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
            ):
                continue
            path = "/api/relationship" + decorator.args[0].value
            if path == _BASE or path.startswith(_BASE + "/"):
                key = (decorator.func.attr.upper(), path)
                assert key not in declared
                declared[key] = function.name
                policy = _ENTITY_ROUTE_POLICY[key][1]
                owner_calls = [
                    call
                    for call in ast.walk(function)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "_assert_owner_role"
                ]
                assert bool(owner_calls) == (policy == "owner")
    assert set(declared) == set(mounted)
    for key, (name, policy) in _ENTITY_ROUTE_POLICY.items():
        assert policy in {"owner", "authenticated-domain", "fact-authority"}
        assert mounted[key] is getattr(module, name)
        assert declared[key] == name


@pytest.mark.parametrize("order", ["forward", "reverse", "shuffle"])
async def test_entity_owner_route_survivor_table(shared_relationship_domain_app, order):
    """Complete named owner-route cohort, independently ordered on one app."""
    import random

    rows = [
        (method, path, endpoint, state)
        for (method, path), (endpoint, policy) in _ENTITY_ROUTE_POLICY.items()
        if policy == "owner"
        for state in ("non-owner", "no-row", "error")
    ]
    assert rows
    if order == "reverse":
        rows.reverse()
    elif order == "shuffle":
        random.Random(1103).shuffle(rows)
    for method, path, endpoint, state in rows:
        await _run_entity_owner_case(shared_relationship_domain_app, method, path, endpoint, state)


async def test_entity_shared_owner_and_not_found_companions(shared_relationship_domain_app):
    """Healthy owner can execute; missing entity is 404 after its real gate."""
    app = shared_relationship_domain_app
    module = _relationship_module(app)
    old = dict(app.dependency_overrides)
    try:
        for exists in (True, False, True):
            pool = AsyncMock()
            pool.fetchrow.side_effect = [
                {"id": _ENT_ID, "roles": ["owner"]},
                {"id": _ENT_ID} if exists else None,
            ]
            pool.execute.return_value = "UPDATE 1"
            db = MagicMock(spec=DatabaseManager)
            db.pool.return_value = pool
            app.dependency_overrides[module._get_db_manager] = lambda: db
            response = await _request(app, "post", f"{_BASE}/{_ENT_ID}/archive", {})
            assert response.status_code == (204 if exists else 404)
            assert pool.fetchrow.await_count == 2
            assert pool.execute.await_count == int(exists)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)
    assert app.dependency_overrides == old
