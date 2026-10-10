"""Tests for participant_entity_ids field and participant_entity_id filter on chronicler API.

Covers (tasks.md §4.3, §4.4, §4.6):
- GET /api/chronicler/episodes returns participant_entity_ids array field.
- ?participant_entity_id=<uuid> filters to episodes where the entity appears in the array.
- GET /api/chronicler/episodes/{id} response includes participant_entity_ids.
- participant_entity_ids is empty list [] when column absent from row (defensive default).
- SQL WHERE clause for participant_entity_id uses ::uuid = ANY(participant_entity_ids).

(The owner-only ?entity_id filter was removed in bu-cfsgy.)
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

_NOW = datetime.now(UTC)
_ENTITY_A = uuid4()
_ENTITY_B = uuid4()
_ENTITY_C = uuid4()

EPISODE_ID_1 = "00000000-0000-0000-0000-000000000001"
EPISODE_ID_2 = "00000000-0000-0000-0000-000000000002"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """asyncpg.Record-like dict subclass."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _episode_row(
    *,
    episode_id: str = EPISODE_ID_1,
    participant_entity_ids: list[UUID] | None = None,
    source_name: str = "google_calendar.completed",
    episode_type: str = "scheduled_block",
) -> _Row:
    """Build a mock v_episodes_corrected row."""
    return _Row(
        {
            "id": UUID(episode_id),
            "source_name": source_name,
            "source_ref": f"{source_name}:ref",
            "episode_type": episode_type,
            "start_at": _NOW - timedelta(hours=1),
            "end_at": _NOW,
            "precision": "exact",
            "title": "Team meeting",
            "payload": {},
            "privacy": "normal",
            "retention_days": None,
            "tombstone_at": None,
            "canonical_start_at": _NOW - timedelta(hours=1),
            "canonical_end_at": _NOW,
            "canonical_title": "Team meeting",
            "canonical_privacy": "normal",
            "corrected_at": None,
            "correction_note": None,
            "created_at": _NOW - timedelta(hours=2),
            "updated_at": _NOW,
            "participant_entity_ids": participant_entity_ids
            if participant_entity_ids is not None
            else [],
        }
    )


def _build_app(rows: list[_Row], *, fetchrow_result: _Row | None = None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.fetchrow = AsyncMock(
        return_value=fetchrow_result if fetchrow_result is not None else (rows[0] if rows else None)
    )
    pool.fetchval = AsyncMock(return_value=len(rows))
    pool.execute = AsyncMock(return_value="OK")

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break

    return app, pool


# ---------------------------------------------------------------------------
# §4.4: single-episode response includes participant_entity_ids
# ---------------------------------------------------------------------------


async def test_get_episode_includes_participant_entity_ids_populated() -> None:
    """GET /api/chronicler/episodes/{id} returns participant_entity_ids array."""
    row = _episode_row(
        participant_entity_ids=[_ENTITY_A, _ENTITY_B],
    )
    app, _ = _build_app([row])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/chronicler/episodes/{EPISODE_ID_1}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "participant_entity_ids" in body
    assert body["participant_entity_ids"] == [str(_ENTITY_A), str(_ENTITY_B)]


async def test_get_episode_includes_participant_entity_ids_empty() -> None:
    """GET /api/chronicler/episodes/{id} returns empty list when no entity links."""
    row = _episode_row(participant_entity_ids=[])
    app, _ = _build_app([row])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/chronicler/episodes/{EPISODE_ID_1}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["participant_entity_ids"] == []


async def test_get_episode_participant_entity_ids_defensive_missing_key() -> None:
    """GET /api/chronicler/episodes/{id} defaults to [] when column absent from row."""
    row = _episode_row()
    # Remove the participant_entity_ids key to simulate a row from an older view.
    del row["participant_entity_ids"]
    app, _ = _build_app([row])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/chronicler/episodes/{EPISODE_ID_1}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["participant_entity_ids"] == []


# ---------------------------------------------------------------------------
# §4.3 / §4.6: list endpoint field + filters
# ---------------------------------------------------------------------------


async def test_list_episodes_includes_participant_entity_ids_field() -> None:
    """GET /api/chronicler/episodes includes participant_entity_ids on each row."""
    rows = [
        _episode_row(episode_id=EPISODE_ID_1, participant_entity_ids=[_ENTITY_A]),
        _episode_row(episode_id=EPISODE_ID_2, participant_entity_ids=[]),
    ]
    app, _ = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/chronicler/episodes")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data) == 2
    assert data[0]["participant_entity_ids"] == [str(_ENTITY_A)]
    assert data[1]["participant_entity_ids"] == []


async def test_list_episodes_participant_entity_id_filter_passes_uuid_to_sql() -> None:
    """?participant_entity_id=<uuid> is accepted and passed through to the DB query."""
    rows = [_episode_row(participant_entity_ids=[_ENTITY_B])]
    app, pool = _build_app(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/chronicler/episodes?participant_entity_id={_ENTITY_B}")
    assert resp.status_code == 200, resp.text
    # Verify the pool was queried (filter was applied, not rejected).
    assert pool.fetchval.called or pool.fetch.called


# ---------------------------------------------------------------------------
# SQL guardrail: WHERE clause shape
# ---------------------------------------------------------------------------


def _extract_handler_sql(source: str, handler_name: str) -> str:
    """Extract the SQL WHERE-clause fragment from the named handler function body."""
    import re

    _SQL_FRAGMENT = re.compile(
        r"^\s*(SELECT|INSERT|UPDATE|DELETE|WHERE|AND)\b",
        re.IGNORECASE,
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == handler_name:
            handler_src = ast.unparse(node)
            return handler_src
    return ""


def test_list_episodes_sql_uses_any_for_participant_entity_id() -> None:
    """The list_episodes handler uses $N::uuid = ANY(participant_entity_ids) for the filter."""
    source = _ROUTER_PATH.read_text()
    tree = ast.parse(source)
    # Walk all string constants and find the ANY(participant_entity_ids) clause.
    any_clauses = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "ANY(participant_entity_ids)" in node.value
    ]
    assert any_clauses, (
        "router.py must contain a SQL clause with ANY(participant_entity_ids) "
        "for the participant_entity_id filter"
    )
    assert any("::uuid" in c for c in any_clauses), (
        "The ANY(participant_entity_ids) clause must cast the parameter with ::uuid"
    )


async def test_location_api_export_birth_commits_before_response_and_unknown_refuses():
    """REQ-location-retention-005/006; actual API producer, software-only SQL double."""
    from contextlib import asynccontextmanager

    from butlers.chronicler import location_retention

    row = _episode_row(source_name="owntracks.points", episode_type="movement")
    births = []
    dispositions = {}
    trace = []
    unknown = False
    schema = "chronicler"

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            trace.append("transaction")
            yield
            trace.append("commit")

        async def fetchrow(self, query, *args):
            if "location_retention_policy" in query:
                return {"days": 30, "version": 1, "spatial_scheme_version": 1, "updated_at": _NOW}
            if "location_native_api_dispositions" in query:
                return dispositions.get(args[0])
            return row

        async def fetch(self, query, *args):
            assert "location_projection_outputs" in query
            return [{"output_id": row["id"]}]

        async def execute(self, query, *args):
            if "INSERT INTO location_native_copy_births" in query:
                births.append(args)
                trace.append("birth")
            if "INSERT INTO location_native_api_dispositions" in query:
                dispositions[args[0]] = {
                    "server_request": args[1],
                    "producer_kind": args[2],
                    "body_digest": args[3],
                    "receipt_id": args[4],
                }
                trace.append("server_lifetime_disposition")

        async def fetchval(self, query, *args):
            if "current_schema()" in query:
                return schema
            if "location_native_api_dispositions" in query:
                trace.append("disposition_readback")
                return dispositions.get(args[0], {}).get("receipt_id")
            if "location_retention_frontiers" in query:
                return False
            assert "location_native_copy_births" in query
            if "receiving_server_request" in query:
                return sum(b[0] == args[0] and b[8] == args[1] and b[3] == args[2] for b in births)
            if len(args) == 1:
                return sum(b[0] == args[0] for b in births)
            trace.append("readback")
            return 0 if unknown else sum(b[0] == args[0] for b in births)

    conn = Conn()

    class Pool:
        async def fetchrow(self, query, *args):
            return row

        @asynccontextmanager
        async def acquire(self):
            yield conn

    db = MagicMock(spec=DatabaseManager)
    pool = Pool()
    db.pool.return_value = pool
    location_retention._api_copy_pools.add(pool)  # Test-only configuration, not actual role proof.
    app = create_app(api_key="")
    for butler, module in app.state.butler_routers:
        if butler == "chronicler":
            app.dependency_overrides[module._get_db_manager] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        path = f"/api/chronicler/episodes/{row['id']}?runtime_session_id=forged"
        response = await client.get(path)
        assert response.status_code == 200
        assert response.json()["source_name"] == "owntracks.points"
        assert trace[:4] == ["transaction", "birth", "commit", "readback"]
        assert trace.index("server_lifetime_disposition") > trace.index("readback")
        assert trace[-1] == "disposition_readback"
        assert dispositions[births[-1][0]]["server_request"] == births[-1][8]
        assert births[-1][8] is not None  # Native ASGI scope, never caller query/principal.
        assert births[-1][5] is None  # Existing API identity is not a receiving runtime.
        assert births[-1][4] is True and births[-1][7] == "api_export"
        unknown = True
        response = await client.get(path)
        assert response.status_code == 500 and "owntracks.points" not in response.text
        unknown = False
        schema = "unrelated"
        before = len(births)
        assert (await client.get(path)).status_code == 500
        assert len(births) == before
        schema = "chronicler"
        assert (await client.get(path)).status_code == 200
    from butlers.chronicler.location_export_lifetime import _current_location_export

    async def interrupted_app(scope, receive, send):
        async def failed_send(message):
            if message.get("type") == "http.response.body":
                raise RuntimeError("Synthetic interrupted export")
            await send(message)

        await app(scope, receive, failed_send)

    prior_count = len(births)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=interrupted_app), base_url="http://test"
    ) as client:
        with pytest.raises(Exception, match="Synthetic interrupted export"):
            await client.get(path)
    assert len(births) > prior_count and births[-1][0] not in dispositions
    assert _current_location_export.get() is None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get(path)).status_code == 200
    assert births[-1][0] in dispositions
    from butlers.chronicler.location_export_lifetime import LocationExportLifetimeMiddleware

    async def unfinished_source(scope, receive, send):
        async def native_reader(actual):
            return [row]

        await location_retention.capture_api_read(pool, "episode", native_reader)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})

    async def consume(message):
        return None

    await LocationExportLifetimeMiddleware(unfinished_source)(
        {"type": "http", "path": "/api/chronicler/episodes"}, None, consume
    )
    assert births[-1][0] not in dispositions and _current_location_export.get() is None
    location_retention._api_copy_pools.discard(pool)
