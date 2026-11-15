"""The Public MCP kill switches over a real PG17 primary and its real roles.

REQ-dashboard-settings-console-001 (owner disable with actor and time, re-enable, 401 with no
change) and REQ-public-mcp-authorization-009 (the control row the public server and the
importer read). The dashboard API login and the CLI login are the migration principal, the
owner of ``mcp_surface.controls``: the write needs no grant, and no butler runtime role gains
one. The route runs over a real asyncpg pool as that principal; the CLI over its own engine.
"""

from __future__ import annotations

import asyncpg
import httpx
import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from sqlalchemy import make_url, text

from butlers.api.deps import get_db
from butlers.api.routers import public_mcp_controls as routes
from butlers.cli import cli
from butlers.core.dashboard_stepup import verified_dashboard_session
from butlers.public_mcp_controls import CONTROL_DOMAINS
from butlers.testing.migration import _bootstrap_migration_prerequisites
from butlers.testing.public_mcp import public_mcp_topology

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def topology():
    with public_mcp_topology() as topology:
        yield topology


@pytest.fixture(autouse=True)
def reset(topology):
    yield
    with topology.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mcp_surface.controls SET state='enabled', changed_by='system', "
                "changed_at=now()"
            )
        )


def _row(topology, domain):
    with topology.engine.connect() as conn:
        return conn.execute(
            text("SELECT state, changed_at, changed_by FROM mcp_surface.controls WHERE domain=:d"),
            {"d": domain},
        ).one()


@pytest.fixture
async def app(topology):
    url = topology.engine.url
    # The pool as the dashboard builds it: the migration login, the switchboard search path.
    pool = await asyncpg.create_pool(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database=url.database,
        min_size=1,
        max_size=2,
        server_settings={"search_path": "switchboard,public"},
    )
    app = FastAPI()
    state = {"session": "owner-session"}

    @app.middleware("http")
    async def owner_auth(request, call_next):
        token = verified_dashboard_session.set(state["session"])
        try:
            return await call_next(request)
        finally:
            verified_dashboard_session.reset(token)

    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: type("Db", (), {"pool": lambda s, n: pool})()
    app.state.test = state
    try:
        yield app
    finally:
        await pool.close()


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_the_table_has_the_actor_column_and_the_seed_rows_name_the_system(topology):
    with topology.engine.connect() as conn:
        columns = (
            conn.execute(
                text(
                    "SELECT attname||':'||format_type(atttypid,atttypmod)||':'||attnotnull "
                    "FROM pg_attribute WHERE attrelid='mcp_surface.controls'::regclass "
                    "AND attnum>0 AND NOT attisdropped ORDER BY attnum"
                )
            )
            .scalars()
            .all()
        )
        seeded = conn.execute(text("SELECT domain, changed_by FROM mcp_surface.controls")).all()
    assert columns[-1] == "changed_by:text:true"
    assert sorted(d for d, _ in seeded) == sorted(CONTROL_DOMAINS)
    assert {actor for _, actor in seeded} == {"system"}


def test_bootstrap_adds_the_actor_column_to_a_table_that_predates_it(topology):
    with topology.admin.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE mcp_surface.controls DROP COLUMN changed_by")
    _bootstrap_migration_prerequisites(
        topology.admin.url.render_as_string(hide_password=False), topology.migration_role
    )
    with topology.engine.connect() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM mcp_surface.controls WHERE changed_by='system'")
        ).scalar_one() == len(CONTROL_DOMAINS)


async def test_owner_disables_finance_over_the_real_table_with_the_owner_as_actor(app, topology):
    async with _client(app) as client:
        put = await client.put("/api/settings/public-mcp/finance", json={"state": "disabled"})
        listed = (await client.get("/api/settings/public-mcp")).json()["data"]
    assert put.status_code == 200, put.text
    state, changed_at, changed_by = _row(topology, "finance")
    assert (state, changed_by) == ("disabled", "owner:dashboard")
    finance = next(c for c in listed["controls"] if c["domain"] == "finance")
    assert finance["state"] == "disabled" and finance["changed_by"] == "owner:dashboard"
    assert finance["changed_at"] is not None and changed_at is not None
    assert _row(topology, "health")[0] == "enabled"
    assert listed["summary"] == {
        "disabled_domains": 1,
        "total_domains": 7,
        "all_disabled": False,
        "missing_rows": 0,
    }


async def test_unauthenticated_write_is_401_and_the_row_is_unchanged(app, topology):
    before = _row(topology, "finance")
    app.state.test["session"] = None
    async with _client(app) as client:
        put = await client.put("/api/settings/public-mcp/finance", json={"state": "disabled"})
    assert put.status_code == 401
    assert _row(topology, "finance") == before


async def test_an_unknown_domain_is_refused_and_no_row_appears(app, topology):
    async with _client(app) as client:
        put = await client.put("/api/settings/public-mcp/crypto", json={"state": "disabled"})
    assert put.status_code == 404
    with topology.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM mcp_surface.controls")).scalar_one() == 8


async def test_reenable_and_a_repeated_write_keep_the_real_last_change(app, topology):
    async with _client(app) as client:
        await client.put("/api/settings/public-mcp/travel", json={"state": "disabled"})
        first = _row(topology, "travel")
        again = await client.put("/api/settings/public-mcp/travel", json={"state": "disabled"})
        assert _row(topology, "travel") == first, "a no-op must not restamp the change"
        assert again.json()["data"]["changed_at"] is not None
        back = await client.put("/api/settings/public-mcp/travel", json={"state": "enabled"})
    assert back.status_code == 200
    state, changed_at, actor = _row(topology, "travel")
    assert (state, actor) == ("enabled", "owner:dashboard")
    assert changed_at > first[1]


async def test_a_deleted_seed_row_can_still_be_disabled_in_an_incident(app, topology):
    with topology.engine.begin() as conn:
        conn.execute(text("DELETE FROM mcp_surface.controls WHERE domain='all'"))
    try:
        async with _client(app) as client:
            listed = (await client.get("/api/settings/public-mcp")).json()["data"]
            assert listed["controls"][0] == {
                "domain": "all",
                "state": None,
                "changed_at": None,
                "changed_by": None,
            }
            put = await client.put("/api/settings/public-mcp/all", json={"state": "disabled"})
        assert put.status_code == 200
        assert _row(topology, "all")[0] == "disabled"
    finally:
        with topology.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO mcp_surface.controls(domain,state) VALUES('all','enabled') "
                    "ON CONFLICT DO NOTHING"
                )
            )


def test_the_cli_writes_the_real_table_as_the_cli_actor(topology, monkeypatch):
    url = make_url(topology.engine.url.render_as_string(hide_password=False))
    params = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    monkeypatch.setattr("butlers.db.db_params_from_env", lambda: params)
    runner = CliRunner()
    off = runner.invoke(cli, ["public-mcp", "disable", "health", "--database", url.database])
    assert off.exit_code == 0, off.output
    assert _row(topology, "health")[0::2] == ("disabled", "owner:cli")
    again = runner.invoke(cli, ["public-mcp", "disable", "health", "--database", url.database])
    assert "already disabled" in again.output
    on = runner.invoke(cli, ["public-mcp", "enable", "health", "--database", url.database])
    assert on.exit_code == 0 and _row(topology, "health")[0] == "enabled"


def test_no_butler_runtime_role_can_write_the_table_and_only_relationship_reads_it(topology):
    with topology.admin.connect() as conn:
        roles = (
            conn.execute(
                text(
                    "SELECT rolname FROM pg_roles "
                    "WHERE rolname LIKE 'butler\\_%\\_rw' OR rolname='connector_writer'"
                )
            )
            .scalars()
            .all()
        )
        assert roles
        for role in roles:
            can_write = conn.execute(
                text(
                    "SELECT has_table_privilege(:r,'mcp_surface.controls','INSERT,UPDATE,DELETE') "
                    "OR has_column_privilege(:r,'mcp_surface.controls','changed_by','UPDATE')"
                ),
                {"r": role},
            ).scalar_one()
            assert not can_write, role
        readers = {
            role
            for role in roles
            if conn.execute(
                text("SELECT has_table_privilege(:r,'mcp_surface.controls','SELECT')"), {"r": role}
            ).scalar_one()
        }
    assert readers == {"butler_relationship_rw"}


def test_the_actor_never_enters_the_publication(topology):
    """changed_by stays on the primary: reconcile publishes only the manifest's three columns."""
    from butlers.knowledge_read.manifest import load_manifest
    from butlers.migrations.public_mcp import reconcile_connection

    with topology.engine.begin() as conn:
        reconcile_connection(conn, load_manifest())
        published = conn.execute(
            text(
                "SELECT attnames FROM pg_publication_tables WHERE pubname='butlers_public_v1' "
                "AND schemaname='mcp_surface' AND tablename='controls'"
            )
        ).scalar_one()
    assert published == ["domain", "state", "changed_at"]
