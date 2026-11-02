"""Owner projects: General charters, versions and typed links (bu-jv1moa.12).

Unit cases drive the production app and owner middleware to prove the refusal
order: owner control before any pool access, the owner's dashboard session
before any write body is read, and strict request models that refuse crafted
draft-lineage or promotion fields.  Integration cases run the mounted routes
against migrated PostgreSQL: the create/edit/replay/close/expire lifecycle,
one committed successor under concurrent edits, insert-only versions, link
add/remove, the absence of any side effect outside the project tables, and
gen_004 upgrade/downgrade.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import asyncpg
import httpx
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.api import dashboard_audit_middleware
from butlers.api.app import create_app
from butlers.api.deps import get_db_optional
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import assert_at_chain_head
from tests.api.auth_helpers import _DomainOwnerState, create_authenticated_domain_app

_DOCKER = shutil.which("docker") is not None
_ROUTE = "/api/general/projects"
_OWNER_KEY = "synthetic-owner-key"
_FIXED_ID = "00000000-0000-4000-8000-0000000012aa"
_TRIP_ID = "aaaaaaaa-1111-4111-8111-11111111111b"
_PROJECT_TABLES = {
    "general.projects",
    "general.project_charter_versions",
    "general.project_links",
}


def _horizon(days: int = 30) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0).isoformat()


# One fixed horizon so replays and dedupe compare equal requests.
_HORIZON = _horizon()


def _charter(**overrides) -> dict:
    charter = {
        "title": "Move to Tiong Bahru",
        "horizon_end": _HORIZON,
        "objective": "Move flat by mid December.",
        "done_condition": "Keys handed back for the old flat.",
        "constraints": {"budget": {"amount": "4000", "currency": "SGD"}, "must_keep": ["cat"]},
    }
    charter.update(overrides)
    return charter


def _create(**overrides) -> dict:
    return {"kind": "project", "idempotency_key": "move-1", **_charter(), **overrides}


class _Manager:
    """Dashboard DB-manager seam that counts pool requests; no pool means unavailable."""

    def __init__(self, pool: asyncpg.Pool | None = None) -> None:
        self.serving = pool
        self.calls = 0

    def pool(self, name: str) -> asyncpg.Pool:
        assert name == "general"
        self.calls += 1
        if self.serving is None:
            raise KeyError("synthetic unavailable")
        return self.serving


class _OwnerSession(_DomainOwnerState):
    """The owner's verified dashboard session (session digest present)."""

    async def authorize(self, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(method="session", expires_at=None, session_digest="digest")


@pytest.fixture
def seams(monkeypatch):
    observed = SimpleNamespace(manager=_Manager(), generic=[])

    async def record_generic(_db_manager, **fields) -> None:
        observed.generic.append(fields)

    monkeypatch.setattr(dashboard_audit_middleware, "get_db_manager", lambda: observed.manager)
    monkeypatch.setattr(dashboard_audit_middleware, "emit_dashboard_audit", record_generic)
    return observed


def _mounted(manager: _Manager, app=None, *, session: bool = False):
    app = app if app is not None else create_authenticated_domain_app()
    if session:
        app.state.owner_auth_service = _OwnerSession(_OWNER_KEY)
    app.dependency_overrides[get_db_optional] = lambda: manager
    return app


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


_WRITES = [
    ("POST", _ROUTE, _create()),
    ("PUT", f"{_ROUTE}/{_FIXED_ID}/charter", {**_charter(), "expected_version": 1}),
    ("POST", f"{_ROUTE}/{_FIXED_ID}/close", {"expected_version": 1}),
    ("POST", f"{_ROUTE}/{_FIXED_ID}/links", {"ref_kind": "trip", "ref_id": _TRIP_ID}),
    ("DELETE", f"{_ROUTE}/{_FIXED_ID}/links/{_FIXED_ID}", None),
]


@pytest.mark.unit
async def test_non_owner_and_non_session_writers_are_refused_before_pool_or_body(seams) -> None:
    """Non-owners never reach the pool; an owner API key reads but cannot author."""
    no_owner = _mounted(seams.manager, create_app(api_key=_OWNER_KEY))
    no_owner.state.owner_auth_service = _DomainOwnerState(_OWNER_KEY)
    async with _client(no_owner) as http:
        for method, path, body in [("GET", _ROUTE, None), *_WRITES]:
            response = await http.request(method, path, json=body)
            assert response.status_code == 401, (method, path)
    assert seams.manager.calls == 0

    # An owner API key (a runtime, script or connector stand-in) has owner control
    # but no dashboard session: every write is refused before its body or pool.
    async with _client(_mounted(seams.manager)) as http:
        for method, path, body in _WRITES:
            response = await http.request(method, path, json=body)
            assert (response.status_code, response.json()["error"]["code"]) == (
                403,
                "OWNER_SESSION_REQUIRED",
            ), (method, path)
        assert seams.manager.calls == 0
        # Positive control: the same caller can read, reaching the (unavailable) pool.
        listed = await http.get(_ROUTE)
    assert (listed.status_code, listed.json()["error"]["code"]) == (503, "PROJECTS_UNAVAILABLE")
    assert seams.manager.calls == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        _create(derived_from_draft="draft-1"),
        _create(promote_draft_id=_FIXED_ID),
        _create(state="closed"),
        _create(created_by="general-runtime"),
        _create(kind="goal"),
        _create(objective="x" * 1001),
        _create(title=" padded "),
        _create(horizon_end="2026-12-01T00:00:00"),
        _create(constraints={"budget": {"amount": "-1", "currency": "SGD"}}),
        _create(constraints={"spend_limit": 10}),
    ],
)
async def test_crafted_or_invalid_create_is_refused_without_pool(seams, payload: dict) -> None:
    """Draft lineage, promotion, actor and state fields cannot ride along with a write."""
    async with _client(_mounted(seams.manager, session=True)) as http:
        response = await http.post(_ROUTE, json=payload)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "INVALID_REQUEST")
    assert seams.manager.calls == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ref_kind", "ref_id"),
    [
        ("trip", "https://example.test/trip"),
        ("trip", "not-a-uuid"),
        ("calendar_event", _TRIP_ID.upper()),
        ("goal", _TRIP_ID),
        ("drive_file", "folder/file"),
    ],
)
async def test_links_take_typed_opaque_ids_only(seams, ref_kind: str, ref_id: str) -> None:
    async with _client(_mounted(seams.manager, session=True)) as http:
        response = await http.post(
            f"{_ROUTE}/{_FIXED_ID}/links", json={"ref_kind": ref_kind, "ref_id": ref_id}
        )
    assert response.status_code == 422
    assert seams.manager.calls == 0


# ---------------------------------------------------------------------------
# Real migrated PostgreSQL
# ---------------------------------------------------------------------------


@pytest.fixture
async def projects_pool(db):
    """A General-schema pool on the shared migrated clone (core in public, General schema)."""
    return await db("core", "general", schemas={"general": "general"}, schema="general", max_size=6)


async def _row_counts(pool: asyncpg.Pool) -> dict[str, int]:
    """Row count of every readable user table in every schema, in one literal query."""
    rows = await pool.fetch(
        "SELECT format('%I.%I', n.nspname, c.relname) AS name, "
        "(xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I.%I', "
        "n.nspname, c.relname), false, true, '')))[1]::text::bigint AS row_count "
        "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' "
        "AND has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(c.oid, 'SELECT')"
    )
    return {row["name"]: row["row_count"] for row in rows}


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_project_lifecycle_versions_links_and_no_side_effects(
    projects_pool, seams, monkeypatch
) -> None:
    """Create, replay, dedupe, CAS edit, links, close and expire; nothing else changes."""
    pool = projects_pool
    outbound: list[object] = []

    async def no_network(_transport, request):
        outbound.append(request.url)
        raise AssertionError("owner projects must make no outbound call")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    seams.manager.serving = pool
    before = await _row_counts(pool)

    async with _client(_mounted(seams.manager, session=True)) as http:
        created = await http.post(_ROUTE, json=_create())
        assert created.status_code == 201
        project = created.json()["data"]
        assert (project["state"], project["current_charter_version"]) == ("draft", 1)
        assert project["charter"]["created_by"] == "owner"
        pid = project["id"]

        replay = await http.post(_ROUTE, json=_create())
        assert (replay.status_code, replay.json()["meta"]["outcome"]) == (200, "replayed")
        assert replay.json()["data"]["id"] == pid
        reused = await http.post(_ROUTE, json=_create(objective="Something else."))
        assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
        twin = await http.post(
            _ROUTE,
            json=_create(idempotency_key="move-2", title="move  to TIONG bahru"),
        )
        assert (twin.status_code, twin.json()["meta"]["outcome"]) == (200, "deduplicated")
        assert twin.json()["data"]["id"] == pid

        edit = {**_charter(objective="Move by Dec 15."), "expected_version": 1}
        edited = await http.put(f"{_ROUTE}/{pid}/charter", json=edit)
        assert (edited.status_code, edited.json()["data"]["current_charter_version"]) == (200, 2)
        again = await http.put(f"{_ROUTE}/{pid}/charter", json=edit)
        assert (again.status_code, again.json()["meta"]["outcome"]) == (200, "unchanged")
        stale = await http.put(
            f"{_ROUTE}/{pid}/charter", json={**_charter(objective="Other."), "expected_version": 1}
        )
        assert (stale.status_code, stale.json()["error"]["code"]) == (409, "VERSION_CONFLICT")

        # Two concurrent edits from the same version: exactly one successor commits.
        racers = await asyncio.gather(
            *(
                http.put(
                    f"{_ROUTE}/{pid}/charter",
                    json={**_charter(objective=f"Racer {n}."), "expected_version": 2},
                )
                for n in range(2)
            )
        )
        assert sorted(r.status_code for r in racers) == [200, 409]
        assert await pool.fetchval(
            "SELECT array_agg(version ORDER BY version) FROM project_charter_versions "
            "WHERE project_id = $1::uuid",
            pid,
        ) == [1, 2, 3]
        with pytest.raises(asyncpg.PostgresError, match="insert-only"):
            await pool.execute("UPDATE project_charter_versions SET objective = 'rewritten'")

        linked = await http.post(
            f"{_ROUTE}/{pid}/links", json={"ref_kind": "trip", "ref_id": _TRIP_ID}
        )
        assert linked.status_code == 201
        relinked = await http.post(
            f"{_ROUTE}/{pid}/links", json={"ref_kind": "trip", "ref_id": _TRIP_ID}
        )
        assert relinked.json()["meta"]["outcome"] == "unchanged"
        link_id = relinked.json()["data"]["links"][0]["id"]
        await http.post(
            f"{_ROUTE}/{pid}/links", json={"ref_kind": "drive_file", "ref_id": "1AbC_def-9"}
        )
        unlinked = await http.delete(f"{_ROUTE}/{pid}/links/{link_id}")
        assert [link["ref_kind"] for link in unlinked.json()["data"]["links"]] == ["drive_file"]

        closed = await http.post(f"{_ROUTE}/{pid}/close", json={"expected_version": 3})
        assert closed.json()["data"]["state"] == "closed"
        frozen = await http.put(
            f"{_ROUTE}/{pid}/charter", json={**_charter(objective="Reopen."), "expected_version": 3}
        )
        assert frozen.json()["error"]["code"] == "PROJECT_NOT_DRAFT"

        # Expiry is deterministic at the horizon: reads see it at once, and the
        # first write persists it and refuses.
        second = (
            await http.post(_ROUTE, json=_create(idempotency_key="decide-1", kind="decision"))
        ).json()["data"]
        await pool.execute(
            "UPDATE projects SET horizon_end = now() - interval '1 minute' WHERE id = $1::uuid",
            second["id"],
        )
        read = await http.get(f"{_ROUTE}/{second['id']}")
        assert read.json()["data"]["state"] == "expired"
        refused = await http.post(f"{_ROUTE}/{second['id']}/close", json={"expected_version": 1})
        assert refused.json()["error"]["code"] == "PROJECT_NOT_DRAFT"
        assert (
            await pool.fetchval("SELECT state FROM projects WHERE id = $1::uuid", second["id"])
            == "expired"
        )

        listed = await http.get(_ROUTE)
        assert listed.json()["meta"]["total"] == 2

    after = await _row_counts(pool)
    changed = {name for name in after if after[name] != before.get(name)}
    assert changed == _PROJECT_TABLES
    assert outbound == []


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_database_freezes_terminal_projects_and_edits_persist_due_expiry(
    projects_pool, seams
) -> None:
    """Below the API, a closed or expired project takes no version or link change."""
    pool = projects_pool
    seams.manager.serving = pool
    async with _client(_mounted(seams.manager, session=True)) as http:
        closed = (await http.post(_ROUTE, json=_create(idempotency_key="c1"))).json()["data"]
        await http.post(
            f"{_ROUTE}/{closed['id']}/links", json={"ref_kind": "trip", "ref_id": _TRIP_ID}
        )
        await http.post(f"{_ROUTE}/{closed['id']}/close", json={"expected_version": 1})
        lapsed = (
            await http.post(_ROUTE, json=_create(idempotency_key="l1", title="Lapsed"))
        ).json()["data"]
        live = (await http.post(_ROUTE, json=_create(idempotency_key="v1", title="Live"))).json()[
            "data"
        ]
    await pool.execute(
        "UPDATE projects SET horizon_end = now() - interval '1 minute' WHERE id = $1::uuid",
        lapsed["id"],
    )

    version = (
        "INSERT INTO project_charter_versions (project_id, version, title, horizon_end, "
        "objective, done_condition, constraints, created_by) VALUES "
        "($1::uuid, 99, 'x', now() + interval '1 day', 'o', 'd', '{}'::jsonb, 'owner')"
    )
    link = (
        "INSERT INTO project_links (project_id, ref_kind, ref_id, created_by) "
        "VALUES ($1::uuid, 'drive_file', 'file-1', 'owner')"
    )
    for project_id in (closed["id"], lapsed["id"]):
        for statement in (version, link):
            with pytest.raises(asyncpg.PostgresError, match="frozen"):
                await pool.execute(statement, project_id)
    with pytest.raises(asyncpg.PostgresError, match="frozen"):
        await pool.execute("DELETE FROM project_links WHERE project_id = $1::uuid", closed["id"])
    # Positive control: a live draft accepts both.
    await pool.execute(version, live["id"])
    await pool.execute(link, live["id"])

    # A charter edit persists due expiries before it writes, so a draft that
    # already reads as expired no longer holds the draft title/horizon slot.
    assert (
        await pool.fetchval("SELECT state FROM projects WHERE id = $1::uuid", lapsed["id"])
        == "draft"
    )
    async with _client(_mounted(seams.manager, session=True)) as http:
        edited = await http.put(
            f"{_ROUTE}/{live['id']}/charter",
            json={**_charter(title="Live", objective="Edited."), "expected_version": 1},
        )
    assert edited.status_code == 200
    assert (
        await pool.fetchval("SELECT state FROM projects WHERE id = $1::uuid", lapsed["id"])
        == "expired"
    )


# ---------------------------------------------------------------------------
# gen_004 upgrade / downgrade
# ---------------------------------------------------------------------------


def _migrate(db_url: str, target: str) -> None:
    config = _build_alembic_config(db_url, ["general"], target_schema="general")
    if target == "head":
        command.upgrade(config, "general@head")
    else:
        command.downgrade(config, f"general@{target}")


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER, reason="Docker not available")
def test_gen_004_upgrades_downgrades_and_retains_charter_history(fresh_db) -> None:
    db_url = fresh_db("general", schemas={"general": "general"})
    engine = create_engine(db_url, connect_args={"options": "-c search_path=general,public"})

    def present(conn) -> bool:
        return conn.execute(text("SELECT to_regclass('projects') IS NOT NULL")).scalar_one()

    try:
        _migrate(db_url, "gen_003")  # empty: dropped
        with engine.begin() as conn:
            assert not present(conn)
        _migrate(db_url, "head")
        with engine.begin() as conn:
            assert_at_chain_head(conn, "general", chain="general")
            pid = conn.execute(
                text(
                    "INSERT INTO projects (kind, title, current_charter_version, horizon_end, "
                    "create_idempotency_key, create_request_digest, created_by) VALUES "
                    "('project', 'Keep', 1, now() + interval '1 day', 'k', :d, 'owner') "
                    "RETURNING id"
                ),
                {"d": "a" * 64},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO project_charter_versions (project_id, version, title, "
                    "horizon_end, objective, done_condition, constraints, created_by) VALUES "
                    "(:p, 1, 'Keep', now() + interval '1 day', 'o', 'd', CAST(:c AS jsonb), "
                    "'owner')"
                ),
                {"p": pid, "c": json.dumps({"must_keep": []})},
            )

        _migrate(db_url, "gen_003")  # non-empty: retained inert
        with engine.begin() as conn:
            assert present(conn)
            assert conn.execute(text("SELECT count(*) FROM project_charter_versions")).scalar_one()
        _migrate(db_url, "head")
        with engine.begin() as conn:
            assert_at_chain_head(conn, "general", chain="general")
            assert conn.execute(text("SELECT count(*) FROM projects")).scalar_one() == 1
    finally:
        engine.dispose()
