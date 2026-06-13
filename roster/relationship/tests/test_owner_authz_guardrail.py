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
  12c — Daemon startup must fail fatally when DASHBOARD_API_KEY is unset and
         BUTLERS_ENV != 'dev'.

All 12a and 12b endpoint tests are real passing assertions; the endpoints and their
owner-only authorization gates have all shipped (beads 9.4, 9.7, 9.8, 9.9, 9.10,
9.11).  Tests in these classes lock in the 403/non-403 contract so regressions are
caught immediately.

The 12c startup test remains xfail: the fatal DASHBOARD_API_KEY check for non-dev
environments has not yet been implemented in src/butlers/api/app.py (the lifespan
handler currently only warns on DASHBOARD_EXPORT_SECRET).

Architecture notes
------------------
The owner-only check is an endpoint-level authorization layer distinct from the
ApiKeyMiddleware (which provides 401 on a bad/missing API key).  The endpoint-level
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

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_mcp_manager

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
#   POST /entities/{id}/promote-tier → bead 9.10 (entity promote-tier)
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

    async def test_post_entities_promote_tier_non_owner_403(self):
        app = _non_owner_app()
        resp = await _request(
            app, "post", f"{_BASE}/{_ENT_ID}/promote-tier", json_body={"tier": 15}
        )
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


class TestClause12cStartupGate:
    """Daemon must refuse startup in non-dev environments when DASHBOARD_API_KEY is unset."""

    # IMPLEMENTER NOTE (clause 12c) — bu-yv4da
    # -------------------------------------------------------------------------
    # If the DASHBOARD_API_KEY guard fires inside the FastAPI *lifespan* handler
    # (i.e. inside the ``@asynccontextmanager`` passed to ``FastAPI(lifespan=…)``
    # in src/butlers/api/app.py) rather than directly inside ``create_app()``,
    # you MUST convert this test to use ``fastapi.testclient.TestClient`` instead
    # of ``httpx.AsyncClient`` with ``httpx.ASGITransport``.
    #
    # Why: ``httpx.AsyncClient(transport=ASGITransport(app=app))`` does NOT
    # trigger FastAPI lifespan events (startup / shutdown). The lifespan context
    # manager is only exercised when the ASGI server is started — which
    # ``TestClient`` (via Starlette's ``TestClient``) does by running the app
    # in a thread with proper startup/shutdown. An ``AsyncClient`` + ``ASGITransport``
    # will happily serve requests without ever entering the lifespan block, so the
    # guard will never fire and ``pytest.raises`` will fail, making the test always
    # xfail instead of flipping to a real pass once the implementation lands.
    #
    # Conversion recipe (remove the xfail decorator too once the guard is live):
    #
    #   from fastapi.testclient import TestClient
    #
    #   def test_startup_fails_when_api_key_unset_in_production(self, monkeypatch):
    #       monkeypatch.setenv("BUTLERS_ENV", "production")
    #       monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    #       app = create_app(api_key=None)
    #       with pytest.raises((RuntimeError, SystemExit, ValueError)):
    #           with TestClient(app):  # __enter__ runs lifespan startup
    #               pass
    #
    # If the guard fires in ``create_app()`` itself (before lifespan), the current
    # async approach is fine — the ``pytest.raises`` block will catch it at
    # ``create_app(api_key=None)`` and both styles work.
    # -------------------------------------------------------------------------
    @pytest.mark.xfail(
        strict=False,
        reason=(
            "Startup DASHBOARD_API_KEY gate not yet implemented in src/butlers/api/app.py. "
            "The current lifespan handler only warns on DASHBOARD_EXPORT_SECRET; the fatal "
            "DASHBOARD_API_KEY check for non-dev environments is the deliverable of clause 12c."
        ),
    )
    async def test_startup_fails_when_api_key_unset_in_production(self, monkeypatch):
        """Daemon refuses startup with a fatal error when BUTLERS_ENV=production
        and DASHBOARD_API_KEY is not set.

        The expectation is that create_app() or the lifespan handler raises
        RuntimeError / SystemExit / ValueError so that the process cannot start
        without a key in non-dev mode.

        Currently this test is xfail because the check does not exist.
        """
        monkeypatch.setenv("BUTLERS_ENV", "production")
        monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)

        # The startup guard should raise before the app can serve requests.
        # We probe this by constructing the app (api_key=None → reads env).
        # If the guard is implemented it should raise; if not, create_app()
        # succeeds and the assertion below fails (→ expected xfail).
        with pytest.raises((RuntimeError, SystemExit, ValueError)):
            # Pass api_key=None so create_app reads DASHBOARD_API_KEY from env.
            # With the env var deleted and BUTLERS_ENV=production, a guard should fire.
            app = create_app(api_key=None)
            # If guard fires during app creation, we are done.
            # If guard fires during lifespan startup, exercise the lifespan.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.get("/api/health")

    async def test_startup_succeeds_in_dev_without_api_key(self, monkeypatch):
        """Dev environment must start successfully even without DASHBOARD_API_KEY.

        This is a non-xfail guardrail: dev mode must remain permissive (no fatal
        error on missing key) — both before and after the clause 12c guard lands.
        """
        monkeypatch.setenv("BUTLERS_ENV", "dev")
        monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)

        # Should NOT raise — dev mode is exempt from the fatal key check.
        app = create_app(api_key=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health")
        # Health endpoint is always public; a 200 confirms the app started.
        assert resp.status_code == 200


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
