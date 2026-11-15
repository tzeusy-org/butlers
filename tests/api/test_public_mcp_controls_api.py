"""The Public MCP kill-switch owner route over an in-memory control table.

REQ-dashboard-settings-console-001 (list, owner disable, unauthenticated 401, enable) and
REQ-dashboard-settings-console-002 (the panel summary). The SQL itself, the real roles and the
table shape run against PG17 in ``tests/integration/test_public_mcp_controls.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.deps import get_db
from butlers.api.routers import public_mcp_controls as routes
from butlers.core.dashboard_stepup import verified_dashboard_session
from butlers.public_mcp_controls import CONTROL_DOMAINS, DATA_DOMAINS

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)


class _Table:
    """``mcp_surface.controls`` with its seed rows, a clock and an optional write fault."""

    def __init__(self):
        self.now = T0
        self.rows = {
            d: {"domain": d, "state": "enabled", "changed_at": T0, "changed_by": "system"}
            for d in CONTROL_DOMAINS
        }
        self.fail_writes = False
        self.fail_reads = False
        self.writes = 0

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetch(self, sql, domains):
        if self.fail_reads:
            raise RuntimeError("select statement text must not leak")
        assert "FROM mcp_surface.controls" in sql
        return [self.rows[d] for d in domains if d in self.rows]

    async def fetchrow(self, sql, domain, *args):
        if sql.lstrip().startswith("INSERT"):
            state, actor = args
            self.writes += 1
            if self.fail_writes:
                raise RuntimeError("insert statement text must not leak")
            current = self.rows.get(domain)
            if current is not None and current["state"] == state:
                return None  # the WHERE keeps the recorded change
            self.now += timedelta(seconds=1)
            self.rows[domain] = {
                "domain": domain,
                "state": state,
                "changed_at": self.now,
                "changed_by": actor,
            }
            return self.rows[domain]
        return self.rows.get(domain)


def _app(table, *, session="session-digest") -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def owner_auth(request, call_next):  # what OwnerAuthMiddleware sets
        token = verified_dashboard_session.set(session)
        try:
            return await call_next(request)
        finally:
            verified_dashboard_session.reset(token)

    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(pool=lambda _name: table)
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def table():
    return _Table()


async def test_lists_all_then_each_domain_with_state_time_and_actor(table):
    async with _client(_app(table)) as client:
        body = (await client.get("/api/settings/public-mcp")).json()["data"]
    assert [c["domain"] for c in body["controls"]] == list(CONTROL_DOMAINS)
    assert body["controls"][0]["domain"] == "all"
    assert all(c["state"] == "enabled" for c in body["controls"])
    assert body["controls"][1]["changed_at"] == T0.isoformat().replace("+00:00", "Z")
    assert body["controls"][1]["changed_by"] == "system"
    assert body["summary"] == {
        "disabled_domains": 0,
        "total_domains": 7,
        "all_disabled": False,
        "missing_rows": 0,
    }


async def test_owner_disables_finance_with_the_owner_as_actor_and_a_time(table):
    async with _client(_app(table)) as client:
        put = await client.put("/api/settings/public-mcp/finance", json={"state": "disabled"})
        listed = (await client.get("/api/settings/public-mcp")).json()["data"]
    row = put.json()["data"]
    assert put.status_code == 200
    assert row["state"] == "disabled"
    assert row["changed_by"] == "owner:dashboard"
    assert row["changed_at"] is not None and row["changed_at"] != T0.isoformat()
    finance = next(c for c in listed["controls"] if c["domain"] == "finance")
    assert finance["state"] == "disabled" and finance["changed_by"] == "owner:dashboard"
    assert next(c for c in listed["controls"] if c["domain"] == "health")["state"] == "enabled"


async def test_unauthenticated_write_is_401_and_changes_nothing(table):
    async with _client(_app(table, session=None)) as client:
        put = await client.put("/api/settings/public-mcp/finance", json={"state": "disabled"})
    assert put.status_code == 401
    assert table.writes == 0
    assert table.rows["finance"]["state"] == "enabled"


async def test_unauthenticated_write_to_an_unknown_domain_is_still_401(table):
    async with _client(_app(table, session=None)) as client:
        put = await client.put("/api/settings/public-mcp/nonsense", json={"state": "disabled"})
    assert put.status_code == 401


@pytest.mark.parametrize("domain", ["nonsense", "ALL", "finance ", "relationship;", "Finance"])
async def test_an_unknown_domain_is_refused_with_no_write(table, domain):
    async with _client(_app(table)) as client:
        put = await client.put(f"/api/settings/public-mcp/{domain}", json={"state": "disabled"})
    assert put.status_code == 404
    assert put.json()["detail"] == {"error": "unknown_domain"}
    assert table.writes == 0


@pytest.mark.parametrize(
    "body",
    [{"state": "paused"}, {}, {"state": "disabled", "changed_by": "someone-else"}],
)
async def test_the_request_names_a_state_and_nothing_else(table, body):
    async with _client(_app(table)) as client:
        put = await client.put("/api/settings/public-mcp/finance", json=body)
    assert put.status_code == 422
    assert table.writes == 0


async def test_reenable_takes_one_request_and_records_the_actor(table):
    async with _client(_app(table)) as client:
        await client.put("/api/settings/public-mcp/finance", json={"state": "disabled"})
        put = await client.put("/api/settings/public-mcp/finance", json={"state": "enabled"})
    assert put.status_code == 200
    assert put.json()["data"]["state"] == "enabled"
    assert put.json()["data"]["changed_by"] == "owner:dashboard"


async def test_a_repeated_write_reports_the_real_last_change(table):
    async with _client(_app(table)) as client:
        first = (
            await client.put("/api/settings/public-mcp/health", json={"state": "disabled"})
        ).json()["data"]
        again = (
            await client.put("/api/settings/public-mcp/health", json={"state": "disabled"})
        ).json()["data"]
    assert again == first


async def test_summary_counts_disabled_domains_and_the_all_switch(table):
    async with _client(_app(table)) as client:
        for domain in ("finance", "health", "all"):
            await client.put(f"/api/settings/public-mcp/{domain}", json={"state": "disabled"})
        summary = (await client.get("/api/settings/public-mcp/summary")).json()["data"]
    # ``all`` is not one of the seven.
    assert summary == {
        "disabled_domains": 2,
        "total_domains": 7,
        "all_disabled": True,
        "missing_rows": 0,
    }


async def test_a_missing_row_lists_as_unknown_and_never_counts_as_disabled(table):
    del table.rows["travel"]
    async with _client(_app(table)) as client:
        body = (await client.get("/api/settings/public-mcp")).json()["data"]
    travel = next(c for c in body["controls"] if c["domain"] == "travel")
    assert travel == {"domain": "travel", "state": None, "changed_at": None, "changed_by": None}
    assert body["summary"]["disabled_domains"] == 0
    assert body["summary"]["missing_rows"] == 1


async def test_a_failed_write_is_a_closed_503_that_leaks_no_driver_text(table):
    table.fail_writes = True
    async with _client(_app(table)) as client:
        put = await client.put("/api/settings/public-mcp/finance", json={"state": "disabled"})
    assert put.status_code == 503
    assert put.json()["detail"] == {"error": "public_mcp_controls_unavailable"}
    assert "statement" not in put.text
    assert table.rows["finance"]["state"] == "enabled"


async def test_a_failed_read_is_a_closed_503(table):
    table.fail_reads = True
    async with _client(_app(table)) as client:
        got = await client.get("/api/settings/public-mcp")
    assert got.status_code == 503
    assert "statement" not in got.text


def test_the_domain_list_is_the_public_servers_plus_all():
    from butlers.knowledge_read.core import DOMAINS

    assert set(DATA_DOMAINS) == set(DOMAINS)
    assert len(DATA_DOMAINS) == 7 and CONTROL_DOMAINS[0] == "all"


async def test_an_unauthenticated_malformed_body_is_401_not_422(table):
    async with _client(_app(table, session=None)) as client:
        put = await client.put("/api/settings/public-mcp/finance", json={"state": "x", "z": 1})
    assert put.status_code == 401
    assert table.writes == 0
